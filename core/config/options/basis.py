from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...prompts import DEFAULT_WEB_TODAY_PROMPT
from .cast import as_bool, as_float, as_int, as_str, normalize_time, normalize_time_window


DEFAULT_CHAT_STYLE_PROMPT = (
    "日常闲聊先接住当下的一句话，不为了显得温柔或有趣而多铺陈。"
    "轻松接话保持短气口，一句只放一个主要意思，能自然停住就停住。"
    "认真问题、事实解释和情绪支持按内容自然展开，先给判断，再补必要原因。"
)

@dataclass(slots=True)
class WeatherSettings:
    api_key: str = ""
    default_city: str = ""
    aware_outfit: bool = True
    aware_activity: bool = True

    @staticmethod
    def from_dict(data: Any) -> "WeatherSettings":
        if not isinstance(data, dict):
            return WeatherSettings()
        return WeatherSettings(
            api_key=as_str(data.get("api_key", "")),
            default_city=as_str(data.get("default_city", "")),
            aware_outfit=as_bool(data.get("aware_outfit", True), True),
            aware_activity=as_bool(data.get("aware_activity", True), True),
        )


@dataclass(slots=True)
class StateSettings:
    enabled: bool = True
    provider: str = ""
    refresh_minutes: int = 30
    quiet_hours: str = "00:00-06:30"

    @staticmethod
    def from_dict(data: Any) -> "StateSettings":
        if not isinstance(data, dict):
            return StateSettings()
        return StateSettings(
            enabled=as_bool(data.get("enabled", True), True),
            provider=as_str(data.get("provider", "")).strip(),
            refresh_minutes=as_int(data.get("refresh_minutes", 30), 30, 5, 240),
            quiet_hours=normalize_time_window(data.get("quiet_hours", "00:00-06:30")),
        )


@dataclass(slots=True)
class ProactiveReplySettings:
    enabled: bool = False
    provider: str = ""
    group_enabled: bool = True
    private_enabled: bool = True
    talk_frequency: float = 0.35
    private_talk_frequency: float = 0.55
    idle_minutes: int = 30
    private_idle_minutes: int = 60
    cooldown_minutes: int = 20
    private_cooldown_minutes: int = 8
    min_message_length: int = 4
    min_confidence: float = 0.72
    max_reply_length: int = 80
    private_revisit_enabled: bool = False
    revisit_interval_minutes: int = 30
    revisit_cooldown_minutes: int = 180
    revisit_min_confidence: float = 0.82

    @staticmethod
    def from_dict(data: Any) -> "ProactiveReplySettings":
        if not isinstance(data, dict):
            return ProactiveReplySettings()
        return ProactiveReplySettings(
            enabled=as_bool(data.get("enabled", False), False),
            provider=as_str(data.get("provider", "")).strip(),
            group_enabled=as_bool(data.get("group_enabled", True), True),
            private_enabled=as_bool(data.get("private_enabled", True), True),
            talk_frequency=as_float(data.get("talk_frequency", 0.35), 0.35, 0.0, 1.0),
            private_talk_frequency=as_float(data.get("private_talk_frequency", 0.55), 0.55, 0.0, 1.0),
            idle_minutes=as_int(data.get("idle_minutes", 30), 30, 1, 1440),
            private_idle_minutes=as_int(data.get("private_idle_minutes", 60), 60, 5, 1440),
            cooldown_minutes=as_int(data.get("cooldown_minutes", 20), 20, 1, 1440),
            private_cooldown_minutes=as_int(data.get("private_cooldown_minutes", 8), 8, 1, 1440),
            min_message_length=as_int(data.get("min_message_length", 4), 4, 1, 200),
            min_confidence=as_float(data.get("min_confidence", 0.72), 0.72, 0.0, 1.0),
            max_reply_length=as_int(data.get("max_reply_length", 80), 80, 10, 300),
            private_revisit_enabled=as_bool(data.get("private_revisit_enabled", False), False),
            revisit_interval_minutes=as_int(data.get("revisit_interval_minutes", 30), 30, 5, 1440),
            revisit_cooldown_minutes=as_int(data.get("revisit_cooldown_minutes", 180), 180, 10, 10080),
            revisit_min_confidence=as_float(data.get("revisit_min_confidence", 0.82), 0.82, 0.0, 1.0),
        )


