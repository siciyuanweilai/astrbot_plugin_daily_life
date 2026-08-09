from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import importlib
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger

from ..config.options import (
    DEFAULT_VOLCENGINE_FORMAT,
    DEFAULT_VOLCENGINE_SAMPLE_RATE,
    DEFAULT_VOLCENGINE_TTS_MODEL,
    VoiceGenerationSettings,
)
from .base import (
    LOG_PREFIX,
    GeneratedVoice,
    normalize_emotion_category,
    normalize_voice_style,
)

VOLCENGINE_TTS_ENDPOINT = (
    "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
)
_TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_MAX_AUDIO_BYTES = 32 * 1024 * 1024
# 火山大模型接口的单次文本上限会随资源配置变化，保守控制在 900 UTF-8 字节以内。
_MAX_TEXT_BYTES = 900
_VOICE_STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "neutral": {
        "speech_rate": 0,
        "loudness_rate": 0,
        "pitch": 0,
        "instruction": "自然平稳",
    },
    "happy": {
        "speech_rate": 10,
        "loudness_rate": 6,
        "pitch": 2,
        "instruction": "开心明快",
    },
    "light": {
        "speech_rate": 4,
        "loudness_rate": 2,
        "pitch": 1,
        "instruction": "轻松自然",
    },
    "sad": {
        "speech_rate": -10,
        "loudness_rate": -6,
        "pitch": -2,
        "instruction": "委屈低缓",
    },
    "angry": {
        "speech_rate": 8,
        "loudness_rate": 10,
        "pitch": 1,
        "instruction": "克制但明显地不高兴",
    },
}
class VolcengineVoiceError(RuntimeError):
    """火山语音接口返回的可读错误。"""

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


