"""글 폴더(post.md, meta.json, images/)를 네이버에 넣고 점검한 뒤 임시저장한다. 원하면 예약 발행까지.

    uv run python scripts/draft_folder.py <글 폴더>
    uv run python scripts/draft_folder.py <글 폴더> --reserve "2026-09-17 06:30"            # 예약 발행
    uv run python scripts/draft_folder.py <글 폴더> --reserve "2026-09-17 06:30" --dry-run  # 발행 버튼 직전까지

환경변수: NAVER_BLOG_ID(필수), NAVER_DIVIDER_STYLE(line2 권장), NAVER_KEEP_OPEN(사람이 볼 때만 1),
NAVER_TOPIC(주제, 기본 비즈니스·경제. meta.json 의 topic 이 우선),
NAVER_COVER(대표 이미지로 쓸 표지 파일. 기본은 images/00-cover.*, 0/off 면 넣지 않음)

표지(`images/00-cover.png`)가 있으면 본문 맨 앞에 넣고 대표 이미지로 지정한다.
네이버는 본문에 들어간 이미지 중에서만 대표를 고르게 해서, 따로 올릴 방법이 없다.
본문이 이미 그 파일을 쓰고 있으면 두 번 넣지 않는다.

예약 발행은 아래 점검을 전부 통과해야만 한다. 하나라도 걸리면 임시저장만 남기고 멈춘다.
- 서식 이상 0, 구분선 없이 붙은 소제목 0, 체험 링크 수가 meta.json ref_codes 수와 같음
- 글쓰기 기록에 '누락', '실패' 없음
- 표지가 있으면 대표 이미지로 지정됨
- 예약 시각이 지금보다 15분 넘게 뒤
- 발행 레이어의 예약 날짜, 시, 분이 화면에서 정확히 그 값
발행 뒤에는 '예약 발행 N건' 숫자가 1 늘었는지 확인한다.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from naver_blog_mcp import selectors as S  # noqa: E402
from naver_blog_mcp.editor import (  # noqa: E402
    EditorError, close_draft_list, close_publish_panel, delete_draft, draft_count, get_editor_frame, goto_editor,
    list_drafts, open_draft_list, open_publish_panel, rep_image_index, reserved_count, set_category,
    set_reservation, set_rep_image, set_tags, set_topic,
    set_visibility,
    write_post,
)
from naver_blog_mcp.server import find_cover, preflight, read_meta, split_title  # noqa: E402
from naver_blog_mcp.session import STATE, Session, snapshot  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))

# 이 도구가 네이버에 올린 글을 적어 두는 장부. 쿠키와 같은 폴더(커밋 제외)에 둔다.
# 같은 글을 두 번 올리는 사고를 막으려고 쓴다(2026-09-23 메타 글 2회 발행,
# 2026-09-24 LG 글 예약 1건 + 임시저장 1건).
LEDGER = STATE.parent / "drafted.json"

DUMP = r"""
() => [...document.querySelectorAll('.se-component')].flatMap(c => {
  const kind = c.classList.contains('se-text') ? 'text'
    : c.classList.contains('se-quotation') ? 'quote'
    : c.classList.contains('se-horizontalLine') ? 'hr'
    : c.classList.contains('se-image') ? 'image'
    : c.classList.contains('se-material') ? 'material'
    : c.classList.contains('se-oembed') ? 'video'
    : c.classList.contains('se-documentTitle') ? 'title' : 'other';
  if (kind === 'hr' || kind === 'material' || kind === 'image' || kind === 'video')
    return [{kind, t: kind === 'image' || kind === 'hr' ? '' : c.innerText.split(String.fromCharCode(10)).join(' '), style: c.className}];
  return [...c.querySelectorAll('.se-text-paragraph')].map(p => {
    const t = p.innerText.split(String.fromCharCode(8203)).join('').trim();
    const flags = new Set();
    for (const s of p.querySelectorAll('span.__se-node')) {
      [...s.classList].filter(x => x.startsWith('se-fs')).forEach(x => flags.add(x));
      if (s.querySelector('b')) flags.add('B');
      if (s.querySelector('i')) flags.add('I');
      if (s.querySelector('u')) flags.add('U');
      if (s.classList.contains('se-link')) {
        const h = s.getAttribute('data-href') || '';
        // 유입 코드는 두 모양으로 온다: ...apply?ref={코드} 와 leetkey.kr/l|r|y/{코드}
        const m = h.match(/[?&]ref=([^&#]+)/) || h.match(/leetkey\.kr\/(?:l|r|y)\/([^/?#]+)/);
        flags.add('L:' + (m ? m[1] : undefined));
      }
    }
    return {kind, t, style: [...flags].join(' ')};
  });
})
"""


def log(*a):
    print(*a, flush=True)


def _norm(t: str) -> str:
    """제목 비교용. 공백만 맞춘다. 네이버가 앞뒤 공백을 다듬는 일이 있다."""
    return " ".join((t or "").split())


def ledger_read() -> list[dict]:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))["posts"]
    except Exception:
        return []


def ledger_find(title: str) -> dict | None:
    n = _norm(title)
    return next((p for p in ledger_read() if _norm(p.get("title", "")) == n), None)


def ledger_add(title: str, reserved_for: dt.datetime | None) -> None:
    """올린 글을 장부에 적는다. 실패해도 본 작업을 망치지 않는다."""
    posts = ledger_read()
    posts.append({
        "title": _norm(title),
        "at": dt.datetime.now(KST).isoformat(timespec="minutes"),
        "reserved_for": f"{reserved_for:%Y-%m-%d %H:%M}" if reserved_for else None,
        "blog": os.environ.get("NAVER_BLOG_ID", ""),
    })
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        tmp = LEDGER.with_suffix(".tmp")
        tmp.write_text(json.dumps({"posts": posts[-500:]}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, LEDGER)
    except Exception as e:
        log("장부를 못 적었다(계속 진행):", e)


async def blog_has_title(ctx, title: str) -> bool:
    """이미 발행된 글 중에 같은 제목이 있나. 예약본은 여기 안 나온다."""
    blog = os.environ.get("NAVER_BLOG_ID", "")
    url = (f"https://blog.naver.com/PostTitleListAsync.naver?blogId={blog}&currentPage=1"
           "&countPerPage=30&categoryNo=0&parentCategoryNo=0&viewdate=&range=&type="
           "&pagingType=&noTitleYn=")
    try:
        txt = await (await ctx.request.get(url)).text()
    except Exception as e:
        log("블로그 글 목록을 못 읽었다(계속 진행):", type(e).__name__)
        return False
    n = _norm(title)
    return any(_norm(urllib.parse.unquote_plus(t)) == n
               for t in re.findall(r'"title"\s*:\s*"(.*?)"', txt))


async def already_there(ctx, page, title: str) -> str:
    """같은 글이 이미 올라가 있으면 어디에 있는지 한 줄로 돌려준다. 없으면 빈 문자열.

    세 군데를 본다. 장부가 먼저다(공짜이고, 예약본까지 잡는 유일한 방법이다).
    예약 목록은 네이버가 따로 안 준다.
    """
    hit = ledger_find(title)
    if hit:
        where = f"이 도구가 {hit.get('at')} 에 올렸다"
        if hit.get("reserved_for"):
            where += f" (예약 {hit['reserved_for']})"
        return where
    if await blog_has_title(ctx, title):
        return "블로그에 이미 발행돼 있다"
    # 편집기가 덜 떴을 때 목록을 못 읽는 일이 있다. 한 번 더 해 본다
    drafts = None
    for attempt in (1, 2):
        try:
            frame = await get_editor_frame(page)
            await open_draft_list(page, frame)
            drafts = await list_drafts(page, frame)
            await close_draft_list(page, frame)
            break
        except Exception as e:
            if attempt == 2:
                log("임시저장함을 못 읽었다(계속 진행):", type(e).__name__)
                return ""
            await page.wait_for_timeout(2000)
    n = _norm(title)
    return next((f"임시저장함에 있다({d[1]})" for d in drafts or [] if _norm(d[0]) == n), "")


async def main(args) -> int:
    folder = Path(args.folder)
    head_title, body = split_title((folder / "post.md").read_text(encoding="utf-8"))
    meta = read_meta(folder)
    title = meta.get("title") or head_title
    cover = find_cover(folder, body)
    body, problems = preflight(folder, body)
    if problems:
        log("사전 확인 실패:", problems)
        return 2

    when = None
    if args.reserve:
        when = dt.datetime.strptime(args.reserve, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        if when - dt.datetime.now(KST) < dt.timedelta(minutes=15):
            log(f"예약 시각 {when:%m-%d %H:%M} 이 지금보다 15분 넘게 뒤가 아님. 임시저장만 한다")
            when = None

    headings = {line[3:].strip() for line in body.splitlines() if line.startswith("## ")}
    t0 = time.time()
    async with Session() as ctx:
        page = await ctx.new_page()
        await goto_editor(page, os.environ["NAVER_BLOG_ID"])
        await snapshot(ctx)

        # 같은 글을 두 번 올리지 않는다. 부르는 데가 둘이라(루틴, 발행 워커) 한쪽이
        # 이미 올린 걸 다른 쪽이 모르고 또 올리는 사고가 났다.
        if not args.force:
            where = await already_there(ctx, page, title)
            if where:
                log(f"이미 올라간 글이다 — {where}")
                log(f"제목: {title}")
                log("정말 다시 올리려면 --force 를 준다.")
                return 7

        notes = await write_post(page, title, body, cover=str(cover) if cover else None)
        frame = await get_editor_frame(page)
        # 표지를 본문 맨 앞에 넣었으면 그것을 대표 이미지로 굳힌다. 첫 이미지가 기본
        # 대표라 대개 이미 지정돼 있지만, 기본값에 기대지 않고 확인한다.
        rep_ok = await set_rep_image(page, frame, 0) if cover else None
        rep_at = await rep_image_index(frame)
        rows = await frame.evaluate(DUMP)
        log(f"작성 {time.time() - t0:.0f}초 | 기록 중 확인할 것:", [n for n in notes if "글감" in n or "누락" in n or "실패" in n])

        bad = []
        for r in rows:
            if r["kind"] != "text" or not r["t"]:
                continue
            is_head, big = r["t"] in headings, "se-fs19" in r["style"]
            if is_head and not ("B" in r["style"] and big):
                bad.append(f"제목 서식 빠짐: {r['t'][:24]}")
            if not is_head and big:
                bad.append(f"본문이 제목 크기: {r['t'][:24]}")
        seq = [r for r in rows if r["kind"] in ("hr", "text") and (r["kind"] == "hr" or r["t"])]
        glued = [seq[k]["t"][:20] for k in range(1, len(seq)) if seq[k]["t"] in headings and seq[k - 1]["kind"] != "hr"]
        links = [s for r in rows for s in r["style"].split() if s.startswith("L:")]
        want_links = len(re.findall(r"\]\(https?://", body))
        videos = [r["t"][:30] for r in rows if r["kind"] == "video"]
        want_videos = len(re.findall(r"^\s*:::\s*video\s", body, re.M))
        log("서식 이상:", bad or 0, "| 붙은 소제목:", glued or 0, "| 링크:", links,
            "| 그림:", sum(1 for r in rows if r["kind"] == "image"), "| 구분선:", sum(1 for r in rows if r["kind"] == "hr"),
            "| 글감 카드:", sum(1 for r in rows if r["kind"] == "material"), "| 영상:", videos)
        log("표지:", cover.name if cover else "없음",
            "| 대표 이미지:", "못 지정" if rep_ok is False else (f"{rep_at}번째 그림" if rep_at is not None else "없음"))

        stop = []
        if bad:
            stop.append("서식 이상")
        if glued:
            stop.append("붙은 소제목")
        if len([x for x in links if x != "L:undefined"]) != len(meta.get("ref_codes", [])):
            stop.append(f"체험 링크 {len(links)} != ref_codes {len(meta.get('ref_codes', []))}")
        if len(links) != want_links:
            log(f"링크 누락: 글에는 {want_links}개, 화면에는 {len(links)}개")
            stop.append("링크 누락")
        if len(videos) != want_videos:
            log(f"영상 누락: 글에는 {want_videos}개, 화면에는 {len(videos)}개")
            stop.append("영상 누락")
        if any("누락" in n or "실패" in n for n in notes):
            stop.append("글쓰기 기록에 누락/실패")
        if cover and not rep_ok:
            stop.append("대표 이미지 지정")

        before = await draft_count(frame)
        await open_publish_panel(page, frame)
        try:
            await set_category(page, frame, meta["category"])
        except Exception as e:
            stop.append("카테고리")
            log("카테고리 못 넣음:", meta["category"], "|", e)
        topic = meta.get("topic") or os.environ.get("NAVER_TOPIC", "")
        if topic:
            try:
                log("주제:", await set_topic(page, frame, topic))
            except Exception as e:
                stop.append("주제")
                log("주제 못 넣음:", topic, "|", e)
        n = await set_tags(page, frame, meta["tags"])
        await close_publish_panel(page, frame)
        if args.dry_run:
            log(f"[dry-run] 임시저장하지 않음, 태그 {n}개")
            drafts_to_clean = 0
        else:
            await (await S.first(frame, S.SAVE_DRAFT)).click()
            await page.wait_for_timeout(2500)
            log(f"임시저장 {before} -> {await draft_count(frame)}, 태그 {n}개")
            drafts_to_clean = 5

        for _ in range(drafts_to_clean):
            try:
                await close_draft_list(page, frame)
                drafts = await list_drafts(page, frame)
                if sum(1 for t, _ in drafts if t == title) < 2:
                    break
                log("예전 본 삭제:", await delete_draft(page, frame, title=title, index=2))
            except Exception as e:
                log("예전 본 삭제 멈춤:", e)
                break
        try:
            await close_draft_list(page, frame)
        except Exception:
            pass

        if not when:
            if not args.dry_run:
                ledger_add(title, None)
            log("끝. 임시저장만")
            return 0
        if stop:
            if not args.dry_run:
                ledger_add(title, None)
            log("예약 발행 안 함. 걸린 점검:", stop, "| 임시저장본은 남아 있다")
            return 3

        try:
            await set_visibility(page, frame, "public")
            state = await set_reservation(page, frame, when)
            log("예약 화면 값 확인:", state)
        except EditorError as e:
            log("예약 설정 실패, 발행 안 함:", e)
            try:
                await close_publish_panel(page, frame)
            except Exception:
                pass
            return 4

        count0 = await reserved_count(frame)
        if args.dry_run:
            log(f"[dry-run] 발행 버튼 직전에서 멈춤. 예약 발행 수 {count0}")
            await frame.locator(S.RESERVE_NOW[0]).first.evaluate("e => e.click()")
            await close_publish_panel(page, frame)
            return 0

        confirm = await S.first(frame, S.PUBLISH_CONFIRM)
        if not confirm:
            log("최종 발행 버튼을 못 찾음, 발행 안 함")
            return 5
        await confirm.click()
        await page.wait_for_timeout(5000)
        log("발행 버튼 누른 뒤 주소:", page.url)

        check = await ctx.new_page()
        await goto_editor(check, os.environ["NAVER_BLOG_ID"])
        count1 = await reserved_count(await get_editor_frame(check))
        log(f"예약 발행 수 {count0} -> {count1}")
        # 늘었든 아니든 네이버에는 글이 올라갔다. 장부에 적어야 다음 실행이 또 안 올린다
        ledger_add(title, when)
        if count0 is not None and count1 == count0 + 1:
            log(f"예약 발행 확인: {when:%Y-%m-%d %H:%M} KST '{title}'")
            return 0
        log("예약 발행 수가 늘지 않음. 네이버에서 직접 확인 필요")
        return 6


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--reserve", help="예약 발행 시각 KST, 'YYYY-MM-DD HH:MM' (분은 10분 단위)")
    ap.add_argument("--dry-run", action="store_true", help="예약 값까지 맞추고 발행 버튼은 누르지 않는다")
    ap.add_argument("--force", action="store_true",
                    help="같은 제목이 이미 올라가 있어도 그냥 올린다(중복 확인 건너뜀)")
    os.environ.setdefault("NAVER_BLOG_ID", "leetkey_lab")
    # 리트키랩 연구소 글은 전부 블로그 홈 주제 '비즈니스·경제'로 분류한다(대표 2026-09-16)
    os.environ.setdefault("NAVER_TOPIC", "비즈니스·경제")
    sys.exit(asyncio.run(main(ap.parse_args())))
