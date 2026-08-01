from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .coerce import compact_text as _text
from .primitive import optional_float, optional_int


class _RecordMappingMixin:
    def as_dict(self) -> dict[str, Any]:
        """将记录转换为可持久化字典。

        Returns:
            包含记录全部字段的独立字典。
        """

        return asdict(self)


def _payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, _RecordMappingMixin):
        return value.as_dict()
    return None


def _bounded_float(
    value: Any, default: float, *, lower: float = 0.0, upper: float = 1.0
) -> float:
    number = optional_float(value)
    if number is None:
        number = default
    return max(lower, min(float(number), upper))


def _string_list(value: Any, *, limit: int = 100) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, 160)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


@dataclass(slots=True)
class TemporalFactRecord(_RecordMappingMixin):
    """带有效时间和来源链的结构化事实。"""

    id: int = 0
    scope: str = ""
    subject: str = ""
    predicate: str = ""
    object_value: Any = None
    observed_at: str = ""
    valid_from: str = ""
    valid_to: str = ""
    confidence: float = 1.0
    status: str = "active"
    source: str = "observation"
    source_type: str = ""
    source_id: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    supersedes_id: int = 0
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any) -> TemporalFactRecord | None:
        """规范化时间化事实。

        Args:
            value: 字典或已有事实记录。

        Returns:
            可用事实记录；结构化键不完整时返回 ``None``。
        """

        raw = _payload(value)
        if raw is None:
            return None
        scope = _text(raw.get("scope"), 180)
        subject = _text(raw.get("subject"), 180)
        predicate = _text(raw.get("predicate"), 120)
        if not (scope and subject and predicate):
            return None
        provenance = raw.get("provenance")
        return TemporalFactRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=scope,
            subject=subject,
            predicate=predicate,
            object_value=raw.get("object_value", raw.get("object")),
            observed_at=_text(raw.get("observed_at"), 40),
            valid_from=_text(raw.get("valid_from"), 40),
            valid_to=_text(raw.get("valid_to"), 40),
            confidence=_bounded_float(raw.get("confidence"), 1.0),
            status=_text(raw.get("status") or "active", 40) or "active",
            source=_text(raw.get("source") or "observation", 80) or "observation",
            source_type=_text(raw.get("source_type"), 80),
            source_id=_text(raw.get("source_id"), 180),
            provenance=dict(provenance) if isinstance(provenance, dict) else {},
            supersedes_id=optional_int(raw.get("supersedes_id")) or 0,
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )


@dataclass(slots=True)
class FactEvidenceSignalRecord(_RecordMappingMixin):
    """强化或反驳事实的单条证据信号。"""

    id: int = 0
    fact_id: int = 0
    signal: str = "reinforce"
    weight: float = 1.0
    confidence: float = 1.0
    summary: str = ""
    source: str = "observation"
    source_id: str = ""
    observed_at: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @staticmethod
    def from_value(value: Any) -> FactEvidenceSignalRecord | None:
        """规范化事实证据信号。

        Args:
            value: 字典或已有证据信号。

        Returns:
            可用信号；事实编号或信号类型无效时返回 ``None``。
        """

        raw = _payload(value)
        if raw is None:
            return None
        fact_id = optional_int(raw.get("fact_id")) or 0
        signal = _text(raw.get("signal") or "reinforce", 20).lower()
        if fact_id <= 0 or signal not in {"reinforce", "dispute"}:
            return None
        provenance = raw.get("provenance")
        return FactEvidenceSignalRecord(
            id=optional_int(raw.get("id")) or 0,
            fact_id=fact_id,
            signal=signal,
            weight=max(optional_float(raw.get("weight")) or 0.0, 0.0),
            confidence=_bounded_float(raw.get("confidence"), 1.0),
            summary=_text(raw.get("summary"), 500),
            source=_text(raw.get("source") or "observation", 80) or "observation",
            source_id=_text(raw.get("source_id"), 180),
            observed_at=_text(raw.get("observed_at"), 40),
            provenance=dict(provenance) if isinstance(provenance, dict) else {},
            created_at=_text(raw.get("created_at"), 40),
        )


