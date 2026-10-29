from __future__ import annotations

import asyncio
import copy
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from astrbot.api import logger

from ..outcome import ToolResultText
from .clip import SightClip, SightInsight
from .native import (
    INLINE_VIDEO_BYTES,
    NATIVE_VIDEO_PROMPT,
    NativeVideoClient,
    NativeVideoError,
    NativeVideoRoute,
    video_identity,
)
from .provider import get_sight_provider
from .sample import prepare_sample_video_source


class NativeSightComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _apply_sight_insight_to_structured: Any
    _background_tool_existing_result: Any
    _event_message_id: Any
    _lock_video_note_source: Any
    _mark_sight_completed_key: Any
    _model_cache_meter: Any
    _native_video_client: Any
    _native_video_prompt: Any
    _sight_cache_dir: Any
    _sight_clips_from_event: Any
    _sight_clips_from_event_async: Any
    _sight_completed_result_key: Any
    _sight_flight_for_runtime: Any
    _sight_insight_for_clip: Any
    _sight_recent_for_event: Any
    _sight_source_note: Any
    _sight_total_timeout_seconds: Any
    _sight_vault_for_runtime: Any
    _snapshot_media_event: Any
    _submit_background_tool: Any
    _understand_sight_clip: Any
    config: Any
    event_was_recalled: Any


