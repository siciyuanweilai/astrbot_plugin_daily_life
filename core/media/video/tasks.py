from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import aiohttp

from ...config.options import VideoGenerationSettings
from ..shared import (
    LOG_PREFIX,
    GeneratedVideo,
    extract_content_url,
    extract_request_id,
    extract_video_url,
    origin_from_url,
    upstream_error_text,
)
from .errors import VideoRequestTimeout, VideoTaskError

JsonRequester = Callable[..., Awaitable[Any]]
VideoDownloader = Callable[[aiohttp.ClientSession, str, dict[str, str], str], Awaitable[str]]
SleepFunc = Callable[[float], Awaitable[None]]
LogWriter = Callable[[str], None]

_DONE = {"done", "completed", "succeeded", "success"}
_FAILED = {"failed", "expired", "error", "cancelled", "canceled", "rejected"}


def task_status_url(endpoint: str, request_id: str) -> str:
    return f"{endpoint.rstrip('/')}/{quote(str(request_id), safe='')}"


def status_label(status: str) -> str:
    labels = {
        "created": "已创建",
        "starting": "启动中",
        "queued": "排队中",
        "pending": "等待中",
        "in_progress": "生成中",
        "processing": "生成中",
        "generating": "生成中",
        "running": "生成中",
        "working": "生成中",
        "done": "已完成",
        "completed": "已完成",
        "succeeded": "已完成",
        "success": "已完成",
        "failed": "失败",
        "expired": "已过期",
        "error": "错误",
        "cancelled": "已取消",
        "canceled": "已取消",
        "rejected": "已拒绝",
    }
    text = str(status or "").strip().lower()
    return labels.get(text) or text or "未知"


async def resolve_video_task(
    *,
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    endpoint: str,
    data: Any,
    id_label: str,
    poll_video_url: Callable[[aiohttp.ClientSession, dict[str, str], str, str], Awaitable[str]],
) -> GeneratedVideo:
    origin = origin_from_url(endpoint)
    direct_url = extract_video_url(data, origin)
    if direct_url:
        return GeneratedVideo(direct_url)
    request_id = extract_request_id(data)
    if not request_id:
        raise RuntimeError(f"Grok 视频生成未返回{id_label}标识：{upstream_error_text(data)}")
    return GeneratedVideo(await poll_video_url(session, headers, endpoint, request_id))


async def poll_video_url(
    *,
    settings: VideoGenerationSettings,
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    endpoint: str,
    request_id: str,
    request: JsonRequester,
    download: VideoDownloader,
    sleep: SleepFunc,
    log_debug: LogWriter,
    log_info: LogWriter,
) -> str:
    status_url = task_status_url(endpoint, request_id)
    origin = origin_from_url(endpoint)
    deadline = time.monotonic() + settings.timeout_seconds
    last_logged_status = ""
    last_log_at = 0.0
    while True:
        if time.monotonic() >= deadline:
            raise VideoTaskError(f"Grok 视频任务超时：{request_id}")
        await sleep(settings.poll_interval_seconds)
        try:
            data = await request(
                session,
                "GET",
                status_url,
                headers,
                timeout_seconds=settings.request_timeout_seconds,
                operation="查询任务状态",
            )
        except VideoRequestTimeout as exc:
            log_debug(f"{LOG_PREFIX} 视频任务查询超时，继续等待：{exc}")
            continue
        video_url = extract_video_url(data, origin)
        status = str(data.get("status") or "").strip().lower() if isinstance(data, dict) else ""
        if video_url and (not status or status in _DONE):
            log_info(f"{LOG_PREFIX} 视频生成完成：{request_id}")
            return video_url
        if status in _FAILED:
            raise VideoTaskError(f"Grok 视频任务失败：{request_id}，{upstream_error_text(data)}")
        if status in _DONE:
            content_url = extract_content_url(data, origin)
            if content_url:
                return await download(session, content_url, headers, request_id)
            raise VideoTaskError(f"Grok 视频任务完成但没有视频地址：{request_id}")
        now = time.monotonic()
        if status != last_logged_status or now - last_log_at >= 60:
            log_debug(f"{LOG_PREFIX} 等待视频生成任务：{request_id}，状态：{status_label(status)}")
            last_logged_status = status
            last_log_at = now


async def download_video(
    *,
    settings: VideoGenerationSettings,
    output_dir: Path,
    session: aiohttp.ClientSession,
    url: str,
    headers: dict[str, str],
    request_id: str,
    timeout_factory: Callable[[float | int | None], aiohttp.ClientTimeout | None],
    seconds_label: Callable[[float | int | None], str],
    log_info: LogWriter,
) -> str:
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    path = output_dir / f"grok_{request_id}_{uuid.uuid4().hex[:8]}.mp4"
    tmp_path = path.with_suffix(".mp4.part")
    download_headers = {"Accept": "video/*,application/octet-stream"}
    auth = str(headers.get("Authorization") or "").strip()
    if auth:
        download_headers["Authorization"] = auth
    timeout_seconds = max(settings.request_timeout_seconds, 300)
    try:
        async with session.get(
            url,
            headers=download_headers,
            timeout=timeout_factory(timeout_seconds),
        ) as response:
            if response.status >= 400:
                detail = await response.text()
                raise RuntimeError(f"Grok 视频内容下载失败（HTTP {response.status}）：{detail[:300]}")
            with tmp_path.open("wb") as file:
                async for chunk in response.content.iter_chunked(1024 * 256):
                    if chunk:
                        await asyncio.to_thread(file.write, chunk)
    except asyncio.TimeoutError as exc:
        raise VideoRequestTimeout(
            f"Grok 视频内容下载超时（{seconds_label(timeout_seconds)} 秒）：{url}"
        ) from exc
    await asyncio.to_thread(tmp_path.replace, path)
    log_info(f"{LOG_PREFIX} 视频内容已下载：{path.name}")
    return str(path)
