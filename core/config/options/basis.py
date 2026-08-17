from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...prompts import DEFAULT_WEB_TODAY_PROMPT
from .cast import (
    as_bool,
    as_float,
    as_int,
    as_str,
    as_str_list,
    normalize_time,
    normalize_time_window,
)

DEFAULT_CHAT_STYLE_PROMPT = (
    "日常闲聊先接住当下的一句话，不为了显得温柔或有趣而多铺陈。"
    "轻松接话保持短气口，一句只放一个主要意思，能自然停住就停住。"
    "认真问题、事实解释和情绪支持按内容自然展开，先给判断，再补必要原因。"
)
DEFAULT_PUNCTUATION_CLEANUP_CHARS = "，。！？；、,.!?;"

DEFAULT_OUTFIT_PREFERENCE_WEIGHT = 0.0


@dataclass(slots=True)
class WeatherSettings:
    api_key: str = ""
    aware_outfit: bool = True
    aware_activity: bool = True

    @staticmethod
    def from_dict(data: Any) -> WeatherSettings:
        if not isinstance(data, dict):
            return WeatherSettings()
        return WeatherSettings(
            api_key=as_str(data.get("api_key", "")),
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
    def from_dict(data: Any) -> StateSettings:
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
    provider: str = ""
    group_enabled: bool = False
    private_enabled: bool = False
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
    adaptive_feedback_enabled: bool = False

    @staticmethod
    def from_dict(data: Any) -> ProactiveReplySettings:
        if not isinstance(data, dict):
            return ProactiveReplySettings()
        return ProactiveReplySettings(
            provider=as_str(data.get("provider", "")).strip(),
            group_enabled=as_bool(data.get("group_enabled", False), False),
            private_enabled=as_bool(data.get("private_enabled", False), False),
            talk_frequency=as_float(data.get("talk_frequency", 0.35), 0.35, 0.0, 1.0),
            private_talk_frequency=as_float(
                data.get("private_talk_frequency", 0.55), 0.55, 0.0, 1.0
            ),
            idle_minutes=as_int(data.get("idle_minutes", 30), 30, 1, 1440),
            private_idle_minutes=as_int(
                data.get("private_idle_minutes", 60), 60, 5, 1440
            ),
            cooldown_minutes=as_int(data.get("cooldown_minutes", 20), 20, 1, 1440),
            private_cooldown_minutes=as_int(
                data.get("private_cooldown_minutes", 8), 8, 1, 1440
            ),
            min_message_length=as_int(data.get("min_message_length", 4), 4, 1, 200),
            min_confidence=as_float(data.get("min_confidence", 0.72), 0.72, 0.0, 1.0),
            max_reply_length=as_int(data.get("max_reply_length", 80), 80, 10, 300),
            private_revisit_enabled=as_bool(
                data.get("private_revisit_enabled", False), False
            ),
            revisit_interval_minutes=as_int(
                data.get("revisit_interval_minutes", 30), 30, 5, 1440
            ),
            revisit_cooldown_minutes=as_int(
                data.get("revisit_cooldown_minutes", 180), 180, 10, 10080
            ),
            revisit_min_confidence=as_float(
                data.get("revisit_min_confidence", 0.82), 0.82, 0.0, 1.0
            ),
            adaptive_feedback_enabled=as_bool(
                data.get("adaptive_feedback_enabled", False), False
            ),
        )

    def idle_enabled(self) -> bool:
        return self.group_enabled or self.private_enabled


@dataclass(slots=True)
class ResponseGateSettings:
    group_enabled: bool = True
    private_enabled: bool = True
    group_talk_frequency: float = 0.45
    private_talk_frequency: float = 0.65
    min_interval_seconds: int = 12
    no_reply_backoff_seconds: int = 25
    no_reply_backoff_cap_seconds: int = 180
    no_reply_backoff_start_count: int = 2
    bypass_pending_count: int = 5
    media_only_group_frequency: float = 0.2

    @staticmethod
    def from_dict(data: Any) -> ResponseGateSettings:
        if not isinstance(data, dict):
            return ResponseGateSettings()
        return ResponseGateSettings(
            group_enabled=as_bool(data.get("group_enabled", True), True),
            private_enabled=as_bool(data.get("private_enabled", True), True),
            group_talk_frequency=as_float(
                data.get("group_talk_frequency", 0.45), 0.45, 0.0, 1.0
            ),
            private_talk_frequency=as_float(
                data.get("private_talk_frequency", 0.65), 0.65, 0.0, 1.0
            ),
            min_interval_seconds=as_int(
                data.get("min_interval_seconds", 12), 12, 0, 300
            ),
            no_reply_backoff_seconds=as_int(
                data.get("no_reply_backoff_seconds", 25), 25, 0, 600
            ),
            no_reply_backoff_cap_seconds=as_int(
                data.get("no_reply_backoff_cap_seconds", 180), 180, 0, 1800
            ),
            no_reply_backoff_start_count=as_int(
                data.get("no_reply_backoff_start_count", 2), 2, 1, 20
            ),
            bypass_pending_count=as_int(data.get("bypass_pending_count", 5), 5, 0, 50),
            media_only_group_frequency=as_float(
                data.get("media_only_group_frequency", 0.2), 0.2, 0.0, 1.0
            ),
        )


@dataclass(slots=True)
class ChatStyleSettings:
    enabled: bool = True
    casual_short_prompt: str = DEFAULT_CHAT_STYLE_PROMPT
    casual_max_chars: int = 50
    group_casual_max_chars: int = 30
    private_casual_max_chars: int = 15
    proactive_max_chars: int = 15
    continuous_turn_enabled: bool = True
    continuous_turn_wait_seconds: float = 1.5
    continuous_turn_max_wait_seconds: float = 4.0
    continuous_turn_group_enabled: bool = False
    continuous_turn_semantic_enabled: bool = True
    punctuation_cleanup_enabled: bool = True
    punctuation_cleanup_chars: str = DEFAULT_PUNCTUATION_CLEANUP_CHARS
    semantic_provider: str = ""
    semantic_max_segments: int = 10
    semantic_timeout_seconds: float = 8.0
    segment_delay_range: str = "1.5,3.5"
    segment_min_delay_seconds: float = 1.5
    segment_max_delay_seconds: float = 3.5

    @staticmethod
    def from_dict(data: Any) -> ChatStyleSettings:
        if not isinstance(data, dict):
            return ChatStyleSettings()
        raw_range = data.get("segment_delay_range")
        minimum = maximum = None
        if isinstance(raw_range, str):
            parts = [part.strip() for part in raw_range.split(",")]
            if len(parts) == 2:
                minimum = as_float(parts[0], 1.5, 0.0, 8.0)
                maximum = as_float(parts[1], 3.5, 0.0, 8.0)
        if minimum is None or maximum is None:
            minimum, maximum = 1.5, 3.5
        if maximum <= 0:
            minimum = maximum = 0.0
        else:
            minimum, maximum = sorted((minimum, maximum))
        continuous_wait = as_float(
            data.get("continuous_turn_wait_seconds", 1.5), 1.5, 0.0, 3.0
        )
        continuous_max_wait = max(
            continuous_wait,
            as_float(
                data.get("continuous_turn_max_wait_seconds", 4.0),
                4.0,
                1.0,
                8.0,
            ),
        )
        return ChatStyleSettings(
            enabled=as_bool(data.get("enabled", True), True),
            casual_short_prompt=as_str(
                data.get("casual_short_prompt", DEFAULT_CHAT_STYLE_PROMPT),
                DEFAULT_CHAT_STYLE_PROMPT,
            ).strip()
            or DEFAULT_CHAT_STYLE_PROMPT,
            casual_max_chars=as_int(data.get("casual_max_chars", 50), 50, 10, 50),
            group_casual_max_chars=as_int(
                data.get("group_casual_max_chars", 30), 30, 10, 30
            ),
            private_casual_max_chars=as_int(
                data.get("private_casual_max_chars", 15), 15, 10, 30
            ),
            proactive_max_chars=as_int(data.get("proactive_max_chars", 15), 15, 10, 30),
            continuous_turn_enabled=as_bool(
                data.get("continuous_turn_enabled", True), True
            ),
            continuous_turn_wait_seconds=continuous_wait,
            continuous_turn_max_wait_seconds=continuous_max_wait,
            continuous_turn_group_enabled=as_bool(
                data.get("continuous_turn_group_enabled", False), False
            ),
            continuous_turn_semantic_enabled=as_bool(
                data.get("continuous_turn_semantic_enabled", True), True
            ),
            punctuation_cleanup_enabled=as_bool(
                data.get("punctuation_cleanup_enabled", True), True
            ),
            punctuation_cleanup_chars=as_str(
                data.get(
                    "punctuation_cleanup_chars", DEFAULT_PUNCTUATION_CLEANUP_CHARS
                ),
                DEFAULT_PUNCTUATION_CLEANUP_CHARS,
            ),
            semantic_provider=as_str(data.get("semantic_provider", "")).strip(),
            semantic_max_segments=as_int(
                data.get("semantic_max_segments", 10), 10, 1, 10
            ),
            semantic_timeout_seconds=as_float(
                data.get("semantic_timeout_seconds", 8.0),
                8.0,
                1.0,
                20.0,
            ),
            segment_delay_range=f"{minimum:g},{maximum:g}",
            segment_min_delay_seconds=minimum,
            segment_max_delay_seconds=maximum,
        )


@dataclass(slots=True)
class TaskModelSettings:
    provider: str = ""

    @staticmethod
    def from_dict(data: Any) -> TaskModelSettings:
        if not isinstance(data, dict):
            return TaskModelSettings()
        return TaskModelSettings(provider=as_str(data.get("provider", "")).strip())


@dataclass(slots=True)
class OutfitSettings:
    provider: str = ""
    default_style_preference: str = ""
    default_hair_preference: str = ""
    default_preference_weight: float = DEFAULT_OUTFIT_PREFERENCE_WEIGHT

    @staticmethod
    def from_dict(data: Any) -> OutfitSettings:
        if not isinstance(data, dict):
            return OutfitSettings()
        return OutfitSettings(
            provider=as_str(data.get("provider", "")).strip(),
            default_style_preference=as_str(
                data.get("default_style_preference", ""),
                "",
            ).strip(),
            default_hair_preference=as_str(
                data.get("default_hair_preference", ""),
                "",
            ).strip(),
            default_preference_weight=as_float(
                data.get("default_preference_weight", DEFAULT_OUTFIT_PREFERENCE_WEIGHT),
                DEFAULT_OUTFIT_PREFERENCE_WEIGHT,
                0.0,
                2.0,
            ),
        )


@dataclass(slots=True)
class EmojiSettings:
    collect_chat_emojis: bool = False
    max_ready: int = 128
    replace_when_full: bool = True
    max_size_mb: float = 5.0
    send_candidate_limit: int = 30
    review_batch_size: int = 3
    inactive_record_keep_days: int = 30
    orphan_cache_grace_hours: int = 24
    auto_send_enabled: bool = True
    send_on_regular_reply: bool = True
    send_on_proactive_reply: bool = True
    send_on_private_revisit: bool = True
    send_on_commitment: bool = True
    tool_send_enabled: bool = True
    send_cooldown_seconds: int = 0
    recent_sent_exclusion_limit: int = 5
    semantic_candidate_limit: int = 8

    @staticmethod
    def from_dict(data: Any) -> EmojiSettings:
        if not isinstance(data, dict):
            return EmojiSettings()
        return EmojiSettings(
            collect_chat_emojis=as_bool(data.get("collect_chat_emojis", False), False),
            max_ready=as_int(data.get("max_ready", 128), 128, 1, 300),
            replace_when_full=as_bool(data.get("replace_when_full", True), True),
            max_size_mb=as_float(data.get("max_size_mb", 5.0), 5.0, 1.0, 20.0),
            send_candidate_limit=as_int(
                data.get("send_candidate_limit", 30), 30, 1, 50
            ),
            review_batch_size=as_int(data.get("review_batch_size", 3), 3, 1, 10),
            inactive_record_keep_days=as_int(
                data.get("inactive_record_keep_days", 30), 30, 1, 90
            ),
            orphan_cache_grace_hours=as_int(
                data.get("orphan_cache_grace_hours", 24), 24, 1, 168
            ),
            auto_send_enabled=as_bool(data.get("auto_send_enabled", True), True),
            send_on_regular_reply=as_bool(
                data.get("send_on_regular_reply", True), True
            ),
            send_on_proactive_reply=as_bool(
                data.get("send_on_proactive_reply", True), True
            ),
            send_on_private_revisit=as_bool(
                data.get("send_on_private_revisit", True), True
            ),
            send_on_commitment=as_bool(data.get("send_on_commitment", True), True),
            tool_send_enabled=as_bool(data.get("tool_send_enabled", True), True),
            send_cooldown_seconds=as_int(
                data.get("send_cooldown_seconds", 0), 0, 0, 86400
            ),
            recent_sent_exclusion_limit=as_int(
                data.get("recent_sent_exclusion_limit", 5), 5, 0, 30
            ),
            semantic_candidate_limit=as_int(
                data.get("semantic_candidate_limit", 8), 8, 1, 20
            ),
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
    auto_video_understanding: bool = False
    bili_auto_summary: bool = False

    @staticmethod
    def from_dict(data: Any) -> SightSettings:
        if not isinstance(data, dict):
            return SightSettings()
        return SightSettings(
            summary_provider=as_str(data.get("summary_provider", "")).strip(),
            frame_provider=as_str(data.get("frame_provider", "")).strip(),
            total_timeout_seconds=as_int(
                data.get("total_timeout_seconds", 300), 300, 60, 1800
            ),
            max_transcript_chars=as_int(
                data.get("max_transcript_chars", 8000), 8000, 500, 30000
            ),
            max_frames=as_int(data.get("max_frames", 8), 8, 1, 24),
            video_download_max_mb=as_int(
                data.get("video_download_max_mb", 500), 500, 0, 1024
            ),
            video_download_timeout_seconds=as_int(
                data.get("video_download_timeout_seconds", 240), 240, 30, 600
            ),
            video_cache_ttl_hours=as_int(
                data.get("video_cache_ttl_hours", 2), 2, 1, 168
            ),
            video_cache_max_items=as_int(
                data.get("video_cache_max_items", 60), 60, 8, 500
            ),
            sight_cache_keep_days=as_int(
                data.get("sight_cache_keep_days", 7), 7, 1, 30
            ),
            audio_transcript_mode=_normalize_audio_transcript_mode(
                data.get("audio_transcript_mode", "local")
            ),
            local_asr_timeout_seconds=as_int(
                data.get("local_asr_timeout_seconds", 900), 900, 120, 3600
            ),
            note_max_transcript_chars=as_int(
                data.get("note_max_transcript_chars", 20000), 20000, 2000, 60000
            ),
            auto_video_understanding=as_bool(
                data.get("auto_video_understanding", False), False
            ),
            bili_auto_summary=as_bool(data.get("bili_auto_summary", False), False),
        )


def _normalize_audio_transcript_mode(value: Any) -> str:
    text = as_str(value, "local").strip().lower()
    if text in {"bcut", "必剪"}:
        return "bcut"
    return "local"


@dataclass(slots=True)
class SearchSettings:
    enabled: bool = False
    provider: str = ""
    tavily_api_keys: list[str] = field(default_factory=list)
    max_results: int = 8
    max_sources: int = 8
    timeout_seconds: int = 30
    total_timeout_seconds: int = 75
    fetch_timeout_seconds: int = 30
    crawl_timeout_seconds: int = 150
    research_timeout_seconds: int = 300
    research_poll_interval_seconds: int = 3
    cache_ttl_seconds: int = 300
    cache_max_items: int = 128
    deep_max_followups: int = 2
    max_page_chars: int = 12000
    map_max_results: int = 100
    map_max_depth: int = 3
    map_max_breadth: int = 50
    crawl_max_results: int = 50
    crawl_max_depth: int = 3
    crawl_max_breadth: int = 20
    inspiration_enabled: bool = False
    today_prompt: str = DEFAULT_WEB_TODAY_PROMPT

    @staticmethod
    def from_dict(data: Any) -> SearchSettings:
        if not isinstance(data, dict):
            return SearchSettings()
        return SearchSettings(
            enabled=as_bool(data.get("enabled", False), False),
            provider=as_str(data.get("provider", "")).strip(),
            tavily_api_keys=as_str_list(data.get("tavily_api_keys", [])),
            max_results=as_int(data.get("max_results", 8), 8, 1, 20),
            max_sources=as_int(data.get("max_sources", 8), 8, 1, 20),
            timeout_seconds=as_int(data.get("timeout_seconds", 30), 30, 5, 120),
            total_timeout_seconds=as_int(
                data.get("total_timeout_seconds", 75), 75, 10, 300
            ),
            fetch_timeout_seconds=as_int(
                data.get("fetch_timeout_seconds", 30), 30, 5, 180
            ),
            crawl_timeout_seconds=as_int(
                data.get("crawl_timeout_seconds", 150), 150, 10, 180
            ),
            research_timeout_seconds=as_int(
                data.get("research_timeout_seconds", 300), 300, 30, 900
            ),
            research_poll_interval_seconds=as_int(
                data.get("research_poll_interval_seconds", 3), 3, 1, 30
            ),
            cache_ttl_seconds=as_int(data.get("cache_ttl_seconds", 300), 300, 0, 86400),
            cache_max_items=as_int(data.get("cache_max_items", 128), 128, 16, 512),
            deep_max_followups=as_int(data.get("deep_max_followups", 2), 2, 0, 3),
            max_page_chars=as_int(
                data.get("max_page_chars", 12000), 12000, 2000, 30000
            ),
            map_max_results=as_int(data.get("map_max_results", 100), 100, 1, 500),
            map_max_depth=as_int(data.get("map_max_depth", 3), 3, 1, 5),
            map_max_breadth=as_int(data.get("map_max_breadth", 50), 50, 1, 500),
            crawl_max_results=as_int(data.get("crawl_max_results", 50), 50, 1, 500),
            crawl_max_depth=as_int(data.get("crawl_max_depth", 3), 3, 1, 5),
            crawl_max_breadth=as_int(data.get("crawl_max_breadth", 20), 20, 1, 500),
            inspiration_enabled=as_bool(data.get("inspiration_enabled", False), False),
            today_prompt=(
                as_str(
                    data.get("today_prompt", DEFAULT_WEB_TODAY_PROMPT),
                    DEFAULT_WEB_TODAY_PROMPT,
                ).strip()
                or DEFAULT_WEB_TODAY_PROMPT
            ),
        )


@dataclass(slots=True)
class LifecycleSettings:
    provider: str = ""
    review_time: str = "23:45"
    max_preferences: int = 16

    @staticmethod
    def from_dict(data: Any) -> LifecycleSettings:
        if not isinstance(data, dict):
            return LifecycleSettings()
        return LifecycleSettings(
            provider=as_str(data.get("provider", "")).strip(),
            review_time=normalize_time(data.get("review_time", "23:45"), "23:45"),
            max_preferences=as_int(data.get("max_preferences", 16), 16, 0, 50),
        )


@dataclass(slots=True)
class StorageSettings:
    domains_keep_days: int = 180
    cognition_keep_days: int = 30
    daily_keep_days: int = 30
    relationships_keep_days: int = 0
    world_keep_days: int = 0
    conversation_keep_days: int = 180
    experience_keep_days: int = 0
    expression_keep_days: int = 0
    media_keep_days: int = 30
    longterm_keep_days: int = 0
    review_keep_days: int = 120
    planning_keep_days: int = 180
    generated_media_keep_days: int = 30
    reverse_cache_keep_days: int = 7

    @staticmethod
    def from_dict(data: Any) -> StorageSettings:
        if not isinstance(data, dict):
            return StorageSettings()
        return StorageSettings(
            domains_keep_days=as_int(data.get("domains_keep_days", 180), 180, 0, 3650),
            cognition_keep_days=as_int(
                data.get("cognition_keep_days", 30), 30, 0, 3650
            ),
            daily_keep_days=as_int(data.get("daily_keep_days", 30), 30, 0, 3650),
            relationships_keep_days=as_int(
                data.get("relationships_keep_days", 0), 0, 0, 3650
            ),
            world_keep_days=as_int(data.get("world_keep_days", 0), 0, 0, 3650),
            conversation_keep_days=as_int(
                data.get("conversation_keep_days", 180), 180, 0, 3650
            ),
            experience_keep_days=as_int(
                data.get("experience_keep_days", 0), 0, 0, 3650
            ),
            expression_keep_days=as_int(
                data.get("expression_keep_days", 0), 0, 0, 3650
            ),
            media_keep_days=as_int(data.get("media_keep_days", 30), 30, 0, 3650),
            longterm_keep_days=as_int(data.get("longterm_keep_days", 0), 0, 0, 3650),
            review_keep_days=as_int(data.get("review_keep_days", 120), 120, 0, 3650),
            planning_keep_days=as_int(
                data.get("planning_keep_days", 180), 180, 0, 3650
            ),
            generated_media_keep_days=as_int(
                data.get("generated_media_keep_days", 30), 30, 0, 3650
            ),
            reverse_cache_keep_days=as_int(
                data.get("reverse_cache_keep_days", 7), 7, 0, 3650
            ),
        )
