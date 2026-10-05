from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..markers import LOG_PREFIX
from .judge import VoiceSwitchJudgeMixin
from .limit import VoiceSwitchGateMixin
from .preface import SilentToolPrefaceMixin
from .trace import VoiceSwitchRecordMixin


class VoiceSwitchMixin(
    VoiceSwitchGateMixin,
    SilentToolPrefaceMixin,
    VoiceSwitchJudgeMixin,
    VoiceSwitchRecordMixin,
):
    async def apply_voice_switch_before_send(self, event: Any) -> bool:
        scope = self._voice_switch_scope_key(event)
        if not scope:
            return False
        self._prune_voice_switch_rounds()
        item = self._voice_switch_round_store().get(scope)
        if (
            not isinstance(item, dict)
            or item.get("used_voice")
            or item.get("pre_send_checked")
        ):
            return False
        reply_text = self._voice_switch_reply_text_from_event(event)
        if not reply_text:
            return False
        voice_config = getattr(self.config, "voice_generation", None)
        if (
            not voice_config
            or not voice_config.enabled
            or not getattr(voice_config, "smart_switch_enabled", True)
        ):
            return False
        if not self._voice_allowed_for_scope(event):
            return False
        item["pre_send_checked"] = True
        if self._is_active_agent_intermediate_result(event):
            item["text_reason"] = (
                "工具还在执行中，这句只是调用前的临时说明，保留文字更合适。"
            )
            return False
        payload = self._judge_voice_switch_channel(event, reply_text)
        channel = str(payload.get("channel") or "text").strip().lower()
        reason = str(payload.get("reason") or "").strip()
        emotion = str(payload.get("emotion") or "").strip()
        emotion_category = str(payload.get("emotion_category") or "").strip()
        voice_style = str(payload.get("voice_style") or "").strip().lower()
        try:
            revision_getter = getattr(self, "_semantic_segment_revision", None)
            revision = revision_getter(scope) if callable(revision_getter) else None
            confidence = float(payload.get("confidence", 1.0) or 1.0)
        except (TypeError, ValueError):
            confidence = 1.0
        if channel != "voice":
            item["text_reason"] = reason or "我想把这轮话打出来，留在屏幕上更清楚。"
            return False
        allowed, gate_reason = self._voice_switch_auto_gate(
            event, reply_text, confidence
        )
        if not allowed:
            item["text_reason"] = gate_reason
            return False
        try:
            voice_kwargs = {
                "emotion": emotion,
                "emotion_category": emotion_category,
            }
            if voice_style and voice_style != "neutral":
                voice_kwargs["voice_style"] = voice_style
            generated = await self.media.voice.synthesize(reply_text, **voice_kwargs)
            current = getattr(self, "continuous_turn_event_is_current", None)
            recalled = getattr(self, "event_was_recalled", None)
            if (
                getattr(event, "is_stopped", lambda: False)()
                or (callable(current) and not current(event))
                or (callable(recalled) and recalled(event))
                or (callable(revision_getter) and revision_getter(scope) != revision)
            ):
                getattr(event, "set_result", lambda value: None)(None)
                return False
            self.mark_structured_pending_bot_text(event, reply_text, media="语音")
            self._replace_result_with_voice(event, str(generated.path))
            setattr(event, self._SEMANTIC_SEGMENT_PENDING_ATTR, [])
            item["used_voice"] = True
            event._daily_life_pending_voice_receipt = {
                "scope": scope,
                "text": reply_text,
                "reason": reason or "我觉得这句话更适合直接说出来。",
                "emotion": emotion,
                "emotion_category": emotion_category,
                "confidence": confidence,
            }
            return True
        except Exception as exc:
            item["text_reason"] = (
                f"{reason or '我原本想直接说出来'}；但语音生成失败，改用文字发送：{exc}"
            )
            logger.debug(f"{LOG_PREFIX} 发送前语音智能切换失败，保留文字发送：{exc}")
            return False

    async def note_voice_switch_message_sent(self, event: Any) -> bool:
        receipt = getattr(event, "_daily_life_pending_voice_receipt", None)
        if not isinstance(receipt, dict):
            return False
        delattr(event, "_daily_life_pending_voice_receipt")
        await self._append_turn_history(
            receipt["scope"],
            event,
            self._event_user_history_text(event),
            receipt["text"],
        )
        await self._note_voice_expression_decision(
            event=event,
            channel="语音",
            source="普通聊天",
            result="已发送",
            **{key: value for key, value in receipt.items() if key != "scope"},
        )
        self._mark_voice_switch_channel(event, "语音")
        return True


__all__ = ["VoiceSwitchMixin"]
