"""动作与地点的结构化约束；只采用字段和执行回执，不从叙述猜地点。"""

from __future__ import annotations

import json

from ..clock import local_datetime
from ..models import DayRecord, LifeActionIntent, PlaceRecord

_PHYSICAL_PLACE_ACTIONS = frozenset({
    "rest", "drink", "meal", "cook", "order_food", "purchase", "move", "travel",
    "work", "study", "chore", "exercise", "groom", "change_outfit",
})


def completed_action_description(action) -> str:
    """移动回执只证明已到达的终点，目标文案中后续的意向不升级为经历。"""
    raw = action.as_dict() if isinstance(action, LifeActionIntent) else action
    if not isinstance(raw, dict):
        return ""
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    if raw.get("action_type") in {"move", "travel"} and payload.get("origin") and payload.get("destination"):
        return f"从{payload['origin']}到达{payload['destination']}"
    return str(raw.get("target") or raw.get("action_type") or "")


def action_place(day: DayRecord, action: LifeActionIntent) -> str:
    moving = action.action_type in {"move", "travel"}
    value = action.payload.get("destination" if moving else "place")
    if not value and action.timeline_index is not None:
        if 0 <= action.timeline_index < len(day.timeline):
            value = day.timeline[action.timeline_index].place
    return str(value or "").strip()


def place_issue(day: DayRecord, action: LifeActionIntent) -> str:
    # 聊天、拍照或视频回执只证明消息/媒体事件，不证明角色仍在该媒体
    # 的拍摄地点；只有实体生活动作参与当前位置衔接。
    if action.action_type not in _PHYSICAL_PLACE_ACTIONS:
        return ""
    current = str(day.meta.get("current_place") or "").strip()
    if action.action_type in {"move", "travel"}:
        expected = str(action.payload.get("origin") or "").strip()
    else:
        expected = action_place(day, action)
    if current and expected and current != expected:
        return f"行动地点尚未衔接：当前在{current}，此动作需要在{expected}；先确认地点或安排移动"
    return ""


def action_chain_place_issue(day: DayRecord, steps: list[LifeActionIntent]) -> str:
    """每步明确执行地点；移动结果决定下一步起点，不从自然语言猜返程。"""
    current = str(day.meta.get("current_place") or "").strip()
    for action in steps:
        if action.action_type not in _PHYSICAL_PLACE_ACTIONS:
            continue
        moving = action.action_type in {"move", "travel"}
        expected = str(action.payload.get("origin") or "").strip() if moving else action_place(day, action)
        if current and not expected:
            return f"动作 {action.action_type} 缺少结构化执行地点 place"
        if current and expected and current != expected:
            return f"行动链地点不连续：前一步在{current}，下一步需要在{expected}；须先安排移动"
        current = action_place(day, action) if moving else (expected or current)
    return ""


def commit_place(day: DayRecord, action: LifeActionIntent, at: str) -> None:
    if action.action_type not in _PHYSICAL_PLACE_ACTIONS:
        return
    place = action_place(day, action)
    # 没有结构化地点的动作不会把整句 target 写进当前位置。
    if not place:
        return
    previous_at = str(day.meta.get("place_fact_at") or "")
    committed_at = local_datetime(at)
    previous_time = local_datetime(previous_at)
    if committed_at is None or (previous_time and committed_at < previous_time):
        return
    previous = str(day.meta.get("current_place") or "")
    if previous and previous != place:
        day.meta["previous_place"] = previous
    day.meta.update(current_place=place, place_fact_at=at,
                    place_fact_action_id=action.action_id)
    if not any(item.name == place for item in day.places):
        day.places.append(PlaceRecord(name=place, source="life_action", last_seen=at))


def reconcile_place(day: DayRecord) -> None:
    """从已提交动作恢复投影；时间轴计划和进行中动作都不作为到达证据。"""
    try:
        actions = json.loads(day.meta.get("planned_life_actions") or "[]")
        settlements = json.loads(day.meta.get("life_action_settlements") or "{}")
    except (TypeError, ValueError):
        return
    if not isinstance(actions, list) or not isinstance(settlements, dict):
        return
    confirmed = []
    for raw in actions:
        if not isinstance(raw, dict):
            continue
        action = LifeActionIntent.from_value(raw)
        if action is None:
            continue
        receipt = settlements.get(action.action_id) or {}
        if not isinstance(receipt, dict) or receipt.get("status") != "committed":
            continue
        at = str(receipt.get("committed_at") or "")
        if instant := local_datetime(at):
            confirmed.append((instant, at, action))
    for _, at, action in sorted(confirmed, key=lambda item: item[0]):
        commit_place(day, action, at)
