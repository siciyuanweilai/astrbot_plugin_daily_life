from __future__ import annotations

import datetime
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

from ..models import DayRecord


def score(value: Any, default: float = 50.0) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return default
    return round(max(0.0, min(100.0, number)), 4) if math.isfinite(number) else default


def instant(value: Any) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def nonnegative(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return default
    return max(0.0, number) if math.isfinite(number) else default


@dataclass(slots=True)
class ContinuousBody:
    energy: float = 65.0
    sleep_pressure: float = 30.0
    fatigue: float = 20.0
    hunger: float = 25.0
    thirst: float = 20.0
    social_battery: float = 65.0
    updated_at: str = ""
    uncertain_minutes: float = 0.0

    @classmethod
    def from_value(cls, raw: Any, day: DayRecord | None = None) -> ContinuousBody:
        raw = raw if isinstance(raw, dict) else {}
        state = day.state if day else None
        return cls(
            energy=score(raw.get("energy"), score(getattr(state, "energy", None), 65)),
            sleep_pressure=score(
                raw.get("sleep_pressure"), score(getattr(state, "sleepiness", None), 30)
            ),
            fatigue=score(raw.get("fatigue"), 20),
            hunger=score(raw.get("hunger"), 25),
            thirst=score(raw.get("thirst"), 20),
            social_battery=score(
                raw.get("social_battery"),
                score(getattr(state, "interaction_capacity", None), 65),
            ),
            updated_at=str(raw.get("updated_at") or ""),
            uncertain_minutes=nonnegative(raw.get("uncertain_minutes")),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def advance(
        self,
        minutes: float,
        *,
        activity: str = "idle",
        sleeping: bool = False,
        intensity: float = 1.0,
    ) -> None:
        minutes = nonnegative(minutes)
        effort = max(1.0, min(5.0, nonnegative(intensity, 1)))
        resting = activity == "rest"
        demanding = activity in {
            "work",
            "study",
            "exercise",
            "chore",
            "cook",
            "move",
            "travel",
        }
        energy_rate = (
            0.12
            if sleeping
            else 0.04
            if resting
            else -0.035 * effort
            if demanding
            else -0.015
        )
        fatigue_rate = (
            -0.12
            if sleeping
            else -0.06
            if resting
            else 0.035 * effort
            if demanding
            else -0.005
        )
        social_rate = (
            -0.08
            if activity in {"chat", "social"}
            else 0.045
            if resting or sleeping
            else 0.015
        )
        for field, rate in (
            ("energy", energy_rate),
            ("sleep_pressure", -0.17 if sleeping else 0.065),
            ("fatigue", fatigue_rate),
            ("hunger", 0.03 if sleeping else 0.08),
            (
                "thirst",
                0.03 if sleeping else 0.10 + (0.035 * effort if demanding else 0),
            ),
            ("social_battery", social_rate),
        ):
            setattr(self, field, score(getattr(self, field) + rate * minutes))

    def complete(self, action_type: str, payload: dict[str, Any]) -> None:
        if action_type in {"meal", "cook", "order_food"}:
            self.hunger = score(self.hunger - 65)
            self.energy = score(self.energy + min(8, max(0, 100 - self.energy) / 8))
        if action_type == "drink":
            volume = min(1000, nonnegative(payload.get("volume_ml"), 250))
            self.thirst = score(self.thirst - min(80, volume * 0.26))


def project_body(day: DayRecord, body: ContinuousBody) -> None:
    if day.state is not None:
        day.state.energy = round(body.energy)
        day.state.sleepiness = round(body.sleep_pressure)
        day.state.interaction_capacity = round(body.social_battery)
        day.state.physiological_rhythm.social_battery = round(body.social_battery)
    day.meta["continuous_body"] = json.dumps(body.as_dict(), ensure_ascii=False)


def body_context(raw: Any) -> str:
    if not isinstance(raw, dict) or not raw.get("updated_at"):
        return ""
    body = ContinuousBody.from_value(raw)
    return (
        f"持续身体需求（0-100）：体力 {body.energy:.0f}；困倦 {body.sleep_pressure:.0f}；"
        f"疲劳 {body.fatigue:.0f}；饥饿 {body.hunger:.0f}；口渴 {body.thirst:.0f}；"
        f"社交电量 {body.social_battery:.0f}。这些是时间和已执行动作的累积结果，"
        "只用于自然判断当前感受与行动，不是疾病或必须向用户汇报的内容。"
    )
