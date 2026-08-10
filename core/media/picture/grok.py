from __future__ import annotations

import base64
from typing import Any

from astrbot.api import logger

from ..base import LOG_PREFIX, absolute_url, normalize_openai_base_url, origin_from_url
from .openai import size_for
from .pipe import ImageRequest, ImageRoute


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
    payload: dict[str, Any] = {
        "model": route.model,
        "prompt": "\n".join(texts)[:4000],
        "n": 1,
        "response_format": "b64_json",
        "aspect_ratio": aspect_ratio,
        "resolution": resolution.lower(),
    }
    if not images:
        payload["stream"] = False
        return ImageRequest(
            url=f"{base}/images/generations",
            headers=headers,
            payload=payload,
        )

    if len(images) == 1:
        payload["image"] = {"url": images[0]}
    else:
        payload["images"] = [{"url": image} for image in images]
    # 部分 OpenAI 兼容中转只识别精确 size，不读取 Grok 的档位与比例字段。
    payload["size"] = size_for(resolution, aspect_ratio)
    return ImageRequest(
        url=f"{base}/images/edits",
        headers=headers,
        payload=payload,
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
