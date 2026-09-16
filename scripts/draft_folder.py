"""글 폴더(post.md, meta.json, images/)를 네이버에 넣고 점검한 뒤 임시저장한다. 원하면 예약 발행까지.

    uv run python scripts/draft_folder.py <글 폴더>
    uv run python scripts/draft_folder.py <글 폴더> --reserve "2026-09-17 06:30"            # 예약 발행
    uv run python scripts/draft_folder.py <글 폴더> --reserve "2026-09-17 06:30" --dry-run  # 발행 버튼 직전까지

환경변수: NAVER_BLOG_ID(필수), NAVER_DIVIDER_STYLE(line2 권장), NAVER_KEEP_OPEN(사람이 볼 때만 1),
NAVER_TOPIC(주제, 기본 비즈니스·경제. meta.json 의 topic 이 우선)

예약 발행은 아래 점검을 전부 통과해야만 한다. 하나라도 걸리면 임시저장만 남기고 멈춘다.
- 서식 이상 0, 구분선 없이 붙은 소제목 0, 체험 링크 수가 meta.json ref_codes 수와 같음
- 글쓰기 기록에 '누락', '실패' 없음
- 예약 시각이 지금보다 15분 넘게 뒤
- 발행 레이어의 예약 날짜, 시, 분이 화면에서 정확히 그 값
발행 뒤에는 '예약 발행 N건' 숫자가 1 늘었는지 확인한다.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from naver_blog_mcp import selectors as S  # noqa: E402
from naver_blog_mcp.editor import (  # noqa: E402
    EditorError, close_draft_list, close_publish_panel, delete_draft, draft_count, get_editor_frame, goto_editor,
    list_drafts, open_publish_panel, reserved_count, set_category, set_reservation, set_tags, set_topic,
    set_visibility,
    write_post,
)
from naver_blog_mcp.server import preflight, read_meta, split_title  # noqa: E402
from naver_blog_mcp.session import Session, snapshot  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))

DUMP = """
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
      if (s.classList.contains('se-link')) flags.add('L:' + (s.getAttribute('data-href') || '').split('ref=')[1]);
    }
    return {kind, t, style: [...flags].join(' ')};
  });
})
"""


def log(*a):
    print(*a, flush=True)


async def main(args) -> int:
    folder = Path(args.folder)
    head_title, body = split_title((folder / "post.md").read_text(encoding="utf-8"))
    meta = read_meta(folder)
    title = meta.get("title") or head_title
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
        notes = await write_post(page, title, body)
        frame = await get_editor_frame(page)
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
            log("끝. 임시저장만")
            return 0
        if stop:
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
    os.environ.setdefault("NAVER_BLOG_ID", "leetkey_lab")
    # 리트키랩 연구소 글은 전부 블로그 홈 주제 '비즈니스·경제'로 분류한다(대표 2026-09-16)
    os.environ.setdefault("NAVER_TOPIC", "비즈니스·경제")
    sys.exit(asyncio.run(main(ap.parse_args())))
