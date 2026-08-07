from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .markers import LOG_PREFIX


@dataclass(slots=True)
class ContinuousTurnBatch:
    revision: int
    first_at: float
    last_at: float
    deadline: float
    phase: str = "collecting"
    messages: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)


class ContinuousTurnMixin:
    """在主模型调用前收束同一用户短时间连续发送的普通消息。"""

    _CONTINUOUS_TURN_SCOPE_ATTR = "_daily_life_continuous_turn_scope"
    _CONTINUOUS_TURN_PARTICIPANT_ATTR = "_daily_life_continuous_turn_participant"
    _CONTINUOUS_TURN_REVISION_ATTR = "_daily_life_continuous_turn_revision"
    _CONTINUOUS_TURN_MESSAGES_ATTR = "_daily_life_continuous_turn_messages"
    _CONTINUOUS_TURN_DEADLINE_ATTR = "_daily_life_continuous_turn_deadline"
    _CONTINUOUS_TURN_STOPPED_ATTR = "_daily_life_continuous_turn_stopped"
    _CONTINUOUS_TURN_WAIT_ATTR = "_daily_life_continuous_turn_wait_seconds"
    _CONTINUOUS_TURN_MAX_MESSAGES = 12
    _CONTINUOUS_TURN_MAX_CHARS = 4000
    _CONTINUOUS_TURN_ACTIVE_SECONDS = 90.0

    def _init_continuous_turn_state(self) -> None:
        self._continuous_turn_batches: dict[str, dict[str, ContinuousTurnBatch]] = {}
        self._continuous_turn_revisions: dict[str, dict[str, int]] = {}
        self._continuous_turn_metrics: dict[str, int] = {
            "registered": 0,
            "merged": 0,
            "superseded": 0,
            "semantic_wait": 0,
            "completed": 0,
        }

    def _continuous_turn_style(self) -> Any | None:
        return getattr(getattr(self, "config", None), "chat_style", None)

    def _continuous_turn_enabled(self) -> bool:
        style = self._continuous_turn_style()
        return bool(
            style
            and getattr(style, "enabled", False)
            and getattr(style, "continuous_turn_enabled", True)
        )

    def _continuous_turn_eligible(self, event: Any) -> bool:
        if not self._continuous_turn_enabled() or event is None:
            return False
        is_stopped = getattr(event, "is_stopped", None)
        if callable(is_stopped) and is_stopped():
            return False
        if bool(getattr(event, "_has_send_oper", False)):
            return False
        command_checker = getattr(self, "_event_has_command_handler", None)
        if callable(command_checker) and command_checker(event):
            return False
        self_checker = getattr(self, "_proactive_is_self_message", None)
        if callable(self_checker) and self_checker(event):
            return False
        quote_checker = getattr(self, "_event_has_quote", None)
        if callable(quote_checker) and quote_checker(event):
            return False
        media_checker = getattr(self, "_response_gate_has_media", None)
        if callable(media_checker) and media_checker(event):
            return False
        text = str(getattr(event, "message_str", "") or "").strip()
        if not text:
            return False
        is_group_checker = getattr(self, "_event_is_group_message", None)
        is_group = (
            bool(is_group_checker(event)) if callable(is_group_checker) else False
        )
        style = self._continuous_turn_style()
        return not is_group or bool(
            getattr(style, "continuous_turn_group_enabled", False)
        )

    def _continuous_turn_identity(self, event: Any) -> tuple[str, str]:
        session_getter = getattr(self, "_event_session_id", None)
        scope = (
            str(session_getter(event) or "").strip()
            if callable(session_getter)
            else str(getattr(event, "unified_msg_origin", "") or "").strip()
        )
        if not scope:
            return "", ""
        is_group_checker = getattr(self, "_event_is_group_message", None)
        is_group = (
            bool(is_group_checker(event)) if callable(is_group_checker) else False
        )
        if not is_group:
            return scope, "private"
        sender_getter = getattr(self, "_safe_event_call", None)
        sender = (
            str(sender_getter(event, "get_sender_id") or "").strip()
            if callable(sender_getter)
            else str(getattr(event, "sender_id", "") or "").strip()
        )
        return scope, sender or "unknown"

    @staticmethod
    def _continuous_turn_message_id(event: Any) -> str:
        getter = getattr(event, "get_message_id", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = ""
        else:
            value = getattr(event, "message_id", "")
        return str(value or f"event:{id(event)}").strip()

    def _continuous_turn_revision(self, scope: str, participant: str) -> int:
        store = getattr(self, "_continuous_turn_revisions", None)
        if not isinstance(store, dict):
            self._init_continuous_turn_state()
            store = self._continuous_turn_revisions
        bucket = store.setdefault(scope, {})
        return int(bucket.get(participant, 0))

    async def _continuous_turn_wait(self, event: Any, delay: float) -> None:
        delay = max(0.0, float(delay or 0.0))
        if delay <= 0:
            return
        started_at = time.monotonic()
        try:
            await asyncio.sleep(delay)
        finally:
            elapsed = max(0.0, time.monotonic() - started_at)
            intentional_wait = min(delay, elapsed)
            previous = self.continuous_turn_intentional_wait_seconds(event)
            setattr(
                event,
                self._CONTINUOUS_TURN_WAIT_ATTR,
                previous + intentional_wait,
            )

    def continuous_turn_intentional_wait_seconds(self, event: Any) -> float:
        try:
            return max(
                0.0,
                float(getattr(event, self._CONTINUOUS_TURN_WAIT_ATTR, 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return 0.0

    def _continuous_turn_batch(
        self, scope: str, participant: str
    ) -> ContinuousTurnBatch | None:
        store = getattr(self, "_continuous_turn_batches", None)
        if not isinstance(store, dict):
            return None
        bucket = store.get(scope)
        if not isinstance(bucket, dict):
            return None
        batch = bucket.get(participant)
        return batch if isinstance(batch, ContinuousTurnBatch) else None

    def _continuous_turn_trim_messages(
        self, messages: list[str], message_ids: list[str]
    ) -> tuple[list[str], list[str]]:
        while len(messages) > self._CONTINUOUS_TURN_MAX_MESSAGES:
            messages.pop(0)
            message_ids.pop(0)
        while (
            len(messages) > 1
            and sum(len(item) for item in messages) > self._CONTINUOUS_TURN_MAX_CHARS
        ):
            messages.pop(0)
            message_ids.pop(0)
        if messages and len(messages[0]) > self._CONTINUOUS_TURN_MAX_CHARS:
            messages[0] = messages[0][-self._CONTINUOUS_TURN_MAX_CHARS :]
        return messages, message_ids

    def note_continuous_turn_incoming(self, event: Any) -> bool:
        if not self._continuous_turn_eligible(event):
            return False
        scope, participant = self._continuous_turn_identity(event)
        if not scope:
            return False
        style = self._continuous_turn_style()
        now = time.monotonic()
        max_wait = max(
            0.0, float(getattr(style, "continuous_turn_max_wait_seconds", 4.0) or 0.0)
        )
        revisions = getattr(self, "_continuous_turn_revisions", None)
        batches = getattr(self, "_continuous_turn_batches", None)
        if not isinstance(revisions, dict) or not isinstance(batches, dict):
            self._init_continuous_turn_state()
            revisions = self._continuous_turn_revisions
            batches = self._continuous_turn_batches
        revision_bucket = revisions.setdefault(scope, {})
        revision = int(revision_bucket.get(participant, 0)) + 1
        revision_bucket[participant] = revision
        batch_bucket = batches.setdefault(scope, {})
        previous = batch_bucket.get(participant)
        active = bool(
            isinstance(previous, ContinuousTurnBatch)
            and previous.phase in {"collecting", "generating", "waiting"}
            and now - previous.last_at <= self._CONTINUOUS_TURN_ACTIVE_SECONDS
        )
        messages = list(previous.messages) if active else []
        message_ids = list(previous.message_ids) if active else []
        message_id = self._continuous_turn_message_id(event)
        text = str(getattr(event, "message_str", "") or "").strip()
        if message_id not in message_ids:
            messages.append(text)
            message_ids.append(message_id)
        messages, message_ids = self._continuous_turn_trim_messages(
            messages, message_ids
        )
        first_at = previous.first_at if active else now
        batch = ContinuousTurnBatch(
            revision=revision,
            first_at=first_at,
            last_at=now,
            deadline=first_at + max_wait,
            messages=messages,
            message_ids=message_ids,
        )
        batch_bucket[participant] = batch
        setattr(event, self._CONTINUOUS_TURN_SCOPE_ATTR, scope)
        setattr(event, self._CONTINUOUS_TURN_PARTICIPANT_ATTR, participant)
        setattr(event, self._CONTINUOUS_TURN_REVISION_ATTR, revision)
        setattr(event, self._CONTINUOUS_TURN_DEADLINE_ATTR, batch.deadline)
        self._continuous_turn_metrics["registered"] += 1
        if len(messages) > 1:
            self._continuous_turn_metrics["merged"] += 1
        return True

    def _continuous_turn_event_identity(
        self, event: Any
    ) -> tuple[str, str, int] | None:
        scope = str(getattr(event, self._CONTINUOUS_TURN_SCOPE_ATTR, "") or "").strip()
        participant = str(
            getattr(event, self._CONTINUOUS_TURN_PARTICIPANT_ATTR, "") or ""
        ).strip()
        try:
            revision = int(getattr(event, self._CONTINUOUS_TURN_REVISION_ATTR, 0) or 0)
        except (TypeError, ValueError):
            revision = 0
        if not scope or not participant or revision <= 0:
            return None
        return scope, participant, revision

    def continuous_turn_event_is_current(self, event: Any) -> bool:
        identity = self._continuous_turn_event_identity(event)
        if identity is None:
            return True
        scope, participant, revision = identity
        return self._continuous_turn_revision(scope, participant) == revision

    @staticmethod
    def _continuous_turn_stop_event(event: Any) -> None:
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        llm_setter = getattr(event, "should_call_llm", None)
        if callable(llm_setter):
            llm_setter(True)
        else:
            setattr(event, "call_llm", True)
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()

    def stop_stale_continuous_turn_event(self, event: Any) -> bool:
        if self._continuous_turn_event_identity(event) is None:
            return False
        if self.continuous_turn_event_is_current(event):
            return False
        self._continuous_turn_stop_event(event)
        if not bool(getattr(event, self._CONTINUOUS_TURN_STOPPED_ATTR, False)):
            setattr(event, self._CONTINUOUS_TURN_STOPPED_ATTR, True)
            self._continuous_turn_metrics["superseded"] += 1
            logger.debug(f"{LOG_PREFIX} 连续消息旧话轮已由后续消息接管。")
        return True

    async def settle_continuous_turn(self, event: Any) -> bool:
        identity = self._continuous_turn_event_identity(event)
        if identity is None:
            return True
        scope, participant, revision = identity
        batch = self._continuous_turn_batch(scope, participant)
        if batch is None or batch.revision != revision:
            self.stop_stale_continuous_turn_event(event)
            return False
        style = self._continuous_turn_style()
        wait_seconds = max(
            0.0, float(getattr(style, "continuous_turn_wait_seconds", 1.5) or 0.0)
        )
        remaining = max(0.0, batch.deadline - time.monotonic())
        delay = min(wait_seconds, remaining)
        if delay > 0:
            await self._continuous_turn_wait(event, delay)
        if not self.continuous_turn_event_is_current(event):
            self.stop_stale_continuous_turn_event(event)
            return False
        batch = self._continuous_turn_batch(scope, participant)
        if batch is None or batch.revision != revision:
            self.stop_stale_continuous_turn_event(event)
            return False
        batch.phase = "generating"
        messages = tuple(item for item in batch.messages if item)
        setattr(event, self._CONTINUOUS_TURN_MESSAGES_ATTR, messages)
        setattr(event, self._CONTINUOUS_TURN_DEADLINE_ATTR, batch.deadline)
        if len(messages) > 1:
            logger.debug(
                f"{LOG_PREFIX} 连续消息已收束：{len(messages)} 条合并为一个话轮。"
            )
        return True

    def continuous_turn_messages(self, event: Any) -> tuple[str, ...]:
        values = getattr(event, self._CONTINUOUS_TURN_MESSAGES_ATTR, ())
        if isinstance(values, (list, tuple)):
            return tuple(str(item).strip() for item in values if str(item).strip())
        return ()

    def continuous_turn_message_count(self, event: Any) -> int:
        return len(self.continuous_turn_messages(event))

    def continuous_turn_semantic_enabled_for_event(self, event: Any) -> bool:
        style = self._continuous_turn_style()
        return bool(
            self._continuous_turn_event_identity(event) is not None
            and self.continuous_turn_event_is_current(event)
            and style
            and getattr(style, "continuous_turn_semantic_enabled", True)
        )

    async def wait_continuous_turn_after_semantic(self, event: Any) -> str:
        identity = self._continuous_turn_event_identity(event)
        if identity is None:
            return "disabled"
        if not self.continuous_turn_event_is_current(event):
            self.stop_stale_continuous_turn_event(event)
            return "superseded"
        style = self._continuous_turn_style()
        if not bool(style and getattr(style, "continuous_turn_semantic_enabled", True)):
            return "reply"
        try:
            deadline = float(
                getattr(event, self._CONTINUOUS_TURN_DEADLINE_ATTR, 0.0) or 0.0
            )
        except (TypeError, ValueError):
            deadline = 0.0
        remaining = max(0.0, deadline - time.monotonic())
        batch = self._continuous_turn_batch(identity[0], identity[1])
        if batch is not None:
            batch.phase = "waiting"
        if remaining > 0:
            await self._continuous_turn_wait(event, remaining)
        if not self.continuous_turn_event_is_current(event):
            self.stop_stale_continuous_turn_event(event)
            return "superseded"
        batch = self._continuous_turn_batch(identity[0], identity[1])
        if batch is not None:
            batch.phase = "generating"
        self._continuous_turn_metrics["semantic_wait"] += 1
        return "reply"

    def prepare_continuous_turn_llm_request(self, event: Any, request: Any) -> bool:
        if self.stop_stale_continuous_turn_event(event):
            return False
        messages = self.continuous_turn_messages(event)
        if len(messages) < 2:
            return True
        request.prompt = "\n".join(messages)
        request.system_prompt = (
            str(getattr(request, "system_prompt", "") or "")
            + "\n\n[HiddenContinuousTurn]\n"
            + "当前用户输入由同一人在短时间内连续发送，属于同一个话轮。"
            + "结合全部内容统一回应，不要把每条消息分别重复回答。"
        )
        return True

    def complete_continuous_turn(self, event: Any) -> bool:
        identity = self._continuous_turn_event_identity(event)
        if identity is None or not self.continuous_turn_event_is_current(event):
            return False
        scope, participant, revision = identity
        batch = self._continuous_turn_batch(scope, participant)
        if batch is None or batch.revision != revision:
            return False
        batch.phase = "completed"
        batch.messages.clear()
        batch.message_ids.clear()
        batch.last_at = time.monotonic()
        self._continuous_turn_metrics["completed"] += 1
        return True


__all__ = ["ContinuousTurnBatch", "ContinuousTurnMixin"]
