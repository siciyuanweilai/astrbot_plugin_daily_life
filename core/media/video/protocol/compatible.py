from __future__ import annotations

from typing import Any

import aiohttp

from ....config.options import VideoGenerationSettings
from ...shared import LOG_PREFIX, image_data_url
from .size import video_size
from .wire import JsonRequester, LogWriter


def compatible_create_timeout_seconds(settings: VideoGenerationSettings) -> int:
    return max(int(settings.request_timeout_seconds), int(settings.timeout_seconds))


async def create_compatible_task(
    *,
    settings: VideoGenerationSettings,
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    endpoint: str,
    prompt: str,
    image_bytes: bytes | None,
    request: JsonRequester,
    log_info: LogWriter,
) -> Any:
    payload = {
        "model": settings.model,
        "prompt": prompt,
        "seconds": str(settings.duration),
        "size": video_size(settings.aspect_ratio, settings.resolution),
        "n": 1,
    }
    if image_bytes:
        payload["image"] = image_data_url(image_bytes)

    log_info(f"{LOG_PREFIX} 正在通过 /v1/videos 兼容协议创建视频任务：{settings.model}")
    return await request(
        session,
        "POST",
        endpoint,
        dict(headers),
        json_body=payload,
        timeout_seconds=compatible_create_timeout_seconds(settings),
        operation="兼容协议创建任务",
    )
