from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .hearing import AudioTranscriptError, prepare_audio_source
from .codec import ffmpeg_executable
from .sample import source_fingerprint
from .transcript import TranscriptResult, TranscriptSegment
from .stash import transcript_cache_path, write_transcript_cache
from ..runtime.markers import LOG_PREFIX


REQUIRED_MODULES = ("funasr", "modelscope", "torch", "torchaudio")
ASR_MODEL = "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
VAD_MODEL = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
MODEL_ALIASES = {
    ASR_MODEL: "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    VAD_MODEL: "speech_fsmn_vad_zh-cn-16k-common-pytorch",
}

_prepare_lock = asyncio.Lock()
_semaphores: dict[int, asyncio.Semaphore] = {}
_ready_keys: set[str] = set()


@dataclass(slots=True)
class LocalAsrConfig:
    batch_size_s: int = 300
    timeout_seconds: int = 900


async def transcribe_local(
    source: str,
    cache_dir: Path,
    *,
    config: LocalAsrConfig | None = None,
    max_chars: int = 8000,
) -> TranscriptResult | None:
    config = config or LocalAsrConfig()
    audio_path = await prepare_audio_source(source, cache_dir)
    if not audio_path:
        return None
    return await transcribe_local_audio(audio_path, cache_dir, config=config, max_chars=max_chars)


async def transcribe_local_audio(
    audio_path: Path,
    cache_dir: Path,
    *,
    config: LocalAsrConfig | None = None,
    max_chars: int = 8000,
) -> TranscriptResult | None:
    config = config or LocalAsrConfig()
    wav_path = await ensure_wav(audio_path, cache_dir)
    await ensure_ready(cache_dir, config)
    output_path = transcript_cache_path(cache_dir, "local_asr", wav_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_worker(wav_path, output_path, cache_dir, config)
    payload = _read_json(output_path)
    result = transcript_from_payload(payload, max_chars=max_chars)
    if result and result.has_text:
        write_transcript_cache(cache_dir, "local_asr", wav_path, result, raw_payload=payload)
    return result


async def ensure_wav(audio_path: Path, cache_dir: Path) -> Path:
    if audio_path.suffix.lower() == ".wav":
        return audio_path
    target_dir = cache_dir / "audio"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source_fingerprint(str(audio_path))}.wav"
    if target.is_file() and target.stat().st_size > 0:
        return target
    ok = await asyncio.to_thread(_to_wav_sync, audio_path, target)
    if not ok:
        raise AudioTranscriptError("本地ASR音频转换失败")
    return target


def transcript_from_payload(payload: dict[str, Any], *, max_chars: int = 8000) -> TranscriptResult | None:
    pieces: list[str] = []
    segments: list[TranscriptSegment] = []
    for item in list(payload.get("segments") or []):
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        start = _seconds(item.get("start_ms"))
        end = _seconds(item.get("end_ms"))
        segments.append(TranscriptSegment(start=start, end=end, text=text[:300]))
        pieces.append(text)
        if sum(len(piece) for piece in pieces) >= max_chars:
            break
    plain_text = " ".join(str(payload.get("plain_text") or "").split())
    full_text = (" ".join(pieces) if pieces else plain_text)[:max_chars]
    if not full_text and not segments:
        return None
    return TranscriptResult(
        language="zh",
        full_text=full_text,
        segments=tuple(segments),
        metadata={"segments": len(segments), "engine": "funasr"},
        source="本地ASR",
    )


async def ensure_ready(cache_dir: Path, config: LocalAsrConfig) -> None:
    async with _prepare_lock:
        models_dir = _models_dir(cache_dir)
        ready_key = str(models_dir)
        if ready_key in _ready_keys:
            return
        if not await asyncio.to_thread(_modules_ready):
            await asyncio.to_thread(_install_requirements)
            if not await asyncio.to_thread(_modules_ready):
                raise AudioTranscriptError("本地ASR依赖安装后仍无法导入")
        await asyncio.to_thread(_ensure_models, models_dir, max(300, int(config.timeout_seconds or 900)))
        _ready_keys.add(ready_key)


def _to_wav_sync(source: Path, target: Path) -> bool:
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
        "-f",
        "wav",
        str(target),
    ]
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=180)
    except Exception:
        return False
    return result.returncode == 0 and target.is_file() and target.stat().st_size > 0


