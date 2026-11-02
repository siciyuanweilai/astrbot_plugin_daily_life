from __future__ import annotations

import json
from typing import Any

from .body import ContinuousBody, body_context, nonnegative, project_body
from .goals import goals_context
from .presence import kernel_context, sync_kernel_from_day
from .locationfacts import completed_action_description

RUN_STATUS_LABELS = {
    "running": "进行中",
    "paused": "暂停",
    "ready": "等待结算",
    "settling": "结算中",
    "completed": "已完成",
    "failed": "未完成",
    "cancelled": "已取消",
}


def observation_pending(run: dict[str, Any] | None) -> bool:
    """运行观察待确认不等同于生活行动被打断。"""
    return bool(
        run
        and run.get("status") == "running"
        and run.get("observation_state") == "pending"
    )


def run_status_label(run: dict[str, Any]) -> str:
    if observation_pending(run):
        return "最近确认，当前进展待确认"
    return RUN_STATUS_LABELS.get(run.get("status"), "待确认")


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
            f"{run_status_label(run)}；"
            f"实际累计 {nonnegative(run.get('active_seconds')) / 60:.1f}/"
            f"{action.get('duration_minutes', 0)} 分钟；{run.get('reason', '')}。"
            "进行中、暂停和进展待确认均不能说已经做完，计划也不能覆盖当前实际行动。"
        )
        payload = action.get("payload")
        next_step = payload.get("next_step") if isinstance(payload, dict) else None
        if next_step:
            sections.append(f"后续意向（尚未执行，届时重新判断）：{str(next_step)[:240]}。")
    history = world.get("history", [])[-3:]
    if history:
        sections.append(
            "最近实际行动："
            + "；".join(
                f"{item.get('finished_at', '')} "
                f"{completed_action_description(item.get('action')) if item.get('status') == 'completed' else item.get('action', {}).get('target', '')} "
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
    from .chatview import chat_life_projection

    day.meta["continuous_chat_context"] = chat_life_projection(world)
    day.meta["continuous_execution"] = json.dumps(
        world.get("run") or {}, ensure_ascii=False
    )
    day.meta["continuous_sleeping"] = "true" if world.get("sleeping") else "false"
    if day.state is not None:
        run = world.get("run") or {}
        if observation_pending(run):
            return
        if world.get("sleeping"):
            day.state.sleep.depth = (
                "deep_sleep"
                if nonnegative(run.get("active_seconds")) >= 2700
                else "light_sleep"
            )
        elif day.state.sleep.depth in {"light_sleep", "deep_sleep"}:
            day.state.sleep.depth = "awake"
