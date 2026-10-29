"""复合生活活动按结构化步骤执行，共用回执判断顺序与完成状态。"""

from __future__ import annotations

import json

from ..models import LifeActionIntent


def actions(day) -> list[LifeActionIntent]:
    try:
        raw = json.loads(day.meta.get("planned_life_actions") or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [LifeActionIntent.from_value(item) for item in raw if isinstance(item, dict)]


def settlements(day) -> dict:
    try:
        raw = json.loads(day.meta.get("life_action_settlements") or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def committed_ids(day) -> set[str]:
    return {
        key
        for key, value in settlements(day).items()
        if isinstance(value, dict) and value.get("status") == "committed"
    }


def execution_evidence(day) -> dict:
    """为生活判断提供实际动作与回执，不拿到点的日程冒充执行事实。"""
    try:
        run = json.loads(day.meta.get("continuous_execution") or "{}")
    except (TypeError, ValueError):
        run = {}
    current = {}
    if isinstance(run, dict) and isinstance(run.get("action"), dict):
        current = {
            "action_type": run["action"].get("action_type"),
            "target": run["action"].get("target"),
            "status": run.get("status"),
            "observation_state": run.get("observation_state", "observed"),
            "observed_at": run.get("observed_at", ""),
        }
    outcomes = settlements(day)
    recent = []
    for action in actions(day):
        outcome = outcomes.get(action.action_id)
        if isinstance(outcome, dict) and outcome.get("status") == "committed":
            recent.append(
                {
                    "action_type": action.action_type,
                    "target": action.target,
                    "committed_at": outcome.get("committed_at", ""),
                }
            )
    recent.sort(key=lambda item: str(item["committed_at"]))
    return {"current": current, "recent_completed": recent[-3:]}


def preceding_actions(day, action: LifeActionIntent) -> list[LifeActionIntent]:
    """同一节点或自主行动链内，只允许前序已完成的步骤开始。"""
    result = []
    chain = action.payload.get("execution_chain_id")
    for item in actions(day):
        if item.action_id == action.action_id:
            break
        if (
            action.timeline_index is not None
            and item.timeline_index == action.timeline_index
        ) or (chain and item.payload.get("execution_chain_id") == chain):
            result.append(item)
    return result


def ready(day, action: LifeActionIntent) -> bool:
    completed = committed_ids(day)
    return action.action_id not in completed and all(
        item.action_id in completed for item in preceding_actions(day, action)
    )


def timeline_complete(day, action: LifeActionIntent) -> bool:
    """当前步骤即将提交；其余步骤也有成功回执时才收束整个节点。"""
    completed = committed_ids(day) | {action.action_id}
    return all(
        item.action_id in completed
        for item in actions(day)
        if item.timeline_index == action.timeline_index
    )


async def prepare_new_actions(host, day, steps: list[LifeActionIntent]) -> str:
    """核验自主步骤的结构；库存和地点在每一步实际开始时重新核验。"""
    from .engine import _resolve_planned_outfit_catalog

    raw = [item.as_dict() for item in steps]
    for item in steps:
        if item.action_type in {"move", "travel"}:
            origin = str(item.payload.get("origin") or "").strip()
            destination = str(item.payload.get("destination") or "").strip()
            if not origin or not destination or origin == destination:
                return "移动需要明确且不同的起点和终点"
        if item.action_type != "change_outfit":
            continue
        references = item.payload.get("catalog_reference_ids")
        if not references:
            return "即时换装缺少衣橱引用"
        ids = host._style_catalog_reference_ids(references)
        if not ids:
            return "即时换装缺少衣橱引用"
        available = await host.archive.get_style_catalog_items(
            ids=ids, status="active", limit=len(ids)
        )
        if {entry.id for entry in available} != set(ids):
            return "即时换装引用的衣物已不可用"
    issue = (
        await _resolve_planned_outfit_catalog(
            host,
            {
                "planned_actions": raw,
                "timeline": [item.as_dict() for item in day.timeline],
                "outfit_components": day.meta.get("outfit_components", ""),
            },
            manual_extra=False,
        )
        if any(item.action_type == "change_outfit" for item in steps)
        else ""
    )
    if not issue:
        steps[:] = [LifeActionIntent.from_value(item) for item in raw]
    return issue
