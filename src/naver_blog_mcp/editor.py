"""에디터 구동부. 셀렉터는 전부 selectors.py 에서 가져온다."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from pathlib import Path

from playwright.async_api import Frame, Page

from . import selectors as S
from .ir import Segment, Span, Block, parse_markdown, segment


class EditorError(RuntimeError):
    pass


async def get_editor_frame(page: Page) -> Frame:
    for sel in S.EDITOR_FRAME:
        el = await page.query_selector(sel)
        if el:
            fr = await el.content_frame()
            if fr:
                return fr
    # iframe 없이 같은 문서에 렌더되는 경우도 있음
    return page.main_frame


async def dismiss_popups(frame: Frame) -> None:
    """진행을 막는 레이어들을 닫는다. 없으면 조용히 통과.

    도움말 패널은 로그인 직후 첫 글쓰기 화면에서 자동으로 열리고 **툴바 클릭을
    통째로 막는다**. login_setup.py 를 돌린 직후 첫 툴 호출이 여기 걸린다.
    """
    for cands, label in ((S.HELP_PANEL_CLOSE, "도움말"),
                         (S.RECOVER_POPUP_CANCEL, "복구")):
        btn = await S.first(frame, cands, timeout=1500)
        if btn:
            try:
                await btn.click()
                await asyncio.sleep(0.4)
            except Exception:
                pass  # 닫기 실패가 글쓰기 전체를 막지는 않게


async def wait_for_editor(page: Page, timeout: float = 20.0) -> None:
    """에디터 iframe 이 붙을 때까지 기다린다.

    get_editor_frame() 은 못 찾으면 조용히 main_frame 으로 폴백한다. 그대로 두면
    "iframe 이 없다" 와 "아직 안 떴다" 가 구분되지 않은 채 엉뚱한 프레임을 쓰게 되고,
    결국 "제목 영역을 못 찾음 — selectors.TITLE 갱신 필요" 처럼 원인과 무관한
    메시지가 나온다. 그래서 못 찾으면 여기서 명확히 실패시킨다.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        for sel in S.EDITOR_FRAME:
            if await page.query_selector(sel):
                await asyncio.sleep(1.5)  # 프레임 내부 렌더 여유
                return
        await asyncio.sleep(0.5)
    raise EditorError(
        f"에디터 프레임이 {timeout:.0f}초 안에 안 붙었습니다 "
        f"(selectors.EDITOR_FRAME 확인). URL: {page.url}")


async def goto_editor(page: Page, blog_id: str) -> Frame:
    """글쓰기 화면을 열고 에디터 프레임을 돌려준다.

    networkidle 을 쓰지 말 것. 네이버 에디터는 연결을 계속 열어두므로 idle 에 도달하지
    않고, 실측에서 TargetClosedError 로 터졌다.
    """
    await page.goto(S.WRITE_URL.format(blog_id=blog_id), wait_until="domcontentloaded")
    if "nid.naver.com" in page.url:
        raise EditorError("세션 만료 — uv run python login_setup.py 재실행 필요")
    await wait_for_editor(page)
    frame = await get_editor_frame(page)
    await dismiss_popups(frame)
    return frame


# ------------------------------------------------------------ 발행 설정 레이어
# 카테고리와 태그는 에디터 첫 화면에 없다. 발행 버튼을 눌러 레이어를 열어야 DOM 에 생긴다.
# 레이어를 여는 것만으로는 발행되지 않는다 — 최종 발행은 PUBLISH_CONFIRM 클릭이다.


async def _any_visible(frame: Frame, candidates: list[str]) -> bool:
    """후보 중 보이는 요소가 하나라도 있나. S.first 와 같은 판정을 쓴다."""
    return await S.first(frame, candidates, timeout=0) is not None


async def _layer_open(frame: Frame) -> bool:
    return await _any_visible(frame, S.PUBLISH_LAYER)


async def open_publish_panel(page: Page, frame: Frame) -> None:
    if await _layer_open(frame):
        return
    opener = await S.first(frame, S.PUBLISH_OPEN)
    if not opener:
        raise EditorError("발행 버튼을 못 찾음 — selectors.PUBLISH_OPEN 갱신 필요")
    await opener.click()
    for _ in range(24):
        if await _layer_open(frame):
            await asyncio.sleep(0.6)
            return
        await asyncio.sleep(0.25)
    raise EditorError("발행 설정 레이어가 안 열림 — selectors.PUBLISH_LAYER 확인")


async def close_publish_panel(page: Page, frame: Frame) -> None:
    """Escape 로 닫는다.

    드롭다운(카테고리 등)이 열려 있으면 Escape 를 먹는 쪽이 드롭다운이라 레이어가
    안 닫힌다. 실측: 카테고리를 연 상태에서는 Escape 2번이 필요했다.
    """
    for _ in range(4):
        if not await _layer_open(frame):
            return
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.6)


def _clean_category(text: str) -> tuple[str, bool]:
    """('아반떼MD', True) 처럼 (이름, 하위여부) 로 만든다.

    하위 카테고리는 스크린리더용 span.blind 때문에 innerText 가
    "하위 카테고리\n아반떼MD" 로 나온다.
    """
    t = text.strip()
    child = t.startswith(S.CATEGORY_CHILD_MARK)
    if child:
        t = t[len(S.CATEGORY_CHILD_MARK):].strip()
    return t, child


async def _locate_all(scope, candidates: list[str]):
    """후보를 순서대로 시도해서 처음으로 하나 이상 잡히는 로케이터(전체)를 반환."""
    for sel in candidates:
        loc = scope.locator(sel)
        try:
            if await loc.count():
                return loc
        except Exception:
            continue
    return None


async def _category_items(frame: Frame):
    return await _locate_all(frame, S.CATEGORY_ITEM_ALL)


async def _open_category_dropdown(page: Page, frame: Frame):
    await open_publish_panel(page, frame)
    btn = await S.first(frame, S.CATEGORY_OPEN)
    if not btn:
        raise EditorError("카테고리 버튼을 못 찾음 — selectors.CATEGORY_OPEN 갱신 필요")
    if (await btn.get_attribute("aria-expanded")) != "true":
        await btn.click()
        await asyncio.sleep(1.0)
    return btn


async def read_categories(page: Page, frame: Frame) -> list[tuple[str, bool]]:
    """[(이름, 하위여부), ...]. 발행 레이어를 열어야 읽을 수 있다."""
    await _open_category_dropdown(page, frame)
    loc = await _category_items(frame)
    if loc is None:
        raise EditorError("카테고리 목록을 못 읽음 — selectors.CATEGORY_ITEM_ALL 갱신 필요")
    return [_clean_category(t) for t in await loc.all_inner_texts() if t.strip()]


def split_category_path(name: str) -> str:
    """"부모 > 자식" 으로 준 이름에서 실제로 고를 이름(마지막 칸)만 남긴다.

    드롭다운 항목은 부모와 자식이 각각 한 줄이라 "종목 분석 > 국장" 같은
    전체 경로로는 아무것도 못 고른다. 사람이 쓰는 표기를 그대로 받기 위해 여기서 자른다.
    """
    return name.split(">")[-1].strip() if ">" in name else name.strip()


async def set_category(page: Page, frame: Frame, name: str) -> None:
    """카테고리를 고른다. 이름이 정확히 일치하는 항목을 우선한다.

    :has-text() 는 부분 매칭이라 "캠핑" 이 "캠핑하는 남자" 를 잡는다. 그래서
    정확 일치를 먼저 훑고, 없을 때만 부분 매칭으로 떨어진다.
    "부모 > 자식" 표기로 줘도 된다. 마지막 칸으로 고른다.
    """
    name = split_category_path(name)
    await _open_category_dropdown(page, frame)
    loc = await _category_items(frame)
    if loc is not None:
        for i in range(await loc.count()):
            item = loc.nth(i)
            clean, _ = _clean_category(await item.inner_text())
            if clean == name:
                await item.click()
                await asyncio.sleep(0.8)
                return
    fallback = await S.first(frame, S.CATEGORY_ITEM, timeout=2000, name=name)
    if not fallback:
        have = [n for n, _ in await read_categories(page, frame)]
        raise EditorError(f"카테고리 '{name}' 없음. 있는 것: {', '.join(have)}")
    await fallback.click()
    await asyncio.sleep(0.8)


