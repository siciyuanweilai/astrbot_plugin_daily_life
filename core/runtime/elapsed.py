from __future__ import annotations

import datetime
import math
import time
from collections.abc import Callable
from typing import Any

from astrbot.api import logger

from ..clock import TIMEZONE
from ..sources.events import is_chat_message_event

RECEIVED_AT = "_daily_life_received_at"
SENT_AT = "_daily_life_sent_at"
TURN_RECEIVED_AT = "_daily_life_turn_received_at"


def event_time(event: Any, attribute: str) -> float:
    value = getattr(event, attribute, None)
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return float(value)
    return 0.0


def local_message_time(event: Any, attribute: str) -> datetime.datetime | None:
    stamp = event_time(event, attribute)
    return (
        datetime.datetime.fromtimestamp(stamp, TIMEZONE).replace(tzinfo=None)
        if stamp else None
    )


def format_elapsed(seconds: float) -> str:
    remaining = max(0, int(seconds))
    parts = []
    for unit, size in (("天", 86400), ("小时", 3600), ("分钟", 60), ("秒", 1)):
        count, remaining = divmod(remaining, size)
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts) or "不到1秒"


class ConversationTimingService:
    """使用收发回执感知对话间隔，不依赖记忆提炼或模型推测时间。"""

    def __init__(
        self,
        *,
        archive: Callable[[], Any],
        is_self_message: Callable[[Any], bool],
        session_id: Callable[[Any], str],
        is_group: Callable[[Any], bool],
    ) -> None:
        self._archive = archive
        self._is_self_message = is_self_message
        self._session_id = session_id
        self._is_group = is_group
        self._receipts: dict[str, list[float]] = {}
        self._arrivals: dict[str, list[float]] = {}

    def note_conversation_incoming(self, event: Any) -> None:
        if (
            not is_chat_message_event(event)
            or getattr(event, "is_proactive_synthetic", False)
            or self._is_self_message(event)
            or event_time(event, RECEIVED_AT)
        ):
            return
        now = time.time()
        created = event_time(event, "created_at")
        # AstrBot 在事件构造时记录 created_at，优先使用它排除前置钩子的等待。
        received = created if 0 < created <= now else now
        setattr(event, RECEIVED_AT, received)
        scope = self._session_id(event)
        if scope:
            self._arrivals[scope] = sorted(
                set(self._arrivals.pop(scope, []) + [received])
            )[-32:]
            while len(self._arrivals) > 512:
                self._arrivals.pop(next(iter(self._arrivals)))

    async def record_conversation_arrival(self, event: Any) -> None:
        if (
            not is_chat_message_event(event)
            or getattr(event, "is_proactive_synthetic", False)
            or self._is_self_message(event)
        ):
            return
        received = event_time(event, RECEIVED_AT)
        scope = self._session_id(event)
        writer = getattr(self._archive(), "save_conversation_arrival", None)
        if not received or not scope or not callable(writer):
            return
        try:
            await writer(scope, received)
        except Exception:
            logger.warning(
                "[对话时间] 接收时间持久化失败，本次继续使用内存时间戳。"
            )

    def clear_conversation_timing(self) -> None:
        self._receipts.clear()
        self._arrivals.clear()

    def evict_scopes(self, aliases: set[str]) -> None:
        for alias in aliases:
            self._receipts.pop(alias, None)
            self._arrivals.pop(alias, None)

    def note_conversation_sent(
        self, scope: str, sent_at: float, source_event: Any = None
    ) -> None:
        if not scope or not math.isfinite(sent_at) or sent_at <= 0:
            return
        store = self._receipts
        # 保留少量回执，使生成期间的新发送不会覆盖本轮到达前的时间锚点。
        store[scope] = sorted(set(store.pop(scope, []) + [sent_at]))[-32:]
        while len(store) > 512:
            store.pop(next(iter(store)))
        if source_event is not None:
            setattr(source_event, SENT_AT, max(sent_at, event_time(source_event, SENT_AT)))

    async def record_conversation_delivery(
        self, scope: str, sent_at: float, source_event: Any = None
    ) -> None:
        self.note_conversation_sent(scope, sent_at, source_event)
        writer = getattr(self._archive(), "save_conversation_receipt", None)
        if not scope or not callable(writer):
            return
        try:
            await writer(scope, sent_at)
        except Exception:
            # 收发事实记录失败不能把已经成功发送的消息变成投递失败。
            logger.warning("[对话时间] 发送时间持久化失败，本次继续使用内存回执。")

    async def build_conversation_timing_context(self, event: Any) -> str:
        if event is None or getattr(event, "is_proactive_synthetic", False):
            return ""
        received = event_time(event, RECEIVED_AT)
        if not received:
            return ""
        cached = getattr(event, "_daily_life_conversation_timing_context", None)
        if isinstance(cached, str):
            return cached
        scope = self._session_id(event)
        if not scope:
            return ""
        started = event_time(event, TURN_RECEIVED_AT) or received
        started = min(started, received)
        store = self._receipts
        previous = max((t for t in store.get(scope, []) if t <= started), default=0.0)
        reader = getattr(self._archive(), "get_conversation_receipt_before", None)
        if not previous and callable(reader):
            try:
                previous = await reader(scope, started)
            except Exception:
                logger.warning("[对话时间] 暂时无法读取历史发送时间，不推测对话间隔。")
        previous_incoming = max(
            (t for t in self._arrivals.get(scope, []) if t < started), default=0.0
        )
        reader = getattr(self._archive(), "get_conversation_arrival_before", None)
        if not previous_incoming and callable(reader):
            try:
                previous_incoming = await reader(scope, started)
            except Exception:
                logger.warning(
                    "[对话时间] 暂时无法读取历史接收时间，不推测对话间隔。"
                )

        def stamp(value: float) -> str:
            return datetime.datetime.fromtimestamp(value, TIMEZONE).isoformat(timespec="seconds")

        lines = ["\n\n[ConversationTiming]", f"本条消息到达：{stamp(received)}"]
        if started != received:
            lines.append(f"本轮连续消息开始到达：{stamp(started)}")
        if previous and 0 < previous <= started:
            lines.extend((
                f"本轮到达前，本会话上次成功发送：{stamp(previous)}",
                f"到本轮开始已过：{format_elapsed(started - previous)}（{int(started - previous)}秒）",
            ))
        else:
            lines.append("上次成功发送时间：无可核实记录，间隔未知")
        group = self._is_group(event)
        if 0 < previous_incoming < started:
            subject = "群内" if group else "对方"
            lines.extend(
                (
                    f"本轮之前，{subject}上次发来消息：{stamp(previous_incoming)}",
                    f"{subject}两次发来消息相隔：{format_elapsed(started - previous_incoming)}（{int(started - previous_incoming)}秒）",
                )
            )
            if previous > previous_incoming:
                lines.append("期间我有发送消息；这不代表对方在此期间回复过。")
        else:
            lines.append("对方此前发来消息的时间：无可核实记录，间隔未知")
        if group:
            lines.append(
                "范围：群聊消息记录，不能把群内收发间隔归因于当前发言者；不据此判断这个人迟回。"
            )
        context = "\n".join(lines)
        setattr(event, "_daily_life_conversation_timing_context", context)
        return context
