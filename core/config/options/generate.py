from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .parse import (
    as_bool,
    as_float,
    as_float_map,
    as_int,
    as_reference_image_items,
    as_str,
    as_str_list,
    as_str_map,
)

DEFAULT_VOICE_MODEL = "FunAudioLLM/CosyVoice2-0.5B"
DEFAULT_VOICE_MAP = "neutral:\nhappy:\nsad:\nangry:"
DEFAULT_SPEED_MAP = "neutral: 1.0\nhappy: 1.15\nsad: 0.9\nangry: 1.1"
DEFAULT_VOICE_SPEED = 1.0
DEFAULT_VOICE_FORMAT = "wav"
IMAGE_PROTOCOLS = {"gemini", "openai"}
IMAGE_RESOLUTIONS = {"1K", "2K", "4K"}
IMAGE_ASPECT_RATIOS = (
    "1:1",
    "1:4",
    "1:8",
    "2:3",
    "3:2",
    "3:4",
    "4:1",
    "4:3",
    "4:5",
    "5:4",
    "8:1",
    "9:16",
    "16:9",
    "21:9",
)


@dataclass(slots=True)
class ImageApiChannel:
    api_url: str
    api_key: str
    model: str = ""
    protocol: str = "gemini"
    resolution: str = "4K"
    aspect_ratio: str = "1:1"
    timeout_seconds: int = 120


def _image_resolution(value: Any) -> str:
    resolution = as_str(value, "4K").strip().upper() or "4K"
    return resolution if resolution in IMAGE_RESOLUTIONS else "4K"


def _image_aspect_ratio(value: Any) -> str:
    aspect_ratio = as_str(value, "1:1").strip() or "1:1"
    return aspect_ratio if aspect_ratio in IMAGE_ASPECT_RATIOS else "1:1"


def _image_channels(value: Any) -> list[ImageApiChannel]:
    if not isinstance(value, list):
        return []
    result: list[ImageApiChannel] = []
    seen: set[tuple[str, str, str, str, str, str, int]] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        api_url = as_str(raw.get("api_url", "")).strip().rstrip("/")
        api_key = as_str(raw.get("api_key", "")).strip()
        protocol = as_str(raw.get("protocol") or raw.get("__template_key") or "gemini", "gemini").strip().lower()
        protocol = protocol if protocol in IMAGE_PROTOCOLS else "gemini"
        default_model = "gpt-image-2" if protocol == "openai" else "gemini-3-pro-image-preview"
        model = as_str(raw.get("model", default_model), default_model).strip() or default_model
        resolution = _image_resolution(raw.get("resolution", "4K"))
        aspect_ratio = _image_aspect_ratio(raw.get("aspect_ratio", "1:1"))
        timeout_seconds = as_int(raw.get("timeout_seconds", 120), 120, 10, 600)
        if not api_url or not api_key:
            continue
        key = (api_url, api_key, model, protocol, resolution, aspect_ratio, timeout_seconds)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            ImageApiChannel(
                api_url=api_url,
                api_key=api_key,
                model=model,
                protocol=protocol,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                timeout_seconds=timeout_seconds,
            )
        )
    return result


@dataclass(slots=True)
class ImageGenerationSettings:
    enabled: bool = False
    channels: list[ImageApiChannel] = field(default_factory=list)
    character_reference_images: list[dict[str, Any]] = field(default_factory=list)
    character_reference_policy: str = "off"
    reference_max_count: int = 6

    @staticmethod
    def from_dict(data: Any) -> "ImageGenerationSettings":
        if not isinstance(data, dict):
            return ImageGenerationSettings()
        policy = as_str(data.get("character_reference_policy", "off"), "off").strip().lower() or "off"
        if policy not in {"auto", "always", "off"}:
            policy = "off"
        return ImageGenerationSettings(
            enabled=as_bool(data.get("enabled", False), False),
            channels=_image_channels(data.get("channels", [])),
            character_reference_images=as_reference_image_items(data.get("character_reference_images", [])),
            character_reference_policy=policy,
            reference_max_count=as_int(data.get("reference_max_count", 6), 6, 1, 12),
        )