async def set_visibility(page: Page, frame: Frame, level: str) -> None:
    """공개 설정을 바꾼다. level: public | neighbor | both_neighbor | private

    커스텀 라디오라 input 이 화면상 클릭 대상이 아닐 수 있어 JS click() 으로 누른다.
    (2026-08-25 실측: 이 방식으로 비공개 발행 확인)
    """
    cands = S.VISIBILITY.get(level)
    if not cands:
        raise EditorError(f"알 수 없는 공개 설정: {level} (가능: {', '.join(S.VISIBILITY)})")
    await open_publish_panel(page, frame)
    for sel in cands:
        loc = frame.locator(sel).first
        try:
            if await frame.locator(sel).count():
                await loc.evaluate("e => e.click()")
                await asyncio.sleep(0.4)
                return
        except Exception:
            continue
    raise EditorError(f"공개 설정 '{level}' 을 못 찾음 — selectors.VISIBILITY 갱신 필요")


async def read_topic(frame: Frame) -> str | None:
    """발행 레이어에 지금 보이는 주제 이름. 못 읽으면 None."""
    try:
        return (await frame.locator(S.TOPIC_CURRENT).first.inner_text(timeout=2000)).strip()
    except Exception:
        return None


async def set_topic(page: Page, frame: Frame, name: str) -> str:
    """발행 설정의 주제를 고른다(예: "비즈니스·경제"). 고른 뒤 화면에 보이는 주제를 돌려준다.

    새 글쓰기 화면은 주제 칸에 블로그 기본 주제를 보여주지만, 불러온 임시저장 글 등에서는
    선택이 빠진 채 발행된 적이 있다(2026-09-16 대표 확인). 그래서 매번 명시적으로 고른다.
    """
    await open_publish_panel(page, frame)
    opener = await S.first(frame, S.TOPIC_OPEN)
    if not opener:
        raise EditorError("주제 버튼을 못 찾음 — selectors.TOPIC_OPEN 갱신 필요")
    await opener.click()
    await asyncio.sleep(0.8)
    picked = await frame.evaluate(
        """([sel, name]) => {
            for (const input of document.querySelectorAll(sel)) {
                const label = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
                if (label && label.textContent.trim() === name) { input.click(); return true; }
            }
            return false;
        }""",
        [S.TOPIC_RADIO, name],
    )
    if not picked:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        raise EditorError(f"주제 '{name}' 을 목록에서 못 찾음")
    ok = await S.first(frame, S.TOPIC_OK)
    if not ok:
        raise EditorError("주제 확인 버튼을 못 찾음 — selectors.TOPIC_OK 갱신 필요")
    await ok.click()
    await asyncio.sleep(0.8)
    now = await read_topic(frame)
    if now != name:
        raise EditorError(f"주제를 '{name}' 로 골랐는데 화면에는 '{now}'")
    return now


async def set_tags(page: Page, frame: Frame, tags: list[str]) -> int:
    """태그를 입력한다. 입력란은 발행 레이어 안에만 있다. 넣은 개수를 돌려준다."""
    await open_publish_panel(page, frame)
    ti = await S.first(frame, S.TAG_INPUT)
    if not ti:
        raise EditorError("태그 입력란을 못 찾음 — selectors.TAG_INPUT 갱신 필요")
    n = 0
    for t in tags[:30]:  # 네이버 제한: 최대 30개
        t = t.strip()
        if not t:
            continue
        await ti.click()
        await ti.press_sequentially(t, delay=20)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.35)
        n += 1
    return n


# ------------------------------------------------------------ 임시저장 목록
# 글쓰기 화면은 임시저장 글을 자동 복구하지 않는다 (복구 팝업도 없다).
# 발행하려면 이 목록에서 명시적으로 불러와야 한다. (2026-08-25 실측)


async def _draft_layer_open(frame: Frame) -> bool:
    return await _any_visible(frame, S.DRAFT_LAYER)


async def open_draft_list(page: Page, frame: Frame) -> None:
    if await _draft_layer_open(frame):
        return
    btn = await S.first(frame, S.DRAFT_LIST_OPEN)
    if not btn:
        raise EditorError("임시저장 목록 버튼을 못 찾음 — selectors.SAVE_COUNT 갱신 필요")
    await btn.click()
    for _ in range(20):
        if await _draft_layer_open(frame):
            await asyncio.sleep(0.5)
            return
        await asyncio.sleep(0.25)
    raise EditorError("임시저장 목록 레이어가 안 열림")


async def close_draft_list(page: Page, frame: Frame) -> None:
    btn = await S.first(frame, S.DRAFT_LAYER_CLOSE, timeout=1500)
    if btn:
        await btn.click()
        await asyncio.sleep(0.5)


async def list_drafts(page: Page, frame: Frame) -> list[tuple[str, str]]:
    """[(제목, 저장일시), ...]. 최신순으로 나온다."""
    await open_draft_list(page, frame)
    # 삭제 직후에는 목록이 다시 그려지는 동안 잠깐 비어 보인다. 바로 [] 를 돌려주면
    # 연달아 지울 때 "임시저장된 글이 없습니다" 로 멈춘다. 개수 표시가 0 이 아니면 기다린다.
    items = None
    for _ in range(16):
        items = await _locate_all(frame, S.DRAFT_ITEM)
        if items is not None or await draft_count(frame) == 0:
            break
        await asyncio.sleep(0.25)
    if items is None:
        return []
    out = []
    for i in range(await items.count()):
        it = items.nth(i)
        title = await _inner(it, S.DRAFT_ITEM_TITLE)
        date = await _inner(it, S.DRAFT_ITEM_DATE)
        # 제목이 안 읽혀도 건너뛰면 안 된다. 이 리스트의 인덱스가 그대로
        # load_draft/delete_draft 의 DOM 인덱스로 쓰이므로, 하나라도 빠지면
        # 이후 항목이 한 칸씩 밀려 엉뚱한 글을 발행하거나 삭제한다.
        out.append((title or f"(제목 없음 #{i + 1})", date))
    return out


async def _inner(scope, candidates: list[str]) -> str:
    loc = await _locate_all(scope, candidates)
    if loc is None:
        return ""
    try:
        return (await loc.first.inner_text()).strip()
    except Exception:
        return ""


def pick_draft(drafts: list[tuple[str, str]], title: str = "", index: int | None = None) -> int:
    """발행하거나 삭제할 글의 인덱스를 고른다. 브라우저 없이 검증할 수 있게 순수 함수로 뒀다.

    발행과 삭제는 되돌리기 어렵다. 조금이라도 애매하면 고르지 않고 예외를 던진다:
    제목이 여러 글과 맞거나, 제목 없이 호출됐는데 임시저장이 2건 이상이면 거부.

    index 는 목록 순번(1 이 최신)이다. 같은 제목으로 여러 번 임시저장했을 때 쓴다.
    title 을 같이 주면 그 순번의 제목이 맞는지 확인하고, 다르면 거부한다.
    """
    if not drafts:
        raise EditorError("임시저장된 글이 없습니다")
    listing = "\n".join(f"  {i}. {t} ({d})" for i, (t, d) in enumerate(drafts, 1))

    if index is not None:
        if not 1 <= index <= len(drafts):
            raise EditorError(f"순번 {index} 는 범위 밖입니다(1~{len(drafts)}):\n{listing}")
        if title and title not in drafts[index - 1][0]:
            raise EditorError(f"순번 {index} 의 제목이 '{title}' 과 다릅니다: {drafts[index - 1][0]}")
        return index - 1

    if not title:
        if len(drafts) > 1:
            raise EditorError(
                f"임시저장 글이 {len(drafts)}건입니다. title 로 지정하세요:\n{listing}")
        return 0

    exact = [i for i, (t, _) in enumerate(drafts) if t == title]
    if len(exact) == 1:
        return exact[0]
    hits = exact or [i for i, (t, _) in enumerate(drafts) if title in t]
    if not hits:
        raise EditorError(f"'{title}' 과 맞는 임시저장 글이 없습니다. 있는 것:\n{listing}")
    if len(hits) > 1:
        cand = "\n".join(f"  - {drafts[i][0]}" for i in hits)
        raise EditorError(f"'{title}' 이 여러 글과 맞습니다. 제목을 더 구체적으로:\n{cand}")
    return hits[0]


async def load_draft(page: Page, frame: Frame, title: str = "") -> str:
    """임시저장 글을 에디터로 불러온다. 불러온 글의 제목을 반환.

    title 이 비면 임시저장이 정확히 1건일 때만 그것을 쓴다. 여러 건이면 거부한다 —
    발행은 되돌리기 어려우므로 어느 글인지 사람이 지정하게 한다.
    """
    drafts = await list_drafts(page, frame)
    idx = pick_draft(drafts, title)
    items = await _locate_all(frame, S.DRAFT_ITEM)
    await items.nth(idx).click()
    await asyncio.sleep(3.0)  # 불러오기는 확인 팝업 없이 바로 반영된다
    if await title_is_empty(frame):
        raise EditorError("불러왔는데 제목이 비어 있습니다 — 불러오기 실패로 보입니다")
    return drafts[idx][0]


