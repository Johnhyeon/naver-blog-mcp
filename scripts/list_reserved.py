"""네이버에 예약 발행으로 걸려 있는 글을 찍는다. 읽기만 한다.

    uv run --directory D:/project/naver-blog-mcp python scripts/list_reserved.py
    uv run --directory D:/project/naver-blog-mcp python scripts/list_reserved.py --json

예약본은 블로그 글 목록 API 에 안 나온다. 발행 워커가 "이미 걸려 있나" 를 보려면
이 길밖에 없다(`tools/publish_queue.py` 의 `naver_reserved`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from naver_blog_mcp.editor import goto_editor, list_reserved  # noqa: E402
from naver_blog_mcp.session import Session  # noqa: E402


async def main(as_json: bool) -> int:
    async with Session(headless=True) as ctx:
        page = await ctx.new_page()
        frame = await goto_editor(page, os.environ["NAVER_BLOG_ID"])
        rows = await list_reserved(page, frame)
    if as_json:
        print(json.dumps([{"title": t, "when": w} for t, w in rows], ensure_ascii=False))
    else:
        print(f"예약 발행 {len(rows)}건")
        for t, w in rows:
            print(f"  {w}  {t}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="기계가 읽을 형태로")
    os.environ.setdefault("NAVER_BLOG_ID", "leetkey_lab")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main(ap.parse_args().json)))
