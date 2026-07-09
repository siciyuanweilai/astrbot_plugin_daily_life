from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .hearing import AudioTranscriptError, prepare_audio_source, transcribe_bcut, transcribe_bcut_audio
from .clip import SightClip
from .local import LocalAsrConfig, transcribe_local, transcribe_local_audio
from .probe import clean_source
from .sample import sight_cache_dir
from .transcript import TranscriptResult
from ..runtime.markers import LOG_PREFIX


AUDIO_TRANSCRIPT_TIMEOUT_SECONDS = 240
LOCAL_ASR_BATCH_SIZE_SECONDS = 300


@dataclass(slots=True)
class SightTextResult:
    transcript: str = ""
    transcript_source: str = ""
    note: str = ""
    note_source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    raw_transcript: Any = None

    @property
    def has_content(self) -> bool:
        return bool(self.transcript.strip() or self.note.strip())


class SightReader:
    def __init__(self, runtime: Any):
        self.runtime = runtime
        settings = getattr(getattr(runtime, "config", None), "sight", None)
        max_chars = getattr(settings, "max_transcript_chars", 8000)
        self.audio_timeout_seconds = AUDIO_TRANSCRIPT_TIMEOUT_SECONDS
        self.audio_transcript_mode = str(getattr(settings, "audio_transcript_mode", "local") or "local")
        self.max_chars = max(500, int(max_chars or 8000))
        self.local_asr_config = LocalAsrConfig(
            batch_size_s=LOCAL_ASR_BATCH_SIZE_SECONDS,
            timeout_seconds=max(120, int(getattr(settings, "local_asr_timeout_seconds", 900) or 900)),
        )
        self.settings_signature = (
            self.max_chars,
            self.audio_transcript_mode.strip().lower(),
            self.audio_timeout_seconds,
            self.local_asr_config.batch_size_s,
            self.local_asr_config.timeout_seconds,
        )

    async def read(self, _event: Any, clip: SightClip) -> SightTextResult:
        source = clean_source(clip.source or clip.text)
        if not source:
            return SightTextResult()
        base_metadata = dict(getattr(clip, "metadata", None) or {})
        errors: list[str] = []
        transcript = await self._read_transcript(source, errors=errors)
        return self._text_result_from_transcript(clip, transcript, base_metadata, errors)

    async def prepare_audio(self, source: str) -> Path | None:
        if not self.transcript_enabled:
            return None
        source = clean_source(source)
        if not source:
            return None
        return await prepare_audio_source(source, self._cache_dir())

    async def read_prepared_audio(self, _event: Any, clip: SightClip, audio_path: Path | None) -> SightTextResult:
        base_metadata = dict(getattr(clip, "metadata", None) or {})
        if not self.transcript_enabled:
            return SightTextResult(metadata=base_metadata)
        errors: list[str] = []
        if audio_path:
            transcript = await self._read_prepared_transcript(audio_path, errors=errors, log_stage=True)
        else:
            source = clean_source(clip.source or clip.text)
            transcript = await self._read_transcript(source, errors=errors, log_stage=True) if source else None
        return self._text_result_from_transcript(clip, transcript, base_metadata, errors)

    def _text_result_from_transcript(
        self,
        clip: SightClip,
        transcript: TranscriptResult | None,
        base_metadata: dict[str, Any],
        errors: list[str],
    ) -> SightTextResult:
        if not transcript:
            return SightTextResult(metadata=base_metadata, errors=errors)
        if base_metadata:
            merged = dict(base_metadata)
            merged.update({key: value for key, value in dict(transcript.metadata or {}).items() if value not in ("", None)})
            transcript.metadata = merged
        setattr(clip, "raw_transcript", transcript)
        return self._from_transcript(transcript)

    async def _read_transcript(
        self,
        source: str,
        *,
        errors: list[str] | None = None,
        log_stage: bool = False,
    ) -> TranscriptResult | None:
        for route in self._transcript_routes():
            result = await self._read_route(route, source, errors=errors, log_stage=log_stage)
            if result:
                return result
        return None

    async def _read_route(
        self,
        route: str,
        source: str,
        *,
        errors: list[str] | None = None,
        log_stage: bool = False,
    ) -> TranscriptResult | None:
        label = "本地ASR" if route == "local" else "必剪"
        timeout_seconds = self.local_asr_config.timeout_seconds if route == "local" else self.audio_timeout_seconds
        try:
            if log_stage:
                logger.debug(f"{LOG_PREFIX} 视频音频转写开始：{label}")
            result = await asyncio.wait_for(
                self._call_route(route, source),
                timeout=timeout_seconds + 10,
            )
            if result and result.has_text:
                if log_stage:
                    logger.debug(f"{LOG_PREFIX} 视频音频转写完成")
                return result
            if errors is not None:
                errors.append(f"{label}转写没有返回可用文字")
            return None
        except asyncio.TimeoutError:
            if errors is not None:
                errors.append(f"{label}转写超时")
            logger.debug(f"{LOG_PREFIX} 视频{label}转写超时")
            return None
        except AudioTranscriptError as exc:
            if errors is not None:
                errors.append(f"{label}转写失败：{exc}")
            logger.debug(f"{LOG_PREFIX} 视频{label}转写失败：{exc}")
            return None
        except Exception as exc:
            if errors is not None:
                errors.append(f"{label}转写失败：{exc}")
            logger.debug(f"{LOG_PREFIX} 视频{label}转写失败：{exc}")
            return None

    async def _call_route(self, route: str, source: str) -> TranscriptResult | None:
        if route == "local":
            return await transcribe_local(
                source,
                self._cache_dir(),
                config=self.local_asr_config,
                max_chars=self.max_chars,
            )
        return await transcribe_bcut(
            source,
            self._cache_dir(),
            timeout_seconds=self.audio_timeout_seconds,
            max_chars=self.max_chars,
        )

    async def _read_prepared_transcript(
        self,
        audio_path: Path,
        *,
        errors: list[str] | None = None,
        log_stage: bool = False,
    ) -> TranscriptResult | None:
        for route in self._transcript_routes():
            result = await self._read_prepared_route(route, audio_path, errors=errors, log_stage=log_stage)
            if result:
                return result
        return None

    async def _read_prepared_route(
        self,
        route: str,
        audio_path: Path,
        *,
        errors: list[str] | None = None,
        log_stage: bool = False,
    ) -> TranscriptResult | None:
        label = "本地ASR" if route == "local" else "必剪"
        timeout_seconds = self.local_asr_config.timeout_seconds if route == "local" else self.audio_timeout_seconds
        try:
            if log_stage:
                logger.debug(f"{LOG_PREFIX} 视频音频转写开始：{label}")
            result = await asyncio.wait_for(
                self._call_prepared_route(route, audio_path),
                timeout=timeout_seconds + 10,
            )
            if result and result.has_text:
                if log_stage:
                    logger.debug(f"{LOG_PREFIX} 视频音频转写完成")
                return result
            if errors is not None:
                errors.append(f"{label}转写没有返回可用文字")
            return None
        except asyncio.TimeoutError:
            if errors is not None:
                errors.append(f"{label}转写超时")
            logger.debug(f"{LOG_PREFIX} 视频{label}转写超时")
            return None
        except AudioTranscriptError as exc:
            if errors is not None:
                errors.append(f"{label}转写失败：{exc}")
            logger.debug(f"{LOG_PREFIX} 视频{label}转写失败：{exc}")
            return None
        except Exception as exc:
            if errors is not None:
                errors.append(f"{label}转写失败：{exc}")
            logger.debug(f"{LOG_PREFIX} 视频{label}转写失败：{exc}")
            return None

    async def _call_prepared_route(self, route: str, audio_path: Path) -> TranscriptResult | None:
        if route == "local":
            return await transcribe_local_audio(
                audio_path,
                self._cache_dir(),
                config=self.local_asr_config,
                max_chars=self.max_chars,
            )
        return await transcribe_bcut_audio(
            audio_path,
            cache_dir=self._cache_dir(),
            timeout_seconds=self.audio_timeout_seconds,
            max_chars=self.max_chars,
        )

    def _transcript_routes(self) -> tuple[str, ...]:
        mode = self.audio_transcript_mode.strip().lower()
        if mode == "local":
            return ("local", "bcut")
        return ("bcut", "local")

    @property
    def transcript_enabled(self) -> bool:
        return bool(self._transcript_routes())

    def _cache_dir(self) -> Path:
        return sight_cache_dir(getattr(self.runtime, "data_path", None))

    @staticmethod
    def _from_transcript(result: TranscriptResult) -> SightTextResult:
        transcript = " ".join(str(result.full_text or "").split())
        metadata = dict(result.metadata or {})
        if result.language:
            metadata["language"] = result.language
        if result.segments:
            metadata["segments"] = len(result.segments)
            metadata["transcript_segments"] = [
                {
                    "start": float(segment.start or 0),
                    "end": float(segment.end or 0),
                    "text": " ".join(str(segment.text or "").split())[:500],
                }
                for segment in result.segments
                if str(segment.text or "").strip()
            ]
        return SightTextResult(
            transcript=transcript,
            transcript_source=result.source or "转写",
            metadata=metadata,
            raw_transcript=result,
        )
