"""예약 발행으로 걸어 둔 글을 지운다. 되돌릴 수 없다.

    uv run --directory D:/project/naver-blog-mcp python scripts/delete_reserved.py "<제목>"
    uv run --directory D:/project/naver-blog-mcp python scripts/delete_reserved.py --list

**예약을 걸고 나면 그 글은 임시저장함에 없다.** 그래서 글을 고쳐 다시 올리려면
예약본을 먼저 지워야 한다. 2026-09-29 에 표지를 다시 뽑았는데 네이버에 걸린
예약본은 옛 표지 그대로였고, 지울 길이 도구에 없어서 그때 스크립트를 새로 썼다.

제목은 네이버에 올라간 그대로 준다(말머리 `[와인스타인]` 까지). 여러 건과 맞으면 거부한다.
지운 뒤에는 `draft_folder.py <글 폴더> --reserve "<시각>"` 로 다시 올린다.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from naver_blog_mcp.editor import delete_reserved, goto_editor, list_reserved  # noqa: E402
from naver_blog_mcp.session import Session  # noqa: E402


async def main(a) -> int:
    async with Session(headless=True) as ctx:
        page = await ctx.new_page()
        frame = await goto_editor(page, os.environ["NAVER_BLOG_ID"])
        if a.list or not a.title:
            rows = await list_reserved(page, frame)
            print(f"예약 발행 {len(rows)}건")
            for t, w in rows:
                print(f"  {w}  {t}")
            if not a.title:
                print("\n지우려면 제목을 그대로 준다")
                return 1
            return 0
        title, when = await delete_reserved(page, frame, a.title)
    print(f"지웠다: {when}  {title}")
    print("다시 올리려면 draft_folder.py <글 폴더> --reserve \"<시각>\"")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("title", nargs="?", help="네이버에 올라간 제목 그대로")
    ap.add_argument("--list", action="store_true", help="예약 목록만 찍는다")
    os.environ.setdefault("NAVER_BLOG_ID", "leetkey_lab")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main(ap.parse_args())))
