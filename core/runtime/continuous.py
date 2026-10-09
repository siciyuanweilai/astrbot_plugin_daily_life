from __future__ import annotations

import asyncio
import copy
import datetime
import hashlib
import json
import uuid
from typing import Any

from astrbot.api import logger

from ..life.body import ContinuousBody, instant, project_body
from ..life.goals import apply_goal_decisions, credit_goal, ready_steps
from ..life.presence import (
    apply_action_reflections,
    apply_self_model_updates,
    kernel_context,
    record_action_outcome,
    record_event,
    sync_kernel_from_day,
    update_self_model,
)
from ..life.settlement import _ACTION_RULES
from ..life.tools import (
    extract_json_from_text,
    timeline_deferred_until,
    timeline_item_datetime,
)
from ..models import INTERNAL_SIMULATED_ACTION_TYPES, LifeActionIntent
from ..prompts import cache_friendly_prompt
from .locks import operation_lock
from .markers import LOG_PREFIX

_MAX_OBSERVED_GAP_SECONDS = 180
_TERMINAL_STATES = frozenset({"completed", "skipped", "cancelled", "expired"})


def planned_actions(day) -> dict[str, LifeActionIntent]:
    try:
        values = json.loads(day.meta.get("planned_life_actions") or "[]")
    except (ValueError, TypeError):
        return {}
    return (
        {
            action.action_id: action
            for raw in values
            if isinstance(raw, dict)
            if (action := LifeActionIntent.from_value(raw)).action_id
        }
        if isinstance(values, list)
        else {}
    )


def attached_action(day, run: dict[str, Any]) -> LifeActionIntent | None:
    original = LifeActionIntent.from_value(run["action"])
    if original.timeline_index is None:
        return original
    current = planned_actions(day).get(original.action_id)
    if (
        current is None
        or current.action_type != original.action_type
        or current.target != original.target
    ):
        return None
    if current.timeline_index is None or not 0 <= current.timeline_index < len(
        day.timeline
    ):
        return None
    if (
        current.payload != original.payload
        or current.preconditions != original.preconditions
        or (
            current.duration_minutes
            or day.timeline[current.timeline_index].duration_minutes
        )
        != original.duration_minutes
    ):
        return None
    original.timeline_index = current.timeline_index
    return original


def sleep_delayed(day, run, now) -> bool:
    if run.get("rest_kind") != "sleep":
        return False
    until = instant(day.meta.get("rest_delay_until"))
    return bool(until and until > now)


def next_decision_time(day, world, now):
    run = world.get("run") or {}
    remaining = max(
        1,
        float(run.get("action", {}).get("duration_minutes", 5))
        - float(run.get("active_seconds") or 0) / 60,
    )
    minutes = min(30, remaining) if run.get("status") == "running" else 5
    due = now + datetime.timedelta(minutes=minutes)
    for item in day.timeline:
        if item.execution_state in _TERMINAL_STATES:
            continue
        at = timeline_item_datetime(item, day.date, meta=day.meta)
        if at and now < at < due:
            due = at
    return due


