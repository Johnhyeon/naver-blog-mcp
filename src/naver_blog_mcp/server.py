"""MCP 서버. 기본값은 임시저장이고, 발행은 별도 툴로 분리한다.

이유: 셀렉터가 깨졌을 때 라이브 블로그에 깨진 글이 올라가는 것보다
      임시저장함에 쌓이는 게 낫다. 자동화보다 되돌릴 수 있음이 우선.

카테고리와 태그는 에디터 첫 화면에 없다. 발행 설정 레이어를 열어야 DOM 에 생긴다.
(2026-08-25 실측) 레이어를 여는 것 자체는 발행이 아니다.
"""

from __future__ import annotations

import functools
import json
import os
import re
from pathlib import Path

# mcp 2.0 에서 FastMCP -> MCPServer 로 이름이 바뀌었다. 데코레이터/run() 은 동일.
from mcp.server import MCPServer

from . import selectors as S
from .editor import (
    EditorError,
    close_draft_list,
    close_publish_panel,
    delete_draft as _delete_draft,
    delete_post as _delete_post,
    draft_count,
    get_editor_frame,
    goto_editor,
    list_drafts as _list_drafts,
    load_draft,
    open_publish_panel,
    read_categories,
    set_category,
    set_tags,
    set_visibility,
    title_is_empty,
    write_post,
)
from .session import Session

mcp = MCPServer("naver-blog")
BLOG_ID = os.getenv("NAVER_BLOG_ID", "")

# 읽기 전용 모드: 발행과 삭제 도구를 아예 등록하지 않는다.
# 부르지 못하게 막는 가장 확실한 방법은 도구 목록에 없는 것이다.
READONLY = os.getenv("NAVER_BLOG_READONLY", "").lower() not in ("", "0", "false", "no")

_NO_BLOG_ID = (
    "NAVER_BLOG_ID 가 설정되지 않았습니다.\n"
    "MCP 설정의 env 에 NAVER_BLOG_ID=<블로그아이디> 를 넣으세요 "
    "(blog.naver.com/<여기>)."
)


