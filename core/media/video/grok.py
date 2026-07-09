from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import aiohttp
from astrbot.api import logger

from ...config.options import VideoGenerationSettings
from ..shared import GeneratedVideo, videos_endpoint
from .http import request_json, seconds_label, timeout_from_seconds
from .protocol import create_video_task
from .tasks import download_video, poll_video_url, resolve_video_task


class GrokVideoService:
    def __init__(self, settings: VideoGenerationSettings, data_dir: Path | None = None):
        self.settings = settings
        self.video_endpoint = videos_endpoint(settings.base_url)
        self.output_dir = Path(data_dir or tempfile.gettempdir()) / "generated" / "videos"
        self._key_index = 0

    async def generate_video(
        self,
        prompt: str,
        image_bytes: bytes | None = None,
        aspect_ratio: str = "",
        duration: int = 0,
    ) -> GeneratedVideo:
        prompt = self._validate_prompt(prompt)
        headers = self._headers()
        timeout = aiohttp.ClientTimeout(total=None)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            return await self._generate_video_task(
                session,
                headers,
                prompt,
                image_bytes,
                aspect_ratio=aspect_ratio,
                duration=duration,
            )

    def _validate_prompt(self, prompt: str) -> str:
        if not self.settings.enabled:
            raise RuntimeError("视频生成未启用")
        text = str(prompt or "").strip()
        if not text:
            raise ValueError("缺少视频提示词")
        if not self.video_endpoint:
            raise RuntimeError("Grok 视频生成缺少中转接口地址")
        if not self.settings.api_keys:
            raise RuntimeError("Grok 视频生成缺少接口密钥")
        return text

    async def _generate_video_task(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        prompt: str,
        image_bytes: bytes | None,
        *,
        aspect_ratio: str = "",
        duration: int = 0,
    ) -> GeneratedVideo:
        data = await create_video_task(
            settings=self.settings,
            session=session,
            headers=headers,
            endpoint=self.video_endpoint,
            prompt=prompt,
            image_bytes=image_bytes,
            aspect_ratio=aspect_ratio,
            duration=duration,
            request=self._request_json,
            log_info=logger.info,
        )
        return await self._resolve_task(
            session=session,
            headers=headers,
            endpoint=self.video_endpoint,
            data=data,
            id_label="任务",
        )

    async def _resolve_task(
        self,
        *,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        endpoint: str,
        data: Any,
        id_label: str,
    ) -> GeneratedVideo:
        return await resolve_video_task(
            session=session,
            headers=headers,
            endpoint=endpoint,
            data=data,
            id_label=id_label,
            poll_video_url=self._poll_video_url,
        )

    def _headers(self) -> dict[str, str]:
        key = self.settings.api_keys[self._key_index % len(self.settings.api_keys)]
        self._key_index = (self._key_index + 1) % len(self.settings.api_keys)
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @staticmethod
    def _timeout(seconds: float | int | None) -> aiohttp.ClientTimeout | None:
        return timeout_from_seconds(seconds)

    @staticmethod
    def _seconds_label(seconds: float | int | None) -> str:
        return seconds_label(seconds)

    async def _request_json(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        json_body: dict[str, Any] | None = None,
        data: Any = None,
        timeout_seconds: float | int | None = None,
        operation: str = "请求",
    ) -> Any:
        return await request_json(
            session,
            method,
            url,
            headers,
            json_body=json_body,
            data=data,
            timeout_seconds=timeout_seconds,
            operation=operation,
        )

    async def _poll_video_url(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        endpoint: str,
        request_id: str,
    ) -> str:
        return await poll_video_url(
            settings=self.settings,
            session=session,
            headers=headers,
            endpoint=endpoint,
            request_id=request_id,
            request=self._request_json,
            download=self._download_video,
            sleep=asyncio.sleep,
            log_debug=logger.debug,
            log_info=logger.info,
        )

    async def _download_video(
        self,
        session: aiohttp.ClientSession,
        url: str,
        headers: dict[str, str],
        request_id: str,
    ) -> str:
        return await download_video(
            settings=self.settings,
            output_dir=self.output_dir,
            session=session,
            url=url,
            headers=headers,
            request_id=request_id,
            timeout_factory=self._timeout,
            seconds_label=self._seconds_label,
            log_info=logger.info,
        )
