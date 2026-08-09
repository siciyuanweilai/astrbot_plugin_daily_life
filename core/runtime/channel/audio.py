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
        voice_style: str = "",
        user_requested: bool = False,
        decision_reason: str = "",
    ) -> str | None:
        text = str(text or "").strip()
        decision_reason = str(decision_reason or "").strip()

        def mark_outcome(outcome: str) -> None:
            marker = getattr(self, "mark_tool_outcome", None)
            if callable(marker):
                marker(event, "life_voice_generate", outcome)

        if not text:
            mark_outcome("fallback")
            return "没有收到语音文本。"
        if not user_requested:
            reason = "用户没有明确要求语音，普通聊天交由发送前自动判断。"
            self._mark_voice_switch_channel(event, "文字")
            await self._note_voice_expression_decision(
                event=event,
                channel="文字",
                source="普通聊天",
                reason=reason,
                result="改用文字",
                text=text,
                emotion=emotion,
                emotion_category=emotion_category,
                user_requested=False,
                confidence=1.0,
            )
            mark_outcome("fallback")
            return "用户没有明确要求语音，请直接用文字回复。"
        scope = self._event_session_id(event)
        if not scope:
            mark_outcome("fallback")
            return "当前会话不可发送语音。"
        if not self._voice_allowed_for_scope(event):
            await self._note_voice_expression_decision(
                event=event,
                channel="文字",
                source="用户明确要求",
                reason="语音生成未启用，保持文字聊天。",
                result="被拦截",
                text=text,
                emotion=emotion,
                emotion_category=emotion_category,
                user_requested=True,
                confidence=1.0,
            )
            mark_outcome("fallback")
            return "语音生成未启用。"
        if self._chat_style_enabled():
            style_context = self._chat_style_context(event)
            setattr(event, "_daily_life_chat_style_context", style_context)
            self.log_chat_style_trace(event, text, style_context, changed=False)
        try:
            voice_kwargs = {
                "emotion": emotion,
                "emotion_category": emotion_category,
            }
            if voice_style:
                voice_kwargs["voice_style"] = voice_style
            generated = await self.media.voice.synthesize(text, **voice_kwargs)
            if not await self.send_message_if_not_recalled(
                scope,
                self._record_message_chain(generated.path),
                source_event=event,
            ):
                mark_outcome("fallback")
                return "原消息已撤回，已取消语音发送。"
            self.mark_voice_switch_used(event)
            mark_outcome("sent")
            self.note_structured_bot_message(
                scope, text, source_event=event, media="语音"
            )
            await self._append_turn_history(
                scope, event, self._event_user_history_text(event), text
            )
            await self._note_voice_expression_decision(
                event=event,
                channel="语音",
                source="用户明确要求",
                reason=decision_reason or "用户明确要求这轮使用语音。",
                result="已发送",
                text=text,
                emotion=emotion,
                emotion_category=emotion_category,
                user_requested=True,
                confidence=1.0,
            )
            self._mark_voice_switch_channel(event, "语音")
            return None
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 语音生成或发送失败：{error}")
            self._mark_voice_switch_channel(event, "文字")
            mark_outcome("fallback")
            await self._note_voice_expression_decision(
                event=event,
                channel="文字",
                source="用户明确要求",
                reason=f"语音生成或发送失败，改用文字：{error}",
                result="改用文字",
                text=text,
                emotion=emotion,
                emotion_category=emotion_category,
                user_requested=True,
                confidence=1.0,
            )
            return f"语音生成失败：{error}"
