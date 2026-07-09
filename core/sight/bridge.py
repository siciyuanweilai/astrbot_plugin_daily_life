import asyncio
import contextlib
import html
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Plain
from astrbot.core.agent.message import TextPart

from ..runtime.markers import LOG_PREFIX
from .bili import BiliTarget, fetch_bili_metadata, find_bili_target, resolve_bili_target
from .brief import SightBrief
from .clip import SightClip, SightInsight
from .cookie import BiliCookieJar
from .digest import (
    VIDEO_ANSWER_BOUNDARY_RULE,
    content_details,
    frame_note_from_text,
    frame_prompt,
    insight_from_notes,
    tool_result_text,
)
from .embed import embed_local_markdown_images
from .flight import SightFlight, sight_flight_key
from .login import BiliLoginService, BiliLoginStatus
from .model import get_sight_provider
from .note import SightNote, SightNoteError
from .probe import (
    clips_from_items,
    clips_from_text_links,
    clips_from_value,
    dedupe_clips,
    explicit_clip,
    source_from_value,
)
from .sample import SightFrame, extract_video_frames, prepare_sample_video_source, sight_cache_dir
from .prune import SightCleanupMixin
from .reader import AUDIO_TRANSCRIPT_TIMEOUT_SECONDS, SightReader, SightTextResult
from .vault import SightVault


BILI_RESOLVE_TIMEOUT_SECONDS = 10
DEFAULT_SIGHT_TOTAL_TIMEOUT_SECONDS = 300


