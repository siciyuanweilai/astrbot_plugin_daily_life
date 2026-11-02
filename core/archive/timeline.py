from __future__ import annotations

import json

from ..models import TimelineItem


def project_committed_timeline(day) -> None:
    """整组动作的成功结算决定完成态，部分完成与未执行计划保持原状。"""
    from ..life.actionchain import actions, settlements

    outcomes = settlements(day)
    grouped = {}
    for action in actions(day):
        index = action.timeline_index
        if index is not None and 0 <= index < len(day.timeline):
            grouped.setdefault(index, []).append(outcomes.get(action.action_id))
    for index, records in grouped.items():
        if not all(isinstance(r, dict) and r.get("status") == "committed" for r in records):
            continue
        item = day.timeline[index]
        if item.execution_state == "completed":
            continue
        last = max(records, key=lambda r: str(r.get("committed_at") or ""))
        item.execution_state = "completed"
        item.execution_reason = str(last.get("reason") or "生活动作已结算")
        item.execution_evidence = str(last.get("evidence") or "")
        item.execution_updated_at = str(last.get("committed_at") or "")
    try:
        pending = json.loads(day.meta.get("schedule_replan_pending") or "{}")
    except (TypeError, ValueError):
        pending = {}
    if isinstance(pending, dict):
        settled = outcomes.get(pending.get("action_id"))
        if isinstance(settled, dict) and settled.get("status") == "committed":
            day.meta.pop("schedule_replan_pending", None)


def rebind_planned_actions(
    meta: dict[str, str], previous: list[TimelineItem], current: list[TimelineItem]
) -> None:
    """节点位置变化时，保持动作与原活动的绑定。"""
    raw = meta.get("planned_life_actions")
    if not raw or previous == current:
        return
    try:
        actions = json.loads(raw)
    except (TypeError, ValueError):
        return
    if not isinstance(actions, list):
        return

    def identity(item: TimelineItem) -> tuple[str, ...]:
        return (item.activity, item.place, item.place_kind, item.place_scope)

    changed = False
    for action in actions:
        if not isinstance(action, dict):
            continue
        index = action.get("timeline_index")
        if not isinstance(index, int) or not 0 <= index < len(previous):
            continue
        original = previous[index]
        candidates = [
            i for i, item in enumerate(current) if identity(item) == identity(original)
        ]
        # 相同活动可能先取消再重做；已执行状态与检查点用于区分真实记录。
        observed = [
            i for i in candidates
            if current[i].execution_state == original.execution_state
            and current[i].execution_updated_at == original.execution_updated_at
        ]
        candidates = observed or candidates
        exact = [i for i in candidates if current[i].time == original.time]
        candidates = exact or candidates
        # 活动已被移除或归属不明确时，不得转而绑定其他节点。
        replacement = candidates[0] if len(candidates) == 1 else None
        if replacement != index:
            action["timeline_index"] = replacement
            changed = True
    if changed:
        meta["planned_life_actions"] = json.dumps(
            actions, ensure_ascii=False, separators=(",", ":")
        )