@dataclass(slots=True)
class ResponseGateSettings:
    enabled: bool = True
    group_talk_frequency: float = 0.45
    private_talk_frequency: float = 0.65
    min_interval_seconds: int = 12
    no_reply_backoff_seconds: int = 25
    no_reply_backoff_cap_seconds: int = 180
    no_reply_backoff_start_count: int = 2
    bypass_pending_count: int = 5
    media_only_group_frequency: float = 0.2

    @staticmethod
    def from_dict(data: Any) -> "ResponseGateSettings":
        if not isinstance(data, dict):
            return ResponseGateSettings()
        return ResponseGateSettings(
            enabled=as_bool(data.get("enabled", True), True),
            group_talk_frequency=as_float(data.get("group_talk_frequency", 0.45), 0.45, 0.0, 1.0),
            private_talk_frequency=as_float(data.get("private_talk_frequency", 0.65), 0.65, 0.0, 1.0),
            min_interval_seconds=as_int(data.get("min_interval_seconds", 12), 12, 0, 300),
            no_reply_backoff_seconds=as_int(data.get("no_reply_backoff_seconds", 25), 25, 0, 600),
            no_reply_backoff_cap_seconds=as_int(data.get("no_reply_backoff_cap_seconds", 180), 180, 0, 1800),
            no_reply_backoff_start_count=as_int(data.get("no_reply_backoff_start_count", 2), 2, 1, 20),
            bypass_pending_count=as_int(data.get("bypass_pending_count", 5), 5, 0, 50),
            media_only_group_frequency=as_float(data.get("media_only_group_frequency", 0.2), 0.2, 0.0, 1.0),
        )


@dataclass(slots=True)
class ChatStyleSettings:
    casual_short_prompt: str = DEFAULT_CHAT_STYLE_PROMPT
    casual_max_chars: int = 50
    group_casual_max_chars: int = 30
    private_casual_max_chars: int = 15
    proactive_max_chars: int = 15

    @staticmethod
    def from_dict(data: Any) -> "ChatStyleSettings":
        if not isinstance(data, dict):
            return ChatStyleSettings()
        return ChatStyleSettings(
            casual_short_prompt=as_str(
                data.get("casual_short_prompt", DEFAULT_CHAT_STYLE_PROMPT),
                DEFAULT_CHAT_STYLE_PROMPT,
            ).strip()
            or DEFAULT_CHAT_STYLE_PROMPT,
            casual_max_chars=as_int(data.get("casual_max_chars", 50), 50, 10, 50),
            group_casual_max_chars=as_int(data.get("group_casual_max_chars", 30), 30, 10, 30),
            private_casual_max_chars=as_int(data.get("private_casual_max_chars", 15), 15, 10, 30),
            proactive_max_chars=as_int(data.get("proactive_max_chars", 15), 15, 10, 30),
        )


@dataclass(slots=True)
class TaskModelSettings:
    provider: str = ""

    @staticmethod
    def from_dict(data: Any) -> "TaskModelSettings":
        if not isinstance(data, dict):
            return TaskModelSettings()
        return TaskModelSettings(provider=as_str(data.get("provider", "")).strip())


@dataclass(slots=True)
class EmojiSettings:
    collect_chat_emojis: bool = True
    max_ready: int = 128
    replace_when_full: bool = True
    max_size_mb: float = 5.0
    send_candidate_limit: int = 30
    review_batch_size: int = 3
    inactive_record_keep_days: int = 30
    orphan_cache_grace_hours: int = 24

    @staticmethod
    def from_dict(data: Any) -> "EmojiSettings":
        if not isinstance(data, dict):
            return EmojiSettings()
        return EmojiSettings(
            collect_chat_emojis=as_bool(data.get("collect_chat_emojis", True), True),
            max_ready=as_int(data.get("max_ready", 128), 128, 1, 300),
            replace_when_full=as_bool(data.get("replace_when_full", True), True),
            max_size_mb=as_float(data.get("max_size_mb", 5.0), 5.0, 1.0, 20.0),
            send_candidate_limit=as_int(data.get("send_candidate_limit", 30), 30, 1, 50),
            review_batch_size=as_int(data.get("review_batch_size", 3), 3, 1, 10),
            inactive_record_keep_days=as_int(data.get("inactive_record_keep_days", 30), 30, 1, 90),
            orphan_cache_grace_hours=as_int(data.get("orphan_cache_grace_hours", 24), 24, 1, 168),
        )


