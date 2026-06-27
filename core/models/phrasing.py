from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_bool, optional_float, optional_int
from .coerce import compact_text as _text, compact_texts as _texts


@dataclass(slots=True)
class ExpressionProfileRecord:
    id: int = 0
    scope: str = ""
    profile_id: str = ""
    label: str = ""
    tone: str = ""
    habits: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    evidence: str = ""
    confidence: float = 1.0
    source: str = "chat_memory"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, source: str = "chat_memory") -> "ExpressionProfileRecord | None":
        if isinstance(value, ExpressionProfileRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        scope = _text(raw.get("scope"), 160)
        profile_id = _text(raw.get("profile_id"), 120)
        label = _text(raw.get("label"), 80)
        tone = _text(raw.get("tone"), 120)
        habits = _texts(raw.get("habits") or raw.get("features"), 8, 80)
        avoid = _texts(raw.get("avoid") or raw.get("taboo"), 6, 80)
        evidence = _text(raw.get("evidence"), 240)
        if not (scope or profile_id or label or tone or habits or evidence):
            return None
        confidence = optional_float(raw.get("confidence"))
        return ExpressionProfileRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=scope,
            profile_id=profile_id,
            label=label,
            tone=tone,
            habits=habits,
            avoid=avoid,
            evidence=evidence,
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "profile_id": self.profile_id,
            "label": self.label,
            "tone": self.tone,
            "habits": list(self.habits),
            "avoid": list(self.avoid),
            "evidence": self.evidence,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass(slots=True)
class ExpressionReviewRecord:
    id: int = 0
    scope: str = ""
    reply_text: str = ""
    passed: bool = True
    risk: str = ""
    suggestion: str = ""
    reason: str = ""
    source: str = "proactive_reply"
    created_at: str = ""

    @staticmethod
    def from_value(value: Any, *, source: str = "proactive_reply") -> "ExpressionReviewRecord | None":
        if isinstance(value, ExpressionReviewRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        reply_text = _text(raw.get("reply_text") or raw.get("text"), 240)
        reason = _text(raw.get("reason"), 240)
        risk = _text(raw.get("risk"), 80)
        suggestion = _text(raw.get("suggestion"), 240)
        if not (reply_text or reason or risk or suggestion):
            return None
        passed = optional_bool(raw.get("passed"))
        return ExpressionReviewRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=_text(raw.get("scope"), 180),
            reply_text=reply_text,
            passed=True if passed is None else passed,
            risk=risk,
            suggestion=suggestion,
            reason=reason,
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "reply_text": self.reply_text,
            "passed": self.passed,
            "risk": self.risk,
            "suggestion": self.suggestion,
            "reason": self.reason,
            "source": self.source,
            "created_at": self.created_at,
        }

@dataclass(slots=True)
class TemporaryExpressionStateRecord:
    id: int = 0
    scope: str = ""
    label: str = ""
    tone: str = ""
    reason: str = ""
    intensity: int = 50
    expires_at: str = ""
    source: str = "chat_memory"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, source: str = "chat_memory") -> "TemporaryExpressionStateRecord | None":
        if isinstance(value, TemporaryExpressionStateRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        label = _text(raw.get("label") or raw.get("state"), 80)
        tone = _text(raw.get("tone") or raw.get("style"), 120)
        reason = _text(raw.get("reason"), 240)
        if not (label or tone or reason):
            return None
        intensity = optional_int(raw.get("intensity"))
        return TemporaryExpressionStateRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=_text(raw.get("scope"), 160),
            label=label,
            tone=tone,
            reason=reason,
            intensity=max(0, min(intensity if intensity is not None else 50, 100)),
            expires_at=_text(raw.get("expires_at"), 40),
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "label": self.label,
            "tone": self.tone,
            "reason": self.reason,
            "intensity": self.intensity,
            "expires_at": self.expires_at,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass(slots=True)
class ExpressionIntentRecord:
    id: int = 0
    scope: str = ""
    message_id: str = ""
    reply_text: str = ""
    emotion: str = ""
    emotion_category: str = ""
    emoji_intent: str = ""
    action_intent: str = ""
    send_emoji: bool = False
    emoji_id: int = 0
    reason: str = ""
    source: str = "proactive_reply"
    created_at: str = ""

    @staticmethod
    def from_value(value: Any, *, source: str = "proactive_reply") -> "ExpressionIntentRecord | None":
        if isinstance(value, ExpressionIntentRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        emotion = _text(raw.get("emotion") or raw.get("expression_intent"), 80)
        emotion_category = _text(raw.get("emotion_category"), 20)
        emoji_intent = _text(raw.get("emoji_intent") or raw.get("emoji_style"), 80)
        action_intent = _text(raw.get("action_intent") or raw.get("action"), 120)
        reason = _text(raw.get("reason"), 240)
        if not (emotion or emotion_category or emoji_intent or action_intent or reason):
            return None
        return ExpressionIntentRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=_text(raw.get("scope"), 180),
            message_id=_text(raw.get("message_id"), 80),
            reply_text=_text(raw.get("reply_text"), 240),
            emotion=emotion,
            emotion_category=emotion_category,
            emoji_intent=emoji_intent,
            action_intent=action_intent,
            send_emoji=optional_bool(raw.get("send_emoji")) or False,
            emoji_id=max(optional_int(raw.get("emoji_id")) or 0, 0),
            reason=reason,
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "message_id": self.message_id,
            "reply_text": self.reply_text,
            "emotion": self.emotion,
            "emotion_category": self.emotion_category,
            "emoji_intent": self.emoji_intent,
            "action_intent": self.action_intent,
            "send_emoji": self.send_emoji,
            "emoji_id": self.emoji_id,
            "reason": self.reason,
            "source": self.source,
            "created_at": self.created_at,
        }

@dataclass(slots=True)
class EmojiAssetRecord:
    id: int = 0
    file_hash: str = ""
    file_path: str = ""
    label: str = ""
    description: str = ""
    emotions: list[str] = field(default_factory=list)
    source_scope: str = ""
    source_message_id: str = ""
    source_url: str = ""
    status: str = "pending"
    used_count: int = 0
    last_used_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "EmojiAssetRecord | None":
        if isinstance(value, EmojiAssetRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        file_hash = _text(raw.get("file_hash") or raw.get("hash"), 80)
        file_path = _text(raw.get("file_path") or raw.get("path"), 500)
        if not (file_hash or file_path):
            return None
        return EmojiAssetRecord(
            id=optional_int(raw.get("id")) or 0,
            file_hash=file_hash,
            file_path=file_path,
            label=_text(raw.get("label"), 80),
            description=_text(raw.get("description"), 240),
            emotions=_texts(raw.get("emotions") or raw.get("emotion"), 8, 40),
            source_scope=_text(raw.get("source_scope"), 180),
            source_message_id=_text(raw.get("source_message_id") or raw.get("message_id"), 80),
            source_url=_text(raw.get("source_url") or raw.get("url"), 500),
            status=_text(raw.get("status") or "pending", 40) or "pending",
            used_count=max(optional_int(raw.get("used_count")) or 0, 0),
            last_used_at=_text(raw.get("last_used_at"), 40),
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_hash": self.file_hash,
            "file_path": self.file_path,
            "label": self.label,
            "description": self.description,
            "emotions": list(self.emotions),
            "source_scope": self.source_scope,
            "source_message_id": self.source_message_id,
            "source_url": self.source_url,
            "status": self.status,
            "used_count": self.used_count,
            "last_used_at": self.last_used_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
