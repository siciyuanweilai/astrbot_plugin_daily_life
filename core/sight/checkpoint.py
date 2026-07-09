from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from astrbot.api import logger
from ..runtime.markers import LOG_PREFIX
from .sample import sight_cache_dir


TRANSCRIPT_DIRECT_LIMIT = 900
TRANSCRIPT_EXCERPT_LIMIT = 1800
TRANSCRIPT_CHUNK_CHARS = 4500
TRANSCRIPT_CHUNKS_PER_PASS = 4
BRIEF_CHECKPOINT_DIR = "brief"
BRIEF_CHECKPOINT_VERSION = 1


def _brief_checkpoint_dir(runtime: Any) -> Path:
    return sight_cache_dir(getattr(runtime, "data_path", None)) / BRIEF_CHECKPOINT_DIR


def _brief_checkpoint_path(runtime: Any, checkpoint_key: str, provider_id: str) -> Path:
    digest = hashlib.sha256(
        f"{checkpoint_key}|{provider_id}".encode("utf-8", errors="ignore")
    ).hexdigest()[:32]
    return _brief_checkpoint_dir(runtime) / f"{digest}.json"


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


async def _save_checkpoint_async(path: Path, payload: dict[str, Any]) -> None:
    await asyncio.to_thread(_save_checkpoint, path, payload)


def _checkpoint_payload(
    checkpoint_key: str,
    provider_id: str,
    *,
    stage: str,
    next_chunk: int,
    partials: list[str] | None = None,
    summary: str = "",
    details: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": BRIEF_CHECKPOINT_VERSION,
        "checkpoint_key": checkpoint_key,
        "provider_id": provider_id,
        "stage": stage,
        "next_chunk": next_chunk,
        "partials": list(partials or []),
    }
    if summary or details is not None:
        payload["summary"] = summary
        payload["details"] = list(details or [])
    return payload


def _normalize_partials(values: Any) -> list[str]:
    items = values if isinstance(values, list) else []
    result: list[str] = []
    for item in items:
        text = " ".join(str(item or "").split())
        if text and text not in result:
            result.append(text)
    return result


def _clean_list(value: Any, limit: int = 5) -> list[str]:
    values = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    result: list[str] = []
    for item in values:
        text = " ".join(str(item or "").split())[:140]
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _json_summary(text: str) -> tuple[str, list[str]]:
    from ..life.tools import extract_json_from_text

    payload = extract_json_from_text(text)
    if isinstance(payload, dict):
        summary = " ".join(str(payload.get("summary") or "").split())[:420]
        details = _clean_list(payload.get("details"), limit=4)
        return summary, details
    summary = " ".join(str(text or "").split())[:260]
    return summary, []


def _chunks(text: str, size: int) -> list[str]:
    values: list[str] = []
    current: list[str] = []
    current_len = 0
    for piece in str(text or "").split():
        piece_len = len(piece) + 1
        if current and current_len + piece_len > size:
            values.append(" ".join(current))
            current = []
            current_len = 0
        current.append(piece)
        current_len += piece_len
    if current:
        values.append(" ".join(current))
    return values or ([str(text or "").strip()] if str(text or "").strip() else [])