def parse_log_no(url_or_log_no: str) -> str:
    """글 URL 이나 logNo 에서 숫자 id 만 뽑는다."""
    m = re.search(r"(\d{6,})", url_or_log_no)
    if not m:
        raise EditorError(f"글 번호를 못 찾음: {url_or_log_no!r}")
    return m.group(1)


def _make_delete_dialog_handler(seen: list[str]):
    """삭제 확인 dialog 만 수락하는 핸들러.

    Playwright 는 기본적으로 dialog 를 dismiss 하므로 핸들러가 없으면 삭제가
    조용히 취소된다. 반대로 아무 dialog 나 수락하면 "로그인이 필요합니다" 같은
    다른 알림까지 삼키고 아무것도 안 지웠으면서 성공으로 보고하게 된다.
    그래서 문구를 확인하고, 아니면 dismiss 한다.
    """

    def _on_dialog(d):
        seen.append(d.message)
        if all(h in d.message for h in S.DELETE_CONFIRM_HINTS):
            asyncio.ensure_future(d.accept())
        else:
            asyncio.ensure_future(d.dismiss())

    return _on_dialog


def _assert_delete_confirmed(seen: list[str], target: str) -> None:
    if not seen:
        raise EditorError(f"삭제 확인 창이 뜨지 않았습니다 — 삭제되지 않았습니다: {target}")
    if not any(all(h in m for h in S.DELETE_CONFIRM_HINTS) for m in seen):
        got = " / ".join(m.replace("\n", " ")[:60] for m in seen)
        raise EditorError(f"삭제 확인이 아닌 알림이 떴습니다 — 삭제되지 않았습니다: {got}")


async def delete_post(page: Page, blog_id: str, url_or_log_no: str) -> str:
    """발행된 글을 삭제한다. 복구되지 않는다. 삭제한 글의 URL 을 반환.

    임시저장 삭제와는 다른 UI 다 (글 페이지의 "삭제" 링크).
    확인이 네이티브 dialog 로 뜬다 — Playwright 기본값은 dismiss 라
    핸들러를 걸지 않으면 삭제가 조용히 취소되고 예외도 안 난다.
    """
    log_no = parse_log_no(url_or_log_no)
    url = S.POST_URL.format(blog_id=blog_id, log_no=log_no)
    await page.goto(url, wait_until="domcontentloaded")
    if "nid.naver.com" in page.url:
        raise EditorError("세션 만료 — uv run python login_setup.py 재실행 필요")
    await asyncio.sleep(2.5)

    frame = await get_editor_frame(page)  # 글 페이지도 #mainFrame 을 쓴다

    # 대상 확인. 이 가드가 없으면 위험하다 (2026-08-25 실측):
    # 이미 삭제됐거나 없는 글 URL 로 가면 네이버가 블로그 홈으로 튕기는데,
    # 그 페이지에도 보이는 "삭제" 링크가 있다(_param(1|false|false)).
    # 그대로 진행하면 엉뚱한 페이지에서 삭제를 누르고 확인 dialog 까지 자동 수락한다.
    if log_no not in page.url and log_no not in (frame.url or ""):
        raise EditorError(
            f"대상 글을 찾을 수 없습니다 (이미 삭제됐거나 없는 글): {url}")

    link = await S.first(frame, S.POST_DELETE, timeout=8000)
    if not link:
        raise EditorError(f"삭제 링크를 못 찾음 (내 글이 맞는지 확인): {url}")

    seen: list[str] = []
    handler = _make_delete_dialog_handler(seen)
    page.on("dialog", handler)
    try:
        await link.click()
        await asyncio.sleep(3.0)
    finally:
        page.remove_listener("dialog", handler)

    _assert_delete_confirmed(seen, url)
    return url


async def delete_draft(page: Page, frame: Frame, title: str = "", index: int | None = None) -> str:
    """임시저장 글을 삭제한다. 삭제된 글의 제목을 반환. 복구되지 않는다.

    확인이 **네이티브 dialog** 로 뜬다 ("삭제된 글은 복구되지 않습니다").
    Playwright 는 기본적으로 dialog 를 dismiss 하므로 핸들러를 걸지 않으면
    삭제가 조용히 취소된다 — 그러고도 예외가 안 나서 성공한 것처럼 보인다.

    대상 선택은 publish 와 같은 pick_draft 안전장치를 쓴다 (애매하면 거부).
    """
    drafts = await list_drafts(page, frame)
    idx = pick_draft(drafts, title, index)
    target = drafts[idx][0]

    edit = await S.first(frame, S.DRAFT_EDIT_MODE, timeout=5000)
    if not edit:
        raise EditorError("'편집' 버튼을 못 찾음 — selectors.DRAFT_EDIT_MODE 갱신 필요")
    await edit.click()
    await asyncio.sleep(0.9)

    labels = await _locate_all(frame, S.DRAFT_CHECK_LABEL)
    if labels is None or await labels.count() <= idx:
        raise EditorError("삭제할 항목을 못 찾음 — selectors.DRAFT_CHECK_LABEL 갱신 필요")
    await labels.nth(idx).click()  # 체크박스는 label 이 덮고 있다
    await asyncio.sleep(0.4)

    seen: list[str] = []

    handler = _make_delete_dialog_handler(seen)
    page.on("dialog", handler)
    try:
        btn = await S.first(frame, S.DRAFT_DELETE_SELECTED)
        if not btn:
            raise EditorError("'선택 삭제' 버튼을 못 찾음 — selectors 갱신 필요")
        await btn.click()
        await asyncio.sleep(2.5)
    finally:
        page.remove_listener("dialog", handler)

    _assert_delete_confirmed(seen, target)
    return target


async def reserve_state(frame: Frame) -> dict:
    """발행 레이어의 발행 시간 상태. {pre, date, hour, minute}"""
    return await frame.evaluate(
        """([pre, date, hour, minute]) => ({
          pre: !!(document.querySelector(pre) || {}).checked,
          date: (document.querySelector(date) || {}).value || "",
          hour: (document.querySelector(hour) || {}).value || "",
          minute: (document.querySelector(minute) || {}).value || "" })""",
        [S.RESERVE_PRE[0], S.RESERVE_DATE[0], S.RESERVE_HOUR[0], S.RESERVE_MINUTE[0]])


async def set_reservation(page: Page, frame: Frame, when) -> dict:
    """발행 레이어에서 예약 발행 시각을 맞추고, 화면 값이 정확히 그 시각인지 확인해 돌려준다.

    when: datetime (한국 시간). 분은 10분 단위만 된다. 이번 달 날짜만 지원한다(달 넘기기 없음).
    값이 하나라도 다르면 EditorError. 이 함수는 발행 버튼을 누르지 않는다.
    """
    if when.minute % 10:
        raise EditorError(f"예약 분은 10분 단위만 됩니다: {when:%H:%M}")
    await open_publish_panel(page, frame)
    pre = frame.locator(S.RESERVE_PRE[0])
    if not await pre.count():
        raise EditorError("예약 라디오를 못 찾음 — selectors.RESERVE_PRE 갱신 필요")
    await pre.first.evaluate("e => e.click()")
    await asyncio.sleep(0.8)

    want_date = f"{when:%Y. %m. %d}"
    state = await reserve_state(frame)
    if state["date"] != want_date:
        if state["date"][:9] != want_date[:9]:
            raise EditorError(f"다른 달 예약은 지원하지 않음: 지금 {state['date']}, 원하는 {want_date}")
        await frame.locator(S.RESERVE_DATE[0]).first.click()
        await asyncio.sleep(0.8)
        days = frame.locator(S.RESERVE_DAY)
        for i in range(await days.count()):
            if (await days.nth(i).inner_text()).strip() == str(when.day):
                await days.nth(i).click()
                break
        else:
            raise EditorError(f"달력에서 {when.day}일을 못 찾음(지난 날짜일 수 있음)")
        await asyncio.sleep(0.6)
    await frame.locator(S.RESERVE_HOUR[0]).first.select_option(f"{when.hour:02d}")
    await frame.locator(S.RESERVE_MINUTE[0]).first.select_option(f"{when.minute:02d}")
    await asyncio.sleep(0.6)

    state = await reserve_state(frame)
    want = {"pre": True, "date": want_date, "hour": f"{when.hour:02d}", "minute": f"{when.minute:02d}"}
    if state != want:
        raise EditorError(f"예약 시각이 화면에 제대로 안 들어감: 화면 {state}, 원하는 {want}")
    return state


async def reserved_count(frame: Frame) -> int | None:
    """상단의 '예약 발행 N건' 숫자. 화면에 숨어 있어도 글자는 DOM 에 있다."""
    text = await frame.evaluate("() => document.body.textContent")
    m = re.search(r"예약\s*발행\s*(\d+)\s*건", text or "")
    return int(m.group(1)) if m else None


