from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit
from weakref import WeakValueDictionary

import aiohttp

from astrbot.api import logger

from ..config.options import MusicGenerationSettings
from .base import LOG_PREFIX

MUSIC_PROMPT_MAX_UTF16_UNITS = 3000
MUSIC_MAX_AUDIO_BYTES = 64 * 1024 * 1024


class MusicGenerationError(RuntimeError):
    """音乐任务失败，错误信息不会包含请求凭据或上游响应正文。"""


class _MusicHttpError(MusicGenerationError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"音乐接口请求失败（HTTP {status}）。")


def music_api_base(raw: str) -> str:
    """接受根地址、/v1 或完整创建地址，保留中转站的路径前缀。"""
    value = str(raw or "").strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise MusicGenerationError("请在音乐设置中填写有效的 HTTP(S) 接口地址。")
    if value.endswith("/music/generations"):
        value = value[: -len("/music/generations")]
    return value if value.endswith("/v1") else f"{value}/v1"


@dataclass(frozen=True, slots=True)
class GeneratedMusic:
    path: Path
    task_id: str
    model: str


def prompt_utf16_units(prompt: str) -> int:
    return len(str(prompt).encode("utf-16-le")) // 2


def _find_value(data: Any, keys: tuple[str, ...]) -> str:
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
        for value in data.values():
            result = _find_value(value, keys)
            if result:
                return result
    elif isinstance(data, list):
        for value in data:
            result = _find_value(value, keys)
            if result:
                return result
    return ""


def _audio_extension(data: bytes, content_type: str) -> str:
    lowered = str(content_type or "").split(";", 1)[0].strip().lower()
    if data.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
        return ".mp3"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav"
    if data.startswith(b"OggS"):
        return ".ogg"
    if data.startswith(b"fLaC"):
        return ".flac"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return ".m4a"
    extensions = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/ogg": ".ogg",
        "audio/flac": ".flac",
        "audio/mp4": ".m4a",
    }
    return extensions.get(lowered, ".mp3")


