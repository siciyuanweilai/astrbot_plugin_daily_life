from __future__ import annotations

import json
from typing import Any

from .body import ContinuousBody, body_context, nonnegative, project_body
from .goals import goals_context
from .presence import kernel_context, sync_kernel_from_day

RUN_STATUS_LABELS = {
    "running": "进行中",
    "paused": "暂停",
    "ready": "等待结算",
    "settling": "结算中",
    "completed": "已完成",
    "failed": "未完成",
    "cancelled": "已取消",
}


def execution_context(world: dict[str, Any]) -> str:
    sections = [
        kernel_context(world),
        body_context(world.get("body")),
        goals_context(world),
    ]
    run = world.get("run")
    if isinstance(run, dict):
        action = run.get("action", {})
        sections.append(
            f"当前自主行动：{action.get('target') or action.get('action_type')}；"
            f"{RUN_STATUS_LABELS.get(run.get('status'), '待确认')}；"
            f"实际累计 {nonnegative(run.get('active_seconds')) / 60:.1f}/"
            f"{action.get('duration_minutes', 0)} 分钟；{run.get('reason', '')}。"
            "进行中和暂停均不能说已经做完，计划也不能覆盖当前实际行动。"
        )
    history = world.get("history", [])[-3:]
    if history:
        sections.append(
            "最近实际行动："
            + "；".join(
                f"{item.get('finished_at', '')} {item.get('action', {}).get('target', '')} "
                f"{RUN_STATUS_LABELS.get(item.get('status'), '待确认')}"
                for item in history
            )
        )
    return "\n".join(section for section in sections if section)


def project_world(day, world: dict[str, Any]) -> None:
    if not world.get("body"):
        return
    project_body(day, ContinuousBody.from_value(world["body"], day))
    sync_kernel_from_day(day, world)
    day.meta["continuous_life_context"] = execution_context(world)
    day.meta["continuous_execution"] = json.dumps(
        world.get("run") or {}, ensure_ascii=False
    )
    day.meta["continuous_sleeping"] = "true" if world.get("sleeping") else "false"
    if day.state is not None:
        run = world.get("run") or {}
        if world.get("sleeping"):
            day.state.sleep.depth = (
                "deep_sleep"
                if nonnegative(run.get("active_seconds")) >= 2700
                else "light_sleep"
            )
        elif day.state.sleep.depth in {"light_sleep", "deep_sleep"}:
            day.state.sleep.depth = "awake"