def _excerpt(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    head = value[: limit // 2].rstrip()
    tail = value[-limit // 2 :].lstrip()
    return f"{head}\n...\n{tail}"


async def audio_outline_checkpointed(
    brief: Any,
    provider: Any,
    call_llm: Any,
    composer: Any,
    transcript: str,
    metadata: dict[str, Any],
    *,
    provider_id: str = "",
    checkpoint_key: str = "",
) -> tuple[str, list[str]]:
    runtime = getattr(brief, "runtime", None)
    if runtime is None or not checkpoint_key:
        return await brief._audio_outline(provider, call_llm, composer, transcript, metadata, provider_id=provider_id)

    path = _brief_checkpoint_path(runtime, checkpoint_key, provider_id)
    checkpoint = await asyncio.to_thread(_load_checkpoint, path)
    if not _checkpoint_matches(checkpoint, checkpoint_key, provider_id):
        checkpoint = {}
    if checkpoint.get("stage") == "done":
        summary = " ".join(str(checkpoint.get("summary") or "").split())
        details = _normalize_partials(checkpoint.get("details"))
        if summary or details:
            return summary, details

    transcript = " ".join(str(transcript or "").split())
    if not transcript:
        return "", []
    if len(transcript) <= TRANSCRIPT_DIRECT_LIMIT:
        return await _audio_outline_direct_checkpointed(
            brief,
            provider,
            call_llm,
            composer,
            transcript,
            metadata,
            path=path,
            checkpoint_key=checkpoint_key,
            provider_id=provider_id,
        )

    chunks = _chunks(transcript, TRANSCRIPT_CHUNK_CHARS)
    partials = _normalize_partials(checkpoint.get("partials"))
    next_chunk = _checkpoint_next_chunk(checkpoint, len(chunks))
    if checkpoint.get("stage") == "merge":
        next_chunk = len(chunks)

    partials = await _audio_outline_collect_partials(
        brief,
        provider,
        call_llm,
        composer,
        chunks,
        partials,
        metadata,
        path=path,
        checkpoint_key=checkpoint_key,
        provider_id=provider_id,
        next_chunk=next_chunk,
    )
    return await _audio_outline_result_from_partials(
        brief,
        provider,
        call_llm,
        composer,
        transcript,
        chunks,
        partials,
        metadata,
        path=path,
        checkpoint_key=checkpoint_key,
        provider_id=provider_id,
    )


async def _audio_outline_direct_checkpointed(
    brief: Any,
    provider: Any,
    call_llm: Any,
    composer: Any,
    transcript: str,
    metadata: dict[str, Any],
    *,
    path: Path,
    checkpoint_key: str,
    provider_id: str,
) -> tuple[str, list[str]]:
    result = await brief._audio_outline(provider, call_llm, composer, transcript, metadata, provider_id=provider_id)
    try:
        await _save_checkpoint_async(
            path,
            _checkpoint_payload(
                checkpoint_key,
                provider_id,
                stage="done",
                next_chunk=0,
                summary=result[0],
                details=result[1],
            ),
        )
    except Exception as exc:
        logger.debug(f"{LOG_PREFIX} 视频简介断点保存失败：{exc}")
    return result


async def _audio_outline_collect_partials(
    brief: Any,
    provider: Any,
    call_llm: Any,
    composer: Any,
    chunks: list[str],
    partials: list[str],
    metadata: dict[str, Any],
    *,
    path: Path,
    checkpoint_key: str,
    provider_id: str,
    next_chunk: int,
) -> list[str]:
    for start in range(next_chunk, len(chunks), TRANSCRIPT_CHUNKS_PER_PASS):
        group = chunks[start : start + TRANSCRIPT_CHUNKS_PER_PASS]
        if not group:
            continue
        text = await brief._call_model(
            provider,
            call_llm,
            composer,
            brief._audio_prompt(group, metadata, offset=start, total=len(chunks)),
            prefix="daily_life_sight_audio",
            provider_id=provider_id,
        )
        summary, details = _json_summary(text)
        if summary:
            partials.append(summary)
        partials.extend(details)
        await _save_checkpoint_async(
            path,
            _checkpoint_payload(
                checkpoint_key,
                provider_id,
                stage="groups",
                next_chunk=start + len(group),
                partials=partials,
            ),
        )
    return partials


async def _audio_outline_result_from_partials(
    brief: Any,
    provider: Any,
    call_llm: Any,
    composer: Any,
    transcript: str,
    chunks: list[str],
    partials: list[str],
    metadata: dict[str, Any],
    *,
    path: Path,
    checkpoint_key: str,
    provider_id: str,
) -> tuple[str, list[str]]:
    if not partials:
        partials = [_excerpt(transcript, TRANSCRIPT_EXCERPT_LIMIT)]
    if len(partials) <= 3:
        summary = "；".join(partials)[:420]
        details = [f"音频主线：{summary}"] if summary else []
        await _save_checkpoint_async(
            path,
            _checkpoint_payload(
                checkpoint_key,
                provider_id,
                stage="done",
                next_chunk=len(chunks),
                partials=partials,
                summary=summary,
                details=details,
            ),
        )
        return summary, details

    await _save_checkpoint_async(
        path,
        _checkpoint_payload(
            checkpoint_key,
            provider_id,
            stage="merge",
            next_chunk=len(chunks),
            partials=partials,
        ),
    )
    text = await brief._call_model(
        provider,
        call_llm,
        composer,
        brief._merge_audio_prompt(partials, metadata),
        prefix="daily_life_sight_audio",
        provider_id=provider_id,
    )
    summary, details = _json_summary(text)
    summary = summary or "；".join(partials[:4])[:420]
    result_details = _clean_list([f"音频主线：{summary}", *details], limit=5)
    await _save_checkpoint_async(
        path,
        _checkpoint_payload(
            checkpoint_key,
            provider_id,
            stage="done",
            next_chunk=len(chunks),
            partials=partials,
            summary=summary,
            details=result_details,
        ),
    )
    return summary, result_details


def _checkpoint_matches(payload: dict[str, Any], checkpoint_key: str, provider_id: str) -> bool:
    return (
        payload.get("version") == BRIEF_CHECKPOINT_VERSION
        and str(payload.get("checkpoint_key") or "") == checkpoint_key
        and str(payload.get("provider_id") or "") == provider_id
    )


def _checkpoint_next_chunk(payload: dict[str, Any], total_chunks: int) -> int:
    try:
        value = int(payload.get("next_chunk") or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(value, max(0, total_chunks)))
