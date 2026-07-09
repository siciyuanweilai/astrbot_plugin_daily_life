from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SightClip:
    scope: str = ""
    message_id: str = ""
    source: str = ""
    file_id: str = ""
    name: str = ""
    origin: str = "current"
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        raw = "|".join(
            (
                self.scope,
                self.message_id,
                self.source,
                self.file_id,
                self.name,
                self.origin,
            )
        )
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SightClip":
        return cls(
            scope=str(value.get("scope") or ""),
            message_id=str(value.get("message_id") or ""),
            source=str(value.get("source") or ""),
            file_id=str(value.get("file_id") or ""),
            name=str(value.get("name") or ""),
            origin=str(value.get("origin") or "current"),
            text=str(value.get("text") or ""),
            metadata=dict(value.get("metadata") or {}) if isinstance(value.get("metadata"), dict) else {},
            created_at=float(value.get("created_at") or time.time()),
        )


@dataclass
class SightInsight:
    clip: SightClip
    summary: str = ""
    details: list[str] = field(default_factory=list)
    frame_notes: list[str] = field(default_factory=list)
    transcript: str = ""
    transcript_source: str = ""
    note: str = ""
    note_source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source_note: str = ""
    status: str = "ready"
    error: str = ""
    updated_at: float = field(default_factory=time.time)

    @property
    def scope(self) -> str:
        return self.clip.scope

    @property
    def message_id(self) -> str:
        return self.clip.message_id

    @property
    def key(self) -> str:
        return self.clip.key

    def as_dict(self) -> dict[str, Any]:
        return {
            "clip": self.clip.as_dict(),
            "summary": self.summary,
            "details": list(self.details),
            "frame_notes": list(self.frame_notes),
            "transcript": self.transcript,
            "transcript_source": self.transcript_source,
            "note": self.note,
            "note_source": self.note_source,
            "metadata": dict(self.metadata),
            "source_note": self.source_note,
            "status": self.status,
            "error": self.error,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SightInsight | None":
        clip_value = value.get("clip")
        if not isinstance(clip_value, dict):
            return None
        return cls(
            clip=SightClip.from_dict(clip_value),
            summary=str(value.get("summary") or ""),
            details=[
                str(item or "").strip()
                for item in list(value.get("details") or [])
                if str(item or "").strip()
            ],
            frame_notes=[
                str(item or "").strip()
                for item in list(value.get("frame_notes") or [])
                if str(item or "").strip()
            ],
            transcript=str(value.get("transcript") or ""),
            transcript_source=str(value.get("transcript_source") or ""),
            note=str(value.get("note") or ""),
            note_source=str(value.get("note_source") or ""),
            metadata=dict(value.get("metadata") or {}) if isinstance(value.get("metadata"), dict) else {},
            source_note=str(value.get("source_note") or ""),
            status=str(value.get("status") or "ready"),
            error=str(value.get("error") or ""),
            updated_at=float(value.get("updated_at") or time.time()),
        )
