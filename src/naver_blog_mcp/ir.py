"""본문 중간표현(IR).

마크다운 -> IR -> (HTML 클립보드 | 키스트로크) 두 경로로 갈라진다.
셀렉터에 전혀 의존하지 않으므로 네이버가 에디터를 바꿔도 이 파일은 안 건드린다.
"""

from __future__ import annotations

import html
import os
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

BlockType = Literal["heading", "paragraph", "quote", "list", "code", "image", "divider", "table", "file", "formula", "place", "news", "stock", "book", "video"]


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
    gap: bool = False         # 앞에 빈 줄이 있었나. 에디터에서도 한 줄 띄운다


# ---------------------------------------------------------------- 인라인 파서

_INLINE = re.compile(
    r"(?P<link>\[(?P<ltext>[^\]]+)\]\((?P<href>[^)]+)\))"
    r"|(?P<bold>\*\*(?P<btext>.+?)\*\*)"
    r"|(?P<strike>~~(?P<stext>.+?)~~)"
    # 밑줄은 표준 마크다운에 없다. ++글자++ 로 쓴다(markdown-it ins 확장과 같은 표기).
    r"|(?P<under>\+\+(?P<utext>.+?)\+\+)"
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
        elif m.group("under"):
            spans.append(Span(m.group("utext"), underline=True))
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
# :::news 매체 | 기사 제목:::  :::stock 072950:::  :::book 책 제목:::  (글감 카드, material.py)
# :::video https://www.youtube.com/watch?v=...:::  (유튜브 영상 플레이어)
_DIRECTIVE = re.compile(r"^:::\s*(?P<name>[a-z]+)\s+(?P<arg>.+?)\s*:::$")
_KNOWN_DIRECTIVES = {"file", "formula", "place", "news", "stock", "book", "video"}

# 에디터가 영상 플레이어(se-oembed)로 바꾸는 유튜브 주소. 다른 주소는 링크 카드가 되므로 받지 않는다.
YOUTUBE_URL = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)[\w-]{11}(?:[?&#][^\s]*)?$")


_TABLE_SEP = re.compile(r"^\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?$")
# 제목은 샵 뒤에 공백이 있어야 한다. "#태그" 같은 줄은 제목이 아니다.
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


# 표 색 (2026-09-18 실측)
#
# 스마트에디터 표는 셀의 **배경색과 테두리, 굵게만** 살린다. 글자색은 죽는다 —
# style="color:", <span style="color:">, <font color=""> 셋 다 검정으로 돌아온다.
# 그래서 charts.py 의 그림 표처럼 "검정 헤더 + 흰 글자" 를 쓸 수 없다.
# 밝은 배경에 검정 글자로만 층을 만든다: 헤더가 제일 진하고, 기준 열(첫 열)이
# 그다음, 나머지 셀은 흰 배경.
#
# 색은 leetkey-blog RULES A8 팔레트 안에서 고른다.
#   #E2DDD0 웜 그레이 / #F7F4EC 웜화이트 / #8A8A8A 선에만 쓰는 회색
# NAVER_TABLE_STYLE=off 로 끄면 예전처럼 스타일 없는 <table> 이 된다.
TABLE_HEAD_BG = "#E2DDD0"
TABLE_KEY_BG = "#F7F4EC"
TABLE_LINE = "#8A8A8A"


def _table_colors() -> tuple[str, str, str] | None:
    """끄면 None. 그때는 예전처럼 스타일 속성이 아예 없는 <table> 을 만든다."""
    if os.getenv("NAVER_TABLE_STYLE", "").strip().lower() in {"off", "0", "no", "false"}:
        return None
    return TABLE_HEAD_BG, TABLE_KEY_BG, TABLE_LINE


def _cell_width(cells: list[list[Span]]) -> int:
    """셀 글자 폭. 한글과 전각은 2, 나머지는 1로 센다."""
    text = "".join(sp.text for sp in cells)
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def _col_plan(rows: list[list[list[Span]]]) -> list[str]:
    """열마다 너비(%). 열에서 가장 긴 글자 폭으로 나눈다.

    스마트에디터는 열을 1:1 로 잡는다. 순서 칸에 숫자 하나가 들어가는 표가
    긴 설명 칸과 같은 너비를 차지해 헐렁해 보였다(대표 2026-09-23 아침 브리핑 글).
    짧은 칸(숫자, 기호, 짧은 이름)을 좁힌다. 가운데 맞춤은 에디터가 지워서 못 쓴다.
    """
    n = max((len(r) for r in rows), default=0)
    if n < 2:
        return []
    longest = []
    for i in range(n):
        longest.append(max((_cell_width(r[i]) for r in rows if i < len(r)), default=2))
    # 한 열이 너무 얇아지지 않게 바닥을 둔다. 바닥이 없으면 글자가 세로로 쪼개진다
    floor = 6
    raw = [max(w, floor) for w in longest]
    total = sum(raw) or 1
    return [f"{max(round(w / total * 100), floor)}%" for w in raw]


def _table_cells(line: str) -> list[list[Span]]:
    """한 행을 셀 단위 스팬 리스트로. 양끝 파이프는 있어도 없어도 된다."""
    return [parse_inline(c.strip()) for c in line.strip().strip("|").split("|")]


def parse_markdown(md: str) -> list[Block]:
    """네이버가 실제로 표현 가능한 것만 남긴다.

    지원: 제목(1~3), 문단, 인용, 순서/비순서 목록, 코드블록, 이미지, 구분선, 표,
          파일 첨부(:::file 경로:::), 수식(:::formula ...:::), 장소(:::place 검색어:::),
          글감 카드(:::news 매체 | 제목:::, :::stock 코드:::, :::book 제목:::),
          유튜브 영상(:::video 주소:::)
    미지원(문단으로 강등): 각주, 중첩목록 3단계 이상

    표는 GFM 파이프 문법이다. 붙여넣기로 se-table 컴포넌트가 되는 것을 실측했다.
    셀 안의 이스케이프된 파이프(\\|)는 지원하지 않는다.
    """
    blocks: list[Block] = []
    lines = md.replace("\r\n", "\n").split("\n")
    i = 0
    gap = [False]

    def add(b: Block) -> None:
        # 앞에 빈 줄이 있었으면 표시한다. 첫 블록과 그림·파일 바로 뒤에는 붙이지 않는다.
        # 그림과 구분선 컴포넌트는 에디터가 위아래 간격을 이미 준다.
        if gap[0] and blocks and blocks[-1].type not in ("image", "file", "divider", "news", "stock", "book", "video"):
            b.gap = True
        gap[0] = False
        blocks.append(b)

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            gap[0] = True
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
            add(Block("code", lang=lang, raw="\n".join(body)))
            continue

        # 구분선
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            add(Block("divider"))
            i += 1
            continue

        # 디렉티브 (:::file 경로:::)
        m = _DIRECTIVE.match(stripped)
        if m and m.group("name") in _KNOWN_DIRECTIVES:
            name, arg = m.group("name"), m.group("arg")
            # file 은 경로, formula/place 는 스크립트·검색어라 raw 에 담는다.
            add(Block(name, path=arg) if name == "file" else Block(name, raw=arg))
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
            add(Block("table", rows=rows, header=True))
            continue

        # 이미지 (단독 줄일 때만)
        m = _IMG.match(stripped)
        if m:
            add(Block("image", path=m.group("src"), caption=m.group("alt")))
            i += 1
            continue

        # 제목
        m = _HEADING.match(stripped)
        if m:
            level = min(len(m.group(1)), 3)  # 네이버는 사실상 3단계
            add(Block("heading", spans=parse_inline(m.group(2)), level=level))
            i += 1
            continue

        # 인용
        if stripped.startswith(">"):
            buf: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            add(Block("quote", spans=parse_inline(" ".join(buf))))
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
            add(Block("list", ordered=ordered, items=items))
            continue

        # 문단 (빈 줄까지 이어붙임)
        buf = []
        while i < len(lines) and lines[i].strip():
            nxt = lines[i].strip()
            # "#" 로 시작해도 제목이 아닐 수 있다. 해시태그 줄("#광통신 #종목분석")이 그렇다.
            # 제목이 아닌데 여기서 끊으면 아무 줄도 소비하지 못해 무한 루프가 된다.
            if nxt.startswith((">", "```")) or _HEADING.match(nxt) or _IMG.match(nxt):
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
            # 줄을 공백으로 이어 붙이면 "데이터 출처" 같은 목록이 한 줄로 뭉친다.
            # 사람이 줄을 바꿨으면 에디터에서도 바꾼다(2026-09-16 실측).
            for text in buf:
                add(Block("paragraph", spans=parse_inline(text)))
        elif i < len(lines):
            # 여기까지 왔는데 한 줄도 못 먹었다면 어떤 분기도 이 줄을 처리하지 못한 것이다.
            # 그대로 두면 같은 자리를 무한히 돈다 (2026-09-16 실측: 해시태그 줄에서 정지).
            # 내용을 버리지 않고 문단으로 강등한 뒤 반드시 한 줄 전진한다.
            add(Block("paragraph", spans=parse_inline(lines[i].strip())))
            i += 1

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
    """블록 하나를 클립보드용 HTML로. 이미지 블록은 빈 문자열(별도 처리).

    앞에 빈 줄이 있던 블록(gap)은 빈 문단을 하나 앞에 붙여 에디터에서도 띄운다.
    """
    html_ = _block_html(b)
    return "<p><br></p>" + html_ if (b.gap and html_) else html_


# 제목 글자 크기(px). 에디터 크기 코드 fs24/fs19/fs16 에 맞춘다.
HEADING_PX = {1: 24, 2: 19, 3: 16}


def _block_html(b: Block) -> str:
    if b.type == "image":
        return ""
    if b.type == "divider":
        return "<hr>"
    if b.type == "code":
        return f"<pre>{html.escape(b.raw)}</pre>"
    if b.type == "heading":
        # <h2> 는 에디터가 크기를 제멋대로 준다. 굵게와 픽셀 크기를 직접 준다.
        # 제목 뒤 서식 초기화는 write_post 가 한다(제목은 단독 세그먼트).
        inner = "".join(_span_html(s) for s in b.spans)
        px = HEADING_PX.get(b.level, 19)
        return f'<p><b><span style="font-size:{px}px">{inner}</span></b></p>'
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
        colors = _table_colors()

        plan = _col_plan(b.rows)

        def _cell(c, tag, *, bg="", col=0):
            inner = "".join(_span_html(s) for s in c)
            bits = []
            if col < len(plan):
                # 가운데 맞춤은 넣지 않는다. 스마트에디터가 붙여넣기에서 셀의
                # text-align 과 옛 align 속성을 둘 다 지운다(2026-09-23 실측,
                # scripts/check_table_width.py). 너비는 살아남는다
                bits.append(f"width:{plan[col]}")
            if colors is not None:
                bits.append(f"border:1px solid {colors[2]}")
                bits.append("padding:6px")
                if bg:
                    bits.append(f"background-color:{bg}")
                    # 헤더와 기준 열은 굵게. 굵게는 붙여넣기에서 살아남는다
                    inner = f"<b>{inner}</b>"
            if not bits:
                return f"<{tag}>{inner}</{tag}>"
            return f'<{tag} style="{";".join(bits)}">{inner}</{tag}>'

        def _row(cells, tag, *, head=False):
            bg_of = (lambda i: "") if colors is None else (
                (lambda i: colors[0]) if head else (lambda i: colors[1] if i == 0 else "")
            )
            return "<tr>" + "".join(
                _cell(c, tag, bg=bg_of(i), col=i) for i, c in enumerate(cells)
            ) + "</tr>"

        head = f"<thead>{_row(b.rows[0], 'th', head=True)}</thead>" if b.header and b.rows else ""
        body = b.rows[1:] if b.header else b.rows
        rows = "".join(_row(r, "td") for r in body)
        table_style = "" if colors is None else ' style="border-collapse:collapse"'
        return f"<table{table_style}>{head}<tbody>{rows}</tbody></table>"
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


# 구분선 모양. 비워두면 <hr> 을 붙여넣어 긴 가는 선(line1)이 된다.
# 값을 주면 툴바에서 그 모양을 골라 넣는다(예: line2 = 가운데 짧은 굵은 선).
DIVIDER_STYLE = os.getenv("NAVER_DIVIDER_STYLE", "").strip()


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
    if b.type == "divider" and DIVIDER_STYLE:
        return False
    if b.type == "heading":
        # 링크 없는 제목은 붙여넣는다. 붙여넣은 제목의 굵게·크기가 다음 줄로 번지는
        # 문제는 write_post 가 세그먼트마다 서식을 초기화해서 막는다(2026-09-16 실측).
        return not _has_link(b)
    if b.type != "paragraph":
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
        elif is_paste_safe(b) and b.type == "heading":
            # 제목은 단독으로 붙여넣는다. 같은 클립보드에 뒤 문단을 실으면
            # 초기화할 틈 없이 제목 서식을 물려받을 수 있다.
            flush_manual()
            flush_html()
            buf.append(block_html(b))
            src.append(b)
            flush_html()
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
