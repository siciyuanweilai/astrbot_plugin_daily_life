from __future__ import annotations

import base64
from typing import Any

from astrbot.api import logger

from ..shared import LOG_PREFIX, image_mime_and_ext, normalize_gemini_base_url
from .pipe import ImageRequest, ImageRoute


def build_request(route: ImageRoute, parts: list[dict[str, Any]], *, resolution: str, aspect_ratio: str) -> ImageRequest:
    url = f"{normalize_gemini_base_url(route.api_url)}/{route.model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": route.api_key}
    image_config = {"imageSize": resolution, "aspectRatio": aspect_ratio}
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "maxOutputTokens": 8192,
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_config,
            "responseFormat": {"image": image_config},
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
    }
    return ImageRequest(url=url, headers=headers, payload=payload)


def extract_image(data: dict[str, Any]) -> bytes:
    images: list[bytes] = []
    for candidate in data.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts", []):
            if not isinstance(part, dict):
                continue
            inline_data = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline_data, dict):
                continue
            raw = inline_data.get("data")
            if isinstance(raw, str) and raw.strip():
                try:
                    images.append(base64.b64decode(raw))
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 图片数据解码失败（Gemini接口）：{exc}")
    return images[-1] if images else b""


def origin(api_url: str) -> str:
    text = str(api_url or "").strip().rstrip("/")
    normalized = normalize_gemini_base_url(text)
    suffix = "/v1beta/models"
    return normalized[: -len(suffix)] if normalized.endswith(suffix) else text or "空接口地址"
