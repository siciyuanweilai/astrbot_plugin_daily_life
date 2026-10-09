"""持续生活的统一生命内核投影。

这里保存跨模块共享的当前自我快照和事实引用，不复制身体、记忆或关系的
完整数据。所有事件都必须来自已经落库的真实交换或执行回执。
"""

from __future__ import annotations

import datetime
import hashlib
from typing import Any

from ..clock import now as life_now

KERNEL_VERSION = 1
_EVENT_LIMIT = 80
_OUTCOME_LIMIT = 80
_THREAD_LIMIT = 40
_TRACE_LIMIT = 80
_AUTOBIOGRAPHY_LIMIT = 160


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _score(value: Any, default: float = 0.0) -> float:
    try:
        fallback = float(default)
    except (TypeError, ValueError):
        fallback = 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    if number != number or number in {float("inf"), float("-inf")}:
        return fallback
    return max(0.0, min(100.0, number))


def _now_text(value: datetime.datetime | str | None = None) -> str:
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            value = None
    return (value or life_now()).replace(tzinfo=None).isoformat()


def _bounded_append(items: list[Any], value: Any, limit: int) -> None:
    items.append(value)
    del items[:-limit]


def _as_action_dict(action: Any) -> dict[str, Any]:
    if hasattr(action, "as_dict"):
        value = action.as_dict()
        return value if isinstance(value, dict) else {}
    return dict(action) if isinstance(action, dict) else {}


def ensure_kernel(
    world: dict[str, Any], now: datetime.datetime | None = None
) -> dict[str, Any]:
    """为旧检查点补齐统一内核的有限字段。"""

    kernel = world.setdefault("kernel", {})
    if not isinstance(kernel, dict):
        kernel = world["kernel"] = {}
    kernel.setdefault("version", KERNEL_VERSION)
    kernel.setdefault(
        "self_model",
        {
            "owner": "self",
            "continuity": "通过已落库的身体、互动、行动和记忆事实延续",
            "agency": "只对自身行动负责，不把计划当作经历",
        },
    )
    kernel.setdefault("affect", {})
    kernel.setdefault("attention", {})
    kernel.setdefault("environment", {})
    kernel.setdefault("social", {})
    kernel.setdefault("memory", {})
    kernel.setdefault("reflection", {})
    kernel.setdefault("action_outcomes", [])
    kernel.setdefault("causal_traces", [])
    kernel.setdefault("autobiography", [])
    kernel.setdefault("open_threads", [])
    for key, limit in (
        ("action_outcomes", _OUTCOME_LIMIT),
        ("causal_traces", _TRACE_LIMIT),
        ("autobiography", _AUTOBIOGRAPHY_LIMIT),
        ("open_threads", _THREAD_LIMIT),
    ):
        values = kernel.get(key)
        kernel[key] = values[-limit:] if isinstance(values, list) else []
    model = kernel["self_model"]
    if not isinstance(model, dict):
        model = kernel["self_model"] = {}
    model.setdefault("owner", "self")
    model.setdefault("continuity", "通过已落库的身体、互动、行动和记忆事实延续")
    model.setdefault("agency", "只对自身行动负责，不把计划当作经历")
    model.setdefault("interests", [])
    model.setdefault("capabilities", [])
    model.setdefault("recent_changes", [])
    model.setdefault("evidence_ids", [])
    events = kernel.get("events")
    kernel["events"] = events[-_EVENT_LIMIT:] if isinstance(events, list) else []
    kernel.setdefault("updated_at", _now_text(now))
    return kernel


