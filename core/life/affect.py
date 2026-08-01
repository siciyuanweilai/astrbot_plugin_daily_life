from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Any

_AFFECT_LAYERS = {"transient", "daily", "relationship"}
_LAYER_DEFAULTS = {
    "transient": {"baseline": 0.05, "half_life": 180.0, "gain": 0.75},
    "daily": {"baseline": 0.35, "half_life": 1440.0, "gain": 0.35},
    "relationship": {"baseline": 0.50, "half_life": 10080.0, "gain": 0.12},
}


def _unit(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(number, 1.0))


def _valence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(-1.0, min(number, 1.0))


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _evidence_ids(value: Any, *, limit: int = 12) -> list[str]:
    raw = value if isinstance(value, list) else [value] if value else []
    result: list[str] = []
    for item in raw:
        evidence_id = _text(item, 120)
        if evidence_id and evidence_id not in result:
            result.append(evidence_id)
        if len(result) >= limit:
            break
    return result


@dataclass(slots=True)
class AffectSignal:
    """描述一次有明确证据的情绪变化信号。

    Attributes:
        layer: 变化所属层级：短时、当日或关系。
        label: 供展示和生成参考的自然情绪标签。
        valence: 情绪效价，范围为 -1 到 1。
        arousal: 唤醒程度，范围为 0 到 1。
        intensity: 本次信号强度，范围为 0 到 1。
        evidence_ids: 支撑变化的事件、消息或决策编号。
        source: 结构化信号来源。
    """

    layer: str = "transient"
    label: str = ""
    valence: float = 0.0
    arousal: float = 0.5
    intensity: float = 0.5
    evidence_ids: list[str] = field(default_factory=list)
    source: str = "state"

    @classmethod
    def from_value(cls, value: Any) -> AffectSignal | None:
        """从结构化对象创建情绪信号。

        Args:
            value: 包含层级、数值和证据编号的对象。

        Returns:
            合法信号；缺少层级、标签或证据时返回 ``None``。
        """

        if isinstance(value, AffectSignal):
            return value if value.evidence_ids else None
        if not isinstance(value, dict):
            return None
        layer = _text(value.get("layer"), 40).lower()
        label = _text(value.get("label"), 80)
        evidence = _evidence_ids(value.get("evidence_ids"))
        if layer not in _AFFECT_LAYERS or not label or not evidence:
            return None
        return cls(
            layer=layer,
            label=label,
            valence=_valence(value.get("valence")),
            arousal=_unit(value.get("arousal"), 0.5),
            intensity=_unit(value.get("intensity"), 0.5),
            evidence_ids=evidence,
            source=_text(value.get("source") or "state", 40) or "state",
        )


@dataclass(slots=True)
class AffectiveSnapshot:
    """表示某个情绪层在一个时间点的可衰减状态。"""

    scope: str = ""
    layer: str = "transient"
    label: str = ""
    valence: float = 0.0
    arousal: float = 0.5
    intensity: float = 0.05
    baseline: float = 0.05
    decay_half_life_minutes: float = 180.0
    evidence_ids: list[str] = field(default_factory=list)
    updated_at: datetime.datetime | None = None


@dataclass(slots=True)
class ReflectionGate:
    """表示是否值得触发一次模型反思及其数值依据。"""

    should_reflect: bool
    importance: float
    reason_code: str
    components: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class RelationshipUpdate:
    """描述一次缓慢且可追溯的关系数值变化。"""

    profile_id: str
    familiarity_delta: float = 0.0
    trust_delta: float = 0.0
    affinity_delta: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_value(cls, value: Any) -> RelationshipUpdate | None:
        """从结构化对象创建关系变化。

        Args:
            value: 包含对象编号、变化量和证据编号的对象。

        Returns:
            有证据且至少包含一个变化量的关系变化，否则返回 ``None``。
        """

        if not isinstance(value, dict):
            return None
        profile_id = _text(value.get("profile_id"), 180)
        evidence = _evidence_ids(value.get("evidence_ids"))
        if not profile_id or not evidence:
            return None

        def delta(name: str) -> float:
            try:
                number = float(value.get(name) or 0.0)
            except (TypeError, ValueError):
                number = 0.0
            return max(-0.08, min(number, 0.08))

        item = cls(
            profile_id=profile_id,
            familiarity_delta=delta("familiarity_delta"),
            trust_delta=delta("trust_delta"),
            affinity_delta=delta("affinity_delta"),
            evidence_ids=evidence,
            reason=_text(value.get("reason"), 240),
        )
        if not any(
            abs(number) > 0
            for number in (
                item.familiarity_delta,
                item.trust_delta,
                item.affinity_delta,
            )
        ):
            return None
        return item


