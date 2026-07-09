from __future__ import annotations

import base64
from typing import Any

import aiohttp
from astrbot.api import logger

from ..shared import LOG_PREFIX, image_mime_and_ext, normalize_openai_base_url
from .pipe import ImageRequest, ImageRoute

SIZE_MAP = {
    ("1K", "1:1"): "1024x1024",
    ("1K", "16:9"): "1792x1024",
    ("1K", "9:16"): "1024x1792",
    ("1K", "21:9"): "1792x1024",
    ("1K", "4:1"): "1792x1024",
    ("1K", "8:1"): "1792x1024",
    ("1K", "1:4"): "1024x1792",
    ("1K", "1:8"): "1024x1792",
    ("2K", "1:1"): "2048x2048",
    ("2K", "16:9"): "2560x1440",
    ("2K", "9:16"): "1440x2560",
    ("2K", "21:9"): "2560x1440",
    ("2K", "4:1"): "2560x1440",
    ("2K", "8:1"): "2560x1440",
    ("2K", "1:4"): "1440x2560",
    ("2K", "1:8"): "1440x2560",
    ("4K", "1:1"): "2048x2048",
    ("4K", "16:9"): "3840x2160",
    ("4K", "9:16"): "2160x3840",
    ("4K", "21:9"): "3840x2160",
    ("4K", "4:1"): "3840x2160",
    ("4K", "8:1"): "3840x2160",
    ("4K", "1:4"): "2160x3840",
    ("4K", "1:8"): "2160x3840",
}


class SimpleFormData:
    def __init__(self) -> None:
        self.fields: list[tuple[str, Any, dict[str, Any]]] = []

    def add_field(self, name: str, value: Any, **kwargs: Any) -> None:
        self.fields.append((name, value, kwargs))


def build_request(route: ImageRoute, parts: list[dict[str, Any]], *, resolution: str, aspect_ratio: str) -> ImageRequest:
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
    texts = [str(part.get("text") or "").strip() for part in parts if isinstance(part, dict) and part.get("text")]
    if any(isinstance(part, dict) and isinstance(part.get("inlineData") or part.get("inline_data"), dict) for part in parts):
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
        mime_type = str(inline.get("mimeType") or inline.get("mime_type") or image_mime_and_ext(image_bytes)[0]).strip()
        images.append((image_bytes, mime_type or "image/png"))
    return images


def size_for(resolution: str, aspect_ratio: str) -> str:
    key = (str(resolution or "4K").upper(), str(aspect_ratio or "1:1"))
    if key in SIZE_MAP:
        return SIZE_MAP[key]
    if key[1] in {"3:2", "4:3", "5:4"}:
        return "1536x1024"
    if key[1] in {"2:3", "3:4", "4:5"}:
        return "1024x1536"
    if key[1] in {"16:9", "21:9", "4:1", "8:1"}:
        return SIZE_MAP[(key[0], "16:9")]
    if key[1] in {"9:16", "1:4", "1:8"}:
        return SIZE_MAP[(key[0], "9:16")]
    return "1024x1024"


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
                logger.warning(f"{LOG_PREFIX} 图片数据解码失败（OpenAI接口）：{exc}")
    return images[-1] if images else b""


def origin(api_url: str) -> str:
    text = str(api_url or "").strip().rstrip("/")
    normalized = normalize_openai_base_url(text)
    suffix = "/v1"
    return normalized[: -len(suffix)] if normalized.endswith(suffix) else text or "空接口地址"
