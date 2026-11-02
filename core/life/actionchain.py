"""复合生活活动按结构化步骤执行，共用回执判断顺序与完成状态。"""

from __future__ import annotations

import json

from ..models import LifeActionIntent, LifeActionOutcome


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


def committed_result(day, action_id: str) -> LifeActionOutcome | None:
    """成功结算是动作事实；重放不再使用当前地点、库存或衣橱校验。"""
    raw = settlements(day).get(action_id)
    if not isinstance(raw, dict) or raw.get("status") != "committed":
        return None
    outcome = LifeActionOutcome.from_value(raw)
    outcome.replayed = True
    return outcome


def execution_evidence(day) -> dict:
    """为生活判断提供实际动作与回执，不拿到点的日程冒充执行事实。"""
    from .locationfacts import completed_action_description
    try:
        run = json.loads(day.meta.get("continuous_execution") or "{}")
    except (TypeError, ValueError):
        run = {}
    current = {}
    if isinstance(run, dict) and isinstance(run.get("action"), dict):
        current = {
            "action_id": run["action"].get("action_id"),
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
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "target": completed_action_description(action),
                    "committed_at": outcome.get("committed_at", ""),
                    "location": {key: action.payload[key] for key in ("origin", "destination", "place")
                                 if action.payload.get(key)},
                    "next_intention": action.payload.get("next_step", ""),
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


def dependency_issue(day, action: LifeActionIntent) -> str:
    """依赖只认稳定动作编号的成功回执，取消、到点和文案都不能放行。"""
    ids = action.payload.get("depends_on_action_ids", [])
    if not isinstance(ids, list) or any(not isinstance(key, str) or not key for key in ids):
        return "动作依赖格式无效"
    completed = committed_ids(day)
    if any(key not in completed for key in ids):
        return "等待所依赖动作的完成回执"
    if any(item.action_id not in completed for item in preceding_actions(day, action)):
        return "同一活动的前序步骤尚未完成"
    return ""


def ready(day, action: LifeActionIntent) -> bool:
    return action.action_id not in committed_ids(day) and not dependency_issue(day, action)


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
    from .locationfacts import action_chain_place_issue

    if issue := action_chain_place_issue(day, steps):
        return issue

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
