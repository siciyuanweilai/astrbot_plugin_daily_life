from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum

import aiohttp

from .auth import browser_headers, enrich_bili_cookies, parse_login_url_cookies, remember_response_cookies


PASSPORT_BASE = "https://passport.bilibili.com"
QR_GENERATE = f"{PASSPORT_BASE}/x/passport-login/web/qrcode/generate"
QR_POLL = f"{PASSPORT_BASE}/x/passport-login/web/qrcode/poll"


class BiliLoginStatus(str, Enum):
    SUCCESS = "success"
    EXPIRED = "expired"
    SCANNED = "scanned"
    WAITING = "waiting"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(slots=True, frozen=True)
class BiliQRCode:
    url: str
    key: str


@dataclass(slots=True, frozen=True)
class BiliLoginResult:
    status: BiliLoginStatus
    cookies: dict[str, str] | None = None


class BiliLoginService:
    def __init__(self, timeout_seconds: int = 15):
        self.timeout = aiohttp.ClientTimeout(total=max(5, int(timeout_seconds or 15)))
        self._cookies: dict[str, str] = {}

    async def generate(self) -> BiliQRCode | None:
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                await self._prepare_browser_cookies(session)
                async with session.get(QR_GENERATE, headers=self._headers()) as response:
                    if int(response.status or 0) != 200:
                        return None
                    remember_response_cookies(self._cookies, response)
                    payload = await response.json(content_type=None)
        except Exception:
            return None
        data = payload.get("data") if isinstance(payload, dict) else {}
        url = str((data or {}).get("url") or "")
        key = str((data or {}).get("qrcode_key") or "")
        return BiliQRCode(url=url, key=key) if url and key else None

    async def poll(self, key: str) -> BiliLoginResult:
        try:
            async with aiohttp.ClientSession(timeout=self.timeout, headers=self._headers()) as session:
                async with session.get(QR_POLL, params={"qrcode_key": key}, headers=self._headers()) as response:
                    if int(response.status or 0) != 200:
                        return BiliLoginResult(BiliLoginStatus.ERROR)
                    payload = await response.json(content_type=None)
                    remember_response_cookies(self._cookies, response)
                    cookies = self.cookies()
        except Exception:
            return BiliLoginResult(BiliLoginStatus.ERROR)
        data = payload.get("data") if isinstance(payload, dict) else {}
        code = (data or {}).get("code")
        if code == 0:
            cookies.update(parse_login_url_cookies(str((data or {}).get("url") or "")))
            if cookies:
                self._cookies.update(cookies)
            async with aiohttp.ClientSession(timeout=self.timeout, headers=self._headers()) as session:
                await self._prepare_browser_cookies(session)
            return (
                BiliLoginResult(BiliLoginStatus.SUCCESS, self.cookies())
                if self._cookies.get("SESSDATA")
                else BiliLoginResult(BiliLoginStatus.ERROR)
            )
        if code == 86090:
            return BiliLoginResult(BiliLoginStatus.SCANNED)
        if code == 86101:
            return BiliLoginResult(BiliLoginStatus.WAITING)
        if code == 86038:
            return BiliLoginResult(BiliLoginStatus.EXPIRED)
        return BiliLoginResult(BiliLoginStatus.ERROR)

    def cookies(self) -> dict[str, str]:
        return dict(self._cookies)

    def _headers(self) -> dict[str, str]:
        return browser_headers(self._cookies)

    async def _prepare_browser_cookies(self, session: aiohttp.ClientSession) -> None:
        self._cookies = await enrich_bili_cookies(self._cookies, timeout_seconds=15)

    async def run_until_complete(self, key: str, *, total_timeout: float = 180) -> BiliLoginResult:
        elapsed = 0.0
        delay = 2.0
        while elapsed < total_timeout:
            result = await self.poll(key)
            if result.status in {BiliLoginStatus.SUCCESS, BiliLoginStatus.EXPIRED, BiliLoginStatus.ERROR}:
                return result
            sleep_for = delay + random.uniform(0, 0.5)
            await asyncio.sleep(sleep_for)
            elapsed += sleep_for
            delay = 1.5 if result.status == BiliLoginStatus.SCANNED else min(5.0, delay * 1.2)
        return BiliLoginResult(BiliLoginStatus.TIMEOUT)
