"""이미 발행된 글을 글 폴더(post.md, meta.json, images/)의 내용으로 고친다. 주소와 댓글은 그대로 남는다.

    uv run python scripts/update_post.py <글 폴더> --dry-run      # 비우고 다시 써서 점검까지, 발행 안 함
    uv run python scripts/update_post.py <글 폴더>                # 고쳐서 다시 발행
    uv run python scripts/update_post.py <글 폴더> --log-no 224431796170 --title-now "지금 네이버에 걸린 제목"

지우고 다시 올리지 않는 까닭: 주소가 바뀌면 달린 댓글과 검색에 잡힌 자리를 잃는다.
그래서 글 페이지의 '수정하기'와 같은 화면(`?Redirect=Update`)을 열어 제목과 본문을 비우고
draft_folder 와 같은 길(write_post)로 다시 쓴 뒤, 같은 점검(inspect)을 통과할 때만 발행을 누른다.

**카테고리·태그·공개 설정은 건드리지 않는다.** 발행된 글에 걸린 값을 그대로 둔다.
태그 입력은 덧붙이기만 해서, 다시 넣으면 빠진 태그가 남고 겹친 태그가 쌓인다.

대상 확인을 두 번 한다. 글 번호를 제목으로 찾고(`--log-no` 를 주면 그 번호), 수정 화면에 실린
제목이 지금 제목(`--title-now`, 없으면 meta.json 의 title)과 같을 때만 비운다.
비우기 전에 옛 글의 제목과 본문 글자를 `<글 폴더>/naver-before-<시각>.txt` 에 남긴다.
점검에 걸리거나 --dry-run 이면 발행을 누르지 않고 닫는다 — 블로그의 글은 그대로다.

종료 코드
  0  고쳐서 발행했고 글 페이지에서 새 내용을 확인했다 (--dry-run 은 점검 통과)
  2  사전 확인 실패, 또는 글 번호를 못 찾았다 — 아무것도 안 건드렸다
  3  다시 쓴 결과가 점검에 걸렸다 — 발행 안 함, 블로그의 글은 그대로다
  4  수정 화면에 실린 글이 대상이 아니다 — 아무것도 안 건드렸다
  6  발행은 눌렀는데 글 페이지에서 새 내용을 확인 못 했다 — 네이버에서 직접 확인
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from draft_folder import DUMP, _norm, inspect, log  # noqa: E402
from naver_blog_mcp import selectors as S  # noqa: E402
from naver_blog_mcp.editor import (  # noqa: E402
    DOC_TEXT, EditorError, clear_document, get_editor_frame, goto_update, open_publish_panel,
    rep_image_index, set_rep_image, write_post,
)
from naver_blog_mcp.server import find_cover, preflight, read_meta, split_title  # noqa: E402
from naver_blog_mcp.session import Session, snapshot  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))


async def find_log_no(ctx, title: str) -> str | None:
    """발행된 글 목록에서 제목이 똑같은 글의 번호. 최근 90편을 본다."""
    blog = os.environ["NAVER_BLOG_ID"]
    n = _norm(title)
    for page_no in (1, 2, 3):
        url = (f"https://blog.naver.com/PostTitleListAsync.naver?blogId={blog}&currentPage={page_no}"
               "&countPerPage=30&categoryNo=0&parentCategoryNo=0&viewdate=&range=&type=&pagingType=&noTitleYn=")
        txt = await (await ctx.request.get(url)).text()
        for no, t in re.findall(r'"logNo"\s*:\s*"?(\d+)"?.*?"title"\s*:\s*"(.*?)"', txt):
            if _norm(urllib.parse.unquote_plus(t)) == n:
                return no
    return None


def check_lines(body: str, k: int = 3) -> list[str]:
    """새 글에만 있을 법한 평문 줄 몇 개. 발행 후 글 페이지에 실렸는지 대 보는 데 쓴다."""
    plain = [ln.strip() for ln in body.splitlines()
             if len(ln.strip()) >= 20 and not re.search(r"[\[\]()*|>:!+#_`]", ln)]
    if len(plain) <= k:
        return plain
    return [plain[0], plain[len(plain) // 2], plain[-1]]


async def read_live(ctx, log_no: str) -> dict:
    """글 페이지(로그인한 눈)에서 제목·본문 글자·그림 수를 읽는다."""
    page = await ctx.new_page()
    await page.goto(S.POST_URL.format(blog_id=os.environ["NAVER_BLOG_ID"], log_no=log_no),
                    wait_until="domcontentloaded")
    await asyncio.sleep(4)
    frame = await get_editor_frame(page)   # 글 페이지도 #mainFrame 을 쓴다
    got = await frame.evaluate("""() => {
      const box = document.querySelector('.se-main-container') || document.body;
      return {
        title: ((document.querySelector('.se-title-text, .pcol1') || {}).innerText || '').trim(),
        text: box.innerText || '',
        images: box.querySelectorAll('.se-component.se-image').length,
      };
    }""")
    await page.close()
    return got


async def main(args) -> int:
    folder = Path(args.folder)
    head_title, body = split_title((folder / "post.md").read_text(encoding="utf-8"))
    meta = read_meta(folder)
    title = meta.get("title") or head_title
    title_now = args.title_now or title
    cover = find_cover(folder, body)
    body, problems = preflight(folder, body)
    if problems:
        log("사전 확인 실패:", problems)
        return 2

    t0 = time.time()
    async with Session() as ctx:
        log_no = args.log_no or await find_log_no(ctx, title_now)
        if not log_no:
            log(f"발행된 글 목록에서 '{title_now}' 를 못 찾았다. --log-no 로 글 번호를 준다")
            return 2
        page = await ctx.new_page()
        try:
            frame = await goto_update(page, os.environ["NAVER_BLOG_ID"], log_no)
        except EditorError as e:
            log("수정 화면을 못 열었다:", e)
            return 4
        await snapshot(ctx)

        old = await frame.evaluate(DOC_TEXT)
        if _norm(old["title"]) != _norm(title_now) and not args.force:
            log(f"수정 화면의 글이 대상이 아니다 — 실린 제목 '{old['title']}' / 기대한 제목 '{title_now}'")
            log("제목을 바꿔 올리는 중이면 --title-now 에 지금 네이버 제목을 준다")
            return 4
        stamp = dt.datetime.now(KST).strftime("%Y%m%d-%H%M")
        backup = folder / f"naver-before-{stamp}.txt"
        backup.write_text(f"logNo {log_no}\n제목 {old['title']}\n그림 {old['images']}장\n\n{old['body']}\n",
                          encoding="utf-8", newline="\n")
        log(f"글 {log_no} | 옛 제목 '{old['title']}' | 컴포넌트 {old['comps']}개, 그림 {old['images']}장 → {backup.name}")

        cleared = await clear_document(page, frame)
        log("비웠다:", f"컴포넌트 {cleared['comps']}개")
        notes = await write_post(page, title, body, cover=str(cover) if cover else None)
        frame = await get_editor_frame(page)
        rep_ok = await set_rep_image(page, frame, 0) if cover else None
        rep_at = await rep_image_index(frame)
        rows = await frame.evaluate(DUMP)
        log(f"다시 쓰기 {time.time() - t0:.0f}초 | 기록 중 확인할 것:",
            [n for n in notes if "글감" in n or "누락" in n or "실패" in n])
        stop = inspect(rows, body, meta, notes, cover, rep_ok, rep_at)
        new = await frame.evaluate(DOC_TEXT)
        if _norm(new["title"]) != _norm(title):
            stop.append(f"제목이 다르게 들어갔다('{new['title'][:20]}')")

        if stop or args.dry_run:
            # 저장도 발행도 안 누르고 닫는다. 블로그의 글은 옛 그대로다.
            log("발행 안 함 —", ("걸린 점검: " + ", ".join(stop)) if stop else "[dry-run] 점검 통과",
                "| 블로그의 글은 그대로다")
            return 3 if stop else 0

        await open_publish_panel(page, frame)
        confirm = await S.first(frame, S.PUBLISH_CONFIRM)
        if not confirm:
            log("발행 버튼을 못 찾음 — 발행 안 함, 블로그의 글은 그대로다")
            return 3
        await confirm.click()
        await page.wait_for_timeout(6000)
        log("발행 누른 뒤 주소:", page.url)

        live = await read_live(ctx, log_no)
        want_images = len(re.findall(r"^\s*!\[", body, re.M)) + (1 if cover else 0)
        text = _norm(live["text"])
        missing = [ln[:30] for ln in check_lines(body) if _norm(ln) not in text]
        log(f"글 페이지 확인 | 그림 {live['images']}/{want_images} | 새 글 줄 없음: {missing or 0}")
        if missing or live["images"] != want_images:
            log("새 내용을 다 확인하지 못했다 — 네이버에서 직접 본다:",
                S.POST_URL.format(blog_id=os.environ["NAVER_BLOG_ID"], log_no=log_no))
            return 6
        log(f"수정 발행 확인: {log_no} '{title}'")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--log-no", help="고칠 글 번호(주소 끝 숫자). 없으면 제목으로 찾는다")
    ap.add_argument("--title-now", help="지금 네이버에 걸린 제목. 제목을 바꿔 올릴 때만 준다")
    ap.add_argument("--dry-run", action="store_true", help="비우고 다시 써서 점검까지, 발행은 안 누른다")
    ap.add_argument("--force", action="store_true", help="실린 제목이 달라도 그 번호의 글을 고친다")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    os.environ.setdefault("NAVER_BLOG_ID", "leetkey_lab")
    os.environ.setdefault("NAVER_DIVIDER_STYLE", "line2")
    sys.exit(asyncio.run(main(ap.parse_args())))