def guarded(fn):
    """블로그 아이디 확인 + 예외를 읽을 수 있는 메시지로.

    BLOG_ID 가 비면 URL 이 https://blog.naver.com/?Redirect=Write 가 되어
    글쓰기 화면이 안 뜨고, 결국 "제목 영역을 못 찾음 — selectors.TITLE 갱신 필요"
    같은 엉뚱한 메시지가 나온다. 원인을 그대로 말해주는 편이 낫다.

    EditorError 가 아닌 Playwright 예외(파일 다이얼로그 타임아웃 등)도
    툴 크래시로 나가지 않게 여기서 잡는다.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if not BLOG_ID:
            return _NO_BLOG_ID
        try:
            return await fn(*args, **kwargs)
        except EditorError as e:
            return f"실패: {e}"
        except Exception as e:  # noqa: BLE001 - 툴은 문자열을 돌려줘야 한다
            return f"예기치 못한 오류: {type(e).__name__}: {e}"

    return wrapper


@mcp.tool()
@guarded
async def check_session() -> str:
    """세션이 살아있는지 확인. 만료면 login_setup.py 재실행이 필요하다."""
    try:
        async with Session(headless=True) as ctx:
            page = await ctx.new_page()
            # DOM 마크(LOGGED_IN_MARK)는 로그인 상태에서도 visible=False 라 못 쓴다.
            # MyBlog.naver 가 로그인 페이지로 튕기는지로 판정한다. (실측)
            await page.goto(S.MYBLOG_PROBE_URL, wait_until="domcontentloaded")
            if "nid.naver.com" in page.url:
                return "세션 만료 — uv run python login_setup.py 재실행 필요"
            return f"세션 정상 ({page.url})"
    except Exception as e:
        return f"확인 실패: {e}"


@mcp.tool()
@guarded
async def list_categories() -> str:
    """블로그 카테고리 목록을 조회한다. 발행하지 않는다.

    발행 설정 레이어를 열어서 읽고 다시 닫는다. 하위 카테고리는 들여쓰기로 표시된다.
    """
    async with Session() as ctx:
        page = await ctx.new_page()
        try:
            frame = await goto_editor(page, BLOG_ID)
            cats = await read_categories(page, frame)
            await close_publish_panel(page, frame)
        except EditorError as e:
            return f"조회 실패: {e}"
        if not cats:
            return "카테고리를 못 읽음 — selectors.CATEGORY_ITEM_ALL 갱신 필요"
        return "\n".join(f"{'  - ' if child else ''}{name}" for name, child in cats)


@mcp.tool()
@guarded
async def create_draft(
    title: str,
    markdown: str,
    category: str = "",
    tags: list[str] | None = None,
) -> str:
    """마크다운으로 글을 작성하고 임시저장한다. 발행하지 않는다.

    지원 서식: 제목(1~3), 굵게, 기울임, 취소선, 인라인코드, 링크,
              인용, 순서/비순서 목록, 코드블록, 구분선, 이미지, 표(GFM 파이프),
              파일 첨부, 수식, 장소.
    이미지는 ![캡션](로컬경로), 파일 첨부는 :::file 로컬경로::: 형식이다.
    둘 다 로컬 파일 경로여야 한다 (URL 불가). 파일은 개당 10MB 제한.
    수식은 :::formula x^2+y^2=z^2:::, 장소는 :::place 강남역::: 이다.
    장소는 검색 결과 중 첫 번째를 쓰며, 무엇을 골랐는지 결과에 표시된다.

    글과 이미지가 한 폴더에 있으면 create_draft_from_folder 가 편하다.
    """
    return await _draft(title, markdown, category, tags)


async def _draft(title: str, markdown: str, category: str, tags: list[str] | None) -> str:
    async with Session() as ctx:
        page = await ctx.new_page()
        try:
            await goto_editor(page, BLOG_ID)
            # 세그먼트별로 붙여넣기/타이핑 중 무엇을 썼는지 돌려준다.
            notes = await write_post(page, title, markdown)
        except EditorError as e:
            return f"작성 실패: {e}"

        frame = await get_editor_frame(page)
        before = await draft_count(frame)

        # 카테고리/태그는 발행 레이어 안에만 있다. 열고, 설정하고, 다시 닫는다.
        if category or tags:
            try:
                await open_publish_panel(page, frame)
                if category:
                    await set_category(page, frame, category)
                    notes.append(f"카테고리={category}")
                if tags:
                    n = await set_tags(page, frame, tags)
                    notes.append(f"태그 {n}개")
            except EditorError as e:
                notes.append(f"설정 실패({e})")
            finally:
                await close_publish_panel(page, frame)

        save = await S.first(frame, S.SAVE_DRAFT)
        if not save:
            return "임시저장 버튼을 못 찾음 — 브라우저에서 직접 저장하세요"
        await save.click()
        await page.wait_for_timeout(2500)

        after = await draft_count(frame)
        if before is not None and after is not None and after <= before:
            return (f"임시저장이 안 된 것 같습니다 (임시저장 수 {before} -> {after}). "
                    f"브라우저에서 확인하세요.")
        tail = f" [{', '.join(notes)}]" if notes else ""
        return (f"임시저장 완료: {title}{tail}\n"
                f"임시저장 수 {before} -> {after}\n"
                f"확인 후 publish_draft 를 호출하세요.")


# ---------------------------------------------------------------- 폴더 통째로

_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_FILE_RE = re.compile(r":::file\s+([^:]+):::")
_MAX_BYTES = 10 * 1024 * 1024


def _abs_local(base: Path, raw: str) -> Path:
    p = Path(raw.strip().strip('"').strip("'")).expanduser()
    return p if p.is_absolute() else (base / p)


def preflight(base: Path, markdown: str) -> tuple[str, list[str]]:
    """이미지·첨부 경로를 절대경로로 바꾸고, 없는 파일과 용량 초과를 모아 돌려준다.

    브라우저를 띄우기 전에 한 번에 확인한다. 넣다가 중간에 멈추면 임시저장함에
    반쪽짜리 글이 남고, 어디까지 들어갔는지 사람이 다시 확인해야 한다.
    """
    problems: list[str] = []

    def fix_img(m: re.Match) -> str:
        p = _abs_local(base, m.group(2))
        if not p.exists():
            problems.append(f"이미지 없음: {m.group(2)}")
        elif p.stat().st_size > _MAX_BYTES:
            problems.append(f"10MB 초과: {m.group(2)} ({p.stat().st_size / 1_048_576:.1f}MB)")
        return f"![{m.group(1)}]({p})"

    def fix_file(m: re.Match) -> str:
        p = _abs_local(base, m.group(1))
        if not p.exists():
            problems.append(f"첨부 없음: {m.group(1)}")
        return f":::file {p}:::"

    out = _FILE_RE.sub(fix_file, _IMG_RE.sub(fix_img, markdown))
    return out, problems


def split_title(markdown: str) -> tuple[str, str]:
    """첫 줄이 '# 제목' 이면 떼어낸다. 제목이 본문에 한 번 더 나오지 않게."""
    lines = markdown.lstrip().splitlines()
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip(), "\n".join(lines[1:]).lstrip("\n")
    return "", markdown


def read_meta(folder: Path) -> dict:
    """meta.json 에서 제목·카테고리·태그를 읽는다. 없거나 깨졌으면 빈 값.

    title·category·tags 를 먼저 보고, title 이 없으면
    title_candidates 와 title_recommended 조합을 쓴다.
    """
    f = folder / "meta.json"
    if not f.exists():
        return {}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}
    title = d.get("title") or ""
    if not title:
        cands = d.get("title_candidates") or []
        i = d.get("title_recommended", 0)
        if isinstance(i, int) and 0 <= i < len(cands):
            title = cands[i]
    return {"title": title, "category": d.get("category") or "", "tags": d.get("tags") or []}


@mcp.tool()
@guarded
async def create_draft_from_folder(
    folder: str,
    markdown_file: str = "post.md",
    title: str = "",
    category: str = "",
    tags: list[str] | None = None,
) -> str:
    """글 파일과 이미지가 같은 폴더에 있을 때 폴더째 임시저장한다. 발행하지 않는다.

    - 이미지는 폴더 기준 상대경로로 적어도 된다(`![캡션](images/01-x.png)`).
    - 같은 폴더의 meta.json 에서 제목·카테고리·태그를 읽는다. 인자로 준 값이 우선.
    - 글 첫 줄이 '# 제목' 이면 제목으로 쓰고 본문에서 뺀다.
    - 브라우저를 열기 전에 이미지부터 확인한다. 하나라도 없으면 아무것도 하지 않는다.
    """
    base = Path(folder).expanduser().resolve()
    src = base / markdown_file
    if not src.exists():
        return f"글 파일 없음: {src}"
    head_title, body = split_title(src.read_text(encoding="utf-8"))
    meta = read_meta(base)
    title = title or meta.get("title") or head_title
    category = category or meta.get("category", "")
    tags = tags or meta.get("tags") or None
    if not title:
        return "제목이 없습니다 — 인자로 주거나 글 첫 줄에 '# 제목' 을 두세요."
    body, problems = preflight(base, body)
    if problems:
        return "넣기 전에 걸린 것 (아무것도 하지 않았습니다):\n- " + "\n- ".join(problems)
    return await _draft(title, body, category, tags)


@mcp.tool()
@guarded
async def list_drafts() -> str:
    """임시저장 글 목록을 조회한다. 최신순."""
    async with Session() as ctx:
        page = await ctx.new_page()
        try:
            frame = await goto_editor(page, BLOG_ID)
            drafts = await _list_drafts(page, frame)
            await close_draft_list(page, frame)
        except EditorError as e:
            return f"조회 실패: {e}"
        if not drafts:
            return "임시저장된 글이 없습니다"
        return "\n".join(f"{t}  ({d})" for t, d in drafts)


@guarded
async def delete_draft(confirm: bool = False, title: str = "") -> str:
    """임시저장 글을 삭제한다. 복구되지 않으므로 confirm=True 를 명시해야 한다.

    title 규칙은 publish_draft 와 같다: 부분 일치 가능, 여러 글과 맞으면 거부,
    비워두면 임시저장이 정확히 1건일 때만 동작한다.
    """
    if not confirm:
        return "삭제하려면 confirm=True 로 다시 호출하세요. 복구되지 않습니다."
    async with Session() as ctx:
        page = await ctx.new_page()
        try:
            frame = await goto_editor(page, BLOG_ID)
            gone = await _delete_draft(page, frame, title)
            left = await draft_count(frame)
        except EditorError as e:
            return f"삭제 실패: {e}"
        return f"삭제 완료: {gone}\n남은 임시저장: {left}건"


@guarded
async def delete_post(url_or_log_no: str, confirm: bool = False) -> str:
    """발행된 글을 삭제한다. 복구되지 않으므로 confirm=True 를 명시해야 한다.

    임시저장 삭제(delete_draft)와는 다른 대상이다. 글 URL 이나 글 번호를 넘긴다.
    대상을 반드시 명시해야 한다 — 제목으로 찾아주지 않는다.
    """
    if not confirm:
        return "삭제하려면 confirm=True 로 다시 호출하세요. 복구되지 않습니다."
    async with Session() as ctx:
        page = await ctx.new_page()
        try:
            gone = await _delete_post(page, BLOG_ID, url_or_log_no)
        except EditorError as e:
            return f"삭제 실패: {e}"
        return f"글 삭제 완료: {gone}"


@guarded
async def publish_draft(confirm: bool = False, title: str = "",
                        visibility: str = "") -> str:
    """임시저장 글을 불러와서 발행한다. confirm=True 를 명시해야 동작한다.

    글쓰기 화면은 임시저장 글을 자동 복구하지 않으므로 목록에서 명시적으로 불러온다.
    title 은 부분 일치도 되지만, 여러 글과 맞으면 거부한다. 비워두면 임시저장이
    정확히 1건일 때만 동작한다 — 발행은 되돌리기 어려우니 대상을 사람이 정하게 한다.

    visibility: public | neighbor | both_neighbor | private.
    비우면 글에 이미 설정된 값을 그대로 쓴다.
    """
    if not confirm:
        return "발행하려면 confirm=True 로 다시 호출하세요."
    async with Session() as ctx:
        page = await ctx.new_page()
        try:
            frame = await goto_editor(page, BLOG_ID)
            loaded = await load_draft(page, frame, title)
        except EditorError as e:
            return f"발행 실패: {e}"

        # 빈 글이 발행되는 사고를 막는다. 되돌릴 수 있음이 자동화보다 우선.
        if await title_is_empty(frame):
            return "제목이 비어 있어 발행을 중단했습니다."

        try:
            await open_publish_panel(page, frame)
            if visibility:
                await set_visibility(page, frame, visibility)
        except EditorError as e:
            return f"발행 실패: {e}"
        confirm_btn = await S.first(frame, S.PUBLISH_CONFIRM)
        if not confirm_btn:
            return "발행 확인 버튼을 못 찾음 — selectors.PUBLISH_CONFIRM 갱신 필요"
        await confirm_btn.click()
        await page.wait_for_timeout(3000)
        vis = f" ({visibility})" if visibility else ""
        return f"발행 완료: {loaded}{vis}\n{page.url}"


# 발행과 삭제는 되돌리기 어렵다. 읽기 전용 모드면 도구 목록에서 아예 뺀다.
if not READONLY:
    for _fn in (publish_draft, delete_draft, delete_post):
        mcp.tool()(_fn)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
