"""执行时间独立于日程时间，按稳定动作编号记录；不拿计划时长推算经历。"""
from __future__ import annotations

import json

from .actionchain import actions, settlements
from .tools import parse_life_datetime


def execution_times(day) -> dict:
    try:
        raw = json.loads(day.meta.get("life_action_execution_times") or "{}")
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def record_execution_time(day, action_id: str, **fields) -> None:
    records = execution_times(day)
    previous = records.get(action_id)
    records[action_id] = {**(previous if isinstance(previous, dict) else {}), **fields}
    day.meta["life_action_execution_times"] = json.dumps(records, ensure_ascii=False)


def timeline_execution_times(day) -> dict[int, dict]:
    records, outcomes = execution_times(day), settlements(day)
    grouped = {}
    for action in actions(day):
        if action.timeline_index is not None:
            grouped.setdefault(action.timeline_index, []).append(action.action_id)
    result = {}
    for index, ids in grouped.items():
        times = [records.get(key) or {} for key in ids]
        times = [item if isinstance(item, dict) else {} for item in times]
        starts = [parse_life_datetime(item.get("started_at")) for item in times]
        starts = [at for at in starts if at is not None]
        committed = [outcomes.get(key) or {} for key in ids]
        complete = all(isinstance(item, dict) and item.get("status") == "committed" for item in committed)
        ends = [parse_life_datetime(item.get("committed_at")) for item in committed if isinstance(item, dict)]
        ends = [at for at in ends if at is not None]
        result[index] = {
            "actual_started_at": min(starts).isoformat(sep=" ") if starts else "",
            "actual_completed_at": max(ends).isoformat(sep=" ") if complete and ends else "",
            "actual_duration_minutes": round(sum(float(item["active_seconds"]) for item in times) / 60, 1)
            if complete and all(isinstance(item.get("active_seconds"), (int, float)) for item in times) else None,
        }
    return result
