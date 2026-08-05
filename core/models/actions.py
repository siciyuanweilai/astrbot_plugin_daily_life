from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LIFE_ACTION_TYPES = frozenset(
    {
        "rest",
        "meal",
        "cook",
        "order_food",
        "purchase",
        "move",
        "travel",
        "work",
        "study",
        "chore",
        "exercise",
        "groom",
        "change_outfit",
        "social",
        "chat",
        "photo",
        "video",
    }
)

EXTERNAL_RECEIPT_ACTION_TYPES = frozenset({"social", "chat", "photo", "video"})
INTERNAL_SIMULATED_ACTION_TYPES = LIFE_ACTION_TYPES - EXTERNAL_RECEIPT_ACTION_TYPES


def _text(value: Any, limit: int = 160) -> str:
    """压缩模型文本字段。

    Args:
        value: 原始字段值。
        limit: 最大字符数。

    Returns:
        去除多余空白并截断后的文本。
    """
    return " ".join(str(value or "").split())[:limit]


def _score(value: Any) -> float:
    """把评分字段约束到零至一百。

    Args:
        value: 原始评分。

    Returns:
        约束后的浮点评分。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return round(max(0.0, min(100.0, number)), 2)


@dataclass(slots=True)
class LifeActionPrecondition:
    """生活动作的显式前置条件。"""

    field: str = ""
    operator: str = "eq"
    expected: Any = None

    @staticmethod
    def from_value(value: Any) -> LifeActionPrecondition | None:
        """从结构化值创建前置条件。

        Args:
            value: 包含字段、运算符和期望值的字典。

        Returns:
            有效的前置条件；字段或运算符无效时返回空。
        """
        raw = value if isinstance(value, dict) else {}
        field_name = _text(raw.get("field"), 60)
        operator = _text(raw.get("operator") or "eq", 16).lower()
        if not field_name or operator not in {
            "eq",
            "ne",
            "gte",
            "lte",
            "in",
            "not_in",
            "present",
        }:
            return None
        return LifeActionPrecondition(
            field=field_name,
            operator=operator,
            expected=raw.get("expected"),
        )

    def as_dict(self) -> dict[str, Any]:
        """序列化前置条件。

        Returns:
            可写入 JSON 的字典。
        """
        return {
            "field": self.field,
            "operator": self.operator,
            "expected": self.expected,
        }


@dataclass(slots=True)
class LifeActionEffect:
    """生活动作对数值状态产生的显式影响。"""

    field: str = ""
    operation: str = "add"
    value: float = 0.0

    @staticmethod
    def from_value(value: Any) -> LifeActionEffect | None:
        """从结构化值创建状态影响。

        Args:
            value: 包含字段、操作和值的字典。

        Returns:
            有效的状态影响；内容无效时返回空。
        """
        raw = value if isinstance(value, dict) else {}
        field_name = _text(raw.get("field"), 60)
        operation = _text(raw.get("operation") or "add", 16).lower()
        try:
            number = float(raw.get("value"))
        except (TypeError, ValueError):
            return None
        if not field_name or operation not in {"add", "set"}:
            return None
        return LifeActionEffect(
            field=field_name,
            operation=operation,
            value=round(number, 2),
        )

    def as_dict(self) -> dict[str, Any]:
        """序列化状态影响。

        Returns:
            可写入 JSON 的字典。
        """
        return {
            "field": self.field,
            "operation": self.operation,
            "value": self.value,
        }


@dataclass(slots=True)
class LifeActionIntent:
    """等待可行性校验和结算的结构化生活动作。"""

    action_id: str = ""
    action_type: str = ""
    target: str = ""
    timeline_index: int | None = None
    requested_at: str = ""
    duration_minutes: int = 0
    preconditions: list[LifeActionPrecondition] = field(default_factory=list)
    effects: list[LifeActionEffect] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    evidence: str = ""
    source: str = ""

    @staticmethod
    def from_value(value: Any) -> LifeActionIntent:
        """从字典创建动作意图。

        Args:
            value: 原始动作意图。

        Returns:
            已规范化的动作意图。
        """
        if isinstance(value, LifeActionIntent):
            return value
        raw = value if isinstance(value, dict) else {}
        timeline_index = raw.get("timeline_index")
        try:
            timeline_index = int(timeline_index) if timeline_index is not None else None
        except (TypeError, ValueError):
            timeline_index = None
        try:
            duration_minutes = int(raw.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            duration_minutes = 0
        conditions = []
        for item in raw.get("preconditions") or []:
            condition = LifeActionPrecondition.from_value(item)
            if condition:
                conditions.append(condition)
        effects = []
        for item in raw.get("effects") or []:
            effect = LifeActionEffect.from_value(item)
            if effect:
                effects.append(effect)
        return LifeActionIntent(
            action_id=_text(raw.get("action_id"), 80),
            action_type=_text(raw.get("action_type"), 32).lower(),
            target=_text(raw.get("target"), 200),
            timeline_index=timeline_index,
            requested_at=_text(raw.get("requested_at"), 32),
            duration_minutes=max(0, min(1440, duration_minutes)),
            preconditions=conditions[:12],
            effects=effects[:12],
            payload=dict(raw.get("payload"))
            if isinstance(raw.get("payload"), dict)
            else {},
            evidence=_text(raw.get("evidence"), 240),
            source=_text(raw.get("source"), 60),
        )

    def as_dict(self) -> dict[str, Any]:
        """序列化动作意图。

        Returns:
            可写入 JSON 的字典。
        """
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "target": self.target,
            "timeline_index": self.timeline_index,
            "requested_at": self.requested_at,
            "duration_minutes": self.duration_minutes,
            "preconditions": [item.as_dict() for item in self.preconditions],
            "effects": [item.as_dict() for item in self.effects],
            "payload": dict(self.payload),
            "evidence": self.evidence,
            "source": self.source,
        }


@dataclass(slots=True)
class LifeActionOutcome:
    """生活动作经过校验和结算后的结果。"""

    action_id: str = ""
    action_type: str = ""
    status: str = "rejected"
    reason: str = ""
    committed_at: str = ""
    state_changes: dict[str, dict[str, float | int | None]] = field(
        default_factory=dict
    )
    timeline_index: int | None = None
    evidence: str = ""
    replayed: bool = False

    @staticmethod
    def from_value(value: Any) -> LifeActionOutcome:
        """从持久化值恢复动作结果。

        Args:
            value: 原始结果字典。

        Returns:
            已规范化的动作结果。
        """
        if isinstance(value, LifeActionOutcome):
            return value
        raw = value if isinstance(value, dict) else {}
        timeline_index = raw.get("timeline_index")
        try:
            timeline_index = int(timeline_index) if timeline_index is not None else None
        except (TypeError, ValueError):
            timeline_index = None
        changes = raw.get("state_changes")
        return LifeActionOutcome(
            action_id=_text(raw.get("action_id"), 80),
            action_type=_text(raw.get("action_type"), 32).lower(),
            status=_text(raw.get("status") or "rejected", 24).lower(),
            reason=_text(raw.get("reason"), 240),
            committed_at=_text(raw.get("committed_at"), 32),
            state_changes=dict(changes) if isinstance(changes, dict) else {},
            timeline_index=timeline_index,
            evidence=_text(raw.get("evidence"), 240),
            replayed=bool(raw.get("replayed")),
        )

    def as_dict(self) -> dict[str, Any]:
        """序列化动作结果。

        Returns:
            可写入 JSON 的字典。
        """
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "status": self.status,
            "reason": self.reason,
            "committed_at": self.committed_at,
            "state_changes": dict(self.state_changes),
            "timeline_index": self.timeline_index,
            "evidence": self.evidence,
            "replayed": self.replayed,
        }


@dataclass(slots=True)
class ScheduleAnchor:
    """从全天时间轴提炼出的稳定日程锚点。"""

    anchor_id: str = ""
    time: str = ""
    activity: str = ""
    status: str = ""
    source_index: int = -1
    execution_state: str = "planned"
    refinement_state: str = "anchor"
    replaces_anchor_id: str = ""
    evidence: str = ""

    @staticmethod
    def from_value(value: Any) -> ScheduleAnchor:
        """从结构化值创建日程锚点。

        Args:
            value: 原始锚点字典。

        Returns:
            已规范化的日程锚点。
        """
        if isinstance(value, ScheduleAnchor):
            return value
        raw = value if isinstance(value, dict) else {}
        try:
            source_index = int(raw.get("source_index", -1))
        except (TypeError, ValueError):
            source_index = -1
        return ScheduleAnchor(
            anchor_id=_text(raw.get("anchor_id"), 100),
            time=_text(raw.get("time"), 5),
            activity=_text(raw.get("activity"), 200),
            status=_text(raw.get("status"), 120),
            source_index=source_index,
            execution_state=_text(raw.get("execution_state") or "planned", 24).lower(),
            refinement_state=_text(raw.get("refinement_state") or "anchor", 24).lower(),
            replaces_anchor_id=_text(raw.get("replaces_anchor_id"), 100),
            evidence=_text(raw.get("evidence"), 240),
        )

    def as_dict(self) -> dict[str, Any]:
        """序列化日程锚点。

        Returns:
            可写入 JSON 的字典。
        """
        return {
            "anchor_id": self.anchor_id,
            "time": self.time,
            "activity": self.activity,
            "status": self.status,
            "source_index": self.source_index,
            "execution_state": self.execution_state,
            "refinement_state": self.refinement_state,
            "replaces_anchor_id": self.replaces_anchor_id,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class PlanRevision:
    """局部日程重排的校验与应用结果。"""

    status: str = "rejected"
    reason: str = ""
    applied_anchor_ids: list[str] = field(default_factory=list)
    changed_indexes: list[int] = field(default_factory=list)
    revised_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        """序列化重排结果。

        Returns:
            可写入 JSON 的字典。
        """
        return {
            "status": self.status,
            "reason": self.reason,
            "applied_anchor_ids": list(self.applied_anchor_ids),
            "changed_indexes": list(self.changed_indexes),
            "revised_at": self.revised_at,
        }


@dataclass(slots=True)
class ReflectionSignal:
    """触发生活反思的结构化评分信号。"""

    importance: float = 0.0
    novelty: float = 0.0
    emotional_intensity: float = 0.0
    recurrence: float = 0.0
    observed_at: str = ""
    evidence: list[str] = field(default_factory=list)

    @staticmethod
    def from_value(value: Any) -> ReflectionSignal:
        """从结构化值创建反思信号。

        Args:
            value: 原始评分字典。

        Returns:
            已约束到零至一百的反思信号。
        """
        if isinstance(value, ReflectionSignal):
            return value
        raw = value if isinstance(value, dict) else {}
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
        return ReflectionSignal(
            importance=_score(raw.get("importance")),
            novelty=_score(raw.get("novelty")),
            emotional_intensity=_score(raw.get("emotional_intensity")),
            recurrence=_score(raw.get("recurrence")),
            observed_at=_text(raw.get("observed_at"), 32),
            evidence=[_text(item, 160) for item in evidence if _text(item, 160)][:8],
        )

    def as_dict(self) -> dict[str, Any]:
        """序列化反思信号。

        Returns:
            可写入 JSON 的字典。
        """
        return {
            "importance": self.importance,
            "novelty": self.novelty,
            "emotional_intensity": self.emotional_intensity,
            "recurrence": self.recurrence,
            "observed_at": self.observed_at,
            "evidence": list(self.evidence),
        }


@dataclass(slots=True)
class ReflectionDecision:
    """反思评分器的确定性决策。"""

    should_reflect: bool = False
    score: float = 0.0
    threshold: float = 65.0
    reason: str = ""
    next_eligible_at: str = ""
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """序列化反思决策。

        Returns:
            可写入 JSON 的字典。
        """
        return {
            "should_reflect": self.should_reflect,
            "score": self.score,
            "threshold": self.threshold,
            "reason": self.reason,
            "next_eligible_at": self.next_eligible_at,
            "evidence": list(self.evidence),
        }


__all__ = [
    "EXTERNAL_RECEIPT_ACTION_TYPES",
    "INTERNAL_SIMULATED_ACTION_TYPES",
    "LIFE_ACTION_TYPES",
    "LifeActionEffect",
    "LifeActionIntent",
    "LifeActionOutcome",
    "LifeActionPrecondition",
    "PlanRevision",
    "ReflectionDecision",
    "ReflectionSignal",
    "ScheduleAnchor",
]
