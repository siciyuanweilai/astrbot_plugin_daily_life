"""自主决策的紧凑投影；同一事实只发送一次，稳定资料排在实时信息之前。"""

from __future__ import annotations

import hashlib
import json

from ..contextbudget import compact_json


def decision_prompt_context(context: dict) -> str:
    """较稳定的角色资料在前，实时事实在后；嵌套字段仍用稳定编码。"""
    keys = dict.fromkeys(("persona", "preferences", "focus", "self_model", *sorted(context)))
    return "{" + ",".join(
        f"{compact_json(key)}:{compact_json(context[key])}"
        for key in keys if key in context
    ) + "}"


def decision_observation(day, world: dict) -> dict:
    """仅保留会改变行动选择的事实，不让时钟、普通聊天或身体微小波动触发重评。"""
    run = world.get("run") or {}
    rhythm = day.state.physiological_rhythm if day.state else None
    facts = {
        "date": day.date,
        "timeline": [
            {key: getattr(item, key, None) for key in (
                "time", "activity", "activity_kind", "place", "place_kind",
                "duration_minutes", "execution_state",
            )}
            for item in day.timeline
        ],
        "plan": day.meta.get("planned_life_actions", ""),
        "deferred": {key: day.meta.get(key, "") for key in (
            "rest_delay_date", "rest_delay_targets", "rest_delay_until",
        )},
        "place": day.meta.get("current_place", ""),
        "outfit": day.outfit,
        "outfit_ids": day.meta.get("style_catalog_reference_ids", ""),
        "condition": {key: getattr(rhythm.body_condition, key, None) for key in (
            "label", "kind", "phase", "burden_present", "intensity",
        )} if rhythm else {},
        "attention": {key: getattr(day.state, key, "") for key in (
            "watch_state", "interrupt_level",
        )},
        "run": {key: run.get(key) for key in (
            "status", "action", "rest_kind", "observation_state",
        )},
    }
    return {
        "context_key": hashlib.sha256(json.dumps(
            facts, ensure_ascii=False, sort_keys=True, default=str,
        ).encode()).hexdigest(),
        "body": {key: (world.get("body") or {}).get(key, 0) for key in (
            "energy", "sleep_pressure", "fatigue", "hunger", "thirst",
            "social_battery", "discomfort",
        )},
    }


def decision_observation_changed(previous: dict | None, current: dict) -> bool:
    if not previous or previous.get("context_key") != current["context_key"]:
        return True
    # 这里只触发模型复核，不由分数代替生活判断、安排睡眠或诊断身体。
    return any(
        abs(value - previous.get("body", {}).get(key, value)) >= 10
        for key, value in current["body"].items()
    )


def execution_context(world: dict) -> dict:
    kernel = world.get("kernel") or {}
    return {
        "attention": kernel.get("attention") or {},
        "reflection": kernel.get("reflection") or {},
        "recent_events": [
            {key: item.get(key) for key in ("id", "kind", "at", "summary", "evidence_ids")}
            for item in kernel.get("events", [])[-4:] if isinstance(item, dict)
        ],
        "body_observations": world.get("body_observations", [])[-4:],
    }


def skill_context(world: dict) -> dict:
    return {
        name: {key: value for key, value in skill.items() if key != "assessments"}
              | {"assessments": skill.get("assessments", [])[-2:]}
        for name, skill in (world.get("skills") or {}).items() if isinstance(skill, dict)
    }


def run_context(world: dict) -> dict | None:
    run = world.get("run")
    if not isinstance(run, dict):
        return None
    return {key: run[key] for key in (
        "status", "action", "started_at", "active_seconds", "changed_at", "resumed_at",
        "goal_id", "step_id", "rest_kind", "reason",
    ) if key in run}