def record_event(
    world: dict[str, Any],
    *,
    kind: str,
    source_id: str,
    at: datetime.datetime | None = None,
    summary: str = "",
    evidence_ids: list[str] | None = None,
) -> bool:
    """写入一个幂等的事实引用，返回是否真的新增。"""

    kernel = ensure_kernel(world, at)
    kind = _text(kind, 60)
    source_id = _text(source_id, 180)
    if not kind or not source_id:
        return False
    event_key = hashlib.sha256(f"{kind}:{source_id}".encode()).hexdigest()[:24]
    events = kernel["events"]
    if any(item.get("id") == event_key for item in events if isinstance(item, dict)):
        return False
    references = []
    for value in evidence_ids or []:
        value = _text(value, 120)
        if value and value not in references:
            references.append(value)
    events.append(
        event := {
            "id": event_key,
            "kind": kind,
            "source_id": source_id,
            "at": _now_text(at),
            "summary": _text(summary),
            "evidence_ids": references[:12],
            "observed": True,
        }
    )
    kernel["events"] = events[-_EVENT_LIMIT:]
    kernel["last_event_at"] = events[-1]["at"]
    _bounded_append(
        kernel["autobiography"],
        {
            "id": event_key,
            "kind": kind,
            "source_id": source_id,
            "at": event["at"],
            "summary": event["summary"],
            "evidence_ids": references[:12],
            "observed": True,
        },
        _AUTOBIOGRAPHY_LIMIT,
    )
    return True


