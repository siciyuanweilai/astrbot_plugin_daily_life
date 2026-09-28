from __future__ import annotations

from typing import Any

import aiohttp

from ....config.options import VideoGenerationSettings
from ...base import LOG_PREFIX, image_data_url
from .size import video_aspect_ratio
from .wire import JsonRequester, LogWriter


def video_task_timeout_seconds(settings: VideoGenerationSettings) -> int:
    return max(int(settings.request_timeout_seconds), int(settings.timeout_seconds))


async def create_video_task(
    *,
    settings: VideoGenerationSettings,
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    endpoint: str,
    prompt: str,
    image_bytes: bytes | None,
    aspect_ratio: str = "",
    duration: int = 0,
    request: JsonRequester,
    log_info: LogWriter,
) -> Any:
    seconds = max(1, min(15, int(duration or settings.duration)))
    ratio = aspect_ratio or settings.aspect_ratio
    payload = video_task_payload(
        settings,
        prompt=prompt,
        image_bytes=image_bytes,
        aspect_ratio=ratio,
        seconds=seconds,
    )

    log_info(f"{LOG_PREFIX} 正在创建视频任务：{settings.model}")
    return await request(
        session,
        "POST",
        endpoint,
        dict(headers),
        json_body=payload,
        timeout_seconds=video_task_timeout_seconds(settings),
        operation="创建视频任务",
    )


def video_task_payload(
    settings: VideoGenerationSettings,
    *,
    prompt: str,
    image_bytes: bytes | None,
    aspect_ratio: str,
    seconds: int,
) -> dict[str, Any]:
    resolution = str(settings.resolution or "720p").strip().lower() or "720p"
    if resolution not in {"480p", "720p", "1080p"}:
        raise ValueError("Grok 视频分辨率仅支持 480p、720p 或 1080p")
    payload = {
        "model": settings.model,
        "prompt": prompt,
        "seconds": str(seconds),
        "aspect_ratio": video_aspect_ratio(aspect_ratio),
        "resolution": resolution,
    }
    if image_bytes:
        payload["input_reference"] = {"image_url": image_data_url(image_bytes)}
    return payload
