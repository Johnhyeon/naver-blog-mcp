"""글감(뉴스, 증권, 책) 카드 고르기. 브라우저 없이 도는 부분만 담는다.

마크다운 디렉티브
    :::news 매체 | 기사 제목:::     뉴스 카드. 매체와 제목이 둘 다 맞는 결과만 넣는다
    :::stock 072950:::             증권 카드. 종목코드나 종목명
    :::book 책 제목:::              책 카드

끝에 `| 요약형` 이나 `| 기본형` 을 붙이면 카드 모양을 고른다. 없으면 요약형.
    기본형(material_basic): 썸네일이 붙은 어두운 카드. 긴 제목은 잘린다
    요약형(material_small): 아이콘 + 제목 전체 + 매체 한 줄

글감 검색은 결과가 많고 비슷한 제목이 섞여 나온다(같은 사건을 여러 매체가 쓴다).
엉뚱한 기사를 넣는 것보다 안 넣는 게 낫다. 기준에 못 미치면 None 을 돌려주고,
호출부는 원래 글자를 그대로 적는다.

증권 카드는 넣는 순간의 시세를 그림으로 굳힌다. 애프터마켓(16~20시)에 넣으면
정규장 종가와 다른 숫자가 찍힌다(2026-09-16 실측: 빛샘전자 정규장 +26.0%, 18:50 카드 +28.88%).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

CATEGORY = {"news": "뉴스", "stock": "증권", "book": "책"}
LAYOUT = {"요약형": "material_small", "기본형": "material_basic"}
DEFAULT_LAYOUT = "material_small"
TITLE_MIN = 0.72


def _norm(s: str) -> str:
    """비교용. 한글, 영문, 숫자만 남기고 소문자로."""
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def split_layout(arg: str) -> tuple[str, str]:
    """끝의 '| 요약형' 또는 '| 기본형' 을 떼어낸다. (나머지, 레이아웃 값)."""
    head, _, last = arg.rpartition("|")
    if head and last.strip() in LAYOUT:
        return head.strip(), LAYOUT[last.strip()]
    return arg.strip(), DEFAULT_LAYOUT


def parse_arg(kind: str, arg: str) -> tuple[str, str, str]:
    """(검색어, 원하는 제목, 원하는 출처). news 는 '매체 | 제목'."""
    arg, _ = split_layout(arg)
    if kind == "news":
        if "|" not in arg:
            raise ValueError(f"뉴스는 ':::news 매체 | 제목:::' 형식이어야 합니다: {arg!r}")
        source, title = (x.strip() for x in arg.split("|", 1))
        return title, title, source
    return arg.strip(), arg.strip(), ""


def queries(kind: str, arg: str) -> list[str]:
    """검색어 후보. 긴 기사 제목은 특수문자 때문에 결과가 비기도 해서 앞 낱말로 한 번 더 찾는다."""
    query = parse_arg(kind, arg)[0]
    out = [query]
    words = [w for w in re.split(r"[^0-9A-Za-z가-힣]+", query) if w]
    if kind != "stock" and len(words) > 4:
        out.append(" ".join(words[:4]))
    return out


def title_score(want: str, got: str) -> float:
    a, b = _norm(want), _norm(got)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # 에디터 목록은 긴 제목을 자르지 않지만, 매체가 제목 앞뒤를 조금 바꿔 싣는 경우가 있다
    if len(a) >= 8 and (a in b or b in a):
        return 0.95
    return SequenceMatcher(None, a, b).ratio()


def best_match(kind: str, arg: str, items: list[tuple[str, str]]) -> int | None:
    """items = [(제목, 설명)]. 설명은 뉴스면 매체, 증권이면 '코드 시장'.

    뉴스: 매체가 같고 제목 유사도가 TITLE_MIN 이상인 것 중 가장 높은 것
    증권: 코드가 설명에 있거나 종목명이 정확히 같은 것
    책:   제목 유사도가 가장 높은 것(TITLE_MIN 이상)
    """
    _, want_title, want_source = parse_arg(kind, arg)
    if kind == "stock":
        key = _norm(want_title)
        for i, (title, desc) in enumerate(items):
            if key.isdigit() and key in _norm(desc):
                return i
            if _norm(title) == key:
                return i
        return None

    best, best_score = None, TITLE_MIN
    for i, (title, desc) in enumerate(items):
        if kind == "news" and _norm(want_source) != _norm(desc):
            continue
        score = title_score(want_title, title)
        if score >= best_score:
            best, best_score = i, score
    return best


def fallback_text(kind: str, arg: str) -> str:
    """카드를 못 넣었을 때 대신 적을 글자."""
    _, title, source = parse_arg(kind, arg)
    return f"{source}, {title}" if kind == "news" else title