@dataclass(slots=True)
class ReflectionRecord(_RecordMappingMixin):
    """由多条证据归纳出的候选反思。"""

    id: int = 0
    scope: str = ""
    kind: str = "reflection"
    summary: str = ""
    importance: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    assertion_subject: str = ""
    assertion_predicate: str = ""
    assertion_object: Any = None
    confidence: float = 1.0
    status: str = "pending"
    source: str = "reflection"
    promoted_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any) -> ReflectionRecord | None:
        """规范化反思记录。

        Args:
            value: 字典或已有反思记录。

        Returns:
            有摘要的反思记录；否则返回 ``None``。
        """

        raw = _payload(value)
        if raw is None:
            return None
        summary = _text(raw.get("summary"), 1000)
        if not summary:
            return None
        return ReflectionRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=_text(raw.get("scope"), 180),
            kind=_text(raw.get("kind") or "reflection", 60) or "reflection",
            summary=summary,
            importance=_bounded_float(raw.get("importance"), 0.0),
            evidence_ids=_string_list(raw.get("evidence_ids")),
            assertion_subject=_text(raw.get("assertion_subject"), 180),
            assertion_predicate=_text(raw.get("assertion_predicate"), 120),
            assertion_object=raw.get("assertion_object"),
            confidence=_bounded_float(raw.get("confidence"), 1.0),
            status=_text(raw.get("status") or "pending", 40) or "pending",
            source=_text(raw.get("source") or "reflection", 80) or "reflection",
            promoted_at=_text(raw.get("promoted_at"), 40),
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )


@dataclass(slots=True)
class PersonaAssertionRecord(_RecordMappingMixin):
    """由高价值反思晋升的长期人格断言。"""

    id: int = 0
    scope: str = ""
    subject: str = ""
    predicate: str = ""
    object_value: Any = None
    confidence: float = 1.0
    source_reflection_id: int = 0
    valid_from: str = ""
    valid_to: str = ""
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class DurableTaskRecord(_RecordMappingMixin):
    """具备租约和失败恢复能力的持久任务。"""

    id: int = 0
    task_key: str = ""
    kind: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    priority: int = 50
    available_at: str = ""
    lease_owner: str = ""
    lease_expires_at: str = ""
    attempts: int = 0
    max_attempts: int = 3
    last_error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""


@dataclass(slots=True)
class DecisionTraceRecord(_RecordMappingMixin):
    """跨决策阶段共享的可审计轨迹。"""

    id: int = 0
    trace_id: str = ""
    scope: str = ""
    stage: str = ""
    reason_code: str = ""
    decision: str = ""
    scores: dict[str, Any] = field(default_factory=dict)
    evidence: list[Any] = field(default_factory=list)
    outcome: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class LifeActionOutcomeRecord(_RecordMappingMixin):
    """显式生活动作的提议、校验和结算结果。"""

    id: int = 0
    action_id: str = ""
    date: str = ""
    action_type: str = ""
    target: str = ""
    preconditions: dict[str, Any] = field(default_factory=dict)
    effects: dict[str, Any] = field(default_factory=dict)
    status: str = "proposed"
    reason: str = ""
    evidence: list[Any] = field(default_factory=list)
    started_at: str = ""
    committed_at: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class LifeActionReceiptRecord(_RecordMappingMixin):
    """可追溯的生活动作执行回执。"""

    id: int = 0
    receipt_id: str = ""
    action_id: str = ""
    date: str = ""
    action_type: str = ""
    status: str = "confirmed"
    evidence: list[Any] = field(default_factory=list)
    source: str = ""
    source_id: str = ""
    artifact_path: str = ""
    occurred_at: str = ""
    created_at: str = ""


@dataclass(slots=True)
class AffectiveStateRecord(_RecordMappingMixin):
    """短时、日级或关系级的可衰减情绪状态。"""

    id: int = 0
    scope: str = ""
    layer: str = "transient"
    label: str = ""
    valence: float = 0.0
    arousal: float = 0.5
    intensity: float = 0.5
    baseline: float = 0.5
    decay_half_life_minutes: float = 240.0
    evidence: list[Any] = field(default_factory=list)
    valid_from: str = ""
    valid_to: str = ""
    status: str = "active"
    source: str = "state"
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class GroundedDiaryEntryRecord(_RecordMappingMixin):
    """只引用已落库证据的第一人称日记条目。"""

    id: int = 0
    date: str = ""
    scope: str = ""
    title: str = ""
    summary: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    mood_label: str = ""
    source: str = "daily_review"
    created_at: str = ""
    updated_at: str = ""


__all__ = [
    "AffectiveStateRecord",
    "DecisionTraceRecord",
    "DurableTaskRecord",
    "FactEvidenceSignalRecord",
    "GroundedDiaryEntryRecord",
    "LifeActionOutcomeRecord",
    "LifeActionReceiptRecord",
    "PersonaAssertionRecord",
    "ReflectionRecord",
    "TemporalFactRecord",
]
