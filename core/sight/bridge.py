import asyncio
import contextlib
import hashlib
import html
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Plain
from astrbot.core.agent.message import TextPart

from ..runtime.delivery import BackgroundTextMode
from ..runtime.markers import LOG_PREFIX
from .bili import BiliTarget, fetch_bili_metadata, find_bili_target, resolve_bili_target
from .brief import SightBrief
from .clip import SightClip, SightInsight
from .cookie import BiliCookieJar
from .digest import (
    VIDEO_ANSWER_BOUNDARY_RULE,
    ToolResultText,
    batch_frame_notes_from_text,
    batch_frame_prompt,
    content_details,
    frame_note_from_text,
    frame_prompt,
    insight_from_notes,
    tool_result_text,
)
from .embed import embed_local_markdown_images
from .flight import (
    SightFlight,
    sight_flight_key,
    sight_resource_keys,
    sight_resource_matches,
)
from .identity import SightIdentityMixin
from .login import BiliLoginService, BiliLoginStatus
from .note import (
    PROFESSIONAL_NOTE_CACHE_SCHEMA,
    SightNote,
    SightNoteError,
    professional_note_prompt_key,
    professional_note_unavailable_reason,
)
from .probe import (
    clips_from_items,
    clips_from_text_links,
    clips_from_value,
    dedupe_clips,
    explicit_clip,
    payload_from_item,
    source_from_value,
)
from .provider import get_sight_provider
from .prune import SightCleanupMixin
from .reader import (
    AUDIO_TRANSCRIPT_TIMEOUT_SECONDS,
    LOCAL_ASR_BATCH_SIZE_SECONDS,
    TRANSCRIPT_CAPTURE_CHARS,
    SightReader,
    SightTextResult,
)
from .sample import (
    SightFrame,
    extract_video_frames,
    prepare_sample_video_source,
    sight_cache_dir,
)
from .vault import SightVault

BILI_RESOLVE_TIMEOUT_SECONDS = 10
DEFAULT_SIGHT_TOTAL_TIMEOUT_SECONDS = 300


@dataclass(slots=True)
class SightFrameDescriptionResult:
    notes: list[str] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    request_count: int = 0
    mode: str = "none"

    def __iter__(self):
        yield self.notes
        yield self.assets


