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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
