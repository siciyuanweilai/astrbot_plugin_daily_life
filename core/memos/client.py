from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass(slots=True)
class MemOSClientResult:
    success: bool
    data: Any = None
    error: str = ""


class HostedMemOSClient:
    """MemOS 托管服务 HTTP 客户端。"""

    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: float = 15.0):
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout_seconds = max(0.5, min(float(timeout_seconds or 15.0), 30.0))

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def post(self, endpoint: str, payload: dict[str, Any]) -> MemOSClientResult:
        if not self.available:
            return MemOSClientResult(False, error="MemOS 托管服务未配置")
        endpoint = "/" + str(endpoint or "").lstrip("/")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Token {self.api_key}",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.base_url}{endpoint}", headers=headers, json=payload) as response:
                    if response.status < 200 or response.status >= 300:
                        return MemOSClientResult(False, error=f"HTTP {response.status}: {await response.text()}")
                    result = await response.json()
        except Exception as exc:
            return MemOSClientResult(False, error=str(exc))

        if not isinstance(result, dict):
            return MemOSClientResult(False, error="MemOS 响应格式不是对象")
        code = result.get("code")
        if code not in (None, 0, 200):
            return MemOSClientResult(False, data=result.get("data"), error=str(result.get("message") or code))
        data = result.get("data")
        if isinstance(data, dict) and data.get("success") is False:
            return MemOSClientResult(False, data=data, error=str(data.get("message") or data.get("status") or "请求失败"))
        return MemOSClientResult(True, data=result.get("data"), error="")

    async def search_memory(self, payload: dict[str, Any]) -> MemOSClientResult:
        return await self.post("/search/memory", payload)

    async def add_message(self, payload: dict[str, Any]) -> MemOSClientResult:
        return await self.post("/add/message", payload)

    async def add_feedback(self, payload: dict[str, Any]) -> MemOSClientResult:
        return await self.post("/add/feedback", payload)
