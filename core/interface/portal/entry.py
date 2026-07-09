from __future__ import annotations

from typing import Awaitable, Callable

from astrbot.api import logger

try:
    from quart import jsonify as _quart_jsonify
    from quart import request as _quart_request
except Exception:
    _quart_jsonify = None
    _quart_request = None


class PortalBaseMixin:
    async def _page_response(self, payload: dict, status: int = 200):
        if _quart_jsonify is None:
            return payload
        response = _quart_jsonify(payload)
        response.status_code = status
        return response

    async def _page_json(self, callback: Callable[[], Awaitable[dict]]):
        try:
            payload = await callback()
            return await self._page_response({"ok": True, "data": payload})
        except Exception as exc:
            logger.exception(f"[日常生活] 面板接口处理失败：{exc}")
            return await self._page_response(
                {"ok": False, "error": {"message": str(exc) or "请求失败"}},
                200,
            )

    async def _page_json_body(self) -> dict:
        if _quart_request is None:
            return {}
        try:
            data = await _quart_request.get_json(silent=True)
        except TypeError:
            data = await _quart_request.get_json()
        return data if isinstance(data, dict) else {}

    def _page_request_method(self) -> str:
        if _quart_request is None:
            return "GET"
        return str(getattr(_quart_request, "method", "GET") or "GET").upper()

    @staticmethod
    def _page_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "enable", "enabled", "启用", "开启", "是"}

    def _page_query_args(self) -> dict:
        if _quart_request is None:
            return {}
        args = getattr(_quart_request, "args", None)
        return dict(args) if args else {}

    async def page_status(self):
        return await self._page_json(self._build_page_status)

    async def page_status_wait(self):
        async def handler():
            args = self._page_query_args()
            try:
                since = int(str(args.get("since") or "0"))
            except ValueError:
                since = 0
            try:
                timeout = float(str(args.get("timeout") or "25"))
            except ValueError:
                timeout = 25.0
            version = await self.runtime.wait_page_status_changed(since, timeout)
            if version <= since:
                return {"status_version": version, "changed": False}
            status = await self._build_page_status()
            status["changed"] = True
            return status

        return await self._page_json(handler)

    async def page_config(self):
        async def handler():
            if self._page_request_method() == "POST":
                body = await self._page_json_body()
                await self.runtime.apply_config(body.get("config", body))
                return await self._build_page_config(saved=True)
            return await self._build_page_config(saved=False)

        return await self._page_json(handler)
