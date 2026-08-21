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

CREATIVE_STYLE_GENERATION_MODES = {"text_to_image", "image_to_image"}

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


@dataclass(slots=True)
class CreativeWardrobeSettings:
    enabled: bool = False
    default_mode: str = "text_to_image"

    @staticmethod
    def from_dict(data: Any) -> CreativeWardrobeSettings:
        if not isinstance(data, dict):
            return CreativeWardrobeSettings()
        default_mode = as_str(data.get("default_mode", "text_to_image")).strip().lower()
        if default_mode not in CREATIVE_STYLE_GENERATION_MODES:
            default_mode = "text_to_image"

        return CreativeWardrobeSettings(
            enabled=as_bool(data.get("enabled", False), False),
            default_mode=default_mode,
        )


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
    creative_wardrobe: CreativeWardrobeSettings = field(
        default_factory=CreativeWardrobeSettings
    )

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
            creative_wardrobe=CreativeWardrobeSettings.from_dict(
                data.get("creative_wardrobe", {})
            ),
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


@dataclass(slots=True)
class RealtimeVoiceCallSettings:
    enabled: bool = False
    listen_host: str = "0.0.0.0"
    listen_port: int = 6186
    public_url: str = ""
    endpoint_url: str = "wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue"
    model: str = "1.2.6.1"
    max_duration_seconds: int = 1800
    idle_timeout_seconds: int = 90
    invite_expire_seconds: int = 120
    max_concurrent_calls: int = 1
    context_turns: int = 8
    allow_function_calls: bool = False
    tool_call_timeout_seconds: int = 60
    short_url_enabled: bool = True

    @staticmethod
    def from_dict(data: Any) -> RealtimeVoiceCallSettings:
        if not isinstance(data, dict):
            return RealtimeVoiceCallSettings()
        endpoint_url = as_str(data.get("endpoint_url", "")).strip()
        if not endpoint_url.startswith(("ws://", "wss://")):
            endpoint_url = "wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue"
        return RealtimeVoiceCallSettings(
            enabled=as_bool(data.get("enabled", False), False),
            listen_host=as_str(data.get("listen_host", "0.0.0.0")).strip() or "0.0.0.0",
            listen_port=as_int(data.get("listen_port", 6186), 6186, 1024, 65535),
            public_url=as_str(
                data.get("public_url", data.get("gateway_url", ""))
            ).strip().rstrip("/"),
            endpoint_url=endpoint_url,
            model=as_str(data.get("model", "1.2.6.1")).strip() or "1.2.6.1",
            max_duration_seconds=as_int(
                data.get("max_duration_seconds", 1800), 1800, 30, 7200
            ),
            idle_timeout_seconds=as_int(
                data.get("idle_timeout_seconds", 90), 90, 30, 1800
            ),
            invite_expire_seconds=as_int(
                data.get("invite_expire_seconds", 120), 120, 30, 3600
            ),
            max_concurrent_calls=as_int(
                data.get("max_concurrent_calls", 1), 1, 1, 4
            ),
            context_turns=as_int(data.get("context_turns", 8), 8, 0, 20),
            allow_function_calls=as_bool(
                data.get("allow_function_calls", False), False
            ),
            tool_call_timeout_seconds=as_int(
                data.get("tool_call_timeout_seconds", 60), 60, 1, 300
            ),
            short_url_enabled=as_bool(data.get("short_url_enabled", True), True),
        )
