from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..markers import LOG_PREFIX


class RuntimeVoiceMediaMixin:
    async def life_voice_generate(
        self,
        event: Any,
        text: str,
        emotion: str = "",
        emotion_category: str = "",
        user_requested: bool = False,
        decision_reason: str = "",
    ) -> str | None:
        text = str(text or "").strip()
        decision_reason = str(decision_reason or "").strip()
        if not text:
            return "没有收到语音文本。"
        scope = self._event_session_id(event)
        if not scope:
            return "当前会话不可发送语音。"
        self.mark_voice_switch_used(event)
        if not self._voice_allowed_for_scope(event):
            await self._note_voice_expression_decision(
                event=event,
                channel="文字",
                source="用户明确要求" if user_requested else "普通聊天",
                reason="语音生成未启用，保持文字聊天。",
                result="被拦截",
                text=text,
                emotion=emotion,
                emotion_category=emotion_category,
                user_requested=user_requested,
                confidence=1.0,
            )
            return "语音生成未启用。"
        voice_config = getattr(self.config, "voice_generation", None)
        if not getattr(voice_config, "smart_switch_enabled", True) and not user_requested:
            await self._note_voice_expression_decision(
                event=event,
                channel="文字",
                source="普通聊天",
                reason="智能切换已关闭，模型自主语音被拦截，保持文字聊天。",
                result="被拦截",
                text=text,
                emotion=emotion,
                emotion_category=emotion_category,
                user_requested=False,
                confidence=1.0,
            )
            return "当前已关闭自动语音，请直接用文字回复；如果用户明确要求语音，请带 user_requested=true 重新调用。"
        if not user_requested:
            allowed, gate_reason = self._voice_switch_auto_gate(event, text, 1.0)
            if not allowed:
                self._mark_voice_switch_channel(event, "文字")
                await self._note_voice_expression_decision(
                    event=event,
                    channel="文字",
                    source="普通聊天",
                    reason=gate_reason,
                    result="改用文字",
                    text=text,
                    emotion=emotion,
                    emotion_category=emotion_category,
                    user_requested=False,
                    confidence=1.0,
                )
                return f"{gate_reason} 请直接用文字回复。"
        style_decision = getattr(event, "_daily_life_chat_style_decision", None)
        if not isinstance(style_decision, dict):
            style_decision = self._classify_chat_message(event, getattr(event, "message_str", ""))
            setattr(event, "_daily_life_chat_style_decision", style_decision)
        self.log_chat_style_trace(event, text, style_decision, changed=False)
        try:
            generated = await self.media.voice.synthesize(text, emotion=emotion, emotion_category=emotion_category)
            if not await self.send_message_if_not_recalled(
                scope,
                self._record_message_chain(generated.path),
                source_event=event,
            ):
                return "原消息已撤回，已取消语音发送。"
            self.note_structured_bot_message(scope, text, source_event=event, media="语音")
            await self._append_turn_history(scope, event, self._event_user_history_text(event), text)
            await self._note_voice_expression_decision(
                event=event,
                channel="语音",
                source="用户明确要求" if user_requested else "普通聊天",
                reason=decision_reason or "我这轮更适合直接说出来，但没有补充更具体的内心理由。",
                result="已发送",
                text=text,
                emotion=emotion,
                emotion_category=emotion_category,
                user_requested=user_requested,
                confidence=1.0,
            )
            self._mark_voice_switch_channel(event, "语音")
            return None
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 语音生成或发送失败：{error}")
            self._mark_voice_switch_channel(event, "文字")
            await self._note_voice_expression_decision(
                event=event,
                channel="文字",
                source="用户明确要求" if user_requested else "普通聊天",
                reason=f"语音生成或发送失败，改用文字：{error}",
                result="改用文字",
                text=text,
                emotion=emotion,
                emotion_category=emotion_category,
                user_requested=user_requested,
                confidence=1.0,
            )
            return f"语音生成失败：{error}"
