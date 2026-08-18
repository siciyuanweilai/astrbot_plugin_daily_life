import asyncio
from typing import Any

from astrbot.api import logger

from ..delivery import BackgroundTextMode
from ..markers import LOG_PREFIX


class ProactiveSegmentMixin:
    async def _send_segmented_proactive_message(
        self,
        target_scope: str,
        reply_text: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
        raise_delivery_errors: bool = False,
    ) -> bool:
        return await self.send_background_text(
            target_scope,
            reply_text,
            mode=BackgroundTextMode.EXPRESSIVE,
            source_event=source_event,
            source_message_id=source_message_id,
            source="proactive",
            raise_delivery_errors=raise_delivery_errors,
            user_message=str(getattr(source_event, "message_str", "") or "").strip(),
            length_hint=self._chat_style_limit_for_scope(target_scope),
        )

    def _chat_style_limit_for_scope(self, target_scope: str) -> int:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        checker = getattr(self, "_chat_style_enabled", None)
        if not style or (callable(checker) and not checker()):
            return 0
        if not callable(checker) and not bool(getattr(style, "enabled", False)):
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
        attr = (
            "group_casual_max_chars"
            if ":GroupMessage:" in scope
            else "private_casual_max_chars"
        )
        default = 30 if attr == "group_casual_max_chars" else 15
        try:
            channel_limit = int(getattr(style, attr, default) or default)
        except (TypeError, ValueError):
            channel_limit = default
        limits = [
            value
            for value in (casual_limit, channel_limit, proactive_limit)
            if value > 0
        ]
        return min(limits) if limits else 0

    def _proactive_send_delay_seconds(self, payload: dict[str, Any] | None) -> float:
        if not isinstance(payload, dict):
            return 0.0
        timing = (
            payload.get("send_timing")
            if isinstance(payload.get("send_timing"), dict)
            else {}
        )
        value = timing.get("delay_seconds", payload.get("delay_seconds"))
        try:
            delay = float(value)
        except (TypeError, ValueError):
            delay = 0.0
        if delay <= 0:
            reply_text = str(payload.get("reply_text") or "").strip()
            delay_getter = getattr(
                self, "_chat_style_initial_typing_delay_seconds", None
            )
            if reply_text and callable(delay_getter):
                return max(0.0, min(float(delay_getter(reply_text)), 3.5))
            return 0.0
        return max(0.0, min(delay, 12.0))

    async def _apply_proactive_send_timing(
        self, payload: dict[str, Any] | None
    ) -> None:
        delay = self._proactive_send_delay_seconds(payload)
        if delay <= 0:
            return
        reason = "按文本长度模拟自然打字"
        if isinstance(payload, dict) and isinstance(payload.get("send_timing"), dict):
            reason = str(payload["send_timing"].get("reason") or "").strip() or reason
        logger.debug(
            f"{LOG_PREFIX} 闲时回复发送节奏等待 {delay:.1f} 秒"
            + (f"：{reason}" if reason else "")
        )
        await asyncio.sleep(delay)
