"""自主行动与日程同步；语义由行动判断提供，执行层只校验结构与证据。"""

from __future__ import annotations

import copy
import datetime
import json

from ..models import INTERNAL_SIMULATED_ACTION_TYPES, TIMELINE_TERMINAL_STATES, LifeActionIntent, TimelineItem
from .actionchain import actions, committed_ids
from .locationfacts import action_place
from .tools import parse_life_datetime, timeline_deferred_until, timeline_item_datetime


def protected_indexes(day, world) -> set[int]:
    """已执行步骤、正在执行节点和对外约定不由自主行动重排。"""
    completed = committed_ids(day)
    protected = {
        item.timeline_index for item in actions(day)
        if item.timeline_index is not None and (
            item.action_id in completed
            or item.source in {"invite", "commitment"}
            or item.action_type not in INTERNAL_SIMULATED_ACTION_TYPES
            or item.payload.get("commitment_links")
            or item.payload.get("commitment_ids")
        )
    }
    current = (world.get("run") or {}).get("action") or {}
    if type(current.get("timeline_index")) is int:
        protected.add(current["timeline_index"])
    return protected


def prepare_updates(day, world, payload, now):
    """先在副本上校验整组变更，拒绝部分落地或顺带完成未执行事项。"""
    proposed = copy.deepcopy(day)
    updates = payload.get("timeline_updates", [])
    if not isinstance(updates, list) or len(updates) > len(day.timeline):
        return None
    protected = protected_indexes(day, world)
    allowed = set(payload.get("_sources") or [])
    seen = set()
    changes = []
    for update in updates:
        if not isinstance(update, dict):
            return None
        index = update.get("timeline_index")
        if type(index) is not int or not 0 <= index < len(day.timeline) or index in seen:
            return None
        seen.add(index)
        item = proposed.timeline[index]
        reason = str(update.get("reason") or "").strip()[:300]
        evidence = update.get("source_ids")
        if (index in protected or item.execution_state in TIMELINE_TERMINAL_STATES
                or item.execution_state == "active" or not reason
                or not isinstance(evidence, list) or not evidence
                or any(not isinstance(value, str) or value not in allowed for value in evidence)):
            return None
        operation = update.get("operation")
        before = item.as_dict()
        if operation == "reschedule":
            at = parse_life_datetime(update.get("at"))
            base = datetime.date.fromisoformat(day.date)
            deferred = timeline_deferred_until(item, day.date, day.meta)
            if (at is None or at < now.replace(second=0, microsecond=0)
                    or not 0 <= (at.date() - base).days <= 1
                    or (deferred and at < deferred)):
                return None
            item.time = at.strftime("%H:%M")
            item.day_offset = (at.date() - base).days
            item.execution_state = "planned"
        elif operation in {"skip", "cancel"}:
            if timeline_deferred_until(item, day.date, day.meta):
                return None
            item.execution_state = "skipped" if operation == "skip" else "cancelled"
        else:
            return None
        item.execution_reason = reason
        item.execution_evidence = "、".join(evidence)
        item.execution_updated_at = now.isoformat(sep=" ")
        changes.append({"timeline_index": index, "before": before, "after": item.as_dict(),
                        "reason": reason, "source_ids": evidence})
    if changes:
        proposed.meta.pop("schedule_anchors", None)
        proposed.meta["schedule_alignment"] = json.dumps(
            {"at": now.isoformat(), "changes": changes}, ensure_ascii=False
        )
        try:
            revision = int(proposed.meta.get("schedule_revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        proposed.meta["schedule_revision"] = str(revision + 1)
    return proposed


def synchronize_run(day, world, now):
    """即时行动也落在时间轴上；排序时同步全部动作绑定，保留动作编号与回执。"""
    run = world.get("run") or {}
    planned = actions(day)
    inserted = False
    if run.get("status") in {"running", "paused"}:
        action = LifeActionIntent.from_value(run["action"])
        if action.timeline_index is None:
            started = parse_life_datetime(run.get("started_at")) or now
            offset = (started.date() - datetime.date.fromisoformat(day.date)).days
            if offset < 0:
                return
            action.timeline_index = len(day.timeline)
            action.payload["execution_chain_unbound"] = False
            inserted = True
            day.timeline.append(TimelineItem(
                time=started.strftime("%H:%M"), day_offset=offset,
                activity=action.target,
                activity_kind="rest" if action.action_type == "rest" else "other",
                duration_minutes=action.duration_minutes,
                place=action_place(day, action) or str(day.meta.get("current_place") or ""),
                travel_origin=str(action.payload.get("origin") or "")
                if action.action_type in {"move", "travel"} else "",
                travel_mode=str(action.payload.get("travel_mode") or "")
                if action.action_type in {"move", "travel"} else "",
                execution_state="active",
                execution_reason="自主行动已实际开始",
                execution_evidence=str(run.get("reason") or ""),
                execution_updated_at=now.isoformat(sep=" "),
            ))
            found = False
            for index, item in enumerate(planned):
                if item.action_id == action.action_id:
                    planned[index] = action
                    found = True
                    break
            if not found:
                planned.append(action)
            run["action"] = action.as_dict()
    order = sorted(range(len(day.timeline)), key=lambda index: (
        timeline_item_datetime(day.timeline[index], day.date, meta=day.meta) or datetime.datetime.max,
        index,
    ))
    mapping = {old: new for new, old in enumerate(order)}
    for action in planned:
        if action.timeline_index in mapping:
            action.timeline_index = mapping[action.timeline_index]
    if run.get("action", {}).get("timeline_index") in mapping:
        run["action"]["timeline_index"] = mapping[run["action"]["timeline_index"]]
    day.timeline = [day.timeline[index] for index in order]
    day.meta["planned_life_actions"] = json.dumps([item.as_dict() for item in planned], ensure_ascii=False)
    if inserted or order != list(range(len(order))):
        day.meta.pop("schedule_anchors", None)