async def title_is_empty(frame: Frame) -> bool:
    """제목이 비었는지. 비어 있으면 se-placeholder 가 보인다."""
    ph = await S.first(frame, S.TITLE_PLACEHOLDER, timeout=1500)
    return ph is not None


async def draft_count(frame: Frame) -> int | None:
    """임시저장 개수. aria-label 이 "임시저장된 글 보기, 3개" 형태다.

    저장 전후로 읽어 비교하면 임시저장이 실제로 됐는지 확인할 수 있다.
    """
    btn = await S.first(frame, S.SAVE_COUNT, timeout=2000)
    if not btn:
        return None
    m = re.search(r"(\d+)\s*개", await btn.get_attribute("aria-label") or "")
    return int(m.group(1)) if m else None


# ------------------------------------------------------------ 붙여넣기 경로

_CLIPBOARD_JS = """
async ([html, text]) => {
  const item = new ClipboardItem({
    'text/html': new Blob([html], {type: 'text/html'}),
    'text/plain': new Blob([text], {type: 'text/plain'}),
  });
  await navigator.clipboard.write([item]);
}
"""


async def _focus_tail(page: Page, frame: Frame) -> None:
    """문서 끝의 빈 본문 컴포넌트로 커서를 옮긴다.

    이미지/파일/코드블록을 넣으면 포커스가 캡션이나 textarea 로 가버린다.
    다행히 그 뒤에는 항상 빈 본문 컴포넌트가 새로 생기므로, 그걸 클릭하면
    안전하게 문서 끝으로 돌아온다 (빈 컴포넌트라 클릭 지점이 곧 끝이다).
    """
    loc = await _last_body(frame)
    if loc is not None:
        await loc.click()
        await asyncio.sleep(0.3)


async def _fresh_line(page: Page, frame: Frame) -> None:
    """새 줄에서 시작하게 만든다.

    목록의 "- " 자동 변환은 줄 맨 앞에서만 걸린다. 기존 문단 끝에 이어 치면
    그냥 "- " 문자로 남는다. 커서가 선 줄이 이미 비어 있으면 Enter 를 치지 않는다.
    문서 전체 길이로 보면 영상·글감 카드 뒤 빈 줄에서도 Enter 를 쳐서 빈 줄이 하나 더
    생긴다(2026-09-17 실측). 커서 줄을 못 읽으면 예전처럼 문서 길이로 본다.
    """
    try:
        empty = await frame.evaluate(_CARET_LINE_EMPTY_JS)
    except Exception:
        empty = None
    if empty is None:
        empty = await _body_len(frame) <= 0
    if not empty:
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.25)


_CARET_LINE_EMPTY_JS = """
() => {
  const n = window.getSelection().anchorNode;
  const p = n && (n.nodeType === 1 ? n : n.parentElement).closest('.se-text-paragraph');
  return p ? p.innerText.split(String.fromCharCode(8203)).join('').trim() === '' : null;
}
"""


async def paste_html(page: Page, frame: Frame, html: str, plain: str) -> None:
    """클립보드에 리치 HTML을 실어 Ctrl+V. 에디터가 자체 블록으로 변환해준다.

    현재 커서 위치에 붙인다 — 컴포넌트를 다시 클릭하지 않는다.
    다시 클릭하면 커서가 클릭 지점(글 중간)에 놓여 내용이 뒤섞인다.

    권한: context 생성 시 clipboard-read/write 를 grant 해야 한다 (session.py 참조).

    창이 뒤에 있으면 navigator.clipboard.write 가 거부도 아니고 그냥 안 끝난다.
    사람이 다른 창을 쓰는 동안 돌리면 여기서 영원히 멈춘다(2026-09-16 실측: 제목만
    써진 채 17분 정지). 그래서 창을 앞으로 끌어오고, 그래도 안 끝나면 끊어서
    호출부가 타이핑 경로로 넘어가게 한다.
    """
    try:
        await page.bring_to_front()
    except Exception:
        pass
    try:
        await asyncio.wait_for(page.evaluate(_CLIPBOARD_JS, [html, plain]), timeout=5)
    except asyncio.TimeoutError as e:
        raise EditorError("클립보드 쓰기 5초 초과 — 창이 뒤에 있거나 권한 문제") from e
    await page.keyboard.press("ControlOrMeta+V")
    await asyncio.sleep(0.6)


# ------------------------------------------------------------ 키스트로크 폴백

# 2026-08-25 실측: B/I/U 는 동작한다. 취소선 단축키(ControlOrMeta+Shift+S)는
# 동작하지 않아 툴바 버튼(S.STRIKE_BUTTON)을 쓴다.
_SHORTCUT = {
    "bold": "ControlOrMeta+B",
    "italic": "ControlOrMeta+I",
    "underline": "ControlOrMeta+U",
}


async def _click_toolbar(frame: Frame, candidates: list[str], label: str) -> None:
    btn = await S.first(frame, candidates, timeout=3000)
    if not btn:
        raise EditorError(f"{label} 버튼을 못 찾음 — selectors 갱신 필요")
    await btn.click()
    await asyncio.sleep(0.3)


async def _clear_toggle(frame: Frame, candidates: list[str]) -> None:
    """켜진 채 남은 툴바 토글을 끈다.

    취소선은 선택 구간에 적용한 뒤에도 토글이 켜져 있어서, 그대로 두면 이후
    타이핑이 전부 취소선이 된다 (실측: 뒤따르는 모든 문단에 번졌다).
    """
    btn = await S.first(frame, candidates, timeout=2000)
    if btn and S.TOGGLE_ON_CLASS in (await btn.get_attribute("class") or ""):
        await btn.click()
        await asyncio.sleep(0.25)


async def reset_style(page: Page, frame: Frame) -> None:
    """다음에 쓸 글의 서식을 보통으로 되돌린다.

    에디터는 마지막으로 쓴 서식을 기억했다가, 뒤에 붙여넣거나 타이핑하는 글에 그대로
    입힌다. 제목 뒤 모든 문단이 큰 글씨·굵게가 되고, 기울임 한 줄 뒤로 전부 기울임이
    됐다(2026-09-16 실측). 인라인 스타일로 "보통"을 명시해도 굵게는 안 풀렸고,
    툴바 토글을 끄고 글자 크기를 본문으로 되돌리는 것만 확실히 먹혔다.
    """
    for cands in (S.BOLD_BUTTON, S.ITALIC_BUTTON, S.UNDERLINE_BUTTON, S.STRIKE_BUTTON):
        try:
            await _clear_toggle(frame, cands)
        except Exception:
            pass
    try:
        fs = await S.first(frame, S.FONT_SIZE_BUTTON, timeout=1500)
        label = (await fs.inner_text()).strip() if fs else ""
        if S.BODY_SIZE.replace("fs", "") not in label:
            await set_font_size(page, frame, S.BODY_SIZE)
    except Exception:
        pass


async def apply_link(page: Page, frame: Frame, url: str) -> None:
    """선택된 텍스트에 링크를 건다.

    붙여넣기는 <a href> 를 지운다 (라벨=URL, 맨 URL 까지 전부 실측함).
    그래서 텍스트를 선택한 뒤 툴바로 거는 이 경로가 유일하다.

    결과는 <a href> 가 아니라 <span class="se-link" data-href="..."> 다.
    """
    await _click_toolbar(frame, S.LINK_BUTTON, "링크")
    inp = await S.first(frame, S.LINK_INPUT, timeout=5000)
    if not inp:
        raise EditorError("링크 URL 입력란을 못 찾음 — selectors.LINK_INPUT 갱신 필요")
    await inp.fill(url)
    await _click_toolbar(frame, S.LINK_APPLY, "링크 적용")
    await asyncio.sleep(0.4)
    # 링크 입력 층이 닫힐 때까지 기다린다. 열린 채로 다음 줄을 치면 방금 건 링크 글자와
    # 뒤 링크 줄이 통째로 사라졌다(2026-09-16 실측: 글 끝 링크 3개 중 3개 누락).
    for _ in range(20):
        if not await _any_visible(frame, S.LINK_INPUT):
            break
        await asyncio.sleep(0.15)
    else:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.4)


async def set_font_size(page: Page, frame: Frame, value: str) -> None:
    """선택 구간(또는 이후 입력)의 글자 크기를 바꾼다. value 예: "fs24"."""
    await _click_toolbar(frame, S.FONT_SIZE_BUTTON, "글자 크기")
    opt = await S.first(frame, S.FONT_SIZE_OPTION, timeout=4000, value=value)
    if not opt:
        raise EditorError(f"글자 크기 {value} 옵션을 못 찾음 — selectors.FONT_SIZE_OPTION 갱신 필요")
    await opt.click()
    await asyncio.sleep(0.4)


