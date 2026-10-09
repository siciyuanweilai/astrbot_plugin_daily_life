from __future__ import annotations

import datetime
import uuid
from typing import Any

from ..models import INTERNAL_SIMULATED_ACTION_TYPES
from ..models.coerce import compact_text
from .body import nonnegative


def ready_steps(goal: dict[str, Any]) -> list[dict[str, Any]]:
    if goal.get("status") != "active":
        return []
    steps = goal.get("steps", [])
    completed = {step["id"] for step in steps if step.get("status") == "completed"}
    return [
        step
        for step in steps
        if step.get("status") == "pending"
        and set(step.get("depends_on", [])) <= completed
    ]


def normalize_steps(
    raw: Any, *, completed_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        return []
    steps = []
    for item in raw:
        if not isinstance(item, dict):
            return []
        step_id = compact_text(item.get("id"), 40)
        title = compact_text(item.get("title"), 120)
        action_type = str(item.get("action_type") or "")
        try:
            minutes = int(item.get("required_minutes") or 0)
        except (ValueError, TypeError):
            return []
        dependencies = item.get("depends_on", [])
        if (
            not step_id
            or not title
            or action_type not in INTERNAL_SIMULATED_ACTION_TYPES
            or not 1 <= minutes <= 10080
            or not isinstance(dependencies, list)
        ):
            return []
        steps.append(
            {
                "id": step_id,
                "title": title,
                "action_type": action_type,
                "required_minutes": minutes,
                "practice_minutes": 0.0,
                "depends_on": [str(value) for value in dependencies],
                "status": "pending",
                "evidence": [],
            }
        )
    by_id = {step["id"]: step for step in steps}
    if len(by_id) != len(steps) or set(by_id) & (completed_ids or set()):
        return []
    resolved: set[str] = set(completed_ids or set())
    for _ in steps:
        newly_resolved = {
            key
            for key, step in by_id.items()
            if key not in resolved and set(step["depends_on"]) <= resolved
        }
        if not newly_resolved:
            break
        resolved.update(newly_resolved)
    return steps if set(by_id) <= resolved else []


def apply_goal_decisions(
    world: dict[str, Any],
    payload: dict[str, Any],
    *,
    sources: set[str],
    now: datetime.datetime,
) -> None:
    goals = world.setdefault("goals", [])
    active = [goal for goal in goals if goal.get("status") in {"active", "blocked"}]
    for raw in (
        payload.get("new_goals", [])[:2]
        if isinstance(payload.get("new_goals"), list)
        else []
    ):
        source_id = str(raw.get("source_id") or "") if isinstance(raw, dict) else ""
        if (
            not isinstance(raw, dict)
            or raw.get("owner") != "self"
            or source_id not in sources
            or not (
                source_id == "persona"
                or source_id.startswith(("preference:", "focus:"))
            )
        ):
            continue
        title = compact_text(raw.get("title"), 120)
        reason = compact_text(raw.get("reason"), 240)
        steps = normalize_steps(raw.get("steps"))
        if (
            not title
            or not reason
            or not steps
            or len(active) >= 3
            or any(goal["title"] == title for goal in goals)
        ):
            continue
        goal = {
            "id": uuid.uuid4().hex[:16],
            "title": title,
            "reason": reason,
            "source_id": raw["source_id"],
            "skill": compact_text(raw.get("skill"), 80),
            "status": "active",
            "steps": steps,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "obstacle": "",
        }
        goals.append(goal)
        active.append(goal)
    for raw in (
        payload.get("goal_updates", [])[:3]
        if isinstance(payload.get("goal_updates"), list)
        else []
    ):
        if (
            not isinstance(raw, dict)
            or str(raw.get("evidence_id") or "") not in sources
        ):
            continue
        goal = next((item for item in goals if item["id"] == raw.get("goal_id")), None)
        status = raw.get("status")
        reason = compact_text(raw.get("reason"), 240)
        if (
            goal is None
            or goal.get("status") not in {"active", "blocked"}
            or status not in {"active", "blocked", "abandoned"}
            or not reason
        ):
            continue
        protected = [
            step
            for step in goal["steps"]
            if step.get("status") == "completed" or step.get("practice_minutes")
        ]
        completed_ids = {step["id"] for step in protected}
        replacements = (
            normalize_steps(raw.get("remaining_steps"), completed_ids=completed_ids)
            if raw.get("remaining_steps")
            else []
        )
        if replacements and len(protected) + len(replacements) <= 8:
            # 只调整未开始阶段，任何已发生的练习与证据都原样保留。
            remaining_ids = {step["id"] for step in replacements}
            if all(
                set(step.get("depends_on", [])) <= completed_ids | remaining_ids
                for step in protected
            ):
                goal["steps"] = [*protected, *replacements]
        goal.update(status=status, obstacle=reason, updated_at=now.isoformat())
    current = [goal for goal in goals if goal.get("status") in {"active", "blocked"}]
    closed = [goal for goal in goals if goal.get("status") not in {"active", "blocked"}]
    world["goals"] = [*closed[-(30 - len(current)) :], *current]


def credit_goal(
    world: dict[str, Any], run: dict[str, Any], now: datetime.datetime
) -> None:
    if run.get("goal_credited"):
        return
    goal = next(
        (item for item in world.get("goals", []) if item["id"] == run.get("goal_id")),
        None,
    )
    step = next(
        (
            item
            for item in ready_steps({**(goal or {}), "status": "active"})
            if item["id"] == run.get("step_id")
        ),
        None,
    )
    if (
        goal is None
        or step is None
        or step["action_type"] != run["action"]["action_type"]
    ):
        return
    minutes = nonnegative(run.get("active_seconds")) / 60
    if not minutes:
        return
    step["practice_minutes"] = round(
        min(step["required_minutes"], step["practice_minutes"] + minutes), 2
    )
    step["evidence"].append(
        {
            "action_id": run["action"]["action_id"],
            "minutes": round(minutes, 2),
            "occurred_at": now.isoformat(),
        }
    )
    step["evidence"] = step["evidence"][-60:]
    if step["practice_minutes"] >= step["required_minutes"]:
        step["status"] = "completed"
    if goal.get("status") in {"active", "blocked"} and all(
        item["status"] == "completed" for item in goal["steps"]
    ):
        goal["status"] = "completed"
    goal["updated_at"] = now.isoformat()
    run["goal_credited"] = True
    if goal.get("skill"):
        skill = world.setdefault("skills", {}).setdefault(
            goal["skill"], {"practice_minutes": 0.0, "sessions": 0, "last_evidence": ""}
        )
        skill["practice_minutes"] = round(skill["practice_minutes"] + minutes, 2)
        skill["sessions"] += 1
        skill["last_evidence"] = run["action"]["action_id"]


def goals_context(world: dict[str, Any]) -> str:
    lines = []
    labels = {
        "active": "进行中",
        "blocked": "遇到阻碍",
        "abandoned": "已放弃",
        "completed": "已完成",
    }
    for goal in world.get("goals", [])[-8:]:
        completed = sum(step["status"] == "completed" for step in goal["steps"])
        lines.append(
            f"- {goal['title']}：{labels.get(goal['status'], '待确认')}；阶段 {completed}/{len(goal['steps'])}；{goal.get('obstacle') or goal['reason']}"
        )
        for step in ready_steps(goal)[:2]:
            lines.append(
                f"  可继续阶段 goal_id={goal['id']} step_id={step['id']}：{step['title']}；{step['practice_minutes']}/{step['required_minutes']} 分钟；动作 {step['action_type']}"
            )
    for name, value in world.get("skills", {}).items():
        lines.append(
            f"- {name}：已有 {value['practice_minutes']:.0f} 分钟、{value['sessions']} 次有效练习；不等同于能力认证。"
        )
    return "长期目标与实际成长：\n" + "\n".join(lines) if lines else ""
