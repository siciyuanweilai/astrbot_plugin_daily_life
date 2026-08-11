from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_float, optional_int


@dataclass(slots=True)
class StyleCatalogItemRecord:
    id: int = 0
    kind: str = "outfit"
    title: str = ""
    description: str = ""
    image_path: str = ""
    source_url: str = ""
    source_scope: str = ""
    source_kind: str = "user_image"
    source_image_hash: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    preference_score: float = 0.0
    feedback_count: int = 0
    seen_count: int = 1
    status: str = "active"
    last_used_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def _text(value: Any, limit: int = 0) -> str:
        text = " ".join(str(value or "").strip().split())
        return text[:limit].strip() if limit > 0 else text

    @staticmethod
    def _attributes(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        try:
            encoded = json.dumps(value, ensure_ascii=False, default=str)
            decoded = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @classmethod
    def from_value(cls, value: Any) -> StyleCatalogItemRecord | None:
        if isinstance(value, StyleCatalogItemRecord):
            return value
        if not isinstance(value, dict):
            return None
        kind = cls._text(value.get("kind"), 16).lower()
        if kind not in {"outfit", "hair"}:
            return None
        image_hash = cls._text(value.get("source_image_hash"), 64).lower()
        description = cls._text(value.get("description"), 600)
        if not image_hash or not description:
            return None
        confidence = optional_float(value.get("confidence"))
        score = optional_float(value.get("preference_score"))
        return cls(
            id=optional_int(value.get("id")) or 0,
            kind=kind,
            title=cls._text(value.get("title"), 100),
            description=description,
            image_path=cls._text(value.get("image_path"), 1000),
            source_url=cls._text(value.get("source_url"), 1500),
            source_scope=cls._text(value.get("source_scope"), 240),
            source_kind=cls._text(value.get("source_kind"), 32) or "user_image",
            source_image_hash=image_hash,
            attributes=cls._attributes(value.get("attributes")),
            confidence=max(0.0, min(float(confidence or 0.0), 1.0)),
            preference_score=max(-2.0, min(float(score or 0.0), 2.0)),
            feedback_count=max(optional_int(value.get("feedback_count")) or 0, 0),
            seen_count=max(optional_int(value.get("seen_count")) or 1, 1),
            status=cls._text(value.get("status"), 24) or "active",
            last_used_at=cls._text(value.get("last_used_at"), 40),
            created_at=cls._text(value.get("created_at"), 40),
            updated_at=cls._text(value.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "image_path": self.image_path,
            "source_url": self.source_url,
            "source_scope": self.source_scope,
            "source_kind": self.source_kind,
            "source_image_hash": self.source_image_hash,
            "attributes": self._attributes(self.attributes),
            "confidence": self.confidence,
            "preference_score": self.preference_score,
            "feedback_count": self.feedback_count,
            "seen_count": self.seen_count,
            "status": self.status,
            "last_used_at": self.last_used_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


__all__ = ["StyleCatalogItemRecord"]
