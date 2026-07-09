from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .sample import source_fingerprint
from .transcript import TranscriptResult


def transcript_cache_path(cache_dir: Path, engine: str, source_audio: str | Path) -> Path:
    name = _engine_name(engine)
    return cache_dir / "transcripts" / f"{name}_{source_fingerprint(str(source_audio))}.json"


def write_transcript_cache(
    cache_dir: Path,
    engine: str,
    source_audio: str | Path,
    result: TranscriptResult,
    *,
    raw_payload: dict[str, Any] | None = None,
) -> Path:
    path = transcript_cache_path(cache_dir, engine, source_audio)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": 1,
        "engine": _engine_name(engine),
        "source": str(result.source or ""),
        "source_audio": str(source_audio),
        "language": str(result.language or ""),
        "full_text": str(result.full_text or ""),
        "segments": [
            {
                "start": float(segment.start or 0.0),
                "end": float(segment.end or 0.0),
                "text": str(segment.text or ""),
            }
            for segment in result.segments
            if str(segment.text or "").strip()
        ],
        "metadata": dict(result.metadata or {}),
        "created_at": int(time.time()),
    }
    if raw_payload:
        payload["raw_payload"] = raw_payload
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return path


def _engine_name(value: str) -> str:
    text = "".join(char for char in str(value or "").strip().lower() if char.isalnum() or char in {"_", "-"})
    return text or "unknown"
