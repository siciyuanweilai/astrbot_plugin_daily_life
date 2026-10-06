from __future__ import annotations

import base64
import math
from typing import Any

from astrbot.api import logger

from ..base import LOG_PREFIX, absolute_url, normalize_openai_base_url, origin_from_url
from .pipe import ImageRequest, ImageRoute

_GROK_SIZES = {
    "1K": {
        "1:1": "1024x1024",
        "2:3": "1024x1536",
        "3:2": "1536x1024",
        "3:4": "1152x1536",
        "4:3": "1536x1152",
        "9:16": "864x1536",
        "16:9": "1536x864",
        "1:3": "512x1536",
        "3:1": "1536x512",
        "7:3": "2016x864",
    },
    "2K": {
        "1:1": "2048x2048",
        "2:3": "2048x3072",
        "3:2": "3072x2048",
        "3:4": "1536x2048",
        "4:3": "2048x1536",
        "4:5": "2048x2560",
        "5:4": "2560x2048",
        "9:16": "1152x2048",
        "16:9": "2048x1152",
        "1:3": "1024x3072",
        "3:1": "3072x1024",
    },
}


def size_for(resolution: str, aspect_ratio: str) -> str:
    """把插件的分辨率档位和比例转换成 Grok 文档支持的精确尺寸。"""
    tier = str(resolution or "").strip().upper()
    if tier not in _GROK_SIZES:
        raise ValueError("Grok 图片接口只支持 1K 或 2K 分辨率")
    ratio, width_ratio, height_ratio = _normalized_ratio(aspect_ratio)
    supported = _GROK_SIZES[tier]
    if ratio not in supported:
        target = width_ratio / height_ratio
        ratio = min(
            supported,
            key=lambda candidate: abs(
                math.log(
                    (int(candidate.split(":", 1)[0]) / int(candidate.split(":", 1)[1]))
                    / target
                )
            ),
        )
    return supported[ratio]


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


def build_request(
    route: ImageRoute,
    parts: list[dict[str, Any]],
    *,
    resolution: str,
    aspect_ratio: str,
) -> ImageRequest:
    """构建 Grok 文生图或图生图请求。

    Args:
        route: 当前图片接口通道路由。
        parts: 已整理的文本与内联参考图。
        resolution: 当前图片输出分辨率档位。
        aspect_ratio: 当前图片输出宽高比。

    Returns:
        可由共享图片请求器直接发送的请求对象。
    """
    base = normalize_openai_base_url(route.api_url)
    headers = {
        "Authorization": f"Bearer {route.api_key}",
        "Content-Type": "application/json",
    }
    texts: list[str] = []
    images: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = str(part.get("text") or "").strip()
        if text:
            texts.append(text)
        inline = part.get("inlineData") or part.get("inline_data")
        if not isinstance(inline, dict):
            continue
        raw = str(inline.get("data") or "").strip()
        if not raw:
            continue
        mime_type = str(
            inline.get("mimeType") or inline.get("mime_type") or "image/png"
        ).strip()
        images.append(f"data:{mime_type or 'image/png'};base64,{raw}")
    size = size_for(resolution, aspect_ratio)
    payload: dict[str, Any] = {
        "model": route.model,
        "prompt": "\n".join(texts)[:4000],
        "size": size,
        "n": 1,
        "response_format": "b64_json",
    }
    if not images:
        return ImageRequest(
            url=f"{base}/images/generations",
            headers=headers,
            payload=payload,
        )

    if route.model in {"grok-imagine-image", "grok-imagine-image-2.0"}:
        payload["model"] = "grok-imagine-image-edit"
    if len(images) == 1:
        payload["image"] = {"url": images[0]}
    else:
        payload["images"] = [{"image_url": image} for image in images]
    return ImageRequest(
        url=f"{base}/images/edits",
        headers=headers,
        payload=payload,
        reference_image_count=len(images),
    )


def extract_image(data: dict[str, Any], api_url: str) -> tuple[bytes, str]:
    """读取 Grok 响应中的 Base64 图片或结果地址。

    Args:
        data: Grok 图片接口返回对象。
        api_url: 当前接口地址，用于补全相对结果地址。

    Returns:
        已解码图片数据与可选结果地址。
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
                logger.warning(f"{LOG_PREFIX} 图片数据解码失败（Grok接口）：{exc}")
        candidate_url = absolute_url(item.get("url"), base_origin)
        if candidate_url:
            image_url = candidate_url
    return image_bytes, image_url


def origin(api_url: str) -> str:
    """提取用于日志展示的 Grok 图片接口来源。

    Args:
        api_url: 用户配置的根地址或完整接口地址。

    Returns:
        去除图片接口路径后的来源地址。
    """
    text = str(api_url or "").strip().rstrip("/")
    normalized = normalize_openai_base_url(text)
    suffix = "/v1"
    return (
        normalized[: -len(suffix)]
        if normalized.endswith(suffix)
        else text or "空接口地址"
    )
