"""표지(대표 이미지) 고르기 테스트.

핵심은 두 가지다. 표지를 찾아내는 것, 그리고 본문이 이미 쓰고 있으면 두 번 넣지 않는 것.
같은 그림이 본문에 두 번 들어가면 사람이 임시저장함에서 손으로 지워야 한다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from naver_blog_mcp.server import find_cover  # noqa: E402


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.delenv("NAVER_COVER", raising=False)
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "00-cover.png").write_bytes(b"x")
    (tmp_path / "images" / "01-first.png").write_bytes(b"x")
    return tmp_path


def test_finds_cover_by_convention(folder):
    assert find_cover(folder) == folder / "images" / "00-cover.png"


def test_none_when_no_cover(tmp_path, monkeypatch):
    monkeypatch.delenv("NAVER_COVER", raising=False)
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "01-first.png").write_bytes(b"x")
    assert find_cover(tmp_path) is None


def test_skips_when_body_already_uses_it(folder):
    # 본문이 이미 표지를 쓰고 있으면 앞에 또 넣지 않는다
    body = "첫 줄\n\n![표지](images/00-cover.png)\n"
    assert find_cover(folder, body) is None


def test_other_images_in_body_do_not_block(folder):
    body = "첫 줄\n\n![질문 1](images/01-first.png)\n"
    assert find_cover(folder, body) == folder / "images" / "00-cover.png"


def test_env_can_turn_it_off(folder, monkeypatch):
    monkeypatch.setenv("NAVER_COVER", "0")
    assert find_cover(folder) is None


def test_env_can_point_elsewhere(folder, monkeypatch):
    other = folder / "images" / "01-first.png"
    monkeypatch.setenv("NAVER_COVER", "images/01-first.png")
    assert find_cover(folder) == other


def test_env_path_that_is_missing_is_ignored(folder, monkeypatch):
    monkeypatch.setenv("NAVER_COVER", "images/99-nope.png")
    assert find_cover(folder) is None


def test_jpg_cover_also_found(tmp_path, monkeypatch):
    monkeypatch.delenv("NAVER_COVER", raising=False)
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "00-cover.jpg").write_bytes(b"x")
    assert find_cover(tmp_path) == tmp_path / "images" / "00-cover.jpg"
