from __future__ import annotations

import base64
import math
from typing import Any

import aiohttp

from astrbot.api import logger

from ..base import (
    LOG_PREFIX,
    absolute_url,
    image_mime_and_ext,
    normalize_openai_base_url,
    origin_from_url,
)
from .pipe import ImageRequest, ImageRoute

_TIER_MAX_EDGES = {"1K": 1024, "2K": 2048, "4K": 3840}
_MAX_TOTAL_PIXELS = 3840 * 2160
_SIZE_ALIGNMENT = 8
_GPT_IMAGE_2_SIZES = {
    "1K": {
        "2:3": "848x1264",
        "1:1": "1024x1024",
        "16:9": "1376x768",
        "3:2": "1264x848",
        "3:4": "896x1200",
        "5:4": "1152x928",
        "4:3": "1200x896",
        "4:5": "928x1152",
        "9:16": "768x1376",
        "21:9": "1584x672",
    },
    "2K": {
        "2:3": "1376x2048",
        "1:1": "2048x2048",
        "16:9": "2048x1136",
        "3:2": "2048x1376",
        "3:4": "1536x2048",
        "5:4": "2048x1648",
        "4:3": "2048x1536",
        "4:5": "1648x2048",
        "9:16": "1136x2048",
        "21:9": "2048x864",
    },
    "4K": {
        "2:3": "2336x3504",
        "1:1": "2880x2880",
        "16:9": "3584x2016",
        "3:2": "3504x2336",
        "3:4": "2448x3264",
        "5:4": "3200x2560",
        "4:3": "3264x2448",
        "4:5": "2560x3200",
        "9:16": "2016x3584",
        "21:9": "3808x1632",
    },
}


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
    size = size_for(resolution, aspect_ratio, model=route.model)
    images = inline_images(parts)
    if not images:
        return ImageRequest(
            url=f"{base}/images/generations",
            headers=headers,
            payload={
                "model": route.model,
                "prompt": prompt_from_parts(parts),
                "size": size,
            },
        )

    form = form_data()
    form.add_field("model", route.model)
    form.add_field("prompt", prompt_from_parts(parts))
    form.add_field("size", size)
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


def size_for(
    resolution: str,
    aspect_ratio: str,
    *,
    model: str = "gpt-image-2",
) -> str:
    resolution = str(resolution or "").strip().upper()
    if resolution not in _TIER_MAX_EDGES:
        raise ValueError("图片输出分辨率只能是 1K、2K 或 4K")
    if str(model or "").strip().lower() == "gpt-image-2":
        return _GPT_IMAGE_2_SIZES[resolution][
            supported_aspect_ratio(model, aspect_ratio)
        ]
    return _generic_size_for(resolution, aspect_ratio)


def supported_aspect_ratio(model: str, aspect_ratio: str) -> str:
    ratio, width_ratio, height_ratio = _normalized_ratio(aspect_ratio)
    if str(model or "").strip().lower() != "gpt-image-2":
        return ratio
    supported = _GPT_IMAGE_2_SIZES["1K"]
    if ratio in supported:
        return ratio
    target = width_ratio / height_ratio
    return min(
        supported,
        key=lambda candidate: abs(
            math.log(
                (int(candidate.split(":", 1)[0]) / int(candidate.split(":", 1)[1]))
                / target
            )
        ),
    )


def _generic_size_for(resolution: str, aspect_ratio: str) -> str:
    _ratio, width_ratio, height_ratio = _normalized_ratio(aspect_ratio)
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


def _normalized_ratio(aspect_ratio: str) -> tuple[str, int, int]:
    ratio = str(aspect_ratio or "1:1").strip()
    try:
        width_ratio, height_ratio = (int(value) for value in ratio.split(":", 1))
    except (TypeError, ValueError):
        width_ratio = height_ratio = 1
    if width_ratio <= 0 or height_ratio <= 0:
        width_ratio = height_ratio = 1
    divisor = math.gcd(width_ratio, height_ratio)
    width_ratio //= divisor
    height_ratio //= divisor
    return f"{width_ratio}:{height_ratio}", width_ratio, height_ratio


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


def extract_image(data: dict[str, Any], api_url: str = "") -> tuple[bytes, str]:
    """读取 OpenAI 图片响应中的 Base64 图片或结果地址。

    OpenAI 兼容中转接口可能返回 ``b64_json``，也可能只返回
    ``data[].url``。调用方负责对结果地址执行网络白名单校验和下载。
    """
    image_bytes = b""
    image_url = ""
    items = data.get("data")
    if not isinstance(items, list):
        return image_bytes, image_url
    base_origin = origin_from_url(normalize_openai_base_url(api_url))
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("b64_json") or "").strip()
        if raw:
            try:
                encoded = (
                    raw.split(",", 1)[1]
                    if raw.startswith("data:") and "," in raw
                    else raw
                )
                image_bytes = base64.b64decode(encoded)
            except Exception as exc:
                logger.warning(f"{LOG_PREFIX} 图片数据解码失败（GPT接口）：{exc}")
        candidate_url = absolute_url(item.get("url"), base_origin)
        if candidate_url:
            image_url = candidate_url
    return image_bytes, image_url


def origin(api_url: str) -> str:
    text = str(api_url or "").strip().rstrip("/")
    normalized = normalize_openai_base_url(text)
    suffix = "/v1"
    return (
        normalized[: -len(suffix)]
        if normalized.endswith(suffix)
        else text or "空接口地址"
    )