async def _run_worker(input_path: Path, output_path: Path, cache_dir: Path, config: LocalAsrConfig) -> None:
    semaphore = _semaphore()
    async with semaphore:
        output_path.unlink(missing_ok=True)
        command = [
            sys.executable,
            str(Path(__file__).with_name("worker.py")),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--model",
            ASR_MODEL,
            "--vad-model",
            VAD_MODEL,
            "--models-dir",
            str(_models_dir(cache_dir)),
            "--batch-size-s",
            str(max(1, int(config.batch_size_s or 300))),
        ]
        started = time.monotonic()
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(60, int(config.timeout_seconds or 900)),
        )
        if result.returncode != 0:
            detail = _last_lines(result.stderr or result.stdout)
            raise AudioTranscriptError(f"本地ASR转写失败：{detail or result.returncode}")
        if not output_path.is_file():
            raise AudioTranscriptError("本地ASR没有生成转写结果")
        elapsed = time.monotonic() - started
        logger.debug(f"{LOG_PREFIX} 本地ASR转写耗时：{elapsed:.1f} 秒")


def _semaphore() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    semaphore = _semaphores.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(1)
        _semaphores[key] = semaphore
    return semaphore


def _modules_ready() -> bool:
    code = "; ".join(f"import {name}" for name in REQUIRED_MODULES)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except Exception:
        return False
    return result.returncode == 0


def _install_requirements() -> None:
    requirements = Path(__file__).resolve().parents[2] / "requirements-asr.txt"
    if not requirements.is_file():
        raise AudioTranscriptError(f"找不到本地ASR依赖文件：{requirements}")
    logger.info(f"{LOG_PREFIX} 本地ASR依赖准备开始")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=3600,
    )
    if result.returncode != 0:
        raise AudioTranscriptError(f"本地ASR依赖安装失败：{_last_lines(result.stderr or result.stdout)}")
    logger.info(f"{LOG_PREFIX} 本地ASR依赖准备完成")


def _ensure_models(models_dir: Path, timeout_seconds: int) -> None:
    models_dir.mkdir(parents=True, exist_ok=True)
    for repo, alias in MODEL_ALIASES.items():
        target = models_dir / alias
        if _looks_like_model(target):
            continue
        _download_model(repo, target, models_dir / "download", timeout_seconds)


def _models_ready(models_dir: Path) -> bool:
    return all(_looks_like_model(models_dir / alias) for alias in MODEL_ALIASES.values())


def _download_model(repo: str, target: Path, cache_dir: Path, timeout_seconds: int) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    code = (
        "from modelscope import snapshot_download; "
        "import sys; "
        "print(snapshot_download(sys.argv[1], cache_dir=sys.argv[2]))"
    )
    logger.info(f"{LOG_PREFIX} 本地ASR模型准备开始：{repo}")
    result = subprocess.run(
        [sys.executable, "-c", code, repo, str(cache_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout_seconds,
    )
    downloaded = _downloaded_model_path(result.stdout, cache_dir, repo)
    if result.returncode != 0 and not downloaded:
        raise AudioTranscriptError(f"本地ASR模型下载失败：{_last_lines(result.stderr or result.stdout)}")
    if not downloaded or not _looks_like_model(downloaded):
        raise AudioTranscriptError(f"本地ASR模型校验失败：{repo}")
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(downloaded, target)
    logger.info(f"{LOG_PREFIX} 本地ASR模型准备完成：{target.name}")


def _downloaded_model_path(stdout: str, cache_dir: Path, repo: str) -> Path | None:
    for line in reversed([item.strip() for item in str(stdout or "").splitlines() if item.strip()]):
        path = Path(line)
        if _looks_like_model(path):
            return path
    if "/" not in repo:
        return None
    namespace, name = repo.split("/", 1)
    for candidate in (
        cache_dir / namespace / name,
        cache_dir / "models" / namespace / name,
        cache_dir / "._____temp" / namespace / name,
    ):
        if _looks_like_model(candidate):
            return candidate
    return None


def _looks_like_model(path: Path) -> bool:
    return path.is_dir() and any((path / name).exists() for name in ("configuration.json", "config.yaml", "model.pt"))


def _models_dir(cache_dir: Path) -> Path:
    return cache_dir / "asr" / "models"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AudioTranscriptError(f"本地ASR结果读取失败：{exc}") from exc
    return payload if isinstance(payload, dict) else {}


def _seconds(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0) / 1000.0)
    except (TypeError, ValueError):
        return 0.0


def _last_lines(text: str, max_lines: int = 8) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return "；".join(lines[-max_lines:])[:800]
