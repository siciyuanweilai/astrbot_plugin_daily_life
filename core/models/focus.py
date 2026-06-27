from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_bool, optional_float, optional_int
from .coerce import compact_text as _text, compact_texts as _texts


@dataclass(slots=True)
class FocusSlotRecord:
    id: int = 0
    scope: str = ""
    focus_key: str = ""
    label: str = ""
    priority: int = 50
    reason: str = ""
    last_active_at: str = ""
    expires_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "FocusSlotRecord | None":
        if isinstance(value, FocusSlotRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        focus_key = _text(raw.get("focus_key") or raw.get("target_id"), 140)
        label = _text(raw.get("label"), 80)
        if not (focus_key or label):
            return None
        priority = optional_int(raw.get("priority"))
        return FocusSlotRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=_text(raw.get("scope"), 180),
            focus_key=focus_key or label,
            label=label or focus_key,
            priority=max(0, min(priority if priority is not None else 50, 100)),
            reason=_text(raw.get("reason"), 240),
            last_active_at=_text(raw.get("last_active_at"), 40),
            expires_at=_text(raw.get("expires_at"), 40),
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "focus_key": self.focus_key,
            "label": self.label,
            "priority": self.priority,
            "reason": self.reason,
            "last_active_at": self.last_active_at,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass(slots=True)
class FocusTargetRecord:
    id: int = 0
    target_type: str = "topic"
    target_id: str = ""
    label: str = ""
    priority: int = 50
    reason: str = ""
    scope: str = ""
    enabled: bool = True
    expires_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "FocusTargetRecord | None":
        if isinstance(value, FocusTargetRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        label = _text(raw.get("label"), 80)
        target_id = _text(raw.get("target_id"), 120)
        if not (label or target_id):
            return None
        priority = optional_int(raw.get("priority"))
        return FocusTargetRecord(
            id=optional_int(raw.get("id")) or 0,
            target_type=_text(raw.get("target_type") or "topic", 40) or "topic",
            target_id=target_id or label,
            label=label or target_id,
            priority=max(0, min(priority if priority is not None else 50, 100)),
            reason=_text(raw.get("reason"), 240),
            scope=_text(raw.get("scope"), 160),
            enabled=True if raw.get("enabled") is None else bool(optional_bool(raw.get("enabled"))),
            expires_at=_text(raw.get("expires_at"), 40),
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "label": self.label,
            "priority": self.priority,
            "reason": self.reason,
            "scope": self.scope,
            "enabled": self.enabled,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