class MusicGenerationService:
    def __init__(self, settings: MusicGenerationSettings, data_dir: Path):
        self.settings = settings
        self.output_dir = Path(data_dir).expanduser().resolve() / "generated" / "music"
        self._task_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def _local_music_path(self, path: Path) -> Path:
        resolved = Path(path).resolve()
        if (
            resolved.parent != self.output_dir.resolve()
            or not resolved.name.startswith("music_")
            or resolved.suffix.lower() not in {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
            or not resolved.is_file()
        ):
            raise MusicGenerationError("音乐文件已不可用。")
        return resolved

    def _scope_record(self, scope: str) -> Path:
        if not scope:
            raise MusicGenerationError("当前会话无法读取音乐记录。")
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        return self.output_dir / f"latest_{digest}.json"

    async def remember_for_scope(self, scope: str, music: GeneratedMusic) -> None:
        path = await asyncio.to_thread(self._local_music_path, music.path)
        await self._write_task_record(
            self._scope_record(scope),
            {
                "file": path.name,
                "task_id": music.task_id,
                "model": music.model,
            },
        )

    def _read_scope_task(self, scope: str) -> tuple[Path, dict[str, str]] | None:
        """Only resolve the current conversation's explicitly bound task."""
        try:
            pointer = json.loads(self._scope_record(scope).read_text("utf-8"))
            name = pointer.get("task_record", "")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not name.startswith("task_")
                or not name.endswith(".json")
            ):
                return None
            path = self.output_dir / name
            if path.is_symlink():
                return None
            record = json.loads(path.read_text("utf-8"))
            if (
                record.get("scope_hash")
                != hashlib.sha256(scope.encode("utf-8")).hexdigest()
            ):
                return None
            return path, record
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    async def has_task_for_scope(self, scope: str) -> bool:
        return await asyncio.to_thread(self._read_scope_task, scope) is not None

    async def latest_for_scope(self, scope: str) -> GeneratedMusic | None:
        def read() -> GeneratedMusic | None:
            try:
                record = json.loads(self._scope_record(scope).read_text("utf-8"))
                if "task_record" in record:
                    task = self._read_scope_task(scope)
                    if task is None:
                        return None
                    _, record = task
                filename = record["file"]
                if not isinstance(filename, str) or Path(filename).name != filename:
                    return None
                path = self._local_music_path(self.output_dir / filename)
                return GeneratedMusic(
                    path, str(record["task_id"]), str(record["model"])
                )
            except (OSError, ValueError, TypeError, KeyError, MusicGenerationError):
                return None

        return await asyncio.to_thread(read)

    async def resume_for_scope(self, scope: str) -> GeneratedMusic:
        task = await asyncio.to_thread(self._read_scope_task, scope)
        if task is None:
            raise MusicGenerationError("当前会话没有可恢复的音乐任务，未重新生成。")
        task_path, _ = task
        lock = self._task_locks.setdefault(task_path.name, asyncio.Lock())
        async with lock:
            # A concurrent generation may have finished while we waited.
            existing = await self.latest_for_scope(scope)
            if existing is not None:
                return existing
            task = await asyncio.to_thread(self._read_scope_task, scope)
            if task is None or task[0] != task_path:
                raise MusicGenerationError(
                    "当前会话的音乐任务已更新，请重新查询，未重新生成。"
                )
            _, record = task
            if not record.get("task_id"):
                raise MusicGenerationError(
                    "原音乐请求是否受理尚未确认，且没有可查询的任务编号；请核对原请求记录，未重新生成。"
                )
            if record.get("status") in {"failed", "error", "cancelled", "canceled"}:
                raise MusicGenerationError("原音乐任务已失败，未重新生成。")
            if not self.settings.api_key:
                raise MusicGenerationError(
                    "查询原音乐任务需要在音乐设置中填写接口密钥。"
                )
            if record.get("api_base") != music_api_base(self.settings.base_url):
                raise MusicGenerationError(
                    "音乐接口地址已变更，无法查询原任务；请恢复原接口配置，未重新生成。"
                )
            fingerprint = record.get("credential_id")
            if (
                fingerprint
                and fingerprint
                != hashlib.sha256(self.settings.api_key.encode()).hexdigest()
            ):
                raise MusicGenerationError(
                    "音乐接口密钥已变更，无法确认原任务所属账号；请恢复原凭据，未重新生成。"
                )
            headers = {
                "Authorization": f"Bearer {self.settings.api_key}",
                "Accept": "application/json",
            }
            timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
            logger.info(
                f"{LOG_PREFIX} 音乐恢复查询原任务：{record['task_id']}；不重新提交生成"
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                return await self._wait_for_result(
                    session,
                    task_path,
                    record,
                    headers,
                    time.monotonic() + self.settings.timeout_seconds,
                )

    async def mp3_path(self, music: GeneratedMusic) -> Path:
        source = await asyncio.to_thread(self._local_music_path, music.path)

        def is_mp3(path: Path) -> bool:
            try:
                if path.is_symlink():
                    return False
                with path.open("rb") as file:
                    return file.read(3).startswith(
                        (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
                    )
            except OSError:
                return False

        output = source.with_suffix(".mp3")
        if await asyncio.to_thread(is_mp3, output):
            return output
        from ..sight.ffmpeg import ffmpeg_executable

        executable = await asyncio.to_thread(ffmpeg_executable)
        if not executable:
            raise MusicGenerationError("当前环境缺少 FFmpeg，无法转换为 MP3 文件。")
        temporary = output.with_suffix(f".{uuid.uuid4().hex}.part")
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                "-fs",
                str(MUSIC_MAX_AUDIO_BYTES + 1),
                "-f",
                "mp3",
                str(temporary),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=60)
            if process.returncode or not await asyncio.to_thread(is_mp3, temporary):
                raise MusicGenerationError("音乐转换为 MP3 失败，原始文件已保留。")
            if temporary.stat().st_size > MUSIC_MAX_AUDIO_BYTES:
                raise MusicGenerationError("转换后的 MP3 超过 64 MiB，未发送。")
            await asyncio.to_thread(temporary.replace, output)
            return output
        except asyncio.TimeoutError as exc:
            raise MusicGenerationError("音乐转换为 MP3 超时，原始文件已保留。") from exc
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    def _validate(self, prompt: str, model: str) -> tuple[str, str, str]:
        if not self.settings.enabled:
            raise MusicGenerationError("音乐生成功能尚未启用。")
        if not self.settings.api_key:
            raise MusicGenerationError("请先在音乐设置中填写接口密钥。")
        api_base = music_api_base(self.settings.base_url)
        if not api_base:
            raise MusicGenerationError("请先在音乐设置中填写接口地址。")
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise MusicGenerationError("请提供音乐描述。")
        if prompt_utf16_units(clean_prompt) > MUSIC_PROMPT_MAX_UTF16_UNITS:
            raise MusicGenerationError("音乐描述超过 3000 个 UTF-16 字符。")
        selected_model = (
            str(model or self.settings.model).strip() or self.settings.model
        )
        return clean_prompt, selected_model, api_base

    async def generate(
        self,
        prompt: str,
        *,
        instrumental: bool = False,
        model: str = "",
        scope: str = "",
    ) -> GeneratedMusic:
        clean_prompt, selected_model, api_base = self._validate(prompt, model)
        task_nonce = uuid.uuid4().hex
        idempotency_key = str(uuid.uuid4())
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        payload = {
            "model": selected_model,
            "prompt": clean_prompt,
            "instrumental": bool(instrumental),
            "n": 1,
        }
        deadline = time.monotonic() + self.settings.timeout_seconds
        await asyncio.to_thread(self.output_dir.mkdir, parents=True, exist_ok=True)
        task_record = self.output_dir / f"task_{task_nonce}.json"
        record = {
            "model": selected_model,
            "api_base": api_base,
            "idempotency_key": idempotency_key,
            "status": "submitting",
            "credential_id": hashlib.sha256(self.settings.api_key.encode()).hexdigest(),
        }
        if scope:
            record["scope_hash"] = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        # 在创建前保存幂等标识；响应丢失时仍能核对原请求，不能盲目重建。
        await self._write_task_record(task_record, record)
        if scope:
            # Bind before submission, not after download: reloads and unknown
            # creation outcomes must remain distinguishable from no request.
            await self._write_task_record(
                self._scope_record(scope), {"task_record": task_record.name}
            )
        timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
        lock = self._task_locks.setdefault(task_record.name, asyncio.Lock())
        async with lock, aiohttp.ClientSession(timeout=timeout) as session:
            try:
                created = await self._request_json(
                    session,
                    "POST",
                    f"{api_base}/music/generations",
                    headers,
                    json_body=payload,
                    timeout_seconds=min(
                        self.settings.request_timeout_seconds,
                        self.settings.timeout_seconds,
                    ),
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                record["status"] = "uncertain"
                await self._write_task_record(task_record, record)
                raise MusicGenerationError(
                    "创建请求未确认是否已受理；请先检查任务记录，不要再次提交。"
                ) from exc
            except MusicGenerationError as exc:
                record["status"] = "uncertain"
                await self._write_task_record(task_record, record)
                raise MusicGenerationError(
                    f"{exc} 创建结果未确认，请核对原请求记录，不要重复提交。"
                ) from exc
            task_id = _find_value(created, ("task_id", "id"))
            if not task_id:
                record["status"] = "uncertain"
                await self._write_task_record(task_record, record)
                raise MusicGenerationError("音乐接口已响应，但没有返回任务编号。")
            record.update(task_id=task_id, status="submitted")
            await self._write_task_record(task_record, record)

            logger.info(
                f"{LOG_PREFIX} 音乐任务已受理：{task_id}；模型={selected_model}"
            )
            return await self._wait_for_result(
                session, task_record, record, headers, deadline
            )

    async def _wait_for_result(
        self,
        session: aiohttp.ClientSession,
        task_record: Path,
        record: dict[str, str],
        headers: dict[str, str],
        deadline: float,
    ) -> GeneratedMusic:
        task_id, api_base = record["task_id"], record["api_base"]
        task_nonce = task_record.stem.removeprefix("task_")
        status_url = f"{api_base}/music/results/{quote(task_id, safe='')}"
        last_status = str(record.get("status") or "")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                record["status"] = "timeout"
                await self._write_task_record(task_record, record)
                raise MusicGenerationError(
                    f"音乐任务仍在处理中，任务编号：{task_id}。可稍后查询原任务，不要重新提交。"
                )
            await asyncio.sleep(min(self.settings.poll_interval_seconds, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                continue
            try:
                result = await self._request_json(
                    session,
                    "GET",
                    status_url,
                    headers,
                    timeout_seconds=min(
                        self.settings.request_timeout_seconds, remaining
                    ),
                )
            except (aiohttp.ClientError, asyncio.TimeoutError):
                # 查询失败只会重试同一个 task_id，不会重新创建计费任务。
                continue
            except _MusicHttpError as exc:
                if exc.status in {408, 429} or exc.status >= 500:
                    continue
                raise

            status = _find_value(result, ("status",)).lower()
            if status and status != last_status:
                record["status"] = status
                await self._write_task_record(task_record, record)
                logger.info(f"{LOG_PREFIX} 音乐原任务状态：{task_id}；{status}")
                last_status = status
            if status in {"failed", "error", "cancelled", "canceled"}:
                record["status"] = "failed"
                await self._write_task_record(task_record, record)
                raise MusicGenerationError(
                    "音乐生成任务失败，请检查接口用量或稍后重试。"
                )
            if status not in {"success", "succeeded", "completed", "complete"}:
                # 包括 uncertain 在内的非终态均继续查询，不重新提交。
                continue

            content_url = (
                f"{api_base}/tasks/{quote(task_id, safe='')}/artifacts/audio/content"
            )
            audio, content_type = await self._download_audio(
                session,
                content_url,
                headers,
                timeout_seconds=min(
                    self.settings.request_timeout_seconds,
                    max(1.0, deadline - time.monotonic()),
                ),
            )
            extension = _audio_extension(audio, content_type)
            output_path = self.output_dir / f"music_{task_nonce}{extension}"
            part_path = output_path.with_suffix(output_path.suffix + ".part")
            await asyncio.to_thread(part_path.write_bytes, audio)
            await asyncio.to_thread(part_path.replace, output_path)
            record.update(status="success", file=output_path.name)
            await self._write_task_record(task_record, record)
            logger.info(
                f"{LOG_PREFIX} 音乐原任务已下载：{task_id}；格式={extension}；字节={len(audio)}"
            )
            return GeneratedMusic(output_path, task_id, record["model"])

    async def _request_json(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        json_body: dict[str, Any] | None = None,
        timeout_seconds: float,
    ) -> Any:
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    raise _MusicHttpError(response.status)
                return await response.json(content_type=None)
        except MusicGenerationError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise
        except Exception as exc:
            raise MusicGenerationError("音乐接口返回内容无法读取。") from exc

    async def _download_audio(
        self,
        session: aiohttp.ClientSession,
        url: str,
        headers: dict[str, str],
        *,
        timeout_seconds: float,
    ) -> tuple[bytes, str]:
        current_url = url
        deadline = time.monotonic() + timeout_seconds
        for _ in range(4):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MusicGenerationError("音乐文件下载超时。")
                async with session.get(
                    current_url,
                    headers={
                        "Authorization": headers["Authorization"],
                        "Accept": "audio/*,application/octet-stream",
                    },
                    timeout=aiohttp.ClientTimeout(total=remaining),
                    allow_redirects=False,
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = str(response.headers.get("Location") or "").strip()
                        redirected = urljoin(current_url, location)
                        if not location or (
                            urlsplit(redirected).scheme,
                            urlsplit(redirected).netloc,
                        ) != (urlsplit(url).scheme, urlsplit(url).netloc):
                            raise MusicGenerationError("音乐文件下载地址无效。")
                        current_url = redirected
                        continue
                    if not 200 <= response.status < 300:
                        raise MusicGenerationError(
                            f"音乐文件下载失败（HTTP {response.status}）。"
                        )
                    content_type = str(response.headers.get("Content-Type") or "")
                    chunks = []
                    size = 0
                    # StreamReader.read(n) 只保证返回当前可读数据，不保证读满 n。
                    while True:
                        chunk = await response.content.read(64 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MUSIC_MAX_AUDIO_BYTES:
                            raise MusicGenerationError("音乐文件超过 64 MiB，未保存。")
                        chunks.append(chunk)
                    audio = b"".join(chunks)
                    known_audio_signature = any(
                        audio.startswith(signature)
                        for signature in (
                            b"ID3",
                            b"\xff\xfb",
                            b"\xff\xf3",
                            b"\xff\xf2",
                            b"OggS",
                            b"fLaC",
                        )
                    ) or (
                        len(audio) >= 12
                        and (
                            (audio[:4] == b"RIFF" and audio[8:12] == b"WAVE")
                            or audio[4:8] == b"ftyp"
                        )
                    )
                    if not audio or (
                        not content_type.lower().startswith("audio/")
                        and not known_audio_signature
                    ):
                        raise MusicGenerationError("下载结果不是可识别的音频文件。")
                    return audio, content_type
            except MusicGenerationError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise MusicGenerationError("音乐文件下载暂时失败。") from exc
        raise MusicGenerationError("音乐文件下载重定向次数过多。")

    @staticmethod
    async def _write_task_record(path: Path, payload: dict[str, str]) -> None:
        part_path = path.with_suffix(f".{uuid.uuid4().hex}.json.part")
        await asyncio.to_thread(
            part_path.write_text,
            json.dumps(payload, ensure_ascii=False),
            "utf-8",
        )
        await asyncio.to_thread(part_path.replace, path)
