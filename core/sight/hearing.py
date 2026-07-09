from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import aiohttp
from astrbot.api import logger

from .codec import ffmpeg_executable, ytdlp_ffmpeg_location
from .cookie import BiliCookieJar
from .probe import clean_source
from .sample import resolve_sample_source, source_fingerprint
from .transcript import TranscriptResult, TranscriptSegment
from .stash import write_transcript_cache
from ..runtime.markers import LOG_PREFIX


API_BASE = "https://member.bilibili.com/x/bcut/rubick-interface"
MAX_AUDIO_BYTES = 50 * 1024 * 1024
POLL_INTERVAL_SECONDS = 1.2
AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".webm"}


class AudioTranscriptError(RuntimeError):
    pass


async def transcribe_bcut(
    source: str,
    cache_dir: Path,
    *,
    timeout_seconds: int = 240,
    max_chars: int = 8000,
) -> TranscriptResult | None:
    audio_path = await prepare_audio_source(source, cache_dir)
    if not audio_path:
        return None
    return await transcribe_bcut_audio(audio_path, cache_dir=cache_dir, timeout_seconds=timeout_seconds, max_chars=max_chars)


async def transcribe_bcut_audio(
    audio_path: Path,
    *,
    cache_dir: Path | None = None,
    timeout_seconds: int = 240,
    max_chars: int = 8000,
) -> TranscriptResult | None:
    audio_path = _checked_audio_path(audio_path)
    transcriber = BcutTranscriber(timeout_seconds=timeout_seconds, max_chars=max_chars)
    result = await transcriber.transcribe(audio_path)
    if result and result.has_text and cache_dir is not None:
        write_transcript_cache(cache_dir, "bcut", audio_path, result, raw_payload=transcriber.last_payload)
    return result


async def prepare_audio_source(source: str, cache_dir: Path, *, cookiefile: Path | None = None) -> Path | None:
    audio_path = await download_audio_with_ytdlp(source, cache_dir, cookiefile=cookiefile)
    if audio_path:
        return _checked_audio_path(audio_path)

    source_path = await resolve_sample_source(source, cache_dir)
    if not source_path:
        logger.debug(f"{LOG_PREFIX} 未找到可用的视频源")
        return None

    audio_path = await extract_audio(source_path, cache_dir)
    if not audio_path:
        logger.debug(f"{LOG_PREFIX} 视频音频提取失败，已跳过")
        return None

    return _checked_audio_path(audio_path)


def _checked_audio_path(path: Path) -> Path:
    if path.stat().st_size > MAX_AUDIO_BYTES:
        raise AudioTranscriptError("音频文件过大")
    return path


async def extract_audio(source: Path, cache_dir: Path) -> Path | None:
    target_dir = cache_dir / "audio"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source_fingerprint(str(source))}.mp3"
    if target.is_file() and target.stat().st_size > 0:
        return target
    ok = await asyncio.to_thread(_extract_audio_sync, source, target)
    return target if ok else None


def _extract_audio_sync(source: Path, target: Path) -> bool:
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return False
    target.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(target),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=90,
        )
    except Exception:
        return False
    return result.returncode == 0 and target.is_file() and target.stat().st_size > 0


def _yt_dlp_module() -> Any:
    try:
        return importlib.import_module("yt_dlp")
    except Exception:
        return None


def _existing_audio(target_dir: Path, fingerprint: str) -> Path | None:
    for path in sorted(target_dir.glob(f"{fingerprint}.*")):
        if path.suffix.lower() in AUDIO_SUFFIXES and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _downloaded_audio_path(info: dict[str, Any], target_dir: Path, fingerprint: str) -> Path | None:
    candidates: list[Path] = []
    for item in list(info.get("requested_downloads") or []):
        if not isinstance(item, dict):
            continue
        for key in ("filepath", "filename"):
            value = str(item.get(key) or "").strip()
            if value:
                candidates.append(Path(value))
    for key in ("filepath", "_filename", "filename"):
        value = str(info.get(key) or "").strip()
        if value:
            candidates.append(Path(value))
    candidates.extend(sorted(target_dir.glob(f"{fingerprint}.*")))
    for path in candidates:
        if path.suffix.lower() in AUDIO_SUFFIXES and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _download_audio_with_ytdlp_sync(
    source: str,
    target_dir: Path,
    fingerprint: str,
    cookiefile: Path | None = None,
) -> Path | None:
    yt_dlp = _yt_dlp_module()
    if yt_dlp is None:
        return None

    options: dict[str, Any] = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(target_dir / f"{fingerprint}.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }
        ],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 1,
        "fragment_retries": 1,
        "max_filesize": MAX_AUDIO_BYTES * 2,
    }
    ffmpeg = ytdlp_ffmpeg_location()
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    if cookiefile and cookiefile.is_file():
        options["cookiefile"] = str(cookiefile)

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(source, download=True) or {}
    except Exception as exc:
        logger.debug(f"{LOG_PREFIX} 音频下载失败：{str(exc)[:160]}")
        return None
    return _downloaded_audio_path(info if isinstance(info, dict) else {}, target_dir, fingerprint)


async def download_audio_with_ytdlp(source: str, cache_dir: Path, *, cookiefile: Path | None = None) -> Path | None:
    source = clean_source(source)
    if not source.startswith(("http://", "https://")):
        return None

    target_dir = cache_dir / "audio"
    target_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = source_fingerprint(source)

    cached = _existing_audio(target_dir, fingerprint)
    if cached:
        return cached

    if cookiefile is None:
        cookiefile = BiliCookieJar(cache_dir).ensure_cookiefile()
    return await asyncio.to_thread(_download_audio_with_ytdlp_sync, source, target_dir, fingerprint, cookiefile)


