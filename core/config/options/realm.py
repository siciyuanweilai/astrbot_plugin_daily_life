from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cast import as_bool, as_int, as_str


@dataclass(slots=True)
class LifeDomainSettings:
    """生活领域运行设置。"""

    enabled: bool = True
    activity_tracking_enabled: bool = True
    simulate_internal_actions: bool = True
    location_enabled: bool = True
    home_address: str = ""
    amap_api_key: str = ""
    default_travel_minutes: int = 15
    meals_enabled: bool = True
    pantry_enabled: bool = True
    chores_enabled: bool = True
    fitness_enabled: bool = True
    conversation_actions_enabled: bool = True
    context_budget_enabled: bool = True
    context_budget_chars: int = 2400

    @staticmethod
    def from_dict(data: Any) -> LifeDomainSettings:
        raw = data if isinstance(data, dict) else {}
        return LifeDomainSettings(
            enabled=as_bool(raw.get("enabled"), True),
            activity_tracking_enabled=as_bool(
                raw.get("activity_tracking_enabled"), True
            ),
            simulate_internal_actions=as_bool(
                raw.get("simulate_internal_actions"), True
            ),
            location_enabled=as_bool(raw.get("location_enabled"), True),
            home_address=as_str(raw.get("home_address", "")).strip(),
            amap_api_key=as_str(raw.get("amap_api_key", "")).strip(),
            default_travel_minutes=as_int(
                raw.get("default_travel_minutes", 15), 15, 1, 240
            ),
            meals_enabled=as_bool(raw.get("meals_enabled"), True),
            pantry_enabled=as_bool(raw.get("pantry_enabled"), True),
            chores_enabled=as_bool(raw.get("chores_enabled"), True),
            fitness_enabled=as_bool(raw.get("fitness_enabled"), True),
            conversation_actions_enabled=as_bool(
                raw.get("conversation_actions_enabled"), True
            ),
            context_budget_enabled=as_bool(raw.get("context_budget_enabled"), True),
            context_budget_chars=as_int(
                raw.get("context_budget_chars", 2400), 2400, 600, 12000
            ),
        )


__all__ = ["LifeDomainSettings"]
