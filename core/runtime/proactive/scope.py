from __future__ import annotations

import asyncio
import datetime
import hashlib
from typing import Any, Protocol

from ...clock import local_datetime
from ...clock import now as life_now
from ...config.options.cast import as_bool
from ...life.tools import build_time_context, timeline_item_datetime
from ...sources.events import is_chat_message_event
from .procontext import ProactiveSyntheticEvent


class ProactiveScopeComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _AIR_MAX_RECENT_MESSAGES: Any
    _candidate_pending_count: Any
    _evaluate_idle_candidate: Any
    _event_group_meta: Any
    _event_has_command_handler: Any
    _event_is_directed: Any
    _event_is_group_message: Any
    _event_message_id: Any
    _event_session_id: Any
    _observe_proactive_reply_effect: Any
    _proactive_allowed_for_event: Any
    _proactive_chat_enabled: Any
    _proactive_feedback_watches: Any
    _proactive_idle_candidates: Any
    _proactive_idle_seconds: Any
    _proactive_current_day: Any
    _refresh_private_proactive_timing: Any
    _proactive_idle_tasks: Any
    _proactive_is_self_message: Any
    _proactive_last_reply_at: Any
    _proactive_lifecycle_snapshot: Any
    _proactive_scope_key: Any
    _recent_messages_for_candidate: Any
    _record_virtual_life_metric: Any
    _reset_proactive_air_state: Any
    _runtime_initialized: Any
    _safe_event_call: Any
    _schedule_background_task: Any
    _schedule_proactive_idle_evaluation: Any
    _transition_proactive_lifecycle: Any
    _virtual_life_metrics: Any
    archive: Any
    config: Any
    format_structured_message_context: Any
    get_text_provider: Any
    raw_config: Any
    runtime_service_lease: Any