class AffectEngine:
    """结算三层情绪、反思门槛和有证据的关系变化。"""

    def decay(
        self,
        state: AffectiveSnapshot,
        now: datetime.datetime,
    ) -> AffectiveSnapshot:
        """按半衰期把旧状态平滑拉回层级基线。

        Args:
            state: 上一次已结算的情绪状态。
            now: 当前结算时间。

        Returns:
            衰减到当前时间的独立状态快照。
        """

        defaults = _LAYER_DEFAULTS.get(state.layer, _LAYER_DEFAULTS["transient"])
        baseline = _unit(state.baseline, defaults["baseline"])
        half_life = max(float(state.decay_half_life_minutes or 0), 1.0)
        updated_at = state.updated_at or now
        elapsed = max((now - updated_at).total_seconds() / 60.0, 0.0)
        retention = math.pow(0.5, elapsed / half_life)
        return AffectiveSnapshot(
            scope=state.scope,
            layer=state.layer,
            label=state.label,
            valence=_valence(state.valence * retention),
            arousal=_unit(0.5 + (state.arousal - 0.5) * retention, 0.5),
            intensity=_unit(baseline + (state.intensity - baseline) * retention),
            baseline=baseline,
            decay_half_life_minutes=half_life,
            evidence_ids=list(state.evidence_ids),
            updated_at=now,
        )

    def apply(
        self,
        current: AffectiveSnapshot | None,
        signal: AffectSignal,
        *,
        scope: str,
        now: datetime.datetime,
    ) -> AffectiveSnapshot:
        """把一次显式信号结算到对应层级。

        Args:
            current: 该层已有状态；首次结算时可为空。
            signal: 已通过结构校验且带证据的情绪信号。
            scope: 会话、关系或全局生活范围。
            now: 当前结算时间。

        Returns:
            结算后的新状态快照。
        """

        defaults = _LAYER_DEFAULTS[signal.layer]
        existing = current or AffectiveSnapshot(
            scope=scope,
            layer=signal.layer,
            baseline=defaults["baseline"],
            intensity=defaults["baseline"],
            decay_half_life_minutes=defaults["half_life"],
            updated_at=now,
        )
        decayed = self.decay(existing, now)
        weight = _unit(defaults["gain"] * signal.intensity)
        evidence = list(decayed.evidence_ids)
        for evidence_id in signal.evidence_ids:
            if evidence_id not in evidence:
                evidence.append(evidence_id)
        return AffectiveSnapshot(
            scope=scope,
            layer=signal.layer,
            label=signal.label,
            valence=_valence(
                decayed.valence * (1.0 - weight) + signal.valence * weight
            ),
            arousal=_unit(
                decayed.arousal * (1.0 - weight) + signal.arousal * weight,
                0.5,
            ),
            intensity=_unit(decayed.intensity + (1.0 - decayed.intensity) * weight),
            baseline=decayed.baseline,
            decay_half_life_minutes=decayed.decay_half_life_minutes,
            evidence_ids=evidence[-12:],
            updated_at=now,
        )

    @staticmethod
    def reflection_gate(
        *,
        novelty: Any,
        emotional_intensity: Any,
        goal_impact: Any,
        social_impact: Any,
        threshold: float = 65.0,
    ) -> ReflectionGate:
        """按稳定权重判断本轮是否值得调用模型反思。

        Args:
            novelty: 新颖度，范围为 0 到 1。
            emotional_intensity: 情绪强度，范围为 0 到 1。
            goal_impact: 对计划或承诺的影响，范围为 0 到 1。
            social_impact: 对关系或互动的影响，范围为 0 到 1。
            threshold: 触发阈值，范围为 0 到 100。

        Returns:
            带有总分、各项分值和稳定原因码的门控结果。
        """

        components = {
            "novelty": _unit(novelty),
            "emotional_intensity": _unit(emotional_intensity),
            "goal_impact": _unit(goal_impact),
            "social_impact": _unit(social_impact),
        }
        importance = round(
            100.0
            * (
                components["novelty"] * 0.35
                + components["emotional_intensity"] * 0.30
                + components["goal_impact"] * 0.20
                + components["social_impact"] * 0.15
            ),
            2,
        )
        should_reflect = importance >= max(0.0, min(float(threshold), 100.0))
        return ReflectionGate(
            should_reflect=should_reflect,
            importance=importance,
            reason_code=(
                "importance_threshold_met"
                if should_reflect
                else "importance_below_threshold"
            ),
            components=components,
        )

    @staticmethod
    def signals_from_payload(payload: Any) -> list[AffectSignal]:
        """读取模型返回的显式情绪变化数组，不分析自由文本。"""

        raw = payload.get("affect_updates") if isinstance(payload, dict) else []
        if not isinstance(raw, list):
            return []
        return [
            item
            for item in (AffectSignal.from_value(value) for value in raw[:9])
            if item is not None
        ]

    @staticmethod
    def relationship_updates_from_payload(payload: Any) -> list[RelationshipUpdate]:
        """读取模型返回的显式关系变化数组，不分析自由文本。"""

        raw = payload.get("relationship_updates") if isinstance(payload, dict) else []
        if not isinstance(raw, list):
            return []
        return [
            item
            for item in (RelationshipUpdate.from_value(value) for value in raw[:6])
            if item is not None
        ]

    @staticmethod
    def grounded_diary_from_payload(
        payload: Any,
        *,
        date: str,
        allowed_evidence_ids: set[str],
        scope: str = "",
    ) -> dict[str, Any] | None:
        """只在证据编号可核验时构造第一人称日记记录。

        Args:
            payload: 包含 ``grounded_diary`` 对象的结构化复盘结果。
            date: 日记所属日期。
            allowed_evidence_ids: 本轮真实存在的事件、决策和反馈编号。
            scope: 可选的生活或关系范围。

        Returns:
            可直接持久化的日记对象；证据为空或越权时返回 ``None``。
        """

        raw = payload.get("grounded_diary") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return None
        summary = _text(raw.get("summary"), 500)
        evidence = [
            item
            for item in _evidence_ids(raw.get("evidence_ids"))
            if item in allowed_evidence_ids
        ]
        if not summary or not evidence:
            return None
        return {
            "date": _text(date, 20),
            "scope": _text(scope, 180),
            "title": _text(raw.get("title") or "今天", 80),
            "summary": summary,
            "evidence_ids": evidence,
            "mood_label": _text(raw.get("mood_label"), 80),
            "source": "daily_review",
        }


__all__ = [
    "AffectEngine",
    "AffectSignal",
    "AffectiveSnapshot",
    "ReflectionGate",
    "RelationshipUpdate",
]
