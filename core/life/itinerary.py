from __future__ import annotations

import copy
import datetime
import json
import uuid
from typing import Any

from ..archive.timeline import rebind_planned_actions
from ..models import DayRecord, LIFE_ACTION_TYPES, LifeActionIntent, TimelineItem
from ..prompts import LIFE_ACTION_SCOPE_RULES
from .dressing import wardrobe_conditions
from .engine import _resolve_planned_outfit_catalog
from .actionchain import actions, committed_ids, execution_evidence


EDIT_ACTION_RULES = """日程编辑同时交付 planned_actions 数组，没有需要执行结算的动作时返回 []。
每项用 edit_index 绑定本轮 timeline_edits 中 insert/replace 的序号（从 0 开始），不要猜合并后的 timeline_index。
换装、穿脱鞋或外层、拿放随身配饰必须有 change_outfit；其他需要实际状态变化的准备、饮食、移动等也应填写对应动作，不能只写活动文案。
复合节点按实际先后绑定多项动作，各 target 只描述本步骤；所有步骤有执行回执后节点才完成。duration_minutes 是该步骤实际耗时，不是自动完成的倒计时。
wait_for_action_ids 填写这项安排必须等到完成的已有动作编号，无依赖时为 []；只从当前执行资料中选择，保留所依赖节点，不以估计耗时替代完成回执。无法确认前置条件时先改约或保留待确认，不声称已能行动。
change_outfit 从本轮衣橱候选选 catalog_reference_ids，填写 scene_category 和 outfit_components；局部调整保留主体服装。没有可用衣物依据时不要虚构换装。
planned_actions 格式：[{"edit_index":0,"action_type":"rest|drink|meal|cook|order_food|purchase|move|travel|work|study|chore|exercise|groom|change_outfit|social|chat|photo|video","target":"具体目标","duration_minutes":5,"preconditions":[],"effects":[],"payload":{},"evidence":"准备这项动作的依据"}]。
只安排计划，不把动作写成已完成；媒体和共同活动仍须真实交付或参与证据。\n""" + LIFE_ACTION_SCOPE_RULES


async def itinerary_wardrobe_context(
    host: Any, date_str: str, now: datetime.datetime
) -> str:
    day = await host.archive.get_day(date_str) or DayRecord(date=date_str)
    conditions = wardrobe_conditions(
        day,
        now,
        residence=str(getattr(host.config.domains, "home_address", "") or ""),
    )
    catalog = await host._style_catalog_context(limit=14, conditions=conditions)
    execution = {**execution_evidence(day), "actions": [item.as_dict() for item in actions(day)]}
    return (f"当前实际穿搭：{day.outfit or '尚未确认'}\n当前穿戴组成：{day.meta.get('outfit_components', '')}\n{catalog}"
            f"\n当前执行资料（计划不等于完成）：{json.dumps(execution, ensure_ascii=False)}")


def outfit_managed_by_schedule(decision: dict) -> bool:
    """具体换装或有前置依赖的安排交执行链，避免旁路提前改变衣着。"""
    return any(item.get("action_type") == "change_outfit"
               or item.get("payload", {}).get("depends_on_action_ids")
               for item in decision.get("_planned_life_actions", []) if isinstance(item, dict))


