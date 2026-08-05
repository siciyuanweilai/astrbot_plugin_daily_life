from __future__ import annotations

from typing import Any

import aiohttp

from ....config.options import VideoGenerationSettings
from ...base import LOG_PREFIX, image_data_url
from .size import video_aspect_ratio, video_size
from .wire import JsonRequester, LogWriter

XAI_REQUEST_FORMAT = "xai"
LEGACY_REQUEST_FORMAT = "legacy"


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
    request_format: str = XAI_REQUEST_FORMAT,
    request: JsonRequester,
    log_info: LogWriter,
) -> Any:
    seconds = max(1, min(15, int(duration or settings.duration)))
    ratio = aspect_ratio or settings.aspect_ratio
    payload = video_task_payload(
        settings,
        endpoint=endpoint,
        prompt=prompt,
        image_bytes=image_bytes,
        aspect_ratio=ratio,
        seconds=seconds,
        request_format=request_format,
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
    endpoint: str,
    prompt: str,
    image_bytes: bytes | None,
    aspect_ratio: str,
    seconds: int,
    request_format: str,
) -> dict[str, Any]:
    if request_format == LEGACY_REQUEST_FORMAT:
        payload: dict[str, Any] = {
            "model": settings.model,
            "prompt": prompt,
            "seconds": str(seconds),
            "size": video_size(aspect_ratio, settings.resolution),
            "n": 1,
        }
        if image_bytes:
            payload["image"] = image_data_url(image_bytes)
        return payload

    resolution = str(settings.resolution or "720p").strip().lower() or "720p"
    payload = {
        "model": settings.model,
        "prompt": prompt,
        "aspect_ratio": video_aspect_ratio(aspect_ratio),
        "resolution": resolution,
    }
    if _official_generation_endpoint(endpoint):
        payload["duration"] = seconds
        if image_bytes:
            payload["image"] = {"url": image_data_url(image_bytes)}
    else:
        payload["seconds"] = str(seconds)
        payload["resolution"] = resolution.upper()
        if image_bytes:
            payload["image"] = image_data_url(image_bytes)
    return payload


def _official_generation_endpoint(endpoint: str) -> bool:
    return str(endpoint or "").rstrip("/").lower().endswith("/v1/videos/generations")