def update_self_model(
    world: dict[str, Any],
    *,
    persona: str = "",
    preferences: list[dict[str, Any]] | None = None,
    focus: list[dict[str, Any]] | None = None,
    skills: dict[str, Any] | None = None,
    evidence_ids: list[str] | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """根据结构化来源更新自我模型，只记录来源投影，不替模型宣称人格结论。"""

    kernel = ensure_kernel(world, now)
    model = kernel["self_model"]
    sources = []
    for value in evidence_ids or []:
        value = _text(value, 180)
        if value and value not in sources:
            sources.append(value)

    inputs = []
    for source_kind, values in (
        ("focus", focus or []),
        ("preference", preferences or []),
    ):
        for item in values:
            if not isinstance(item, dict):
                continue
            source_id = _text(item.get("id"), 120)
            label = _text(
                item.get("title")
                or item.get("content")
                or item.get("summary")
                or item.get("label")
                or item.get("name"),
                180,
            )
            if not label:
                continue
            entry = {
                "source": f"{source_kind}:{source_id}" if source_id else source_kind,
                "label": label,
            }
            if entry not in inputs:
                inputs.append(entry)
            if source_id:
                sources.append(f"{source_kind}:{source_id}")
    model["source_context"] = inputs[-18:]
    model["capabilities"] = [
        {
            "name": _text(name, 160),
            "practice_minutes": item.get("practice_minutes", 0),
            "sessions": item.get("sessions", 0),
            "verified_mastery": False,
        }
        for name, item in (skills if isinstance(skills, dict) else {}).items()
        if isinstance(item, dict)
    ][-20:]
    if persona:
        model["persona_evidence"] = _text(persona, 500)
        sources.append("persona")
    source_list = []
    for value in sources:
        if value and value not in source_list:
            source_list.append(value)
    model["evidence_ids"] = (model.get("evidence_ids") or [])[-24:]
    for value in source_list:
        if value not in model["evidence_ids"]:
            model["evidence_ids"].append(value)
    model["evidence_ids"] = model["evidence_ids"][-32:]
    model["updated_at"] = _now_text(now)
    return model


def apply_self_model_updates(
    world: dict[str, Any],
    updates: Any,
    *,
    sources: set[str],
    now: datetime.datetime,
) -> None:
    """自身兴趣、价值和边界只能由本轮可引用证据支持，并按证据缓慢累积。"""

    if not isinstance(updates, list):
        return
    model = ensure_kernel(world, now)["self_model"]
    for raw in updates[:4]:
        if not isinstance(raw, dict) or raw.get("owner") != "self":
            continue
        field = {
            "interest": "interests",
            "value": "values",
            "boundary": "boundaries",
        }.get(raw.get("field"))
        label = _text(raw.get("text"), 180)
        reason = _text(raw.get("reason"), 240)
        refs = (
            list(
                dict.fromkeys(
                    str(item)
                    for item in raw.get("source_ids", [])
                    if str(item) in sources
                )
            )
            if isinstance(raw.get("source_ids"), list)
            else []
        )
        if not field or not label or not reason or not refs:
            continue
        values = model.setdefault(field, [])
        key = _text(raw.get("id") or label, 120)
        current = next(
            (
                item
                for item in values
                if isinstance(item, dict) and item.get("id") == key
            ),
            None,
        )
        if current is None:
            current = {
                "id": key,
                "label": label,
                "evidence_ids": [],
                "support_count": 0,
                "status": "tentative",
            }
            values.append(current)
        new_refs = [item for item in refs if item not in current["evidence_ids"]]
        if not new_refs:
            continue
        current["evidence_ids"] = [*current["evidence_ids"], *new_refs][-16:]
        current["support_count"] = min(20, current["support_count"] + 1)
        current.update(label=label, reason=reason, updated_at=_now_text(now))
        current["status"] = (
            "supported"
            if current["support_count"] >= 2 or "persona" in refs
            else "tentative"
        )
        model[field] = values[-12:]
        _bounded_append(
            model["recent_changes"],
            {
                "field": field,
                "id": key,
                "label": label,
                "reason": reason,
                "evidence_ids": new_refs,
                "at": _now_text(now),
                "kind": "evidence_supported_interpretation",
            },
            20,
        )
        record_event(
            world,
            kind="self_model_updated",
            source_id=f"{field}:{key}:{','.join(new_refs)}",
            at=now,
            summary=f"根据新证据更新自身认识：{label}",
            evidence_ids=new_refs,
        )


def record_causal_trace(
    world: dict[str, Any],
    *,
    source_id: str,
    kind: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    consequence: str = "",
    evidence_ids: list[str] | None = None,
    at: datetime.datetime | None = None,
    observed: bool = True,
    scope: str = "global",
) -> bool:
    kernel = ensure_kernel(world, at)
    source_id = _text(source_id, 180)
    kind = _text(kind, 80)
    if not source_id or not kind:
        return False
    trace_id = hashlib.sha256(f"{kind}:{source_id}".encode()).hexdigest()[:24]
    traces = kernel["causal_traces"]
    if any(item.get("id") == trace_id for item in traces if isinstance(item, dict)):
        return False
    refs = [_text(item, 120) for item in (evidence_ids or []) if _text(item, 120)]
    _bounded_append(
        traces,
        {
            "id": trace_id,
            "kind": kind,
            "scope": _text(scope, 180) or "global",
            "source_id": source_id,
            "at": _now_text(at),
            "before": before or {},
            "after": after or {},
            "consequence": _text(consequence, 360),
            "evidence_ids": refs[:12],
            "observed": observed,
        },
        _TRACE_LIMIT,
    )
    return True


def record_action_outcome(
    world: dict[str, Any],
    action: Any,
    *,
    status: str,
    completed_at: datetime.datetime | None = None,
    reason: str = "",
    run: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """把动作回执变成可跨日追踪的结果和未完事项。"""

    action_data = _as_action_dict(action)
    action_id = _text(action_data.get("action_id"), 180)
    action_type = _text(action_data.get("action_type"), 60)
    target = _text(action_data.get("target"), 240)
    status = _text(status, 40) or "unknown"
    if status == "completed":
        status = "committed"
    if not action_id or not action_type:
        return None
    kernel = ensure_kernel(world, completed_at)
    outcome_id = hashlib.sha256(f"{action_id}:{status}".encode()).hexdigest()[:24]
    outcomes = kernel["action_outcomes"]
    if any(item.get("id") == outcome_id for item in outcomes if isinstance(item, dict)):
        return next(
            item
            for item in outcomes
            if isinstance(item, dict) and item.get("id") == outcome_id
        )
    payload = (
        action_data.get("payload")
        if isinstance(action_data.get("payload"), dict)
        else {}
    )
    observed_minutes = 0.0
    if (
        isinstance(run, dict)
        and (run.get("action") or {}).get("action_id") == action_id
    ):
        try:
            observed_minutes = max(0.0, float(run.get("active_seconds") or 0) / 60)
        except (TypeError, ValueError):
            observed_minutes = 0.0
    try:
        planned_minutes = max(0.0, float(action_data.get("duration_minutes") or 0))
    except (TypeError, ValueError):
        planned_minutes = 0.0
    if status == "committed":
        result = (
            "完成一段学习过程，是否掌握尚未验证"
            if action_type == "study"
            else "完成一次实际动作回执，外部成果以已保存证据为准"
        )
    else:
        result = f"动作未完成：{status}"
    evidence = [
        _text(item, 180)
        for item in [action_data.get("evidence"), reason]
        if _text(item, 180)
    ]
    outcome = {
        "id": outcome_id,
        "action_id": action_id,
        "action_type": action_type,
        "target": target,
        "status": status,
        "at": _now_text(completed_at),
        "planned_minutes": planned_minutes,
        "observed_minutes": round(observed_minutes, 2),
        "result": result,
        "anticipated_obstacle": _text(payload.get("obstacle"), 240),
        "obstacle": "",
        "next_step": _text(payload.get("next_step"), 240),
        "next_step_source": "action_plan",
        "planned_artifact": _text(payload.get("artifact"), 240),
        "artifact": None,
        "evidence_ids": evidence[:12],
        "observed": True,
    }
    _bounded_append(outcomes, outcome, _OUTCOME_LIMIT)
    record_event(
        world,
        kind="action_outcome",
        source_id=outcome_id,
        at=completed_at,
        summary=f"动作结果：{target or action_type}（{status}）",
        evidence_ids=[action_id, *evidence],
    )
    before_body = (
        (run or {}).get("body_before")
        if isinstance(run, dict)
        and (run.get("action") or {}).get("action_id") == action_id
        else None
    )
    record_causal_trace(
        world,
        source_id=outcome_id,
        kind="action_consequence",
        before={"body": before_body} if before_body else {},
        after={"body": world.get("body") or {}},
        consequence=f"{target or action_type}：{result}",
        evidence_ids=[action_id, *evidence],
        at=completed_at,
    )
    goal_id = _text((run or {}).get("goal_id") or payload.get("goal_id"), 160)
    if action_type in {"work", "study", "exercise", "chore"} and (
        action_type in {"work", "study"}
        or payload.get("thread_id")
        or goal_id
        or payload.get("next_step")
    ):
        thread_id = _text(payload.get("thread_id") or goal_id, 160)
        if not thread_id:
            thread_id = f"{action_type}:{target}"[:160]
        threads = kernel["open_threads"]
        thread = next(
            (
                item
                for item in threads
                if isinstance(item, dict) and item.get("id") == thread_id
            ),
            None,
        )
        if thread is None:
            thread = {
                "id": thread_id,
                "title": target,
                "status": "open",
                "practice_minutes": 0.0,
                "evidence_ids": [],
            }
            threads.append(thread)
        thread.update(
            title=target or thread.get("title") or action_type,
            status="open",
            last_action_id=action_id,
            last_at=outcome["at"],
            next_step=outcome["next_step"],
            obstacle=outcome["obstacle"],
            goal_id=goal_id,
        )
        thread["practice_minutes"] = round(
            float(thread.get("practice_minutes") or 0) + observed_minutes, 2
        )
        thread["evidence_ids"] = list(
            dict.fromkeys([*(thread.get("evidence_ids") or []), action_id])
        )[-12:]
        kernel["open_threads"] = threads[-_THREAD_LIMIT:]
        outcome["thread_id"] = thread_id
        record_causal_trace(
            world,
            source_id=f"{thread_id}:{action_id}",
            kind="thread_continuation",
            before={"status": "open"},
            after={"status": thread["status"], "next_step": thread.get("next_step")},
            consequence="保留未完事项，后续自主判断可继续推进",
            evidence_ids=[action_id],
            at=completed_at,
        )
    return outcome


def apply_action_reflections(
    world: dict[str, Any],
    reflections: Any,
    *,
    allowed_action_ids: set[str],
    now: datetime.datetime,
) -> None:
    """把事后判断与观察事实分开保存，笔记正文属于本轮实际产生的数字成果。"""

    if not isinstance(reflections, list):
        return
    kernel = ensure_kernel(world, now)
    for raw in reflections[:2]:
        if not isinstance(raw, dict):
            continue
        action_id = _text(raw.get("action_id"), 180)
        summary = _text(raw.get("summary"), 360)
        if action_id not in allowed_action_ids or not summary:
            continue
        outcome = next(
            (
                item
                for item in kernel["action_outcomes"]
                if isinstance(item, dict) and item.get("action_id") == action_id
            ),
            None,
        )
        if not outcome or outcome.get("reflection"):
            continue
        outcome["reflection"] = {
            "summary": summary,
            "at": _now_text(now),
            "source": "post_action_interpretation",
            "evidence_ids": [action_id],
            "observed": False,
        }
        outcome["obstacle"] = _text(raw.get("obstacle"), 240)
        outcome["next_step"] = _text(raw.get("next_step"), 240)
        outcome["next_step_source"] = "post_action_plan"
        artifact = raw.get("artifact")
        if isinstance(artifact, dict) and artifact.get("kind") in {
            "practice_note",
            "work_draft",
        }:
            content = str(artifact.get("content") or "").strip()[:1800]
            if content and outcome.get("status") == "committed":
                outcome["artifact"] = {
                    "kind": artifact["kind"],
                    "content": content,
                    "created_at": _now_text(now),
                    "source": "post_action_generation",
                    "evidence_ids": [action_id],
                }
        thread = next(
            (
                item
                for item in kernel["open_threads"]
                if isinstance(item, dict) and item.get("id") == outcome.get("thread_id")
            ),
            None,
        )
        if thread and thread.get("last_action_id") == action_id:
            thread.update(
                next_step=outcome["next_step"],
                obstacle=outcome["obstacle"],
                next_step_source="post_action_plan",
            )
            goal = next(
                (
                    item
                    for item in world.get("goals", [])
                    if item.get("id") == thread.get("goal_id")
                ),
                None,
            )
            if (
                raw.get("close_thread") is True
                and outcome.get("status") == "committed"
                and (
                    not thread.get("goal_id")
                    or (goal and goal.get("status") in {"completed", "abandoned"})
                )
            ):
                thread["status"] = "completed"
        record_event(
            world,
            kind="action_reflection",
            source_id=action_id,
            at=now,
            summary=f"整理行动结果：{outcome.get('target') or outcome.get('action_type')}",
            evidence_ids=[action_id],
        )
        record_causal_trace(
            world,
            kind="action_reflection",
            source_id=action_id,
            before={"status": outcome.get("status")},
            after={
                "next_step": outcome["next_step"],
                "artifact_generated": bool(outcome["artifact"]),
            },
            consequence=summary,
            evidence_ids=[action_id],
            at=now,
            observed=False,
        )


def sync_kernel_from_day(
    day: Any,
    world: dict[str, Any],
    *,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """把现有模块的当前状态投影到统一快照，不覆盖权威身体数值。"""

    kernel = ensure_kernel(world, now)
    state = getattr(day, "state", None)
    body = world.get("body") or {}
    if state is not None:
        kernel["affect"] = {
            "mood": _text(getattr(state, "mood", ""), 100),
            "mood_score": _score(getattr(state, "mood_score", 50), 50),
            "stress": _score(getattr(state, "stress", 50), 50),
            "stability": _score(getattr(state, "emotional_stability", 50), 50),
            "summary": _text(getattr(state, "summary", ""), 360),
            "updated_at": _now_text(now),
        }
        kernel["attention"] = {
            "capacity": _score(getattr(state, "interaction_capacity", 50), 50),
            "openness": _score(getattr(state, "attention_openness", 50), 50),
            "watch_state": _text(getattr(state, "watch_state", ""), 40),
            "interrupt_level": _text(getattr(state, "interrupt_level", ""), 40),
            "reason": _text(getattr(state, "interrupt_reason", ""), 180),
            "updated_at": _now_text(now),
        }
    kernel["environment"] = {
        "date": _text(getattr(day, "date", ""), 20),
        "place": _text((getattr(day, "meta", {}) or {}).get("current_place"), 160),
        "outfit": _text(getattr(day, "outfit", ""), 360),
        "weather": _text(getattr(day, "weather", ""), 240),
        "updated_at": _now_text(now),
    }
    social = kernel["social"]
    social["battery"] = _score(body.get("social_battery"), social.get("battery", 50))
    social["last_scope"] = _text(world.get("last_chat_scope"), 180)
    social["last_contact_at"] = _text(world.get("last_chat_at"), 40)
    kernel["memory"]["recent_evidence_ids"] = [
        _text(value, 180)
        for value in world.get("chat_events", [])[-12:]
        if _text(value, 180)
    ]
    kernel["agency"] = {
        "run_status": _text((world.get("run") or {}).get("status"), 40) or "idle",
        "current_action_id": _text(
            ((world.get("run") or {}).get("action") or {}).get("action_id"), 180
        ),
        "goal_count": len(world.get("goals", []))
        if isinstance(world.get("goals"), list)
        else 0,
    }
    kernel["self_model"]["capabilities"] = [
        {
            "name": _text(name, 160),
            "practice_minutes": item.get("practice_minutes", 0),
            "sessions": item.get("sessions", 0),
            "verified_mastery": False,
        }
        for name, item in (world.get("skills") or {}).items()
        if isinstance(item, dict)
    ][-20:]
    kernel["updated_at"] = _now_text(now)
    return kernel


def kernel_context(world: dict[str, Any]) -> str:
    """生成给聊天、状态和自主判断共同使用的事实摘要。"""

    kernel = ensure_kernel(world)
    affect = kernel.get("affect") or {}
    attention = kernel.get("attention") or {}
    environment = kernel.get("environment") or {}
    social = kernel.get("social") or {}
    agency = kernel.get("agency") or {}
    model = kernel.get("self_model") or {}
    lines = [
        "统一生命内核：",
        "- 连续性：自身行动、互动和记忆只以已观察事实延续，计划与推测不算经历。",
        f"- 当前环境：{environment.get('place') or '未记录'}；穿搭：{environment.get('outfit') or '未记录'}；天气：{environment.get('weather') or '未记录'}。",
        f"- 情绪：{affect.get('mood') or '未记录'}；心情 {_score(affect.get('mood_score'), 50):.0f}/100；压力 {_score(affect.get('stress'), 50):.0f}/100；稳定度 {_score(affect.get('stability'), 50):.0f}/100。",
        f"- 注意力：回应余力 {_score(attention.get('capacity'), 50):.0f}/100；开放度 {_score(attention.get('openness'), 50):.0f}/100；观看姿态 {attention.get('watch_state') or '未记录'}。",
        f"- 社交电量：{_score(social.get('battery'), 50):.0f}/100；最近会话：{social.get('last_scope') or '无'}。",
        f"- 自主性：{agency.get('run_status') or '空闲'}；当前行动编号：{agency.get('current_action_id') or '无'}；自身目标：{agency.get('goal_count', 0)} 个。",
    ]
    interests = [
        _text(item.get("label"), 100)
        for item in model.get("interests", [])
        if isinstance(item, dict) and _text(item.get("label"), 100)
    ]
    if interests:
        lines.append(f"- 自我模型中的持续关注：{'、'.join(interests[-6:])}。")
    affect_layers = kernel.get("affect_layers") or {}
    if isinstance(affect_layers, dict) and affect_layers:
        lines.append(
            "- 已结算的分层情绪："
            + "；".join(
                f"{_text(item.get('label'), 80)}（{_text(item.get('layer'), 40)}，强度 {float(item.get('intensity') or 0):.2f}）"
                for item in list(affect_layers.values())[-4:]
                if isinstance(item, dict)
            )
        )
    for field, label in (("values", "价值取向"), ("boundaries", "自身边界")):
        values = [item for item in model.get(field, []) if isinstance(item, dict)]
        if values:
            lines.append(
                f"- {label}："
                + "；".join(
                    f"{_text(item.get('label'), 100)}（{'有支持' if item.get('status') == 'supported' else '暂定'}）"
                    for item in values[-4:]
                )
            )
    threads = [
        item
        for item in kernel.get("open_threads", [])
        if isinstance(item, dict) and item.get("status") == "open"
    ]
    if threads:
        lines.append(
            "- 尚未完成的自身事项："
            + "；".join(
                f"{_text(item.get('title'), 100)}"
                f"（下一步：{_text(item.get('next_step'), 100) or '待重新判断'}）"
                for item in threads[-5:]
            )
        )
    events = [item for item in kernel.get("events", [])[-5:] if isinstance(item, dict)]
    if events:
        lines.append(
            "- 最近已观察事实："
            + "；".join(
                f"{item.get('at', '')} {item.get('summary') or item.get('kind')}"
                for item in events
            )
        )
    reflection = kernel.get("reflection") or {}
    if reflection.get("summary"):
        lines.append(f"- 最近自我复盘：{reflection['summary']}")
    outcomes = [
        item
        for item in kernel.get("action_outcomes", [])[-4:]
        if isinstance(item, dict)
    ]
    if outcomes:
        lines.append(
            "- 最近行动结果："
            + "；".join(
                f"{_text(item.get('target') or item.get('action_type'), 90)}"
                f"={_text(item.get('result'), 120)}"
                for item in outcomes
            )
        )
        for item in outcomes[-2:]:
            if item.get("reflection"):
                lines.append(
                    f"- 事后判断（不是观察事实）：{_text(item['reflection'].get('summary'), 200)}。"
                )
            artifact = item.get("artifact")
            if isinstance(artifact, dict) and artifact.get("content"):
                lines.append(
                    f"- 已保存的本轮数字笔记/草稿：{_text(artifact['content'], 600)}。"
                )
    traces = [
        item
        for item in kernel.get("causal_traces", [])
        if isinstance(item, dict) and item.get("scope", "global") == "global"
    ][-3:]
    if traces:
        lines.append(
            "- 最近因果记录："
            + "；".join(_text(item.get("consequence"), 120) for item in traces)
        )
    body_traces = [
        item
        for item in kernel.get("causal_traces", [])
        if isinstance(item, dict) and item.get("kind") == "action_consequence"
    ][-2:]
    for item in body_traces:
        before = (item.get("before") or {}).get("body") or {}
        after = (item.get("after") or {}).get("body") or {}
        changes = [
            f"{label} {_score(after[key]) - _score(before[key]):+.1f}"
            for key, label in (
                ("energy", "体力"),
                ("fatigue", "疲劳"),
                ("sleep_pressure", "困倦"),
                ("hunger", "饥饿"),
                ("thirst", "口渴"),
            )
            if key in before and key in after
        ]
        if changes:
            lines.append(
                f"- 行动前后已观察身体变化：{item.get('consequence') or ''}；{'、'.join(changes)}。"
            )
    autobiography = [
        item for item in kernel.get("autobiography", [])[-3:] if isinstance(item, dict)
    ]
    if autobiography:
        lines.append(
            "- 自传索引："
            + "；".join(
                _text(item.get("summary") or item.get("kind"), 120)
                for item in autobiography
            )
        )
    return "\n".join(lines)