class SightMixin(SightCleanupMixin):
    def _init_sight(self) -> None:
        self._sight_vault = SightVault(
            getattr(self, "archive", None),
            ttl_seconds=self._sight_cache_ttl_seconds(),
            max_items=self._sight_cache_max_items(),
        )
        self._sight_reader = SightReader(self)
        self._sight_brief = SightBrief(self)
        self._sight_flight = SightFlight()
        self._sight_note = SightNote(self)

    def _sight_vault_for_runtime(self) -> SightVault:
        vault = getattr(self, "_sight_vault", None)
        if isinstance(vault, SightVault) and getattr(vault, "ttl_seconds", None) == self._sight_cache_ttl_seconds() and getattr(vault, "max_items", None) == self._sight_cache_max_items():
            return vault
        vault = SightVault(
            getattr(self, "archive", None),
            ttl_seconds=self._sight_cache_ttl_seconds(),
            max_items=self._sight_cache_max_items(),
        )
        self._sight_vault = vault
        return vault

    def _sight_cache_ttl_seconds(self) -> int:
        settings = getattr(getattr(self, "config", None), "sight", None)
        hours = max(1, int(getattr(settings, "video_cache_ttl_hours", 2) or 2))
        return hours * 3600

    def _sight_cache_max_items(self) -> int:
        settings = getattr(getattr(self, "config", None), "sight", None)
        return max(8, int(getattr(settings, "video_cache_max_items", 60) or 60))

    def _sight_reader_for_runtime(self) -> SightReader:
        reader = getattr(self, "_sight_reader", None)
        if isinstance(reader, SightReader) and reader.settings_signature == self._sight_reader_signature():
            return reader
        reader = SightReader(self)
        self._sight_reader = reader
        return reader

    def _sight_reader_signature(self) -> tuple[object, ...]:
        settings = getattr(getattr(self, "config", None), "sight", None)
        max_chars = max(500, int(getattr(settings, "max_transcript_chars", 8000) or 8000))
        mode = str(getattr(settings, "audio_transcript_mode", "local") or "local").strip().lower()
        return (
            max_chars,
            mode,
            AUDIO_TRANSCRIPT_TIMEOUT_SECONDS,
            int(getattr(settings, "local_asr_timeout_seconds", 900) or 900),
        )

    def _sight_brief_for_runtime(self) -> SightBrief:
        brief = getattr(self, "_sight_brief", None)
        if isinstance(brief, SightBrief):
            return brief
        brief = SightBrief(self)
        self._sight_brief = brief
        return brief

    def _sight_flight_for_runtime(self) -> SightFlight:
        flight = getattr(self, "_sight_flight", None)
        if isinstance(flight, SightFlight):
            return flight
        flight = SightFlight()
        self._sight_flight = flight
        return flight

    def _sight_note_for_runtime(self) -> SightNote:
        note = getattr(self, "_sight_note", None)
        if isinstance(note, SightNote):
            return note
        note = SightNote(self)
        self._sight_note = note
        return note

    def remove_recalled_sight_context(self, scope: str, message_id: str) -> None:
        self._schedule_background_task(
            self._sight_vault_for_runtime().remove_message(scope, message_id),
            label="撤回视频理解清理",
            key=f"sight_recall:{scope}:{message_id}",
        )

    def _sight_cache_dir(self) -> Path:
        return sight_cache_dir(getattr(self, "data_path", None))

    def _sight_prepare_cache_path(self, clip: SightClip) -> Path:
        return self._sight_cache_dir() / "prepare" / f"{clip.key}.json"

    def _save_sight_prepare_cache(
        self,
        clip: SightClip,
        *,
        source_note: str,
        source_path: str = "",
        frame_notes: list[str],
        text_result: SightTextResult,
        metadata: dict[str, Any],
        error: str,
    ) -> None:
        path = self._sight_prepare_cache_path(clip)
        payload = {
            "source_note": str(source_note or ""),
            "source_path": str(source_path or ""),
            "frame_notes": [str(item or "").strip() for item in frame_notes if str(item or "").strip()],
            "metadata": dict(metadata or {}),
            "error": str(error or ""),
            "text_result": {
                "transcript": str(text_result.transcript or ""),
                "transcript_source": str(text_result.transcript_source or ""),
                "note": str(text_result.note or ""),
                "note_source": str(text_result.note_source or ""),
                "metadata": dict(text_result.metadata or {}),
                "errors": [str(item or "") for item in list(text_result.errors or []) if str(item or "").strip()],
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _load_sight_prepare_cache(self, clip: SightClip) -> dict[str, Any] | None:
        path = self._sight_prepare_cache_path(clip)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        text_payload = payload.get("text_result") if isinstance(payload.get("text_result"), dict) else {}
        return {
            "source_note": str(payload.get("source_note") or ""),
            "source_path": str(payload.get("source_path") or ""),
            "frame_notes": [str(item or "").strip() for item in list(payload.get("frame_notes") or []) if str(item or "").strip()],
            "metadata": dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {},
            "error": str(payload.get("error") or ""),
            "text_result": SightTextResult(
                transcript=str(text_payload.get("transcript") or ""),
                transcript_source=str(text_payload.get("transcript_source") or ""),
                note=str(text_payload.get("note") or ""),
                note_source=str(text_payload.get("note_source") or ""),
                metadata=dict(text_payload.get("metadata") or {}) if isinstance(text_payload.get("metadata"), dict) else {},
                errors=[str(item or "") for item in list(text_payload.get("errors") or []) if str(item or "").strip()],
            ),
        }

    def _clear_sight_prepare_cache(self, clip: SightClip) -> None:
        with contextlib.suppress(OSError):
            self._sight_prepare_cache_path(clip).unlink()

    def _bili_cookie_jar_for_runtime(self) -> BiliCookieJar:
        jar = getattr(self, "_bili_cookie_jar", None)
        if isinstance(jar, BiliCookieJar):
            return jar
        jar = BiliCookieJar(getattr(self, "data_path", None) or self._sight_cache_dir())
        self._bili_cookie_jar = jar
        return jar

    async def bili_login(self, event: Any) -> AsyncIterator[Any]:
        if self._event_is_group_message(event):
            yield self._sight_plain_result(event, "B站登录请在私聊里使用。")
            return
        jar = self._bili_cookie_jar_for_runtime()
        if jar.is_logged_in():
            yield self._sight_plain_result(event, "B站已登录，如需重新登录请先使用 /B站登出")
            return
        login = BiliLoginService()
        qr = await login.generate()
        if not qr:
            yield self._sight_plain_result(event, "B站登录二维码生成失败，请稍后再试。")
            return
        qr_path = self._sight_cache_dir() / f"bili_login_{uuid.uuid4().hex[:8]}.png"
        try:
            import segno

            await asyncio.to_thread(lambda: segno.make(qr.url).save(str(qr_path), scale=10, border=4))
        except Exception as exc:
            yield self._sight_plain_result(event, f"B站登录二维码生成失败：{exc}")
            return
        yield_chain = getattr(event, "chain_result", None)
        if callable(yield_chain):
            yield yield_chain(
                [
                    Plain("请使用 B站 App 扫描二维码登录，二维码有效期 3 分钟。"),
                    Image.fromFileSystem(str(qr_path)),
                ]
            )
        result = await login.run_until_complete(qr.key, total_timeout=180)
        with contextlib.suppress(OSError):
            await asyncio.to_thread(qr_path.unlink)
        if result.status == BiliLoginStatus.SUCCESS and result.cookies and await asyncio.to_thread(jar.save, result.cookies):
            yield self._sight_plain_result(event, "B站登录成功，后续 B站视频总结会自动使用登录态。")
            return
        if result.status == BiliLoginStatus.EXPIRED:
            yield self._sight_plain_result(event, "B站登录二维码已过期，请重新使用 /B站登录。")
            return
        if result.status == BiliLoginStatus.TIMEOUT:
            yield self._sight_plain_result(event, "B站登录超时，请重新使用 /B站登录。")
            return
        yield self._sight_plain_result(event, "B站登录失败，请重新使用 /B站登录。")

    async def bili_logout(self, event: Any) -> Any:
        if self._event_is_group_message(event):
            return self._sight_plain_result(event, "B站登录请在私聊里使用。")
        jar = self._bili_cookie_jar_for_runtime()
        if not jar.is_logged_in():
            return self._sight_plain_result(event, "当前未登录 B站。")
        await asyncio.to_thread(jar.clear)
        return self._sight_plain_result(event, "已退出 B站登录。")

    async def bili_status(self, event: Any) -> Any:
        if self._event_is_group_message(event):
            return self._sight_plain_result(event, "B站登录请在私聊里使用。")
        status = "已登录" if self._bili_cookie_jar_for_runtime().is_logged_in() else "未登录"
        return self._sight_plain_result(event, f"B站登录状态：{status}")

    def _sight_clips_from_event(self, event: Any, explicit: str = "") -> list[SightClip]:
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event) or f"event:{id(event)}"
        text = str(getattr(event, "message_str", "") or "").strip()
        clips: list[SightClip] = []
        if explicit:
            clip = explicit_clip(explicit, scope=scope, message_id=message_id, text=text)
            if clip:
                clips.append(clip)
        clips.extend(
            clips_from_items(
                self._event_message_items(event),
                scope=scope,
                message_id=message_id,
                origin="current",
                text=text,
            )
        )
        clips.extend(clips_from_text_links(text, scope=scope, message_id=message_id))
        clips.extend(self._sight_quote_clips_from_event(event, scope, message_id, text))
        return dedupe_clips(clips)

    async def _sight_clips_from_event_async(self, event: Any, explicit: str = "") -> list[SightClip]:
        clips = list(self._sight_clips_from_event(event, explicit))
        if explicit:
            return dedupe_clips(clips)
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event) or f"event:{id(event)}"
        text = str(getattr(event, "message_str", "") or "").strip()
        quote_clips = await self._sight_quote_video_clips_from_event(event, scope, message_id, text)
        if quote_clips:
            clips = [
                clip
                for clip in clips
                if clip.origin != "quote" or self._sight_clip_source_is_usable(clip.source)
            ]
        return dedupe_clips([*quote_clips, *clips])

    def _sight_quote_clips_from_event(
        self,
        event: Any,
        scope: str,
        message_id: str,
        text: str,
    ) -> list[SightClip]:
        clips: list[SightClip] = []
        for source in self._event_sources(event):
            for attr in ("quote", "reply", "reply_message"):
                clips.extend(
                    clips_from_value(
                        getattr(source, attr, None),
                        scope=scope,
                        message_id=message_id,
                        origin="quote",
                        text=text,
                    )
                )
            for item in self._event_message_items(source):
                kind = self._event_component_kind(item)
                if "reply" not in kind and "quote" not in kind:
                    continue
                clips.extend(
                    clips_from_value(
                        item,
                        scope=scope,
                        message_id=message_id,
                        origin="quote",
                        text=text,
                    )
                )
            raw = getattr(getattr(source, "message_obj", None), "raw_message", None) or getattr(source, "raw_message", None)
            if isinstance(raw, dict):
                clips.extend(
                    clips_from_value(
                        raw,
                        scope=scope,
                        message_id=message_id,
                        origin="quote",
                        text=text,
                    )
                )
        return clips

    async def _sight_quote_video_clips_from_event(
        self,
        event: Any,
        scope: str,
        message_id: str,
        text: str,
    ) -> list[SightClip]:
        clips: list[SightClip] = []
        for source in self._event_sources(event):
            for item in self._event_message_items(source):
                kind = self._event_component_kind(item)
                if "reply" not in kind and "quote" not in kind:
                    continue
                clips.extend(await self._sight_video_clips_from_component(item, scope=scope, message_id=message_id, origin="quote", text=text))
            for attr in ("quote", "reply", "reply_message"):
                clips.extend(
                    await self._sight_video_clips_from_component(
                        getattr(source, attr, None),
                        scope=scope,
                        message_id=message_id,
                        origin="quote",
                        text=text,
                    )
                )
        return dedupe_clips(clips)

    @staticmethod
    def _sight_clip_source_is_usable(source: str) -> bool:
        text = str(source or "").strip()
        if not text:
            return False
        if text.lower().startswith(("http://", "https://", "file://", "base64://", "data:")):
            return True
        try:
            if Path(text).expanduser().exists():
                return True
        except OSError:
            return False
        return Path(text).suffix.lower() in {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv", ".ts"}

    def event_has_sight_video(self, event: Any) -> bool:
        return bool(event is not None and self._sight_clips_from_event(event))

    def schedule_video_context_from_event(self, event: Any) -> bool:
        if event is None or self._event_has_command_handler(event) or self.event_was_recalled(event, log_skip=True):
            return False
        clips = self._sight_clips_from_event(event)
        if not clips:
            return False
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event)
        if not scope:
            return False
        return self._schedule_background_task(
            self._collect_sight_context_background(event),
            label="视频上下文理解",
            key=f"sight:{scope}:{message_id or id(event)}",
        )

    def schedule_bili_summary_from_event(self, event: Any) -> bool:
        if event is None or self._event_has_command_handler(event) or self.event_was_recalled(event, log_skip=True):
            return False
        settings = getattr(getattr(self, "config", None), "sight", None)
        if not bool(getattr(settings, "bili_auto_summary", True)):
            return False
        target = find_bili_target(event)
        if not target:
            return False
        scope = self._event_session_id(event)
        if not scope:
            return False
        stopper = getattr(self, "_suppress_default_llm", None)
        if callable(stopper):
            stopper(event)
        message_id = self._event_message_id(event) or f"event:{id(event)}"
        return self._schedule_background_task(
            self._send_bili_summary_background(event, target),
            label="B站视频总结",
            key=f"bili:{scope}:{target.identity or message_id}",
        )

    async def _collect_sight_context_background(self, event: Any) -> None:
        if self.event_was_recalled(event, log_skip=True):
            return
        for clip in (await self._sight_clips_from_event_async(event))[:2]:
            if self.event_was_recalled(event, log_skip=True):
                return
            cached = await self._sight_vault_for_runtime().get(clip.key)
            insight = cached or await self._understand_sight_clip(event, clip)
            if insight and insight.summary:
                self._apply_sight_insight_to_structured(insight)

    async def _send_bili_summary_background(self, event: Any, target: BiliTarget) -> None:
        if self.event_was_recalled(event, log_skip=True):
            return
        resolved = await resolve_bili_target(
            target,
            timeout_seconds=BILI_RESOLVE_TIMEOUT_SECONDS,
        )
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event) or f"event:{id(event)}"
        if not resolved or not resolved.canonical_url:
            detail = "没有识别到有效视频链接"
            logger.warning(f"{LOG_PREFIX} B站视频自动总结失败：{detail}")
            await self._send_bili_summary_failure(event, detail, scope=scope, message_id=message_id)
            return
        metadata = await fetch_bili_metadata(
            resolved,
            timeout_seconds=BILI_RESOLVE_TIMEOUT_SECONDS,
            cookies=self._bili_cookie_jar_for_runtime().get(),
        )
        if not metadata:
            logger.debug(f"{LOG_PREFIX} B站视频元数据未获取：{resolved.bvid or resolved.canonical_url}")
        metadata_dict = metadata.as_dict() if metadata else {"platform": "bilibili", "bvid": resolved.bvid, "url": resolved.canonical_url}
        title = str(metadata_dict.get("title") or "").strip()
        clip = SightClip(
            scope=scope,
            message_id=message_id,
            source=resolved.canonical_url,
            name=title or resolved.bvid or "B站视频",
            origin="bilibili",
            text=str(getattr(event, "message_str", "") or "").strip(),
            metadata=metadata_dict,
        )
        log_subject = self._bili_summary_log_subject(metadata_dict, resolved)
        logger.info(f"{LOG_PREFIX} B站视频自动总结开始：{log_subject}")
        insight = await self._understand_sight_clip(event, clip)
        if self.event_was_recalled(event, log_skip=True):
            return
        if not insight or insight.status == "failed":
            detail = getattr(insight, "error", "") or getattr(insight, "summary", "") or "没有拿到可确认的视频内容"
            logger.warning(f"{LOG_PREFIX} B站视频自动总结失败：{detail}")
            await self._send_bili_summary_failure(event, detail, scope=scope, message_id=message_id)
            return
        insight = self._sight_insight_for_clip(insight, clip)
        markdown = self._cached_sight_note_markdown(insight, style="professional")
        try:
            if not markdown:
                markdown = await self._sight_note_for_runtime().compose(insight, style="professional")
            markdown = self._sight_note_for_runtime().normalize(insight, markdown)
        except SightNoteError as exc:
            detail = str(exc) or "总结模型生成失败"
            logger.warning(f"{LOG_PREFIX} B站视频自动总结失败：{detail}")
            await self._send_bili_summary_failure(event, detail, scope=scope, message_id=message_id)
            return
        insight = await self._cache_sight_note_markdown(insight, markdown, style="professional")
        if self.event_was_recalled(event, log_skip=True):
            return
        if await self._send_sight_note(
            event,
            markdown,
            source_event=event,
            source_message_id=message_id,
            status_text="[B站视频专业总结已发送]",
        ):
            logger.info(f"{LOG_PREFIX} B站视频自动总结已发送：{log_subject}")

    @staticmethod
    def _bili_summary_log_subject(metadata: dict[str, Any], target: BiliTarget) -> str:
        title = str((metadata or {}).get("title") or "").strip()
        author = str(
            (metadata or {}).get("author")
            or (metadata or {}).get("uploader")
            or (metadata or {}).get("owner_name")
            or ""
        ).strip()
        if title or author:
            return f"标题={title or '未知'}；作者={author or '未知'}"
        return target.bvid or target.canonical_url

    @staticmethod
    def _cached_sight_note_markdown(insight: SightInsight, *, style: str = "professional") -> str:
        metadata = dict(getattr(insight, "metadata", None) or {})
        notes = metadata.get("notes") if isinstance(metadata.get("notes"), dict) else {}
        return str(notes.get(str(style or "professional")) or "").strip()

    @classmethod
    def _sight_note_digest(cls, markdown: str, *, limit: int = 600) -> str:
        limit = max(80, int(limit or 600))
        values: list[str] = []
        in_fence = False
        for raw_line in str(markdown or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or line.startswith("![") or line.lower().startswith("<img"):
                continue
            line = cls._sight_note_digest_line(line)
            if line and line not in values:
                values.append(line)
            if len("；".join(values)) >= limit:
                break
        return cls._sight_compact_text("；".join(values), limit)

    @staticmethod
    def _sight_note_digest_line(line: str) -> str:
        text = str(line or "").strip()
        heading = 0
        while heading < len(text) and text[heading] == "#":
            heading += 1
        if heading == 1:
            return ""
        if heading:
            text = text[heading:].strip()
        while text and text[0] in {"-", "*", "+", ">"}:
            text = text[1:].strip()
        index = 0
        while index < len(text) and text[index].isdigit():
            index += 1
        if index and index < len(text) and text[index] in {".", "、", ")", "）"}:
            text = text[index + 1 :].strip()
        for token in ("**", "__", "`"):
            text = text.replace(token, "")
        return " ".join(text.split())[:180]

    @staticmethod
    def _sight_compact_text(value: object, limit: int) -> str:
        return " ".join(str(value or "").split())[: max(1, int(limit or 1))]

    async def _cache_sight_note_markdown(
        self,
        insight: SightInsight,
        markdown: str,
        *,
        style: str = "professional",
    ) -> SightInsight:
        markdown = str(markdown or "").strip()
        if not markdown:
            return insight
        metadata = dict(getattr(insight, "metadata", None) or {})
        notes = metadata.get("notes") if isinstance(metadata.get("notes"), dict) else {}
        style_key = str(style or "professional")
        notes[style_key] = markdown
        metadata["notes"] = notes
        if style_key == "professional":
            digest = self._sight_note_digest(markdown)
            if digest:
                metadata["professional_digest"] = digest
        insight.metadata = metadata
        return await self._sight_vault_for_runtime().upsert(insight)

    async def _send_bili_summary_failure(
        self,
        event: Any,
        detail: str,
        *,
        scope: str,
        message_id: str,
    ) -> bool:
        detail = str(detail or "").strip()
        message = f"B站视频自动总结失败：{detail}" if detail else "B站视频自动总结失败"
        if not scope:
            return False
        if not await self.send_message_if_not_recalled(
            scope,
            MessageChain().message(message),
            source_event=event,
            source_message_id=message_id,
        ):
            return False
        self.note_structured_bot_message(scope, message, source_event=event)
        return True

    async def life_video_understand(self, event: Any, target: str = "") -> str:
        clips = await self._sight_clips_from_event_async(event, target)
        if not clips:
            recent = await self._sight_recent_for_event(event, limit=1)
            if recent:
                return tool_result_text(recent[0])
            return "没有找到可理解的视频。请直接发送视频、引用视频，或提供视频文件/直链。"
        insight = await self._understand_sight_clip(event, clips[0])
        if insight and insight.summary:
            self._apply_sight_insight_to_structured(insight)
            return tool_result_text(insight)
        return "视频理解失败：没有拿到可确认的视频内容。"

    async def life_video_note(self, event: Any, target: str = "", style: str = "professional") -> Any:
        clips = await self._sight_clips_from_event_async(event, target)
        if clips:
            insight = await self._understand_sight_clip(event, clips[0])
        else:
            recent = await self._sight_recent_for_event(event, limit=1)
            insight = recent[0] if recent else None
        if not insight:
            return self._sight_plain_result(event, "没有找到可总结的视频。请直接发送视频、引用视频，或提供视频文件/直链。")
        if insight.status == "failed":
            detail = insight.error or insight.summary or "没有拿到可确认的视频内容"
            return self._sight_plain_result(event, f"视频总结失败：{detail}")
        try:
            markdown = await self._sight_note_for_runtime().compose(insight, style=style)
        except SightNoteError as exc:
            return self._sight_plain_result(event, f"视频总结失败：{str(exc) or '总结模型生成失败'}")
        insight = await self._cache_sight_note_markdown(insight, markdown, style=style)
        sent = await self._send_sight_note(event, markdown, source_event=event, status_text="[视频专业总结已发送]")
        return "视频专业总结已发送，无需复述正文。" if sent else self._sight_plain_result(event, markdown)

    async def _understand_sight_clip(
        self,
        event: Any,
        clip: SightClip,
        *,
        force: bool = False,
    ) -> SightInsight:
        clip = await self._resolve_sight_clip_source(event, clip)
        if not force:
            cached = await self._cached_sight_insight_for_clip(clip)
            if cached:
                return cached
        return await self._sight_flight_for_runtime().run(
            sight_flight_key(clip),
            lambda: self._understand_sight_clip_with_timeout(event, clip),
        )

    async def _understand_sight_clip_with_timeout(self, event: Any, clip: SightClip) -> SightInsight:
        try:
            return await self._understand_sight_clip_once(event, clip)
        except asyncio.TimeoutError:
            timeout = self._sight_total_timeout_seconds()
            logger.warning(f"{LOG_PREFIX} 视频理解超时：{timeout} 秒，尝试基于已准备内容继续总结。")
            resumed = await self._resume_sight_summary_after_timeout(event, clip)
            if resumed is not None:
                return resumed
            return SightInsight(
                clip=clip,
                summary="",
                status="failed",
                error="视频理解超时",
            )

    def _sight_total_timeout_seconds(self) -> int:
        settings = getattr(getattr(self, "config", None), "sight", None)
        value = getattr(settings, "total_timeout_seconds", DEFAULT_SIGHT_TOTAL_TIMEOUT_SECONDS)
        return max(60, min(int(value or DEFAULT_SIGHT_TOTAL_TIMEOUT_SECONDS), 1800))

    async def _cached_sight_insight_for_clip(self, clip: SightClip) -> SightInsight | None:
        cached = await self._sight_vault_for_runtime().get(clip.key)
        if cached and cached.status == "ready":
            return cached
        key = sight_flight_key(clip)
        for item in await self._sight_vault_for_runtime().recent(clip.scope, limit=20):
            if item.status == "ready" and sight_flight_key(item.clip) == key:
                return self._sight_insight_for_clip(item, clip)
        return None

    @staticmethod
    def _sight_insight_for_clip(insight: SightInsight, clip: SightClip) -> SightInsight:
        metadata = SightMixin._merged_sight_metadata(insight.metadata, clip.metadata)
        return SightInsight(
            clip=clip,
            summary=insight.summary,
            details=list(insight.details),
            frame_notes=list(insight.frame_notes),
            transcript=insight.transcript,
            transcript_source=insight.transcript_source,
            note=insight.note,
            note_source=insight.note_source,
            metadata=metadata,
            source_note=insight.source_note,
            status=insight.status,
            error=insight.error,
            updated_at=insight.updated_at,
        )

    @staticmethod
    def _merged_sight_metadata(base: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        result = dict(base or {})
        base_notes = result.get("notes") if isinstance(result.get("notes"), dict) else {}
        current_notes = current.get("notes") if isinstance((current or {}).get("notes"), dict) else {}
        for key, value in dict(current or {}).items():
            if key == "notes":
                continue
            if value not in ("", None, [], {}):
                result[key] = value
        notes = {**base_notes, **current_notes}
        if notes:
            result["notes"] = notes
        return result

    async def _understand_sight_clip_once(self, event: Any, clip: SightClip) -> SightInsight:
        prepared = await self._prepare_sight_clip_material(event, clip)
        timeout = self._sight_total_timeout_seconds()
        return await asyncio.wait_for(
            self._finalize_prepared_sight_clip(event, clip, **prepared),
            timeout=timeout,
        )

    async def _prepare_sight_clip_material(self, event: Any, clip: SightClip) -> dict[str, Any]:
        source_note = self._sight_source_note(clip)
        text_result = SightTextResult()
        metadata = dict(getattr(clip, "metadata", None) or {})
        error = ""
        audio_path: Path | None = None
        source_path: Path | None = None
        if clip.source:
            reader = self._sight_reader_for_runtime()
            try:
                audio_path = await reader.prepare_audio(clip.source)
                if audio_path:
                    logger.debug(f"{LOG_PREFIX} 视频音频下载完成")
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 视频音频准备跳过：{str(exc)[:160]}")
            try:
                sight_settings = getattr(self.config, "sight", None)
                max_video_mb = int(getattr(sight_settings, "video_download_max_mb", 500))
                download_timeout_seconds = int(getattr(sight_settings, "video_download_timeout_seconds", 240) or 240)
                source_path = await prepare_sample_video_source(
                    clip.source,
                    self._sight_cache_dir(),
                    max_video_mb=max_video_mb,
                    download_timeout_seconds=download_timeout_seconds,
                )
                if source_path is None:
                    error = "没有抽取到可用视频画面"
                else:
                    logger.debug(f"{LOG_PREFIX} 视频文件下载完成")
            except Exception as exc:
                error = str(exc)[:160]
                logger.debug(f"{LOG_PREFIX} 视频文件准备跳过：{error}")
            text_result = await reader.read_prepared_audio(event, clip, audio_path)
            metadata.update({key: value for key, value in dict(text_result.metadata or {}).items() if value not in ("", None)})
        else:
            error = "没有拿到视频文件或直链"
        if text_result.has_content:
            error = ""
        elif text_result.errors and not error:
            error = "?".join(text_result.errors[:2])
        await asyncio.to_thread(
            self._save_sight_prepare_cache,
            clip,
            source_note=source_note,
            source_path=str(source_path or ""),
            frame_notes=[],
            text_result=text_result,
            metadata=metadata,
            error=error,
        )
        return {
            "source_note": source_note,
            "text_result": text_result,
            "metadata": metadata,
            "error": error,
            "source_path": source_path,
        }

    async def _finalize_prepared_sight_clip(
        self,
        event: Any,
        clip: SightClip,
        *,
        source_note: str,
        text_result: SightTextResult,
        metadata: dict[str, Any],
        error: str,
        source_path: Path | None = None,
    ) -> SightInsight:
        frame_notes: list[str] = []
        frame_assets: list[dict[str, Any]] = []
        if source_path is not None:
            try:
                sight_settings = getattr(self.config, "sight", None)
                max_frames = int(getattr(sight_settings, "max_frames", 8) or 8)
                logger.debug(f"{LOG_PREFIX} 视频抽帧开始")
                frames = await extract_video_frames(source_path, self._sight_cache_dir(), max_frames=max_frames)
                logger.debug(f"{LOG_PREFIX} 视频抽帧完成")
                if frames:
                    logger.debug(f"{LOG_PREFIX} 视频画面理解开始")
                    frame_notes, frame_assets = await self._describe_sight_frames(clip, frames)
                    logger.debug(f"{LOG_PREFIX} 视频画面理解完成")
                    if not frame_notes and not text_result.has_content:
                        error = "视觉模型没有返回可确认的画面描述"
                elif not text_result.has_content:
                    error = "没有抽取到可用视频画面"
            except Exception as exc:
                if not text_result.has_content:
                    error = str(exc)[:160]
                logger.debug(f"{LOG_PREFIX} 视频抽帧理解跳过：{str(exc)[:160]}")
        if frame_assets:
            metadata["frames"] = frame_assets
        self._log_sight_fusion(text_result, frame_notes)
        await asyncio.to_thread(
            self._save_sight_prepare_cache,
            clip,
            source_note=source_note,
            source_path=str(source_path or ""),
            frame_notes=frame_notes,
            text_result=text_result,
            metadata=metadata,
            error=error,
        )
        return await self._finalize_sight_insight(
            event,
            clip,
            source_note=source_note,
            frame_notes=frame_notes,
            text_result=text_result,
            metadata=metadata,
            error=error,
        )

    async def _resume_sight_summary_after_timeout(self, event: Any, clip: SightClip) -> SightInsight | None:
        prepared = await asyncio.to_thread(self._load_sight_prepare_cache, clip)
        if prepared is None:
            return None
        logger.info(f"{LOG_PREFIX} 视频理解超时后检测到已准备内容，自动继续完成总结。")
        try:
            source_path = prepared.get("source_path")
            if source_path:
                return await self._finalize_prepared_sight_clip(
                    event,
                    clip,
                    source_note=str(prepared.get("source_note") or ""),
                    text_result=prepared["text_result"],
                    metadata=dict(prepared.get("metadata") or {}),
                    error=str(prepared.get("error") or ""),
                    source_path=Path(str(source_path)),
                )
            prepared = dict(prepared)
            prepared.pop("source_path", None)
            return await self._finalize_sight_insight(event, clip, **prepared)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 视频超时后的自动续跑失败：{exc}")
            return None

    async def _finalize_sight_insight(
        self,
        event: Any,
        clip: SightClip,
        *,
        source_note: str,
        frame_notes: list[str],
        text_result: SightTextResult,
        metadata: dict[str, Any],
        error: str,
    ) -> SightInsight:
        if not text_result.has_content and not frame_notes:
            insight = insight_from_notes(
                clip,
                frame_notes,
                transcript="",
                transcript_source="",
                note="",
                note_source="",
                note_details=[],
                metadata=metadata,
                source_note=source_note,
                error=error or "没有拿到可确认的视频内容",
            )
            if self.event_was_recalled(event, log_skip=True):
                return insight
            saved = await self._sight_vault_for_runtime().upsert(insight)
            await asyncio.to_thread(self._clear_sight_prepare_cache, clip)
            return saved
        note, note_details = await self._sight_brief_for_runtime().summarize(
            clip,
            transcript=text_result.transcript,
            frame_notes=frame_notes,
            metadata=metadata,
        )
        insight = insight_from_notes(
            clip,
            frame_notes,
            transcript=text_result.transcript,
            transcript_source=text_result.transcript_source,
            note=note or text_result.note,
            note_source="内置摘要" if note else text_result.note_source,
            note_details=note_details,
            metadata=metadata,
            source_note=source_note,
            error=error,
        )
        if self.event_was_recalled(event, log_skip=True):
            return insight
        saved = await self._sight_vault_for_runtime().upsert(insight)
        await asyncio.to_thread(self._clear_sight_prepare_cache, clip)
        return saved

    @staticmethod
    def _log_sight_fusion(text_result: SightTextResult, frame_notes: list[str]) -> None:
        text_source = text_result.transcript_source or text_result.note_source or "无文字"
        text_count = len(" ".join(str(text_result.transcript or "").split()))
        frame_source = f"时间线抽帧 {len(frame_notes)} 帧" if frame_notes else "无可用画面"
        logger.debug(f"{LOG_PREFIX} 视频理解融合：文字={text_source}（{text_count} 字）；画面={frame_source}")

    async def _describe_sight_frames(self, clip: SightClip, frames: list[Path | SightFrame]) -> tuple[list[str], list[dict[str, Any]]]:
        provider = await get_sight_provider(self, "frame_provider")
        if not provider:
            return [], []
        if not any(callable(getattr(provider, name, None)) for name in ("text_chat", "image_chat", "vision_chat")):
            return [], []
        notes: list[str] = []
        assets: list[dict[str, Any]] = []
        total = len(frames)
        session_id = f"daily_life_video_sight_{uuid.uuid4().hex[:8]}"
        try:
            for index, frame in enumerate(frames, start=1):
                frame_path, label, second = self._sight_frame_parts(frame)
                result = await self._call_sight_vision_provider(
                    provider,
                    frame_prompt(index, total, clip, label),
                    str(frame_path),
                    session_id,
                )
                note = frame_note_from_text(self._completion_text(result))
                if note:
                    notes.append(f"{label}：{note}" if label else note)
                if frame_path.is_file():
                    asset: dict[str, Any] = {
                        "path": str(frame_path),
                        "label": label,
                        "second": float(second or 0.0),
                    }
                    if note:
                        asset["note"] = note
                    assets.append(asset)
        finally:
            cleanup = getattr(self.composer, "_cleanup_conversation", None)
            if callable(cleanup):
                await cleanup(session_id)
        return notes, assets

    @staticmethod
    def _sight_frame_parts(frame: Path | SightFrame) -> tuple[Path, str, float]:
        if isinstance(frame, SightFrame):
            return frame.path, frame.label, float(frame.second or 0.0)
        return Path(frame), "", 0.0

    @staticmethod
    async def _call_sight_vision_provider(provider: Any, prompt: str, image: str, session_id: str) -> Any:
        for name, kwargs in (
            ("text_chat", {"prompt": prompt, "image_urls": [image], "session_id": session_id}),
            ("image_chat", {"prompt": prompt, "image": image, "session_id": session_id}),
            ("vision_chat", {"prompt": prompt, "image": image, "session_id": session_id}),
        ):
            method = getattr(provider, name, None)
            if not callable(method):
                continue
            try:
                result = method(**kwargs)
            except (TypeError, NotImplementedError, AttributeError):
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except (TypeError, NotImplementedError, AttributeError):
                continue
            return result
        return None

    async def _sight_video_clips_from_component(
        self,
        item: Any,
        *,
        scope: str,
        message_id: str,
        origin: str,
        text: str = "",
        depth: int = 0,
    ) -> list[SightClip]:
        if item is None or depth > 4:
            return []
        clips: list[SightClip] = []
        kind = self._event_component_kind(item)
        if "video" in kind:
            clip = await self._sight_video_clip_from_component(item, scope=scope, message_id=message_id, origin=origin, text=text)
            if clip:
                clips.append(clip)
        for nested in self._sight_nested_components(item):
            clips.extend(
                await self._sight_video_clips_from_component(
                    nested,
                    scope=scope,
                    message_id=message_id,
                    origin=origin,
                    text=text,
                    depth=depth + 1,
                )
            )
        return dedupe_clips(clips)

    async def _sight_video_clip_from_component(
        self,
        item: Any,
        *,
        scope: str,
        message_id: str,
        origin: str,
        text: str = "",
    ) -> SightClip | None:
        source = source_from_value(item)
        converter = getattr(item, "convert_to_file_path", None)
        if callable(converter):
            try:
                resolved = converter()
                if hasattr(resolved, "__await__"):
                    resolved = await resolved
                source = str(resolved or "").strip() or source
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 引用视频本地解析失败：{str(exc)[:160]}")
        name = str(getattr(item, "name", "") or getattr(item, "file_name", "") or getattr(item, "filename", "") or "").strip()
        file_id = str(getattr(item, "file_id", "") or getattr(item, "fileid", "") or "").strip()
        if not source and not file_id:
            return None
        return SightClip(
            scope=scope,
            message_id=message_id,
            source=source,
            file_id=file_id,
            name=name,
            origin=origin,
            text=text,
        )

    @staticmethod
    def _sight_nested_components(item: Any) -> list[Any]:
        values: list[Any] = []
        if isinstance(item, dict):
            data = item.get("data")
            for key in ("chain", "message", "messages", "items", "segments", "components", "nodes"):
                nested = item.get(key)
                if isinstance(nested, list):
                    values.extend(nested)
            if isinstance(data, dict):
                for key in ("chain", "message", "messages", "items", "segments", "components", "nodes"):
                    nested = data.get(key)
                    if isinstance(nested, list):
                        values.extend(nested)
            return values
        for key in ("chain", "message", "messages", "items", "segments", "components", "nodes"):
            nested = getattr(item, key, None)
            if isinstance(nested, list):
                values.extend(nested)
        data = getattr(item, "data", None)
        if isinstance(data, dict):
            for key in ("chain", "message", "messages", "items", "segments", "components", "nodes"):
                nested = data.get(key)
                if isinstance(nested, list):
                    values.extend(nested)
        return values

    async def _resolve_sight_clip_source(self, event: Any, clip: SightClip) -> SightClip:
        if clip.source or not clip.file_id:
            return clip
        source = await self._resolve_sight_file_source(event, clip.file_id)
        if source:
            clip.source = source
        return clip

    async def _resolve_sight_file_source(self, event: Any, file_id: str) -> str:
        bot = None
        for source in self._event_sources(event):
            bot = getattr(source, "bot", None) or bot
        call_action = getattr(bot, "call_action", None)
        if not callable(call_action):
            return ""
        group_id, _ = self._event_group_meta(event)
        actions: list[tuple[str, dict[str, Any]]] = [
            ("get_file", {"file_id": file_id}),
            ("get_private_file_url", {"file_id": file_id}),
        ]
        if group_id:
            actions.append(("get_group_file_url", {"group_id": group_id, "file_id": file_id}))
            if str(group_id).isdigit():
                actions.append(("get_group_file_url", {"group_id": int(group_id), "file_id": file_id}))
        for action, params in actions:
            try:
                payload = await call_action(action, **params)
            except Exception:
                continue
            source = source_from_value(payload)
            if source:
                return source
        return ""

    @staticmethod
    def _sight_source_note(clip: SightClip) -> str:
        parts = []
        if clip.name:
            parts.append(f"文件名：{clip.name}")
        if clip.source:
            parts.append(f"来源：{clip.source[:160]}")
        elif clip.file_id:
            parts.append(f"文件ID：{clip.file_id}")
        if clip.text:
            parts.append(f"随视频文字：{clip.text[:120]}")
        if not parts:
            return ""
        return "；".join(parts)

    def _apply_sight_insight_to_structured(self, insight: SightInsight) -> None:
        if insight.status != "ready":
            return
        updater = getattr(self, "update_structured_message_video_summary", None)
        if callable(updater):
            updater(insight.scope, insight.message_id, insight.summary)

    @staticmethod
    def _sight_plain_result(event: Any, text: str) -> Any:
        maker = getattr(event, "plain_result", None)
        if callable(maker):
            return maker(str(text or ""))
        return str(text or "")

    async def _send_sight_note(
        self,
        event: Any,
        markdown: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
        status_text: str = "[视频总结已发送]",
    ) -> bool:
        scope = self._event_session_id(event)
        markdown = str(markdown or "").strip()
        if not scope or not markdown:
            return False
        chain = await self._sight_note_chain(scope, markdown)
        if not await self.send_message_if_not_recalled(
            scope,
            chain,
            source_event=source_event or event,
            source_message_id=source_message_id,
        ):
            return False
        self._mark_sight_note_sent(event)
        self.note_structured_bot_message(scope, status_text, source_event=source_event or event, media="图片")
        return True

    @staticmethod
    def _mark_sight_note_sent(event: Any) -> None:
        if event is None:
            return
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            setter("daily_life_sight_note_sent", True)
            return
        setattr(event, "_daily_life_sight_note_sent", True)

    @staticmethod
    def _sight_note_was_sent(event: Any) -> bool:
        if event is None:
            return False
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                return bool(getter("daily_life_sight_note_sent"))
            except Exception:
                return False
        return bool(getattr(event, "_daily_life_sight_note_sent", False))

    def suppress_sight_note_followup(self, event: Any) -> bool:
        if not self._sight_note_was_sent(event):
            return False
        if not self._voice_switch_reply_text_from_event(event):
            return False
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        else:
            result = getattr(event, "get_result", lambda: None)()
            chain = getattr(result, "chain", None)
            if isinstance(chain, list):
                chain.clear()
        logger.debug(f"{LOG_PREFIX} 视频专业总结已直接发送，已隐藏重复收尾回复。")
        return True

    async def _sight_note_chain(self, scope: str, markdown: str) -> MessageChain:
        markdown = str(markdown or "").strip()
        if not markdown:
            return MessageChain()
        try:
            logger.debug(f"{LOG_PREFIX} 视频专业总结渲染开始")
            image = await self._render_sight_note_image(scope, markdown)
            if image:
                logger.debug(f"{LOG_PREFIX} 视频专业总结渲染完成")
                chain = MessageChain()
                method = getattr(chain, "url_image", None) if image.startswith(("http://", "https://")) else getattr(chain, "file_image", None)
                if callable(method):
                    return method(image)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 视频总结文转图失败，改用文字发送：{exc}")
        return MessageChain().message(markdown)

    async def _render_sight_note_image(self, scope: str, markdown: str) -> str:
        from astrbot.core import html_renderer

        config = self._astrbot_config(scope)
        prepared = embed_local_markdown_images("\n\n" + str(markdown or "").strip())
        use_network = str(config.get("t2i_strategy") or "remote") == "remote" or "data:image/" in prepared
        return str(
            await html_renderer.render_t2i(
                prepared,
                return_url=True,
                use_network=use_network,
                template_name=str(config.get("t2i_active_template") or "base"),
            )
            or ""
        ).strip()

    async def _sight_recent_for_event(self, event: Any, *, limit: int = 2) -> list[SightInsight]:
        scope = self._event_session_id(event)
        if not scope:
            return []
        return await self._sight_vault_for_runtime().recent(scope, limit=limit)

    async def format_recent_sight_context(self, event: Any, *, limit: int = 2) -> str:
        insights = await self._sight_recent_for_event(event, limit=limit)
        if not insights:
            return ""
        lines = [
            "<recent_video_understanding>",
            f"  <note>近期真实视频理解结果；回答视频内容时优先参考，不足处说明不确定。{VIDEO_ANSWER_BOUNDARY_RULE}</note>",
        ]
        for item in insights:
            if item.status != "ready":
                continue
            metadata = dict(getattr(item, "metadata", None) or {})
            summary = html.escape(self._sight_compact_text(item.summary, 220))
            details = html.escape("；".join(content_details(item.details, limit=3)))
            professional = html.escape(self._sight_compact_text(metadata.get("professional_digest"), 600))
            note = html.escape(self._sight_compact_text(item.note, 600))
            attrs = [
                f'status="{html.escape(item.status, quote=True)}"',
                f'origin="{html.escape(item.clip.origin, quote=True)}"',
            ]
            if item.message_id:
                attrs.append(f'message_id="{html.escape(item.message_id, quote=True)}"')
            parts = [summary] if summary else []
            if details and details != summary:
                parts.append(f"细节：{details}")
            reference = professional or note
            if reference and reference not in "；".join(parts):
                parts.append(f"{'专业总结' if professional else '笔记'}：{reference}")
            body = "；".join(parts)
            if not body:
                continue
            lines.append(f"  <video {' '.join(attrs)}>{body}</video>")
        if len(lines) == 2:
            return ""
        lines.append("</recent_video_understanding>")
        return "\n".join(lines)

    def _append_video_input_anchor(self, req: Any, event: Any = None) -> None:
        if event is None or not self.event_has_sight_video(event):
            return
        parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(parts, list):
            parts = []
            setattr(req, "extra_user_content_parts", parts)
        text = (
            "[HiddenVideoInputRule] 本轮消息包含真实视频附件、视频文件或引用视频。"
            "回答视频内容相关问题时，必须基于近期视频理解或调用 life_video_understand；"
            "如果还没有完成理解，不要凭日常背景、穿搭或文字描述猜测视频画面；"
            "需要调用工具时直接调用，不要先输出占位说明。"
            f"{VIDEO_ANSWER_BOUNDARY_RULE}"
        )
        for part in parts:
            current = str(getattr(part, "text", "") or (part.get("text", "") if isinstance(part, dict) else ""))
            if current == text:
                return
        part = TextPart(text=text)
        marker = getattr(part, "mark_as_temp", None)
        if callable(marker):
            part = marker()
        parts.append(part)
