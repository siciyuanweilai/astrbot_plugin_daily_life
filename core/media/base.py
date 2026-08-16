from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

LOG_PREFIX = "[日常生活]"
REFERENCE_IMAGE_MAX_BYTES = 24 * 1024 * 1024
VOICE_STYLE_KEYS = frozenset({"neutral", "happy", "light", "sad", "angry"})


def normalize_voice_style(value: Any, fallback: str = "neutral") -> str:
    """校验模型返回的语音风格枚举，拒绝自由文本推断。"""
    style = str(value or "").strip().lower()
    if style in VOICE_STYLE_KEYS:
        return style
    default = str(fallback or "neutral").strip().lower()
    return default if default in VOICE_STYLE_KEYS else "neutral"


GROUP_IDENTITY_CONTINUITY_RULE = (
    "人物 A 与人物 B 是两位既定且不同的人物；保持双方各自的身份、脸部、体态和整体辨识度。"
    "服装、发型、体态和性别呈现等个体属性必须分别绑定到人物 A 或人物 B，"
    "不得串脸、融合、增删或交换人物，也不得把一个人的属性复制给另一个人。"
    "人物参考图只用于确认各自身份和稳定外观，不锁定本轮服装、配饰或发型；"
    "本轮画面要求中分别绑定到人物 A 或人物 B 的造型优先于参考图造型。"
    "不要用一套未标注归属的穿搭描述两个人；未标注归属的单套服装、发型或体态默认只应用于人物 A；"
    "人物 B 使用明确绑定给人物 B 的本轮造型，未明确时再选择符合当前场景的独立穿搭；"
    "参考不足时使用自然中性穿搭，不根据姓名或昵称猜测性别。"
    "只有用户明确要求同款、情侣装或统一造型时，双方才共享穿搭风格。"
)
PHYSICAL_IDENTITY_CONTINUITY_RULE = (
    "当前人物是既定角色时，稳定体貌以角色人设和身份参考资料为准；"
    "身份资料里的服装、配饰、发型、妆容和美甲只在本轮未指定时作为参考，"
    "本轮画面要求明确指定的当天造型优先。"
    "在本轮实际可见的范围内，服装、姿势、景别、镜头和光线应自然呈现既有整体轮廓与比例，"
    "遮挡、远景或宽松衣物不强行突出。"
    "不得因通用审美压平、夸张、扩大、缩小或重塑身体结构及局部比例。"
    "衣料应依照剪裁、材质、支撑、张力和重力自然覆盖身体。"
)


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
    if base.endswith(("/v1/videos", "/v1/videos/generations")):
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
        return (
            f"{code}: {message}"
            if code and message
            else message or code or str(error)[:300]
        )
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
