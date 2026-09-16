"""파서 회귀 테스트. 브라우저 없이 도는 것만 담는다.

무한 루프는 도구 전체를 조용히 멈춰 세운다(제목만 쓰고 정지). 그래서 진행 보장을
테스트로 못 박는다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from naver_blog_mcp.ir import parse_markdown  # noqa: E402


def test_hashtag_line_is_paragraph():
    blocks = parse_markdown("본문 한 줄.\n\n#광통신 #종목분석 #리트키랩\n")
    assert [b.type for b in blocks] == ["paragraph", "paragraph"]
    assert "".join(s.text for s in blocks[1].spans) == "#광통신 #종목분석 #리트키랩"


def test_heading_needs_space():
    blocks = parse_markdown("# 제목\n\n#제목아님\n")
    assert blocks[0].type == "heading"
    assert blocks[1].type == "paragraph"


def test_unknown_line_does_not_stall():
    blocks = parse_markdown("#태그\n#태그2\n본문\n")
    assert blocks  # 멈추지 않고 끝나면 통과


if __name__ == "__main__":  # noqa
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_lines_are_kept():
    blocks = parse_markdown("데이터 출처\n시세: StockLens\n공시: DartLens\n")
    assert [b.type for b in blocks] == ["paragraph"] * 3
    assert not any(b.gap for b in blocks)


def test_blank_line_becomes_gap():
    from naver_blog_mcp.ir import block_html
    blocks = parse_markdown("첫 문단\n\n둘째 문단\n")
    assert [b.gap for b in blocks] == [False, True]
    assert block_html(blocks[1]).startswith("<p><br></p>")


def test_no_gap_right_after_image():
    blocks = parse_markdown("![캡션](a.png)\n\n본문\n")
    assert blocks[1].gap is False


def test_heading_is_its_own_segment():
    from naver_blog_mcp.ir import segment
    segs = segment(parse_markdown("앞 문단\n\n## 소제목\n\n뒤 문단\n"))
    assert [s.kind for s in segs] == ["html", "html", "html"]
    assert "font-size:19px" in segs[1].html and "<b>" in segs[1].html


def test_underline_syntax():
    from naver_blog_mcp.ir import block_html
    b = parse_markdown("공시 목록부터 ++직접 열어본다++\n")[0]
    assert any(s.underline and s.text == "직접 열어본다" for s in b.spans)
    assert "<u>직접 열어본다</u>" in block_html(b)
