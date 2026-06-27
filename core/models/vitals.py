from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_float, optional_int


@dataclass(slots=True)
class SleepState:
    quality: int | None = None
    depth: str = ""
    summary: str = ""

    @staticmethod
    def from_value(value: Any) -> "SleepState":
        raw = value if isinstance(value, dict) else {}
        quality = raw.get("quality")
        return SleepState(
            quality=int(quality) if isinstance(quality, (int, float)) else None,
            depth=str(raw.get("depth") or "").strip(),
            summary=str(raw.get("summary") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.quality is not None:
            result["quality"] = self.quality
        if self.depth:
            result["depth"] = self.depth
        if self.summary:
            result["summary"] = self.summary
        return result


@dataclass(slots=True)
class LifeState:
    energy: int | None = None
    mood: str = ""
    mood_score: int | None = None
    busyness: int | None = None
    social: int | None = None
    stress: int | None = None
    focus: int | None = None
    sleepiness: int | None = None
    outgoing: int | None = None
    emotional_stability: int | None = None
    interaction_capacity: int | None = None
    boredom: int | None = None
    fishing: int | None = None
    attention_openness: int | None = None
    watch_state: str = ""
    interrupt_level: str = ""
    interrupt_reason: str = ""
    sleep: SleepState = field(default_factory=SleepState)
    summary: str = ""
    updated_at: str = ""
    source: str = ""

    @staticmethod
    def from_value(value: Any) -> "LifeState | None":
        if isinstance(value, LifeState):
            return value
        if not isinstance(value, dict):
            return None
        return LifeState(
            energy=optional_int(value.get("energy")),
            mood=str(value.get("mood") or "").strip(),
            mood_score=optional_int(value.get("mood_score")),
            busyness=optional_int(value.get("busyness")),
            social=optional_int(value.get("social")),
            stress=optional_int(value.get("stress")),
            focus=optional_int(value.get("focus")),
            sleepiness=optional_int(value.get("sleepiness")),
            outgoing=optional_int(value.get("outgoing")),
            emotional_stability=optional_int(value.get("emotional_stability")),
            interaction_capacity=optional_int(value.get("interaction_capacity")),
            boredom=optional_int(value.get("boredom")),
            fishing=optional_int(value.get("fishing")),
            attention_openness=optional_int(value.get("attention_openness")),
            watch_state=str(value.get("watch_state") or "").strip(),
            interrupt_level=str(value.get("interrupt_level") or "").strip(),
            interrupt_reason=str(value.get("interrupt_reason") or "").strip(),
            sleep=SleepState.from_value(value.get("sleep")),
            summary=str(value.get("summary") or "").strip(),
            updated_at=str(value.get("updated_at") or "").strip(),
            source=str(value.get("source") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in (
            "energy",
            "mood",
            "mood_score",
            "busyness",
            "social",
            "stress",
            "focus",
            "sleepiness",
            "outgoing",
            "emotional_stability",
            "interaction_capacity",
            "boredom",
            "fishing",
            "attention_openness",
            "watch_state",
            "interrupt_level",
            "interrupt_reason",
            "summary",
            "updated_at",
            "source",
        ):
            value = getattr(self, key)
            if value is not None and value != "":
                result[key] = value
        sleep = self.sleep.as_dict()
        if sleep:
            result["sleep"] = sleep
        return result


@dataclass(slots=True)
class WeatherInfo:
    raw: str = ""
    temp: float | None = None
    condition: str = ""
    is_hot: bool = False
    is_warm: bool = False
    is_cool: bool = False
    is_cold: bool = False
    is_rainy: bool = False
    is_sunny: bool = False
    is_cloudy: bool = False
    is_foggy: bool = False
    outfit_hint: str = ""
    activity_hint: str = ""
    temp_desc: str = ""

    @staticmethod
    def from_value(value: Any) -> "WeatherInfo":
        raw = value if isinstance(value, dict) else {}
        return WeatherInfo(
            raw=str(raw.get("raw") or "").strip(),
            temp=optional_float(raw.get("temp")),
            condition=str(raw.get("condition") or "").strip(),
            is_hot=bool(raw.get("is_hot")),
            is_warm=bool(raw.get("is_warm")),
            is_cool=bool(raw.get("is_cool")),
            is_cold=bool(raw.get("is_cold")),
            is_rainy=bool(raw.get("is_rainy")),
            is_sunny=bool(raw.get("is_sunny")),
            is_cloudy=bool(raw.get("is_cloudy")),
            is_foggy=bool(raw.get("is_foggy")),
            outfit_hint=str(raw.get("outfit_hint") or "").strip(),
            activity_hint=str(raw.get("activity_hint") or "").strip(),
            temp_desc=str(raw.get("temp_desc") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "temp": self.temp,
            "condition": self.condition,
            "is_hot": self.is_hot,
            "is_warm": self.is_warm,
            "is_cool": self.is_cool,
            "is_cold": self.is_cold,
            "is_rainy": self.is_rainy,
            "is_sunny": self.is_sunny,
            "is_cloudy": self.is_cloudy,
            "is_foggy": self.is_foggy,
            "outfit_hint": self.outfit_hint,
            "activity_hint": self.activity_hint,
            "temp_desc": self.temp_desc,
        }
