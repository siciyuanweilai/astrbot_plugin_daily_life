from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ScenarioCase:
    """描述一个可重复执行的虚拟生活行为场景。"""

    case_id: str
    scene: str
    input_data: dict[str, Any]
    expected_decision: str
    allowed_claims: set[str] = field(default_factory=set)
    expected_state: dict[str, Any] = field(default_factory=dict)
    required_stages: tuple[str, ...] = ()


@dataclass(slots=True)
class ScenarioObservation:
    """描述被测实现对一个场景给出的结构化结果。"""

    decision: str
    reason_code: str = ""
    claims: set[str] = field(default_factory=set)
    state: dict[str, Any] = field(default_factory=dict)
    stages: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any) -> ScenarioObservation:
        """把字典或观察对象归一为稳定结构。"""

        if isinstance(value, ScenarioObservation):
            return value
        raw = value if isinstance(value, dict) else {}
        claims = raw.get("claims") if isinstance(raw.get("claims"), list) else []
        stages = raw.get("stages") if isinstance(raw.get("stages"), list) else []
        state = raw.get("state") if isinstance(raw.get("state"), dict) else {}
        return cls(
            decision=str(raw.get("decision") or "").strip(),
            reason_code=str(raw.get("reason_code") or "").strip(),
            claims={str(item).strip() for item in claims if str(item).strip()},
            state=dict(state),
            stages=tuple(str(item).strip() for item in stages if str(item).strip()),
        )


@dataclass(slots=True)
class ScenarioResult:
    """保存单个场景的各项可解释得分。"""

    case_id: str
    passed: bool
    decision_match: bool
    unsupported_claims: list[str]
    missing_stages: list[str]
    state_mismatches: dict[str, dict[str, Any]]
    reason_code: str