def caret_len(text: str) -> int:
    """이 텍스트를 지나려면 커서를 몇 번 움직여야 하는지.

    len() 은 코드포인트 수라 결합문자·ZWJ 이모지에서 어긋난다.
    'é'(e+U+0301) 는 len()==2 지만 커서는 한 번, '👨‍👩‍👧' 는 len()==5 지만 한 번이다.
    그대로 Shift+ArrowLeft 를 세면 선택이 앞 텍스트까지 먹어서 취소선·링크·글자크기가
    엉뚱한 곳에 붙는다. 완전한 그래핌 분할은 아니고 흔한 경우만 잡는 근사다.
    """
    n = 0
    join_next = False
    for ch in text:
        if unicodedata.combining(ch) or ch in "\ufe0e\ufe0f":
            continue  # 결합문자 / 이모지 변형선택자
        if ch == "\u200d":  # ZWJ: 다음 글자와 한 덩어리
            join_next = True
            continue
        if join_next:
            join_next = False
            continue
        n += 1
    return n


async def type_spans(page: Page, frame: Frame, spans: list[Span]) -> None:
    """붙여넣기가 막혔을 때. 단축키로 서식 토글하며 직접 타이핑.

    취소선과 링크는 단축키가 없다. 텍스트를 친 뒤 그 길이만큼 Shift+ArrowLeft 로
    되선택해서 툴바 버튼을 누른다.
    """
    for s in spans:
        toggles = [k for k, on in
                   (("bold", s.bold), ("italic", s.italic), ("underline", s.underline)) if on]
        for t in toggles:
            await page.keyboard.press(_SHORTCUT[t])
        await page.keyboard.type(s.text, delay=8)
        for t in reversed(toggles):
            await page.keyboard.press(_SHORTCUT[t])

        if not (s.strike or s.href):
            continue
        for _ in range(caret_len(s.text)):
            await page.keyboard.press("Shift+ArrowLeft")
        await asyncio.sleep(0.2)
        if s.strike:
            await _click_toolbar(frame, S.STRIKE_BUTTON, "취소선")
        if s.href:
            await apply_link(page, frame, s.href)
        await page.keyboard.press("ArrowRight")  # 선택 해제하고 커서를 끝으로
        if s.href:
            await page.keyboard.press("End")
            await asyncio.sleep(0.3)
        if s.strike:
            await _clear_toggle(frame, S.STRIKE_BUTTON)


# ------------------------------------------------------------ 이미지

async def insert_image(page: Page, frame: Frame, path: str, caption: str = "") -> bool:
    """툴바 이미지 버튼 -> 파일 다이얼로그 가로채기. 붙여넣기로는 안 된다.

    캡션까지 넣었으면 True. 이미지는 들어갔는데 캡션이 실패하면 False.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise EditorError(f"이미지 없음: {p}")
    if p.stat().st_size > 10 * 1024 * 1024:
        raise EditorError(f"10MB 초과: {p.name}")

    btn = await S.first(frame, S.IMAGE_BUTTON)
    if not btn:
        raise EditorError("이미지 버튼을 못 찾음 — selectors.IMAGE_BUTTON 갱신 필요")

    before = await _image_count(frame)
    async with page.expect_file_chooser(timeout=10_000) as fc_info:
        await btn.click()
    chooser = await fc_info.value
    await chooser.set_files(str(p))

    # 업로드 완료 = 이미지 컴포넌트가 하나 늘어난 것.
    # 존재 여부만 보면 이미 이미지가 있는 글에서 두 번째 이미지를 안 기다리고 통과한다.
    for _ in range(60):
        if await _image_count(frame) > before:
            break
        await asyncio.sleep(0.5)
    else:
        raise EditorError(f"이미지 업로드가 30초 안에 안 끝남: {p.name}")
    await asyncio.sleep(0.8)

    if not caption:
        return True

    # 캡션은 평소 0x0 으로 숨어 있다. 이미지 컴포넌트를 클릭해야 노출되고,
    # 노출된 뒤 캡션을 한 번 더 클릭해야 포커스가 들어간다. (2026-08-25 실측)
    # 컴포넌트만 클릭하고 타이핑하면 입력이 아무 데도 안 들어간다.
    # 업로드 직후 컴포넌트 목록을 잠깐 못 읽는 때가 있었다(2026-09-16, 2.4MB GIF 뒤 None 으로 전체가 죽음).
    # 몇 번 다시 읽고, 끝내 못 읽으면 캡션만 실패로 돌린다.
    comps = None
    for _ in range(10):
        comps = await _locate_all(frame, S.IMAGE_COMPONENT)
        if comps is not None and await comps.count() > before:
            break
        await asyncio.sleep(0.5)
    else:
        return False
    comp = comps.nth(before)  # 방금 붙은 것
    try:
        await comp.click()
        cap = await _locate_all(comp, S.IMAGE_CAPTION)
        if cap is None:
            return False
        await cap.first.wait_for(state="visible", timeout=5000)
        await cap.first.click()
        await asyncio.sleep(0.4)
        await page.keyboard.type(caption, delay=10)
        await asyncio.sleep(0.3)
        return True
    except Exception:
        # 캡션 실패로 글 전체를 날리지는 않는다. 호출부가 기록만 한다.
        return False


async def _image_count(frame: Frame) -> int:
    loc = await _locate_all(frame, S.IMAGE_COMPONENT)
    return await loc.count() if loc is not None else 0


async def set_rep_image(page: Page, frame: Frame, index: int = 0) -> bool:
    """index 번째 본문 이미지를 대표 이미지로 지정한다. 지정됐으면 True.

    대표 이미지는 검색 결과와 블로그 목록의 섬네일이다. 네이버는 **본문에 들어간
    이미지 중에서만** 고르게 한다 — 따로 올리는 칸이 없다. 그래서 표지 파일을
    쓰려면 본문에 넣은 뒤 그 이미지의 "대표" 버튼을 눌러야 한다. (2026-09-18 실측)

    첫 이미지가 기본 대표라서 index=0 이면 대개 이미 눌려 있다. 그때는 누르지 않고
    True 를 돌려준다. 이미 지정된 것을 다시 누르면 해제되기 때문이다.
    """
    btns = await _locate_all(frame, S.REP_IMAGE_BUTTON)
    if btns is None:
        return False
    n = await btns.count()
    if n <= index:
        return False
    btn = btns.nth(index)

    async def selected() -> bool:
        cls = await btn.get_attribute("class") or ""
        return S.REP_IMAGE_SELECTED in cls

    if await selected():
        return True
    try:
        await btn.scroll_into_view_if_needed(timeout=5000)
        await btn.click()
    except Exception:
        return False
    for _ in range(10):
        await asyncio.sleep(0.3)
        if await selected():
            return True
    return False


async def rep_image_index(frame: Frame) -> int | None:
    """지금 대표로 지정된 이미지가 몇 번째인지. 없으면 None."""
    btns = await _locate_all(frame, S.REP_IMAGE_BUTTON)
    if btns is None:
        return None
    for i in range(await btns.count()):
        cls = await btns.nth(i).get_attribute("class") or ""
        if S.REP_IMAGE_SELECTED in cls:
            return i
    return None


async def insert_file(page: Page, frame: Frame, path: str) -> None:
    """툴바 파일 버튼 -> '내 컴퓨터' -> 파일 다이얼로그 가로채기.

    이미지와 달리 소스 선택 팝업(내 컴퓨터 / MYBOX)이 한 단계 더 있다. (2026-08-25 실측)
    네이버가 팝업에 "파일당 10MB까지" 라고 명시한다.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise EditorError(f"파일 없음: {p}")
    if p.stat().st_size > 10 * 1024 * 1024:
        raise EditorError(f"10MB 초과: {p.name}")

    btn = await S.first(frame, S.FILE_BUTTON)
    if not btn:
        raise EditorError("파일 버튼을 못 찾음 — selectors.FILE_BUTTON 갱신 필요")

    before = await _file_count(frame)
    await btn.click()
    local = await S.first(frame, S.FILE_SOURCE_LOCAL, timeout=8000)
    if not local:
        raise EditorError("'내 컴퓨터' 버튼을 못 찾음 — selectors.FILE_SOURCE_LOCAL 갱신 필요")

    async with page.expect_file_chooser(timeout=10_000) as fc_info:
        await local.click()
    chooser = await fc_info.value
    await chooser.set_files(str(p))

    for _ in range(60):
        if await _file_count(frame) > before:
            break
        await asyncio.sleep(0.5)
    else:
        raise EditorError(f"파일 업로드가 30초 안에 안 끝남: {p.name}")
    await asyncio.sleep(0.5)


