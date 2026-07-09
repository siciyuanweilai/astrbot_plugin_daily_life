from __future__ import annotations

import asyncio
import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from astrbot.api import logger

from .auth import browser_headers
from .bili import fetch_bili_metadata, resolve_bili_target, target_from_text
from .codec import ffmpeg_executable, ffprobe_executable
from .cookie import BiliCookieJar
from .probe import VIDEO_SUFFIXES, clean_source
from ..runtime.markers import LOG_PREFIX


MAX_REMOTE_VIDEO_BYTES = 500 * 1024 * 1024
BILI_SAMPLE_VIDEO_QN = 32
FALLBACK_FRAME_SECONDS = (0.5, 2.0, 5.0, 8.0, 12.0, 18.0, 25.0, 35.0)
MEDIA_CONTENT_TYPES = (
    "video/",
    "application/octet-stream",
    "application/x-mpegurl",
    "application/vnd.apple.mpegurl",
)
PAGE_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
)


@dataclass(slots=True)
class SightFrame:
    path: Path
    second: float = 0.0
    label: str = "00:00"


def sight_cache_dir(data_path: object) -> Path:
    base = Path(data_path or tempfile.gettempdir()).expanduser().resolve()
    if base.suffix:
        base = base.parent
    target = base / "sight"
    target.mkdir(parents=True, exist_ok=True)
    return target


def source_fingerprint(source: str) -> str:
    return hashlib.sha256(str(source or "").encode("utf-8", errors="ignore")).hexdigest()[:32]


def local_video_path(source: str) -> Path | None:
    text = clean_source(source)
    if not text or text.startswith(("http://", "https://", "data:")):
        return None
    path = Path(text).expanduser()
    return path if path.is_file() else None


def _suffix_for_remote(source: str) -> str:
    suffix = Path(urlparse(source).path).suffix.lower()
    return suffix if suffix in VIDEO_SUFFIXES else ".mp4"


def _remote_has_video_suffix(source: str) -> bool:
    return Path(urlparse(source).path).suffix.lower() in VIDEO_SUFFIXES


def _content_type(value: object) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _looks_like_media_content_type(value: str) -> bool:
    content_type = _content_type(value)
    return any(content_type.startswith(prefix) for prefix in MEDIA_CONTENT_TYPES)


def _looks_like_page_content_type(value: str) -> bool:
    content_type = _content_type(value)
    return any(content_type.startswith(prefix) for prefix in PAGE_CONTENT_TYPES)


def _looks_like_media_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(32)
    except OSError:
        return False
    return (
        (len(header) >= 12 and header[4:8] == b"ftyp")
        or header.startswith((b"\x1aE\xdf\xa3", b"FLV", b"RIFF", b"\x00\x00\x01\xba"))
    )


