from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WeekPlanRecord:
    week_id: str = ""
    theme: str = ""
    goals: list[str] = field(default_factory=list)
    daily_hints: dict[str, str] = field(default_factory=dict)
    suggested_activities: dict[str, list[str]] = field(default_factory=dict)
    generated: bool = False

    @staticmethod
    def from_value(value: Any, week_id: str = "") -> "WeekPlanRecord":
        if isinstance(value, WeekPlanRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        suggestions: dict[str, list[str]] = {}
        raw_suggestions = raw.get("suggested_activities", {})
        if isinstance(raw_suggestions, dict):
            for key, items in raw_suggestions.items():
                if isinstance(items, str):
                    items = [items]
                elif not isinstance(items, list):
                    items = [items]
                suggestions[str(key)] = [str(item).strip() for item in items if str(item).strip()]
        hints = raw.get("daily_hints", {})
        return WeekPlanRecord(
            week_id=week_id,
            theme=str(raw.get("theme") or "").strip(),
            goals=[str(goal).strip() for goal in raw.get("goals", []) if str(goal).strip()]
            if isinstance(raw.get("goals", []), list)
            else [],
            daily_hints={str(key): str(value).strip() for key, value in hints.items()} if isinstance(hints, dict) else {},
            suggested_activities=suggestions,
            generated=bool(raw.get("generated")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme,
            "goals": list(self.goals),
            "daily_hints": dict(self.daily_hints),
            "suggested_activities": {key: list(value) for key, value in self.suggested_activities.items()},
            "generated": self.generated,
        }
