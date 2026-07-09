from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TranscriptSegment:
    start: float = 0.0
    end: float = 0.0
    text: str = ""


@dataclass(slots=True)
class TranscriptResult:
    language: str = ""
    full_text: str = ""
    segments: tuple[TranscriptSegment, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    @property
    def has_text(self) -> bool:
        return bool(self.full_text.strip() or self.segments)