class ProactiveScopeComponent:
    def __init__(self, context: ProactiveScopeComponentContext) -> None:
        self.context = context

    def remove_recalled_proactive_candidate(self, scope: str, message_id: str) -> None:
        """清理所属会话的撤回线索，取消依赖该消息的主动发送。"""
        candidates = getattr(self.context, "_proactive_idle_candidates", None)
        if not isinstance(candidates, dict):
            return
        for key, candidate in list(candidates.items()):
            if (
                not isinstance(candidate, dict)
                or str(candidate.get("target_scope") or "").strip() != scope
            ):
                continue
            if str(candidate.get("message_id") or "").strip() == message_id:
                candidates.pop(key, None)
                self._cancel_proactive_idle_task(key)
                continue
            original = list(candidate.get("recent_messages") or [])
            kept = [
                item for item in original
                if str(item.get("message_id") or "").strip() != message_id
            ]
            if len(kept) != len(original):
                candidate["recent_messages"] = kept
                candidate["pending_count"] = len(kept)

    def _record_virtual_life_metric(self, name: str, amount: int = 1) -> None:
        metrics = getattr(self.context, "_virtual_life_metrics", None)
        if not isinstance(metrics, dict):
            metrics = {}
            self.context._virtual_life_metrics = metrics
        key = str(name or "unknown").strip() or "unknown"
        metrics[key] = int(metrics.get(key, 0)) + int(amount)

    def _proactive_idle_task_store(self) -> dict[str, asyncio.Task]:
        tasks = getattr(self.context, "_proactive_idle_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self.context._proactive_idle_tasks = tasks
        return tasks

    def _cancel_proactive_idle_task(self, key: str) -> None:
        task = self._proactive_idle_task_store().pop(str(key or ""), None)
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _cancel_proactive_idle_tasks(self) -> None:
        tasks = list(self._proactive_idle_task_store().values())
        self._proactive_idle_task_store().clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _proactive_candidate_delay_seconds(
        self, candidate: dict[str, Any], now: datetime.datetime
    ) -> float:
        due_at = candidate.get("next_evaluation_at")
        opportunity_at = candidate.get("private_opportunity_at")
        if isinstance(opportunity_at, datetime.datetime):
            due_at = (
                max(due_at, opportunity_at)
                if isinstance(due_at, datetime.datetime)
                else opportunity_at
            )
        if not isinstance(due_at, datetime.datetime):
            last_activity_at = candidate.get("last_activity_at")
            if not isinstance(last_activity_at, datetime.datetime):
                return 1.0
            due_at = last_activity_at + datetime.timedelta(
                seconds=self.context._proactive_idle_seconds(candidate)
            )
        hold_until = candidate.get("observe_hold_until")
        if isinstance(hold_until, datetime.datetime) and hold_until > due_at:
            due_at = hold_until
        return max(1.0, (due_at - now).total_seconds())

    def _schedule_proactive_idle_evaluation(
        self, key: str, *, now: datetime.datetime | None = None
    ) -> bool:
        if not bool(getattr(self.context, "_runtime_initialized", False)):
            return False
        candidate = self.context._proactive_idle_candidates.get(key)
        if not isinstance(candidate, dict) or not self.context._proactive_chat_enabled(
            bool(candidate.get("is_group"))
        ):
            self._cancel_proactive_idle_task(key)
            return False
        self._cancel_proactive_idle_task(key)
        revision = int(candidate.get("revision") or 0)
        scheduled_at = now or life_now()

        async def runner() -> None:
            try:
                check_at = scheduled_at
                while True:
                    if (
                        self.context._proactive_idle_candidates.get(key) is not candidate
                        or not self.context._proactive_chat_enabled(
                            bool(candidate.get("is_group"))
                        )
                    ):
                        return
                    await self.context._refresh_private_proactive_timing(
                        candidate, check_at
                    )
                    if self.context._proactive_idle_candidates.get(key) is not candidate:
                        return
                    delay = self._proactive_candidate_delay_seconds(candidate, check_at)
                    # 此处仅刷新本地生活事实；语义评估等待合适时机再进行。
                    if not candidate.get("is_group") and delay > 5 * 60:
                        await asyncio.sleep(5 * 60)
                        check_at = life_now()
                        continue
                    await asyncio.sleep(delay)
                    break
                current = self.context._proactive_idle_candidates.get(key)
                if (
                    not isinstance(current, dict)
                    or int(current.get("revision") or 0) != revision
                ):
                    return
                lease = getattr(self.context, "runtime_service_lease", None)
                if callable(lease):
                    async with lease():
                        await self.context._evaluate_idle_candidate(key, current, life_now())
                else:
                    await self.context._evaluate_idle_candidate(key, current, life_now())
            except asyncio.CancelledError:
                raise
            finally:
                tasks = self._proactive_idle_task_store()
                owns_slot = tasks.get(key) is asyncio.current_task()
                if owns_slot:
                    tasks.pop(key, None)
                current = self.context._proactive_idle_candidates.get(key)
                if (
                    owns_slot
                    and isinstance(current, dict)
                    and int(current.get("revision") or 0) == revision
                    and self.context._proactive_chat_enabled(bool(current.get("is_group")))
                ):
                    self.context._schedule_proactive_idle_evaluation(key)

        task = asyncio.create_task(runner(), name=f"daily-life-idle:{key}")
        self._proactive_idle_task_store()[key] = task
        self.context._record_virtual_life_metric("idle_scheduled")
        return True

    def _proactive_chat_enabled(self, is_group: bool) -> bool:
        field = "group_enabled" if is_group else "private_enabled"
        config = getattr(self.context.config, "proactive", None)
        parsed_value = bool(getattr(config, field, False)) if config else False

        raw_config = getattr(self.context, "raw_config", None)
        raw_section = (
            raw_config.get("proactive_config") if isinstance(raw_config, dict) else None
        )
        if isinstance(raw_section, dict) and field in raw_section:
            return as_bool(raw_section.get(field), parsed_value)
        return parsed_value

    def _proactive_idle_enabled(self) -> bool:
        return self.context._proactive_chat_enabled(True) or self.context._proactive_chat_enabled(False)

    def _prune_disabled_proactive_candidates(self) -> int:
        candidates = getattr(self.context, "_proactive_idle_candidates", None)
        if not isinstance(candidates, dict):
            return 0
        removed = 0
        for key, candidate in list(candidates.items()):
            is_group = (
                bool(candidate.get("is_group"))
                if isinstance(candidate, dict)
                else False
            )
            if self.context._proactive_chat_enabled(is_group):
                continue
            if isinstance(candidate, dict):
                self.context._transition_proactive_lifecycle(
                    key,
                    "abandoned",
                    event="channel_disabled",
                    reason="主动回复通道已关闭",
                    revision=int(candidate.get("revision") or 0),
                    candidate=candidate,
                )
            candidates.pop(key, None)
            self._cancel_proactive_idle_task(key)
            removed += 1
        return removed

    async def _get_proactive_provider(self):
        provider_id = self.context.config.proactive.provider
        return await self.context.get_text_provider(provider_id)

    def _proactive_scope_key(self, event: Any) -> str:
        group_id, _ = self.context._event_group_meta(event)
        session_id = self.context._event_session_id(event)
        return group_id or session_id or self.context._safe_event_call(event, "get_sender_id")

    def _proactive_is_self_message(self, event: Any) -> bool:
        sender_id = self.context._safe_event_call(event, "get_sender_id")
        self_id = self.context._safe_event_call(event, "get_self_id")
        if sender_id and self_id and sender_id == self_id:
            return True
        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        return isinstance(raw_message, dict) and bool(
            raw_message.get("_proactive_send")
        )

    def _proactive_allowed_for_event(
        self, event: Any, *, continuing: bool = False
    ) -> bool:
        config = self.context.config.proactive
        if event.is_stopped() or bool(getattr(event, "_has_send_oper", False)):
            return False
        if self.context._event_has_command_handler(event):
            return False
        if self.context._proactive_is_self_message(event):
            return False
        message = str(getattr(event, "message_str", "") or "").strip()
        if not continuing and (not message or len(message) < config.min_message_length):
            return False
        is_group = self.context._event_is_group_message(event)
        if not self.context._proactive_chat_enabled(is_group):
            return False
        if not continuing and is_group and self.context._event_is_directed(event):
            return False
        return True

    def _proactive_idle_seconds(self, event_or_candidate: Any) -> int:
        if isinstance(event_or_candidate, dict):
            is_group = bool(event_or_candidate.get("is_group"))
        else:
            is_group = self.context._event_is_group_message(event_or_candidate)
        minutes = (
            self.context.config.proactive.idle_minutes
            if is_group
            else self.context.config.proactive.private_idle_minutes
        )
        return max(1, int(minutes or 30)) * 60

    async def _refresh_private_proactive_timing(
        self, candidate: dict[str, Any], now: datetime.datetime
    ) -> None:
        """结合当前生活节奏，重新评估私聊联系时机。

        Args:
            candidate: 对话时间锚点及可变的调度元数据。
            now: 生活时区的当前时间。
        """
        if candidate.get("is_group"):
            return
        anchor = local_datetime(candidate.get("last_activity_at"))
        if anchor is None:
            return
        scope = str(candidate.get("target_scope") or candidate.get("key") or "")
        reference = self.context._proactive_idle_seconds(candidate)
        # 同一会话在轮询和重载时保持相同的随机变化值。
        seed = hashlib.blake2s(
            f"{scope}|{anchor.isoformat()}".encode(), digest_size=8
        ).digest()
        variation = int.from_bytes(seed, "big") / (2**64 - 1)
        willingness = self.context.config.proactive.private_talk_frequency
        rest_seconds = max(120, int(300 * (1 - 0.6 * willingness)), int(reference * 0.15))
        _, _, day = await self.context._proactive_current_day(now)
        state = getattr(day, "state", None)
        values = state.as_dict() if hasattr(state, "as_dict") else state
        values = values if isinstance(values, dict) else {}
        # 避免旧状态导致今天的联系时机无故提前或推迟。
        updated_at = local_datetime(values.get("updated_at"))
        if (
            updated_at is not None
            and not 0 <= (now - updated_at).total_seconds() <= 3 * 3600
        ):
            values = {}
        levels = {}
        for name in ("busyness", "sleepiness", "interaction_capacity", "social"):
            value = values.get(name)
            levels[name] = (
                max(0.0, min(1.0, float(value) / 100))
                if isinstance(value, (int, float))
                else 0.5
            )
        rhythm = (
            1.0
            + 0.7 * (levels["busyness"] - 0.5)
            + 0.6 * (levels["sleepiness"] - 0.5)
            - 0.4 * (levels["interaction_capacity"] - 0.5)
            - 0.3 * (levels["social"] - 0.5)
        )
        delay = max(rest_seconds, int(reference * (0.7 + 0.8 * variation) * rhythm))
        due_at = anchor + datetime.timedelta(seconds=delay)
        reason = "结合当前生活节奏，留出一段自然的聊天间隔再考虑联系"
        occupied = (
            levels["busyness"] >= 0.75
            or levels["sleepiness"] >= 0.8
            or levels["interaction_capacity"] <= 0.25
        )
        if occupied:
            reason = "当前忙碌、困倦或交流余力较低，晚些再考虑联系"
        elif day:
            # 计划中的活动切换只提供重新评估的机会，不能作为活动完成的证据。
            transitions = []
            for item in getattr(day, "timeline", []) or []:
                execution = str(
                    item.get("execution_state", "")
                    if isinstance(item, dict)
                    else getattr(item, "execution_state", "")
                )
                if execution in {"cancelled", "skipped", "expired"}:
                    continue
                transition = timeline_item_datetime(
                    item, day.date, meta=getattr(day, "meta", None)
                )
                if transition is not None and transition > anchor:
                    opportunity = max(
                        anchor + datetime.timedelta(seconds=rest_seconds),
                        transition
                        + datetime.timedelta(seconds=60 + int(variation * 4 * 60)),
                    )
                    transitions.append(opportunity)
            if transitions and min(transitions) < due_at:
                due_at = min(transitions)
                reason = "日程来到新的时间窗口，结合实际进展考虑是否有值得分享的话"
        candidate["private_opportunity_at"] = due_at
        candidate["timing_reason"] = reason

    def _proactive_observe_hold_seconds(self) -> int:
        return 5 * 60

    def _proactive_observe_hold_remaining(
        self, candidate: dict[str, Any], now: datetime.datetime
    ) -> int:
        hold_until = candidate.get("observe_hold_until")
        if isinstance(hold_until, datetime.datetime):
            if hold_until > now:
                return max(1, int((hold_until - now).total_seconds()))
            candidate.pop("observe_hold_until", None)
        return 0

    def _proactive_cooldown_remaining(self, event: Any, now: datetime.datetime) -> int:
        key = self.context._proactive_scope_key(event)
        if not key:
            return 0
        last_reply_at = self.context._proactive_last_reply_at.get(key)
        if not isinstance(last_reply_at, datetime.datetime):
            return 0
        if self.context._event_is_group_message(event):
            cooldown_minutes = self.context.config.proactive.cooldown_minutes
        else:
            cooldown_minutes = self.context.config.proactive.private_cooldown_minutes
        cooldown = max(1, int(cooldown_minutes or 20)) * 60
        remaining = cooldown - int((now - last_reply_at).total_seconds())
        return max(0, remaining)

    def _mark_proactive_reply_sent(self, event: Any, now: datetime.datetime) -> None:
        key = self.context._proactive_scope_key(event)
        if not key:
            return
        previous = self.context._proactive_last_reply_at.get(key)
        if isinstance(previous, datetime.datetime) and previous >= now:
            return
        self.context._proactive_last_reply_at[key] = now
        self.context._reset_proactive_air_state(key)

    def note_proactive_activity(
        self, event: Any, now: datetime.datetime | None = None
    ) -> None:
        if not is_chat_message_event(event):
            return
        key = self.context._proactive_scope_key(event)
        now = now or life_now()
        learning = getattr(getattr(self.context, "config", None), "memory", None)
        if key and (key in self.context._proactive_feedback_watches() or bool(getattr(learning, "enabled", False))):
            self.context._schedule_background_task(
                self.context._observe_proactive_reply_effect(event, now),
                label="闲时回复效果观察",
                key=f"proactive_feedback:{key}:{self.context._event_message_id(event) or now.isoformat()}",
            )
        previous = self.context._proactive_idle_candidates.get(key)
        if not self.context._proactive_allowed_for_event(
            event, continuing=isinstance(previous, dict)
        ):
            return
        target_scope = self.context._event_session_id(event)
        if not key or not target_scope:
            return
        previous_revision = (
            int(previous.get("revision") or 0) if isinstance(previous, dict) else 0
        )
        lifecycle = self.context._proactive_lifecycle_snapshot(key)
        lifecycle_state = str(lifecycle.get("state") or "")
        if lifecycle_state in {"candidate", "considering", "waiting", "sending"}:
            self.context._transition_proactive_lifecycle(
                key,
                "interrupted",
                event="conversation_revision_changed",
                reason="收到新消息，旧主动候选失效",
                now=now,
                revision=previous_revision,
                candidate=previous if isinstance(previous, dict) else None,
            )
        elif lifecycle_state == "engaged":
            self.context._transition_proactive_lifecycle(
                key,
                "closing",
                event="user_response_received",
                reason="对方已回应主动消息",
                now=now,
                revision=previous_revision,
            )
            self.context._transition_proactive_lifecycle(
                key,
                "cooldown",
                event="engagement_closed",
                reason="本轮主动互动已闭环",
                now=now,
                revision=previous_revision,
            )
        group_id, group_name = self.context._event_group_meta(event)
        recent_messages = self.context._recent_messages_for_candidate(
            self.context._proactive_idle_candidates.get(key)
        )
        recent_messages.append(
            {
                "message_id": self.context._event_message_id(event),
                "sender_id": self.context._safe_event_call(event, "get_sender_id"),
                "sender_name": self.context._safe_event_call(event, "get_sender_name"),
                "content": str(getattr(event, "message_str", "") or "").strip(),
                "seen_at": now,
                "structured": self.context.format_structured_message_context(event, limit=4),
            }
        )
        recent_messages = recent_messages[-self.context._AIR_MAX_RECENT_MESSAGES :]
        candidate = {
            "key": key,
            "target_scope": target_scope,
            "message": str(getattr(event, "message_str", "") or "").strip(),
            "message_id": self.context._event_message_id(event),
            "sender_id": self.context._safe_event_call(event, "get_sender_id"),
            "sender_name": self.context._safe_event_call(event, "get_sender_name"),
            "platform_name": self.context._safe_event_call(event, "get_platform_name"),
            "group_id": group_id,
            "group_name": group_name,
            "is_group": self.context._event_is_group_message(event),
            "last_activity_at": now,
            "last_bot_reply_at": (previous or {}).get("last_bot_reply_at"),
            "observe_hold_until": None,
            "next_evaluation_at": (
                now
                + datetime.timedelta(
                    seconds=self.context._proactive_idle_seconds(event)
                )
                if self.context._event_is_group_message(event)
                else None
            ),
            "recent_messages": recent_messages,
            "pending_count": min(
                self.context._AIR_MAX_RECENT_MESSAGES,
                int((previous or {}).get("pending_count") or 0) + 1,
            ),
            "state": "pending_decision",
            "decision": "",
            "decision_reason": "",
            "revision": int(
                (previous if isinstance(previous, dict) else {}).get("revision")
                or previous_revision
            )
            + 1,
        }
        self.context._proactive_idle_candidates[key] = candidate
        self.context._transition_proactive_lifecycle(
            key,
            "candidate",
            event="activity_observed",
            reason="会话活动已进入静默候选观察",
            now=now,
            revision=int(candidate["revision"]),
            candidate=candidate,
        )
        self.context._record_virtual_life_metric("turn_received")
        self.context._schedule_proactive_idle_evaluation(key, now=now)

    def note_conversation_turn_decision(
        self, event: Any, decision: dict[str, Any]
    ) -> None:
        key = self.context._proactive_scope_key(event)
        candidates = getattr(self.context, "_proactive_idle_candidates", None)
        if not isinstance(candidates, dict):
            return
        candidate = candidates.get(key)
        if not isinstance(candidate, dict):
            return
        action = str(decision.get("action") or "").strip()
        candidate["decision"] = action
        candidate["decision_reason"] = str(decision.get("reason") or "").strip()
        if action == "reply":
            candidate["state"] = "reply_now"
            self.context._record_virtual_life_metric("turn_reply_now")
        elif action in {"observe", "wait"}:
            candidate["state"] = "reevaluate_after_silence"
            self.context._record_virtual_life_metric("turn_observe")

    def note_proactive_bot_reply(
        self, event: Any, now: datetime.datetime | None = None
    ) -> None:
        key = self.context._proactive_scope_key(event)
        if not key:
            return
        candidate = self.context._proactive_idle_candidates.get(key)
        if isinstance(candidate, dict):
            # 普通回复结束当前话轮，并开启新的静默窗口。
            reply_at = now or life_now()
            lifecycle = self.context._proactive_lifecycle_snapshot(key)
            if str(lifecycle.get("state") or "") in {
                "considering", "waiting", "sending", "engaged", "closing"
            }:
                self.context._transition_proactive_lifecycle(
                    key,
                    "interrupted",
                    event="ordinary_reply_committed",
                    reason="普通回复已更新会话，旧主动裁定失效",
                    now=reply_at,
                    revision=int(candidate.get("revision") or 0),
                    candidate=candidate,
                )
            candidate = dict(candidate)
            candidate["last_bot_reply_at"] = reply_at
            candidate["last_activity_at"] = reply_at
            candidate["next_evaluation_at"] = (
                reply_at
                + datetime.timedelta(
                    seconds=self.context._proactive_idle_seconds(candidate)
                )
                if candidate.get("is_group")
                else None
            )
            candidate.pop("private_opportunity_at", None)
            candidate.pop("timing_reason", None)
            candidate.pop("idle_observe_count", None)
            candidate.pop("evaluation_in_progress", None)
            candidate["observe_hold_until"] = None
            candidate["state"] = "after_ordinary_reply"
            candidate["decision"] = ""
            candidate["decision_reason"] = "普通回复已完成，等待新的安静窗口"
            if self.context._event_message_id(event) == candidate.get("message_id"):
                candidate["pending_count"] = 0
            candidate["revision"] = int(candidate.get("revision") or 0) + 1
            self.context._proactive_idle_candidates[key] = candidate
            self.context._transition_proactive_lifecycle(
                key,
                "candidate",
                event="ordinary_reply_committed",
                reason="普通聊天已完成回应，重新开始闲时观察窗口",
                now=reply_at,
                revision=int(candidate["revision"]),
                candidate=candidate,
            )
            self.context._schedule_proactive_idle_evaluation(key, now=reply_at)
            self.context._record_virtual_life_metric("turn_closed_by_reply")

    def _proactive_candidate_event(self, candidate: dict[str, Any]) -> Any:
        return ProactiveSyntheticEvent(
            message=str(candidate.get("message") or ""),
            target_scope=str(candidate.get("target_scope") or ""),
            message_id=str(candidate.get("message_id") or ""),
            sender_id=str(candidate.get("sender_id") or ""),
            sender_name=str(candidate.get("sender_name") or ""),
            platform_name=str(candidate.get("platform_name") or ""),
            group_id=str(candidate.get("group_id") or ""),
            group_name=str(candidate.get("group_name") or ""),
            last_activity_at=candidate.get("last_activity_at"),
            last_bot_reply_at=candidate.get("last_bot_reply_at"),
            recent_messages=self.context._recent_messages_for_candidate(candidate),
            pending_count=self.context._candidate_pending_count(candidate),
        )

    async def _proactive_current_day(
        self,
        now: datetime.datetime,
    ) -> tuple[str, bool, Any | None]:
        today_str = now.strftime("%Y-%m-%d")
        time_context = build_time_context(now, self.context.config.schedule_time)
        target_date_str = time_context.business_date_text
        using_extended_night = target_date_str != today_str
        day = await self.context.archive.get_day(target_date_str)
        if not day and using_extended_night:
            yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            day = await self.context.archive.get_day(yesterday)
            if day:
                target_date_str = yesterday
        return target_date_str, using_extended_night, day
