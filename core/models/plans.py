from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_float, optional_int


@dataclass(slots=True)
class WeekPlanRecord:
    week_id: str = ""
    theme: str = ""
    goals: list[str] = field(default_factory=list)
    daily_hints: dict[str, str] = field(default_factory=dict)
    suggested_activities: dict[str, list[str]] = field(default_factory=dict)
    template_id: str = ""
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
            template_id=str(raw.get("template_id") or "").strip(),
            generated=bool(raw.get("generated")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme,
            "goals": list(self.goals),
            "daily_hints": dict(self.daily_hints),
            "suggested_activities": {key: list(value) for key, value in self.suggested_activities.items()},
            "template_id": self.template_id,
            "generated": self.generated,
        }


@dataclass(slots=True)
class WeekTemplateRecord:
    template_id: str = ""
    name: str = ""
    description: str = ""
    emoji: str = ""
    weight: float = 0.1
    enabled: bool = True
    cooldown_weeks: int = 3
    source: str = "custom"
    goals: list[str] = field(default_factory=list)
    daily_hints: dict[str, str] = field(default_factory=dict)
    suggested_activities: dict[str, list[str]] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    @staticmethod
    def from_value(value: Any, template_id: str = "") -> "WeekTemplateRecord | None":
        raw = value if isinstance(value, dict) else {}
        tid = str(raw.get("template_id") or template_id or "").strip()
        name = str(raw.get("name") or "").strip()
        if not tid or not name:
            return None
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
        goals = raw.get("goals", [])
        tags = raw.get("tags", [])
        raw_weight = optional_float(raw.get("weight"))
        raw_cooldown = optional_int(raw.get("cooldown_weeks"))
        return WeekTemplateRecord(
            template_id=tid,
            name=name,
            description=str(raw.get("description") or name).strip(),
            emoji=str(raw.get("emoji") or "📅").strip(),
            weight=raw_weight if raw_weight is not None else 0.1,
            enabled=bool(raw.get("enabled", True)),
            cooldown_weeks=raw_cooldown if raw_cooldown is not None else 3,
            source=str(raw.get("source") or "custom").strip(),
            goals=[str(goal).strip() for goal in goals if str(goal).strip()] if isinstance(goals, list) else [],
            daily_hints={str(key): str(value).strip() for key, value in hints.items()} if isinstance(hints, dict) else {},
            suggested_activities=suggestions,
            tags=[str(tag).strip() for tag in tags if str(tag).strip()] if isinstance(tags, list) else [],
        )

    def as_template_dict(self) -> dict[str, Any]:
        return {
            "emoji": self.emoji,
            "name": self.name,
            "description": self.description,
            "theme": f"{self.emoji} {self.name}".strip(),
            "goals": list(self.goals),
            "daily_hints": dict(self.daily_hints),
            "suggested_activities": {key: list(value) for key, value in self.suggested_activities.items()},
            "weight": self.weight,
            "enabled": self.enabled,
            "cooldown_weeks": self.cooldown_weeks,
            "source": self.source,
            "tags": list(self.tags),
        }

    def as_dict(self) -> dict[str, Any]:
        result = self.as_template_dict()
        result.update(
            {
                "template_id": self.template_id,
                "enabled": self.enabled,
            }
        )
        return result
