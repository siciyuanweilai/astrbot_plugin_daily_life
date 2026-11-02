"""外观调整的执行边界：计划可供选择，实际动作与回执决定何时生效。"""

from __future__ import annotations

import datetime
import hashlib
import json

from ..models import DayRecord, LifeActionIntent
from .actionchain import actions, settlements
from .outfitrequest import active_outfit_request
from .tools import get_current_timeline_status, parse_life_datetime, timeline_item_datetime
from .wardrobe import normalize_outfit_components, scene_category_for_place_kind

_APPEARANCE_ACTIONS = frozenset({"change_outfit", "groom"})
_OCCUPYING_ACTIONS = _APPEARANCE_ACTIONS | {"exercise", "move", "travel"}


def _mapping(value: object) -> dict:
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()[:32]


def active_appearance_action(day: DayRecord, *, excluding: str = "") -> dict:
    run = _mapping(day.meta.get("continuous_execution"))
    action = _mapping(run.get("action"))
    if (action.get("action_type") not in _OCCUPYING_ACTIONS
            or not action.get("action_id") or action["action_id"] == excluding):
        return {}
    if _mapping(settlements(day).get(action["action_id"])).get("status") == "committed":
        return {}
    if run.get("status") in {"running", "ready", "settling"}:
        return {"action_id": action["action_id"], "action_type": action["action_type"],
                "status": run["status"]}
    return {}


def pending_dressing_action(day: DayRecord, now: datetime.datetime) -> str:
    """已交给行动链的换装由该执行器负责，后台巡检不再另起一次。"""
    outcomes = settlements(day)
    for action in actions(day):
        if action.action_type != "change_outfit" or not action.action_id:
            continue
        if _mapping(outcomes.get(action.action_id)).get("status") in {
            "committed", "failed", "cancelled", "rejected"
        }:
            continue
        if action.timeline_index is None or not 0 <= action.timeline_index < len(day.timeline):
            continue
        item = day.timeline[action.timeline_index]
        if item.execution_state in {"completed", "skipped", "cancelled", "expired"}:
            continue
        at = timeline_item_datetime(item, day.date, meta=day.meta)
        if at and at <= now:
            return action.action_id
    return ""


def completed_appearance_event(day: DayRecord) -> dict:
    """编号来自成功回执，不能因当前节点空档、重排或重复回执而改变。"""
    events = []
    for action_id, value in settlements(day).items():
        value = _mapping(value)
        if value.get("status") != "committed" or value.get("action_type") not in {"groom", "exercise"}:
            continue
        at = parse_life_datetime(value.get("committed_at"))
        if at:
            events.append((at, str(action_id), value["action_type"]))
    if not events:
        return {}
    at, action_id, kind = max(events)
    return {"action_id": action_id, "action_type": kind, "committed_at": str(at)}


def appearance_scene(day: DayRecord, now: datetime.datetime) -> str:
    sleep = getattr(getattr(day, "state", None), "sleep", None)
    if getattr(sleep, "depth", "") in {"light_sleep", "deep_sleep"}:
        return "sleep"
    current, _ = get_current_timeline_status(day.timeline, now, day.date, meta=day.meta)
    place = getattr(current, "place_kind", "") if current is not None else ""
    scene = scene_category_for_place_kind(place, default="")
    if scene:
        return scene
    # 节点间的空档或在线聊天不会抹掉刚才所在的实体场景。
    for item in reversed(day.timeline):
        at = timeline_item_datetime(item, day.date, meta=day.meta)
        if at and at <= now:
            scene = scene_category_for_place_kind(item.place_kind, default="")
            if scene:
                return scene
    return str(day.meta.get("outfit_scene_category") or "unknown")


