from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

LOG_PREFIX = "[日常生活]"
REFERENCE_IMAGE_MAX_BYTES = 24 * 1024 * 1024


@dataclass(slots=True)
class GeneratedImage:
    path: Path


@dataclass(slots=True)
class GeneratedVideo:
    url: str


@dataclass(slots=True)
class GeneratedVoice:
    path: Path


def image_mime_and_ext(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif", ".gif"
    if len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return "image/png", ".png"


def normalize_gemini_base_url(raw: str) -> str:
    url = str(raw or "").strip().rstrip("/")
    if not url:
        return ""
    lower = url.lower()
    for suffix in (
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/images/generations",
        "/images/generations",
        "/v1/completions",
        "/completions",
    ):
        if lower.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            lower = url.lower()
            break
    if lower.endswith("/v1"):
        url = url[:-3].rstrip("/")
        lower = url.lower()
    if lower.endswith("/v1beta/models"):
        return url
    if lower.endswith("/v1beta"):
        return f"{url}/models"
    return f"{url}/v1beta/models"


def normalize_openai_base_url(raw: str) -> str:
    url = str(raw or "").strip().rstrip("/")
    if not url:
        return ""
    lower = url.lower()
    for suffix in (
        "/v1/images/generations",
        "/images/generations",
        "/v1/images/edits",
        "/images/edits",
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/completions",
        "/completions",
    ):
        if lower.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            lower = url.lower()
            break
    if lower.endswith("/v1"):
        return url
    return f"{url}/v1"


def videos_endpoint(raw: str) -> str:
    base = str(raw or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1/videos"):
        return base
    if base.endswith("/v1"):
        return f"{base}/videos"
    return f"{base}/v1/videos"


def origin_from_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def absolute_url(value: Any, base_origin: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("/") and base_origin:
        return urljoin(f"{base_origin}/", text.lstrip("/"))
    return ""


def extract_video_url(data: Any, base_origin: str) -> str:
    if isinstance(data, dict):
        for key in ("video_url", "url", "download_url", "file_url"):
            url = absolute_url(data.get(key), base_origin)
            if url:
                return url
        for key in ("video", "data", "output", "videos", "result"):
            value = data.get(key)
            if isinstance(value, (dict, list)):
                url = extract_video_url(value, base_origin)
                if url:
                    return url
    if isinstance(data, list):
        for item in data:
            url = extract_video_url(item, base_origin)
            if url:
                return url
    return ""


def extract_content_url(data: Any, base_origin: str) -> str:
    if isinstance(data, dict):
        for key in ("content_url", "content"):
            url = absolute_url(data.get(key), base_origin)
            if url:
                return url
        for key in ("video", "data", "output", "videos", "result"):
            value = data.get(key)
            if isinstance(value, (dict, list)):
                url = extract_content_url(value, base_origin)
                if url:
                    return url
    if isinstance(data, list):
        for item in data:
            url = extract_content_url(item, base_origin)
            if url:
                return url
    return ""


def extract_request_id(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("request_id", "task_id", "id"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def upstream_error_text(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)[:300]
    error = data.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("error") or "").strip()
        code = str(error.get("code") or "").strip()
        return f"{code}: {message}" if code and message else message or code or str(error)[:300]
    if isinstance(error, str) and error.strip():
        return error.strip()[:300]
    for key in ("message", "msg", "detail", "reason"):
        value = str(data.get(key) or "").strip()
        if value:
            return value[:300]
    return str(data)[:300]


def image_data_url(image_bytes: bytes) -> str:
    mime, _ = image_mime_and_ext(image_bytes)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def normalize_emotion_category(value: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"happy", "sad", "angry", "neutral"} else ""


def emotion_category_label(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "未裁定"
    return {
        "neutral": "平静",
        "happy": "愉快",
        "sad": "低落",
        "angry": "烦躁",
    }.get(text, "未裁定")
