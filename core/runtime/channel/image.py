from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from astrbot.api import logger
from ...config.options import IMAGE_ASPECT_RATIOS
from ..markers import LOG_PREFIX


class RuntimeImageMediaMixin:
    @staticmethod
    def _image_prompt_aspect_ratio(text: str) -> str:
        compact = ''.join(str(text or '').replace('：', ':').split())
        if not compact:
            return ''
        for index, char in enumerate(compact):
            if char != ':':
                continue
            left = index - 1
            while left >= 0 and compact[left].isdigit():
                left -= 1
            right = index + 1
            while right < len(compact) and compact[right].isdigit():
                right += 1
            if left == index - 1 or right == index + 1:
                continue
            try:
                ratio = f'{int(compact[left + 1:index])}:{int(compact[index + 1:right])}'
            except ValueError:
                continue
            if ratio in IMAGE_ASPECT_RATIOS:
                return ratio
        return ''

    @staticmethod
    def _image_generation_error_needs_rewrite(exc: Exception) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            text = str(current).lower()
            if 'content_policy_violation' in text or 'policy_violation' in text:
                return True
            current = getattr(current, '__cause__', None) or getattr(current, '__context__', None)
        return False

    @staticmethod
    def _image_tool_failure_text(action: str, error: str) -> str:
        detail = str(error or '').strip()
        if not detail:
            return f'{action}失败，已记录失败原因。'
        if len(detail) > 500:
            detail = detail[:500].rstrip() + '...'
        return f'{action}失败：{detail}'

    def _image_policy_rewrite_reason(self, exc: Exception) -> str:
        reason = self._image_policy_rejection_detail(exc)
        if len(reason) > 1000:
            return reason[:1000].rstrip() + '...'
        return reason or type(exc).__name__

    @staticmethod
    def _image_policy_rejection_detail(exc: Exception) -> str:
        current: BaseException | None = exc
        seen: set[int] = set()
        fallback = ''
        http_detail = ''
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            detail = ' '.join(str(current or '').strip().split())
            if detail:
                if not fallback:
                    fallback = detail
                marker = detail.find('HTTP ')
                if marker >= 0:
                    http_detail = detail[marker:]
            current = getattr(current, '__cause__', None) or getattr(current, '__context__', None)
        return http_detail or fallback

    async def _image_operation_with_policy_retry(
        self,
        event: Any,
        prompt: str,
        operation: Callable[[str], Awaitable[Any]],
        *,
        reference: bool = False,
    ) -> Any:
        prompt = str(prompt or '').strip()
        try:
            return await operation(prompt)
        except Exception as exc:
            if not self._image_generation_error_needs_rewrite(exc):
                raise
            rewrite = getattr(self, '_rewrite_life_image_prompt_for_policy_retry', None)
            if not callable(rewrite):
                raise
            reason = self._image_policy_rewrite_reason(exc)
            logger.info(f'{LOG_PREFIX} 图片触发安全拒绝：{reason}')
            try:
                rewritten_prompt = str(await rewrite(event, prompt, reference=reference) or '').strip()
            except Exception as rewrite_exc:
                raise RuntimeError('图片触发安全拒绝，轻量润色失败。') from exc
            if not rewritten_prompt or rewritten_prompt == prompt:
                raise RuntimeError('图片触发安全拒绝，轻量润色没有返回可用的新提示词。') from exc
            logger.info(f'{LOG_PREFIX} 图片轻量润色后重试：{rewritten_prompt}')
            try:
                return await operation(rewritten_prompt)
            except Exception as retry_exc:
                raise RuntimeError(f'图片轻量润色后重试仍失败：{self._media_error_summary(retry_exc)}') from exc

    async def _generate_life_image_with_policy_retry(self, event: Any, prompt: str, aspect_ratio: str = '') -> Any:
        async def generate(safe_prompt: str) -> Any:
            if aspect_ratio:
                return await self.media.image.generate_image(safe_prompt, aspect_ratio=aspect_ratio)
            return await self.media.image.generate_image(safe_prompt)

        return await self._image_operation_with_policy_retry(
            event,
            prompt,
            generate,
        )

    async def _edit_life_image_with_policy_retry(
        self,
        event: Any,
        prompt: str,
        reference_image: str,
        aspect_ratio: str = '',
        *,
        preserve_reference_ratio: bool = True,
    ) -> Any:
        async def edit(safe_prompt: str) -> Any:
            if aspect_ratio:
                return await self.media.image.edit_image(
                    safe_prompt,
                    reference_image,
                    aspect_ratio=aspect_ratio,
                    preserve_reference_ratio=preserve_reference_ratio,
                )
            return await self.media.image.edit_image(
                safe_prompt,
                reference_image,
                preserve_reference_ratio=preserve_reference_ratio,
            )

        return await self._image_operation_with_policy_retry(
            event,
            prompt,
            edit,
            reference=True,
        )

    async def _directed_life_image_result(
        self,
        event: Any,
        prompt: str,
        *,
        direct_prompt: bool = False,
        reference: bool = False,
    ) -> tuple[str, bool, bool]:
        if direct_prompt:
            direct_text = str(prompt or '').strip()
            try:
                result = await self._direct_life_image_payload(
                    event,
                    direct_text,
                    reference=reference,
                    judge_only=True,
                )
                return (
                    direct_text,
                    getattr(result, 'contains_character', False) is True,
                    getattr(result, 'needs_character_reference', False) is True,
                )
            except Exception:
                return direct_text, False, False
        result = await self._direct_life_image_payload(event, prompt, reference=reference)
        directed_prompt = str(getattr(result, 'prompt', '') or '').strip()
        contains_character = getattr(result, 'contains_character', False) is True
        needs_character_reference = getattr(result, 'needs_character_reference', False) is True
        return directed_prompt, contains_character, needs_character_reference

    def note_media_source_event(self, event: Any) -> None:
        scope = self._event_session_id(event)
        if not scope or self._proactive_is_self_message(event):
            return
        reader = getattr(self, '_event_user_history_text', None)
        text = str(reader(event) if callable(reader) else getattr(event, 'message_str', '') or '').strip()
        if not text:
            return
        store = getattr(self, '_life_media_source_events', None)
        if not isinstance(store, dict):
            store = {}
            self._life_media_source_events = store
        store[scope] = {
            'text': text,
            'message_id': self._event_message_id(event),
            'timestamp': time.monotonic(),
        }

    @staticmethod
    def _image_prompt_detail_score(text: str) -> int:
        raw = str(text or '')
        compact = ''.join(raw.split())
        if not compact:
            return 0
        separators = sum(1 for char in raw if char in '，,、；;。.!！?？：:\n\r（）()[]【】')
        line_count = sum(1 for line in raw.splitlines() if line.strip())
        return len(compact) + separators * 8 + max(line_count - 1, 0) * 12

    @staticmethod
    def _normalize_image_subject_route(subject_route: str) -> str:
        route = str(subject_route or '').strip().casefold()
        return route if route in {'current_character', 'scene', 'object', 'free'} else 'free'

    def _event_current_image_request_text(self, event: Any) -> str:
        reader = getattr(self, '_event_user_history_text', None)
        return str(reader(event) if callable(reader) else getattr(event, 'message_str', '') or '').strip()

    def _event_image_prompt_text(self, event: Any) -> str:
        text = self._event_current_image_request_text(event)
        scope = self._event_session_id(event)
        store = getattr(self, '_life_media_source_events', None)
        cached = store.get(scope) if isinstance(store, dict) else None
        if isinstance(cached, dict):
            cached_text = str(cached.get('text') or '').strip()
            cached_age = time.monotonic() - float(cached.get('timestamp') or 0)
            if cached_text and cached_age <= 10 * 60:
                if self._image_prompt_detail_score(cached_text) > self._image_prompt_detail_score(text):
                    return cached_text
        return text

    def _resolve_image_prompt(self, event: Any, prompt: str) -> tuple[str, bool, str]:
        tool_prompt = str(prompt or '').strip()
        event_prompt = self._event_image_prompt_text(event)
        aspect_ratio = self._image_prompt_aspect_ratio(event_prompt) or self._image_prompt_aspect_ratio(tool_prompt)
        event_score = self._image_prompt_detail_score(event_prompt)
        tool_score = self._image_prompt_detail_score(tool_prompt)
        event_length = len(''.join(event_prompt.split()))
        separators = sum(1 for char in event_prompt if char in '，,、；;。.!！?？：:\n\r（）()[]【】')
        reverse_resolver = getattr(self, '_last_reverse_prompt_for_scope', None)
        last_reverse_prompt = str(reverse_resolver(event) if callable(reverse_resolver) else '').strip()
        if tool_prompt and last_reverse_prompt and tool_prompt == last_reverse_prompt:
            return tool_prompt, True, aspect_ratio
        if tool_prompt and event_prompt and tool_score >= max(event_score + 32, int(event_score * 1.8)):
            if event_length < 18 and separators <= 1:
                return tool_prompt, False, aspect_ratio
            return tool_prompt, True, aspect_ratio
        direct_enough = (
            event_length >= 48
            or (event_length >= 18 and separators >= 2)
            or (event_length >= 28 and event_score >= max(tool_score + 18, int(tool_score * 1.4)))
        )
        if event_prompt and direct_enough:
            return event_prompt, True, aspect_ratio
        return tool_prompt or event_prompt, False, aspect_ratio

    @staticmethod
    def _image_generation_mode_label(direct_prompt: bool) -> str:
        return '保持原文' if direct_prompt else '智能提取'

    def _resolve_last_reverse_image_prompt(self, event: Any) -> tuple[str, str]:
        resolver = getattr(self, '_last_reverse_prompt_for_scope', None)
        if not callable(resolver):
            return '', ''
        prompt = str(resolver(event) or '').strip()
        return prompt, self._image_prompt_aspect_ratio(prompt)

    async def _resolve_last_reverse_image_prompt_async(self, event: Any) -> tuple[str, str]:
        resolver = getattr(self, '_last_reverse_prompt_record_for_scope', None)
        if callable(resolver):
            record = await resolver(event)
            prompt = str(record.get('prompt') or '').strip() if isinstance(record, dict) else ''
            if prompt:
                return prompt, self._image_prompt_aspect_ratio(prompt)
        return self._resolve_last_reverse_image_prompt(event)

    def _image_edit_available(self) -> bool:
        image_service = getattr(getattr(self, 'media', None), 'image', None)
        if image_service is None or not callable(getattr(image_service, 'edit_image', None)):
            return False
        checker = getattr(image_service, 'can_edit_image', None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return True

    def _life_character_reference_image(self) -> str:
        image_service = getattr(getattr(self, 'media', None), 'image', None)
        if image_service is None:
            return ''
        checker = getattr(image_service, 'can_edit_image', None)
        if callable(checker):
            try:
                if not checker():
                    return ''
            except Exception:
                return ''
        resolver = getattr(image_service, 'first_character_reference_image', None)
        if not callable(resolver):
            return ''
        try:
            return str(resolver() or '').strip()
        except Exception:
            return ''

    async def generate_life_image_asset(
        self,
        event: Any,
        prompt: str,
        aspect_ratio: str = '',
        *,
        contains_character: bool = False,
        preserve_reference_ratio: bool = False,
        trusted_identity: bool = False,
    ) -> Any:
        prompt = str(prompt or '').strip()
        if not prompt:
            raise ValueError('没有收到图片提示词。')
        aspect_ratio = str(aspect_ratio or '').strip() or self._image_prompt_aspect_ratio(prompt)
        if trusted_identity and contains_character:
            directed_prompt = prompt
            character_reference = self._life_character_reference_image()
        else:
            directed_prompt, detected_character, needs_character_reference = await self._directed_life_image_result(
                event,
                prompt,
                direct_prompt=True,
            )
            if contains_character and not detected_character:
                detected_character = True
            character_reference = self._life_character_reference_image() if needs_character_reference else ''
        if character_reference:
            reason = '已确认角色本人入镜' if trusted_identity and contains_character else '图片导演判定需要角色参考图'
            logger.debug(f'{LOG_PREFIX} {reason}，已自动切换到图生图。')
            return await self._edit_life_image_with_policy_retry(
                event,
                directed_prompt,
                character_reference,
                aspect_ratio,
                preserve_reference_ratio=preserve_reference_ratio,
            )
        return await self._generate_life_image_with_policy_retry(event, directed_prompt, aspect_ratio)

    async def life_image_generate(
        self,
        event: Any,
        prompt: str,
        *,
        use_last_reverse_prompt: bool = False,
        subject_route: str = 'free',
    ) -> str | None:
        current_character_request = False
        if use_last_reverse_prompt:
            prompt, aspect_ratio = await self._resolve_last_reverse_image_prompt_async(event)
            direct_prompt = True
            generation_mode = '上一条反推'
        else:
            route = self._normalize_image_subject_route(subject_route)
            event_prompt = self._event_image_prompt_text(event)
            tool_prompt = str(prompt or '').strip()
            current_character_request = route == 'current_character'
            if current_character_request and tool_prompt:
                prompt = tool_prompt
                direct_prompt = True
                aspect_ratio = self._image_prompt_aspect_ratio(event_prompt) or self._image_prompt_aspect_ratio(tool_prompt)
                generation_mode = '当前角色本人'
            else:
                prompt, direct_prompt, aspect_ratio = self._resolve_image_prompt(event, tool_prompt)
                generation_mode = self._image_generation_mode_label(direct_prompt)
        if not prompt:
            return '没有收到图片提示词。'
        scope = self._event_session_id(event)
        if not scope:
            return '当前会话不可发送图片。'
        started_at = time.monotonic()
        try:
            if direct_prompt:
                generated = await self.generate_life_image_asset(
                    event,
                    prompt,
                    aspect_ratio,
                    contains_character=current_character_request,
                    trusted_identity=current_character_request,
                )
                directed_prompt = prompt
            else:
                directed_prompt, _, needs_character_reference = await self._directed_life_image_result(
                    event,
                    prompt,
                    direct_prompt=False,
                )
                character_reference = self._life_character_reference_image() if needs_character_reference else ''
                if character_reference:
                    logger.debug(f'{LOG_PREFIX} 图片导演判定需要角色参考图，已自动切换到图生图。')
                    generated = await self._edit_life_image_with_policy_retry(
                        event,
                        directed_prompt,
                        character_reference,
                        aspect_ratio,
                        preserve_reference_ratio=False,
                    )
                else:
                    generated = await self._generate_life_image_with_policy_retry(event, directed_prompt, aspect_ratio)
            logger.debug(f'{LOG_PREFIX} 图片生成模式：{generation_mode}；长度：{len(directed_prompt)}')
            if not await self.send_message_if_not_recalled(
                scope,
                self.image_message_chain(generated.path),
                source_event=event,
            ):
                return '原消息已撤回，已取消图片发送。'
            self.note_structured_bot_message(scope, '[图片已发送]', source_event=event, media='图片')
            self._remember_life_image_for_scope(scope, generated.path)
            self.note_life_media_sent(event, '图片')
            return f'图片已发送。{await self._media_result_summary(generated.path, started_at)}。'
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f'{LOG_PREFIX} 图片生成或发送失败：{error}')
            return self._image_tool_failure_text('图片生成', error)

    async def edit_life_image(
        self,
        event: Any,
        prompt: str,
        reference_image: str = '',
        *,
        generate_without_reference: bool = False,
    ) -> str | None:
        raw_prompt = str(prompt or '').strip()
        prompt, direct_prompt, aspect_ratio = self._resolve_image_prompt(event, raw_prompt)
        if not prompt:
            return '没有收到图片编辑提示词。'
        scope = self._event_session_id(event)
        if not scope:
            return '当前会话不可发送图片。'
        reference = await self._resolve_life_image_reference_async(event, reference_image)
        if not reference:
            if generate_without_reference:
                logger.debug(f'{LOG_PREFIX} 图片编辑未找到参考图，已按结构化路由改走文生图。')
                return await self.life_image_generate(event, prompt)
            return '请先发送或引用一张要参考的图片。'
        started_at = time.monotonic()
        try:
            directed_prompt, _, needs_character_reference = await self._directed_life_image_result(
                event,
                prompt,
                direct_prompt=direct_prompt,
                reference=True,
            )
            if needs_character_reference:
                logger.debug(f'{LOG_PREFIX} 图片导演判定需要角色参考图，已自动切换到图生图。')
            logger.debug(f'{LOG_PREFIX} 图片生成模式：{self._image_generation_mode_label(direct_prompt)}；长度：{len(directed_prompt)}')
            generated = await self._edit_life_image_with_policy_retry(
                event,
                directed_prompt,
                reference,
                aspect_ratio,
                preserve_reference_ratio=not bool(aspect_ratio),
            )
            if not await self.send_message_if_not_recalled(
                scope,
                self.image_message_chain(generated.path),
                source_event=event,
            ):
                return '原消息已撤回，已取消图片发送。'
            self.note_structured_bot_message(scope, '[图片已发送]', source_event=event, media='图片')
            self._remember_life_image_for_scope(scope, generated.path)
            self.note_life_media_sent(event, '图片')
            return f'图片已根据参考图生成。{await self._media_result_summary(generated.path, started_at)}。'
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f'{LOG_PREFIX} 参考图生成或发送失败：{error}')
            return self._image_tool_failure_text('参考图生成', error)
