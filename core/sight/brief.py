from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from ..life.tools import extract_json_from_text
from ..prompts import cache_friendly_prompt
from .clip import SightClip
from .checkpoint import audio_outline_checkpointed
from .model import get_sight_provider, sight_provider_id


TRANSCRIPT_DIRECT_LIMIT = 900
TRANSCRIPT_EXCERPT_LIMIT = 1800
TRANSCRIPT_CHUNK_CHARS = 4500
TRANSCRIPT_CHUNKS_PER_PASS = 4


class SightBrief:
    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def summarize(
        self,
        clip: SightClip,
        *,
        transcript: str = "",
        frame_notes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        transcript = " ".join(str(transcript or "").split())[:8000]
        frame_notes = [item for item in (frame_notes or []) if str(item or "").strip()]
        metadata = dict(metadata or {})
        if not transcript and not frame_notes and not metadata:
            return "", []
        composer = getattr(self.runtime, "composer", None)
        if composer is None:
            return "", []
        call_llm = getattr(composer, "_call_llm_text", None)
        if not callable(call_llm):
            return "", []
        provider_id = sight_provider_id(self.runtime, "summary_provider")
        provider = await get_sight_provider(self.runtime, "summary_provider")
        if not provider:
            return "", []
        checkpoint_key = self._checkpoint_key(clip, transcript, frame_notes, metadata, provider_id=provider_id)
        audio_summary, audio_details = await audio_outline_checkpointed(
            self,
            provider,
            call_llm,
            composer,
            transcript,
            metadata,
            provider_id=provider_id,
            checkpoint_key=checkpoint_key,
        )
        fusion_transcript = transcript if len(transcript) <= TRANSCRIPT_DIRECT_LIMIT else _excerpt(transcript, TRANSCRIPT_EXCERPT_LIMIT)
        text = await self._call_model(
            provider,
            call_llm,
            composer,
            self._prompt(
                clip,
                fusion_transcript,
                frame_notes,
                metadata,
                audio_summary=audio_summary,
                audio_details=audio_details,
            ),
            prefix="daily_life_sight_brief",
            provider_id=provider_id,
        )
        payload = extract_json_from_text(text)
        if not isinstance(payload, dict):
            summary = " ".join(str(text or "").split())[:160]
            return summary, audio_details
        summary = " ".join(str(payload.get("summary") or "").split())[:180]
        details = _clean_list(audio_details + _clean_list(payload.get("details"), limit=5), limit=8)
        return summary, details

    def _checkpoint_key(
        self,
        clip: SightClip,
        transcript: str,
        frame_notes: list[str],
        metadata: dict[str, Any],
        *,
        provider_id: str = "",
    ) -> str:
        payload = {
            "clip_key": getattr(clip, "key", ""),
            "provider_id": str(provider_id or ""),
            "transcript": " ".join(str(transcript or "").split())[:8000],
            "frame_notes": _clean_list(frame_notes, limit=32),
            "metadata": json.loads(json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True, default=str)),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    async def _audio_outline(
        self,
        provider: Any,
        call_llm: Any,
        composer: Any,
        transcript: str,
        metadata: dict[str, Any],
        *,
        provider_id: str = "",
    ) -> tuple[str, list[str]]:
        if not transcript:
            return "", []
        if len(transcript) <= TRANSCRIPT_DIRECT_LIMIT:
            return transcript[:260], []
        chunks = _chunks(transcript, TRANSCRIPT_CHUNK_CHARS)
        partials: list[str] = []
        for index in range(0, len(chunks), TRANSCRIPT_CHUNKS_PER_PASS):
            group = chunks[index : index + TRANSCRIPT_CHUNKS_PER_PASS]
            text = await self._call_model(
                provider,
                call_llm,
                composer,
                self._audio_prompt(
                    group,
                    metadata,
                    offset=index,
                    total=len(chunks),
                ),
                prefix="daily_life_sight_audio",
                provider_id=provider_id,
            )
            summary, details = _json_summary(text)
            if summary:
                partials.append(summary)
            partials.extend(details)
        if not partials:
            partials = [_excerpt(transcript, TRANSCRIPT_EXCERPT_LIMIT)]
        if len(partials) <= 3:
            summary = "；".join(partials)[:420]
            return summary, [f"音频主线：{summary}"] if summary else []
        text = await self._call_model(
            provider,
            call_llm,
            composer,
            self._merge_audio_prompt(partials, metadata),
            prefix="daily_life_sight_audio",
            provider_id=provider_id,
        )
        summary, details = _json_summary(text)
        summary = summary or "；".join(partials[:4])[:420]
        result_details = [f"音频主线：{summary}"]
        result_details.extend(details)
        return summary, _clean_list(result_details, limit=5)

    async def _call_model(
        self,
        provider: Any,
        call_llm: Any,
        composer: Any,
        prompt: str,
        *,
        prefix: str,
        provider_id: str = "",
    ) -> str:
        session_id = f"{prefix}_{uuid.uuid4().hex[:8]}"
        try:
            if provider_id:
                try:
                    return await call_llm(provider, prompt, session_id, empty_retries=0, primary_provider_id=provider_id)
                except TypeError:
                    pass
            return await call_llm(provider, prompt, session_id, empty_retries=0)
        finally:
            cleanup = getattr(composer, "_cleanup_conversation", None)
            if callable(cleanup):
                await cleanup(session_id)

    @staticmethod
    def _prompt(
        clip: SightClip,
        transcript: str,
        frame_notes: list[str],
        metadata: dict[str, Any],
        *,
        audio_summary: str = "",
        audio_details: list[str] | None = None,
    ) -> str:
        frame_text = "\n".join(f"- {item}" for item in frame_notes[:12]) or "（没有时间线画面描述）"
        audio_text = _audio_section(audio_summary, audio_details or [])
        title = str(metadata.get("title") or "").strip()
        author = str(metadata.get("author") or metadata.get("uploader") or "").strip()
        duration = str(metadata.get("duration") or "").strip()
        fixed = (
            "请把视频音频主线、转写摘录和时间线画面整理成聊天可用的视频理解结果。\n"
            "音频主线和时间线画面都要纳入判断：音频负责人物说了什么、叙事推进和关键信息；画面负责可见场景、动作和氛围。\n"
            "如果音频与画面信息不一致，分别说明可确认部分，不要让某一边覆盖另一边。\n"
            "只基于视频内容本身，不补编没看到、没听到的信息；不要把标题、作者、时长等资料当成视频内容复述；不确定就保持笼统。\n"
            "输出 JSON：{\"summary\":\"20-90字概括\",\"details\":[\"可确认信息1\",\"可确认信息2\"]}"
        )
        dynamic = (
            f"标题：{title or '未知'}\n"
            f"作者：{author or '未知'}\n"
            f"时长：{duration or '未知'}\n"
            f"随视频文字：{clip.text or '无'}\n\n"
            f"音频主线：\n{audio_text or '（没有音频主线）'}\n\n"
            f"音频转写内容：\n{transcript or '（没有音频转写）'}\n\n"
            f"时间线画面：\n{frame_text}"
        )
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="视频内容")

    @staticmethod
    def _audio_prompt(
        chunks: list[str],
        metadata: dict[str, Any],
        *,
        offset: int,
        total: int,
    ) -> str:
        title = str(metadata.get("title") or "").strip()
        chunk_text = "\n".join(
            f"片段 {offset + index + 1}/{total}：{chunk}"
            for index, chunk in enumerate(chunks)
            if chunk
        )
        fixed = (
            "请只根据视频音频转写提炼音频主线，不要加入画面猜测。\n"
            "保留人物说了什么、事件推进、观点、情绪变化和可直接复述给用户的关键信息。\n"
            "输出 JSON：{\"summary\":\"40-140字音频主线\",\"details\":[\"音频细节1\",\"音频细节2\"]}"
        )
        dynamic = f"标题：{title or '未知'}\n音频转写片段：\n{chunk_text}"
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="音频转写")

    @staticmethod
    def _merge_audio_prompt(
        partials: list[str],
        metadata: dict[str, Any],
    ) -> str:
        title = str(metadata.get("title") or "").strip()
        partial_text = "\n".join(f"- {item}" for item in partials if item)
        fixed = (
            "请把分段音频主线合并成一条完整、连贯的视频音频主线。\n"
            "只合并给出的音频信息，不加入画面猜测。\n"
            "输出 JSON：{\"summary\":\"50-160字音频主线\",\"details\":[\"关键音频信息1\",\"关键音频信息2\"]}"
        )
        dynamic = f"标题：{title or '未知'}\n分段音频主线：\n{partial_text}"
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="音频主线")


