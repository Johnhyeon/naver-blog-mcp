"""세션 관리. 비밀번호는 어디에도 저장하지 않는다.

login_setup.py 로 사람이 직접 로그인 -> storage_state.json 에 쿠키만 덤프.
서버는 그 파일만 로드한다. 만료되면 login_setup.py 를 다시 돌린다.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Playwright

STATE = Path(os.getenv("NAVER_STATE", "playwright-state/storage_state.json"))

# 로그인 상태를 나타내는 쿠키. 하나라도 있어야 "로그인됨" 으로 본다.
AUTH_COOKIES = ("NID_AUT", "NID_SES")


def save_state_file(path: Path) -> None:
    """쿠키 파일 권한을 0600 으로 조인다.

    이 파일은 계정 접근권한 그 자체다. 기본 umask 로 두면 0644 라서
    같은 기기의 다른 사용자·프로세스가 그대로 읽고 계정 행세를 할 수 있다.
    """
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _has_auth_cookies(path: Path) -> bool:
    try:
        names = {c.get("name") for c in json.loads(path.read_text(encoding="utf-8"))["cookies"]}
    except Exception:
        return False
    return any(n in names for n in AUTH_COOKIES)


async def snapshot(ctx: BrowserContext) -> None:
    """지금 쿠키를 즉시 파일에 남긴다.

    네이버는 접속할 때마다 세션 쿠키를 갱신하고 옛 값을 버린다. 긴 작업 도중
    프로세스가 죽으면 갱신분이 저장되지 않아, 다음 실행이 이미 죽은 쿠키를 들고
    로그인 화면으로 튕긴다 (2026-09-16 실측: 멈춘 실행을 강제 종료한 뒤 로그아웃됨).
    그래서 글쓰기처럼 오래 걸리는 작업 앞에서 한 번 저장해 둔다.
    """
    try:
        tmp = STATE.with_suffix(STATE.suffix + ".tmp")
        await ctx.storage_state(path=str(tmp))
        if _has_auth_cookies(tmp):
            os.replace(tmp, STATE)
            save_state_file(STATE)
        else:
            tmp.unlink(missing_ok=True)
    except Exception:
        pass


def keep_open_seconds() -> float:
    """NAVER_KEEP_OPEN 을 초로 바꾼다. 빈 값이면 0(끝나면 바로 닫음).

    사람이 결과를 눈으로 확인하려면 창이 남아 있어야 한다. 1 이나 true 면 10분,
    숫자를 주면 그 초만큼 기다린다. 창을 먼저 닫으면 그 즉시 끝난다.
    """
    v = os.getenv("NAVER_KEEP_OPEN", "").strip().lower()
    if v in ("", "0", "false", "no"):
        return 0.0
    if v in ("1", "true", "yes"):
        return 600.0
    try:
        return max(0.0, float(v))
    except ValueError:
        return 600.0


class Session:
    def __init__(self, headless: bool | None = None):
        self.headless = headless if headless is not None else os.getenv("HEADLESS", "false") == "true"
        self._pw: Playwright | None = None
        self._browser = None
        self.ctx: BrowserContext | None = None

    async def __aenter__(self) -> BrowserContext:
        if not STATE.exists():
            raise RuntimeError(
                f"세션 파일 없음: {STATE}\n"
                "python login_setup.py 를 먼저 실행해서 직접 로그인하세요."
            )
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self.ctx = await self._browser.new_context(
                storage_state=str(STATE),
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                viewport={"width": 1440, "height": 960},
            )
            # 클립보드 붙여넣기 경로에 필수
            await self.ctx.grant_permissions(
                ["clipboard-read", "clipboard-write"], origin="https://blog.naver.com"
            )
        except BaseException:
            # 여기서 정리하지 않으면 __aexit__ 이 불리지 않아 크로미움과 드라이버가
            # 통째로 샌다. 서버가 오래 살면 좀비 브라우저가 쌓인다.
            await self._cleanup()
            raise
        return self.ctx

    async def __aexit__(self, *exc):
        if self.ctx:
            # 세션 갱신분 저장 (만료 연장).
            # 임시 파일에 쓰고 os.replace 로 바꾼다 — 바로 덮어쓰면 쓰는 도중에
            # 죽거나 다른 호출과 겹칠 때 반쪽짜리 JSON 이 남고, 그러면
            # "세션 파일 없음" 안내도 못 받고 파싱 에러만 난다.
            try:
                tmp = STATE.with_suffix(STATE.suffix + ".tmp")
                await self.ctx.storage_state(path=str(tmp))
                if _has_auth_cookies(tmp):
                    os.replace(tmp, STATE)
                    save_state_file(STATE)
                else:
                    # 로그인 페이지로 튕긴 실행이 로그아웃 상태를 덮어쓰면
                    # 멀쩡하던 쿠키까지 날아간다. 일시적 차단이었을 수도 있으므로
                    # 인증 쿠키가 없으면 기존 파일을 건드리지 않는다.
                    tmp.unlink(missing_ok=True)
            except Exception:
                pass
            await self._hold_window()
            try:
                await self.ctx.close()
            except Exception:
                pass
        await self._cleanup()

    async def _hold_window(self) -> None:
        """NAVER_KEEP_OPEN 이 켜져 있으면 사람이 창을 닫을 때까지 기다린다.

        쿠키를 저장한 뒤에 기다린다. 사람이 창을 먼저 닫아도 세션은 이미 남아 있다.
        """
        wait = keep_open_seconds()
        if not wait or self.ctx is None:
            return
        print(f"창을 열어뒀습니다. 확인이 끝나면 창을 닫으세요 (최대 {wait / 60:.0f}분)", flush=True)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + wait
        while loop.time() < deadline:
            if not self.ctx.pages or not (self._browser and self._browser.is_connected()):
                return
            await asyncio.sleep(1.0)

    async def _cleanup(self) -> None:
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
