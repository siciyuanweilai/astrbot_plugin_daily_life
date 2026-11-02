"""聊天需要的生活事实投影；不改写后台状态，不从分数推测感受。"""

from __future__ import annotations

import json
from typing import Any

from .continuity import run_status_label
from .locationfacts import completed_action_description


def chat_life_projection(world: dict[str, Any]) -> str:
    """保留所处场景和最近已发生的变化，不携带审计、复盘或因果报表。"""
    lines = []
    kernel = world.get("kernel") or {}
    environment = kernel.get("environment") or {}
    place = str(environment.get("place") or "").strip()
    if place:
        lines.append(f"最近确认的地点：{place}")
    for item in list(world.get("history") or [])[-2:]:
        if not isinstance(item, dict):
            continue
        target = str((item.get("action") or {}).get("target") or "").strip()
        if not target or item.get("status") not in {"completed", "failed", "cancelled"}:
            continue
        at = str(item.get("finished_at") or "").strip()
        if item.get("status") == "completed":
            target = completed_action_description(item.get("action"))
        lines.append(f"{at} {target}（{run_status_label(item)}）")
    return "\n".join(lines)


def chat_projection_from_day(day: Any) -> str:
    """旧日记录仅提供已保存的执行状态，不解析旧报表来编造体验。"""
    meta = day.meta or {}
    return str(meta.get("continuous_chat_context") or "")


def read_execution(day: Any) -> dict[str, Any]:
    try:
        value = json.loads((day.meta or {}).get("continuous_execution") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
