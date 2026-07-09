from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import aiohttp


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com/"
ORIGIN = "https://www.bilibili.com"
BILI_HOME_URL = "https://www.bilibili.com"
BUVID_URL = "https://api.bilibili.com/x/frontend/finger/spi"


def browser_headers(
    cookies: dict[str, str] | None = None,
    *,
    referer: str = REFERER,
    origin: str = ORIGIN,
) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer or REFERER,
        "Origin": origin or ORIGIN,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    text = cookie_header(cookies)
    if text:
        headers["Cookie"] = text
    return headers


def cookie_header(cookies: dict[str, str] | None = None) -> str:
    return "; ".join(f"{key}={value}" for key, value in dict(cookies or {}).items() if key and value)


def parse_login_url_cookies(url: str) -> dict[str, str]:
    if "?" not in url:
        return {}
    result: dict[str, str] = {}
    for part in url.split("?", 1)[1].split("&"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key:
            result[key] = unquote(value)
    return result


async def enrich_bili_cookies(
    cookies: dict[str, str] | None,
    *,
    timeout_seconds: int = 15,
) -> dict[str, str]:
    values = {str(key): str(value) for key, value in dict(cookies or {}).items() if key and value}
    timeout = aiohttp.ClientTimeout(total=max(5, int(timeout_seconds or 15)))
    async with aiohttp.ClientSession(timeout=timeout, headers=browser_headers(values)) as session:
        await _touch_home(session, values)
        await _touch_buvid(session, values)
    return values


async def _touch_home(session: aiohttp.ClientSession, cookies: dict[str, str]) -> None:
    try:
        async with session.get(BILI_HOME_URL, headers=browser_headers(cookies)) as response:
            remember_response_cookies(cookies, response)
    except Exception:
        return


async def _touch_buvid(session: aiohttp.ClientSession, cookies: dict[str, str]) -> None:
    try:
        async with session.get(BUVID_URL, headers=browser_headers(cookies)) as response:
            remember_response_cookies(cookies, response)
            if int(response.status or 0) != 200:
                return
            payload = await response.json(content_type=None)
    except Exception:
        return
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return
    if data.get("b_3"):
        cookies.setdefault("buvid3", str(data.get("b_3")))
    if data.get("b_4"):
        cookies["buvid4"] = str(data.get("b_4"))


def remember_response_cookies(cookies: dict[str, str], response: Any) -> None:
    for item in getattr(response, "cookies", {}).values():
        if item.key and item.value:
            cookies[item.key] = item.value