async def compile_edited_actions(
    host: Any, result: dict, timeline: list[TimelineItem], *, date_str: str, source: str
) -> str:
    """把本轮编辑中的动作绑定到审计后的节点，校验衣橱引用；不执行动作。"""
    raw_actions = result.get("planned_actions")
    if not isinstance(raw_actions, list):
        return "缺少结构化 planned_actions，尚不能确认日程的执行安排"
    edits = result.get("timeline_edits") or []
    compiled = []
    for raw in raw_actions:
        if not isinstance(raw, dict):
            return "日程动作格式无效"
        edit_index = raw.get("edit_index")
        if type(edit_index) is not int or not 0 <= edit_index < len(edits):
            return "动作没有绑定本轮有效的编辑序号"
        edit = edits[edit_index]
        if edit.get("operation") not in {"insert", "replace"}:
            return "动作不能绑定已移除的节点"
        original = TimelineItem.from_value(edit.get("item"))
        # 地图审计可能调整时间和地点，活动主体应保持一致。
        matches = [
            i for i, item in enumerate(timeline) if item.activity == original.activity
        ]
        exact = [
            i
            for i in matches
            if timeline[i].time == original.time
            and (timeline[i].day_offset or 0) == (original.day_offset or 0)
        ]
        matches = exact or matches
        if len(matches) != 1:
            return "动作对应节点不唯一"
        index = matches[0]
        action = LifeActionIntent.from_value(raw)
        if (
            action.action_type not in LIFE_ACTION_TYPES
            or not action.target
            or action.duration_minutes <= 0
        ):
            return "动作缺少有效类型、目标或实际耗时"
        action.action_id = f"{source}:{date_str}:{uuid.uuid4().hex}"
        action.timeline_index = index
        action.source = source
        compiled.append(action.as_dict())
    current = await host.archive.get_day(date_str)
    dependencies = result.get("wait_for_action_ids", [])
    if (not isinstance(dependencies, list) or len(dependencies) > 16
            or any(not isinstance(key, str) or not key for key in dependencies)):
        return "等待条件必须引用有效的动作编号"
    dependencies = list(dict.fromkeys(dependencies))
    if dependencies:
        if current is None or not compiled:
            return "附带等待条件的安排缺少可执行动作"
        known = {item.action_id: item for item in actions(current)}
        completed = committed_ids(current)
        rebound = copy.deepcopy(current.meta)
        rebind_planned_actions(rebound, current.timeline, timeline)
        rebound_actions = json.loads(rebound.get("planned_life_actions") or "[]")
        indexes = {item["action_id"]: item.get("timeline_index") for item in rebound_actions}
        first_index = min(item["timeline_index"] for item in compiled)
        for key in dependencies:
            if key not in known:
                return "等待条件引用了不存在的动作"
            if key in completed:
                continue
            index = indexes.get(key)
            if (type(index) is not int or index >= first_index
                    or timeline[index].execution_state in {"skipped", "cancelled", "expired"}):
                return "等待的动作已被移除、取消或位于本安排之后"
    # 准备、出发和活动按同一执行链推进，前一步延迟时后一步不能越过。
    compiled.sort(key=lambda item: item["timeline_index"])
    chain_id = f"{source}:{uuid.uuid4().hex}"
    for item in compiled:
        item["payload"]["execution_chain_id"] = chain_id
        item["payload"]["depends_on_action_ids"] = dependencies
    payload = {
        "timeline": [item.as_dict() for item in timeline],
        "planned_actions": compiled,
        "outfit_components": current.meta.get("outfit_components", "")
        if current
        else "",
    }
    issue = await _resolve_planned_outfit_catalog(host, payload, manual_extra=False)
    if issue:
        return issue
    for action in compiled:
        item = timeline[action["timeline_index"]]
        item.duration_minutes = sum(step["duration_minutes"] for step in compiled if step["timeline_index"] == action["timeline_index"])
        item.execution_state = "planned"
        item.execution_reason = "等待结合当前生活状态实际执行"
    result["_planned_life_actions"] = compiled
    return ""


def apply_edited_timeline(
    day: DayRecord, timeline: list[TimelineItem], decision: dict,
    *, commitment_id: int = 0,
) -> None:
    """日程和动作一起提交，保留未改动动作的编号，解除已替换动作的绑定。"""
    try:
        previous = json.loads(day.meta.get("planned_life_actions") or "[]")
    except (ValueError, TypeError):
        previous = []
    previous = (
        [item for item in previous if isinstance(item, dict)]
        if isinstance(previous, list)
        else []
    )
    unbound = {
        item.get("action_id") for item in previous if item.get("timeline_index") is None
    }
    rebind_planned_actions(day.meta, day.timeline, timeline)
    try:
        rebound = json.loads(day.meta.get("planned_life_actions") or "[]")
    except (ValueError, TypeError):
        rebound = []
    additions = copy.deepcopy(decision.get("_planned_life_actions") or [])
    if commitment_id > 0:
        for action in additions:
            # 排程归属只用于跟踪整项安排，不代表某个准备动作已兑现承诺。
            action.setdefault("payload", {})["schedule_commitment_id"] = commitment_id
    replaced = {item["timeline_index"] for item in additions}
    kept = (
        [
            item
            for item in rebound
            if isinstance(item, dict)
            and item.get("timeline_index") not in replaced
            and (
                item.get("timeline_index") is not None
                or item.get("action_id") in unbound
            )
        ]
        if isinstance(rebound, list)
        else []
    )
    day.meta["planned_life_actions"] = json.dumps(
        kept + additions, ensure_ascii=False, separators=(",", ":")
    )
    day.timeline = timeline
