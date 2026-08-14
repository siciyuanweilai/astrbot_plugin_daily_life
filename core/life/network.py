from __future__ import annotations

from typing import Any

import aiohttp

from astrbot.api import logger


async def request_map_json(
    url: str,
    params: dict[str, Any],
    *,
    timeout_seconds: int,
    provider_label: str,
    endpoint_label: str = "",
) -> dict[str, Any]:
    """发送地图 Web 服务请求并返回字典响应。"""

    try:
        timeout = aiohttp.ClientTimeout(total=max(1, int(timeout_seconds)))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        endpoint = f"；接口={endpoint_label}" if endpoint_label else ""
        logger.debug(
            f"[日常生活] {provider_label}请求失败{endpoint}；异常={type(exc).__name__}"
        )
        return {}
    return payload if isinstance(payload, dict) else {}


__all__ = ["request_map_json"]