def _clean_list(value: Any, limit: int = 5) -> list[str]:
    values = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    result: list[str] = []
    for item in values:
        text = " ".join(str(item or "").split())[:140]
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _json_summary(text: str) -> tuple[str, list[str]]:
    payload = extract_json_from_text(text)
    if isinstance(payload, dict):
        summary = " ".join(str(payload.get("summary") or "").split())[:420]
        details = _clean_list(payload.get("details"), limit=4)
        return summary, details
    summary = " ".join(str(text or "").split())[:260]
    return summary, []


def _chunks(text: str, size: int) -> list[str]:
    values: list[str] = []
    current: list[str] = []
    current_len = 0
    for piece in str(text or "").split():
        piece_len = len(piece) + 1
        if current and current_len + piece_len > size:
            values.append(" ".join(current))
            current = []
            current_len = 0
        current.append(piece)
        current_len += piece_len
    if current:
        values.append(" ".join(current))
    return values or ([str(text or "").strip()] if str(text or "").strip() else [])


def _excerpt(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    head = value[: limit // 2].rstrip()
    tail = value[-limit // 2 :].lstrip()
    return f"{head}\n...\n{tail}"


def _audio_section(summary: str, details: list[str]) -> str:
    parts = []
    summary = " ".join(str(summary or "").split())
    if summary:
        parts.append(f"主线：{summary}")
    parts.extend(f"- {item}" for item in _clean_list(details, limit=5))
    return "\n".join(parts)
