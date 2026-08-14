from __future__ import annotations

import copy
from typing import Any

from ..models import DayRecord


class DayRevisionConflict(RuntimeError):
    """每日生活记录在保存期间被其他任务修改。"""


def _merge_mapping_field(
    field_name: str,
    baseline: dict[str, Any],
    current: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    missing = object()
    merged = copy.deepcopy(current)
    for key in set(baseline) | set(incoming):
        before = baseline.get(key, missing)
        desired = incoming.get(key, missing)
        if desired == before:
            continue
        latest = current.get(key, missing)
        if latest != before and latest != desired:
            raise DayRevisionConflict(f"字段 {field_name}.{key} 已被其他任务修改")
        if desired is missing:
            merged.pop(key, None)
        else:
            merged[key] = copy.deepcopy(desired)
    return merged


def _merge_state_log(
    baseline: list[str], current: list[str], incoming: list[str]
) -> list[str]:
    if incoming == baseline:
        return copy.deepcopy(current)
    if current == baseline:
        return copy.deepcopy(incoming[-10:])
    baseline_size = len(baseline)
    if current[:baseline_size] == baseline and incoming[:baseline_size] == baseline:
        merged = list(current)
        for entry in incoming[baseline_size:]:
            if not merged or merged[-1] != entry:
                merged.append(entry)
        return merged[-10:]
    if current == incoming:
        return copy.deepcopy(current)
    raise DayRevisionConflict("字段 state_log 已被其他任务修改")


def merge_day_records(incoming: DayRecord, current: DayRecord) -> DayRecord:
    """把旧版本记录的真实改动合并到最新数据库记录。"""

    baseline = incoming._baseline
    if not baseline or incoming.revision == current.revision:
        return incoming

    incoming_values = incoming.persistence_snapshot()
    for field_name in DayRecord.PERSISTED_FIELDS:
        before = baseline.get(field_name)
        desired = incoming_values[field_name]
        if desired == before:
            continue
        latest = getattr(current, field_name)
        if field_name in {"meta", "outfit_history"}:
            value = _merge_mapping_field(
                field_name,
                before or {},
                latest or {},
                desired or {},
            )
        elif field_name == "state_log":
            value = _merge_state_log(
                before or [],
                latest or [],
                desired or [],
            )
        else:
            if latest != before and latest != desired:
                raise DayRevisionConflict(f"字段 {field_name} 已被其他任务修改")
            value = copy.deepcopy(desired)
        setattr(current, field_name, value)
    return current


__all__ = ["DayRevisionConflict", "merge_day_records"]
