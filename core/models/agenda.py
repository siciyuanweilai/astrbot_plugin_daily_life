from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .relations import EventRecord, PlaceRecord
from .vitals import LifeState, WeatherInfo


@dataclass(slots=True)
class TimelineItem:
    time: str = ""
    activity: str = ""
    status: str = ""
    place: str = ""
    place_kind: str = "none"
    place_scope: str = "local"
    place_city: str = ""
    place_hint: str = ""
    travel_mode: str = ""
    place_address: str = ""
    place_latitude: float | None = None
    place_longitude: float | None = None
    place_coordinate_source: str = ""
    travel_origin: str = ""
    travel_provider: str = ""
    travel_minutes: int = 0
    travel_distance_meters: float = 0.0
    execution_state: str = "planned"
    execution_reason: str = ""
    execution_evidence: str = ""
    execution_updated_at: str = ""

    @staticmethod
    def from_value(value: Any) -> TimelineItem:
        if isinstance(value, TimelineItem):
            return value
        raw = value if isinstance(value, dict) else {}
        execution_state = str(raw.get("execution_state") or "planned").strip().lower()
        if execution_state not in {
            "planned",
            "active",
            "completed",
            "skipped",
            "cancelled",
        }:
            execution_state = "planned"
        return TimelineItem(
            time=str(raw.get("time") or "").strip(),
            activity=str(raw.get("activity") or "").strip(),
            status=str(raw.get("status") or "").strip(),
            place=str(raw.get("place") or "").strip(),
            place_kind=str(raw.get("place_kind") or "none").strip().lower(),
            place_scope=str(raw.get("place_scope") or "local").strip().lower(),
            place_city=str(raw.get("place_city") or "").strip(),
            place_hint=str(raw.get("place_hint") or "").strip(),
            travel_mode=str(raw.get("travel_mode") or "").strip().lower(),
            place_address=str(raw.get("place_address") or "").strip(),
            place_latitude=_optional_float(raw.get("place_latitude")),
            place_longitude=_optional_float(raw.get("place_longitude")),
            place_coordinate_source=str(
                raw.get("place_coordinate_source") or ""
            ).strip(),
            travel_origin=str(raw.get("travel_origin") or "").strip(),
            travel_provider=str(raw.get("travel_provider") or "").strip(),
            travel_minutes=_non_negative_int(raw.get("travel_minutes")),
            travel_distance_meters=_non_negative_float(
                raw.get("travel_distance_meters")
            ),
            execution_state=execution_state,
            execution_reason=str(raw.get("execution_reason") or "").strip(),
            execution_evidence=str(raw.get("execution_evidence") or "").strip(),
            execution_updated_at=str(raw.get("execution_updated_at") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "activity": self.activity,
            "status": self.status,
            "place": self.place,
            "place_kind": self.place_kind,
            "place_scope": self.place_scope,
            "place_city": self.place_city,
            "place_hint": self.place_hint,
            "travel_mode": self.travel_mode,
            "place_address": self.place_address,
            "place_latitude": self.place_latitude,
            "place_longitude": self.place_longitude,
            "place_coordinate_source": self.place_coordinate_source,
            "travel_origin": self.travel_origin,
            "travel_provider": self.travel_provider,
            "travel_minutes": self.travel_minutes,
            "travel_distance_meters": self.travel_distance_meters,
            "execution_state": self.execution_state,
            "execution_reason": self.execution_reason,
            "execution_evidence": self.execution_evidence,
            "execution_updated_at": self.execution_updated_at,
        }


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _non_negative_float(value: Any) -> float:
    number = _optional_float(value)
    return max(0.0, number) if number is not None else 0.0


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass(slots=True)
class DayRecord:
    PERSISTED_FIELDS: ClassVar[tuple[str, ...]] = (
        "outfit",
        "timeline",
        "places",
        "new_events",
        "weather",
        "weather_info",
        "weather_last_update",
        "time_period",
        "meta",
        "outfit_history",
        "memo",
        "state",
        "state_log",
    )

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
    revision: int = 0
    _baseline: dict[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def persistence_snapshot(self) -> dict[str, Any]:
        """返回可安全比较和合并的持久化字段快照。"""

        return {
            name: copy.deepcopy(getattr(self, name)) for name in self.PERSISTED_FIELDS
        }

    def mark_persisted(self, revision: int) -> None:
        """记录当前数据库版本及对应字段基线。"""

        self.revision = max(0, int(revision or 0))
        self._baseline = self.persistence_snapshot()

    def apply_persisted(self, source: DayRecord) -> None:
        """用已保存记录同步当前实例，避免调用方继续持有旧状态。"""

        for name in self.PERSISTED_FIELDS:
            setattr(self, name, copy.deepcopy(getattr(source, name)))
        self.mark_persisted(source.revision)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "revision": self.revision,
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