async def _file_count(frame: Frame) -> int:
    loc = await _locate_all(frame, S.FILE_COMPONENT)
    return await loc.count() if loc is not None else 0


async def insert_divider(page: Page, frame: Frame, style: str) -> None:
    """모양을 골라 구분선을 넣는다. 붙여넣은 <hr> 은 기본 모양(긴 가는 선)으로만 들어간다."""
    before = await _count(frame, S.DIVIDER_COMPONENT)
    opener = await S.first(frame, S.DIVIDER_MENU, timeout=4000)
    if not opener:
        raise EditorError("구분선 메뉴를 못 찾음 — selectors.DIVIDER_MENU 갱신 필요")
    await opener.click()
    await asyncio.sleep(0.4)
    opt = await S.first(frame, S.DIVIDER_OPTION, timeout=4000, value=style)
    if not opt:
        raise EditorError(f"구분선 모양 {style} 을 못 찾음 — selectors.DIVIDER_OPTION 확인")
    await opt.click()
    await _wait_added(frame, S.DIVIDER_COMPONENT, before, "구분선")


async def insert_code_block(page: Page, frame: Frame, code: str) -> None:
    """툴바 소스코드 버튼으로 코드블록을 만들고 내용을 채운다.

    붙여넣기도 타이핑도 안 된다 (2026-08-25 실측):
      - <pre><code> 는 살균기가 지워서 문단으로 뭉개진다
      - ``` 를 타이핑해도 그냥 텍스트다
    내용 영역이 contenteditable 이 아니라 <textarea> 라 keyboard.type 대신 fill() 을 쓴다.
    언어 지정 수단은 없다 (배경색 3종만 있음).
    """
    btn = await S.first(frame, S.CODE_BUTTON)
    if not btn:
        raise EditorError("소스코드 버튼을 못 찾음 — selectors.CODE_BUTTON 갱신 필요")
    await btn.click()
    ta = await S.first(frame, S.CODE_TEXTAREA, timeout=8000)
    if not ta:
        raise EditorError("코드 입력 textarea 를 못 찾음 — selectors.CODE_TEXTAREA 갱신 필요")
    await ta.fill(code)
    await asyncio.sleep(0.6)


async def _count(frame: Frame, candidates: list[str]) -> int:
    loc = await _locate_all(frame, candidates)
    return await loc.count() if loc is not None else 0


async def _wait_added(frame: Frame, candidates: list[str], before: int, what: str) -> None:
    for _ in range(60):
        if await _count(frame, candidates) > before:
            await asyncio.sleep(0.6)
            return
        await asyncio.sleep(0.5)
    raise EditorError(f"{what} 가 30초 안에 안 들어감")


async def insert_formula(page: Page, frame: Frame, script: str) -> None:
    """툴바 수식 버튼 -> 스크립트 입력창에 타이핑 -> 입력.

    비주얼 수식편집기지만 스크립트 입력창이 있어서 자동화가 된다.
    fill() 은 먹지 않는다 — 키 입력으로만 파싱한다 (실측). press_sequentially 를 쓴다.
    """
    btn = await S.first(frame, S.FORMULA_BUTTON)
    if not btn:
        raise EditorError("수식 버튼을 못 찾음 — selectors.FORMULA_BUTTON 갱신 필요")
    before = await _count(frame, S.FORMULA_COMPONENT)
    await btn.click()
    ta = await S.first(frame, S.FORMULA_INPUT, timeout=8000)
    if not ta:
        raise EditorError("수식 입력창을 못 찾음 — selectors.FORMULA_INPUT 갱신 필요")
    await ta.click()
    await ta.press_sequentially(script, delay=25)
    await asyncio.sleep(1.0)
    sub = await S.first(frame, S.FORMULA_SUBMIT, timeout=4000)
    if not sub:
        raise EditorError("수식 '입력' 버튼을 못 찾음 — selectors.FORMULA_SUBMIT 갱신 필요")
    await sub.click()
    await _wait_added(frame, S.FORMULA_COMPONENT, before, "수식")


async def _wait_place_results(frame: Frame, seconds: int = 6):
    """장소 검색 결과 로케이터. 안 뜨면 None."""
    for _ in range(seconds * 2):
        await asyncio.sleep(0.5)
        items = await _locate_all(frame, S.MAP_RESULT_ITEM)
        if items is not None and await items.count():
            return items
    return None


async def insert_place(page: Page, frame: Frame, query: str) -> str:
    """툴바 장소 버튼 -> 검색 -> 첫 결과 '추가' -> 확인. 고른 장소명을 반환.

    검색 결과가 여러 개면 첫 번째를 쓴다. 어느 것을 골랐는지 반환하니
    호출부가 기록해서 사람이 확인할 수 있게 한다.
    """
    btn = await S.first(frame, S.MAP_BUTTON)
    if not btn:
        raise EditorError("장소 버튼을 못 찾음 — selectors.MAP_BUTTON 갱신 필요")
    before = await _count(frame, S.MAP_COMPONENT)
    await btn.click()

    # 지도 팝업은 React 앱이라 입력창이 보여도 아직 준비 전일 수 있다.
    # 준비 전에 돋보기를 누르면 검색이 아예 발동하지 않는데, 그러면 결과가 없어서
    # "검색 결과가 없습니다" 로 오인하게 된다. 간헐적으로 재현되던 버그다 (2026-08-25).
    if not await S.first(frame, S.MAP_POPUP, timeout=10_000):
        raise EditorError("장소 팝업이 안 열림 — selectors.MAP_POPUP 갱신 필요")
    inp = await S.first(frame, S.MAP_INPUT, timeout=10_000)
    if not inp:
        raise EditorError("장소 검색창을 못 찾음 — selectors.MAP_INPUT 갱신 필요")
    await inp.click()
    await inp.press_sequentially(query, delay=30)
    await asyncio.sleep(0.6)  # 자동완성이 뜨고 입력이 반영될 여유

    # 검색이 발동했는지는 결과로만 알 수 있다. 안 떴으면 다시 누른다.
    items = None
    for attempt in range(3):
        search = await S.first(frame, S.MAP_SEARCH, timeout=4000)
        if search:
            await search.click()
        else:
            await page.keyboard.press("Enter")
        items = await _wait_place_results(frame, seconds=6)
        if items is not None:
            break
        await asyncio.sleep(0.8)
    if items is None:
        raise EditorError(
            f"장소 검색 결과가 없습니다 (검색이 발동하지 않았을 수도 있습니다): {query!r}")

    item = items.first
    picked = (await item.inner_text()).replace("\n", " ").strip()[:60]
    # "추가" 버튼은 hover 해야 보인다. 결과 링크를 누르면 지도만 움직이고 선택이 안 된다.
    await item.hover()
    await asyncio.sleep(0.6)
    add_loc = await _locate_all(item, S.MAP_ADD)
    if add_loc is None:
        raise EditorError("장소 '추가' 버튼을 못 찾음 — selectors.MAP_ADD 갱신 필요")
    add = add_loc.first
    if await add.is_visible():
        await add.click()
    else:
        await add.evaluate("e => e.click()")
    await asyncio.sleep(1.2)

    confirm = await S.first(frame, S.MAP_CONFIRM, timeout=5000)
    if not confirm:
        raise EditorError("장소 확인 버튼을 못 찾음 — selectors.MAP_CONFIRM 갱신 필요")
    if await confirm.evaluate("e => e.disabled"):
        raise EditorError(f"장소가 선택되지 않았습니다 (확인 버튼 비활성): {query!r}")
    await confirm.click()
    await _wait_added(frame, S.MAP_COMPONENT, before, "장소")
    return picked


async def _material_open_bar(page: Page, frame: Frame) -> None:
    if await S.first(frame, S.MATERIAL_BAR, timeout=1500):
        return
    btn = await S.first(frame, S.MATERIAL_TOOLBAR)
    if not btn:
        raise EditorError("글감 버튼을 못 찾음 — selectors.MATERIAL_TOOLBAR 갱신 필요")
    await btn.click()
    if not await S.first(frame, S.MATERIAL_BAR, timeout=5000):
        raise EditorError("글감 바가 안 열림 — selectors.MATERIAL_BAR 갱신 필요")


