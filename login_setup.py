"""최초 1회. 사람이 직접 로그인하고 쿠키만 덤프한다.

CAPTCHA, 2차인증, 기기등록 전부 사람이 처리한다.
자동화가 로그인을 흉내내지 않으므로 봇 탐지에 걸릴 표면이 거의 없다.
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

# 서버와 같은 경로를 쓴다. 예전에는 여기만 하드코딩돼 있어서, NAVER_STATE 를 설정하면
# 로그인은 성공하는데 서버는 다른 곳을 보는 바람에 "세션 파일 없음" 이 떴다.
sys.path.insert(0, str(Path(__file__).parent / "src"))
from naver_blog_mcp.session import STATE as OUT, save_state_file


async def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False, args=["--disable-blink-features=AutomationControlled"]
        )
        ctx = await browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul")
        page = await ctx.new_page()
        await page.goto("https://nid.naver.com/nidlogin.login")

        print("=" * 60)
        print("브라우저에서 직접 로그인하세요.")
        print("로그인 후 본인 블로그 홈까지 이동한 다음, 여기서 엔터.")
        print("=" * 60)
        await asyncio.to_thread(input)

        await ctx.storage_state(path=str(OUT))
        save_state_file(OUT)
        print(f"저장됨: {OUT.resolve()}")
        print("이 파일은 계정 접근권한 그 자체입니다. git 에 절대 올리지 마세요.")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