def _bytes_text(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _log_video_prepare_failed(kind: str, reason: str) -> None:
    detail = str(reason or "未知原因").strip()
    logger.debug(f"{LOG_PREFIX} 视频文件准备失败：{kind}，{detail}")


async def download_remote_video(
    source: str,
    cache_dir: Path,
    *,
    headers: dict[str, str] | None = None,
    max_bytes: int = MAX_REMOTE_VIDEO_BYTES,
    timeout_seconds: int = 240,
) -> Path | None:
    path, _reason = await _download_remote_video_with_reason(
        source,
        cache_dir,
        headers=headers,
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
    )
    return path


async def _download_remote_video_with_reason(
    source: str,
    cache_dir: Path,
    *,
    headers: dict[str, str] | None = None,
    max_bytes: int = MAX_REMOTE_VIDEO_BYTES,
    timeout_seconds: int = 240,
) -> tuple[Path | None, str]:
    source = clean_source(source)
    if not source.startswith(("http://", "https://")):
        return None, "不是远程视频地址"
    media_dir = cache_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    has_video_suffix = _remote_has_video_suffix(source)
    target = media_dir / f"{source_fingerprint(source)}{_suffix_for_remote(source)}"
    if target.is_file() and target.stat().st_size > 0 and (has_video_suffix or _looks_like_media_file(target)):
        return target, ""

    timeout = aiohttp.ClientTimeout(total=max(30, min(int(timeout_seconds or 240), 600)))
    written = 0
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            request_headers = dict(headers or {})
            request_headers["Accept"] = "video/*,application/octet-stream,*/*"
            async with session.get(source, headers=request_headers) as response:
                status = int(getattr(response, "status", 0) or 0)
                if status >= 400:
                    return None, f"HTTP {status}"
                response_headers = getattr(response, "headers", {}) or {}
                content_type = _content_type(response_headers.get("Content-Type"))
                if _looks_like_page_content_type(content_type):
                    return None, f"返回内容不是视频（{content_type or '未知类型'}）"
                if not has_video_suffix and content_type and not _looks_like_media_content_type(content_type):
                    return None, f"返回内容类型不可用（{content_type}）"
                if not has_video_suffix and not content_type:
                    return None, "返回内容类型未知"
                try:
                    content_length = int(response_headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    content_length = 0
                if max_bytes > 0 and content_length > max_bytes:
                    return None, f"视频文件超过大小上限（{_bytes_text(content_length)} > {_bytes_text(max_bytes)}）"
                too_large = False
                with target.open("wb") as handle:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if max_bytes > 0 and written > max_bytes:
                            too_large = True
                            break
                        await asyncio.to_thread(handle.write, chunk)
                if too_large:
                    await asyncio.to_thread(target.unlink, missing_ok=True)
                    return None, f"视频文件超过大小上限（>{_bytes_text(max_bytes)}）"
    except Exception as exc:
        await asyncio.to_thread(target.unlink, missing_ok=True)
        return None, f"下载异常：{type(exc).__name__}"
    if target.is_file() and target.stat().st_size > 0:
        return target, ""
    return None, "下载后文件为空"




async def resolve_sample_source(
    source: str,
    cache_dir: Path,
    *,
    max_bytes: int = MAX_REMOTE_VIDEO_BYTES,
    timeout_seconds: int = 240,
) -> Path | None:
    local = local_video_path(source)
    if local:
        return local
    lowered = clean_source(source).lower()
    if "bilibili.com" in lowered or "b23.tv" in lowered:
        cookiefile = BiliCookieJar(cache_dir).ensure_cookiefile()
        bili = await _download_bili_direct_video(
            source,
            cache_dir,
            cookiefile=cookiefile,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )
        return bili
    direct, reason = await _download_remote_video_with_reason(
        source,
        cache_dir,
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
    )
    if direct:
        return direct
    if clean_source(source).startswith(("http://", "https://")):
        _log_video_prepare_failed("远程视频", reason)
    return None


async def _download_bili_direct_video(
    source: str,
    cache_dir: Path,
    *,
    cookiefile: Path | None = None,
    max_bytes: int = MAX_REMOTE_VIDEO_BYTES,
    timeout_seconds: int = 240,
) -> Path | None:
    target = target_from_text(source, source="video")
    if not target:
        _log_video_prepare_failed("B站视频", "没有识别到视频编号")
        return None
    resolved = await resolve_bili_target(target, timeout_seconds=10)
    if not resolved or not resolved.bvid:
        _log_video_prepare_failed("B站视频", "没有解析到有效视频地址")
        return None
    cookies = BiliCookieJar(cache_dir).get()
    if cookiefile and cookiefile.is_file():
        try:
            text = cookiefile.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and parts[5] and parts[6]:
                cookies[parts[5]] = parts[6]
    metadata = await fetch_bili_metadata(resolved, timeout_seconds=10, cookies=cookies or None)
    cid = 0
    if metadata and metadata.cid:
        cid = metadata.cid
    if not cid:
        cid = await _bili_pagelist_cid(resolved.bvid, resolved.page or 1, resolved.canonical_url, cookies)
    if not cid:
        _log_video_prepare_failed("B站视频", "没有获取到视频 cid")
        return None
    direct = await _bili_playurl_direct_url(resolved.bvid, cid, resolved.canonical_url, cookies)
    if not direct:
        _log_video_prepare_failed("B站视频", "没有获取到可下载视频地址")
        return None
    path, reason = await _download_remote_video_with_reason(
        direct,
        cache_dir,
        headers=browser_headers(cookies, referer=resolved.canonical_url),
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
    )
    if not path:
        _log_video_prepare_failed("B站视频", reason)
    return path


async def _bili_pagelist_cid(bvid: str, page: int, referer: str, cookies: dict[str, str]) -> int:
    bvid = str(bvid or "").strip()
    if not bvid:
        return 0
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout, headers=browser_headers(cookies, referer=referer)) as session:
        async with session.get(
            "https://api.bilibili.com/x/player/pagelist",
            params={"bvid": bvid},
        ) as response:
            if int(getattr(response, "status", 0) or 0) >= 400:
                return 0
            payload = await response.json(content_type=None)
    pages = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else []
    if not pages:
        return 0
    index = max(0, min(int(page or 1) - 1, len(pages) - 1))
    item = pages[index] if isinstance(pages[index], dict) else {}
    try:
        return int(item.get("cid") or 0)
    except (TypeError, ValueError):
        return 0


async def _bili_playurl_direct_url(bvid: str, cid: int, referer: str, cookies: dict[str, str]) -> str | None:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout, headers=browser_headers(cookies, referer=referer)) as session:
        async with session.get(
            "https://api.bilibili.com/x/player/playurl",
            params={
                "cid": cid,
                "bvid": bvid,
                "qn": BILI_SAMPLE_VIDEO_QN,
                "fnver": 0,
                "fnval": 0,
                "fourk": 0,
                "otype": "json",
                "platform": "html5",
                "high_quality": 0,
            },
        ) as response:
            if int(getattr(response, "status", 0) or 0) >= 400:
                return None
            payload = await response.json(content_type=None)
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
    if not data:
        return None
    if isinstance(data.get("durl"), list) and data["durl"]:
        first = data["durl"][0] if isinstance(data["durl"][0], dict) else {}
        return str(first.get("url") or "").strip() or None
    dash = data.get("dash") if isinstance(data.get("dash"), dict) else {}
    if dash:
        best = _best_dash_video(dash)
        if best:
            return str(best.get("baseUrl") or best.get("base_url") or "").strip() or None
    return None