async def _material_results(frame: Frame, seconds: int = 8) -> list[tuple[str, str]]:
    """보이는 검색 결과 [(제목, 설명)]. DOM 순서 그대로(숨김 항목 제외)."""
    for _ in range(seconds * 2):
        await asyncio.sleep(0.5)
        rows = await frame.evaluate(
            """([item, title, desc]) => [...document.querySelectorAll(item)]
                 .filter(e => e.offsetParent !== null)
                 .map(e => [(e.querySelector(title) || {}).innerText || '',
                            (e.querySelector(desc) || {}).innerText || ''])""",
            [S.MATERIAL_ITEM[0], S.MATERIAL_ITEM_TITLE[0], S.MATERIAL_ITEM_DESC[0]])
        if rows:
            return [(t.strip(), d.replace(chr(10), " ").strip()) for t, d in rows]
    return []


async def _material_close_popup(page: Page, frame: Frame) -> None:
    close = await S.first(frame, S.MATERIAL_POPUP_CLOSE, timeout=1500)
    if close:
        await close.click()
    else:
        await page.keyboard.press("Escape")
    await asyncio.sleep(0.5)


async def _material_search(page: Page, frame: Frame, kind: str, arg: str) -> tuple[list[tuple[str, str]], int] | None:
    """분류를 고르고 검색어 후보로 찾는다. 맞는 결과가 있으면 (결과, 순번), 결과 팝업은 열린 채로 둔다."""
    from .material import CATEGORY, best_match, queries

    await _material_open_bar(page, frame)
    trigger = await S.first(frame, S.MATERIAL_CATEGORY_TRIGGER)
    if not trigger:
        raise EditorError("글감 분류 버튼을 못 찾음 — selectors.MATERIAL_CATEGORY_TRIGGER 갱신 필요")
    await trigger.click()
    await asyncio.sleep(0.6)
    label = CATEGORY[kind]
    await frame.locator("li, button").filter(has_text=re.compile(rf"^\s*{label}\s*$")).last.click()
    await asyncio.sleep(0.5)

    for query in queries(kind, arg):
        inp = await S.first(frame, S.MATERIAL_INPUT)
        if not inp:
            raise EditorError("글감 검색창을 못 찾음 — selectors.MATERIAL_INPUT 갱신 필요")
        await inp.click()
        await inp.fill(query)
        await page.keyboard.press("Enter")
        rows = await _material_results(frame)
        idx = best_match(kind, arg, rows)
        if idx is not None:
            return rows, idx
    return None


MATERIAL_KINDS = ("news", "stock", "book")


async def resolve_materials(page: Page, frame: Frame, blocks: list[Block], notes: list[str]) -> list[Block]:
    """본문을 쓰기 전에 글감 카드를 미리 찾아본다. 못 찾은 카드는 글자 문단으로 바꾼다.

    쓰는 도중에 검색이 실패하면 커서가 검색창으로 갔다가 돌아오지 못해, 대신 적은 글자가
    앞 줄 위에 끼어든다(2026-09-16 실측). 그래서 실패는 쓰기 전에 걸러낸다.
    """
    from .material import fallback_text

    out: list[Block] = []
    for b in blocks:
        if b.type not in MATERIAL_KINDS:
            out.append(b)
            continue
        try:
            found = await _material_search(page, frame, b.type, b.raw)
        except (EditorError, ValueError) as e:
            found = None
            notes.append(f"글감 {b.type} 검색 오류({e})")
        if found:
            out.append(b)
        else:
            notes.append(f"글감 {b.type} 못 찾음→글자({b.raw[:30]})")
            out.append(Block("paragraph", spans=[Span(fallback_text(b.type, b.raw))], gap=b.gap))
    if any(b.type in MATERIAL_KINDS for b in blocks):
        await _material_close_popup(page, frame)
    return out


async def insert_material(page: Page, frame: Frame, kind: str, arg: str) -> str | None:
    """글감 카드(뉴스, 증권, 책)를 넣는다. 고른 '제목 / 설명' 을 반환, 맞는 결과가 없으면 None.

    엉뚱한 카드를 넣지 않는 게 우선이다. 매칭 기준은 material.best_match.
    """
    from .material import split_layout

    before = await _count(frame, S.MATERIAL_COMPONENT)
    found = await _material_search(page, frame, kind, arg)
    if not found:
        await _material_close_popup(page, frame)
        return None
    rows, idx = found
    item = frame.locator(f"{S.MATERIAL_ITEM[0]}:visible").nth(idx)
    await item.locator(S.MATERIAL_ADD[0]).dispatch_event("click")
    await _wait_added(frame, S.MATERIAL_COMPONENT, before, "글감 카드")
    await _material_close_popup(page, frame)

    _, layout = split_layout(arg)
    card = frame.locator(S.MATERIAL_COMPONENT[0]).nth(before)
    if layout not in (await card.get_attribute("class") or ""):
        await card.click()
        await asyncio.sleep(0.6)
        btn = await S.first(frame, S.MATERIAL_LAYOUT, value=layout)
        if btn:
            await btn.click()
            await asyncio.sleep(0.8)
    return " / ".join(rows[idx])


async def insert_video(page: Page, frame: Frame, url: str) -> str:
    """유튜브 영상 플레이어를 넣는다. 들어간 영상 제목을 반환.

    툴바 '링크' 버튼으로 넣는다(selectors.OGLINK_* 참조). 미리보기가 안 뜨거나 플레이어가
    아닌 링크 카드가 들어가면 EditorError. 실패해도 글자를 대신 치지 않는다 — 팝업에서
    커서가 돌아오지 못해 엉뚱한 줄에 끼어든다(글감 카드와 같은 함정).
    """
    from .ir import YOUTUBE_URL

    if not YOUTUBE_URL.match(url):
        raise EditorError(f"유튜브 영상 주소가 아님: {url}")
    btn = await S.first(frame, S.OGLINK_BUTTON)
    if not btn:
        raise EditorError("링크 버튼을 못 찾음 — selectors.OGLINK_BUTTON 갱신 필요")
    before = await _count(frame, S.VIDEO_COMPONENT)
    await btn.click()
    inp = await S.first(frame, S.OGLINK_INPUT, timeout=5000)
    if not inp:
        raise EditorError("링크 주소 입력창을 못 찾음 — selectors.OGLINK_INPUT 갱신 필요")
    await inp.click()
    await inp.press_sequentially(url, delay=5)
    await page.keyboard.press("Enter")
    preview = await S.first(frame, S.OGLINK_PREVIEW, timeout=10_000)
    confirm = await S.first(frame, S.OGLINK_CONFIRM, timeout=2000) if preview else None
    if not confirm:
        close = await S.first(frame, S.OGLINK_CLOSE, timeout=1500)
        if close:
            await close.click()
        raise EditorError(f"영상 미리보기가 안 뜸(비공개·삭제된 영상일 수 있음): {url}")
    await confirm.click()
    await _wait_added(frame, S.VIDEO_COMPONENT, before, "영상")
    player = frame.locator(S.VIDEO_COMPONENT[0]).nth(before)
    if not await player.locator(S.VIDEO_YOUTUBE).count():
        raise EditorError(f"영상 플레이어가 아닌 것이 들어감: {url}")
    return (await player.locator(S.VIDEO_TITLE).first.inner_text()).strip()


# ------------------------------------------------------------ 본문 조립