def appearance_observation(day: DayRecord, now: datetime.datetime) -> dict:
    weather = day.weather_info
    try:
        temperature = round(float(weather.temp) / 5) if weather.temp is not None else None
    except (TypeError, ValueError):
        temperature = None
    rhythm = getattr(getattr(day, "state", None), "physiological_rhythm", None)
    condition = getattr(rhythm, "body_condition", None)
    return {
        "date": day.date,
        "scene": appearance_scene(day, now),
        "event": completed_appearance_event(day),
        "weather": {"temperature": temperature, "rain": bool(getattr(weather, "is_rainy", False))},
        "condition": {key: getattr(condition, key, None) for key in
                      ("kind", "phase", "burden_present", "intensity")},
        "request": active_outfit_request(day, now).get("id", ""),
        "wearability": day.meta.get("outfit_wearability", ""),
    }


def wearability_key(snapshot: dict) -> str:
    """只追踪身上衣物的实际不可穿状态，衣橱新增备选不构成换装理由。"""
    unavailable = {str(key): item.get("condition")
                   for key, item in snapshot.get("items", {}).items()
                   if item.get("currently_worn")
                   and item.get("condition") in {"dirty", "washing", "drying"}}
    return _digest(unavailable) if unavailable else ""


def appearance_key(day: DayRecord, now: datetime.datetime) -> str:
    return _digest(appearance_observation(day, now))


def has_settled_current_outfit(day: DayRecord) -> bool:
    """升级时沿用可核实的既有穿着；不为初始化记录重新换一遍衣服。"""
    if day.meta.get("outfit_fact_source") != "life_action":
        return False
    value = _mapping(settlements(day).get(day.meta.get("outfit_fact_evidence")))
    at = parse_life_datetime(value.get("committed_at"))
    event_at = parse_life_datetime(completed_appearance_event(day).get("committed_at"))
    return bool(at and value.get("status") == "committed"
                and value.get("action_type") == "change_outfit"
                and (event_at is None or at >= event_at))


def appearance_revision(day: DayRecord) -> str:
    """只比较本次选择依赖的外观事实，普通体力更新不会使结果过期。"""
    return _digest({"outfit": day.outfit, "meta": {key: day.meta.get(key, "") for key in (
        "outfit_components", "style_catalog_reference_ids", "outfit_fact_confirmed_at",
        "hair", "hair_style", "makeup", "makeup_style", "nails", "nails_style",
    )}})


def observation_contract(day: DayRecord, now: datetime.datetime) -> dict:
    return {"key": appearance_key(day, now), "revision": appearance_revision(day),
            "event": completed_appearance_event(day), "observed_at": now.isoformat(sep=" ")}


def appearance_action_issue(day: DayRecord, action: LifeActionIntent) -> str:
    if action.action_type not in _APPEARANCE_ACTIONS:
        return ""
    owner = active_appearance_action(day, excluding=action.action_id)
    if owner:
        return f"等待当前生活动作完成：{owner['action_id']}"
    contract = action.payload.get("appearance_observation")
    if contract is None:
        return ""
    contract = _mapping(contract)
    now = parse_life_datetime(contract.get("observed_at"))
    if (now is None or contract.get("key") != appearance_key(day, now)
            or contract.get("revision") != appearance_revision(day)):
        return "外观选择依据已变化，需要按最新生活状态重新判断"
    return ""


def review_signature(day: DayRecord, now: datetime.datetime, *, commitment: str = "") -> str:
    components = normalize_outfit_components(day.meta.get("outfit_components"))
    return "event_driven_v6|" + _digest({
        "observation": appearance_observation(day, now),
        "owner": active_appearance_action(day),
        "planned_dressing": pending_dressing_action(day, now),
        "commitment": commitment,
        "components": {key: value.get("state", "unknown") for key, value in components.items()
                       if key in {"footwear", "carried_accessories"}},
        # 备选列表不是新换装需求；只有尚未选成时才由新衣物唤醒重试。
        "available_selection": day.meta.get("wardrobe_selection_key", "")
        if day.meta.get("outfit_selection_pending") else "",
    })