def _best_dash_video(dash_obj: dict[str, Any]) -> dict[str, Any] | None:
    videos = dash_obj.get("video") if isinstance(dash_obj.get("video"), list) else []
    best: dict[str, Any] | None = None
    best_qn = -1
    for item in videos:
        if not isinstance(item, dict):
            continue
        try:
            qn = int(item.get("id") or 0)
        except (TypeError, ValueError):
            qn = 0
        url = str(item.get("baseUrl") or item.get("base_url") or "").strip()
        if url and qn >= best_qn:
            best = item
            best_qn = qn
    return best


def _format_second(second: float) -> str:
    second = max(0.0, float(second or 0.0))
    minutes = int(second // 60)
    seconds = int(second % 60)
    return f"{minutes:02d}:{seconds:02d}"


def _safe_label(second: float) -> str:
    return _format_second(second).replace(":", "_")


def _video_duration(ffprobe: str, source: Path) -> float:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=15,
        )
        duration = float(str(result.stdout or "").strip() or 0.0)
    except Exception:
        return 0.0
    return duration if duration > 0 else 0.0


def select_frame_seconds(duration: float, max_frames: int) -> list[float]:
    count = max(1, int(max_frames or 8))
    duration = float(duration or 0.0)
    if duration <= 0:
        return [second for second in FALLBACK_FRAME_SECONDS[:count]]
    if duration <= count:
        return [min(duration * 0.5, 0.5)]
    step = duration / count
    seconds = [min(duration - 0.2, max(0.2, step * (index + 0.5))) for index in range(count)]
    result: list[float] = []
    for second in seconds:
        rounded = round(second, 2)
        if not result or abs(rounded - result[-1]) >= 0.75:
            result.append(rounded)
    return result or [min(duration * 0.5, 0.5)]


def _extract_frame(ffmpeg: str, source: Path, target: Path, second: float) -> bool:
    target.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{second:.2f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        str(target),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=20,
        )
    except Exception:
        return False
    return result.returncode == 0 and target.is_file() and target.stat().st_size > 0


def _file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return ""
    return hasher.hexdigest()


async def prepare_sample_video_source(
    source: str,
    cache_dir: Path,
    *,
    max_video_mb: int = 500,
    download_timeout_seconds: int = 240,
) -> Path | None:
    max_video_mb = max(0, int(max_video_mb))
    max_bytes = max_video_mb * 1024 * 1024
    return await resolve_sample_source(
        source,
        cache_dir,
        max_bytes=max_bytes,
        timeout_seconds=download_timeout_seconds,
    )


async def extract_video_frames(source_path: Path, cache_dir: Path, max_frames: int = 8) -> list[SightFrame]:
    if not source_path:
        return []
    ffmpeg = ffmpeg_executable()
    ffprobe = ffprobe_executable()
    if not ffmpeg:
        logger.debug(f"{LOG_PREFIX} 视频抽帧失败：未找到 ffmpeg")
        return []

    frame_dir = cache_dir / "frames" / source_fingerprint(str(source_path))
    frame_dir.mkdir(parents=True, exist_ok=True)
    duration = await asyncio.to_thread(_video_duration, ffprobe, source_path) if ffprobe else 0.0
    frames: list[SightFrame] = []
    seen_hashes: set[str] = set()
    for index, second in enumerate(select_frame_seconds(duration, max_frames), start=1):
        label = _format_second(second)
        target = frame_dir / f"frame_{index:02d}_{_safe_label(second)}.jpg"
        ok = await asyncio.to_thread(_extract_frame, ffmpeg, source_path, target, second)
        if not ok:
            continue
        digest = await asyncio.to_thread(_file_hash, target)
        if digest and digest in seen_hashes:
            target.unlink(missing_ok=True)
            continue
        if digest:
            seen_hashes.add(digest)
        frames.append(SightFrame(path=target, second=second, label=label))
    return frames