class VolcengineVoiceService:
    """调用火山引擎语音合成 2.0，不负责声音训练或音色管理。"""

    def __init__(self, settings: VoiceGenerationSettings, data_dir: Path):
        self.settings = settings
        self.output_dir = data_dir / "generated" / "voices"
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def synthesize(
        self,
        text: str,
        *,
        emotion: str = "",
        emotion_category: str = "",
        voice_style: str = "",
    ) -> GeneratedVoice:
        if not self.settings.enabled:
            raise RuntimeError("语音生成未启用")
        text = str(text or "").strip()
        if not text:
            raise ValueError("缺少语音文本")
        if not self.settings.api_key:
            raise RuntimeError("火山引擎语音生成缺少 API Key")
        if not self.settings.speaker_id:
            raise RuntimeError("火山引擎语音生成缺少音色 ID")

        route = self._voice_route(emotion, emotion_category, voice_style)
        path = self._cache_path(text, route)
        # 控制台重新训练复刻音色后，服务端仍可能复用相同 speaker ID。
        # 复刻音色不读取本地命中缓存，下一次请求始终以控制台最新音色为准；
        # 路径保持稳定，成功后会覆盖旧文件，不会无限产生重复缓存。
        use_cache = route["source"] == "preset"
        if use_cache and await asyncio.to_thread(
            lambda: path.exists() and path.stat().st_size > 0
        ):
            return GeneratedVoice(path)

        await asyncio.to_thread(self.output_dir.mkdir, parents=True, exist_ok=True)
        text_parts = self._split_text(text)
        if len(text_parts) > 1:
            logger.debug(
                f"{LOG_PREFIX} 长文本语音拆分：{len(text_parts)} 段；"
                f"总字节数={len(text.encode('utf-8'))}"
            )
        audio_parts: list[bytes] = []
        for index, text_part in enumerate(text_parts, start=1):
            audio = await self._request_audio_with_retry(text_part, route)
            if not audio:
                raise RuntimeError(f"火山引擎语音生成失败：第 {index} 段未返回音频")
            if len(audio) > _MAX_AUDIO_BYTES:
                raise RuntimeError(f"火山引擎语音生成失败：第 {index} 段音频超过 32 MB")
            audio_parts.append(audio)
        audio = await asyncio.to_thread(self._join_audio_parts, audio_parts)
        if not audio:
            raise RuntimeError("火山引擎语音生成失败：音频拼接结果为空")
        if len(audio) > _MAX_AUDIO_BYTES:
            raise RuntimeError("火山引擎语音生成失败：拼接后音频超过 32 MB")
        await asyncio.to_thread(self._write_atomic, path, audio)
        logger.info(
            f"{LOG_PREFIX} 火山引擎语音生成完成：{path.name}，"
            f"音色类型={route['source_label']}，情绪={route['emotion']}，"
            f"语气={route['style_key']}，分段数={len(text_parts)}"
        )
        return GeneratedVoice(path)

    async def _request_audio_with_retry(
        self, text: str, route: dict[str, Any]
    ) -> bytes:
        last_error: Any = ""
        for attempt in range(self.settings.max_retries + 1):
            try:
                return await self._request_audio(text, route)
            except VolcengineVoiceError as exc:
                last_error = exc
                if not exc.transient:
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
                logger.debug(f"{LOG_PREFIX} 火山引擎语音请求异常：{exc}")
                break
            if attempt < self.settings.max_retries:
                await asyncio.sleep(min(2**attempt, 8))
        raise RuntimeError(f"火山引擎语音生成失败：{str(last_error)[:300]}")

    async def _request_audio(self, text: str, route: dict[str, Any]) -> bytes:
        session = await self._get_session()
        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-Key": self.settings.api_key,
            "X-Api-Resource-Id": route["resource_id"],
            "X-Api-Request-Id": request_id,
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }
        async with session.post(
            VOLCENGINE_TTS_ENDPOINT,
            headers=headers,
            json=self._payload(text, route),
        ) as response:
            if response.status != 200:
                body = await response.text()
                raise VolcengineVoiceError(
                    f"HTTP {response.status}: {self._short_error(body)}",
                    transient=response.status in _TRANSIENT_HTTP_STATUSES,
                )
            log_id = str((getattr(response, "headers", {}) or {}).get("X-Tt-Logid", "")).strip()
            if log_id:
                logger.debug(f"{LOG_PREFIX} 火山引擎语音请求完成：追踪ID={log_id}")
            return await self._read_sse_audio(response)

    async def _read_sse_audio(self, response: Any) -> bytes:
        content = getattr(response, "content", None)
        if content is None:
            raise VolcengineVoiceError("接口缺少 SSE 响应流")
        chunks = bytearray()
        buffer = ""
        iterator = getattr(content, "iter_chunked", None)
        if iterator is None:
            async def fallback_iterator(_size: int):
                async for chunk in content:
                    yield chunk

            iterator = fallback_iterator
        async for raw in iterator(8192):
            if not raw:
                continue
            buffer += bytes(raw).decode("utf-8", errors="replace")
            lines = buffer.split("\n")
            buffer = lines.pop()
            for line in lines:
                self._consume_sse_line(line.rstrip("\r"), chunks)
        if buffer.strip():
            self._consume_sse_line(buffer.rstrip("\r"), chunks)
        return bytes(chunks)

    @staticmethod
    def _consume_sse_line(line: str, chunks: bytearray) -> None:
        text = line.strip()
        if not text or not text.startswith("data:"):
            return
        payload = text[5:].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise VolcengineVoiceError("接口返回了无效的 SSE JSON") from exc
        if not isinstance(data, dict):
            return
        code = data.get("code")
        if code in (None, 0, "0"):
            encoded = data.get("data")
            if encoded:
                try:
                    chunks.extend(base64.b64decode(str(encoded), validate=True))
                except (ValueError, binascii.Error) as exc:
                    raise VolcengineVoiceError("接口返回了无效的音频数据") from exc
            return
        if code in (20000000, "20000000") or data.get("event") == "FinishConnection":
            return
        message = data.get("message") or data.get("msg") or data.get("detail") or code
        transient = str(code) in {"429", "500", "502", "503", "504"}
        raise VolcengineVoiceError(
            f"接口错误 {code}: {str(message)[:240]}", transient=transient
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.settings.timeout_seconds,
                sock_read=self.settings.timeout_seconds,
            )
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _voice_route(
        self,
        emotion: str = "",
        emotion_category: str = "",
        voice_style: str = "",
    ) -> dict[str, Any]:
        source = self.settings.speaker_source
        resource_id = "seed-tts-2.0" if source == "preset" else "seed-icl-2.0"
        style_key = self._emotion_style_key(voice_style, emotion_category)
        profile = _VOICE_STYLE_PROFILES[style_key]
        return {
            "speaker": self.settings.speaker_id,
            "source": source,
            "source_label": "预置音色" if source == "preset" else "复刻音色",
            "resource_id": resource_id,
            "model": "" if source == "preset" else DEFAULT_VOLCENGINE_TTS_MODEL,
            "emotion": (
                str(emotion or "").strip()
                or normalize_emotion_category(emotion_category)
                or "未裁定"
            ),
            "emotion_category": normalize_emotion_category(emotion_category),
            "voice_style": style_key,
            "style_key": style_key,
            "instruction": profile["instruction"],
            "speech_rate": max(
                -50, min(100, self.settings.speech_rate + profile["speech_rate"])
            ),
            "loudness_rate": max(
                -50, min(100, self.settings.loudness_rate + profile["loudness_rate"])
            ),
            "pitch": max(-12, min(12, profile["pitch"])),
        }

    @staticmethod
    def _emotion_style_key(voice_style: str = "", emotion_category: str = "") -> str:
        category = normalize_emotion_category(emotion_category)
        return normalize_voice_style(voice_style, category)

    def _payload(self, text: str, route: dict[str, Any]) -> dict[str, Any]:
        req_params: dict[str, Any] = {
            "text": text,
            "speaker": route["speaker"],
            "audio_params": {
                "format": DEFAULT_VOLCENGINE_FORMAT,
                "sample_rate": DEFAULT_VOLCENGINE_SAMPLE_RATE,
                "speech_rate": route["speech_rate"],
                "loudness_rate": route["loudness_rate"],
            },
        }
        additions: dict[str, Any] = {
            "explicit_language": "zh-cn",
            "disable_markdown_filter": True,
            "enable_latex_tn": True,
            "post_process": {"pitch": route["pitch"]},
        }
        if route["source"] == "preset" and route["style_key"] != "neutral":
            additions["context_texts"] = [
                f"请用{route['instruction']}的语气自然地说出这句话，不要改变文字内容。"
            ]
        req_params["additions"] = json.dumps(additions, ensure_ascii=False)
        if route["model"]:
            req_params["model"] = route["model"]
        return {"user": {"uid": str(uuid.uuid4())}, "req_params": req_params}

    @staticmethod
    def _split_text(text: str) -> list[str]:
        """按 UTF-8 字节上限拆分文本，并尽量在自然标点处断开。"""
        if len(text.encode("utf-8")) <= _MAX_TEXT_BYTES:
            return [text]
        boundaries = set("。！？!?；;，,、\n")
        parts: list[str] = []
        start = 0
        length = len(text)
        while start < length:
            used = 0
            end = start
            while end < length:
                char_bytes = len(text[end].encode("utf-8"))
                if end > start and used + char_bytes > _MAX_TEXT_BYTES:
                    break
                used += char_bytes
                end += 1
                if used >= _MAX_TEXT_BYTES:
                    break
            if end >= length:
                boundary = length
            else:
                boundary = end
                for index in range(end - 1, start, -1):
                    if text[index] in boundaries:
                        boundary = index + 1
                        break
            part = text[start:boundary].strip()
            if part:
                parts.append(part)
            start = max(boundary, start + 1)
        return parts or [text]

    @staticmethod
    def _ffmpeg_executable() -> str | None:
        executable = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if executable:
            return executable
        try:
            module = importlib.import_module("imageio_ffmpeg")
            return str(module.get_ffmpeg_exe() or "").strip() or None
        except Exception:
            return None

    @classmethod
    def _join_audio_parts(cls, parts: list[bytes]) -> bytes:
        if len(parts) == 1:
            return parts[0]
        executable = cls._ffmpeg_executable()
        if not executable:
            raise VolcengineVoiceError("长文本语音拼接需要 ffmpeg")
        with tempfile.TemporaryDirectory(prefix="daily_life_voice_") as temp_dir:
            directory = Path(temp_dir)
            paths: list[Path] = []
            for index, part in enumerate(parts):
                part_path = directory / f"part_{index}.mp3"
                part_path.write_bytes(part)
                paths.append(part_path)
            manifest = directory / "concat.txt"
            manifest.write_text(
                "".join(f"file '{path.name}'\n" for path in paths), encoding="utf-8"
            )
            output = directory / "joined.mp3"
            try:
                result = subprocess.run(
                    [
                        executable,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(manifest),
                        "-c",
                        "copy",
                        str(output),
                    ],
                    cwd=directory,
                    capture_output=True,
                    timeout=120,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise VolcengineVoiceError(f"长文本语音拼接失败：{exc}") from exc
            if result.returncode != 0 or not output.is_file():
                detail = result.stderr.decode("utf-8", errors="replace").strip()
                raise VolcengineVoiceError(
                    f"长文本语音拼接失败：{detail[:240] or 'ffmpeg 未生成音频'}"
                )
            return output.read_bytes()

    def _cache_path(self, text: str, route: dict[str, Any]) -> Path:
        key = hashlib.sha256(
            json.dumps(
                {
                    "text": text,
                    "speaker": route["speaker"],
                    "source": route["source"],
                    "model": route["model"],
                    "style_key": route["style_key"],
                    "speech_rate": route["speech_rate"],
                    "loudness_rate": route["loudness_rate"],
                    "pitch": route["pitch"],
                    "format": DEFAULT_VOLCENGINE_FORMAT,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return self.output_dir / f"volcengine_{key}.{DEFAULT_VOLCENGINE_FORMAT}"

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)

    @staticmethod
    def _short_error(value: Any) -> str:
        text = str(value or "").replace("\n", " ").strip()
        return text[:300] or "未提供错误详情"
__all__ = ["VOLCENGINE_TTS_ENDPOINT", "VolcengineVoiceError", "VolcengineVoiceService"]
