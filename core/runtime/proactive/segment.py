import asyncio
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain

from ..markers import LOG_PREFIX


class ProactiveSegmentMixin:
    async def _send_segmented_proactive_message(
        self,
        target_scope: str,
        reply_text: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        limit = self._chat_style_limit_for_scope(target_scope)
        splitter = getattr(self, "_split_chat_style_natural_segments", None)
        segments = (
            splitter(reply_text, limit, max_segments_cap=2)
            if callable(splitter)
            else [str(reply_text or "").strip()]
        )
        segments = [str(segment or "").strip() for segment in segments if str(segment or "").strip()]
        if not segments:
            return False
        reply_to_id = str(source_message_id or "").strip()
        if not reply_to_id and source_event is not None:
            try:
                reply_to_id = str(self._event_message_id(source_event) or "").strip()
            except Exception:
                reply_to_id = ""
        logger.debug(f"{LOG_PREFIX} 闲时回复按插件自然短句发送：{len(segments)} 段")
        for index, segment in enumerate(segments):
            if index > 0:
                delay_getter = getattr(self, "_chat_style_natural_segment_delay_seconds", None)
                if callable(delay_getter):
                    delay_seconds = delay_getter(segments[index - 1], segment)
                    if delay_seconds > 0:
                        await asyncio.sleep(delay_seconds)
            chain = MessageChain().message(segment)
            decorator = getattr(self, "decorate_group_addressing_chain", None)
            if callable(decorator):
                decorator(
                    chain,
                    target_scope=target_scope,
                    source_event=source_event,
                    source_message_id=source_message_id,
                    segment_index=index,
                    source="proactive",
                )
            if not await self.send_message_if_not_recalled(
                target_scope,
                chain,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                return False
            self.note_structured_bot_message(
                target_scope,
                segment,
                source_event=source_event if index == 0 else None,
                reply_to_id=reply_to_id if index == 0 else "",
            )
        return True

    def _chat_style_limit_for_scope(self, target_scope: str) -> int:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        if not style:
            return 0
        try:
            casual_limit = int(getattr(style, "casual_max_chars", 50) or 50)
        except (TypeError, ValueError):
            casual_limit = 50
        try:
            proactive_limit = int(getattr(style, "proactive_max_chars", 15) or 15)
        except (TypeError, ValueError):
            proactive_limit = 15
        scope = str(target_scope or "")
        attr = "group_casual_max_chars" if ":GroupMessage:" in scope else "private_casual_max_chars"
        default = 30 if attr == "group_casual_max_chars" else 15
        try:
            channel_limit = int(getattr(style, attr, default) or default)
        except (TypeError, ValueError):
            channel_limit = default
        limits = [value for value in (casual_limit, channel_limit, proactive_limit) if value > 0]
        return min(limits) if limits else 0

    def _proactive_send_delay_seconds(self, payload: dict[str, Any] | None) -> float:
        if not isinstance(payload, dict):
            return 0.0
        timing = payload.get("send_timing") if isinstance(payload.get("send_timing"), dict) else {}
        value = timing.get("delay_seconds", payload.get("delay_seconds"))
        try:
            delay = float(value)
        except (TypeError, ValueError):
            delay = 0.0
        if delay <= 0:
            reply_text = str(payload.get("reply_text") or "").strip()
            delay_getter = getattr(self, "_chat_style_initial_typing_delay_seconds", None)
            if reply_text and callable(delay_getter):
                return max(0.0, min(float(delay_getter(reply_text)), 3.5))
            return 0.0
        return max(0.0, min(delay, 12.0))

    async def _apply_proactive_send_timing(self, payload: dict[str, Any] | None) -> None:
        delay = self._proactive_send_delay_seconds(payload)
        if delay <= 0:
            return
        reason = "按文本长度模拟自然打字"
        if isinstance(payload, dict) and isinstance(payload.get("send_timing"), dict):
            reason = str(payload["send_timing"].get("reason") or "").strip() or reason
        logger.debug(f"{LOG_PREFIX} 闲时回复发送节奏等待 {delay:.1f} 秒" + (f"：{reason}" if reason else ""))
        await asyncio.sleep(delay)
