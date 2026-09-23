"""사람이 직접 로그인하고 쿠키만 덤프한다.

CAPTCHA, 2차인증, 기기등록 전부 사람이 처리한다.
자동화가 로그인을 흉내내지 않으므로 봇 탐지에 걸릴 표면이 거의 없다.

**'로그인 상태 유지'를 켜고 로그인해야 한다.** 이 스크립트가 미리 켜 두지만,
사람이 다시 끄면 안 된다. 꺼진 채로 로그인하면 NID_AUT/NID_SES 가 만료 시각 없는
세션 쿠키로 내려오고, 서버 세션이 끊기는 몇 시간 뒤에 죽는다. 그러면 무인으로 도는
발행 워커와 통계 수집이 아침마다 "세션 만료"로 멈춘다.

2026-09-24 실측: 19:18 에 저장한 쿠키가 01:20 에 이미 로그인 화면으로 튕겼다.
그때 NID_AUT, NID_SES 둘 다 만료 시각이 없었다(세션 쿠키).
저장한 뒤 만료가 붙었는지 여기서 확인해 주고, 안 붙었으면 경고한다.
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

# 서버와 같은 경로를 쓴다. 예전에는 여기만 하드코딩돼 있어서, NAVER_STATE 를 설정하면
# 로그인은 성공하는데 서버는 다른 곳을 보는 바람에 "세션 파일 없음" 이 떴다.
sys.path.insert(0, str(Path(__file__).parent / "src"))
from naver_blog_mcp.session import STATE as OUT, save_state_file


AUTH = {"NID_AUT", "NID_SES"}
# "로그인 상태 유지" 체크박스. 꺼진 채로 로그인하면 NID_AUT/NID_SES 가 만료 없는
# 세션 쿠키로 내려오고, 서버 세션이 끊기는 몇 시간 뒤에 죽는다(2026-09-24 실측:
# 19:18 에 저장한 쿠키가 01:20 에 이미 로그인 화면으로 튕겼다).
# 켜면 만료 시각이 붙어 몇 달 간다. 사람이 잊지 않게 미리 켜 둔다.
STAY = "#loginStay"


async def turn_on_stay(page) -> bool:
    """'로그인 상태 유지'를 켠다. 이미 켜져 있으면 그대로 둔다."""
    try:
        await page.wait_for_selector(STAY, timeout=10000)
        if await page.is_checked(STAY):
            return True
        await page.click("label[for='loginStay']", timeout=5000)
        return await page.is_checked(STAY)
    except Exception as e:
        print(f"'로그인 상태 유지'를 자동으로 켜지 못했습니다({type(e).__name__}).")
        print("로그인 화면에서 직접 체크해 주세요. 안 켜면 몇 시간 만에 다시 만료됩니다.")
        return False


def report_expiry(path) -> None:
    """저장된 인증 쿠키에 만료가 붙었는지 본다. 값은 찍지 않는다."""
    import datetime as dt
    import json as _json

    try:
        cookies = _json.loads(path.read_text(encoding="utf-8"))["cookies"]
    except Exception:
        return
    session_only = []
    for c in cookies:
        if c.get("name") not in AUTH:
            continue
        exp = c.get("expires", -1)
        if exp and exp > 0:
            when = dt.datetime.fromtimestamp(exp)
            left = when - dt.datetime.now()
            print(f"  {c['name']}: {when:%Y-%m-%d %H:%M} 까지 ({left.days}일 남음)")
        else:
            session_only.append(c["name"])
    if session_only:
        print()
        print("  ** 경고: " + ", ".join(session_only) + " 에 만료 시각이 없습니다(세션 쿠키).")
        print("  ** '로그인 상태 유지'가 꺼진 채로 로그인한 것입니다. 몇 시간 뒤 다시 만료됩니다.")
        print("  ** 다시 실행해서, 로그인 화면의 '로그인 상태 유지'를 켜고 로그인해 주세요.")


async def wait_login(ctx, timeout: float = 300.0) -> bool:
    """인증 쿠키가 생길 때까지 기다린다. 엔터 입력을 쓰지 않는 이유는,
    터미널 없이(백그라운드로) 띄워도 로그인만 하면 끝나게 하려는 것이다.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        names = {c["name"] for c in await ctx.cookies()}
        if AUTH & names:
            await asyncio.sleep(1.5)  # 나머지 쿠키까지 실린 뒤에 덤프
            return True
        await asyncio.sleep(2)
    return False


async def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False, args=["--disable-blink-features=AutomationControlled"]
        )
        ctx = await browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul")
        page = await ctx.new_page()
        await page.goto("https://nid.naver.com/nidlogin.login")
        stay = await turn_on_stay(page)

        print("=" * 60)
        print("브라우저에서 직접 로그인하세요. 로그인되면 자동으로 저장하고 닫습니다.")
        print("5분 안에 로그인하지 않으면 아무것도 저장하지 않고 끝납니다.")
        if stay:
            print("'로그인 상태 유지'는 켜 뒀습니다. 끄지 마세요 — 끄면 몇 시간 만에 만료됩니다.")
        else:
            print("** '로그인 상태 유지'를 직접 켜 주세요. 안 켜면 몇 시간 만에 만료됩니다. **")
        print("=" * 60)
        if not await wait_login(ctx):
            print("로그인 확인 못 함 — 저장하지 않고 종료합니다.")
            await browser.close()
            return

        await ctx.storage_state(path=str(OUT))
        save_state_file(OUT)
        print(f"저장됨: {OUT.resolve()}")
        report_expiry(OUT)
        print("이 파일은 계정 접근권한 그 자체입니다. git 에 절대 올리지 마세요.")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
