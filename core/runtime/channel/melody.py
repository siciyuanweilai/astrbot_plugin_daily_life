from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Protocol

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import File

from ...media.music import (
    MUSIC_PROMPT_MAX_UTF16_UNITS,
    MusicGenerationError,
    music_api_base,
    prompt_utf16_units,
)
from ..markers import LOG_PREFIX


class RuntimeMusicMediaComponentContext(Protocol):
    _event_session_id: Any
    _record_message_chain: Any
    _snapshot_media_event: Any
    _submit_background_tool: Any
    can_send_for_source: Any
    cancel_tool_reaction: Any
    config: Any
    media: Any
    note_structured_bot_message: Any
    send_message_if_not_recalled: Any


class RuntimeMusicMediaComponent:
    def __init__(self, context: RuntimeMusicMediaComponentContext) -> None:
        self.context = context

    async def life_music_generate(
        self,
        event: Any,
        prompt: str = "",
        *,
        instrumental: bool = False,
        action: str = "generate",
        delivery: str = "voice",
    ) -> str:
        if action not in {"generate", "resend"}:
            return "音乐操作仅支持 generate 或 resend。"
        if delivery not in {"voice", "file"}:
            return "音乐发送方式仅支持 voice 或 file。"
        prompt = str(prompt or "").strip()
        if action == "generate" and not prompt:
            return "请提供音乐描述。"
        if prompt_utf16_units(prompt) > MUSIC_PROMPT_MAX_UTF16_UNITS:
            return "音乐描述超过 3000 个 UTF-16 字符。"
        settings = getattr(self.context.config, "music_generation", None)
        if not getattr(settings, "enabled", False):
            return "音乐生成功能尚未启用。"
        if action == "generate":
            if not str(getattr(settings, "api_key", "") or "").strip():
                return "请先在音乐设置中填写接口密钥。"
            try:
                if not music_api_base(getattr(settings, "base_url", "")):
                    return "请先在音乐设置中填写接口地址。"
            except MusicGenerationError as exc:
                return str(exc)
        scope = self.context._event_session_id(event)
        if not scope:
            return "当前会话无法发送音乐。"
        previous = None
        recover = False
        if action == "resend":
            previous = await self.context.media.music.latest_for_scope(scope)
            if previous is None:
                recover = await self.context.media.music.has_task_for_scope(scope)
            if previous is None and not recover:
                return "当前会话没有可补发的音乐文件，或文件已被清理；未重新生成。"

        source_event = event
        snapshot = self.context._snapshot_media_event(event)

        async def generate_and_send() -> SimpleNamespace:
            generated = previous
            if recover:
                generated = await self.context.media.music.resume_for_scope(scope)
            elif generated is None:
                generated = await self.context.media.music.generate(
                    prompt,
                    instrumental=bool(instrumental),
                    scope=scope,
                )
            if not self.context.can_send_for_source(scope, source_event=snapshot):
                return SimpleNamespace(status="cancelled")
            if action == "generate":
                try:
                    await self.context.media.music.remember_for_scope(scope, generated)
                except Exception as exc:
                    logger.warning(
                        f"{LOG_PREFIX} 音乐补发记录暂未保存：{type(exc).__name__}"
                    )
            if delivery == "file":
                path = await self.context.media.music.mp3_path(generated)
                chain = MessageChain()
                chain.chain.append(File(name="音乐.mp3", file=str(path)))
            else:
                chain = self.context._record_message_chain(generated.path)
            sent = await self.context.send_message_if_not_recalled(
                scope,
                chain,
                source_event=snapshot,
                raise_delivery_errors=True,
            )
            if not sent:
                return SimpleNamespace(status="cancelled")
            try:
                self.context.note_structured_bot_message(
                    scope,
                    f"[已发送音乐{'文件' if delivery == 'file' else '语音'}：{generated.model}]",
                    source_event=snapshot,
                    media="音乐",
                )
            except Exception as exc:
                logger.warning(
                    f"{LOG_PREFIX} 音乐已发送，记录暂未保存：{type(exc).__name__}"
                )
            return SimpleNamespace(status="sent")

        try:
            return self.context._submit_background_tool(
                snapshot,
                source_event,
                generate_and_send,
                tool_name="life_music_generate",
                media="music",
                label="音乐生成" if action == "generate" else "音乐补发",
                category="durable",
                task_name="音乐生成" if action == "generate" else "音乐补发",
            )
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 音乐任务无法提交：{type(exc).__name__}")
            self.context.cancel_tool_reaction(event, "life_music_generate")
            return "音乐任务暂时无法提交，请稍后重试。"
