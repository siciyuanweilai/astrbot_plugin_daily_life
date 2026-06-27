from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...prompts import (
    DEFAULT_WEB_MATERIAL_PROMPT,
    DEFAULT_WEB_OUTFIT_PROMPT,
    DEFAULT_WEB_TODAY_PROMPT,
    DEFAULT_WEB_WEEK_TEMPLATE_PROMPT,
)
from .parse import as_bool, as_float, as_int, as_str, normalize_time, normalize_time_window


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
class TaskModelSettings:
    provider: str = ""

    @staticmethod
    def from_dict(data: Any) -> "TaskModelSettings":
        if not isinstance(data, dict):
            return TaskModelSettings()
        return TaskModelSettings(provider=as_str(data.get("provider", "")).strip())


@dataclass(slots=True)
class WebInspirationSettings:
    enabled: bool = False
    max_results: int = 3
    timeout_seconds: int = 10
    material_prompt: str = DEFAULT_WEB_MATERIAL_PROMPT
    outfit_prompt: str = DEFAULT_WEB_OUTFIT_PROMPT
    week_template_prompt: str = DEFAULT_WEB_WEEK_TEMPLATE_PROMPT
    today_prompt: str = DEFAULT_WEB_TODAY_PROMPT

    @staticmethod
    def from_dict(data: Any) -> "WebInspirationSettings":
        if not isinstance(data, dict):
            return WebInspirationSettings()
        return WebInspirationSettings(
            enabled=as_bool(data.get("enabled", False), False),
            max_results=as_int(data.get("max_results", 3), 3, 1, 8),
            timeout_seconds=as_int(data.get("timeout_seconds", 10), 10, 3, 30),
            material_prompt=(
                as_str(data.get("material_prompt", DEFAULT_WEB_MATERIAL_PROMPT), DEFAULT_WEB_MATERIAL_PROMPT).strip()
                or DEFAULT_WEB_MATERIAL_PROMPT
            ),
            outfit_prompt=(
                as_str(data.get("outfit_prompt", DEFAULT_WEB_OUTFIT_PROMPT), DEFAULT_WEB_OUTFIT_PROMPT).strip()
                or DEFAULT_WEB_OUTFIT_PROMPT
            ),
            week_template_prompt=(
                as_str(
                    data.get("week_template_prompt", DEFAULT_WEB_WEEK_TEMPLATE_PROMPT),
                    DEFAULT_WEB_WEEK_TEMPLATE_PROMPT,
                ).strip()
                or DEFAULT_WEB_WEEK_TEMPLATE_PROMPT
            ),
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
    workshop_keep_days: int = 0

    @staticmethod
    def from_dict(data: Any) -> "StorageSettings":
        if not isinstance(data, dict):
            return StorageSettings()
        return StorageSettings(
            daily_keep_days=as_int(data.get("daily_keep_days", 30), 30, 0, 3650),
            review_keep_days=as_int(data.get("review_keep_days", 120), 120, 0, 3650),
            memory_keep_days=as_int(data.get("memory_keep_days", 0), 0, 0, 3650),
            planning_keep_days=as_int(data.get("planning_keep_days", 180), 180, 0, 3650),
            workshop_keep_days=as_int(data.get("workshop_keep_days", 0), 0, 0, 3650),
        )
