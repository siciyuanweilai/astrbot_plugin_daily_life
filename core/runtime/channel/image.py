from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

from ..markers import LOG_PREFIX


class RuntimeImageMediaMixin:
    async def life_image_generate(self, event: Any, prompt: str) -> str:
        prompt = str(prompt or "").strip()
        if not prompt:
            return "没有收到图片提示词。"
        scope = self._event_session_id(event)
        if not scope:
            return "当前会话不可发送图片。"
        started_at = time.monotonic()
        try:
            directed_prompt = await self._direct_life_image_prompt(event, prompt)
            generated = await self.media.image.generate_image(directed_prompt)
            if not await self.send_message_if_not_recalled(
                scope,
                self.image_message_chain(generated.path),
                source_event=event,
            ):
                return "原消息已撤回，已取消图片发送。"
            self.note_structured_bot_message(scope, "[图片已发送]", source_event=event, media="图片")
            self._remember_life_image_for_scope(scope, generated.path)
            self.note_life_media_sent(event, "图片")
            return f"图片已发送。{await self._media_result_summary(generated.path, started_at)}。"
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 图片生成或发送失败：{error}")
            return f"图片生成失败：{error}"

    async def edit_life_image(self, event: Any, prompt: str, reference_image: str = "") -> str:
        prompt = str(prompt or "").strip()
        if not prompt:
            return "没有收到图片编辑提示词。"
        scope = self._event_session_id(event)
        if not scope:
            return "当前会话不可发送图片。"
        reference = await self._resolve_life_image_reference_async(event, reference_image)
        if not reference:
            return "没有找到参考图片。"
        started_at = time.monotonic()
        try:
            directed_prompt = await self._direct_life_image_prompt(event, prompt, reference=True)
            generated = await self.media.image.edit_image(directed_prompt, reference)
            if not await self.send_message_if_not_recalled(
                scope,
                self.image_message_chain(generated.path),
                source_event=event,
            ):
                return "原消息已撤回，已取消图片发送。"
            self.note_structured_bot_message(scope, "[图片已发送]", source_event=event, media="图片")
            self._remember_life_image_for_scope(scope, generated.path)
            self.note_life_media_sent(event, "图片")
            return f"图片已根据参考图生成。{await self._media_result_summary(generated.path, started_at)}。"
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 参考图生成或发送失败：{error}")
            return f"图片生成失败：{error}"