class ContinuousLifeMixin:
    """独立于聊天和对外联系的持续生活执行器。"""

    def _continuous_observer_id(self) -> str:
        token = getattr(self, "_continuous_observer_token", None)
        if token is None:
            token = self._continuous_observer_token = uuid.uuid4().hex
        return token

    async def sync_continuous_kernel_state(
        self,
        day,
        *,
        now: datetime.datetime | None = None,
        event_kind: str = "",
        source_id: str = "",
        summary: str = "",
        evidence_ids: list[str] | None = None,
    ) -> None:
        """让外部状态刷新也回写统一生命内核。"""

        if day is None:
            return
        observed_at = now or self._runtime_now().replace(tzinfo=None)

        def sync(latest, world):
            sync_kernel_from_day(latest, world, now=observed_at)
            if event_kind and source_id:
                record_event(
                    world,
                    kind=event_kind,
                    source_id=source_id,
                    at=observed_at,
                    summary=summary,
                    evidence_ids=evidence_ids,
                )

        await self.archive.mutate_continuous_life(day.date, sync)

    async def _continuous_life_decision(self, day, world, now) -> dict[str, Any]:
        candidates = []
        for action in planned_actions(day).values():
            if (
                action.action_type not in INTERNAL_SIMULATED_ACTION_TYPES
                or action.timeline_index is None
                or not 0 <= action.timeline_index < len(day.timeline)
            ):
                continue
            item = day.timeline[action.timeline_index]
            at = timeline_item_datetime(item, day.date, meta=day.meta)
            deferred = timeline_deferred_until(item, day.date, day.meta)
            if (
                at is None
                or at > now
                or (deferred and deferred > now)
                or item.execution_state in _TERMINAL_STATES
            ):
                continue
            duration = action.duration_minutes or item.duration_minutes
            if duration <= 0 or now > at + datetime.timedelta(minutes=duration):
                continue
            action.duration_minutes = duration
            candidates.append(
                {**action.as_dict(), "activity": item.activity, "place": item.place}
            )
        persona = await self.composer._get_persona()
        focus = await self.archive.get_focus_slots(limit=6)
        preferences = await self.archive.get_preferences(limit=12)
        sources = {"persona", f"state:{day.date}:{day.revision}"}
        sources.update(f"focus:{item.id}" for item in focus)
        sources.update(f"preference:{item.id}" for item in preferences)
        kernel = world.get("kernel") if isinstance(world.get("kernel"), dict) else {}
        unreflected_outcomes = [
            item
            for item in kernel.get("action_outcomes", [])
            if isinstance(item, dict)
            and not item.get("reflection")
            and item.get("action_type") in {"study", "work", "exercise", "chore"}
        ][-2:]
        sources.update(f"action:{item['action_id']}" for item in unreflected_outcomes)
        sources.add(f"persona:{hashlib.sha256(str(persona).encode()).hexdigest()[:16]}")
        latest_exchange = []
        chat_scope = world.get("last_chat_scope")
        chat_reader = getattr(self, "_read_recent_context_messages", None)
        if chat_scope and callable(chat_reader):
            try:
                messages = await asyncio.wait_for(chat_reader(chat_scope), timeout=3)
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 当前会话暂未读取完成，继续依据已有生活事实：{type(exc).__name__}"
                )
                messages = []
            latest_exchange = [
                {
                    "role": item.get("role"),
                    "name": item.get("name"),
                    "content": str(item.get("content") or "")[:1200],
                    "message_id": item.get("message_id"),
                }
                for item in messages[-6:]
                if isinstance(item, dict)
            ]
            if latest_exchange and world.get("chat_events"):
                sources.add(f"chat:{world['chat_events'][-1]}")
        goal_source_key = hashlib.sha256(
            json.dumps(
                [
                    persona,
                    [item.as_dict() for item in focus],
                    [item.as_dict() for item in preferences],
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        last_review = instant(world.get("goal_review_at"))
        goal_review_due = (
            goal_source_key != world.get("goal_source_key")
            or last_review is None
            or now - last_review >= datetime.timedelta(hours=6)
        )
        # 同一组事实先决定动作，再校验；模型不直接写入执行结果或身体数值。
        fixed = """为当前角色选择接下来实际要执行的一项内部数字生活行动，并维护少量能持续数周的自身目标。
只输出 JSON：
{"decision":"start|continue|pause|resume|cancel|wait", "action_id":"候选动作编号或空", "new_action":{"owner":"self","action_type":"内部动作类型","target":"具体行动","duration_minutes":10,"payload":{}}, "rest_kind":"break|sleep", "reason":"当前事实依据", "goal_id":"可选已有目标编号", "step_id":"可选可继续阶段编号", "new_goals":[{"owner":"self","source_id":"来源编号","title":"自身长期目标","reason":"为什么想做","skill":"可选练习能力名称","steps":[{"id":"阶段编号","title":"具体练习阶段","action_type":"内部动作类型","required_minutes":60,"depends_on":[]}]}], "goal_updates":[{"goal_id":"已有目标编号","status":"active|blocked|abandoned","evidence_id":"来源编号","reason":"原因","remaining_steps":[]}]}
模型只选择和解释，执行器负责时间、前置条件、库存、完成回执及进度。时间轴是计划，不能因为时间过去就声称完成。
有运行中动作时优先 continue；新证据使其不合适时 pause/cancel。暂停动作可 resume，未观测的停机时间不算执行时间。
start 优先引用候选 action_id；确需即时喝水、用餐、休息或自主工作学习时可给 new_action。所有行动只属于当前角色，不替用户执行，不发消息、不生图、不拍视频、不调用现实采购或支付。
内部类型：rest, drink, meal, cook, order_food, purchase, move, travel, work, study, chore, exercise, groom, change_outfit。
动作不由时钟、关键词或固定轮换表选择。结合实际身体需求、当前地点、聊天打断、未完成约定及角色兴趣；需求数值只是依据，不是到阈值必做。
睡眠用 rest_kind=sleep，普通休息用 break；尊重已确认的延后休息安排。未明确决定睡眠时不能把普通休息当睡觉。
new_action.payload 保留明确领域参数：cook.ingredients 必须来自现有库存；drink 可选 volume_ml；exercise.intensity 为1-5。新移动、旅行或换装必须引用已有候选，不能凭空改地点或编衣服。
长期目标只属于角色自身。来源必须是明确支持角色兴趣、意愿或已经确认的自身目标的 persona、preference 或 focus 编号；不要把用户的愿望、猜测或普通话题当成角色目标。没有依据可以不建目标。
最多三个进行中目标，不要求每天创建；已有目标自然延续。阶段写可实际执行的练习与时间要求，依赖只能引用同一目标的阶段编号。练习累计表示做过，不等于已掌握技能或产出外部成果。
仅当所选行动确实推进已有可继续阶段时填写 goal_id/step_id；不得关联尚未建立的目标、已完成阶段或依赖未完成阶段。
仅在 goal_review_due=true 时提出新目标。已有目标出现本轮状态或对话证据支持的阻碍、意愿变化时，可以暂停、放弃或恢复；remaining_steps 仅替换未开始的阶段，可以依赖保留的已完成阶段，不重写已经发生的练习、完成证据或能力记录。
普通闲聊不必停止内部活动；需要集中回应、共同活动、已确认延后休息或明确换装变化时审视当前行动。保持自然简短，不输出要发给用户的旁白。"""
        fixed += """
统一生命内核是聊天、主动回访、状态刷新和自主行动共同读取的同一事实快照。只把已观察的真实对话、行动回执、身体变化和复盘写入经历；不得把模型提议、时间轴到点或计划中的成果写成已发生。
如果 life_kernel 中有尚未完成的自身事项，优先考虑自然延续；使用其 thread_id，保持同一事项跨日衔接。new_action.payload 可使用 thread_id、next_step、obstacle、artifact 表达后续计划，obstacle 和 artifact 此时都是预期，不能当作已发生。
可增加两个 JSON 数组，没有充分依据时返回空数组：
"action_reflections":[{"action_id":"仅可引用 unreflected_outcomes 中的编号","summary":"对真实执行过程的事后判断","obstacle":"有证据的困难，没有则空","next_step":"可执行的后续计划","close_thread":false,"artifact":{"kind":"practice_note|work_draft","content":"本轮实际整理的练习笔记或工作草稿，允许留空"}}]。
每次最多整理两项行动。时间和回执只能证明练习过，不能宣称已经掌握，也不能杜撰拍过的照片、看过的材料或写过的外部文件。生成的笔记或草稿是本轮数字成果，不能叙述为此前已经产生的作品。已完成的单次事项可以 close_thread=true，仍需继续的兴趣和长期练习保持打开。
"self_model_updates":[{"owner":"self","field":"interest|value|boundary","id":"稳定编号","text":"自己的兴趣、价值或边界","source_ids":["allowed_source_ids 中的完整编号"],"reason":"证据怎样支持这一认识"}]。
仅在 goal_review_due=true 时审视自我模型，每次最多四项，小步变化。自我认识必须属于当前角色，普通话题和用户自身愿望不构成角色兴趣。新认知可以暂定，持续的不同证据才逐步稳定；不得把练习时间直接解释成能力认证。
"""
        try:
            recent_chats = await asyncio.wait_for(
                self.composer._collect_recent_chat_context(persona), timeout=5
            )
        except asyncio.TimeoutError:
            recent_chats = "参考会话暂未读取完成，优先依据当前已确认对话和生活事实"
        dynamic = {
            "now": now.isoformat(),
            "persona": persona,
            "allowed_source_ids": sorted(sources),
            "preferences": [item.as_dict() for item in preferences],
            "focus": [item.as_dict() for item in focus],
            "life_kernel": kernel_context(world),
            "self_model": kernel.get("self_model") or {},
            "unreflected_outcomes": unreflected_outcomes,
            "open_threads": [
                item
                for item in (
                    world.get("kernel", {}).get("open_threads", [])
                    if isinstance(world.get("kernel"), dict)
                    else []
                )
                if isinstance(item, dict) and item.get("status") == "open"
            ][-12:],
            "state": day.state.as_dict() if day.state else {},
            "weather": day.weather,
            "current_place": day.meta.get("current_place", ""),
            "outfit": day.outfit,
            "wardrobe_context": day.meta.get("wardrobe_context", ""),
            "rest_delay_until": day.meta.get("rest_delay_until", ""),
            "candidates": candidates,
            "world": {
                "body": world.get("body"),
                "run": world.get("run"),
                "goals": [
                    {
                        **{
                            key: goal.get(key)
                            for key in (
                                "id",
                                "title",
                                "status",
                                "reason",
                                "skill",
                                "obstacle",
                            )
                        },
                        "steps": [
                            {
                                key: step.get(key)
                                for key in (
                                    "id",
                                    "title",
                                    "status",
                                    "action_type",
                                    "required_minutes",
                                    "practice_minutes",
                                    "depends_on",
                                )
                            }
                            for step in goal["steps"]
                        ],
                    }
                    for goal in world.get("goals", [])
                    if goal.get("status") in {"active", "blocked"}
                ],
                "skills": world.get("skills"),
            },
            "recent_internal_actions": world.get("history", [])[-4:],
            "recent_chats": recent_chats[-3000:],
            "pantry": await self.archive.get_pantry_items(limit=30),
            "latest_exchange": latest_exchange,
            "latest_exchange_at": world.get("last_chat_at", ""),
            "latest_exchange_scope": chat_scope or "",
            "recent_state_evidence": day.state_log[-4:],
            "goal_review_due": goal_review_due,
            "upcoming_anchors": [
                {"at": at.isoformat(), "activity": item.activity, "place": item.place}
                for item in day.timeline
                if item.execution_state not in _TERMINAL_STATES
                and (at := timeline_item_datetime(item, day.date, meta=day.meta))
                and now < at <= now + datetime.timedelta(hours=1)
            ][:4],
        }
        provider = await self.get_text_provider(self.config.state.provider)
        if provider is None:
            return {}
        session = f"daily_life_execution_{uuid.uuid4().hex[:8]}"
        try:
            text = await self.call_text_model(
                provider,
                cache_friendly_prompt(fixed, json.dumps(dynamic, ensure_ascii=False)),
                session,
                empty_retries=0,
                primary_provider_id=self.config.state.provider,
                timeout_seconds=min(45, self.config.llm_timeout_seconds),
            )
            payload = extract_json_from_text(text)
            if isinstance(payload, dict):
                payload["_sources"] = sources
                payload["_candidates"] = candidates
                payload["_goal_review_due"] = goal_review_due
                payload["_goal_source_key"] = goal_source_key
                payload["_reflection_action_ids"] = {
                    item["action_id"] for item in unreflected_outcomes
                }
                payload["_self_model"] = {
                    "persona": persona,
                    "preferences": dynamic["preferences"],
                    "focus": dynamic["focus"],
                    "skills": dynamic["world"].get("skills") or {},
                    "evidence_ids": sorted(sources),
                }
                return payload
            return {}
        finally:
            await self.composer._cleanup_conversation(session)

    def _advance_continuous_world(self, day, world, now) -> None:
        body = ContinuousBody.from_value(world.get("body"), day)
        previous = instant(body.updated_at)
        seconds = max(0.0, (now - previous).total_seconds()) if previous else 0.0
        run = world.get("run")
        activity = "idle"
        sleeping = False
        intensity = 1.0
        observer_id = self._continuous_observer_id()
        restarted = previous is not None and world.get("observer_id") != observer_id
        if seconds > _MAX_OBSERVED_GAP_SECONDS or restarted:
            body.uncertain_minutes += seconds / 60
            body.advance(min(seconds / 60, 120))
            if isinstance(run, dict) and run["status"] == "running":
                run["status"] = "paused"
                run["reason"] = "存在未观测的运行间隔，等待重新判断，不补造完成经历"
                record_event(
                    world,
                    kind="action_pause",
                    source_id=f"{run['action']['action_id']}:observation_gap:{now.isoformat()}",
                    at=now,
                    summary="因未观测的运行间隔暂停行动",
                    evidence_ids=[run["action"]["action_id"]],
                )
            world["observation_gap_at"] = now.isoformat()
            world.pop("next_decision_at", None)
            seconds = 0
        world["observer_id"] = observer_id
        if isinstance(run, dict) and run["status"] == "running":
            action = attached_action(day, run)
            cancelled = action is None or (
                action.timeline_index is not None
                and day.timeline[action.timeline_index].execution_state
                in _TERMINAL_STATES
            )
            cancelled = cancelled or bool(
                action
                and self.composer._planned_outfit_action_is_superseded(day, action)
            )
            delayed = sleep_delayed(day, run, now) or (
                action is not None
                and action.timeline_index is not None
                and (
                    until := timeline_deferred_until(
                        day.timeline[action.timeline_index], day.date, day.meta
                    )
                )
                and until > now
            )
            if cancelled or delayed:
                run.update(
                    status="cancelled" if cancelled else "paused",
                    reason="原行动已调整或休息已顺延",
                    changed_at=now.isoformat(),
                )
                world.pop("next_decision_at", None)
                record_event(
                    world,
                    kind="action_cancel" if cancelled else "action_pause",
                    source_id=f"{run['action']['action_id']}:plan_change:{now.isoformat()}",
                    at=now,
                    summary="原行动已调整或休息已顺延",
                    evidence_ids=[run["action"]["action_id"]],
                )
            else:
                run["action"] = action.as_dict()
                remaining = max(
                    0,
                    action.duration_minutes * 60
                    - float(run.get("active_seconds") or 0),
                )
                observed = min(seconds, remaining)
                run["active_seconds"] = float(run.get("active_seconds") or 0) + observed
                activity = action.action_type
                sleeping = activity == "rest" and run.get("rest_kind") == "sleep"
                intensity = float(
                    action.payload.get("intensity") or action.payload.get("effort") or 1
                )
                body.advance(
                    observed / 60,
                    activity=activity,
                    sleeping=sleeping,
                    intensity=intensity,
                )
                body.advance((seconds - observed) / 60)
                if run["active_seconds"] >= action.duration_minutes * 60:
                    run["status"] = "ready"
                    run["finished_at"] = now.isoformat()
                    world.pop("next_decision_at", None)
                seconds = 0
        body.advance(seconds / 60)
        body.updated_at = (
            max(now, previous).isoformat() if previous else now.isoformat()
        )
        world["body"] = body.as_dict()
        project_body(day, body)
        if day.state is not None and world.get("sleeping") and not sleeping:
            day.state.sleep.depth = "awake"
        if sleeping and day.state is not None:
            day.state.sleep.depth = (
                "deep_sleep"
                if float(run.get("active_seconds") or 0) >= 2700
                else "light_sleep"
            )
        world["sleeping"] = sleeping

    async def _finish_continuous_action(self, run, now) -> None:
        day = await self.archive.get_day(run["date"])
        if day is None:
            return
        action = attached_action(day, run)
        if action is None:
            await self._close_continuous_run(run, "cancelled", "原行动已被调整", now)
            return
        reason = f"自主执行器已记录开始和 {run['active_seconds'] / 60:.1f} 分钟执行过程：{run['reason']}"
        completed_at = instant(run.get("finished_at")) or now
        valid, validation_reason = (
            await self.domains.validate_action(action)
            if run["status"] != "settling"
            else (True, "")
        )
        if not valid:
            await self._close_continuous_run(run, "failed", validation_reason, now)
            return
        outcome = None

        def settle(latest, world):
            nonlocal outcome
            current = world.get("run")
            if (
                not current
                or current["action"]["action_id"] != action.action_id
                or current["status"] not in {"ready", "settling"}
            ):
                return False
            rebound = attached_action(latest, current)
            if rebound is None or (
                rebound.timeline_index is not None
                and latest.timeline[rebound.timeline_index].execution_state
                in {"cancelled", "skipped", "expired"}
            ):
                current.update(status="cancelled", reason="执行结果提交前原行动已调整")
                return
            rebound.evidence = reason
            if (
                rebound.timeline_index is None
                and rebound.action_id not in planned_actions(latest)
            ):
                actions = [*planned_actions(latest).values(), rebound]
                latest.meta["planned_life_actions"] = json.dumps(
                    [item.as_dict() for item in actions], ensure_ascii=False
                )
            if self.composer._planned_outfit_action_is_superseded(latest, rebound):
                current.update(
                    status="cancelled", reason="实际穿搭已由新的聊天决定更新"
                )
                return
            inventory_reason = (
                self.archive.consume_continuous_ingredients_unlocked(rebound)
                if self.config.domains.pantry_enabled
                else ""
            )
            if inventory_reason:
                current.update(status="failed", reason=inventory_reason)
                return
            outcome = self.composer.settle_life_action(
                latest, rebound, now=completed_at
            )
            if outcome.status != "committed":
                current.update(status="failed", reason=outcome.reason)
                return
            if self.config.domains.pantry_enabled:
                self.archive.consume_continuous_ingredients_unlocked(
                    rebound, consume=True, occurred_at=completed_at.isoformat(sep=" ")
                )
            if not current.get("body_credited"):
                body = ContinuousBody.from_value(
                    json.loads(latest.meta["continuous_body"])
                )
                world["body"] = body.as_dict()
                project_body(latest, body)
                current["body_credited"] = True
            record_action_outcome(
                world,
                rebound,
                status="committed",
                completed_at=completed_at,
                reason=reason,
                run=current,
            )
            current["action"] = rebound.as_dict()
            current["status"] = "settling"

        day, world = await self.archive.mutate_continuous_life(run["date"], settle)
        if outcome is None or outcome.status != "committed":
            return
        current = world["run"]
        # 状态与执行检查点已经原子落库；重启只重放同一回执并补齐派生记录。
        action = LifeActionIntent.from_value(current["action"])
        await self.composer._save_action_receipt(
            day,
            action,
            {
                "receipt_id": f"autonomous:{action.action_id}",
                "status": "simulated",
                "source": "continuous_executor",
                "source_id": action.action_id,
                "occurred_at": completed_at.isoformat(sep=" "),
                "evidence": [reason],
            },
            now=completed_at,
        )
        await self.composer.settle_and_persist_life_action(
            day,
            action,
            now=completed_at,
            receipt_status="simulated",
            fact_source="continuous_executor",
            fact_evidence=reason,
        )

        def finish(latest, stored):
            executing = stored.get("run")
            if not executing or executing["action"]["action_id"] != action.action_id:
                return False
            credit_goal(stored, executing, completed_at)
            record_action_outcome(
                stored,
                action,
                status="committed",
                completed_at=completed_at,
                reason=reason,
                run=executing,
            )
            executing.update(
                status="completed", finished_at=completed_at.isoformat(), reason=reason
            )
            record_event(
                stored,
                kind="action_completed",
                source_id=f"{action.action_id}:completed",
                at=completed_at,
                summary=f"自主行动已完成：{action.target}",
                evidence_ids=[reason],
            )
            stored.setdefault("history", []).append(copy.deepcopy(executing))
            stored["history"] = stored["history"][-40:]
            stored.pop("run", None)
            stored.pop("next_decision_at", None)
            stored["sleeping"] = False
            project_body(latest, ContinuousBody.from_value(stored["body"]))

        await self.archive.mutate_continuous_life(run["date"], finish)

    async def _close_continuous_run(self, run, status, reason, now):
        action = LifeActionIntent.from_value(run["action"])
        day = await self.archive.get_day(run["date"])
        if day is not None:
            rebound = attached_action(day, run)
            if rebound is None:
                action.timeline_index = None
            else:
                action = rebound
            if (
                self.composer._load_action_settlements(day)
                .get(action.action_id, {})
                .get("status")
                == "committed"
            ):
                status, reason = "completed", "相同行动已有确认回执，不重复结算"
        if day is not None and status in {"failed", "cancelled"}:
            await self.composer.record_life_action_receipt(
                day,
                action.action_id,
                {
                    "receipt_id": f"autonomous:{action.action_id}:{status}",
                    "status": status,
                    "source": "continuous_executor",
                    "evidence": [reason],
                },
                now=now,
                planned_action=action,
            )

        def close(latest, world):
            current = world.get("run")
            if not current or current["action"]["action_id"] != action.action_id:
                return False
            current.update(status=status, reason=reason, finished_at=now.isoformat())
            record_event(
                world,
                kind=f"action_{status}",
                source_id=f"{action.action_id}:{status}",
                at=now,
                summary=f"自主行动{status}：{action.target}",
                evidence_ids=[reason],
            )
            if action.action_type in {"work", "study", "exercise", "chore"}:
                credit_goal(world, current, now)
            record_action_outcome(
                world,
                action,
                status=status,
                completed_at=now,
                reason=reason,
                run=current,
            )
            world.setdefault("history", []).append(copy.deepcopy(current))
            world["history"] = world["history"][-40:]
            world.pop("run", None)
            world.pop("next_decision_at", None)
            world["sleeping"] = False

        await self.archive.mutate_continuous_life(run["date"], close)

    async def check_continuous_life(self) -> None:
        if (
            not self.config.domains.enabled
            or not self.config.domains.simulate_internal_actions
        ):
            return
        lock = getattr(self, "_continuous_life_lock", None)
        if lock is None:
            lock = self._continuous_life_lock = asyncio.Lock()
        if lock.locked():
            return
        async with lock:
            try:
                await self._check_continuous_life_once()
            except Exception as exc:
                logger.warning(
                    f"{LOG_PREFIX} 持续生活检查暂未完成，保留执行检查点：{type(exc).__name__}"
                )

    async def _check_continuous_life_once(self) -> None:
        now = self._runtime_now().replace(tzinfo=None)
        date, _ = await self.resolve_injection_target(now)
        world = await self.archive.get_continuous_life()
        active_date = (world.get("run") or {}).get("date") or date
        day, world = await self.archive.mutate_continuous_life(
            active_date,
            lambda day, world: self._advance_continuous_world(day, world, now),
        )
        if day is None:
            if active_date == date:
                return

            def detach(latest, stored):
                missing = stored.pop("run", None)
                if missing:
                    missing.update(
                        status="cancelled",
                        reason="原业务日记录已清理",
                        finished_at=now.isoformat(),
                    )
                    stored.setdefault("history", []).append(missing)
                    stored["history"] = stored["history"][-40:]
                    stored.pop("next_decision_at", None)
                self._advance_continuous_world(latest, stored, now)

            day, world = await self.archive.mutate_continuous_life(date, detach)
            active_date = date
            if day is None:
                return
        run = world.get("run")
        if run and run["status"] in {"ready", "settling"}:
            async with operation_lock(
                self, f"continuous_action:{run['action']['action_id']}"
            ):
                await self._finish_continuous_action(run, now)
            world = await self.archive.get_continuous_life()
        elif run and run["status"] in {"failed", "cancelled"}:
            await self._close_continuous_run(run, run["status"], run["reason"], now)
            world = await self.archive.get_continuous_life()
        if active_date != date:
            day, world = await self.archive.mutate_continuous_life(
                date,
                lambda day, world: project_body(
                    day, ContinuousBody.from_value(world.get("body"), day)
                ),
            )
            if day is None:
                return
        else:
            day = await self.archive.get_day(date)
        await self.mark_page_status_changed("continuous_life")
        due = instant(world.get("next_decision_at"))
        if due and due > now:
            return
        try:
            payload = await self._continuous_life_decision(day, world, now)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 自主行动选择暂未完成：{type(exc).__name__}")
            payload = {}
        if not payload:
            expected_revision = world.get("revision")

            def retry(latest, stored):
                if stored.get("revision") != expected_revision:
                    return False
                stored["next_decision_at"] = next_decision_time(
                    latest, {}, self._runtime_now().replace(tzinfo=None)
                ).isoformat()

            await self.archive.mutate_continuous_life(date, retry)
            return
        await self._apply_continuous_decision(
            day, world, payload, self._runtime_now().replace(tzinfo=None)
        )

    async def _apply_continuous_decision(self, day, snapshot, payload, now):
        decision = payload.get("decision")
        reason = str(payload.get("reason") or "").strip()[:240]
        if not reason or decision not in {
            "start",
            "continue",
            "pause",
            "resume",
            "cancel",
            "wait",
        }:
            return
        action = None
        if decision == "start" and not snapshot.get("run"):
            candidate = next(
                (
                    item
                    for item in payload.get("_candidates", [])
                    if item["action_id"] == payload.get("action_id")
                ),
                None,
            )
            if candidate:
                action = LifeActionIntent.from_value(candidate)
            else:
                raw = payload.get("new_action")
                if isinstance(raw, dict) and raw.get("owner") == "self":
                    action = LifeActionIntent.from_value(raw)
                    action.action_id = f"self:{uuid.uuid4().hex}"
                    action.timeline_index = None
                    if action.action_type in {"move", "travel", "change_outfit"}:
                        action = None
            if action and (
                action.action_type not in INTERNAL_SIMULATED_ACTION_TYPES
                or not action.duration_minutes
                or not action.target
            ):
                action = None
            if action:
                valid, _ = await self.domains.validate_action(action)
                if not valid:
                    action = None
        expected_revision = int(snapshot.get("revision") or 0)
        resume_valid = True
        if decision == "resume" and snapshot.get("run"):
            resumed = attached_action(day, snapshot["run"])
            resume_valid = (
                resumed is not None and (await self.domains.validate_action(resumed))[0]
            )

        def apply(latest, world):
            if (
                int(world.get("revision") or 0) != expected_revision
                or latest.revision != day.revision
            ):
                return False
            self._advance_continuous_world(latest, world, now)
            world["next_decision_at"] = next_decision_time(latest, {}, now).isoformat()
            goal_payload = (
                payload
                if payload.get("_goal_review_due", True)
                else {**payload, "new_goals": []}
            )
            apply_goal_decisions(
                world, goal_payload, sources=payload.get("_sources", set()), now=now
            )
            model_input = payload.get("_self_model")
            if isinstance(model_input, dict):
                update_self_model(
                    world,
                    persona=str(model_input.get("persona") or ""),
                    preferences=model_input.get("preferences") or [],
                    focus=model_input.get("focus") or [],
                    skills=model_input.get("skills") or {},
                    evidence_ids=model_input.get("evidence_ids") or [],
                    now=now,
                )
            apply_action_reflections(
                world,
                payload.get("action_reflections"),
                allowed_action_ids=payload.get("_reflection_action_ids", set()),
                now=now,
            )
            if payload.get("_goal_review_due", True):
                apply_self_model_updates(
                    world,
                    payload.get("self_model_updates"),
                    sources=payload.get("_sources", set()),
                    now=now,
                )
            if payload.get("_goal_review_due", True):
                world.update(
                    goal_review_at=now.isoformat(),
                    goal_source_key=payload.get("_goal_source_key", ""),
                )
            current = world.get("run")
            transition_event = ""
            if current:
                if decision == "cancel" and current["status"] in {"running", "paused"}:
                    current.update(status="cancelled", reason=reason)
                    transition_event = "cancel"
                elif decision == "pause" and current["status"] == "running":
                    current.update(status="paused", reason=reason)
                    transition_event = "pause"
                elif decision == "resume" and current["status"] == "paused":
                    resumed = attached_action(latest, current)
                    if (
                        resume_valid
                        and resumed is not None
                        and not sleep_delayed(latest, current, now)
                        and not self.composer._validate_action_preconditions(
                            latest, latest.state, resumed
                        )
                        and not self.composer._validate_action_contract(
                            latest,
                            resumed,
                            _ACTION_RULES[resumed.action_type],
                            latest.state,
                        )
                    ):
                        current.update(status="running", reason=reason)
                        transition_event = "resume"
                if transition_event:
                    current["changed_at"] = now.isoformat()
                    record_event(
                        world,
                        kind=f"action_{transition_event}",
                        source_id=f"{current['action']['action_id']}:{transition_event}:{now.isoformat()}",
                        at=now,
                        summary=f"{transition_event}：{current['action'].get('target') or current['action'].get('action_type')}",
                        evidence_ids=[reason],
                    )
            elif decision == "start" and action is not None:
                rule = _ACTION_RULES[action.action_type]
                if self.composer._validate_action_contract(
                    latest, action, rule, latest.state
                ) or self.composer._validate_action_preconditions(
                    latest, latest.state, action
                ):
                    return
                if action.timeline_index is not None:
                    item = latest.timeline[action.timeline_index]
                    if item.execution_state in _TERMINAL_STATES:
                        return
                    at = timeline_item_datetime(item, latest.date, meta=latest.meta)
                    if at is None or not at <= now <= at + datetime.timedelta(
                        minutes=action.duration_minutes
                    ):
                        return
                proposed_run = {
                    "rest_kind": "sleep"
                    if payload.get("rest_kind") == "sleep"
                    else "break"
                }
                if (
                    sleep_delayed(latest, proposed_run, now)
                    and action.action_type == "rest"
                ):
                    return
                if self.composer._planned_outfit_action_is_superseded(latest, action):
                    return
                if action.timeline_index is not None:
                    item = latest.timeline[action.timeline_index]
                    item.execution_state = "active"
                    item.execution_evidence = reason
                    item.execution_updated_at = now.isoformat(sep=" ")
                action.requested_at = now.isoformat(sep=" ")
                action.evidence = reason
                action.source = "continuous_executor"
                if action.timeline_index is None:
                    actions = list(planned_actions(latest).values())
                    actions.append(action)
                    latest.meta["planned_life_actions"] = json.dumps(
                        [item.as_dict() for item in actions], ensure_ascii=False
                    )
                world["run"] = {
                    "date": latest.date,
                    "action": action.as_dict(),
                    "status": "running",
                    "started_at": now.isoformat(),
                    "active_seconds": 0.0,
                    "reason": reason,
                    "rest_kind": "sleep"
                    if payload.get("rest_kind") == "sleep"
                    else "break",
                    "goal_id": "",
                    "step_id": "",
                    "body_before": copy.deepcopy(world.get("body") or {}),
                }
                record_event(
                    world,
                    kind="action_started",
                    source_id=action.action_id,
                    at=now,
                    summary=f"开始自主行动：{action.target}",
                    evidence_ids=[reason],
                )
                goal = next(
                    (
                        item
                        for item in world.get("goals", [])
                        if item["id"]
                        == (payload.get("goal_id") or action.payload.get("goal_id"))
                    ),
                    None,
                )
                step = next(
                    (
                        item
                        for item in ready_steps(goal or {})
                        if item["id"]
                        == (payload.get("step_id") or action.payload.get("step_id"))
                        and item["action_type"] == action.action_type
                    ),
                    None,
                )
                if step:
                    world["run"].update(goal_id=goal["id"], step_id=step["id"])
            running = world.get("run") or {}
            world["sleeping"] = (
                running.get("status") == "running"
                and running.get("action", {}).get("action_type") == "rest"
                and running.get("rest_kind") == "sleep"
            )
            world["next_decision_at"] = next_decision_time(
                latest, world, now
            ).isoformat()

        await self.archive.mutate_continuous_life(day.date, apply)
        await self.mark_page_status_changed("continuous_life")

    async def note_continuous_chat_exchange(self, event) -> None:
        """只记真实已发送的对话证据，不在消息钩子内调用模型。"""
        if (
            not self.config.domains.enabled
            or not self.config.domains.simulate_internal_actions
        ):
            return
        message_id = self._event_message_id(event)
        scope = self._event_session_id(event)
        if not message_id or not scope:
            return
        now = self._runtime_now().replace(tzinfo=None)
        date, _ = await self.resolve_injection_target(now)
        snapshot = await self.archive.get_continuous_life()
        date = (snapshot.get("run") or {}).get("date") or date
        token = f"{scope}:{message_id}"

        def record(day, world):
            if token in world.get("chat_events", []):
                return False
            if not world.get("body"):
                initial = ContinuousBody.from_value(None, day)
                initial.updated_at = now.isoformat()
                world["body"] = initial.as_dict()
            body = ContinuousBody.from_value(world["body"], day)
            last_exchange = instant(world.get("last_chat_at"))
            previous_tick = instant(body.updated_at)
            # 社交消耗只覆盖已确认连续交流的间隔，首条消息不凭空补时长。
            ongoing_chat = (
                last_exchange
                and previous_tick
                and 0
                <= (now - last_exchange).total_seconds()
                <= _MAX_OBSERVED_GAP_SECONDS
            )
            self._advance_continuous_world(day, world, now)
            if (
                ongoing_chat
                and previous_tick
                and (now - previous_tick).total_seconds() <= _MAX_OBSERVED_GAP_SECONDS
            ):
                body = ContinuousBody.from_value(world["body"])
                observed = max(0, (now - previous_tick).total_seconds()) / 60
                body.social_battery = max(0, body.social_battery - observed * 0.095)
                world["body"] = body.as_dict()
            run = world.get("run") or {}
            if world.get("sleeping") and run.get("status") == "running":
                run.update(
                    status="paused",
                    reason="已实际回应对话，短暂醒来后重新判断睡眠安排",
                    changed_at=now.isoformat(),
                )
                record_event(
                    world,
                    kind="action_pause",
                    source_id=f"{run['action']['action_id']}:chat:{token}",
                    at=now,
                    summary="实际回应对话，短暂醒来后重新判断睡眠安排",
                    evidence_ids=[token],
                )
            world["sleeping"] = False
            world["last_chat_at"] = now.isoformat()
            world["last_chat_scope"] = scope
            record_event(
                world,
                kind="conversation",
                source_id=token,
                at=now,
                summary="完成一次真实对话交换",
                evidence_ids=[token],
            )
            events = world.setdefault("chat_events", [])
            events.append(token)
            world["chat_events"] = events[-128:]
            due = instant(world.get("next_decision_at"))
            reconsider = now + datetime.timedelta(minutes=1)
            if due is None or due > reconsider:
                world["next_decision_at"] = reconsider.isoformat()

        await self.archive.mutate_continuous_life(date, record)
        await self.mark_page_status_changed("continuous_life")
