from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Protocol

from astrbot.api import logger


async def _upload_io(function, *args, **kwargs):
    """磁盘操作结束后才响应取消，保证关闭/删除不会与尚在写入的线程竞争。"""
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancellation = None
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError as exc:
            if worker.cancelled():
                raise
            cancellation = cancellation or exc
        except Exception:
            if cancellation is not None:
                raise cancellation
            raise
    if cancellation is not None:
        raise cancellation
    return result

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


class PortalBaseComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _PAGE_UPLOAD_CHUNK_SIZE: Any
    _build_page_config: Any
    _build_page_status: Any
    _page_bool: Any
    _page_copy_upload: Any
    _page_copy_upload_sync: Any
    _page_json: Any
    _page_json_body: Any
    _page_public_error: Any
    _page_query_args: Any
    _page_request_method: Any
    _page_response: Any
    runtime: Any


class PortalBaseComponent:
    def __init__(self, context: PortalBaseComponentContext) -> None:
        self.context = context

    _PAGE_UPLOAD_CHUNK_SIZE = 256 * 1024

    @staticmethod
    def _page_public_error(exc: Exception) -> str:
        if getattr(exc, "code", None) == 413:
            return "上传内容超过服务器大小限制，请缩小文件后重试"
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
            return await self.context._page_response({"ok": True, "data": payload})
        except Exception as exc:
            logger.exception(f"[日常生活] 面板接口处理失败：{exc}")
            return await self.context._page_response(
                {
                    "ok": False,
                    "error": {
                        "message": self.context._page_public_error(exc),
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
        request_limit = max_bytes + 1024 * 1024
        if content_length > request_limit:
            raise ValueError("上传文件超过大小限制")
        # 按请求覆盖 Quart 的默认限制，为 multipart 头部预留空间。
        _quart_request.max_content_length = request_limit
        files = await _quart_request.files
        upload = files.get("file") if files else None
        if upload is None:
            raise ValueError("没有收到上传文件")
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        size = await self.context._page_copy_upload(upload, target, max_bytes=max_bytes)
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
        size = 0
        try:
            if not inspect.iscoroutinefunction(read):
                return await _upload_io(
                    self.context._page_copy_upload_sync, read, target, max_bytes,
                )
            stack = ExitStack()
            try:
                # 栈持有句柄，即使取消恰好发生在 open 完成时也能安全关闭。
                handle = await _upload_io(lambda: stack.enter_context(target.open("wb")))
                while True:
                    chunk = await read(self.context._PAGE_UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("上传文件超过大小限制")
                    await _upload_io(handle.write, chunk)
            finally:
                await _upload_io(stack.close)
        # 上传取消也要删除未完成文件，随后保留原取消语义。
        except BaseException:
            await _upload_io(target.unlink, missing_ok=True)
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
                    chunk = read(self.context._PAGE_UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("上传文件超过大小限制")
                    handle.write(chunk)
        # 同步读取可能由工作线程取消，临时文件仍需回收。
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
            attachment_filename=filename,
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
        diagnostics = self.context._page_bool(
            self.context._page_query_args().get("diagnostics", False)
        )
        return await self.context._page_json(
            lambda: self.context._build_page_status(diagnostics=diagnostics)
        )

    async def page_status_wait(self):
        async def handler():
            args = self.context._page_query_args()
            diagnostics = self.context._page_bool(args.get("diagnostics", False))
            try:
                since = int(str(args.get("since") or "0"))
            except ValueError:
                since = 0
            try:
                timeout = float(str(args.get("timeout") or "25"))
            except ValueError:
                timeout = 25.0
            version = await self.context.runtime.wait_page_status_changed(
                since, timeout
            )
            # 运行时计数器仅在当前进程有效。插件重载后计数器可能减小，
            # 而已打开的页面仍传入原先较大的版本号，此时返回最新快照。
            if version < since:
                status = await self.context._build_page_status(diagnostics=diagnostics)
                status["changed"] = True
                return status
            if version <= since:
                return {"status_version": version, "changed": False}
            status = await self.context._build_page_status(diagnostics=diagnostics)
            status["changed"] = True
            return status

        return await self.context._page_json(handler)

    async def page_config(self):
        async def handler():
            if self.context._page_request_method() == "POST":
                body = await self.context._page_json_body()
                incoming = body.get("config", body)
                await self.context.runtime.apply_config(incoming)
                return await self.context._build_page_config(saved=True)
            return await self.context._build_page_config(saved=False)

        return await self.context._page_json(handler)
