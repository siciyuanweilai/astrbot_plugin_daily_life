from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cast import (
    as_bool,
    as_float,
    as_friend_reference_profiles,
    as_int,
    as_reference_image_items,
    as_str,
    as_str_list,
)

DEFAULT_VOLCENGINE_TTS_MODEL = "seed-tts-2.0-standard"
DEFAULT_VOLCENGINE_SAMPLE_RATE = 24000
DEFAULT_VOLCENGINE_FORMAT = "mp3"
IMAGE_PROTOCOLS = {"gemini", "openai", "grok"}
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


def _normalize_voice_source(value: Any) -> str:
    source = as_str(value, "cloned").strip().lower()
    aliases = {
        "clone": "cloned",
        "cloned": "cloned",
        "复刻": "cloned",
        "复刻音色": "cloned",
        "preset": "preset",
        "预置": "preset",
        "预置音色": "preset",
    }
    return aliases.get(source, "cloned")


@dataclass(slots=True)
class ImageApiChannel:
    api_url: str
    api_key: str
    group_name: str = ""
    model: str = ""
    protocol: str = "gemini"
    resolution: str = "4K"
    aspect_ratio: str = "1:1"
    timeout_seconds: int = 120


def _image_resolution(value: Any, protocol: str = "gemini") -> str:
    default = "2K" if protocol == "grok" else "4K"
    resolution = as_str(value, default).strip().upper() or default
    if protocol == "grok":
        return resolution if resolution in {"1K", "2K"} else default
    return resolution if resolution in IMAGE_RESOLUTIONS else default


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
        group_name = " ".join(as_str(raw.get("group_name", "")).split())[:40]
        protocol = (
            as_str(
                raw.get("protocol") or raw.get("__template_key") or "gemini", "gemini"
            )
            .strip()
            .lower()
        )
        protocol = protocol if protocol in IMAGE_PROTOCOLS else "gemini"
        default_model = {
            "openai": "gpt-image-2",
            "grok": "grok-imagine-image",
        }.get(protocol, "gemini-3-pro-image-preview")
        model = (
            as_str(raw.get("model", default_model), default_model).strip()
            or default_model
        )
        resolution = _image_resolution(raw.get("resolution"), protocol)
        aspect_ratio = _image_aspect_ratio(raw.get("aspect_ratio", "1:1"))
        timeout_seconds = as_int(raw.get("timeout_seconds", 120), 120, 10, 600)
        if not api_url or not api_key:
            continue
        key = (
            api_url,
            api_key,
            model,
            protocol,
            resolution,
            aspect_ratio,
            timeout_seconds,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(
            ImageApiChannel(
                api_url=api_url,
                api_key=api_key,
                group_name=group_name,
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
    prompt_rewrite_provider: str = ""
    image_director_provider: str = ""
    photo_suite_planning_timeout_seconds: int = 45
    text_channels: list[ImageApiChannel] = field(default_factory=list)
    edit_channels: list[ImageApiChannel] = field(default_factory=list)
    character_reference_images: list[dict[str, Any]] = field(default_factory=list)
    friend_reference_profiles: list[dict[str, Any]] = field(default_factory=list)
    character_reference_policy: str = "off"

    def primary_aspect_ratio(self) -> str:
        for channels in (self.text_channels, self.edit_channels):
            for channel in channels:
                ratio = str(getattr(channel, "aspect_ratio", "") or "").strip()
                if ratio in IMAGE_ASPECT_RATIOS:
                    return ratio
        return "1:1"

    @staticmethod
    def from_dict(data: Any) -> ImageGenerationSettings:
        if not isinstance(data, dict):
            return ImageGenerationSettings()
        policy = (
            as_str(data.get("character_reference_policy", "off"), "off").strip().lower()
            or "off"
        )
        if policy not in {"auto", "always", "off"}:
            policy = "off"
        return ImageGenerationSettings(
            enabled=as_bool(data.get("enabled", False), False),
            prompt_rewrite_provider=as_str(
                data.get("prompt_rewrite_provider", "")
            ).strip(),
            image_director_provider=as_str(
                data.get("image_director_provider", "")
            ).strip(),
            photo_suite_planning_timeout_seconds=as_int(
                data.get("photo_suite_planning_timeout_seconds", 45), 45, 10, 120
            ),
            text_channels=_image_channels(data.get("text_channels", [])),
            edit_channels=_image_channels(data.get("edit_channels", [])),
            character_reference_images=as_reference_image_items(
                data.get("character_reference_images", [])
            ),
            friend_reference_profiles=as_friend_reference_profiles(
                data.get("friend_reference_profiles", [])
            ),
            character_reference_policy=policy,
        )


@dataclass(slots=True)
class VideoGenerationSettings:
    enabled: bool = False
    base_url: str = ""
    api_keys: list[str] = field(default_factory=list)
    model: str = "grok-imagine-video-1.5"
    duration: int = 8
    aspect_ratio: str = "1:1"
    resolution: str = "720p"
    timeout_seconds: int = 300
    request_timeout_seconds: int = 60
    poll_interval_seconds: float = 5.0

    @staticmethod
    def from_dict(data: Any) -> VideoGenerationSettings:
        if not isinstance(data, dict):
            return VideoGenerationSettings()
        return VideoGenerationSettings(
            enabled=as_bool(data.get("enabled", False), False),
            base_url=as_str(data.get("base_url", ""), "").strip(),
            api_keys=as_str_list(data.get("api_keys", [])),
            model=as_str(
                data.get("model", "grok-imagine-video-1.5"),
                "grok-imagine-video-1.5",
            ).strip()
            or "grok-imagine-video-1.5",
            duration=as_int(data.get("duration", 8), 8, 1, 15),
            aspect_ratio="1:1",
            resolution=as_str(data.get("resolution", "720p"), "720p").strip() or "720p",
            timeout_seconds=as_int(data.get("timeout_seconds", 300), 300, 30, 3600),
            request_timeout_seconds=as_int(
                data.get("request_timeout_seconds", 60), 60, 10, 600
            ),
            poll_interval_seconds=as_float(
                data.get("poll_interval_seconds", 5.0), 5.0, 1.0, 120.0
            ),
        )


@dataclass(slots=True)
class VoiceGenerationSettings:
    enabled: bool = False
    smart_switch_enabled: bool = True
    smart_switch_probability: float = 35.0
    proactive_enabled: bool = False
    proactive_probability: float = 100.0
    api_key: str = ""
    speaker_id: str = ""
    speaker_source: str = "cloned"
    speech_rate: int = 0
    loudness_rate: int = 0
    timeout_seconds: int = 30
    max_retries: int = 2

    @staticmethod
    def from_dict(data: Any) -> VoiceGenerationSettings:
        if not isinstance(data, dict):
            return VoiceGenerationSettings()
        return VoiceGenerationSettings(
            enabled=as_bool(data.get("enabled", False), False),
            smart_switch_enabled=as_bool(data.get("smart_switch_enabled", True), True),
            smart_switch_probability=as_float(
                data.get("smart_switch_probability", 35.0), 35.0, 0.0, 100.0
            ),
            proactive_enabled=as_bool(data.get("proactive_enabled", False), False),
            proactive_probability=as_float(
                data.get("proactive_probability", 100.0), 100.0, 0.0, 100.0
            ),
            api_key=as_str(data.get("api_key", "")).strip(),
            speaker_id=as_str(data.get("speaker_id", data.get("speaker", ""))).strip(),
            speaker_source=_normalize_voice_source(
                data.get("speaker_source", data.get("voice_source", "cloned"))
            ),
            speech_rate=as_int(data.get("speech_rate", 0), 0, -50, 100),
            loudness_rate=as_int(data.get("loudness_rate", 0), 0, -50, 100),
            timeout_seconds=as_int(data.get("timeout_seconds", 30), 30, 5, 300),
            max_retries=as_int(data.get("max_retries", 2), 2, 0, 5),
        )
