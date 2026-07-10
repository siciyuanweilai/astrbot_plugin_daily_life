from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..models import PreferenceRecord


APPEARANCE_PREFERENCE_CATEGORIES = ("outfit", "hair", "style")
APPEARANCE_PRIORITY_RULE = (
    "优先级：用户当前明确要求 > 短期生活纠偏 > 已学习长期偏好 > 配置审美 > 近期重复抑制 > 模型自由发挥。"
    "配置审美只作为软底色；当前场景不适合或用户纠偏时必须让位。"
)


def _clean_text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _preference_key(item: PreferenceRecord) -> tuple[str, str]:
    return (_clean_text(item.category, 40), _clean_text(item.content))


def default_appearance_preferences(config: Any) -> list[PreferenceRecord]:
    outfit = getattr(config, "outfit", None)
    if not outfit:
        return []
    try:
        weight = float(getattr(outfit, "default_preference_weight", 0.0))
    except (TypeError, ValueError):
        weight = 0.0
    weight = max(0.0, min(weight, 2.0))
    if weight <= 0:
        return []
    seeds = (
        ("outfit", getattr(outfit, "default_style_preference", "")),
        ("hair", getattr(outfit, "default_hair_preference", "")),
    )
    return [
        PreferenceRecord(
            category=category,
            content=text,
            weight=weight,
            evidence="配置审美",
            source="config",
        )
        for category, raw in seeds
        if (text := _clean_text(raw))
    ]


def appearance_preferences(preferences: Iterable[PreferenceRecord]) -> list[PreferenceRecord]:
    categories = set(APPEARANCE_PREFERENCE_CATEGORIES)
    return [item for item in preferences if _clean_text(item.category) in categories]


def _unique_preferences(preferences: Iterable[PreferenceRecord]) -> list[PreferenceRecord]:
    seen: set[tuple[str, str]] = set()
    result: list[PreferenceRecord] = []
    for item in preferences:
        key = _preference_key(item)
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(item)
    result.sort(
        key=lambda item: (
            float(item.weight or 0.0),
            _clean_text(item.last_seen, 40),
            int(item.id or 0),
        ),
        reverse=True,
    )
    return result


def _format_preference_line(item: PreferenceRecord) -> str:
    evidence = _clean_text(item.evidence or item.source, 80)
    suffix = f"，证据：{evidence}" if evidence else ""
    return f"- [{_clean_text(item.category, 40)}] {_clean_text(item.content)} (权重 {float(item.weight or 0.0):.1f}{suffix})"


def format_life_preference_context(
    preferences: Iterable[PreferenceRecord],
    config: Any,
    *,
    limit: int,
    appearance_only: bool = False,
) -> str:
    learned = list(preferences)
    if appearance_only:
        learned = appearance_preferences(learned)
    learned = _unique_preferences(learned)[: max(0, limit)]
    defaults = default_appearance_preferences(config)
    parts = [f"- {APPEARANCE_PRIORITY_RULE}"]
    if learned:
        parts.append("已学习长期偏好：")
        parts.extend(_format_preference_line(item) for item in learned)
    if defaults:
        parts.append("配置审美（由审美影响程度控制；只作为软参考）：")
        parts.extend(_format_preference_line(item) for item in defaults)
    if len(parts) == 1:
        return ""
    return "\n".join(parts)
