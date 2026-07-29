from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .vitals import LifeState, WeatherInfo
from .relations import EventRecord, PlaceRecord


@dataclass(slots=True)
class TimelineItem:
    time: str = ""
    activity: str = ""
    status: str = ""

    @staticmethod
    def from_value(value: Any) -> "TimelineItem":
        if isinstance(value, TimelineItem):
            return value
        raw = value if isinstance(value, dict) else {}
        return TimelineItem(
            time=str(raw.get("time") or "").strip(),
            activity=str(raw.get("activity") or "").strip(),
            status=str(raw.get("status") or "").strip(),
        )

    def as_dict(self) -> dict[str, str]:
        return {"time": self.time, "activity": self.activity, "status": self.status}


@dataclass(slots=True)
class DayRecord:
    date: str
    outfit: str = ""
    timeline: list[TimelineItem] = field(default_factory=list)
    places: list[PlaceRecord] = field(default_factory=list)
    new_events: list[EventRecord] = field(default_factory=list)
    weather: str = ""
    weather_info: WeatherInfo = field(default_factory=WeatherInfo)
    weather_last_update: int = 0
    time_period: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    outfit_history: dict[str, str] = field(default_factory=dict)
    memo: str = ""
    state: LifeState | None = None
    state_log: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "outfit": self.outfit,
            "timeline": [item.as_dict() for item in self.timeline],
            "places": [place.as_dict() for place in self.places],
            "new_events": [event.as_dict() for event in self.new_events],
            "weather": self.weather,
            "weather_info": self.weather_info.as_dict(),
            "time_period": self.time_period,
            "meta": dict(self.meta),
            "outfit_history": dict(self.outfit_history),
            "memo": self.memo,
            "state_log": list(self.state_log),
        }
        if self.weather_last_update:
            result["weather_last_update"] = self.weather_last_update
        if self.state:
            result["state"] = self.state.as_dict()
        return result
