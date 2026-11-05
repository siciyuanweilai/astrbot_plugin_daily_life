"""Inspect image candidates without confusing previews with animated results."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from uuid import uuid4

from PIL import Image

from astrbot.api import logger

from ...clock import now
from ..base import LOG_PREFIX, absolute_url, origin_from_url

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024


class ImageResultError(RuntimeError):
    """A terminal result failure; never submit another generation for this result."""


@dataclass
class ImageDiagnostics:
    model: str
    size: str
    output_format: str
    frame_count: int | None
    frame_duration_ms: int | None
    mode: str = "text"
    request_id: str = field(default_factory=lambda: f"daily-life-{uuid4().hex}")
    requested_at: str = field(
        default_factory=lambda: now().isoformat(timespec="milliseconds")
    )
    upstream_request_id: str = ""
    http_status: int | None = None
    upstream_error_code: str = ""
    upstream_error_type: str = ""
    upstream_error_param: str = ""
    submitted: bool = False
    secret: str = field(default="", repr=False)

    def emit(self, stage: str, **fields: Any) -> None:
        # Call sites supply only metadata, never URLs, payloads or exception messages.
        record = {
            "stage": stage,
            "requested_at": self.requested_at,
            "request_id": self.request_id,
            "upstream_request_id": self.upstream_request_id,
            "model": self.model,
            "mode": self.mode,
            "size": self.size,
            "output_format": self.output_format,
            "frame_count": self.frame_count,
            "frame_duration_ms": self.frame_duration_ms,
            "http_status": self.http_status,
            "upstream_error_code": self.upstream_error_code,
            "upstream_error_type": self.upstream_error_type,
            "upstream_error_param": self.upstream_error_param,
            **fields,
        }
        text = json.dumps(record, ensure_ascii=False)
        if self.secret:
            text = text.replace(self.secret, "[redacted]")
        logger.info(f"{LOG_PREFIX} 图片结果诊断：{text}")

    def response(self, status: int, headers: Any) -> None:
        self.http_status = int(status)
        value = str(headers.get("X-Request-ID") or headers.get("Request-ID") or "")
        # Do not let arbitrary response header text enter logs.
        if (
            value
            and len(value) <= 160
            and all(c.isascii() and (c.isalnum() or c in "-_.:") for c in value)
        ):
            self.upstream_request_id = value
        self.emit("response")

    async def read_error(self, response: Any, *, private_text: str = "") -> None:
        """Read bounded metadata; free-form messages may echo private input."""
        try:
            async with asyncio.timeout(5):
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    if len(body) + len(chunk) > MAX_ERROR_BYTES:
                        self.emit("error_response", body_state="too_large")
                        return
                    body.extend(chunk)
                payload = json.loads(body)
        except (ValueError, UnicodeError):
            self.emit("error_response", body_state="not_json")
            return
        except Exception as exc:
            self.emit(
                "error_response",
                body_state="unavailable",
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(payload, dict):
            self.emit("error_response", body_state="unstructured")
            return
        error = payload.get("error", payload)
        if not isinstance(error, dict):
            self.emit("error_response", body_state="unstructured")
            return

        def identifier(value: Any) -> str:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                return ""
            text = str(value).strip()
            if (
                not text
                or len(text) > 128
                or not all(
                    char.isascii() and (char.isalnum() or char in "-_.:[]")
                    for char in text
                )
            ):
                return ""
            if (self.secret and self.secret in text) or (
                private_text and (text in private_text or private_text in text)
            ):
                return ""
            return text

        self.upstream_error_code = identifier(error.get("code"))
        self.upstream_error_type = identifier(error.get("type"))
        self.upstream_error_param = identifier(error.get("param"))
        request_id = identifier(payload.get("request_id") or error.get("request_id"))
        if request_id:
            self.upstream_request_id = request_id
        self.emit("error_response", body_state="json")

    def http_error_text(self) -> str:
        detail = f"图片生成接口请求失败（HTTP {self.http_status}）"
        if self.upstream_error_code:
            detail += f"；上游错误码：{self.upstream_error_code}"
        if self.upstream_error_param:
            detail += f"；关联参数：{self.upstream_error_param}"
        return detail


@dataclass(frozen=True)
class ImageInfo:
    format: str
    frames: int
    width: int
    height: int

    def gif_error(self) -> str:
        if self.format == "GIF":
            return "" if self.frames > 1 else "上游返回 GIF，但只有一帧。"
        kind = "静态图" if self.frames == 1 else "动图"
        return f"上游返回 {self.format} {kind}，未得到要求的 GIF。"


def inspect_image(content: bytes) -> ImageInfo:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("图片内容为空或超过大小限制。")
    with Image.open(BytesIO(content)) as image:
        info = ImageInfo(
            str(image.format or "未知"), int(getattr(image, "n_frames", 1)), *image.size
        )
        # Load every GIF frame: a valid header and frame count alone cannot prove
        # that the last frame was downloaded completely.
        for index in range(info.frames if info.format == "GIF" else 1):
            image.seek(index)
            image.load()
        return info


def candidates(data: dict, origin: str) -> Iterator[tuple[str, str]]:
    items = data.get("data")
    if not isinstance(items, list):
        return
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("b64_json", "url"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            source = (
                "base64" if key == "b64_json" or value.startswith("data:") else "url"
            )
            if source == "url":
                value = absolute_url(value, origin)
            candidate = (source, value)
            if candidate not in seen:
                seen.add(candidate)
                yield candidate


async def select_animated_gif(
    data: dict,
    api_url: str,
    download: Callable[[str, Callable[..., None]], Awaitable[bytes]],
    diagnostic: ImageDiagnostics,
) -> bytes:
    failures: list[str] = []
    for index, (source, value) in enumerate(
        candidates(data, origin_from_url(api_url)), start=1
    ):
        download_status: int | None = None

        def note_download(**fields: Any) -> None:
            nonlocal download_status
            download_status = fields.get("download_http_status", download_status)
            diagnostic.emit("download", candidate=index, source=source, **fields)

        try:
            if source == "base64":
                encoded = (
                    value.split(",", 1)[1]
                    if value.startswith("data:") and "," in value
                    else value
                )
                if len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
                    raise ValueError("图片内容超过大小限制")
                content = base64.b64decode(encoded, validate=True)
            else:
                content = await download(value, note_download)
        except Exception as exc:
            # Deliberately discard exception text: transport errors may include a
            # signed URL, API credentials or an echoed upstream request body.
            diagnostic.emit(
                "candidate",
                candidate=index,
                source=source,
                download_http_status=download_status,
                actual_format=None,
                actual_frames=None,
                outcome="download_failed" if source == "url" else "decode_failed",
                error_type=type(exc).__name__,
            )
            failures.append(
                "图片链接下载失败。" if source == "url" else "Base64 图片数据无法解码。"
            )
            continue
        try:
            info = await asyncio.to_thread(inspect_image, content)
        except Exception as exc:
            diagnostic.emit(
                "candidate",
                candidate=index,
                source=source,
                download_http_status=download_status,
                actual_format=None,
                actual_frames=None,
                outcome="invalid_image",
                error_type=type(exc).__name__,
            )
            failures.append("返回的图片内容不完整或无法解析。")
            continue
        reason = info.gif_error()
        diagnostic.emit(
            "candidate",
            candidate=index,
            source=source,
            download_http_status=download_status,
            actual_format=info.format,
            actual_frames=info.frames,
            actual_size=f"{info.width}x{info.height}",
            outcome="format_mismatch" if reason else "selected",
        )
        if not reason:
            return content
        failures.append(reason)
    detail = "；".join(dict.fromkeys(message.rstrip("。") for message in failures))
    raise ImageResultError(
        (detail or "接口响应中没有可读取的图片候选") + "。已停止发送，未自动重新生图。"
    )
