"""셀렉터 실측 스크립트. 코드 짜기 전에 이걸 먼저 돌린다.

에디터를 열고 selectors.py 의 후보들이 실제로 잡히는지 하나씩 확인한 뒤,
잡힌 것만 남기고 못 잡은 건 DOM 을 덤프해서 보여준다.

  uv run python verify_selectors.py              # 창 띄움 (기본)
  HEADLESS=true uv run python verify_selectors.py  # 창 없이

결과 표기:
  OK      보이는 요소로 잡힘. 그대로 쓸 수 있다.
  HIDDEN  DOM 에는 있지만 안 보임. S.first() 는 visible 을 요구하므로 실전에서는 실패한다.
  MISS    아예 없음.

카테고리/태그/발행확인은 발행 레이어 안에만 존재하므로 2단계로 나눠 검사한다.
발행 레이어는 열기만 하고 최종 발행 버튼은 절대 누르지 않는다.
"""

import asyncio
import os
import sys

from src.naver_blog_mcp import selectors as S
from src.naver_blog_mcp.session import Session
from src.naver_blog_mcp.editor import get_editor_frame, dismiss_popups, wait_for_editor

BLOG_ID = os.getenv("NAVER_BLOG_ID", "")
# 프로브에 추가로 포함할 셀렉터 (셀렉터는 selectors.py 에서 가져온다)
_PROBE_EXTRA = ", " + S.TITLE_PLACEHOLDER[-1].split()[-1]

# 에디터 첫 화면에 있는 것들
EDITOR_TARGETS = {
    "TITLE": S.TITLE,
    "BODY": S.BODY,
    "IMAGE_BUTTON": S.IMAGE_BUTTON,
    "PUBLISH_OPEN": S.PUBLISH_OPEN,
    "SAVE_DRAFT": S.SAVE_DRAFT,
}
# 발행 레이어를 열어야 나타나는 것들
PUBLISH_TARGETS = {
    "CATEGORY_OPEN": S.CATEGORY_OPEN,
    "TAG_INPUT": S.TAG_INPUT,
    "PUBLISH_CONFIRM": S.PUBLISH_CONFIRM,
}

# 에디터 안에서 "클릭 가능하거나 입력 가능한" 것들만 추린다. 전체 HTML 을 뒤지는 것보다 빠르다.
_PROBE_JS = """
(extra) => {
  const out = [];
  const sel = 'button, a[role=button], [contenteditable=true], input, textarea, ' +
              '[aria-label], [data-name], [data-a11y-title]' + extra;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const txt = (el.innerText || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
    out.push({
      tag: el.tagName.toLowerCase(),
      visible: !!(r.width && r.height),
      text: txt,
      aria: el.getAttribute('aria-label') || '',
      name: el.getAttribute('data-name') || '',
      a11y: el.getAttribute('data-a11y-title') || '',
      ph: el.getAttribute('placeholder') || '',
      id: el.id || '',
      cls: (el.className && el.className.baseVal !== undefined
              ? el.className.baseVal : String(el.className || '')).slice(0, 80),
    });
  }
  return out;
}
"""

# dismiss_popups 회귀 검사용. 예전에 button:has-text('취소') 가 툴바 "취소선" 을 눌렀다.
# 셀렉터는 인자로 받는다 — 하드코딩하면 selectors.py 만 갱신됐을 때
# 이 검사가 조용히 null 만 돌려주며 영원히 통과한다 (CLAUDE.md 규칙 2).
_STRIKE_JS = """
([sel, onClass]) => {
  const b = document.querySelector(sel);
  return b ? b.className.includes(onClass) : null;
}
"""


def _fmt_probe(rows, label):
    lines = [f"===== {label} — 클릭/입력 가능 요소 {len(rows)}개 =====", ""]
    for r in rows:
        vis = "보임" if r["visible"] else "숨김"
        bits = [f"<{r['tag']}> [{vis}]"]
        for key, tag in (("text", "text"), ("aria", "aria-label"), ("name", "data-name"),
                         ("a11y", "data-a11y-title"), ("ph", "placeholder"),
                         ("id", "id"), ("cls", "class")):
            if r[key]:
                bits.append(f"{tag}={r[key]!r}")
        lines.append("  " + "  ".join(bits))
    lines.append("")
    return "\n".join(lines)