async def write_post(
    page: Page,
    title: str,
    markdown: str,
    *,
    prefer_paste: bool = True,
    cover: str | None = None,
) -> list[str]:
    """cover 를 주면 본문 맨 앞에 그 그림을 넣는다(대표 이미지용).

    네이버는 본문 이미지 중에서만 대표를 고를 수 있어서, 표지를 섬네일로 쓰려면
    본문에 들어가 있어야 한다. 맨 앞에 넣으면 첫 이미지가 되어 기본 대표가 된다.
    지정 자체는 호출부가 set_rep_image 로 한 번 더 확인한다.
    캡션은 넣지 않는다 — 표지에는 글자가 이미 그려져 있어서 아래에 한 줄 더 붙으면
    제목이 두 번 나온다.
    """
    frame = await get_editor_frame(page)
    await dismiss_popups(frame)
    notes: list[str] = []
    blocks = await resolve_materials(page, frame, parse_markdown(markdown), notes)

    title_loc = await S.first(frame, S.TITLE)
    if not title_loc:
        raise EditorError("제목 영역을 못 찾음 — selectors.TITLE 갱신 필요")
    await title_loc.click()
    await page.keyboard.type(title, delay=15)

    body_loc = await _last_body(frame)
    if body_loc is None:
        raise EditorError("본문 영역을 못 찾음 — selectors.BODY 갱신 필요")
    await body_loc.click()  # 최초 1회만 클릭. 이후에는 커서를 그대로 이어 쓴다.

    if cover:
        # 표지가 안 올라가도 글 전체를 날리지는 않는다. 기록만 남기고 본문을 계속 쓴다.
        try:
            await insert_image(page, frame, cover, "")
            notes.append("0:표지")
        except EditorError as e:
            notes.append(f"0:표지 넣기 실패({e})")
        await _focus_tail(page, frame)

    for i, seg in enumerate(segment(blocks), 1):
        if seg.kind in ("image", "file"):
            if seg.kind == "image":
                ok = await insert_image(page, frame, seg.path, seg.caption)
                notes.append(f"{i}:이미지" if ok else f"{i}:이미지(캡션실패)")
            else:
                await insert_file(page, frame, seg.path)
                notes.append(f"{i}:파일")
            await _focus_tail(page, frame)
            continue

        if seg.kind == "manual":
            # 붙여넣기가 문단으로 뭉개는 블록들. 툴바/키보드로 하나씩 만든다.
            for b in seg.blocks:
                if b.type == "code":
                    await insert_code_block(page, frame, b.raw)
                    await _focus_tail(page, frame)  # textarea 에서 빠져나온다
                    notes.append(f"{i}:코드블록")
                elif b.type == "formula":
                    await insert_formula(page, frame, b.raw)
                    await _focus_tail(page, frame)
                    notes.append(f"{i}:수식")
                elif b.type == "divider":
                    from .ir import DIVIDER_STYLE
                    await insert_divider(page, frame, DIVIDER_STYLE or "line1")
                    await _focus_tail(page, frame)
                    notes.append(f"{i}:구분선({DIVIDER_STYLE})")
                elif b.type in MATERIAL_KINDS:
                    try:
                        picked = await insert_material(page, frame, b.type, b.raw)
                    except EditorError as e:
                        picked = None
                        notes.append(f"{i}:글감 {b.type} 넣기 오류({e})")
                    await _focus_tail(page, frame)
                    # 미리 찾아둔 카드라 여기서 못 찾는 일은 드물다. 대신 글자를 치면 커서가
                    # 엉뚱한 곳에 있어 순서가 뒤섞이므로 치지 않고 기록만 남긴다.
                    notes.append(f"{i}:글감 {b.type}({picked[:30]})" if picked
                                 else f"{i}:글감 {b.type} 누락({b.raw[:30]})")
                elif b.type == "place":
                    picked = await insert_place(page, frame, b.raw)
                    await _focus_tail(page, frame)
                    # 검색 결과 중 첫 번째를 쓰므로 무엇을 골랐는지 남긴다.
                    notes.append(f"{i}:장소({picked[:24]})")
                elif b.type == "video":
                    try:
                        picked = await insert_video(page, frame, b.raw)
                        notes.append(f"{i}:영상({picked[:30]})")
                    except EditorError as e:
                        notes.append(f"{i}:영상 누락({e})")
                    await _focus_tail(page, frame)
                else:
                    await _fresh_line(page, frame)
                    await reset_style(page, frame)
                    await _type_block(page, frame, b)
                    notes.append(f"{i}:{b.type}(타이핑)")
            continue

        await reset_style(page, frame)
        if prefer_paste:
            try:
                before = await _doc_state(frame)
                await paste_html(page, frame, seg.html, _to_plain(seg.html))
                # 붙여넣기는 예외 없이 조용히 실패할 수 있다 (클립보드 권한, 헤드리스 등).
                # 본문 길이가 그대로면 안 붙은 것으로 보고 폴백한다.
                after = await _doc_state(frame)
                if before is None or after is None:
                    # 확인이 안 될 뿐 대개는 붙은 상태다. 여기서 타이핑으로 넘어가면
                    # 내용이 두 번 들어간다 — 중복이 누락보다 나쁘다.
                    notes.append(f"{i}:붙여넣기(반영 확인 불가)")
                    continue
                if after != before:
                    notes.append(f"{i}:붙여넣기")
                    continue
                notes.append(f"{i}:붙여넣기 무반응→타이핑")
            except Exception as e:
                notes.append(f"{i}:붙여넣기 실패({type(e).__name__})→타이핑")
        lost = [b.type for b in seg.blocks if b.type == "divider"]
        for b in seg.blocks:
            await _type_block(page, frame, b)
        if lost:
            # 구분선은 키보드로 만들 수 없어 조용히 사라진다. 최소한 알린다.
            notes.append(f"{i}:구분선 {len(lost)}개 누락(타이핑 불가)")

    return notes


async def _last_body(frame: Frame):
    """마지막 본문 컴포넌트. 이미지를 넣으면 본문 컴포넌트가 여러 개가 된다."""
    loc = await _locate_all(frame, S.BODY)
    return loc.last if loc is not None else None


async def _body_len(frame: Frame) -> int:
    """본문 컴포넌트 전체의 텍스트 길이 합. 새 줄이 필요한지 판정하는 데 쓴다."""
    loc = await _locate_all(frame, S.BODY)
    if loc is None:
        return -1
    try:
        return sum(len(t) for t in await loc.all_inner_texts())
    except Exception:
        return -1


async def _doc_state(frame: Frame) -> tuple[int, int] | None:
    """(컴포넌트 수, 전체 텍스트 길이). 붙여넣기 반영 여부 판정용. 못 읽으면 None.

    본문 길이만 보면 안 된다 — 인용구/표/코드는 별도 컴포넌트가 되므로
    본문은 그대로고, 실제로는 붙었는데 "무반응" 으로 오판해 중복 입력하게 된다.

    실패를 (-1,-1) 같은 센티널로 돌려주면 안 된다. 붙여넣기 전후로 두 번 다
    실패했을 때 두 센티널이 서로 같아서 "무반응" 으로 판정되고, 결국 세그먼트
    전체를 다시 타이핑해 내용이 두 번 들어간다.
    """
    loc = await _locate_all(frame, S.DOC_COMPONENT)
    if loc is None:
        return None
    try:
        texts = await loc.all_inner_texts()
        return (len(texts), sum(len(t) for t in texts))
    except Exception:
        return None


def _to_plain(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html)


async def _type_block(page: Page, frame: Frame, b: Block) -> None:
    if b.type in ("image", "file", "divider", "formula", "place", "video"):
        return  # 키보드로 만들 수 없다 (write_post 가 툴바로 처리)
    if b.gap and b.type in ("heading", "paragraph"):
        await page.keyboard.press("Enter")  # 앞에 빈 줄 하나
    if b.type == "heading":
        # 제목은 "글자 크기" 일 뿐이다. 먼저 내용을 치고(링크 포함),
        # 그 줄을 통째로 선택해서 크기를 준다. 그래야 제목과 링크를 둘 다 살린다.
        # 굵게는 타이핑하면서 켰다 끈다. 선택 구간에 굵게를 주면 토글이 켜진 채 남는다.
        spans = [Span(sp.text, bold=True, italic=sp.italic, underline=sp.underline,
                      strike=sp.strike, code=sp.code, href=sp.href) for sp in b.spans]
        await type_spans(page, frame, spans)
        n = sum(caret_len(sp.text) for sp in spans)
        size = S.HEADING_SIZE.get(b.level, S.BODY_SIZE)
        if n and size != S.BODY_SIZE:
            for _ in range(n):
                await page.keyboard.press("Shift+ArrowLeft")
            await asyncio.sleep(0.2)
            await set_font_size(page, frame, size)
            await page.keyboard.press("ArrowRight")
        await page.keyboard.press("Enter")
        if size != S.BODY_SIZE:
            # 크기 모드가 남으면 다음 문단까지 제목 크기가 된다 (취소선과 같은 함정).
            await set_font_size(page, frame, S.BODY_SIZE)
        return
    if b.type == "code":
        await page.keyboard.type(b.raw, delay=5)
    elif b.type == "table":
        # 표는 키보드로 만들 수 없다 (툴바 전용). 붙여넣기가 실패하면
        # 최소한 내용은 살리도록 " | " 로 이은 텍스트로 떨군다.
        for r in b.rows:
            for j, cell in enumerate(r):
                if j:
                    await page.keyboard.type(" | ")
                await type_spans(page, frame, cell)
            await page.keyboard.press("Enter")
        return
    elif b.type == "list":
        # "- " / "1. " 를 치면 에디터가 진짜 목록으로 자동 변환한다 (실측).
        # 단 마커는 첫 항목에만 친다. 목록에 들어간 뒤에는 Enter 가 다음 항목을
        # 만들어주므로, 마커를 또 치면 "- 비순서 둘" 처럼 리터럴로 남는다.
        for j, item in enumerate(b.items):
            if j == 0:
                await page.keyboard.type("1. " if b.ordered else "- ")
            await type_spans(page, frame, item)
            await page.keyboard.press("Enter")
        # 마지막 Enter 로 빈 항목이 남는다. 한 번 더 눌러 목록에서 빠져나온다.
        await page.keyboard.press("Enter")
        return
    else:
        await type_spans(page, frame, b.spans)
    await page.keyboard.press("Enter")
