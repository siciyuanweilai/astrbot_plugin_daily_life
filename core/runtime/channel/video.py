from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain

from ...prompts import cache_friendly_prompt
from ..markers import LOG_PREFIX


class RuntimeVideoMediaMixin:
    @staticmethod
    def _video_prompt_duration_seconds(text: str) -> int:
        compact = str(text or "").strip()
        if not compact:
            return 0
        seconds: list[int] = []
        index = 0
        while index < len(compact):
            number, end = RuntimeVideoMediaMixin._video_read_number(compact, index)
            if end <= index:
                index += 1
                continue
            cursor = RuntimeVideoMediaMixin._video_skip_spaces(compact, end)
            if cursor < len(compact) and compact[cursor] in "-–—~到至":
                right_start = RuntimeVideoMediaMixin._video_skip_spaces(compact, cursor + 1)
                right, right_end = RuntimeVideoMediaMixin._video_read_number(compact, right_start)
                unit_at = RuntimeVideoMediaMixin._video_skip_spaces(compact, right_end)
                if right_end > right_start and RuntimeVideoMediaMixin._video_has_duration_unit(compact, unit_at):
                    seconds.append(RuntimeVideoMediaMixin._video_clamp_seconds(right))
                    index = unit_at + 1
                    continue
            if RuntimeVideoMediaMixin._video_has_duration_unit(compact, cursor):
                seconds.append(RuntimeVideoMediaMixin._video_clamp_seconds(number))
                index = cursor + 1
                continue
            index = end
        return max(seconds) if seconds else 0

    @staticmethod
    def _video_read_number(text: str, start: int) -> tuple[float, int]:
        if start >= len(text) or not text[start].isdigit():
            return 0.0, start
        end = start
        dot_seen = False
        while end < len(text):
            char = text[end]
            if char.isdigit():
                end += 1
                continue
            if char == "." and not dot_seen and end + 1 < len(text) and text[end + 1].isdigit():
                dot_seen = True
                end += 1
                continue
            break
        try:
            return float(text[start:end]), end
        except ValueError:
            return 0.0, start

    @staticmethod
    def _video_skip_spaces(text: str, start: int) -> int:
        while start < len(text) and text[start].isspace():
            start += 1
        return start

    @staticmethod
    def _video_has_duration_unit(text: str, start: int) -> bool:
        if start >= len(text):
            return False
        if text.startswith("秒钟", start) or text[start] == "秒":
            return True
        if text[start] in {"s", "S"}:
            next_index = start + 1
            return next_index >= len(text) or not text[next_index].isascii() or not text[next_index].isalnum()
        return False

    @staticmethod
    def _video_clamp_seconds(value: float) -> int:
        if value <= 0:
            return 0
        seconds = int(value) if value.is_integer() else int(value) + 1
        return max(1, min(15, seconds))

    def _event_video_prompt_text(self, event: Any) -> str:
        resolver = getattr(self, "_event_image_prompt_text", None)
        text = str(resolver(event) if callable(resolver) else "").strip()
        if not text:
            reader = getattr(self, "_event_user_history_text", None)
            text = str(reader(event) if callable(reader) else getattr(event, "message_str", "") or "").strip()
        return text

    def _resolve_video_prompt(self, event: Any, prompt: str) -> tuple[str, bool]:
        tool_prompt = str(prompt or "").strip()
        event_prompt = self._event_video_prompt_text(event)
        if not event_prompt:
            return tool_prompt, False
        event_score = self._image_prompt_detail_score(event_prompt)
        tool_score = self._image_prompt_detail_score(tool_prompt)
        line_count = sum(1 for line in event_prompt.splitlines() if line.strip())
        event_length = len("".join(event_prompt.split()))
        if event_length >= 80 and (line_count >= 3 or event_score >= max(tool_score + 80, int(tool_score * 1.45))):
            return event_prompt, True
        return tool_prompt or event_prompt, False

    async def generate_life_video_asset(
        self,
        event: Any,
        prompt: str,
        reference_image: str = "",
        *,
        direct_prompt: bool = False,
    ) -> Any:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("没有收到视频提示词。")
        reference_image = str(reference_image or "").strip()
        if reference_image:
            try:
                image_bytes = await self._load_life_video_reference_image(reference_image)
            except Exception as exc:
                raise RuntimeError(f"当前消息图片不可用，无法作为视频首帧：{self._media_error_summary(exc)}") from exc
            reference_aspect_ratio = self._life_video_reference_aspect_ratio(image_bytes)
        else:
            reference_aspect_ratio = ""
            first_frame = await self._generate_life_video_first_frame("", prompt, event)
            image_bytes = await self._load_life_video_reference_image(first_frame)
        directed_prompt = prompt if direct_prompt else await self._direct_life_video_prompt(event, prompt)
        aspect_ratio = self._image_prompt_aspect_ratio(prompt) or reference_aspect_ratio
        duration = self._video_prompt_duration_seconds(prompt)
        if aspect_ratio or duration:
            kwargs: dict[str, Any] = {}
            if aspect_ratio:
                kwargs["aspect_ratio"] = aspect_ratio
            if duration:
                kwargs["duration"] = duration
            return await self.media.video.generate_video(
                directed_prompt,
                image_bytes=image_bytes,
                **kwargs,
            )
        return await self.media.video.generate_video(directed_prompt, image_bytes=image_bytes)

    async def life_video_generate(self, event: Any, prompt: str) -> str:
        prompt, direct_prompt = self._resolve_video_prompt(event, prompt)
        if not prompt:
            return "没有收到视频提示词。"
        scope = self._event_session_id(event)
        if not scope:
            return "当前会话不可发送视频。"
        reference = await self._resolve_life_image_reference_async(event)
        request_id = self._register_life_video_request(scope, prompt, event)
        self._schedule_background_task(
            self._life_video_generate_background(scope, prompt, reference, event, request_id, direct_prompt=direct_prompt),
            label="生活视频生成",
            key=f"life_video:{scope}:{prompt[:80]}",
        )
        return "视频生成已开始。插件会先发送视频，发送成功后再补一句自然回复；本轮不要向用户发送等待提示。"

    async def _life_video_generate_background(
        self,
        scope: str,
        prompt: str,
        reference_image: str = "",
        event: Any = None,
        request_id: str = "",
        *,
        direct_prompt: bool = False,
    ) -> None:
        started_at = time.monotonic()
        first_frame = ""
        try:
            if reference_image:
                generated = await self.generate_life_video_asset(
                    event,
                    prompt,
                    reference_image,
                    direct_prompt=direct_prompt,
                )
            else:
                first_frame = await self._generate_life_video_first_frame(scope, prompt, event)
                generated = await self.generate_life_video_asset(
                    event,
                    prompt,
                    first_frame,
                    direct_prompt=direct_prompt,
                )
            if not await self.send_message_if_not_recalled(
                scope,
                self.video_message_chain(generated.url),
                source_event=event,
            ):
                return
            summary = await self._media_result_summary(generated.url, started_at)
            logger.info(f"{LOG_PREFIX} 视频已发送：{summary}")
            self.note_structured_bot_message(
                scope,
                f"[视频已发送：{summary}]",
                source_event=event,
                media="视频",
            )
            self._remember_life_image_for_scope(scope, first_frame or reference_image)
            self.note_life_media_sent(event or scope, "视频")
            await self._send_life_video_followup(scope, prompt, summary, event, request_id)
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 视频生成或发送失败：{error}")
            await self._send_life_video_failure_notice(scope, prompt, error, event)
        finally:
            self._finish_life_video_request(request_id)

    async def _generate_life_video_first_frame(self, scope: str, prompt: str, event: Any) -> str:
        image_service = getattr(getattr(self, "media", None), "image", None)
        if image_service is None:
            raise RuntimeError("未配置图片生成服务，无法自动生成视频首帧。")
        directed = await self._direct_life_image_payload(event, prompt, reference=False)
        image_prompt = str(getattr(directed, "prompt", "") or "").strip()
        if not image_prompt:
            raise RuntimeError("视频首帧图片提示词为空。")
        aspect_ratio = self._image_prompt_aspect_ratio(prompt) or self._image_prompt_aspect_ratio(image_prompt)
        character_reference = self._life_character_reference_image() if getattr(directed, "needs_character_reference", False) is True else ""
        if character_reference:
            logger.debug(f"{LOG_PREFIX} 视频首帧判定需要角色参考图，已自动切换到图生图。")
            generated = await self._edit_life_image_with_policy_retry(
                event,
                image_prompt,
                character_reference,
                aspect_ratio,
                preserve_reference_ratio=False,
            )
        else:
            generate_image = getattr(image_service, "generate_image", None)
            if not callable(generate_image):
                raise RuntimeError("未配置图片生成服务，无法自动生成视频首帧。")
            generated = await self._generate_life_image_with_policy_retry(event, image_prompt, aspect_ratio)
        path = str(getattr(generated, "path", "") or "").strip()
        if not path:
            raise RuntimeError("首帧图片生成完成但没有返回图片路径。")
        logger.info(f"{LOG_PREFIX} 视频首帧图片已生成：{Path(path).name}")
        return path

    def _life_video_requests(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_life_video_pending_requests", None)
        if not isinstance(store, dict):
            store = {}
            self._life_video_pending_requests = store
        return store

    def _register_life_video_request(self, scope: str, prompt: str, event: Any) -> str:
        request_id = uuid.uuid4().hex
        marker = {
            "id": request_id,
            "scope": str(scope or "").strip(),
            "prompt": str(prompt or "").strip(),
            "created_at": time.monotonic(),
            "llm_final_seen": False,
        }
        self._life_video_requests()[request_id] = marker
        for source in self._event_sources(event):
            setattr(source, "_daily_life_video_request_id", request_id)
        return request_id

    def _life_video_request_from_event(self, event: Any) -> dict[str, Any] | None:
        requests = self._life_video_requests()
        for source in self._event_sources(event):
            request_id = str(getattr(source, "_daily_life_video_request_id", "") or "").strip()
            if request_id and request_id in requests:
                return requests[request_id]
        scope = self._event_session_id(event)
        if not scope:
            return None
        candidates = [
            item
            for item in requests.values()
            if str(item.get("scope") or "") == scope and time.monotonic() - float(item.get("created_at") or 0) <= 20
        ]
        return candidates[-1] if candidates else None

    def _finish_life_video_request(self, request_id: str) -> None:
        request_id = str(request_id or "").strip()
        if request_id:
            self._life_video_requests().pop(request_id, None)

    async def _send_life_video_failure_notice(
        self,
        scope: str,
        prompt: str,
        error: str,
        event: Any,
    ) -> bool:
        text = await self._generate_life_video_failure_text(event, prompt, error)
        if not text:
            return False
        text_sent = await self._send_life_video_text(scope, text, source_event=event)
        if text_sent:
            await self._append_assistant_history(scope, text)
        return text_sent

    async def _life_video_reply_model(self, event: Any) -> tuple[Any, Any, str] | None:
        composer = getattr(self, "composer", None)
        get_provider = getattr(composer, "_get_provider", None)
        call_llm = getattr(composer, "_call_llm_text", None)
        if not callable(get_provider) or not callable(call_llm):
            return None
        provider = await get_provider("")
        if provider is None:
            return None
        persona_getter = getattr(composer, "_get_persona", None)
        persona = ""
        if callable(persona_getter):
            try:
                persona = str(await persona_getter(self._event_session_id(event)) or "").strip()
            except TypeError:
                persona = str(await persona_getter() or "").strip()
        return provider, call_llm, persona

    def hold_life_video_final_text(self, event: Any) -> bool:
        marker = self._life_video_request_from_event(event)
        if not marker:
            return False
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not self._is_llm_result_object(result):
            return False
        marker["llm_final_seen"] = True
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        else:
            chain.clear()
        logger.debug(f"{LOG_PREFIX} 已拦截视频生成完成前的文字回复，等待视频发送成功后再补话。")
        return True

    async def _send_life_video_followup(
        self,
        scope: str,
        prompt: str,
        summary: str,
        event: Any,
        request_id: str,
    ) -> bool:
        text = await self._generate_life_video_followup_text(event, prompt, summary)
        if not text:
            return False
        text_sent = await self._send_life_video_text(scope, text, source_event=event)
        if text_sent:
            await self._append_assistant_history(scope, text)
        return text_sent

    async def _generate_life_video_followup_text(self, event: Any, prompt: str, summary: str) -> str:
        try:
            reply_model = await self._life_video_reply_model(event)
            if reply_model is None:
                return ""
            provider, call_llm, persona = reply_model
            fixed = """你正在给刚刚交付完成的一段生活短视频补一句自然回复。
这是成品交付后的回应，不是过程播报。
只输出一句中文短回复。
回复要像角色本人顺手接话，可以轻轻评价画面氛围、人物状态或镜头感，也可以自然回应用户拍视频的请求。
保持生活化语气，不展开解释，不描述内部流程或生成过程。"""
            dynamic = (
                f"角色口吻参考：{persona[:800] if persona else '按当前对话中的角色口吻自然回复。'}\n"
                f"用户的视频要求：{str(prompt or '').strip()}\n"
                f"视频发送结果：{str(summary or '').strip()}"
            )
            prompt_text = cache_friendly_prompt(fixed, dynamic, dynamic_title="已发送视频")
            text = await call_llm(
                provider,
                prompt_text,
                f"daily_life_video_followup_{uuid.uuid4().hex[:8]}",
                empty_retries=0,
                primary_provider_id="",
            )
            return self._clean_life_video_followup_text(text)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 视频发送后补话生成失败：{self._media_error_summary(exc)}")
            return ""

    async def _generate_life_video_failure_text(self, event: Any, prompt: str, error: str) -> str:
        try:
            reply_model = await self._life_video_reply_model(event)
            if reply_model is None:
                return ""
            provider, call_llm, persona = reply_model
            detail = " ".join(str(error or "").split())
            if len(detail) > 500:
                detail = detail[:500].rstrip() + "..."
            fixed = """你正在给一次生活短视频生成失败补一句自然回复。
这是面向用户的聊天回复，不是日志，也不是技术报告。
只输出一句中文短回复。
语气要像当前角色本人顺手说明情况，可以轻轻带一点歉意或吐槽。
不要输出接口、模型、任务、报错；不要说内部工具名、通道、HTTP、JSON、堆栈或重试细节。"""
            dynamic = (
                f"角色口吻参考：{persona[:800] if persona else '按当前对话中的角色口吻自然回复。'}\n"
                f"用户的视频要求：{str(prompt or '').strip()}\n"
                f"内部失败原因（只用于理解，不要复述）：{detail or '未知'}"
            )
            prompt_text = cache_friendly_prompt(fixed, dynamic, dynamic_title="视频未发送")
            text = await call_llm(
                provider,
                prompt_text,
                f"daily_life_video_failure_{uuid.uuid4().hex[:8]}",
                empty_retries=0,
                primary_provider_id="",
            )
            return self._clean_life_video_followup_text(text)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 视频失败补话生成失败：{self._media_error_summary(exc)}")
            return ""

    @staticmethod
    def _clean_life_video_followup_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = text.replace("```", "").strip()
        lines = [line.strip(" \t-　") for line in text.splitlines() if line.strip()]
        text = " ".join(lines).strip()
        if not text:
            return ""
        for separator in ("。", "！", "？", "!", "?"):
            index = text.find(separator)
            if 0 <= index < 120:
                return text[: index + 1].strip()
        return text[:80].strip()

    async def _send_life_video_text(
        self,
        scope: str,
        text: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        text = str(text or "").strip()
        if not scope or not text:
            return False
        if not await self.send_message_if_not_recalled(
            scope,
            MessageChain().message(text),
            source_event=source_event,
            source_message_id=source_message_id,
        ):
            return False
        self.note_structured_bot_message(scope, text)
        return True

    async def _load_life_video_reference_image(self, reference_image: str) -> bytes:
        reference_image = str(reference_image or "").strip()
        if not reference_image:
            raise ValueError("缺少视频首帧参考图")
        load_image = getattr(getattr(self.media, "image", None), "_load_reference_image", None)
        if callable(load_image):
            image_bytes, _ = await load_image(reference_image)
            return image_bytes
        path = Path(reference_image).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"参考图片不存在：{reference_image}")
        return await asyncio.to_thread(path.read_bytes)

    def _life_video_reference_aspect_ratio(self, image_bytes: bytes) -> str:
        resolver = getattr(getattr(self.media, "image", None), "_reference_image_aspect_ratio", None)
        if not callable(resolver):
            return ""
        try:
            return str(resolver(image_bytes) or "").strip()
        except Exception:
            return ""