async def _check(frame, targets: dict) -> list[str]:
    """S.first 와 **같은 판정**을 쓴다.

    예전에는 locator(sel).first.is_visible() 로 봤는데, 그건 S.first 가 버린 방식이다
    (숨김 요소가 앞에 오면 실패). 그 상태로 두면 실전에서 잘 되는 셀렉터를
    HIDDEN/MISS 로 잘못 보고해 엉뚱한 곳을 고치게 만든다.
    """
    bad = []
    for name, cands in targets.items():
        usable = [c for c in cands if "{name}" not in c]  # 포맷 필요한 건 제외
        hit = hidden = None
        # 먼저 준비될 때까지 기다린 뒤, 어느 후보가 걸렸는지 하나씩 되짚는다.
        if await S.first(frame, usable, timeout=5000):
            for s in usable:
                if await S.first(frame, [s], timeout=0):
                    hit = s
                    break
        else:
            for s in usable:
                try:
                    if await frame.locator(s).count():
                        hidden = s
                        break
                except Exception:
                    continue
        if hit:
            print(f"{name:<16} {'OK':<8} {hit}")
        elif hidden:
            print(f"{name:<16} {'HIDDEN':<8} {hidden}")
            bad.append(name)
        else:
            print(f"{name:<16} {'MISS':<8} -")
            bad.append(name)
    return bad


async def main() -> None:
    if not BLOG_ID:
        print("NAVER_BLOG_ID 가 비어 있습니다.  export NAVER_BLOG_ID=<블로그아이디>")
        sys.exit(1)

    async with Session() as ctx:  # HEADLESS 환경변수를 따른다 (기본 headed)
        page = await ctx.new_page()
        # networkidle 금지: 네이버 에디터는 연결을 계속 열어둬서 idle 에 도달하지 않는다.
        await page.goto(S.WRITE_URL.format(blog_id=BLOG_ID), wait_until="domcontentloaded")

        # 세션이 죽었으면 전 항목이 MISS 로 나온다. 셀렉터 탓으로 오해하지 않게 먼저 가른다.
        if "nid.naver.com" in page.url or "login" in page.url.lower():
            print(f"로그인 페이지로 튕겼습니다: {page.url}")
            print("세션 만료 — uv run python login_setup.py 를 다시 실행하세요.")
            sys.exit(1)

        await wait_for_editor(page)
        frame = await get_editor_frame(page)
        in_iframe = frame is not page.main_frame
        print(f"\n에디터 프레임: {'iframe' if in_iframe else 'main_frame (iframe 못 찾음)'}")
        print(f"URL: {page.url}")

        # 툴바가 그려질 때까지 기다린 뒤 검사한다. 너무 일찍 재면 양쪽 다 None 이
        # 나오고 None == None 이라 회귀가 있어도 조용히 통과한다.
        await asyncio.sleep(1.5)
        strike_args = [S.STRIKE_BUTTON[-1], S.TOGGLE_ON_CLASS]
        before = await frame.evaluate(_STRIKE_JS, strike_args)
        if before is None:
            print("\n!! 취소선 버튼을 못 찾아 dismiss_popups 회귀 검사를 못 했습니다.")
            print(f"   selectors.STRIKE_BUTTON 확인 필요: {S.STRIKE_BUTTON[-1]}")
        await dismiss_popups(frame)
        await asyncio.sleep(0.5)
        after = await frame.evaluate(_STRIKE_JS, strike_args)
        if before is not None and before != after:
            print(f"\n!! dismiss_popups 가 툴바를 건드렸습니다 (취소선 {before} -> {after}).")
            print("   RECOVER_POPUP_CANCEL 의 스코프를 좁히세요.")

        print(f"\n{'항목':<16} {'결과':<8} 셀렉터")
        print("-" * 78)
        bad = await _check(frame, EDITOR_TARGETS)

        # --- 2단계: 발행 레이어 (열기만 한다. 최종 발행은 누르지 않는다) ---
        print("-" * 78)
        opener = await S.first(frame, S.PUBLISH_OPEN, timeout=5000)
        if not opener:
            print("발행 레이어를 열 수 없어 카테고리/태그/발행확인은 검사 못 함")
            bad += list(PUBLISH_TARGETS)
        else:
            await opener.click()
            await asyncio.sleep(2.5)
            bad += await _check(frame, PUBLISH_TARGETS)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

        if bad:
            print(f"\n확정 못한 항목: {', '.join(bad)}")
            with open("dom_dump.html", "w", encoding="utf-8") as f:
                f.write(await frame.content())
            probe = _fmt_probe(await frame.evaluate(_PROBE_JS, _PROBE_EXTRA), "에디터 프레임")
            if in_iframe:
                probe += "\n" + _fmt_probe(
                    await page.main_frame.evaluate(_PROBE_JS, _PROBE_EXTRA), "최상위 문서"
                )
            with open("dom_probe.txt", "w", encoding="utf-8") as f:
                f.write(probe)
            print("  -> dom_dump.html  (원본 DOM)")
            print("  -> dom_probe.txt  (클릭/입력 가능 요소 목록 — 이걸 먼저 보세요)")
        else:
            print("\n전부 확정됨.")

        # 사람이 브라우저를 확인할 수 있게 붙잡아 둔다. 파이프/headless 일 때는 그냥 종료.
        if sys.stdin.isatty():
            print("\n브라우저 열어둡니다. 확인 후 엔터.")
            await asyncio.to_thread(input)


if __name__ == "__main__":
    asyncio.run(main())
