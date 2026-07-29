from __future__ import annotations

import base64
import math
from typing import Any

import aiohttp
from astrbot.api import logger

from ..base import LOG_PREFIX, image_mime_and_ext, normalize_openai_base_url
from .pipe import ImageRequest, ImageRoute

_TIER_MAX_EDGES = {"1K": 1024, "2K": 2048, "4K": 3840}
_MAX_TOTAL_PIXELS = 3840 * 2160
_SIZE_ALIGNMENT = 8


class SimpleFormData:
    def __init__(self) -> None:
        self.fields: list[tuple[str, Any, dict[str, Any]]] = []

    def add_field(self, name: str, value: Any, **kwargs: Any) -> None:
        self.fields.append((name, value, kwargs))


def build_request(
    route: ImageRoute,
    parts: list[dict[str, Any]],
    *,
    resolution: str,
    aspect_ratio: str,
) -> ImageRequest:
    base = normalize_openai_base_url(route.api_url)
    headers = {"Authorization": f"Bearer {route.api_key}"}
    images = inline_images(parts)
    if not images:
        return ImageRequest(
            url=f"{base}/images/generations",
            headers=headers,
            payload={
                "model": route.model,
                "prompt": prompt_from_parts(parts),
                "size": size_for(resolution, aspect_ratio),
            },
        )

    form = form_data()
    form.add_field("model", route.model)
    form.add_field("prompt", prompt_from_parts(parts))
    form.add_field("size", size_for(resolution, aspect_ratio))
    for index, (image_bytes, mime_type) in enumerate(images, start=1):
        _, ext = image_mime_and_ext(image_bytes)
        form.add_field(
            "image",
            image_bytes,
            filename=f"reference_{index}{ext}",
            content_type=mime_type,
        )
    return ImageRequest(url=f"{base}/images/edits", headers=headers, form=form)


def form_data() -> Any:
    form = getattr(aiohttp, "FormData", None)
    return form() if callable(form) else SimpleFormData()


def prompt_from_parts(parts: list[dict[str, Any]]) -> str:
    texts = [
        str(part.get("text") or "").strip()
        for part in parts
        if isinstance(part, dict) and part.get("text")
    ]
    if any(
        isinstance(part, dict)
        and isinstance(part.get("inlineData") or part.get("inline_data"), dict)
        for part in parts
    ):
        texts.append("参考随请求提供的图片线索，保持画面要求自然一致。")
    return "\n".join(text for text in texts if text).strip()


def inline_images(parts: list[dict[str, Any]]) -> list[tuple[bytes, str]]:
    images: list[tuple[bytes, str]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        inline = part.get("inlineData") or part.get("inline_data")
        if not isinstance(inline, dict):
            continue
        raw = str(inline.get("data") or "").strip()
        if not raw:
            continue
        try:
            image_bytes = base64.b64decode(raw)
        except Exception:
            continue
        mime_type = str(
            inline.get("mimeType")
            or inline.get("mime_type")
            or image_mime_and_ext(image_bytes)[0]
        ).strip()
        images.append((image_bytes, mime_type or "image/png"))
    return images


def size_for(resolution: str, aspect_ratio: str) -> str:
    resolution = str(resolution or "").strip().upper()
    if resolution not in _TIER_MAX_EDGES:
        raise ValueError("图片输出分辨率只能是 1K、2K 或 4K")
    ratio = str(aspect_ratio or "1:1").strip()
    try:
        width_ratio, height_ratio = (int(value) for value in ratio.split(":", 1))
    except (TypeError, ValueError):
        width_ratio = height_ratio = 1
    if width_ratio <= 0 or height_ratio <= 0:
        width_ratio = height_ratio = 1
    if width_ratio == height_ratio:
        edge = _TIER_MAX_EDGES[resolution]
        width = height = edge
    else:
        long_ratio = max(width_ratio, height_ratio)
        short_ratio = min(width_ratio, height_ratio)
        long_edge = _TIER_MAX_EDGES[resolution]
        short_edge = round(long_edge * short_ratio / long_ratio)
        width, height = (
            (long_edge, short_edge)
            if width_ratio > height_ratio
            else (short_edge, long_edge)
        )
    width, height = _fit_total_pixel_budget(
        width,
        height,
        width_ratio,
        height_ratio,
    )
    return f"{width}x{height}"


def _fit_total_pixel_budget(
    width: int,
    height: int,
    width_ratio: int,
    height_ratio: int,
) -> tuple[int, int]:
    if width * height <= _MAX_TOTAL_PIXELS:
        return width, height
    divisor = math.gcd(width_ratio, height_ratio)
    reduced_width = width_ratio // divisor
    reduced_height = height_ratio // divisor
    unit = math.isqrt(_MAX_TOTAL_PIXELS // (reduced_width * reduced_height))
    unit -= unit % _SIZE_ALIGNMENT
    if unit <= 0:
        raise ValueError("图片比例无法在接口像素限制内生成")
    return reduced_width * unit, reduced_height * unit


def extract_image(data: dict[str, Any]) -> bytes:
    images: list[bytes] = []
    items = data.get("data")
    if not isinstance(items, list):
        return b""
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("b64_json") or "").strip()
        if raw:
            try:
                images.append(base64.b64decode(raw))
            except Exception as exc:
                logger.warning(f"{LOG_PREFIX} 图片数据解码失败（GPT接口）：{exc}")
    return images[-1] if images else b""


def origin(api_url: str) -> str:
    text = str(api_url or "").strip().rstrip("/")
    normalized = normalize_openai_base_url(text)
    suffix = "/v1"
    return (
        normalized[: -len(suffix)]
        if normalized.endswith(suffix)
        else text or "空接口地址"
    )