@dataclass(slots=True)
class VideoGenerationSettings:
    enabled: bool = False
    base_url: str = ""
    api_keys: list[str] = field(default_factory=list)
    model: str = "grok-imagine-video-1.5-preview"
    duration: int = 8
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    timeout_seconds: int = 300
    request_timeout_seconds: int = 60
    poll_interval_seconds: float = 5.0

    @staticmethod
    def from_dict(data: Any) -> "VideoGenerationSettings":
        if not isinstance(data, dict):
            return VideoGenerationSettings()
        return VideoGenerationSettings(
            enabled=as_bool(data.get("enabled", False), False),
            base_url=as_str(data.get("base_url", ""), "").strip(),
            api_keys=as_str_list(data.get("api_keys") or data.get("api_key")),
            model=as_str(
                data.get("model", "grok-imagine-video-1.5-preview"),
                "grok-imagine-video-1.5-preview",
            ).strip()
            or "grok-imagine-video-1.5-preview",
            duration=as_int(data.get("duration", 8), 8, 1, 15),
            aspect_ratio=as_str(data.get("aspect_ratio", "16:9"), "16:9").strip() or "16:9",
            resolution=as_str(data.get("resolution", "720p"), "720p").strip() or "720p",
            timeout_seconds=as_int(data.get("timeout_seconds", 300), 300, 30, 3600),
            request_timeout_seconds=as_int(data.get("request_timeout_seconds", 60), 60, 10, 600),
            poll_interval_seconds=as_float(data.get("poll_interval_seconds", 5.0), 5.0, 1.0, 120.0),
        )


@dataclass(slots=True)
class VoiceGenerationSettings:
    enabled: bool = False
    smart_switch_enabled: bool = True
    smart_switch_probability: float = 35.0
    proactive_enabled: bool = False
    proactive_probability: float = 100.0
    group_whitelist: list[str] = field(default_factory=list)
    group_blacklist: list[str] = field(default_factory=list)
    private_whitelist: list[str] = field(default_factory=list)
    private_blacklist: list[str] = field(default_factory=list)
    api_url: str = "https://api.siliconflow.cn/v1"
    api_key: str = ""
    model: str = DEFAULT_VOICE_MODEL
    voice: str = ""
    emotion_voice_map: dict[str, str] = field(default_factory=dict)
    emotion_speed_map: dict[str, float] = field(default_factory=lambda: as_float_map(DEFAULT_SPEED_MAP, {}))
    timeout_seconds: int = 30
    max_retries: int = 2

    @staticmethod
    def from_dict(data: Any) -> "VoiceGenerationSettings":
        if not isinstance(data, dict):
            return VoiceGenerationSettings()
        return VoiceGenerationSettings(
            enabled=as_bool(data.get("enabled", False), False),
            smart_switch_enabled=as_bool(data.get("smart_switch_enabled", True), True),
            smart_switch_probability=as_float(data.get("smart_switch_probability", 35.0), 35.0, 0.0, 100.0),
            proactive_enabled=as_bool(data.get("proactive_enabled", False), False),
            proactive_probability=as_float(data.get("proactive_probability", 100.0), 100.0, 0.0, 100.0),
            group_whitelist=as_str_list(data.get("group_whitelist", [])),
            group_blacklist=as_str_list(data.get("group_blacklist", [])),
            private_whitelist=as_str_list(data.get("private_whitelist", [])),
            private_blacklist=as_str_list(data.get("private_blacklist", [])),
            api_url=as_str(data.get("api_url", "https://api.siliconflow.cn/v1"), "https://api.siliconflow.cn/v1").strip().rstrip("/")
            or "https://api.siliconflow.cn/v1",
            api_key=as_str(data.get("api_key", "")).strip(),
            model=as_str(data.get("model", DEFAULT_VOICE_MODEL), DEFAULT_VOICE_MODEL).strip() or DEFAULT_VOICE_MODEL,
            voice=as_str(data.get("voice", "")).strip(),
            emotion_voice_map=as_str_map(data.get("emotion_voice_map", DEFAULT_VOICE_MAP)),
            emotion_speed_map=as_float_map(
                data.get("emotion_speed_map", DEFAULT_SPEED_MAP),
                as_float_map(DEFAULT_SPEED_MAP, {}),
            ),
            timeout_seconds=as_int(data.get("timeout_seconds", 30), 30, 5, 300),
            max_retries=as_int(data.get("max_retries", 2), 2, 0, 5),
        )
