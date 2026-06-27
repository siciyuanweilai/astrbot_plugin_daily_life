from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import aiohttp
from astrbot.api import logger

from ..config.options import DEFAULT_VOICE_FORMAT, DEFAULT_VOICE_SPEED, VoiceGenerationSettings
from .shared import LOG_PREFIX, GeneratedVoice, emotion_category_label, normalize_emotion_category


class SiliconFlowVoiceService:
    def __init__(self, settings: VoiceGenerationSettings, data_dir: Path):
        self.settings = settings
        self.output_dir = data_dir / "generated_media" / "voices"
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def synthesize(self, text: str, *, emotion: str = "", emotion_category: str = "") -> GeneratedVoice:
        if not self.settings.enabled:
            raise RuntimeError("语音生成未启用")
        text = str(text or "").strip()
        if not text:
            raise ValueError("缺少语音文本")
        if not self.settings.api_key:
            raise RuntimeError("SiliconFlow 语音生成缺少接口密钥")
        route = self._voice_route(emotion, emotion_category)
        if not route["voice"]:
            raise RuntimeError("SiliconFlow 语音生成缺少音色")

        path = self._cache_path(text, route)
        if path.exists() and path.stat().st_size > 0:
            return GeneratedVoice(path)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "voice": route["voice"],
            "input": text,
            "response_format": DEFAULT_VOICE_FORMAT,
            "speed": route["speed"],
        }

        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Any = ""
        for attempt in range(self.settings.max_retries + 1):
            try:
                session = await self._get_session()
                async with session.post(f"{self.settings.api_url}/audio/speech", headers=headers, json=payload) as response:
                    content_type = str(response.headers.get("Content-Type", "") or "").lower()
                    if 200 <= response.status < 300 and (
                        content_type.startswith("audio/") or content_type.startswith("application/octet-stream")
                    ):
                        data = await response.read()
                        if not data:
                            raise RuntimeError("接口返回空音频")
                        await asyncio.to_thread(path.write_bytes, data)
                        speed_note = "" if route["speed"] == DEFAULT_VOICE_SPEED else f"，语速={route['speed']}"
                        logger.info(
                            f"{LOG_PREFIX} 语音生成完成：{path.name}，情绪={route['emotion']}，"
                            f"分类={emotion_category_label(route['emotion_category'])}{speed_note}"
                        )
                        return GeneratedVoice(path)
                    try:
                        last_error = await response.json()
                    except Exception:
                        last_error = await response.text()
                    if response.status not in {429, 500, 502, 503, 504}:
                        break
            except Exception as exc:
                last_error = exc
            if attempt < self.settings.max_retries:
                await asyncio.sleep(min(2**attempt, 8))
        if path.exists() and path.stat().st_size <= 0:
            path.unlink(missing_ok=True)
        raise RuntimeError(f"SiliconFlow 语音生成失败：{str(last_error)[:300]}")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.settings.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _voice_route(self, emotion: str = "", emotion_category: str = "") -> dict[str, Any]:
        raw_label = str(emotion or "").strip()
        category_label = normalize_emotion_category(emotion_category)
        display_label = raw_label or category_label or "未裁定"
        lookup_labels = []
        for label in (raw_label, category_label):
            if label and label not in lookup_labels:
                lookup_labels.append(label)

        voice = ""
        for label in lookup_labels:
            voice = str(self.settings.emotion_voice_map.get(label) or "").strip()
            if voice:
                break
        voice = voice or str(self.settings.voice).strip()

        speed = None
        for label in lookup_labels:
            if label in self.settings.emotion_speed_map:
                speed = float(self.settings.emotion_speed_map[label])
                break
        if speed is None:
            speed = DEFAULT_VOICE_SPEED
        return {
            "emotion": display_label,
            "emotion_category": category_label,
            "voice": voice,
            "speed": max(0.25, min(4.0, speed)),
        }

    def _cache_path(self, text: str, route: dict[str, Any]) -> Path:
        key = hashlib.sha256(
            json.dumps(
                {
                    "text": text,
                    "emotion": route["emotion"],
                    "emotion_category": route["emotion_category"],
                    "model": self.settings.model,
                    "voice": route["voice"],
                    "speed": route["speed"],
                    "format": DEFAULT_VOICE_FORMAT,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return self.output_dir / f"siliconflow_{key}.{DEFAULT_VOICE_FORMAT}"