def _sight_file_content_fingerprint(path: str) -> str:
    target = Path(str(path or "").strip())
    if not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SightMixin(SightCleanupMixin, SightIdentityMixin):
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
        self._init_sight_identity()

    def _sight_vault_for_runtime(self) -> SightVault:
        vault = getattr(self, "_sight_vault", None)
        if (
            isinstance(vault, SightVault)
            and getattr(vault, "ttl_seconds", None) == self._sight_cache_ttl_seconds()
            and getattr(vault, "max_items", None) == self._sight_cache_max_items()
        ):
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
        if (
            isinstance(reader, SightReader)
            and reader.settings_signature == self._sight_reader_signature()
        ):
            return reader
        reader = SightReader(self)
        self._sight_reader = reader
        return reader

    def _sight_reader_signature(self) -> tuple[object, ...]:
        settings = getattr(getattr(self, "config", None), "sight", None)
        max_chars = max(
            TRANSCRIPT_CAPTURE_CHARS,
            int(getattr(settings, "max_transcript_chars", 8000) or 8000),
            int(getattr(settings, "note_max_transcript_chars", 20000) or 20000),
        )
        mode = (
            str(getattr(settings, "audio_transcript_mode", "local") or "local")
            .strip()
            .lower()
        )
        return (
            max_chars,
            mode,
            AUDIO_TRANSCRIPT_TIMEOUT_SECONDS,
            LOCAL_ASR_BATCH_SIZE_SECONDS,
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

    async def _compose_sight_note_with_timeout(
        self, insight: SightInsight, *, style: str = "professional"
    ) -> str:
        metrics = dict(getattr(insight, "metadata", {}).get("sight_metrics") or {})
        evidence_seconds = 0.0
        for key in ("frame_extract_seconds", "vision_seconds", "brief_seconds"):
            try:
                evidence_seconds += max(0.0, float(metrics.get(key) or 0.0))
            except (TypeError, ValueError):
                continue
        remaining = max(1.0, self._sight_finalize_timeout_seconds() - evidence_seconds)
        try:
            return await asyncio.wait_for(
                self._sight_note_for_runtime().compose(insight, style=style),
                timeout=remaining,
            )
        except asyncio.TimeoutError as exc:
            raise SightNoteError(
                f"专业总结超过视频理解总时间限制（{self._sight_total_timeout_seconds():.0f} 秒）"
            ) from exc

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
            "frame_notes": [
                str(item or "").strip()
                for item in frame_notes
                if str(item or "").strip()
            ],
            "metadata": dict(metadata or {}),
            "error": str(error or ""),
            "text_result": {
                "transcript": str(text_result.transcript or ""),
                "transcript_source": str(text_result.transcript_source or ""),
                "note": str(text_result.note or ""),
                "note_source": str(text_result.note_source or ""),
                "metadata": dict(text_result.metadata or {}),
                "errors": [
                    str(item or "")
                    for item in list(text_result.errors or [])
                    if str(item or "").strip()
                ],
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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
        text_payload = (
            payload.get("text_result")
            if isinstance(payload.get("text_result"), dict)
            else {}
        )
        return {
            "source_note": str(payload.get("source_note") or ""),
            "source_path": str(payload.get("source_path") or ""),
            "frame_notes": [
                str(item or "").strip()
                for item in list(payload.get("frame_notes") or [])
                if str(item or "").strip()
            ],
            "metadata": dict(payload.get("metadata") or {})
            if isinstance(payload.get("metadata"), dict)
            else {},
            "error": str(payload.get("error") or ""),
            "text_result": SightTextResult(
                transcript=str(text_payload.get("transcript") or ""),
                transcript_source=str(text_payload.get("transcript_source") or ""),
                note=str(text_payload.get("note") or ""),
                note_source=str(text_payload.get("note_source") or ""),
                metadata=dict(text_payload.get("metadata") or {})
                if isinstance(text_payload.get("metadata"), dict)
                else {},
                errors=[
                    str(item or "")
                    for item in list(text_payload.get("errors") or [])
                    if str(item or "").strip()
                ],
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
        jar = await asyncio.to_thread(self._bili_cookie_jar_for_runtime)
        if jar.is_logged_in():
            yield self._sight_plain_result(
                event, "B站已登录，如需重新登录请先使用 /B站登出"
            )
            return
        login = BiliLoginService()
        qr = await login.generate()
        if not qr:
            yield self._sight_plain_result(event, "B站登录二维码生成失败，请稍后再试。")
            return
        qr_path = self._sight_cache_dir() / f"bili_login_{uuid.uuid4().hex[:8]}.png"
        try:
            import segno

            await asyncio.to_thread(
                lambda: segno.make(qr.url).save(str(qr_path), scale=10, border=4)
            )
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
        if (
            result.status == BiliLoginStatus.SUCCESS
            and result.cookies
            and await asyncio.to_thread(jar.save, result.cookies)
        ):
            yield self._sight_plain_result(
                event, "B站登录成功，后续 B站视频总结会自动使用登录态。"
            )
            return
        if result.status == BiliLoginStatus.EXPIRED:
            yield self._sight_plain_result(
                event, "B站登录二维码已过期，请重新使用 /B站登录。"
            )
            return
        if result.status == BiliLoginStatus.TIMEOUT:
            yield self._sight_plain_result(event, "B站登录超时，请重新使用 /B站登录。")
            return
        yield self._sight_plain_result(event, "B站登录失败，请重新使用 /B站登录。")

    async def bili_logout(self, event: Any) -> Any:
        if self._event_is_group_message(event):
            return self._sight_plain_result(event, "B站登录请在私聊里使用。")
        jar = await asyncio.to_thread(self._bili_cookie_jar_for_runtime)
        if not jar.is_logged_in():
            return self._sight_plain_result(event, "当前未登录 B站。")
        await asyncio.to_thread(jar.clear)
        return self._sight_plain_result(event, "已退出 B站登录。")

    async def bili_status(self, event: Any) -> Any:
        if self._event_is_group_message(event):
            return self._sight_plain_result(event, "B站登录请在私聊里使用。")
        jar = await asyncio.to_thread(self._bili_cookie_jar_for_runtime)
        status = "已登录" if jar.is_logged_in() else "未登录"
        return self._sight_plain_result(event, f"B站登录状态：{status}")

    def _sight_clips_from_event(
        self, event: Any, explicit: str = ""
    ) -> list[SightClip]:
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event) or f"event:{id(event)}"
        text = str(getattr(event, "message_str", "") or "").strip()
        clips: list[SightClip] = []
        if explicit:
            clip = explicit_clip(
                explicit, scope=scope, message_id=message_id, text=text
            )
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
        clips = self._sight_enrich_clips_from_raw_message(event, clips)
        return dedupe_clips(self._sight_apply_group_upload_id(event, clips))

    async def _sight_clips_from_event_async(
        self, event: Any, explicit: str = ""
    ) -> list[SightClip]:
        clips = list(self._sight_clips_from_event(event, explicit))
        if explicit:
            return dedupe_clips(clips)
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event) or f"event:{id(event)}"
        text = str(getattr(event, "message_str", "") or "").strip()
        current_clips: list[SightClip] = []
        for item in self._event_message_items(event):
            kind = self._event_component_kind(item)
            if "reply" in kind or "quote" in kind:
                continue
            current_clips.extend(
                await self._sight_video_clips_from_component(
                    item,
                    scope=scope,
                    message_id=message_id,
                    origin="current",
                    text=text,
                )
            )
        if current_clips:
            clips = [clip for clip in clips if clip.origin != "current"]
        quote_clips = await self._sight_quote_video_clips_from_event(
            event, scope, message_id, text
        )
        if quote_clips:
            clips = [
                clip
                for clip in clips
                if clip.origin != "quote"
                or self._sight_clip_source_is_usable(clip.source)
            ]
        combined = self._sight_enrich_clips_from_raw_message(
            event, [*current_clips, *quote_clips, *clips]
        )
        return dedupe_clips(self._sight_apply_group_upload_id(event, combined))

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
            raw = getattr(
                getattr(source, "message_obj", None), "raw_message", None
            ) or getattr(source, "raw_message", None)
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
                clips.extend(
                    await self._sight_video_clips_from_component(
                        item,
                        scope=scope,
                        message_id=message_id,
                        origin="quote",
                        text=text,
                    )
                )
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
        if text.lower().startswith(
            ("http://", "https://", "file://", "base64://", "data:")
        ):
            return True
        try:
            if Path(text).expanduser().exists():
                return True
        except OSError:
            return False
        return Path(text).suffix.lower() in {
            ".mp4",
            ".m4v",
            ".mov",
            ".mkv",
            ".webm",
            ".avi",
            ".flv",
            ".ts",
        }

    def event_has_sight_video(self, event: Any) -> bool:
        return bool(event is not None and self._sight_clips_from_event(event))

    def schedule_video_context_from_event(self, event: Any) -> bool:
        if (
            event is None
            or self._event_has_command_handler(event)
            or self.event_was_recalled(event, log_skip=True)
        ):
            return False
        clips = self._sight_clips_from_event(event)
        if not clips:
            return False
        scope = self._event_session_id(event)
        if not scope:
            return False
        clip_identity = clips[0].key
        if self._sight_completed_key_is_fresh(clip_identity):
            logger.debug(f"{LOG_PREFIX} 视频理解跳过：缓存已完成")
            return False
        return self._schedule_background_task(
            self._collect_sight_context_background(event),
            label="视频上下文理解",
            key=f"sight:{scope}:{clip_identity}",
        )

    def schedule_bili_summary_from_event(self, event: Any) -> bool:
        if (
            event is None
            or self._event_has_command_handler(event)
            or self.event_was_recalled(event, log_skip=True)
        ):
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
                self._mark_sight_completed_key(clip.key)
                self._apply_sight_insight_to_structured(insight)

    async def _send_bili_summary_background(
        self, event: Any, target: BiliTarget
    ) -> None:
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
            await self._send_bili_summary_failure(
                event, detail, scope=scope, message_id=message_id
            )
            return
        jar = await asyncio.to_thread(self._bili_cookie_jar_for_runtime)
        metadata = await fetch_bili_metadata(
            resolved,
            timeout_seconds=BILI_RESOLVE_TIMEOUT_SECONDS,
            cookies=jar.get(),
        )
        if not metadata:
            logger.debug(
                f"{LOG_PREFIX} B站视频元数据未获取：{resolved.bvid or resolved.canonical_url}"
            )
        metadata_dict = (
            metadata.as_dict()
            if metadata
            else {
                "platform": "bilibili",
                "bvid": resolved.bvid,
                "url": resolved.canonical_url,
            }
        )
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
        insight = await self._understand_sight_clip(event, clip, purpose="professional")
        if self.event_was_recalled(event, log_skip=True):
            return
        if not insight or insight.status == "failed":
            detail = (
                getattr(insight, "error", "")
                or getattr(insight, "summary", "")
                or "没有拿到可确认的视频内容"
            )
            logger.warning(f"{LOG_PREFIX} B站视频自动总结失败：{detail}")
            await self._send_bili_summary_failure(
                event, detail, scope=scope, message_id=message_id
            )
            return
        insight = self._sight_insight_for_clip(insight, clip)
        note_unavailable = professional_note_unavailable_reason(
            insight, style="professional"
        )
        if note_unavailable:
            logger.warning(f"{LOG_PREFIX} B站视频自动总结失败：{note_unavailable}")
            return
        markdown = self._cached_sight_note_markdown(insight, style="professional")
        note_cache_hit = bool(markdown)
        try:
            if not markdown:
                markdown = await self._compose_sight_note_with_timeout(
                    insight, style="professional"
                )
            markdown = self._sight_note_for_runtime().normalize(
                insight,
                markdown,
                include_frames=False,
            )
        except SightNoteError as exc:
            detail = str(exc) or "总结模型生成失败"
            logger.warning(f"{LOG_PREFIX} B站视频自动总结失败：{detail}")
            await self._send_bili_summary_failure(
                event, detail, scope=scope, message_id=message_id
            )
            return
        insight = await self._cache_sight_note_markdown(
            insight, markdown, style="professional"
        )
        if self.event_was_recalled(event, log_skip=True):
            return
        delivery_started = time.monotonic()
        sent = await self._send_sight_note(
            event,
            markdown,
            source_event=event,
            source_message_id=message_id,
            status_text="[B站视频专业总结已发送]",
        )
        self._record_sight_delivery_metrics(insight, delivery_started)
        self._log_sight_professional_metrics(insight, cache_hit=note_cache_hit)
        if sent:
            logger.debug(f"{LOG_PREFIX} B站视频自动总结已发送：{log_subject}")

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
    def _cached_sight_note_markdown(
        insight: SightInsight, *, style: str = "professional"
    ) -> str:
        metadata = dict(getattr(insight, "metadata", None) or {})
        if (
            str(style or "professional") == "professional"
            and metadata.get("professional_note_schema")
            != PROFESSIONAL_NOTE_CACHE_SCHEMA
        ):
            return ""
        if str(style or "professional") == "professional" and metadata.get(
            "professional_note_prompt_key"
        ) != professional_note_prompt_key(style):
            return ""
        notes = metadata.get("notes") if isinstance(metadata.get("notes"), dict) else {}
        return str(notes.get(str(style or "professional")) or "").strip()

    @staticmethod
    def _record_sight_delivery_metrics(
        insight: SightInsight, started_at: float
    ) -> None:
        metadata = dict(getattr(insight, "metadata", None) or {})
        metrics = dict(metadata.get("sight_metrics") or {})
        metrics["render_send_seconds"] = round(
            max(0.0, time.monotonic() - started_at), 3
        )
        metadata["sight_metrics"] = metrics
        insight.metadata = metadata

    @staticmethod
    def _log_sight_professional_metrics(
        insight: SightInsight, *, cache_hit: bool = False
    ) -> None:
        metrics = dict(getattr(insight, "metadata", {}).get("sight_metrics") or {})

        def seconds(name: str) -> float:
            try:
                return max(0.0, float(metrics.get(name) or 0.0))
            except (TypeError, ValueError):
                return 0.0

        material = seconds("material_seconds")
        extract = seconds("frame_extract_seconds")
        vision = seconds("vision_seconds")
        note = seconds("professional_note_seconds")
        delivery = seconds("render_send_seconds")
        total = material + extract + vision + note + delivery
        transcript_source = str(getattr(insight, "transcript_source", "") or "仅画面")
        if transcript_source == "本地ASR":
            transcript_source = "本地语音识别"
        vision_mode = {
            "single": "单图",
            "multi_image": "多图",
            "fallback_parallel": "单帧并发",
        }.get(str(metrics.get("vision_mode") or ""), "未使用")
        logger.info(
            f"{LOG_PREFIX} 视频专业总结完成：缓存={'命中' if cache_hit else '未命中'}；"
            f"文字={transcript_source}；"
            f"素材={material:.2f} 秒；抽帧={extract:.2f} 秒；"
            f"关键画面={int(metrics.get('key_frames') or 0)}；"
            f"视觉={vision:.2f} 秒/{int(metrics.get('vision_requests') or 0)}次/"
            f"{vision_mode}；"
            f"总结={note:.2f} 秒/{int(metrics.get('professional_model_calls') or 0)}次；"
            f"分段={int(metrics.get('professional_chunk_count') or 1)}；"
            f"章节={int(metrics.get('professional_section_count') or 0)}；"
            f"输入={int(metrics.get('professional_input_chars') or 0)}字；"
            f"输出={int(metrics.get('professional_output_chars') or 0)}字；"
            f"渲染发送={delivery:.2f} 秒；总耗时={total:.2f} 秒"
        )

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
            metadata["professional_note_schema"] = PROFESSIONAL_NOTE_CACHE_SCHEMA
            metadata["professional_note_prompt_key"] = professional_note_prompt_key(
                style_key
            )
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
        return await self.send_background_text(
            scope,
            message,
            mode=BackgroundTextMode.DIRECT,
            source_event=event,
            source_message_id=message_id,
            source="sight_failure",
        )

    async def life_video_understand(self, event: Any, target: str = "") -> str:
        clips = await self._sight_clips_from_event_async(event, target)
        if not clips:
            recent = await self._sight_recent_for_event(event, limit=1)
            if recent:
                return tool_result_text(recent[0])
            return (
                "没有找到可理解的视频。请直接发送视频、引用视频，或提供视频文件/直链。"
            )
        insight = await self._understand_sight_clip(event, clips[0])
        if insight and insight.summary:
            self._apply_sight_insight_to_structured(insight)
            return tool_result_text(insight)
        return "视频理解失败：没有拿到可确认的视频内容。"

    async def life_video_note(
        self, event: Any, target: str = "", style: str = "professional"
    ) -> Any:
        clips = await self._sight_clips_from_event_async(event, target)
        if clips:
            insight = await self._understand_sight_clip(
                event, clips[0], purpose="professional"
            )
        else:
            recent = await self._sight_recent_for_event(event, limit=1)
            insight = recent[0] if recent else None
        if not insight:
            return self._sight_plain_result(
                event,
                "没有找到可总结的视频。请直接发送视频、引用视频，或提供视频文件/直链。",
            )
        if insight.status == "failed":
            detail = insight.error or insight.summary or "没有拿到可确认的视频内容"
            return self._sight_plain_result(event, f"视频总结失败：{detail}")
        note_unavailable = professional_note_unavailable_reason(insight, style=style)
        if note_unavailable:
            return self._sight_plain_result(event, f"视频总结失败：{note_unavailable}")
        try:
            markdown = await self._compose_sight_note_with_timeout(insight, style=style)
        except SightNoteError as exc:
            return self._sight_plain_result(
                event, f"视频总结失败：{str(exc) or '总结模型生成失败'}"
            )
        insight = await self._cache_sight_note_markdown(insight, markdown, style=style)
        delivery_started = time.monotonic()
        sent = await self._send_sight_note(
            event, markdown, source_event=event, status_text="[视频专业总结已发送]"
        )
        self._record_sight_delivery_metrics(insight, delivery_started)
        self._log_sight_professional_metrics(insight)
        return (
            ToolResultText(
                "视频专业总结已发送，无需复述正文。",
                status="sent",
                media="video_note",
            )
            if sent
            else self._sight_plain_result(event, markdown)
        )

    async def _understand_sight_clip(
        self,
        event: Any,
        clip: SightClip,
        *,
        force: bool = False,
        purpose: str = "chat",
    ) -> SightInsight:
        purpose = "professional" if purpose == "professional" else "chat"
        clip = await self._resolve_sight_clip_source(event, clip)
        if not force:
            cached = await self._cached_sight_insight_for_clip(clip)
            if cached:
                metrics = dict(cached.metadata.get("sight_metrics") or {})
                metrics["cache_hit"] = True
                cached.metadata["sight_metrics"] = metrics
                if purpose == "chat" and cached.metadata.get("brief_ready") is False:
                    return await self._complete_cached_sight_brief(event, cached)
                return cached
        return await self._sight_flight_for_runtime().run(
            f"{sight_flight_key(clip)}:{purpose}",
            lambda: self._understand_sight_clip_with_timeout(
                event, clip, purpose=purpose
            ),
        )

    async def _understand_sight_clip_with_timeout(
        self, event: Any, clip: SightClip, *, purpose: str = "chat"
    ) -> SightInsight:
        try:
            return await self._understand_sight_clip_once(event, clip, purpose=purpose)
        except asyncio.TimeoutError:
            timeout = self._sight_finalize_timeout_seconds()
            logger.warning(
                f"{LOG_PREFIX} 视频理解超时：{timeout} 秒，尝试基于已准备内容继续总结。"
            )
            resumed = await self._resume_sight_summary_after_timeout(
                event, clip, purpose=purpose
            )
            if resumed is not None:
                return resumed
            return SightInsight(
                clip=clip,
                summary="",
                status="failed",
                error="视频理解超时",
            )

    async def _complete_cached_sight_brief(
        self, event: Any, insight: SightInsight
    ) -> SightInsight:
        return await self._finalize_sight_insight(
            event,
            insight.clip,
            source_note=insight.source_note,
            frame_notes=list(insight.frame_notes),
            text_result=SightTextResult(
                transcript=insight.transcript,
                transcript_source=insight.transcript_source,
                note=insight.note,
                note_source=insight.note_source,
                metadata=dict(insight.metadata),
            ),
            metadata=dict(insight.metadata),
            error=insight.error,
            purpose="chat",
        )

    def _sight_total_timeout_seconds(self) -> float:
        settings = getattr(getattr(self, "config", None), "sight", None)
        value = getattr(
            settings, "total_timeout_seconds", DEFAULT_SIGHT_TOTAL_TIMEOUT_SECONDS
        )
        return max(60, min(int(value or DEFAULT_SIGHT_TOTAL_TIMEOUT_SECONDS), 1800))

    def _sight_finalize_timeout_seconds(self) -> float:
        return max(float(self._sight_total_timeout_seconds()), 0.05)

    async def _cached_sight_insight_for_clip(
        self, clip: SightClip
    ) -> SightInsight | None:
        cached = await self._sight_vault_for_runtime().get(clip.key)
        if cached and cached.status == "ready":
            return cached
        keys = set(sight_resource_keys(clip))
        if not keys:
            return None
        limit = self._sight_cache_max_items()
        for item in await self._sight_vault_for_runtime().recent(
            clip.scope, limit=limit
        ):
            if item.status == "ready" and sight_resource_matches(clip, item.clip):
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
    def _merged_sight_metadata(
        base: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(base or {})
        base_notes = (
            result.get("notes") if isinstance(result.get("notes"), dict) else {}
        )
        current_notes = (
            current.get("notes")
            if isinstance((current or {}).get("notes"), dict)
            else {}
        )
        for key, value in dict(current or {}).items():
            if key == "notes":
                continue
            if value not in ("", None, [], {}):
                result[key] = value
        notes = {**base_notes, **current_notes}
        if notes:
            result["notes"] = notes
        return result

    async def _understand_sight_clip_once(
        self, event: Any, clip: SightClip, *, purpose: str = "chat"
    ) -> SightInsight:
        prepared = await self._prepare_sight_clip_material(event, clip)
        timeout = self._sight_finalize_timeout_seconds()
        return await asyncio.wait_for(
            self._finalize_prepared_sight_clip(
                event, clip, purpose=purpose, **prepared
            ),
            timeout=timeout,
        )

    async def _prepare_sight_clip_material(
        self, event: Any, clip: SightClip
    ) -> dict[str, Any]:
        source_note = self._sight_source_note(clip)
        metadata = dict(getattr(clip, "metadata", None) or {})
        metrics = dict(metadata.get("sight_metrics") or {})
        material_started = time.monotonic()

        async def prepare_audio_branch(
            prepared_video: Path | None,
        ) -> tuple[SightTextResult, float]:
            started = time.monotonic()
            reader = self._sight_reader_for_runtime()
            audio_path: Path | None = None
            try:
                audio_path = await reader.prepare_audio(
                    clip.source, prepared_video=prepared_video
                )
                if audio_path:
                    logger.debug(f"{LOG_PREFIX} 视频音频准备完成")
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 视频音频准备跳过：{str(exc)[:160]}")
            result = await reader.read_prepared_audio(event, clip, audio_path)
            return result, time.monotonic() - started

        async def prepare_video_branch() -> tuple[Path | None, str, float]:
            started = time.monotonic()
            source_path: Path | None = None
            error = ""
            try:
                sight_settings = getattr(self.config, "sight", None)
                max_video_mb = int(
                    getattr(sight_settings, "video_download_max_mb", 500)
                )
                download_timeout_seconds = int(
                    getattr(sight_settings, "video_download_timeout_seconds", 240)
                    or 240
                )
                source_path = await prepare_sample_video_source(
                    clip.source,
                    self._sight_cache_dir(),
                    max_video_mb=max_video_mb,
                    download_timeout_seconds=download_timeout_seconds,
                )
                if source_path is None:
                    error = "没有抽取到可用视频画面"
                else:
                    logger.debug(f"{LOG_PREFIX} 视频文件准备完成")
            except Exception as exc:
                error = str(exc)[:160]
                logger.debug(f"{LOG_PREFIX} 视频文件准备跳过：{error}")
            return source_path, error, time.monotonic() - started

        if clip.source:
            source_path, error, video_seconds = await prepare_video_branch()
            text_result, audio_seconds = await prepare_audio_branch(source_path)
        else:
            text_result = SightTextResult()
            source_path = None
            error = "没有拿到视频文件或直链"
            audio_seconds = 0.0
            video_seconds = 0.0

        metadata.update(
            {
                key: value
                for key, value in dict(text_result.metadata or {}).items()
                if value not in ("", None)
            }
        )
        metrics.update(
            {
                "audio_prepare_seconds": round(audio_seconds, 3),
                "video_prepare_seconds": round(video_seconds, 3),
                "material_seconds": round(time.monotonic() - material_started, 3),
            }
        )
        metadata["sight_metrics"] = metrics
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
        frame_notes: list[str] | None = None,
        purpose: str = "chat",
    ) -> SightInsight:
        resolved_frame_notes = list(frame_notes or [])
        frame_assets = [
            dict(item)
            for item in list(metadata.get("frames") or [])
            if isinstance(item, dict)
        ]
        metrics = dict(metadata.get("sight_metrics") or {})
        if source_path is not None and not metadata.get("visual_complete"):
            try:
                sight_settings = getattr(self.config, "sight", None)
                max_frames = int(getattr(sight_settings, "max_frames", 8) or 8)
                logger.debug(f"{LOG_PREFIX} 视频抽帧开始")
                extract_started = time.monotonic()
                frames = await extract_video_frames(
                    source_path, self._sight_cache_dir(), max_frames=max_frames
                )
                metrics["frame_extract_seconds"] = round(
                    time.monotonic() - extract_started, 3
                )
                metrics["key_frames"] = len(frames)
                logger.debug(f"{LOG_PREFIX} 视频抽帧完成")
                if frames:
                    logger.debug(f"{LOG_PREFIX} 视频画面理解开始")
                    vision_started = time.monotonic()
                    described = await self._describe_sight_frames(clip, frames)
                    resolved_frame_notes, frame_assets = described
                    metrics["vision_seconds"] = round(
                        time.monotonic() - vision_started, 3
                    )
                    metrics["vision_requests"] = int(
                        getattr(described, "request_count", len(frames)) or 0
                    )
                    metrics["vision_mode"] = str(
                        getattr(described, "mode", "single") or "single"
                    )
                    logger.debug(f"{LOG_PREFIX} 视频画面理解完成")
                    if not resolved_frame_notes and not text_result.has_content:
                        error = "视觉模型没有返回可确认的画面描述"
                elif not text_result.has_content:
                    error = "没有抽取到可用视频画面"
            except Exception as exc:
                if not text_result.has_content:
                    error = str(exc)[:160]
                logger.debug(f"{LOG_PREFIX} 视频抽帧理解跳过：{str(exc)[:160]}")
            finally:
                metadata["visual_complete"] = True
        if frame_assets:
            metadata["frames"] = frame_assets
        metadata["sight_metrics"] = metrics
        self._log_sight_fusion(text_result, resolved_frame_notes)
        await asyncio.to_thread(
            self._save_sight_prepare_cache,
            clip,
            source_note=source_note,
            source_path=str(source_path or ""),
            frame_notes=resolved_frame_notes,
            text_result=text_result,
            metadata=metadata,
            error=error,
        )
        return await self._finalize_sight_insight(
            event,
            clip,
            source_note=source_note,
            frame_notes=resolved_frame_notes,
            text_result=text_result,
            metadata=metadata,
            error=error,
            purpose=purpose,
        )

    async def _resume_sight_summary_after_timeout(
        self, event: Any, clip: SightClip, *, purpose: str = "chat"
    ) -> SightInsight | None:
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
                    frame_notes=list(prepared.get("frame_notes") or []),
                    purpose=purpose,
                )
            prepared = dict(prepared)
            prepared.pop("source_path", None)
            return await self._finalize_sight_insight(
                event, clip, purpose=purpose, **prepared
            )
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
        purpose: str = "chat",
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
        note = ""
        note_details: list[str] = []
        if purpose != "professional":
            brief_started = time.monotonic()
            note, note_details = await self._sight_brief_for_runtime().summarize(
                clip,
                transcript=text_result.transcript,
                frame_notes=frame_notes,
                metadata=metadata,
            )
            metrics = dict(metadata.get("sight_metrics") or {})
            metrics["brief_seconds"] = round(time.monotonic() - brief_started, 3)
            metadata["sight_metrics"] = metrics
            metadata["brief_ready"] = True
        else:
            metadata["brief_ready"] = False
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
        text_source = (
            text_result.transcript_source or text_result.note_source or "无文字"
        )
        text_count = len(" ".join(str(text_result.transcript or "").split()))
        frame_source = (
            f"时间线抽帧 {len(frame_notes)} 帧" if frame_notes else "无可用画面"
        )
        logger.debug(
            f"{LOG_PREFIX} 视频理解融合：文字={text_source}（{text_count} 字）；画面={frame_source}"
        )

    async def _describe_sight_frames(
        self, clip: SightClip, frames: list[Path | SightFrame]
    ) -> SightFrameDescriptionResult:
        provider = await get_sight_provider(self, "frame_provider")
        if not provider:
            return SightFrameDescriptionResult()
        if not any(
            callable(getattr(provider, name, None))
            for name in ("text_chat", "image_chat", "vision_chat")
        ):
            return SightFrameDescriptionResult()

        total = len(frames)
        parts = [self._sight_frame_parts(frame) for frame in frames]
        notes_by_index: dict[int, str] = {}
        request_count = 0
        mode = "single"

        if total > 1 and callable(getattr(provider, "text_chat", None)):
            session_id = f"daily_life_video_sight_batch_{uuid.uuid4().hex[:8]}"
            try:
                request_count += 1
                try:
                    batch_result = await self._call_sight_vision_batch_provider(
                        provider,
                        batch_frame_prompt(
                            [
                                (index, parts[index - 1][1])
                                for index in range(1, total + 1)
                            ],
                            clip,
                        ),
                        [str(path) for path, _label, _second in parts],
                        session_id,
                    )
                except Exception as exc:
                    logger.debug(
                        f"{LOG_PREFIX} 视频多图理解不可用，改用单帧并发：{str(exc)[:160]}"
                    )
                    batch_result = None
                notes_by_index.update(
                    batch_frame_notes_from_text(self._completion_text(batch_result))
                )
                if notes_by_index:
                    mode = "multi_image"
                else:
                    first_note = frame_note_from_text(
                        self._completion_text(batch_result)
                    )
                    if first_note:
                        notes_by_index[1] = first_note
            finally:
                await self._cleanup_sight_vision_session(session_id)

        missing = [
            index for index in range(1, total + 1) if index not in notes_by_index
        ]
        if missing:
            mode = "fallback_parallel" if total > 1 else "single"
            semaphore = asyncio.Semaphore(2)

            async def describe_one(index: int) -> tuple[int, str]:
                frame_path, label, _second = parts[index - 1]
                session_id = f"daily_life_video_sight_{uuid.uuid4().hex[:8]}"
                try:
                    async with semaphore:
                        try:
                            result = await self._call_sight_vision_provider(
                                provider,
                                frame_prompt(index, total, clip, label),
                                str(frame_path),
                                session_id,
                            )
                        except Exception as exc:
                            logger.debug(
                                f"{LOG_PREFIX} 视频单帧理解跳过：时间={label or '未知'}；"
                                f"原因={str(exc)[:120]}"
                            )
                            result = None
                    return index, frame_note_from_text(self._completion_text(result))
                finally:
                    await self._cleanup_sight_vision_session(session_id)

            described = await asyncio.gather(
                *(describe_one(index) for index in missing)
            )
            request_count += len(missing)
            notes_by_index.update(
                {index: note for index, note in described if str(note or "").strip()}
            )

        notes: list[str] = []
        assets: list[dict[str, Any]] = []
        for index, (frame_path, label, second) in enumerate(parts, start=1):
            note = notes_by_index.get(index, "")
            if note:
                notes.append(f"{label}：{note}" if label else note)
            if await asyncio.to_thread(frame_path.is_file):
                asset: dict[str, Any] = {
                    "path": str(frame_path),
                    "label": label,
                    "second": float(second or 0.0),
                }
                if note:
                    asset["note"] = note
                assets.append(asset)
        return SightFrameDescriptionResult(
            notes=notes,
            assets=assets,
            request_count=request_count,
            mode=mode,
        )

    async def _cleanup_sight_vision_session(self, session_id: str) -> None:
        cleanup = getattr(self.composer, "_cleanup_conversation", None)
        if callable(cleanup):
            await cleanup(session_id)

    @staticmethod
    async def _call_sight_vision_batch_provider(
        provider: Any, prompt: str, images: list[str], session_id: str
    ) -> Any:
        method = getattr(provider, "text_chat", None)
        if not callable(method):
            return None
        try:
            result = method(
                prompt=prompt,
                image_urls=list(images),
                session_id=session_id,
            )
        except (TypeError, NotImplementedError, AttributeError):
            return None
        if hasattr(result, "__await__"):
            result = await result
        return result

    @staticmethod
    def _sight_frame_parts(frame: Path | SightFrame) -> tuple[Path, str, float]:
        if isinstance(frame, SightFrame):
            return frame.path, frame.label, float(frame.second or 0.0)
        return Path(frame), "", 0.0

    @staticmethod
    async def _call_sight_vision_provider(
        provider: Any, prompt: str, image: str, session_id: str
    ) -> Any:
        for name, kwargs in (
            (
                "text_chat",
                {"prompt": prompt, "image_urls": [image], "session_id": session_id},
            ),
            (
                "image_chat",
                {"prompt": prompt, "image": image, "session_id": session_id},
            ),
            (
                "vision_chat",
                {"prompt": prompt, "image": image, "session_id": session_id},
            ),
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
            clip = await self._sight_video_clip_from_component(
                item, scope=scope, message_id=message_id, origin=origin, text=text
            )
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
        source = ""
        getter = getattr(item, "get_file", None)
        if callable(getter):
            try:
                resolved = getter()
                if hasattr(resolved, "__await__"):
                    resolved = await resolved
                source = str(resolved or "").strip()
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 视频文件异步获取失败：{str(exc)[:160]}")
        converter = getattr(item, "convert_to_file_path", None)
        if callable(converter) and not source:
            try:
                resolved = converter()
                if hasattr(resolved, "__await__"):
                    resolved = await resolved
                source = str(resolved or "").strip() or source
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 引用视频本地解析失败：{str(exc)[:160]}")
        if not source:
            source = source_from_value(payload_from_item(item))
        payload = payload_from_item(item)
        name = str(
            payload.get("name")
            or payload.get("file_name")
            or payload.get("filename")
            or ""
        ).strip()
        file_id = str(payload.get("file_id") or payload.get("fileid") or "").strip()
        if not source and not file_id:
            return None
        metadata: dict[str, Any] = {}
        file_size = str(payload.get("file_size") or payload.get("size") or "").strip()
        if file_size:
            metadata["file_size"] = file_size
        if source and not source.lower().startswith(
            ("http://", "https://", "file://", "data:", "base64:")
        ):
            try:
                fingerprint = await asyncio.to_thread(
                    _sight_file_content_fingerprint, source
                )
            except Exception:
                fingerprint = ""
            if fingerprint:
                metadata["content_fingerprint"] = fingerprint
        return SightClip(
            scope=scope,
            message_id=message_id,
            source=source,
            file_id=file_id,
            name=name,
            origin=origin,
            text=text,
            metadata=metadata,
        )

    @staticmethod
    def _sight_nested_components(item: Any) -> list[Any]:
        values: list[Any] = []
        if isinstance(item, dict):
            data = item.get("data")
            for key in (
                "chain",
                "message",
                "messages",
                "items",
                "segments",
                "components",
                "nodes",
            ):
                nested = item.get(key)
                if isinstance(nested, list):
                    values.extend(nested)
            if isinstance(data, dict):
                for key in (
                    "chain",
                    "message",
                    "messages",
                    "items",
                    "segments",
                    "components",
                    "nodes",
                ):
                    nested = data.get(key)
                    if isinstance(nested, list):
                        values.extend(nested)
            return values
        for key in (
            "chain",
            "message",
            "messages",
            "items",
            "segments",
            "components",
            "nodes",
        ):
            nested = getattr(item, key, None)
            if isinstance(nested, list):
                values.extend(nested)
        data = getattr(item, "data", None)
        if isinstance(data, dict):
            for key in (
                "chain",
                "message",
                "messages",
                "items",
                "segments",
                "components",
                "nodes",
            ):
                nested = data.get(key)
                if isinstance(nested, list):
                    values.extend(nested)
        return values

    async def _resolve_sight_clip_source(
        self, event: Any, clip: SightClip
    ) -> SightClip:
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
            actions.append(
                ("get_group_file_url", {"group_id": group_id, "file_id": file_id})
            )
            if str(group_id).isdigit():
                actions.append(
                    (
                        "get_group_file_url",
                        {"group_id": int(group_id), "file_id": file_id},
                    )
                )
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
        self.note_structured_bot_message(
            scope, status_text, source_event=source_event or event, media="图片"
        )
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
                method = (
                    getattr(chain, "url_image", None)
                    if image.startswith(("http://", "https://"))
                    else getattr(chain, "file_image", None)
                )
                if callable(method):
                    return method(image)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 视频总结文转图失败，改用文字发送：{exc}")
        return MessageChain().message(markdown)

    async def _render_sight_note_image(self, scope: str, markdown: str) -> str:
        from astrbot.core import html_renderer

        config = self._astrbot_config(scope)
        prepared = await asyncio.to_thread(
            embed_local_markdown_images,
            "\n\n" + str(markdown or "").strip(),
        )
        use_network = (
            str(config.get("t2i_strategy") or "remote") == "remote"
            or "data:image/" in prepared
        )
        return str(
            await html_renderer.render_t2i(
                prepared,
                return_url=True,
                use_network=use_network,
                template_name=str(config.get("t2i_active_template") or "base"),
            )
            or ""
        ).strip()

    async def _sight_recent_for_event(
        self, event: Any, *, limit: int = 2
    ) -> list[SightInsight]:
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
            professional = html.escape(
                self._sight_compact_text(metadata.get("professional_digest"), 600)
            )
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
            current = str(
                getattr(part, "text", "")
                or (part.get("text", "") if isinstance(part, dict) else "")
            )
            if current == text:
                return
        part = TextPart(text=text)
        marker = getattr(part, "mark_as_temp", None)
        if callable(marker):
            part = marker()
        parts.append(part)
