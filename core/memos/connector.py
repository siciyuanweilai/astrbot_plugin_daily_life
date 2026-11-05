from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from ..life.reliability import request_failure_summary


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
        self._session: aiohttp.ClientSession | None = None
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=4, ttl_dns_cache=300),
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        return self._session

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def post(
        self, endpoint: str, payload: dict[str, Any], *, timeout_seconds: float | None = None
    ) -> MemOSClientResult:
        if not self.available:
            return MemOSClientResult(False, error="MemOS 托管服务未配置")
        if self._closed:
            return MemOSClientResult(False, error="MemOS 客户端已关闭")
        endpoint = "/" + str(endpoint or "").lstrip("/")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Token {self.api_key}",
        }
        budget = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        started = time.monotonic()
        phase = "connect_or_response_headers"
        status = None

        def failure(exc: BaseException) -> MemOSClientResult:
            detail = request_failure_summary(
                exc, operation=endpoint, phase=phase,
                timeout_seconds=budget, elapsed_seconds=time.monotonic() - started,
            )
            if status is not None:
                detail += f"; HTTP={status}"
            return MemOSClientResult(False, error=detail)

        try:
            # 包含连接池排队、建连和读取响应；避免 aiohttp 对长超时取整。
            async with asyncio.timeout(budget):
                timeout = aiohttp.ClientTimeout(total=budget, connect=min(budget, 5.0))
                async with self._get_session().post(
                    f"{self.base_url}{endpoint}", headers=headers, json=payload,
                    timeout=timeout,
                ) as response:
                    status = response.status
                    if status < 200 or status >= 300:
                        return failure(RuntimeError("MemOS HTTP 请求失败"))
                    phase = "response_json"
                    result = await response.json()
        except Exception as exc:
            return failure(exc)

        phase = "response_validation"
        if not isinstance(result, dict):
            return failure(ValueError("MemOS 响应格式不是对象"))
        code = result.get("code")
        if code not in (None, 0, 200):
            failed = failure(ValueError("MemOS 业务响应失败"))
            if isinstance(code, int):
                failed.error += f"; API_code={code}"
            return failed
        data = result.get("data")
        if isinstance(data, dict) and data.get("success") is False:
            return failure(ValueError("MemOS 未受理请求"))
        return MemOSClientResult(True, data=result.get("data"), error="")

    async def search_memory(self, payload: dict[str, Any]) -> MemOSClientResult:
        return await self.post("/search/memory", payload)

    async def add_message(self, payload: dict[str, Any]) -> MemOSClientResult:
        # 异步写入只等待受理；超时可能已经被接收，不自动重放写操作。
        return await self.post(
            "/add/message", payload, timeout_seconds=min(self.timeout_seconds, 10.0)
        )

    async def add_feedback(self, payload: dict[str, Any]) -> MemOSClientResult:
        return await self.post("/add/feedback", payload)