@dataclass(slots=True)
class SightSettings:
    summary_provider: str = ""
    frame_provider: str = ""
    total_timeout_seconds: int = 300
    max_transcript_chars: int = 8000
    max_frames: int = 8
    video_download_max_mb: int = 500
    video_download_timeout_seconds: int = 240
    video_cache_ttl_hours: int = 2
    video_cache_max_items: int = 60
    sight_cache_keep_days: int = 7
    audio_transcript_mode: str = "local"
    local_asr_timeout_seconds: int = 900
    note_max_transcript_chars: int = 20000
    bili_auto_summary: bool = True

    @staticmethod
    def from_dict(data: Any) -> "SightSettings":
        if not isinstance(data, dict):
            return SightSettings()
        return SightSettings(
            summary_provider=as_str(data.get("summary_provider", "")).strip(),
            frame_provider=as_str(data.get("frame_provider", "")).strip(),
            total_timeout_seconds=as_int(data.get("total_timeout_seconds", 300), 300, 60, 1800),
            max_transcript_chars=as_int(data.get("max_transcript_chars", 8000), 8000, 500, 30000),
            max_frames=as_int(data.get("max_frames", 8), 8, 1, 24),
            video_download_max_mb=as_int(data.get("video_download_max_mb", 500), 500, 0, 1024),
            video_download_timeout_seconds=as_int(data.get("video_download_timeout_seconds", 240), 240, 30, 600),
            video_cache_ttl_hours=as_int(data.get("video_cache_ttl_hours", 2), 2, 1, 168),
            video_cache_max_items=as_int(data.get("video_cache_max_items", 60), 60, 8, 500),
            sight_cache_keep_days=as_int(data.get("sight_cache_keep_days", 7), 7, 1, 30),
            audio_transcript_mode=_normalize_audio_transcript_mode(data.get("audio_transcript_mode", "local")),
            local_asr_timeout_seconds=as_int(data.get("local_asr_timeout_seconds", 900), 900, 120, 3600),
            note_max_transcript_chars=as_int(data.get("note_max_transcript_chars", 20000), 20000, 2000, 60000),
            bili_auto_summary=as_bool(data.get("bili_auto_summary", True), True),
        )


def _normalize_audio_transcript_mode(value: Any) -> str:
    text = as_str(value, "local").strip().lower()
    if text in {"bcut", "必剪"}:
        return "bcut"
    return "local"

@dataclass(slots=True)
class WebInspirationSettings:
    enabled: bool = False
    max_results: int = 3
    timeout_seconds: int = 10
    today_prompt: str = DEFAULT_WEB_TODAY_PROMPT

    @staticmethod
    def from_dict(data: Any) -> "WebInspirationSettings":
        if not isinstance(data, dict):
            return WebInspirationSettings()
        return WebInspirationSettings(
            enabled=as_bool(data.get("enabled", False), False),
            max_results=as_int(data.get("max_results", 3), 3, 1, 8),
            timeout_seconds=as_int(data.get("timeout_seconds", 10), 10, 3, 30),
            today_prompt=(
                as_str(data.get("today_prompt", DEFAULT_WEB_TODAY_PROMPT), DEFAULT_WEB_TODAY_PROMPT).strip()
                or DEFAULT_WEB_TODAY_PROMPT
            ),
        )


@dataclass(slots=True)
class LifecycleSettings:
    provider: str = ""
    review_time: str = "23:45"
    max_preferences: int = 16

    @staticmethod
    def from_dict(data: Any) -> "LifecycleSettings":
        if not isinstance(data, dict):
            return LifecycleSettings()
        return LifecycleSettings(
            provider=as_str(data.get("provider", "")).strip(),
            review_time=normalize_time(data.get("review_time", "23:45"), "23:45"),
            max_preferences=as_int(data.get("max_preferences", 16), 16, 0, 50),
        )


@dataclass(slots=True)
class StorageSettings:
    daily_keep_days: int = 30
    review_keep_days: int = 120
    memory_keep_days: int = 0
    planning_keep_days: int = 180
    generated_media_keep_days: int = 30
    reverse_cache_keep_days: int = 7

    @staticmethod
    def from_dict(data: Any) -> "StorageSettings":
        if not isinstance(data, dict):
            return StorageSettings()
        return StorageSettings(
            daily_keep_days=as_int(data.get("daily_keep_days", 30), 30, 0, 3650),
            review_keep_days=as_int(data.get("review_keep_days", 120), 120, 0, 3650),
            memory_keep_days=as_int(data.get("memory_keep_days", 0), 0, 0, 3650),
            planning_keep_days=as_int(data.get("planning_keep_days", 180), 180, 0, 3650),
            generated_media_keep_days=as_int(data.get("generated_media_keep_days", 30), 30, 0, 3650),
            reverse_cache_keep_days=as_int(data.get("reverse_cache_keep_days", 7), 7, 0, 3650),
        )
