from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Awaitable, Callable

from astrbot.api import logger

try:
    from quart import has_app_context as _quart_has_app_context
    from quart import has_request_context as _quart_has_request_context
    from quart import jsonify as _quart_jsonify
    from quart import request as _quart_request
    from quart import send_file as _quart_send_file
except Exception:
    _quart_has_app_context = None
    _quart_has_request_context = None
    _quart_jsonify = None
    _quart_request = None
    _quart_send_file = None


class PortalBaseMixin:
    _PAGE_UPLOAD_CHUNK_SIZE = 256 * 1024

    @staticmethod
    def _page_public_error(exc: Exception) -> str:
        message = " ".join(str(exc).split())
        if isinstance(exc, (ValueError, RuntimeError)) and any(
            "\u4e00" <= char <= "\u9fff" for char in message
        ):
            return message
        return "操作失败，请查看后台日志"

    async def _page_response(self, payload: dict, status: int = 200):
        if (
            _quart_jsonify is None
            or _quart_has_app_context is None
            or not _quart_has_app_context()
        ):
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
                {
                    "ok": False,
                    "error": {
                        "message": self._page_public_error(exc),
                        "public": True,
                    },
                },
                200,
            )

    async def _page_json_body(self) -> dict:
        if (
            _quart_request is None
            or _quart_has_request_context is None
            or not _quart_has_request_context()
        ):
            return {}
        try:
            data = await _quart_request.get_json(silent=True)
        except TypeError:
            data = await _quart_request.get_json()
        return data if isinstance(data, dict) else {}

    async def _page_receive_upload(
        self,
        target: Path,
        *,
        max_bytes: int,
    ) -> dict:
        if (
            _quart_request is None
            or _quart_has_request_context is None
            or not _quart_has_request_context()
        ):
            raise ValueError("没有收到上传文件")
        content_length = int(getattr(_quart_request, "content_length", 0) or 0)
        if content_length > max_bytes + 1024 * 1024:
            raise ValueError("上传文件超过大小限制")
        files = await _quart_request.files
        upload = files.get("file") if files else None
        if upload is None:
            raise ValueError("没有收到上传文件")
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        size = await self._page_copy_upload(upload, target, max_bytes=max_bytes)
        return {
            "path": target,
            "filename": str(getattr(upload, "filename", "") or target.name),
            "mime": str(
                getattr(upload, "content_type", "") or "application/octet-stream"
            ),
            "size": size,
        }

    async def _page_copy_upload(self, upload, target: Path, *, max_bytes: int) -> int:
        read = getattr(upload, "read", None)
        if not callable(read):
            raise ValueError("上传文件不可读")
        if not inspect.iscoroutinefunction(read):
            return await asyncio.to_thread(
                self._page_copy_upload_sync,
                read,
                target,
                max_bytes,
            )
        size = 0
        try:
            with target.open("wb") as handle:
                while True:
                    chunk = await read(self._PAGE_UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("上传文件超过大小限制")
                    await asyncio.to_thread(handle.write, chunk)
        except BaseException:
            await asyncio.to_thread(target.unlink, missing_ok=True)
            raise
        return size

    def _page_copy_upload_sync(
        self,
        read: Callable[[int], bytes],
        target: Path,
        max_bytes: int,
    ) -> int:
        size = 0
        try:
            with target.open("wb") as handle:
                while True:
                    chunk = read(self._PAGE_UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("上传文件超过大小限制")
                    handle.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return size

    async def _page_send_download(
        self,
        path: Path,
        *,
        mime: str,
        filename: str,
    ):
        if _quart_send_file is None:
            raise RuntimeError("当前面板运行环境不支持文件下载")
        return await _quart_send_file(
            path,
            mimetype=mime,
            as_attachment=True,
            download_name=filename,
        )

    def _page_request_method(self) -> str:
        if (
            _quart_request is None
            or _quart_has_request_context is None
            or not _quart_has_request_context()
        ):
            return "GET"
        return str(getattr(_quart_request, "method", "GET") or "GET").upper()

    @staticmethod
    def _page_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "enable",
            "enabled",
            "启用",
            "开启",
            "是",
        }

    def _page_query_args(self) -> dict:
        if (
            _quart_request is None
            or _quart_has_request_context is None
            or not _quart_has_request_context()
        ):
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
                incoming = body.get("config", body)
                await self.runtime.apply_config(incoming)
                return await self._build_page_config(saved=True)
            return await self._build_page_config(saved=False)

        return await self._page_json(handler)
