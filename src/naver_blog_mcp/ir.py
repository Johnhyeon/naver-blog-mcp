"""본문 중간표현(IR).

마크다운 -> IR -> (HTML 클립보드 | 키스트로크) 두 경로로 갈라진다.
셀렉터에 전혀 의존하지 않으므로 네이버가 에디터를 바꿔도 이 파일은 안 건드린다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------- inline

@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    href: str | None = None


# ---------------------------------------------------------------- blocks

BlockType = Literal["heading", "paragraph", "quote", "list", "code", "image", "divider", "table", "file", "formula", "place"]


@dataclass
class Block:
    type: BlockType
    spans: list[Span] = field(default_factory=list)
    level: int = 0            # heading: 1~3
    ordered: bool = False     # list
    items: list[list[Span]] = field(default_factory=list)  # list
    lang: str = ""            # code
    raw: str = ""             # code 본문
    path: str = ""            # image/file 로컬 경로
    caption: str = ""         # image 캡션
    rows: list[list[list[Span]]] = field(default_factory=list)  # table: 행 > 셀 > 스팬
    header: bool = False      # table: 첫 행이 헤더인가


# ---------------------------------------------------------------- 인라인 파서

_INLINE = re.compile(
    r"(?P<link>\[(?P<ltext>[^\]]+)\]\((?P<href>[^)]+)\))"
    r"|(?P<bold>\*\*(?P<btext>.+?)\*\*)"
    r"|(?P<strike>~~(?P<stext>.+?)~~)"
    r"|(?P<code>`(?P<ctext>[^`]+)`)"
    r"|(?P<italic>(?<![*\w])\*(?P<itext>[^*]+)\*(?![*\w]))"
)


def parse_inline(text: str) -> list[Span]:
    spans: list[Span] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            spans.append(Span(text[pos:m.start()]))
        if m.group("link"):
            spans.append(Span(m.group("ltext"), href=m.group("href")))
        elif m.group("bold"):
            spans.append(Span(m.group("btext"), bold=True))
        elif m.group("strike"):
            spans.append(Span(m.group("stext"), strike=True))
        elif m.group("code"):
            spans.append(Span(m.group("ctext"), code=True))
        elif m.group("italic"):
            spans.append(Span(m.group("itext"), italic=True))
        pos = m.end()
    if pos < len(text):
        spans.append(Span(text[pos:]))
    return spans or [Span("")]


# ---------------------------------------------------------------- 블록 파서

_IMG = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)\s*$")


# :::name 인자::: 형태의 디렉티브. 마크다운에 표현이 없는 네이버 전용 블록용이다.
# 표준 마크다운 뷰어에서는 그냥 텍스트로 보이므로 원문이 깨지지 않는다.
# :::file 경로:::  :::formula x^2+y^2=z^2:::  :::place 강남역:::
_DIRECTIVE = re.compile(r"^:::\s*(?P<name>[a-z]+)\s+(?P<arg>.+?)\s*:::$")
_KNOWN_DIRECTIVES = {"file", "formula", "place"}


_TABLE_SEP = re.compile(r"^\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?$")


def _table_cells(line: str) -> list[list[Span]]:
    """한 행을 셀 단위 스팬 리스트로. 양끝 파이프는 있어도 없어도 된다."""
    return [parse_inline(c.strip()) for c in line.strip().strip("|").split("|")]


def parse_markdown(md: str) -> list[Block]:
    """네이버가 실제로 표현 가능한 것만 남긴다.

    지원: 제목(1~3), 문단, 인용, 순서/비순서 목록, 코드블록, 이미지, 구분선, 표,
          파일 첨부(:::file 경로:::), 수식(:::formula ...:::), 장소(:::place 검색어:::)
    미지원(문단으로 강등): 각주, 중첩목록 3단계 이상

    표는 GFM 파이프 문법이다. 붙여넣기로 se-table 컴포넌트가 되는 것을 실측했다.
    셀 안의 이스케이프된 파이프(\\|)는 지원하지 않는다.
    """
    blocks: list[Block] = []
    lines = md.replace("\r\n", "\n").split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 코드블록
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            blocks.append(Block("code", lang=lang, raw="\n".join(body)))
            continue

        # 구분선
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            blocks.append(Block("divider"))
            i += 1
            continue

        # 디렉티브 (:::file 경로:::)
        m = _DIRECTIVE.match(stripped)
        if m and m.group("name") in _KNOWN_DIRECTIVES:
            name, arg = m.group("name"), m.group("arg")
            # file 은 경로, formula/place 는 스크립트·검색어라 raw 에 담는다.
            blocks.append(Block(name, path=arg) if name == "file" else Block(name, raw=arg))
            i += 1
            continue
        # 모르는 디렉티브는 버리지 않고 문단으로 강등한다 (아래 문단 처리로 흘러감).

        # 표 (GFM 파이프). 다음 줄이 구분선이어야 표로 본다.
        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1].strip()):
            rows: list[list[list[Span]]] = [_table_cells(stripped)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_table_cells(lines[i].strip()))
                i += 1
            blocks.append(Block("table", rows=rows, header=True))
            continue

        # 이미지 (단독 줄일 때만)
        m = _IMG.match(stripped)
        if m:
            blocks.append(Block("image", path=m.group("src"), caption=m.group("alt")))
            i += 1
            continue

        # 제목
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 3)  # 네이버는 사실상 3단계
            blocks.append(Block("heading", spans=parse_inline(m.group(2)), level=level))
            i += 1
            continue

        # 인용
        if stripped.startswith(">"):
            buf: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(Block("quote", spans=parse_inline(" ".join(buf))))
            continue

        # 목록
        m = re.match(r"^\s*([-*+]|\d+\.)\s+(.*)$", line)
        if m:
            ordered = bool(re.match(r"\d+\.", m.group(1)))
            items: list[list[Span]] = []
            while i < len(lines):
                mm = re.match(r"^\s*([-*+]|\d+\.)\s+(.*)$", lines[i])
                if not mm:
                    break
                items.append(parse_inline(mm.group(2)))
                i += 1
            blocks.append(Block("list", ordered=ordered, items=items))
            continue

        # 문단 (빈 줄까지 이어붙임)
        buf = []
        while i < len(lines) and lines[i].strip():
            nxt = lines[i].strip()
            if nxt.startswith(("#", ">", "```")) or _IMG.match(nxt):
                break
            if re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
                break
            if "|" in nxt and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1].strip()):
                break
            md = _DIRECTIVE.match(nxt)
            if md and md.group("name") in _KNOWN_DIRECTIVES:
                break
            buf.append(nxt)
            i += 1
        if buf:
            blocks.append(Block("paragraph", spans=parse_inline(" ".join(buf))))

    return blocks


# ---------------------------------------------------------------- HTML 직렬화

def _span_html(s: Span) -> str:
    t = html.escape(s.text)
    if s.code:
        t = f"<code>{t}</code>"
    if s.bold:
        t = f"<b>{t}</b>"
    if s.italic:
        t = f"<i>{t}</i>"
    if s.underline:
        t = f"<u>{t}</u>"
    if s.strike:
        # <s> 는 네이버 붙여넣기 살균기가 지운다. <del>/<strike> 는 살아남는다. (2026-08-25 실측)
        t = f"<del>{t}</del>"
    if s.href:
        t = f'<a href="{html.escape(s.href, quote=True)}">{t}</a>'
    return t


def block_html(b: Block) -> str:
    """블록 하나를 클립보드용 HTML로. 이미지 블록은 빈 문자열(별도 처리)."""
    if b.type == "image":
        return ""
    if b.type == "divider":
        return "<hr>"
    if b.type == "code":
        return f"<pre>{html.escape(b.raw)}</pre>"
    if b.type == "heading":
        inner = "".join(_span_html(s) for s in b.spans)
        return f"<h{b.level}>{inner}</h{b.level}>"
    if b.type == "quote":
        inner = "".join(_span_html(s) for s in b.spans)
        return f"<blockquote>{inner}</blockquote>"
    if b.type == "list":
        tag = "ol" if b.ordered else "ul"
        lis = "".join(
            "<li>" + "".join(_span_html(s) for s in item) + "</li>" for item in b.items
        )
        return f"<{tag}>{lis}</{tag}>"
    if b.type == "table":
        def _row(cells, tag):
            return "<tr>" + "".join(
                f"<{tag}>" + "".join(_span_html(s) for s in c) + f"</{tag}>" for c in cells
            ) + "</tr>"
        head = f"<thead>{_row(b.rows[0], 'th')}</thead>" if b.header and b.rows else ""
        body = b.rows[1:] if b.header else b.rows
        return f"<table>{head}<tbody>{''.join(_row(r, 'td') for r in body)}</tbody></table>"
    inner = "".join(_span_html(s) for s in b.spans)
    return f"<p>{inner}</p>"


@dataclass
class Segment:
    """붙여넣기가 안 되는 블록을 기준으로 잘라낸 조각.

    kind='html'   붙여넣기
    kind='image'  / 'file'  툴바 경유 업로드
    kind='manual' 붙여넣기가 뭉개는 블록. 툴바/키보드로 따로 만든다.
    """
    kind: Literal["html", "image", "file", "manual"]
    html: str = ""
    path: str = ""
    caption: str = ""
    # 붙여넣기가 실패했을 때 이 세그먼트만 다시 타이핑하기 위한 원본 블록.
    # 이게 없으면 폴백이 문서 전체를 다시 치게 된다.
    blocks: list["Block"] = field(default_factory=list)


# 붙여넣기로 살아남는 블록.  [2026-08-25 실측]
# 목록(<ul>/<ol>)과 코드블록(<pre>)은 네이버 살균기가 문단으로 뭉개므로 제외한다.
PASTE_SAFE: frozenset[str] = frozenset({"heading", "paragraph", "quote", "divider", "table"})


def _has_link(b: Block) -> bool:
    if any(sp.href for sp in b.spans):
        return True
    return any(sp.href for item in b.items for sp in item)


def is_paste_safe(b: Block) -> bool:
    """붙여넣기로 원형이 보존되는 블록인가.

    링크는 살균기가 <a> 를 지운다. 그래서 링크가 든 **문단**은 타이핑 경로로 보내
    툴바로 링크를 건다.

    제목도 타이핑으로 만들 수 있다 — 제목은 글자 크기일 뿐이라 툴바로 지정 가능하다
    (스마트에디터 ONE 에는 H1~H3 개념이 없다). 그래서 제목도 링크가 있으면 타이핑한다.

    인용/표는 링크가 있어도 붙여넣기로 보낸다 — 타이핑으로는 그 블록 형태 자체를
    만들 수 없어서(se-quotation, se-table) 링크를 살리려다 블록을 잃는다.
    이 경우 링크만 사라진다. 알려진 한계다.
    """
    if b.type not in PASTE_SAFE:
        return False
    if b.type not in ("paragraph", "heading"):
        return True
    return not _has_link(b)


def segment(blocks: list[Block]) -> list[Segment]:
    """붙여넣기 가능한 구간과 아닌 구간으로 쪼갠다.

    이미지·파일은 업로드, 목록·코드블록은 툴바/키보드로 만들어야 한다.
    """
    out: list[Segment] = []
    buf: list[str] = []
    src: list[Block] = []
    man: list[Block] = []

    def flush_html() -> None:
        nonlocal buf, src
        if buf:
            out.append(Segment("html", html="".join(buf), blocks=src))
            buf, src = [], []

    def flush_manual() -> None:
        nonlocal man
        if man:
            out.append(Segment("manual", blocks=man))
            man = []

    for b in blocks:
        if b.type in ("image", "file"):
            flush_html()
            flush_manual()
            out.append(Segment(b.type, path=b.path, caption=b.caption))
        elif is_paste_safe(b):
            flush_manual()
            buf.append(block_html(b))
            src.append(b)
        else:
            flush_html()
            man.append(b)
    flush_html()
    flush_manual()
    return out
