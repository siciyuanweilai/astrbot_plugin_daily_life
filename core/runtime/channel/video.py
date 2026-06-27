from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain

from ..markers import LOG_PREFIX


class RuntimeVideoMediaMixin:
    _VIDEO_PENDING_TTL_SECONDS = 15 * 60
    _VIDEO_FINAL_TEXT_CAPTURE_SECONDS = 2 * 60

    async def life_video_generate(self, event: Any, prompt: str) -> str:
        prompt = str(prompt or "").strip()
        if not prompt:
            return "没有收到视频提示词。"
        scope = self._event_session_id(event)
        if not scope:
            return "当前会话不可发送视频。"
        reference = await self._resolve_life_image_reference_async(event)
        token = self._begin_life_video_delivery(scope, event)
        self._schedule_background_task(
            self._life_video_generate_background(scope, prompt, reference, event, token),
            label="生活视频生成",
            key=f"life_video:{scope}:{prompt[:80]}",
        )
        return "视频生成已开始，完成后我会发送到当前会话。"

    async def _life_video_generate_background(
        self,
        scope: str,
        prompt: str,
        reference_image: str = "",
        event: Any = None,
        token: str = "",
    ) -> None:
        started_at = time.monotonic()
        first_frame = ""
        error = ""
        try:
            image_bytes: bytes
            if reference_image:
                try:
                    image_bytes = await self._load_life_video_reference_image(reference_image)
                except Exception as exc:
                    raise RuntimeError(f"当前消息图片不可用，无法作为视频首帧：{self._media_error_summary(exc)}") from exc
            else:
                first_frame = await self._generate_life_video_first_frame(scope, prompt, event)
                image_bytes = await self._load_life_video_reference_image(first_frame)
            directed_prompt = await self._direct_life_video_prompt(event, prompt)
            generated = await self.media.video.generate_video(directed_prompt, image_bytes=image_bytes)
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
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 视频生成或发送失败：{error}")
            await self._send_life_video_fallback(scope, first_frame, source_event=event)
        finally:
            await self._finish_life_video_delivery(scope, token, error=error)

    async def _generate_life_video_first_frame(self, scope: str, prompt: str, event: Any) -> str:
        image_service = getattr(getattr(self, "media", None), "image", None)
        generate_image = getattr(image_service, "generate_image", None)
        if not callable(generate_image):
            raise RuntimeError("未配置图片生成服务，无法自动生成视频首帧。")
        image_prompt = await self._direct_life_image_prompt(event, prompt)
        generated = await generate_image(image_prompt)
        path = str(getattr(generated, "path", "") or "").strip()
        if not path:
            raise RuntimeError("首帧图片生成完成但没有返回图片路径。")
        logger.info(f"{LOG_PREFIX} 视频首帧图片已生成：{Path(path).name}")
        return path

    async def _send_life_video_fallback(
        self,
        scope: str,
        first_frame: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> None:
        first_frame_sent = False
        if first_frame:
            try:
                if not await self.send_message_if_not_recalled(
                    scope,
                    self.image_message_chain(Path(first_frame)),
                    source_event=source_event,
                    source_message_id=source_message_id,
                ):
                    return
                self.note_structured_bot_message(scope, "[视频失败，已发送兜底图片]", media="图片")
                self._remember_life_image_for_scope(scope, first_frame)
                self.note_life_media_sent(source_event or scope, "图片")
                first_frame_sent = True
            except Exception as exc:
                logger.warning(f"{LOG_PREFIX} 视频兜底图片发送失败：{self._media_error_summary(exc)}")
        text = "这段没录成。"
        if first_frame_sent:
            text = "这段没录成，先把刚拍到的这张给你看。"
        text_sent = await self._send_life_video_text(
            scope,
            text,
            source_event=source_event,
            source_message_id=source_message_id,
        )
        if text_sent:
            await self._append_assistant_history(scope, text)

    def _life_video_pending_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_life_video_pending", None)
        if not isinstance(store, dict):
            self._life_video_pending = {}
            store = self._life_video_pending
        return store

    def _begin_life_video_delivery(self, scope: str, event: Any) -> str:
        scope = str(scope or "").strip()
        if not scope:
            return ""
        self._prune_life_video_pending()
        message_id = self._event_message_id(event)
        token = f"{message_id or id(event)}:{len(self._life_video_pending_store()) + 1}"
        self._life_video_pending_store()[scope] = {
            "token": token,
            "event_id": id(event),
            "message_id": message_id,
            "created_at": asyncio.get_running_loop().time(),
            "final_text": "",
        }
        return token

    def _prune_life_video_pending(self) -> None:
        store = self._life_video_pending_store()
        now = asyncio.get_running_loop().time()
        for scope, item in list(store.items()):
            try:
                expired = now - float(item.get("created_at") or 0) > self._VIDEO_PENDING_TTL_SECONDS
            except (TypeError, ValueError):
                expired = True
            if expired:
                store.pop(scope, None)

    def _life_video_pending_matches_event(self, pending: dict[str, Any], event: Any) -> bool:
        pending_message_id = str(pending.get("message_id") or "").strip()
        current_message_id = self._event_message_id(event)
        if pending_message_id and current_message_id:
            return pending_message_id == current_message_id
        if pending.get("event_id") == id(event):
            return True
        if pending_message_id or current_message_id:
            return False
        try:
            age = asyncio.get_running_loop().time() - float(pending.get("created_at") or 0)
        except (TypeError, ValueError):
            return False
        return 0 <= age <= self._VIDEO_FINAL_TEXT_CAPTURE_SECONDS

    def hold_life_video_final_text(self, event: Any) -> bool:
        scope = self._event_session_id(event)
        if not scope:
            return False
        self._prune_life_video_pending()
        pending = self._life_video_pending_store().get(scope)
        if not pending or not self._life_video_pending_matches_event(pending, event):
            return False
        text = self._voice_switch_reply_text_from_event(event)
        if not text:
            return False
        pending["final_text"] = text
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        else:
            result = getattr(event, "get_result", lambda: None)()
            chain = getattr(result, "chain", None)
            if isinstance(chain, list):
                chain.clear()
        logger.debug(f"{LOG_PREFIX} 已暂存视频生成完成后的文字回复，等待视频发送结果。")
        return True

    async def _finish_life_video_delivery(self, scope: str, token: str, *, error: str = "") -> None:
        pending = self._life_video_pending_store().get(scope)
        if not pending or pending.get("token") != token:
            return
        self._life_video_pending_store().pop(scope, None)
        if error:
            return
        text = str(pending.get("final_text") or "").strip()
        if not text:
            return
        if await self._send_life_video_text(scope, text, source_message_id=str(pending.get("message_id") or "")):
            await self._append_assistant_history(scope, text)

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
