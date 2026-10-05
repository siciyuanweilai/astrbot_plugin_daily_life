from __future__ import annotations

import json

from ..models import TimelineItem


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