class BcutTranscriber:
    def __init__(self, *, timeout_seconds: int = 240, max_chars: int = 8000):
        self.timeout_seconds = max(30, int(timeout_seconds or 240))
        self.max_chars = max(500, int(max_chars or 8000))
        self.last_payload: dict[str, Any] | None = None

    async def transcribe(self, audio_path: Path) -> TranscriptResult | None:
        deadline = time.monotonic() + self.timeout_seconds
        timeout = aiohttp.ClientTimeout(total=max(30, self.timeout_seconds))
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            upload = await self._create_upload(session, audio_path)
            await self._upload_chunks(session, audio_path, upload)
            committed = await self._commit_upload(session, upload)
            task_id = await self._create_task(session, str(committed.get("download_url") or ""))
            payload = await self._await_result(session, task_id, deadline)
        self.last_payload = payload
        return _transcript_from_bcut(payload, max_chars=self.max_chars)

    async def _create_upload(self, session: aiohttp.ClientSession, audio_path: Path) -> dict[str, Any]:
        suffix = audio_path.suffix.lstrip(".").lower() or "mp3"
        return await self._post_json(
            session,
            f"{API_BASE}/resource/create",
            {
                "type": 2,
                "name": audio_path.name,
                "size": audio_path.stat().st_size,
                "ResourceFileType": suffix,
                "model_id": "8",
            },
        )

    async def _upload_chunks(self, session: aiohttp.ClientSession, audio_path: Path, upload: dict[str, Any]) -> None:
        urls = list(upload.get("upload_urls") or [])
        per_size = int(upload.get("per_size") or 0)
        if not urls or per_size <= 0:
            raise AudioTranscriptError("分片上传参数无效")

        data = await asyncio.to_thread(audio_path.read_bytes)
        etags: list[str] = []
        for index, url in enumerate(urls):
            start = index * per_size
            chunk = data[start : min(start + per_size, len(data))]
            async with session.put(str(url), data=chunk, headers={"Content-Type": "application/octet-stream"}) as response:
                if int(response.status or 0) >= 400:
                    raise AudioTranscriptError(f"分片上传失败，HTTP {response.status}")
                etags.append(str(response.headers.get("Etag") or "").strip('"'))
        upload["Etags"] = ",".join(etags)

    async def _commit_upload(self, session: aiohttp.ClientSession, upload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_json(
            session,
            f"{API_BASE}/resource/create/complete",
            {
                "InBossKey": upload.get("in_boss_key"),
                "ResourceId": upload.get("resource_id"),
                "Etags": upload.get("Etags", ""),
                "UploadId": upload.get("upload_id"),
                "model_id": "8",
            },
        )

    async def _create_task(self, session: aiohttp.ClientSession, download_url: str) -> str:
        if not download_url:
            raise AudioTranscriptError("音频转写任务缺少下载地址")
        data = await self._post_json(session, f"{API_BASE}/task", {"resource": download_url, "model_id": "8"})
        task_id = str(data.get("task_id") or "")
        if not task_id:
            raise AudioTranscriptError("音频转写任务创建失败")
        return task_id

    async def _await_result(self, session: aiohttp.ClientSession, task_id: str, deadline: float) -> dict[str, Any]:
        while time.monotonic() < deadline:
            async with session.get(f"{API_BASE}/task/result", params={"model_id": 7, "task_id": task_id}) as response:
                if int(response.status or 0) >= 400:
                    raise AudioTranscriptError(f"音频转写查询失败：HTTP {response.status}")
                payload = await response.json(content_type=None)
            if payload.get("code") != 0:
                raise AudioTranscriptError(str(payload.get("message") or "音频转写查询失败"))
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            state = data.get("state")
            if state == 4:
                return data
            if state == 3:
                raise AudioTranscriptError("音频转写任务失败")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        raise AudioTranscriptError("音频转写超时")

    async def _post_json(self, session: aiohttp.ClientSession, url: str, body: dict[str, Any]) -> dict[str, Any]:
        async with session.post(url, data=json.dumps(body), headers=_headers()) as response:
            if int(response.status or 0) >= 400:
                raise AudioTranscriptError(f"音频转写请求失败：HTTP {response.status}")
            payload = await response.json(content_type=None)
        if payload.get("code") != 0:
            raise AudioTranscriptError(str(payload.get("message") or "音频转写请求失败"))
        data = payload.get("data")
        return data if isinstance(data, dict) else {}


def _transcript_from_bcut(payload: dict[str, Any], *, max_chars: int) -> TranscriptResult | None:
    raw_result = payload.get("result")
    if isinstance(raw_result, str):
        try:
            raw = json.loads(raw_result)
        except json.JSONDecodeError:
            return None
    elif isinstance(raw_result, dict):
        raw = raw_result
    else:
        return None

    segments: list[TranscriptSegment] = []
    pieces: list[str] = []
    total_chars = 0
    for item in list(raw.get("utterances") or []):
        text = " ".join(str(item.get("transcript") or "").split())
        if not text:
            continue
        start = _milliseconds(item.get("start_time"))
        end = _milliseconds(item.get("end_time"))
        segments.append(TranscriptSegment(start=start, end=end, text=text[:300]))
        pieces.append(text)
        total_chars += len(text)
        if total_chars >= max_chars:
            break

    full_text = " ".join(pieces)[:max_chars]
    if not full_text and not segments:
        return None
    return TranscriptResult(
        language=str(raw.get("language") or "zh"),
        full_text=full_text,
        segments=tuple(segments),
        metadata={"segments": len(segments)},
        source="必剪转写",
    )


def _milliseconds(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0) / 1000.0)
    except (TypeError, ValueError):
        return 0.0


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "Bilibili/1.0.0 (https://www.bilibili.com)",
        "Content-Type": "application/json",
    }
