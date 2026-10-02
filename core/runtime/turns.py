from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger
from astrbot.api.message_components import Image

from .markers import LOG_PREFIX


@dataclass(slots=True)
class ContinuousTurnImages:
    items: list[Any]
    prepared: list[dict[str, Any]] = field(default_factory=list)
    ready: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class ContinuousTurnBatch:
    revision: int
    first_at: float
    last_at: float
    deadline: float
    phase: str = "collecting"
    messages: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    images: dict[str, ContinuousTurnImages] = field(default_factory=dict)
    generation_event: Any = None
    video_reference_source: Any = None
    wait_seconds: float = 0.0


class ContinuousTurnMixin:
    """在主模型调用前收束同一用户短时间连续发送的普通消息。"""

    _CONTINUOUS_TURN_SCOPE_ATTR = "_daily_life_continuous_turn_scope"
    _CONTINUOUS_TURN_PARTICIPANT_ATTR = "_daily_life_continuous_turn_participant"
    _CONTINUOUS_TURN_REVISION_ATTR = "_daily_life_continuous_turn_revision"
    _CONTINUOUS_TURN_MESSAGES_ATTR = "_daily_life_continuous_turn_messages"
    _CONTINUOUS_TURN_DEADLINE_ATTR = "_daily_life_continuous_turn_deadline"
    _CONTINUOUS_TURN_STOPPED_ATTR = "_daily_life_continuous_turn_stopped"
    _CONTINUOUS_TURN_WAIT_ATTR = "_daily_life_continuous_turn_wait_seconds"
    _CONTINUOUS_TURN_FOLLOW_UP_ATTR = "_daily_life_continuous_turn_follow_up"
    _CONTINUOUS_TURN_IMAGES_ATTR = "_daily_life_continuous_turn_images"
    _CONTINUOUS_TURN_RESTART_ATTR = "_daily_life_continuous_turn_restart"
    _CONTINUOUS_TURN_MAX_MESSAGES = 12
    _CONTINUOUS_TURN_MAX_CHARS = 4000
    _CONTINUOUS_TURN_ACTIVE_SECONDS = 90.0
    _CONTINUOUS_TURN_CADENCE_MULTIPLIER = 1.5
    _CONTINUOUS_TURN_MAX_TAIL_RATIO = 0.6

    def _init_continuous_turn_state(self) -> None:
        self._continuous_turn_batches: dict[str, dict[str, ContinuousTurnBatch]] = {}
        self._continuous_turn_revisions: dict[str, dict[str, int]] = {}
        self._continuous_turn_cadence: dict[str, dict[str, float]] = {}
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
            # 普通图片参与图文收束；语音、视频和文件仍由各自入口处理。
            if any(
                token in self._event_component_kind(item)
                for item in self._event_message_items(event)
                for token in ("record", "voice", "video", "file")
            ):
                return False
        text = str(getattr(event, "message_str", "") or "").strip()
        if not text and not self._continuous_turn_image_items(event):
            return False
        is_group_checker = getattr(self, "_event_is_group_message", None)
        is_group = (
            bool(is_group_checker(event)) if callable(is_group_checker) else False
        )
        style = self._continuous_turn_style()
        return not is_group or bool(
            getattr(style, "continuous_turn_group_enabled", False)
        )

    def _continuous_turn_image_items(self, event: Any) -> list[Any]:
        return [
            item
            for item in self._event_message_items(event)
            if "image" in self._event_component_kind(item)
        ]

    def note_continuous_turn_media_ready(self, event: Any) -> None:
        images = getattr(event, self._CONTINUOUS_TURN_IMAGES_ATTR, None)
        if not isinstance(images, ContinuousTurnImages) or images.ready.is_set():
            return
        prepared = getattr(event, self._PREPARED_VISUAL_MEDIA_ATTR, [])
        images.prepared = list(prepared) if isinstance(prepared, list) else []
        # 接管旧事件之前保留固化后的图片，避免框架清理原事件临时文件。
        images.items = [
            next(
                (
                    Image.fromFileSystem(str(entry["path"]))
                    for entry in images.prepared
                    if entry.get("item") is item and entry.get("path")
                ),
                item,
            )
            for item in images.items
        ]
        images.ready.set()

    def _continuous_turn_restart_for_image(self, event: Any) -> None:
        """框架的运行中续话只接收文字，图片需交给新的完整图文请求。"""
        current = self._continuous_turn_event_identity(event)
        if current is None or not self.continuous_turn_event_is_current(event):
            return
        batch = self._continuous_turn_batch(current[0], current[1])
        if batch is None or not batch.images:
            return
        source = batch.generation_event
        identity = self._continuous_turn_event_identity(source)
        if identity is None or identity[:2] != current[:2] or identity[2] >= current[2]:
            return
        # 先标记旧事件，覆盖旧 Runner 尚未注册的窗口。
        setter = getattr(source, "set_extra", None)
        if callable(setter):
            setter("agent_stop_requested", True)
        self.stop_stale_continuous_turn_event(source)
        getter = getattr(self, "_active_agent_runner", None)
        runner = getter(event) if callable(getter) else None
        runner_event = getattr(
            getattr(getattr(runner, "run_context", None), "context", None),
            "event",
            None,
        )
        stopper = getattr(runner, "request_stop", None)
        if runner_event is source and callable(stopper):
            stopper()

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

    def _continuous_turn_adaptive_wait(
        self,
        previous: ContinuousTurnBatch | None,
        now: float,
        base_wait: float,
        max_wait: float,
        learned_cadence: float = 0.0,
    ) -> float:
        """根据当前和近期发送节奏决定尾部安静窗口。"""
        base_wait = max(0.0, float(base_wait or 0.0))
        max_wait = max(base_wait, float(max_wait or 0.0))
        cadence = max(0.0, float(learned_cadence or 0.0))
        if previous is not None:
            interval = max(0.0, float(now) - float(previous.last_at))
            if cadence > 0:
                cadence = cadence * 0.35 + interval * 0.65
            else:
                cadence = interval
        if cadence <= 0:
            return min(base_wait, max_wait)
        cadence_wait = cadence * self._CONTINUOUS_TURN_CADENCE_MULTIPLIER
        max_tail_wait = min(
            max_wait,
            max(base_wait, max_wait * self._CONTINUOUS_TURN_MAX_TAIL_RATIO),
        )
        return min(max_tail_wait, max(base_wait, cadence_wait))

    def note_continuous_turn_incoming(self, event: Any) -> bool:
        if not self._continuous_turn_eligible(event):
            return False
        scope, participant = self._continuous_turn_identity(event)
        if not scope:
            return False
        style = self._continuous_turn_style()
        now = time.monotonic()
        max_wait = max(
            0.0, float(getattr(style, "continuous_turn_max_wait_seconds", 12.0) or 0.0)
        )
        revisions = getattr(self, "_continuous_turn_revisions", None)
        batches = getattr(self, "_continuous_turn_batches", None)
        cadences = getattr(self, "_continuous_turn_cadence", None)
        if (
            not isinstance(revisions, dict)
            or not isinstance(batches, dict)
            or not isinstance(cadences, dict)
        ):
            self._init_continuous_turn_state()
            revisions = self._continuous_turn_revisions
            batches = self._continuous_turn_batches
            cadences = self._continuous_turn_cadence
        batch_bucket = batches.setdefault(scope, {})
        cadence_bucket = cadences.setdefault(scope, {})
        previous = batch_bucket.get(participant)
        learned_cadence = max(0.0, float(cadence_bucket.get(participant, 0.0) or 0.0))
        active = bool(
            isinstance(previous, ContinuousTurnBatch)
            and previous.phase in {"collecting", "ready", "generating", "waiting"}
            and now - previous.last_at <= self._CONTINUOUS_TURN_ACTIVE_SECONDS
        )
        image_items = self._continuous_turn_image_items(event)
        generating = bool(active and previous.phase == "generating")
        joins_active_generation = generating and not image_items
        restarts_generation = generating and bool(image_items)
        revision_bucket = revisions.setdefault(scope, {})
        if joins_active_generation:
            revision = previous.revision
        else:
            revision = int(revision_bucket.get(participant, 0)) + 1
            revision_bucket[participant] = revision
        messages = list(previous.messages) if active else []
        message_ids = list(previous.message_ids) if active else []
        images = dict(previous.images) if active else {}
        message_id = self._continuous_turn_message_id(event)
        text = str(getattr(event, "message_str", "") or "").strip()
        if message_id not in message_ids:
            messages.append(text or "[图片]")
            message_ids.append(message_id)
            if image_items:
                images[message_id] = ContinuousTurnImages(items=image_items)
        if message_id in images:
            setattr(event, self._CONTINUOUS_TURN_IMAGES_ATTR, images[message_id])
        messages, message_ids = self._continuous_turn_trim_messages(
            messages, message_ids
        )
        first_at = previous.first_at if active else now
        base_wait = max(
            0.0,
            float(getattr(style, "continuous_turn_wait_seconds", 3.5) or 0.0),
        )
        wait_seconds = self._continuous_turn_adaptive_wait(
            previous if active else None,
            now,
            base_wait,
            max_wait,
            learned_cadence=learned_cadence,
        )
        if active and isinstance(previous, ContinuousTurnBatch):
            interval = max(0.0, now - previous.last_at)
            if 0 < interval <= self._CONTINUOUS_TURN_ACTIVE_SECONDS:
                cadence_bucket[participant] = (
                    learned_cadence * 0.35 + interval * 0.65
                    if learned_cadence > 0
                    else interval
                )
        batch = ContinuousTurnBatch(
            revision=revision,
            first_at=first_at,
            last_at=now,
            deadline=(
                previous.deadline if joins_active_generation else first_at + max_wait
            ),
            wait_seconds=(
                previous.wait_seconds if joins_active_generation else wait_seconds
            ),
            phase=(previous.phase if joins_active_generation else "collecting"),
            messages=messages,
            message_ids=message_ids,
            images={key: images[key] for key in message_ids if key in images},
            generation_event=previous.generation_event if active else None,
            video_reference_source=previous.video_reference_source if active else None,
        )
        batch_bucket[participant] = batch
        setattr(event, self._CONTINUOUS_TURN_SCOPE_ATTR, scope)
        setattr(event, self._CONTINUOUS_TURN_PARTICIPANT_ATTR, participant)
        setattr(event, self._CONTINUOUS_TURN_REVISION_ATTR, revision)
        setattr(event, self._CONTINUOUS_TURN_DEADLINE_ATTR, batch.deadline)
        setattr(event, self._CONTINUOUS_TURN_FOLLOW_UP_ATTR, generating)
        setattr(event, self._CONTINUOUS_TURN_RESTART_ATTR, restarts_generation)
        if batch.video_reference_source is not None:
            # 图片会重启图文对话，但已经开始的视频仍使用原请求的图片来源。
            setattr(
                event, self._VIDEO_REFERENCE_SOURCE_ATTR, batch.video_reference_source
            )
        if restarts_generation:
            self._continuous_turn_restart_for_image(event)
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
        llm_setter = getattr(event, "should_call_llm", None)
        if callable(llm_setter):
            llm_setter(True)
        else:
            setattr(event, "call_llm", True)
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()
        # AstrBot 在停止没有结果的事件时会创建空结果；随后清除它，
        # 使 RespondStage 没有可发送或后处理的内容。
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()

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
        self.note_continuous_turn_media_ready(event)
        identity = self._continuous_turn_event_identity(event)
        if identity is None:
            return True
        if self.continuous_turn_event_is_inflight_follow_up(event) and not bool(
            getattr(event, self._CONTINUOUS_TURN_RESTART_ATTR, False)
        ):
            text = str(getattr(event, "message_str", "") or "").strip()
            setattr(
                event,
                self._CONTINUOUS_TURN_MESSAGES_ATTR,
                (text,) if text else (),
            )
            return True
        scope, participant, revision = identity
        batch = self._continuous_turn_batch(scope, participant)
        if batch is None or batch.revision != revision:
            self.stop_stale_continuous_turn_event(event)
            return False
        wait_seconds = max(0.0, float(batch.wait_seconds or 0.0))
        remaining = max(0.0, batch.deadline - time.monotonic())
        delay = min(wait_seconds, remaining)
        if delay > 0:
            await self._continuous_turn_wait(event, delay)
        for images in batch.images.values():
            await images.ready.wait()
        if not self.continuous_turn_event_is_current(event):
            self.stop_stale_continuous_turn_event(event)
            return False
        batch = self._continuous_turn_batch(scope, participant)
        if batch is None or batch.revision != revision:
            self.stop_stale_continuous_turn_event(event)
            return False
        batch.phase = "ready"
        messages = tuple(item for item in batch.messages if item)
        setattr(event, self._CONTINUOUS_TURN_MESSAGES_ATTR, messages)
        setattr(event, self._CONTINUOUS_TURN_DEADLINE_ATTR, batch.deadline)
        if len(messages) > 1 and batch.images:
            # 在框架构建 ProviderRequest 之前合并真实组件，保留平台图片转换流程。
            chain = getattr(getattr(event, "message_obj", None), "message", None)
            if isinstance(chain, list):
                chain[:] = [
                    item
                    for item in chain
                    if "image" not in self._event_component_kind(item)
                ] + [item for images in batch.images.values() for item in images.items]
            setattr(
                event,
                self._PREPARED_VISUAL_MEDIA_ATTR,
                [
                    entry
                    for images in batch.images.values()
                    for entry in images.prepared
                ],
            )
        if len(messages) > 1:
            logger.debug(
                f"{LOG_PREFIX} 连续消息已收束：{len(messages)} 条合并为一个话轮。"
            )
        self._continuous_turn_restart_for_image(event)
        return True

    def continuous_turn_messages(self, event: Any) -> tuple[str, ...]:
        values = getattr(event, self._CONTINUOUS_TURN_MESSAGES_ATTR, ())
        if isinstance(values, (list, tuple)):
            return tuple(str(item).strip() for item in values if str(item).strip())
        return ()

    def continuous_turn_message_count(self, event: Any) -> int:
        return len(self.continuous_turn_messages(event))

    def continuous_turn_event_is_inflight_follow_up(self, event: Any) -> bool:
        return bool(getattr(event, self._CONTINUOUS_TURN_FOLLOW_UP_ATTR, False))

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
            batch.phase = "ready"
        self._continuous_turn_metrics["semantic_wait"] += 1
        return "reply"

    def prepare_continuous_turn_llm_request(self, event: Any, request: Any) -> bool:
        if self.stop_stale_continuous_turn_event(event):
            return False
        identity = self._continuous_turn_event_identity(event)
        if identity is not None:
            batch = self._continuous_turn_batch(identity[0], identity[1])
            if batch is not None and batch.revision == identity[2]:
                batch.phase = "generating"
                batch.generation_event = event
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
        batch.images.clear()
        batch.generation_event = None
        batch.video_reference_source = None
        batch.last_at = time.monotonic()
        self._continuous_turn_metrics["completed"] += 1
        return True


__all__ = ["ContinuousTurnBatch", "ContinuousTurnMixin"]
