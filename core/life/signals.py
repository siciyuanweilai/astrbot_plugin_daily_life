from __future__ import annotations

from typing import Any

from ..models import LifeState, PhysiologicalRhythmLogRecord


def _score(value: Any, default: int = 0) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(0, min(number, 100))


def _text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def physiological_rhythm_log_from_state(
    state: LifeState | dict | None,
    *,
    date: str,
    source: str,
) -> PhysiologicalRhythmLogRecord | None:
    if isinstance(state, LifeState):
        state_data = state.as_dict()
    elif isinstance(state, dict):
        state_data = state
    else:
        state_data = {}
    rhythm = state_data.get("physiological_rhythm")
    if not isinstance(rhythm, dict):
        return None

    body = rhythm.get("body_condition") if isinstance(rhythm.get("body_condition"), dict) else {}
    cycle = rhythm.get("optional_cycle") if isinstance(rhythm.get("optional_cycle"), dict) else {}
    recovery_actions = rhythm.get("recovery_actions") if isinstance(rhythm.get("recovery_actions"), list) else []
    body_label = _text(body.get("label"), 80)
    energy_curve = _text(rhythm.get("energy_curve"), 120)
    attention_state = _text(rhythm.get("attention_state"), 80)
    summary = _text(rhythm.get("summary"), 160)
    optional_cycle_label = _text(cycle.get("label"), 80) if cycle.get("enabled") else ""
    if not any((energy_curve, body_label, recovery_actions, attention_state, summary, optional_cycle_label)):
        return None

    return PhysiologicalRhythmLogRecord(
        date=_text(date, 20),
        source=_text(source or "state", 40) or "state",
        energy_curve=energy_curve,
        body_label=body_label,
        body_intensity=_score(body.get("intensity"), 0),
        body_source=_text(body.get("source"), 80),
        body_expires_at=_text(body.get("expires_at"), 40),
        recovery_actions=[_text(item, 50) for item in recovery_actions if _text(item, 50)][:6],
        social_battery=_score(rhythm.get("social_battery"), 50),
        attention_state=attention_state,
        optional_cycle_enabled=bool(cycle.get("enabled")),
        optional_cycle_label=optional_cycle_label,
        optional_cycle_intensity=_score(cycle.get("intensity"), 0),
        optional_cycle_source=_text(cycle.get("source"), 80),
        summary=summary,
        lifecycle_kind="transient",
        status="active",
    )
