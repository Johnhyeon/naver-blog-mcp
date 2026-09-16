"""글감 카드 고르기 테스트. 엉뚱한 카드를 넣지 않는 것이 핵심이다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from naver_blog_mcp.ir import parse_markdown  # noqa: E402
from naver_blog_mcp.material import best_match, fallback_text, queries, split_layout  # noqa: E402

NEWS = [
    ("\"통신장비 부족해\"…대한광통신 14% 쑥", "매일경제"),
    ("한국첨단소재, 양자통신·차세대 광통신 사업 확대…글로벌 공략 본격화", "아시아경제"),
    ("한국첨단소재, 전남·광주 양자통신 클러스터 참여…광통신 사업 확대", "뉴시스"),
]


def test_news_needs_source_and_title():
    assert best_match("news", "아시아경제 | 한국첨단소재, 양자통신·차세대 광통신 사업 확대…글로벌 공략 본격화", NEWS) == 1
    # 제목이 비슷해도 매체가 다르면 넣지 않는다
    assert best_match("news", "머니투데이 | 한국첨단소재, 양자통신·차세대 광통신 사업 확대", NEWS) is None
    # 매체가 같아도 다른 기사면 넣지 않는다
    assert best_match("news", "매일경제 | 광통신株 줄줄이 급등", NEWS) is None


def test_stock_by_code_or_name():
    rows = [("빛샘전자", "072950 코스닥"), ("빛샘전자우", "07295K 코스닥")]
    assert best_match("stock", "072950", rows) == 0
    assert best_match("stock", "빛샘전자", rows) == 0
    assert best_match("stock", "005930", rows) is None


def test_layout_suffix():
    assert split_layout("매일경제 | 제목 | 기본형") == ("매일경제 | 제목", "material_basic")
    assert split_layout("매일경제 | 제목")[1] == "material_small"
    assert best_match("news", "뉴시스 | 한국첨단소재, 전남·광주 양자통신 클러스터 참여…광통신 사업 확대 | 기본형", NEWS) == 2


def test_queries_and_fallback():
    q = queries("news", "한국경제 | 美 AI 인프라 투자 기대에 국내 광통신株 줄줄이 급등")
    assert q[0].startswith("美 AI") and len(q) == 2
    assert fallback_text("news", "한국경제, | 제목") == "한국경제,, 제목"
    assert fallback_text("news", "한국경제 | 제목 | 요약형") == "한국경제, 제목"


def test_directive_parses_and_no_gap_after_card():
    blocks = parse_markdown(":::news 매일경제 | 제목:::\n\n다음 줄\n")
    assert [b.type for b in blocks] == ["news", "paragraph"]
    assert blocks[0].raw == "매일경제 | 제목"
    assert not blocks[1].gap