@dataclass(slots=True)
class ScenarioReport:
    """汇总整批离线场景的回归指标。"""

    total: int
    passed: int
    score: float
    decision_accuracy: float
    unsupported_claim_rate: float
    lifecycle_completeness: float
    state_continuity: float
    proactive_overreach_rate: float
    results: list[ScenarioResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """返回可直接写入日志或面板的字典。"""

        return {
            "total": self.total,
            "passed": self.passed,
            "score": self.score,
            "decision_accuracy": self.decision_accuracy,
            "unsupported_claim_rate": self.unsupported_claim_rate,
            "lifecycle_completeness": self.lifecycle_completeness,
            "state_continuity": self.state_continuity,
            "proactive_overreach_rate": self.proactive_overreach_rate,
            "results": [
                {
                    "case_id": item.case_id,
                    "passed": item.passed,
                    "decision_match": item.decision_match,
                    "unsupported_claims": list(item.unsupported_claims),
                    "missing_stages": list(item.missing_stages),
                    "state_mismatches": dict(item.state_mismatches),
                    "reason_code": item.reason_code,
                }
                for item in self.results
            ],
        }


class ScenarioRunner:
    """执行确定性场景回放并计算可比较的质量指标。"""

    async def run(
        self,
        cases: list[ScenarioCase],
        evaluator: Callable[
            [ScenarioCase],
            ScenarioObservation
            | dict[str, Any]
            | Awaitable[ScenarioObservation | dict[str, Any]],
        ],
    ) -> ScenarioReport:
        """依次执行场景并生成聚合报告。

        Args:
            cases: 固定输入与预期结果组成的场景集合。
            evaluator: 接收场景并返回结构化观察结果的被测函数。

        Returns:
            包含决策、事实边界、生命周期和连续性指标的报告。
        """

        results: list[ScenarioResult] = []
        total_claims = 0
        unsupported_count = 0
        expected_stages = 0
        present_stages = 0
        expected_state_fields = 0
        matched_state_fields = 0
        proactive_cases = 0
        proactive_overreach = 0

        for case in cases:
            raw = evaluator(case)
            if hasattr(raw, "__await__"):
                raw = await raw
            observation = ScenarioObservation.from_value(raw)
            decision_match = observation.decision == case.expected_decision
            unsupported = sorted(observation.claims - case.allowed_claims)
            missing_stages = [
                stage
                for stage in case.required_stages
                if stage not in observation.stages
            ]
            state_mismatches: dict[str, dict[str, Any]] = {}
            for key, expected in case.expected_state.items():
                actual = observation.state.get(key)
                if actual != expected:
                    state_mismatches[key] = {"expected": expected, "actual": actual}

            total_claims += len(observation.claims)
            unsupported_count += len(unsupported)
            expected_stages += len(case.required_stages)
            present_stages += len(case.required_stages) - len(missing_stages)
            expected_state_fields += len(case.expected_state)
            matched_state_fields += len(case.expected_state) - len(state_mismatches)
            if case.scene in {"idle_proactive", "private_revisit"}:
                proactive_cases += 1
                if (
                    observation.decision == "reply"
                    and case.expected_decision != "reply"
                ):
                    proactive_overreach += 1

            passed = (
                decision_match
                and not unsupported
                and not missing_stages
                and not state_mismatches
            )
            results.append(
                ScenarioResult(
                    case_id=case.case_id,
                    passed=passed,
                    decision_match=decision_match,
                    unsupported_claims=unsupported,
                    missing_stages=missing_stages,
                    state_mismatches=state_mismatches,
                    reason_code=observation.reason_code,
                )
            )

        total = len(results)
        decision_accuracy = self._ratio(
            sum(1 for item in results if item.decision_match), total
        )
        unsupported_claim_rate = (
            self._ratio(unsupported_count, total_claims) if total_claims else 0.0
        )
        lifecycle_completeness = self._ratio(present_stages, expected_stages)
        state_continuity = self._ratio(matched_state_fields, expected_state_fields)
        proactive_overreach_rate = self._ratio(proactive_overreach, proactive_cases)
        score = round(
            100.0
            * (
                decision_accuracy * 0.35
                + (1.0 - unsupported_claim_rate) * 0.25
                + lifecycle_completeness * 0.15
                + state_continuity * 0.15
                + (1.0 - proactive_overreach_rate) * 0.10
            ),
            2,
        )
        return ScenarioReport(
            total=total,
            passed=sum(1 for item in results if item.passed),
            score=score,
            decision_accuracy=round(decision_accuracy, 4),
            unsupported_claim_rate=round(unsupported_claim_rate, 4),
            lifecycle_completeness=round(lifecycle_completeness, 4),
            state_continuity=round(state_continuity, 4),
            proactive_overreach_rate=round(proactive_overreach_rate, 4),
            results=results,
        )

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return float(numerator) / float(denominator) if denominator else 1.0


def default_virtual_life_scenarios() -> list[ScenarioCase]:
    """返回覆盖核心行为边界的默认场景集合。"""

    return [
        ScenarioCase(
            case_id="first_install_bootstrap",
            scene="schedule",
            input_data={"day_exists": False, "timeline": []},
            expected_decision="bootstrap_day",
            expected_state={"day_exists": True},
            required_stages=("proposed", "validated", "committed"),
        ),
        ScenarioCase(
            case_id="outfit_duplicate_change",
            scene="life_action",
            input_data={"outfit_changed": True, "new_evidence": False},
            expected_decision="replay",
            expected_state={"outfit_change_count": 1},
            required_stages=("proposed", "committed", "replayed"),
        ),
        ScenarioCase(
            case_id="action_precondition_failed",
            scene="life_action",
            input_data={"action_type": "move", "preconditions_met": False},
            expected_decision="reject",
            required_stages=("proposed", "rejected"),
        ),
        ScenarioCase(
            case_id="idle_low_utility",
            scene="idle_proactive",
            input_data={"benefit": 25, "disruption": 70},
            expected_decision="observe",
            required_stages=("candidate", "evaluated", "cooldown"),
        ),
        ScenarioCase(
            case_id="revisit_interrupted",
            scene="private_revisit",
            input_data={"context_changed_before_send": True},
            expected_decision="wait",
            required_stages=("candidate", "evaluated", "interrupted"),
        ),
        ScenarioCase(
            case_id="temporal_fact_superseded",
            scene="memory",
            input_data={"operation": "UPDATE", "same_fact_key": True},
            expected_decision="supersede",
            expected_state={"active_versions": 1},
            required_stages=("observed", "superseded", "committed"),
        ),
        ScenarioCase(
            case_id="reflection_below_threshold",
            scene="reflection",
            input_data={"importance": 42},
            expected_decision="skip",
            required_stages=("scored", "skipped"),
        ),
        ScenarioCase(
            case_id="diary_without_evidence",
            scene="daily_review",
            input_data={"evidence_ids": []},
            expected_decision="reject",
            expected_state={"diary_saved": False},
            required_stages=("proposed", "rejected"),
        ),
    ]


__all__ = [
    "ScenarioCase",
    "ScenarioObservation",
    "ScenarioReport",
    "ScenarioRunner",
    "default_virtual_life_scenarios",
]