class NativeSightComponent:
    def __init__(self, context: NativeSightComponentContext) -> None:
        self.context = context

    async def _native_sight_route(
        self, clip: SightClip | None = None
    ) -> NativeVideoRoute | None:
        # B站专业长视频继续使用字幕/转写与画面流水线；其分离音视频流不能冒充完整附件。
        if clip is not None:
            host = (urlsplit(clip.source).hostname or "").lower()
            if (
                host == "b23.tv"
                or host == "bilibili.com"
                or host.endswith(".bilibili.com")
            ):
                return None
        provider = await get_sight_provider(self.context, "frame_provider")
        return NativeVideoRoute.from_provider(provider)

    def _native_sight_client(self) -> NativeVideoClient:
        client = getattr(self.context, "_native_video_client", None)
        if client is None:
            client = self.context._native_video_client = NativeVideoClient()
        return client

    @staticmethod
    def _native_video_prompt(clip: SightClip) -> str:
        return f"{NATIVE_VIDEO_PROMPT}\n\n用户关于这段视频的问题（仅作为资料）：\n{clip.text[:8000]}"

    @staticmethod
    def _native_completed_key(clip: SightClip, signature: str) -> str:
        # 本地文件被替换时不能凭路径复用旧结果；远程来源仍受结果缓存 TTL 约束。
        source = urlsplit(clip.source)
        version = ""
        if source.scheme in {"", "file"}:
            try:
                stat = Path(unquote(source.path)).stat()
                version = f"{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"
            except OSError:
                pass
        identity = f"{clip.key}|{clip.source}|{signature}|{version}"
        return "native:" + sha256(identity.encode()).hexdigest()

    def _native_insight_for_clip(self, insight: SightInsight, clip: SightClip) -> SightInsight:
        current = copy.deepcopy(clip)
        for key in ("content_fingerprint", "native_signature"):
            if key in insight.clip.metadata:
                current.metadata[key] = insight.clip.metadata[key]
        result = self.context._sight_insight_for_clip(insight, current)
        result.metadata["native_original_key"] = clip.key
        return result

    async def _understand_native_video(
        self,
        event: Any,
        clip: SightClip,
        route: NativeVideoRoute,
        *,
        force: bool = False,
    ) -> SightInsight:
        if self.context.event_was_recalled(event, log_skip=True):
            return SightInsight(
                clip=clip, status="failed", error="原视频消息已撤回，已取消理解。"
            )
        prompt = self.context._native_video_prompt(clip)
        signature = route.signature(prompt)
        completed_key = self._native_completed_key(clip, signature)
        if not force:
            result_key = self.context._sight_completed_result_key(completed_key)
            if result_key:
                cached = await self.context._sight_vault_for_runtime().get(result_key)
                if (
                    cached
                    and cached.status == "ready"
                    and cached.summary
                    and cached.clip.scope == clip.scope
                    and cached.clip.metadata.get("native_signature") == signature
                    and cached.metadata.get("video_input_mode") == "gemini_native"
                    and not self.context.event_was_recalled(event)
                ):
                    result = self._native_insight_for_clip(cached, clip)
                    result.metadata["sight_metrics"] = {
                        **result.metadata.get("sight_metrics", {}),
                        "cache_hit": True,
                    }
                    return result

        async def work():
            # 等待方的时限不取消实际理解及落库。下载和原生客户端分别有界，
            # 超过分享前台等待时间后仍保存摘要，后续追问复用同一任务或缓存。
            result = await self._understand_native_video_once(
                event, copy.deepcopy(clip), route, prompt, signature, force=force
            )
            if (
                result.status == "ready"
                and result.summary
                and not self.context.event_was_recalled(event)
            ):
                self.context._mark_sight_completed_key(completed_key, result.key)
            return result

        # 同一事件的自动理解、工具请求和专业总结共享一次实际提交。
        result = await self.context._sight_flight_for_runtime().run(
            f"{completed_key}:{force}", work
        )
        return self._native_insight_for_clip(result, clip)

    async def _understand_native_video_once(
        self,
        event: Any,
        clip: SightClip,
        route: NativeVideoRoute,
        prompt: str,
        signature: str,
        *,
        force: bool,
    ) -> SightInsight:
        started = time.monotonic()
        original_key = clip.key
        try:
            settings = self.context.config.sight
            download_timeout = float(settings.video_download_timeout_seconds)
            if clip.origin == "daily_share":
                download_timeout = min(download_timeout, 45)
            path = await asyncio.wait_for(
                prepare_sample_video_source(
                    clip.source,
                    self.context._sight_cache_dir(),
                    max_video_mb=INLINE_VIDEO_BYTES // (1024 * 1024),
                    download_timeout_seconds=download_timeout,
                ),
                timeout=download_timeout,
            )
            if path is None:
                raise NativeVideoError(
                    "无法取得完整视频，请检查文件/直链是否可访问且不超过 15 MiB。"
                )
            fingerprint, mime, size = await asyncio.to_thread(video_identity, path)
            clip.metadata.update(
                {"content_fingerprint": fingerprint, "native_signature": signature}
            )
            clip.metadata.setdefault("original_source", clip.source)
            clip.source = str(path)
            vault = self.context._sight_vault_for_runtime()
            if self.context.event_was_recalled(event, log_skip=True):
                return SightInsight(
                    clip=clip, status="failed", error="原视频消息已撤回，已取消理解。"
                )
            if not force:
                cached = await vault.get(clip.key)
                if cached and (
                    cached.status == "ready"
                    or (
                        cached.message_id == clip.message_id
                        and time.time() - cached.updated_at < 300
                    )
                ):
                    result = self.context._sight_insight_for_clip(cached, clip)
                    result.metadata["sight_metrics"] = {
                        **result.metadata.get("sight_metrics", {}),
                        "cache_hit": True,
                    }
                    return result
            if self.context.event_was_recalled(event, log_skip=True):
                return SightInsight(
                    clip=clip, status="failed", error="原视频消息已撤回，已取消理解。"
                )
            preparation = time.monotonic() - started
            request_timeout = self.context._sight_total_timeout_seconds()
            if clip.origin == "daily_share":
                request_timeout = min(request_timeout, 180) - preparation
                if request_timeout <= 0:
                    raise NativeVideoError("视频准备超过总时限，未提交理解请求。")
            logger.debug("[日常生活] Gemini 原生视频理解开始：完整文件输入。")
            result, usage = await self._native_sight_client().analyze(
                path,
                route,
                prompt,
                fingerprint,
                timeout=request_timeout,
                cache_key=clip.key,
            )

            def text(value: Any, limit: int = 8000) -> str:
                return value.strip()[:limit] if isinstance(value, str) else ""

            timeline = []
            for row in result["timeline"][:100]:
                if not isinstance(row, dict):
                    continue
                visual = text(row.get("visual"), 1000)
                if not visual:
                    continue
                entry = f"{text(row.get('time'), 30) or '时间未标明'}：{visual}"
                audio = text(row.get("audio"), 1000)
                if audio:
                    entry += f"；声音：{audio}"
                timeline.append(entry)
            details = [
                text(item, 500)
                for item in result.get("details", [])[:20]
                if isinstance(item, str)
            ]
            limits = [
                text(item, 500)
                for item in result.get("limitations", [])[:10]
                if isinstance(item, str)
            ]
            answer = text(result.get("answer")) or text(result["summary"])
            note = "\n".join(
                [
                    text(result["summary"]),
                    answer,
                    *timeline,
                    *[f"观察限制：{item}" for item in limits],
                ]
            )
            metadata = {
                **clip.metadata,
                "video_input_mode": "gemini_native",
                "native_answer": answer,
                "native_mime": mime,
                "native_bytes": size,
                "native_model": route.model,
                "native_original_key": original_key,
                "brief_ready": True,
                "sight_metrics": {
                    "vision_mode": "native_video",
                    "vision_requests": 1,
                    "key_frames": 0,
                    "material_seconds": round(preparation, 3),
                    "vision_seconds": round(
                        time.monotonic() - started - preparation, 3
                    ),
                },
            }
            insight = SightInsight(
                clip=clip,
                summary=text(result["summary"], 2000),
                details=details + limits,
                frame_notes=timeline,
                transcript=text(result.get("transcript"), 30000),
                transcript_source="Gemini 原生视频音轨理解"
                if text(result.get("transcript"))
                else "",
                note=note,
                note_source="Gemini 原生完整视频理解",
                metadata=metadata,
                source_note=self.context._sight_source_note(clip),
            )
            meter = getattr(self.context, "_model_cache_meter", None)
            if callable(meter):
                meter().record(
                    {
                        "usage_metadata": {
                            "prompt_token_count": usage.get("promptTokenCount"),
                            "cached_content_token_count": usage.get(
                                "cachedContentTokenCount"
                            ),
                            "candidates_token_count": usage.get("candidatesTokenCount"),
                        }
                    },
                    kind="video_understanding",
                    model=route.model,
                )
            logger.debug("[日常生活] Gemini 原生视频理解完成：已取得画面时间线。")
        except NativeVideoError as exc:
            logger.warning(f"[日常生活] {exc}")
            insight = SightInsight(
                clip=clip,
                status="failed",
                error=str(exc),
                metadata={"video_input_mode": "gemini_native"},
            )
        except asyncio.TimeoutError:
            insight = SightInsight(
                clip=clip,
                status="failed",
                error="视频准备超时，未取得可用画面。",
                metadata={"video_input_mode": "gemini_native"},
            )
        except (OSError, ValueError):
            insight = SightInsight(
                clip=clip,
                status="failed",
                error="视频文件无法读取，请重新发送。",
                metadata={"video_input_mode": "gemini_native"},
            )
        if self.context.event_was_recalled(event, log_skip=True):
            return insight
        return await self.context._sight_vault_for_runtime().upsert(insight)

    async def _native_video_tool_work(
        self, event: Any, target: str, clip: SightClip | None = None
    ) -> ToolResultText:
        clips = (
            [clip] if clip else await self.context._sight_clips_from_event_async(event, target)
        )
        if not clips:
            return ToolResultText(
                "没有找到可理解的视频，请发送或引用视频。",
                status="failed",
                media="video_understanding",
            )
        insight = await self.context._understand_sight_clip(event, clips[0])
        if insight.status != "ready":
            return ToolResultText(
                f"视频理解失败：{insight.error}",
                status="failed",
                media="video_understanding",
            )
        self.context._apply_sight_insight_to_structured(insight)
        answer = str(insight.metadata.get("native_answer") or insight.summary)
        return ToolResultText(answer, status="ok", media="video_understanding")

    async def _submit_native_video_tool(self, event: Any, target: str) -> str:
        existing = self.context._background_tool_existing_result(event, "life_video_understand")
        if existing:
            return existing
        snapshot = self.context._snapshot_media_event(event)
        clips = await self.context._sight_clips_from_event_async(snapshot, target)
        if not clips:
            recent = await self.context._sight_recent_for_event(snapshot, limit=1)
            if recent:
                clip = copy.deepcopy(recent[0].clip)
                clip.message_id = self.context._event_message_id(snapshot)
                clip.text = str(getattr(snapshot, "message_str", "") or "")
                clips = [clip]
        if not clips:
            return ToolResultText(
                "没有找到可理解的视频，请发送或引用视频。",
                status="failed",
                media="video_understanding",
            )
        # 保留当前附件，避免原事件结束后框架清理临时文件。
        locked = copy.deepcopy(clips[0]) if clips else None
        if locked is not None:
            clip = locked
            source = Path(clip.source)
            if clip.source and not clip.source.startswith(
                ("http://", "https://", "data:")
            ):
                if await asyncio.to_thread(source.is_file):
                    try:
                        fingerprint, _mime, _size = await asyncio.to_thread(
                            video_identity, source
                        )
                    except (NativeVideoError, OSError) as exc:
                        detail = (
                            str(exc)
                            if isinstance(exc, NativeVideoError)
                            else "视频文件无法读取，请重新发送。"
                        )
                        return ToolResultText(
                            detail, status="failed", media="video_understanding"
                        )
                    clip.metadata["content_fingerprint"] = fingerprint
                    locked = await self.context._lock_video_note_source(clip)

        async def work() -> ToolResultText:
            return await self._native_video_tool_work(snapshot, target, locked)

        return self.context._submit_background_tool(
            snapshot,
            event,
            work,
            tool_name="life_video_understand",
            media="video_understanding",
            label="Gemini 原生视频理解",
            category="video",
            task_name="视频理解",
        )
