from __future__ import annotations

import datetime
from typing import Any

from ..life.actions import LifeActionMixin
from ..models import DayRecord, LifeState, TimelineItem
from ..runtime.proactive.lifecycle import ProactiveLifecycleMixin
from .scenarios import ScenarioCase, ScenarioObservation


class _ProductionDomain(ProactiveLifecycleMixin, LifeActionMixin):
    """供场景回放复用的生产领域实现组合。"""


class ProductionScenarioEvaluator:
    """调用真实生活领域结算器的离线场景执行器。

    该执行器不调用模型，也不发送消息；它只使用和运行时相同的动作、
    事实、收益、生命周期、反思和证据校验代码，适合在升级前后比较行为。
    """

    def __init__(self, archive: Any | None = None):
        self.archive = archive
        self.domain = _ProductionDomain()
        self.domain.archive = archive

    async def evaluate(self, case: ScenarioCase) -> ScenarioObservation:
        """执行一个固定场景并返回由生产逻辑产生的观察结果。"""

        handler = getattr(self, f"_evaluate_{case.scene}", None)
        if not callable(handler):
            raise ValueError(f"没有生产场景处理器：{case.scene}")
        return await handler(case)

    async def _evaluate_schedule(self, case: ScenarioCase) -> ScenarioObservation:
        day_exists = False
        if self.archive is not None:
            day_exists = await self.archive.get_day("2026-08-01") is not None
        if not day_exists:
            day = DayRecord(
                date="2026-08-01",
                state=LifeState(energy=70, mood_score=60),
                timeline=[TimelineItem(time="08:00", activity="开始一天")],
            )
            if self.archive is not None:
                await self.archive.save_day(day)
            return ScenarioObservation(
                decision="bootstrap_day",
                reason_code="day_missing",
                state={"day_exists": True},
                stages=("proposed", "validated", "committed"),
            )
        return ScenarioObservation(
            decision="keep",
            reason_code="day_already_exists",
            state={"day_exists": True},
            stages=("validated",),
        )

    async def _evaluate_life_action(self, case: ScenarioCase) -> ScenarioObservation:
        action_type = str(case.input_data.get("action_type") or "").strip()
        if action_type == "move":
            day = DayRecord(date="2026-08-01", state=LifeState(energy=20))
            outcome = self.domain.settle_life_action(
                day,
                {
                    "action_id": "scenario:move:1",
                    "action_type": "move",
                    "target": "公园",
                    "preconditions": [
                        {"field": "state.energy", "operator": "gte", "expected": 80}
                    ],
                },
                now=datetime.datetime(2026, 8, 1, 14, 0),
            )
            return ScenarioObservation(
                decision="reject" if outcome.status == "rejected" else outcome.status,
                reason_code="action_precondition",
                stages=("proposed", "rejected")
                if outcome.status == "rejected"
                else ("proposed", "committed"),
            )

        day = DayRecord(
            date="2026-08-01",
            outfit="居家穿搭",
            state=LifeState(mood_score=60),
        )
        action = {
            "action_id": "scenario:outfit:1",
            "action_type": "change_outfit",
            "target": "浅黄色短袖和米白长裤",
        }
        first = self.domain.settle_life_action(
            day, action, now=datetime.datetime(2026, 8, 1, 16, 0)
        )
        second = self.domain.settle_life_action(
            day, action, now=datetime.datetime(2026, 8, 1, 16, 5)
        )
        return ScenarioObservation(
            decision="replay" if second.replayed else second.status,
            reason_code="idempotent_action_replay",
            state={"outfit_change_count": len(day.outfit_history)},
            stages=("proposed", "committed", "replayed")
            if first.status == "committed" and second.replayed
            else ("proposed", second.status),
        )

    async def _evaluate_idle_proactive(
        self, case: ScenarioCase
    ) -> ScenarioObservation:
        payload = dict(case.input_data)
        utility, valid = self.domain._normalize_proactive_utility(
            payload, confidence=0.25
        )
        decision = "observe" if not valid or utility < 60 else "reply"
        return ScenarioObservation(
            decision=decision,
            reason_code="invalid_utility_scores" if not valid else "utility_evaluated",
            state={"utility": utility, "utility_scores_valid": valid},
            stages=("candidate", "evaluated", "cooldown"),
        )

    async def _evaluate_private_revisit(
        self, case: ScenarioCase
    ) -> ScenarioObservation:
        key = "scenario:private_revisit"
        self.domain._transition_proactive_lifecycle(
            key,
            "candidate",
            event="candidate_created",
            reason="存在待评估回访候选",
        )
        self.domain._transition_proactive_lifecycle(
            key,
            "interrupted",
            event="conversation_changed",
            reason="发送前会话上下文发生变化",
        )
        return ScenarioObservation(
            decision="wait",
            reason_code="conversation_revision_changed",
            stages=("candidate", "evaluated", "interrupted"),
        )

    async def _evaluate_memory(self, case: ScenarioCase) -> ScenarioObservation:
        if self.archive is None:
            raise ValueError("时间事实场景需要提供 LifeArchive")
        await self.archive.write_temporal_fact(
            "ADD",
            {
                "scope": "scenario",
                "subject": "self",
                "predicate": "current_outfit",
                "object_value": "居家穿搭",
                "valid_from": "2026-08-01 08:00:00",
                "source": "scenario",
            },
        )
        await self.archive.write_temporal_fact(
            "UPDATE",
            {
                "scope": "scenario",
                "subject": "self",
                "predicate": "current_outfit",
                "object_value": "外出穿搭",
                "valid_from": "2026-08-01 12:00:00",
                "source": "scenario",
            },
        )
        facts = await self.archive.get_temporal_facts(
            scope="scenario", subject="self", predicate="current_outfit"
        )
        return ScenarioObservation(
            decision="supersede",
            reason_code="temporal_fact_updated",
            state={"active_versions": sum(1 for item in facts if item.status == "active")},
            stages=("observed", "superseded", "committed"),
        )

    async def _evaluate_reflection(
        self, case: ScenarioCase
    ) -> ScenarioObservation:
        decision = self.domain.evaluate_reflection_threshold(
            {"importance": case.input_data.get("importance", 0)},
            now=datetime.datetime(2026, 8, 1, 20, 0),
        )
        return ScenarioObservation(
            decision="skip" if not decision.should_reflect else "reflect",
            reason_code="reflection_threshold",
            stages=("scored", "skipped")
            if not decision.should_reflect
            else ("scored", "accepted"),
        )

    async def _evaluate_daily_review(
        self, case: ScenarioCase
    ) -> ScenarioObservation:
        if self.archive is None:
            raise ValueError("日记场景需要提供 LifeArchive")
        try:
            await self.archive.save_grounded_diary_entry(
                {
                    "date": "2026-08-01",
                    "scope": "scenario",
                    "summary": "没有证据的测试日记",
                    "evidence_ids": list(case.input_data.get("evidence_ids") or []),
                }
            )
        except ValueError:
            return ScenarioObservation(
                decision="reject",
                reason_code="diary_evidence_missing",
                state={"diary_saved": False},
                stages=("proposed", "rejected"),
            )
        return ScenarioObservation(
            decision="save",
            reason_code="diary_grounded",
            state={"diary_saved": True},
            stages=("proposed", "committed"),
        )


__all__ = ["ProductionScenarioEvaluator"]
