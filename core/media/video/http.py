from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .errors import VideoAPIError, VideoRequestTimeout


def timeout_from_seconds(seconds: float | int | None) -> aiohttp.ClientTimeout | None:
    if seconds is None:
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return aiohttp.ClientTimeout(total=value)


def seconds_label(seconds: float | int | None) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "默认"
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


async def request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    json_body: dict[str, Any] | None = None,
    data: Any = None,
    timeout_seconds: float | int | None = None,
    operation: str = "请求",
) -> Any:
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            json=json_body,
            data=data,
            timeout=timeout_from_seconds(timeout_seconds),
        ) as response:
            if response.status >= 400:
                detail = await response.text()
                message = f"Grok 视频请求失败（HTTP {response.status}）：{detail[:300]}"
                raise VideoAPIError(message, response.status, detail)
            try:
                return await response.json(content_type=None)
            except TypeError:
                return await response.json()
            except Exception as exc:
                body = await response.text()
                raise RuntimeError(f"Grok 视频响应解析失败：{exc}；{body[:200]}") from exc
    except asyncio.TimeoutError as exc:
        raise VideoRequestTimeout(
            f"Grok 视频{operation}超时（{seconds_label(timeout_seconds)} 秒）：{url}"
        ) from exc
