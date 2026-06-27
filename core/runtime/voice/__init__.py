from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..markers import LOG_PREFIX
from . import preface as _preface
from .limit import VoiceSwitchGateMixin
from .judge import VoiceSwitchJudgeMixin
from .preface import VoiceSwitchPrefaceMixin
from .trace import VoiceSwitchRecordMixin

_astrbot_follow_up = _preface._astrbot_follow_up


class VoiceSwitchMixin(
    VoiceSwitchGateMixin,
    VoiceSwitchPrefaceMixin,
    VoiceSwitchJudgeMixin,
    VoiceSwitchRecordMixin,
):
    async def apply_voice_switch_before_send(self, event: Any) -> bool:
        scope = self._voice_switch_scope_key(event)
        if not scope:
            return False
        self._prune_voice_switch_rounds()
        item = self._voice_switch_round_store().get(scope)
        if not isinstance(item, dict) or item.get("used_voice") or item.get("pre_send_checked"):
            return False
        reply_text = self._voice_switch_reply_text_from_event(event)
        if not reply_text:
            return False
        voice_config = getattr(self.config, "voice_generation", None)
        if not voice_config or not voice_config.enabled or not getattr(voice_config, "smart_switch_enabled", True):
            return False
        if not self._voice_allowed_for_scope(event):
            return False
        item["pre_send_checked"] = True
        payload = self._judge_voice_switch_channel(event, reply_text)
        channel = str(payload.get("channel") or "text").strip().lower()
        reason = str(payload.get("reason") or "").strip()
        emotion = str(payload.get("emotion") or "").strip()
        emotion_category = str(payload.get("emotion_category") or "").strip()
        try:
            confidence = float(payload.get("confidence", 1.0) or 1.0)
        except (TypeError, ValueError):
            confidence = 1.0
        if channel != "voice":
            item["text_reason"] = reason or "我想把这轮话打出来，留在屏幕上更清楚。"
            return False
        allowed, gate_reason = self._voice_switch_auto_gate(event, reply_text, confidence)
        if not allowed:
            item["text_reason"] = gate_reason
            return False
        try:
            generated = await self.media.voice.synthesize(
                reply_text,
                emotion=emotion,
                emotion_category=emotion_category,
            )
            self.mark_structured_pending_bot_text(event, reply_text, media="语音")
            self._replace_result_with_voice(event, str(generated.path))
            item["used_voice"] = True
            await self._append_turn_history(scope, event, self._event_user_history_text(event), reply_text)
            await self._note_voice_expression_decision(
                event=event,
                channel="语音",
                source="普通聊天",
                reason=reason or "我觉得这句话更适合直接说出来。",
                result="已发送",
                text=reply_text,
                emotion=emotion,
                emotion_category=emotion_category,
                confidence=confidence,
            )
            self._mark_voice_switch_channel(event, "语音")
            return True
        except Exception as exc:
            item["text_reason"] = f"{reason or '我原本想直接说出来'}；但语音生成失败，改用文字发送：{exc}"
            logger.warning(f"{LOG_PREFIX} 发送前语音智能切换失败，保留文字发送：{exc}")
            return False


__all__ = ["VoiceSwitchMixin", "_astrbot_follow_up"]
