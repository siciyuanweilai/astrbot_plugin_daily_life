from __future__ import annotations

import datetime
import json
from typing import Any

from astrbot.api import logger

from ..clock import now as life_now
from ..models import (
    LIFE_ACTION_TYPES,
    DayRecord,
    LifeActionEffect,
    LifeActionIntent,
    LifeActionOutcome,
    LifeState,
    PlaceRecord,
    PlanRevision,
    ReflectionDecision,
    ReflectionSignal,
    ScheduleAnchor,
)
from .future import outfit_descriptions_match
from .tools import parse_life_datetime, timeline_item_datetime

ACTION_SETTLEMENT_META_KEY = "life_action_settlements"
ACTION_EXPIRATION_META_KEY = "life_action_expirations"
SCHEDULE_ANCHOR_META_KEY = "schedule_anchors"
SCHEDULE_REPLAN_META_KEY = "schedule_replan_pending"

_NUMERIC_STATE_FIELDS = frozenset(
    {
        "energy",
        "mood_score",
        "busyness",
        "social",
        "stress",
        "focus",
        "sleepiness",
        "outgoing",
        "emotional_stability",
        "interaction_capacity",
        "boredom",
        "fishing",
        "attention_openness",
    }
)

_ACTION_RULES: dict[str, dict[str, Any]] = {
    "rest": {
        "effects": (("energy", 12), ("stress", -8), ("sleepiness", -12)),
        "allowed": {"energy", "stress", "sleepiness", "mood_score"},
    },
    "meal": {
        "effects": (("energy", 8), ("stress", -2), ("mood_score", 3)),
        "allowed": {"energy", "stress", "mood_score"},
    },
    "cook": {
        "effects": (("energy", 5), ("stress", -3), ("mood_score", 4)),
        "allowed": {"energy", "stress", "mood_score"},
        "minimum": ("energy", 8),
    },
    "order_food": {
        "effects": (("energy", 7), ("stress", -2), ("mood_score", 2)),
        "allowed": {"energy", "stress", "mood_score"},
    },
    "purchase": {
        "effects": (("energy", -3), ("busyness", 2)),
        "allowed": {"energy", "busyness", "stress", "mood_score"},
        "minimum": ("energy", 5),
    },
    "move": {
        "effects": (("energy", -5), ("stress", -5), ("mood_score", 4)),
        "allowed": {"energy", "stress", "mood_score", "sleepiness"},
        "minimum": ("energy", 10),
    },
    "travel": {
        "effects": (("energy", -6), ("stress", -2), ("mood_score", 2)),
        "allowed": {"energy", "stress", "mood_score", "sleepiness"},
        "minimum": ("energy", 10),
        "requires_target": True,
    },
    "work": {
        "effects": (("energy", -10), ("stress", 4), ("focus", -8)),
        "allowed": {"energy", "stress", "focus", "busyness"},
        "minimum": ("energy", 10),
    },
    "study": {
        "effects": (("energy", -8), ("stress", 3), ("focus", -6)),
        "allowed": {"energy", "stress", "focus", "mood_score"},
        "minimum": ("energy", 10),
    },
    "chore": {
        "effects": (("energy", -6), ("stress", -4), ("mood_score", 3)),
        "allowed": {"energy", "stress", "mood_score", "busyness"},
        "minimum": ("energy", 8),
        "requires_target": True,
    },
    "exercise": {
        "effects": (("energy", -10), ("stress", -7), ("mood_score", 5)),
        "allowed": {"energy", "stress", "mood_score", "sleepiness"},
        "minimum": ("energy", 15),
        "requires_target": True,
    },
    "groom": {
        "effects": (("mood_score", 4), ("stress", -2)),
        "allowed": {"mood_score", "stress"},
    },
    "change_outfit": {
        "effects": (("mood_score", 2),),
        "allowed": {"mood_score", "stress"},
        "requires_target": True,
    },
    "social": {
        "effects": (
            ("energy", -8),
            ("social", 8),
            ("interaction_capacity", -8),
            ("mood_score", 4),
        ),
        "allowed": {
            "energy",
            "social",
            "interaction_capacity",
            "mood_score",
            "stress",
        },
        "minimum": ("interaction_capacity", 8),
    },
    "chat": {
        "effects": (
            ("energy", -3),
            ("social", 4),
            ("interaction_capacity", -3),
        ),
        "allowed": {
            "energy",
            "social",
            "interaction_capacity",
            "mood_score",
        },
        "minimum": ("interaction_capacity", 5),
    },
    "photo": {
        "effects": (("energy", -4), ("focus", -2), ("mood_score", 3)),
        "allowed": {"energy", "focus", "mood_score", "stress"},
        "minimum": ("energy", 8),
    },
    "video": {
        "effects": (("energy", -8), ("focus", -5), ("mood_score", 4)),
        "allowed": {"energy", "focus", "mood_score", "stress"},
        "minimum": ("energy", 8),
    },
}


class LifeActionMixin:
    """提供生活动作结算、分层日程和反思门槛能力。"""

    @staticmethod
    def _load_action_settlements(day: DayRecord) -> dict[str, Any]:
        stored_text = str((day.meta or {}).get(ACTION_SETTLEMENT_META_KEY) or "")
        if not stored_text:
            return {}
        try:
            stored_value = json.loads(stored_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return stored_value if isinstance(stored_value, dict) else {}

    @staticmethod
    def _validate_action_contract(
        day: DayRecord,
        action: LifeActionIntent,
        rule: dict[str, Any],
        state: LifeState | None,
    ) -> str:
        if not action.action_id:
            return "缺少 action_id，无法保证动作幂等"
        if action.action_type not in LIFE_ACTION_TYPES:
            return "action_type 不在受支持的生活动作集合中"
        if rule.get("requires_target") and not action.target:
            return "该动作需要明确 target"
        if action.timeline_index is not None:
            if not 0 <= action.timeline_index < len(day.timeline):
                return "timeline_index 超出当日时间轴范围"
            timeline_state = day.timeline[action.timeline_index].execution_state
            if timeline_state in {"skipped", "cancelled"} or (
                timeline_state == "completed" and action.source != "daily_plan"
            ):
                return "对应日程已经收束，不能重复结算新动作"

        minimum = rule.get("minimum")
        if minimum and state is not None:
            minimum_field, minimum_value = minimum
            current_value = getattr(state, minimum_field, None)
            if current_value is not None and current_value < minimum_value:
                return f"{minimum_field} 低于动作最低可行值 {minimum_value}"
        return ""

    @staticmethod
    def _action_condition_value(
        day: DayRecord,
        state: LifeState | None,
        action: LifeActionIntent,
        field: str,
    ) -> tuple[bool, Any]:
        if field.startswith("state."):
            field_name = field.removeprefix("state.")
            if field_name not in _NUMERIC_STATE_FIELDS and field_name not in {
                "mood",
                "watch_state",
                "interrupt_level",
            }:
                return False, None
            return True, getattr(state, field_name, None) if state else None
        if field == "timeline.execution_state":
            if action.timeline_index is None:
                return False, None
            return True, day.timeline[action.timeline_index].execution_state
        if field in {
            "day.date",
            "day.outfit",
            "day.time_period",
            "day.current_place",
        }:
            field_name = field.removeprefix("day.")
            if field_name == "current_place":
                return True, str((day.meta or {}).get("current_place") or "").strip()
            return True, getattr(day, field_name, None)
        if field.startswith("weather."):
            field_name = field.removeprefix("weather.")
            actual = getattr(day.weather_info, field_name, None)
            return actual is not None, actual
        return False, None

    @staticmethod
    def _action_condition_matches(
        *, known: bool, actual: Any, operator: str, expected: Any
    ) -> bool:
        if operator == "present":
            return known and actual is not None and actual != ""
        if operator == "eq":
            return known and actual == expected
        if operator == "ne":
            return known and actual != expected
        if operator in {"in", "not_in"}:
            if not known or not isinstance(expected, (list, tuple, set)):
                return False
            contained = actual in expected
            return contained if operator == "in" else not contained
        if operator in {"gte", "lte"}:
            if not known:
                return False
            try:
                left = float(actual)
                right = float(expected)
            except (TypeError, ValueError):
                return False
            return left >= right if operator == "gte" else left <= right
        return False

    @classmethod
    def _validate_action_preconditions(
        cls,
        day: DayRecord,
        state: LifeState | None,
        action: LifeActionIntent,
    ) -> str:
        for condition in action.preconditions:
            known, actual = cls._action_condition_value(
                day, state, action, condition.field
            )
            if not cls._action_condition_matches(
                known=known,
                actual=actual,
                operator=condition.operator,
                expected=condition.expected,
            ):
                return f"前置条件未满足：{condition.field} {condition.operator}"
        return ""

    @staticmethod
    def _resolve_action_effects(
        action: LifeActionIntent, rule: dict[str, Any]
    ) -> list[LifeActionEffect]:
        return action.effects or [
            LifeActionEffect(field=field_name, operation="add", value=value)
            for field_name, value in rule.get("effects", ())
        ]

    @staticmethod
    def _validate_action_effects(
        effects: list[LifeActionEffect], rule: dict[str, Any]
    ) -> str:
        allowed_effects = rule.get("allowed", set())
        for effect in effects:
            if (
                effect.field not in _NUMERIC_STATE_FIELDS
                or effect.field not in allowed_effects
            ):
                return f"动作不允许修改状态字段：{effect.field}"
        return ""

    @staticmethod
    def _commit_action_effects(
        day: DayRecord,
        action: LifeActionIntent,
        effects: list[LifeActionEffect],
        outcome: LifeActionOutcome,
        committed_at: str,
        *,
        preserve_outfit_fact: bool = False,
    ) -> None:
        state = day.state or LifeState()
        day.state = state
        changes: dict[str, dict[str, float | int | None]] = {}
        applicable_effects = [] if preserve_outfit_fact else effects
        for effect in applicable_effects:
            previous = getattr(state, effect.field, None)
            base_value = float(previous) if previous is not None else 50.0
            updated = (
                base_value + effect.value if effect.operation == "add" else effect.value
            )
            updated = round(max(0.0, min(100.0, updated)), 2)
            normalized: int | float = int(updated) if updated.is_integer() else updated
            setattr(state, effect.field, normalized)
            changes[effect.field] = {"before": previous, "after": normalized}
        outcome.state_changes = changes
        if not preserve_outfit_fact:
            state.updated_at = committed_at
            state.source = f"life_action:{action.action_type}"

        if action.action_type == "change_outfit" and not preserve_outfit_fact:
            current_outfit = str(day.outfit or "").strip()
            resolved_outfit = (
                current_outfit
                if current_outfit
                and outfit_descriptions_match(current_outfit, action.target)
                else action.target
            )
            day.outfit = resolved_outfit
            day.outfit_history[committed_at] = resolved_outfit
            day.meta["outfit_decision"] = "life_action"
            day.meta["outfit_fact_source"] = "life_action"
            day.meta["outfit_fact_confirmed_at"] = committed_at
            day.meta["outfit_fact_evidence"] = action.action_id
        if action.action_type in {"move", "travel"} and action.target:
            previous_place = str((day.meta or {}).get("current_place") or "").strip()
            if previous_place:
                day.meta["previous_place"] = previous_place
            day.meta["current_place"] = action.target
            if not any(place.name == action.target for place in day.places):
                day.places.append(
                    PlaceRecord(
                        name=action.target,
                        source="life_action",
                        last_seen=committed_at,
                    )
                )
        if action.timeline_index is not None:
            item = day.timeline[action.timeline_index]
            item.execution_state = "completed"
            item.execution_reason = f"生活动作已结算：{action.action_type}"
            item.execution_evidence = action.evidence or action.action_id
            item.execution_updated_at = committed_at

    @staticmethod
    def _planned_outfit_action_is_superseded(
        day: DayRecord, action: LifeActionIntent
    ) -> bool:
        """判断旧日程换装是否已被稍后的用户明确穿搭替代。"""
        if (
            action.action_type != "change_outfit"
            or action.timeline_index is None
            or not 0 <= action.timeline_index < len(day.timeline)
            or str(day.meta.get("outfit_fact_source") or "").strip()
            != "user_instruction"
        ):
            return False
        confirmed_at = parse_life_datetime(
            day.meta.get("outfit_fact_confirmed_at")
        )
        scheduled_at = timeline_item_datetime(
            day.timeline[action.timeline_index], day.date
        )
        return bool(
            confirmed_at is not None
            and scheduled_at is not None
            and scheduled_at <= confirmed_at
        )

    def settle_life_action(
        self,
        day: DayRecord,
        intent: LifeActionIntent | dict[str, Any],
        *,
        now: datetime.datetime | None = None,
    ) -> LifeActionOutcome:
        """校验并幂等结算一项显式生活动作。

        Args:
            day: 动作发生的当日日记录。
            intent: 包含动作类型、前置条件和影响的结构化意图。
            now: 结算时间，缺省时使用插件时钟。

        Returns:
            已提交、拒绝或从历史结算中重放的动作结果。
        """
        action = LifeActionIntent.from_value(intent)
        current_time = now or life_now()
        committed_at = current_time.strftime("%Y-%m-%d %H:%M:%S")
        settlements = self._load_action_settlements(day)

        if action.action_id and isinstance(settlements.get(action.action_id), dict):
            replayed = LifeActionOutcome.from_value(settlements[action.action_id])
            replayed.replayed = True
            return replayed

        rule = _ACTION_RULES.get(action.action_type, {})
        state = day.state
        reason = self._validate_action_contract(day, action, rule, state)
        if not reason:
            reason = self._validate_action_preconditions(day, state, action)
        effects = self._resolve_action_effects(action, rule)
        if not reason:
            reason = self._validate_action_effects(effects, rule)
        preserve_outfit_fact = self._planned_outfit_action_is_superseded(day, action)

        outcome = LifeActionOutcome(
            action_id=action.action_id,
            action_type=action.action_type,
            status="rejected" if reason else "committed",
            reason=reason or "动作已通过可行性校验并结算",
            committed_at=committed_at,
            timeline_index=action.timeline_index,
            evidence=action.evidence,
        )
        if not reason:
            self._commit_action_effects(
                day,
                action,
                effects,
                outcome,
                committed_at,
                preserve_outfit_fact=preserve_outfit_fact,
            )
            if preserve_outfit_fact:
                outcome.reason = "计划换装已结算，但当前穿搭保留稍后确认的用户要求"

        if action.action_id:
            settlements[action.action_id] = outcome.as_dict()
            day.meta[ACTION_SETTLEMENT_META_KEY] = json.dumps(
                settlements,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return outcome

    async def sync_day_world_facts(
        self,
        day: DayRecord,
        *,
        observed_at: str = "",
        source: str = "daily_state",
        source_id: str = "",
        evidence: str = "",
    ) -> list[Any]:
        """把当前生活状态同步为可替代的时间事实。

        Args:
            day: 当前日记录。
            observed_at: 本次观察或结算时间。
            source: 事实来源类别。
            source_id: 可追溯来源编号。
            evidence: 当前状态的简短证据。

        Returns:
            实际写入或确认的事实记录。
        """

        writer = getattr(self.archive, "upsert_current_temporal_fact", None)
        if not callable(writer):
            return []
        timestamp = observed_at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        facts: list[tuple[str, Any]] = []
        if day.outfit:
            facts.append(("current_outfit", day.outfit))
        current_place = str((day.meta or {}).get("current_place") or "").strip()
        if current_place:
            facts.append(("current_place", current_place))
        saved = []
        for predicate, value in facts:
            fact = await writer(
                scope="global",
                subject="self",
                predicate=predicate,
                object_value=value,
                observed_at=timestamp,
                source=source,
                source_type="life_state",
                source_id=source_id,
                provenance={"date": day.date, "source_id": source_id},
                evidence_summary=evidence,
            )
            if fact is not None:
                saved.append(fact)
        return saved

    async def _save_action_receipt(
        self,
        day: DayRecord,
        action: LifeActionIntent,
        receipt: dict[str, Any],
        *,
        now: datetime.datetime,
    ) -> Any:
        saver = getattr(self.archive, "save_life_action_receipt", None)
        if not callable(saver):
            return None
        evidence = receipt.get("evidence")
        if not isinstance(evidence, list):
            evidence = [evidence] if str(evidence or "").strip() else []
        return await saver(
            {
                "receipt_id": str(receipt.get("receipt_id") or "").strip(),
                "action_id": action.action_id,
                "date": day.date,
                "action_type": action.action_type,
                "status": str(receipt.get("status") or "confirmed").strip(),
                "evidence": evidence,
                "source": str(receipt.get("source") or "").strip(),
                "source_id": str(receipt.get("source_id") or "").strip(),
                "artifact_path": str(receipt.get("artifact_path") or "").strip(),
                "occurred_at": str(receipt.get("occurred_at") or "").strip()
                or now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    @staticmethod
    def _receipt_evidence_text(receipt: dict[str, Any], fallback: str = "") -> str:
        evidence = receipt.get("evidence")
        values = evidence if isinstance(evidence, list) else [evidence]
        for value in values:
            text = " ".join(str(value or "").split())
            if text:
                return text[:240]
        return " ".join(str(fallback or "").split())[:240]

    async def record_life_action_receipt(
        self,
        day: DayRecord,
        action_id: str,
        receipt: dict[str, Any],
        *,
        now: datetime.datetime | None = None,
    ) -> LifeActionOutcome | None:
        """使用可验证的外部回执结算一项已计划动作。

        Args:
            day: 当前日记录。
            action_id: 日程生成时分配的动作编号。
            receipt: 已确认、失败或取消的回执。
            now: 结算时间，缺省时使用插件时钟。

        Returns:
            已持久化的动作结果；找不到对应动作时返回空。
        """

        current_time = now or life_now()
        raw_actions = str((day.meta or {}).get("planned_life_actions") or "")
        try:
            planned_actions = json.loads(raw_actions) if raw_actions else []
        except (TypeError, ValueError, json.JSONDecodeError):
            planned_actions = []
        action = next(
            (
                LifeActionIntent.from_value(item)
                for item in planned_actions
                if isinstance(item, dict)
                and str(item.get("action_id") or "").strip() == str(action_id).strip()
            ),
            None,
        )
        if action is None or action.action_type not in LIFE_ACTION_TYPES:
            return None
        status = str(receipt.get("status") or "confirmed").strip().lower()
        if status not in {"confirmed", "simulated", "failed", "cancelled"}:
            return None
        domain_service = getattr(self, "domains", None)
        validator = getattr(domain_service, "validate_action", None)
        if status in {"confirmed", "simulated"} and callable(validator):
            valid, validation_reason = await validator(action)
            if not valid:
                status = "failed"
                receipt = {
                    **receipt,
                    "source": "life_domain_validation",
                    "evidence": [validation_reason],
                }
        evidence = self._receipt_evidence_text(receipt, action.evidence)
        receipt = {
            **receipt,
            "status": status,
            "occurred_at": str(receipt.get("occurred_at") or "").strip()
            or current_time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        await self._save_action_receipt(day, action, receipt, now=current_time)
        if status in {"failed", "cancelled"}:
            outcome = LifeActionOutcome(
                action_id=action.action_id,
                action_type=action.action_type,
                status=status,
                reason=self._receipt_evidence_text(receipt, "动作未能执行"),
                committed_at=current_time.strftime("%Y-%m-%d %H:%M:%S"),
                timeline_index=action.timeline_index,
                evidence=evidence,
            )
            if action.timeline_index is not None and 0 <= action.timeline_index < len(
                day.timeline
            ):
                item = day.timeline[action.timeline_index]
                # 时间轴不存储 failed；失败的动作视为已跳过，其结果仍完整保留在回执与动作结果中。
                item.execution_state = (
                    "cancelled" if status == "cancelled" else "skipped"
                )
                item.execution_reason = outcome.reason
                item.execution_evidence = evidence
                item.execution_updated_at = outcome.committed_at
            day.meta[SCHEDULE_REPLAN_META_KEY] = json.dumps(
                {
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "status": status,
                    "reason": outcome.reason,
                    "evidence": evidence,
                    "occurred_at": outcome.committed_at,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            await self.archive.save_day(day)
            saver = getattr(self.archive, "save_life_action_outcome", None)
            if callable(saver):
                await saver(
                    {
                        "action_id": action.action_id,
                        "date": day.date,
                        "action_type": action.action_type,
                        "target": action.target,
                        "preconditions": {
                            "all": [item.as_dict() for item in action.preconditions]
                        },
                        "effects": {
                            "requested": [item.as_dict() for item in action.effects]
                        },
                        "status": status,
                        "reason": outcome.reason,
                        "evidence": [evidence] if evidence else [],
                        "started_at": action.requested_at,
                        "committed_at": outcome.committed_at,
                    }
                )
            tracer = getattr(self.archive, "save_decision_trace", None)
            if callable(tracer):
                await tracer(
                    {
                        "trace_id": f"life_action:{action.action_id}",
                        "scope": f"day:{day.date}",
                        "stage": "receipt",
                        "reason_code": f"action_{status}",
                        "decision": status,
                        "evidence": [evidence] if evidence else [],
                        "outcome": outcome.reason,
                    }
                )
            return outcome

        if action.timeline_index is not None and 0 <= action.timeline_index < len(
            day.timeline
        ):
            item = day.timeline[action.timeline_index]
            item.execution_state = "completed"
            item.execution_reason = "已收到动作执行回执"
            item.execution_evidence = evidence
            item.execution_updated_at = current_time.strftime("%Y-%m-%d %H:%M:%S")
        action.source = "daily_plan"
        action.evidence = evidence
        outcome = await self.settle_and_persist_life_action(
            day,
            action,
            now=current_time,
            fact_source="life_action_receipt",
            fact_evidence=evidence,
            receipt_status=status,
        )
        if outcome.status == "committed":
            if action.action_type in {"chat", "social"} and action.target:
                writer = getattr(self.archive, "upsert_current_temporal_fact", None)
                if callable(writer):
                    await writer(
                        scope="global",
                        subject=action.target,
                        predicate="latest_interaction",
                        object_value={
                            "action_type": action.action_type,
                            "date": day.date,
                            "evidence": evidence,
                        },
                        observed_at=outcome.committed_at,
                        source="life_action_receipt",
                        source_type="action_receipt",
                        source_id=action.action_id,
                        provenance={"date": day.date, "receipt_status": status},
                        evidence_summary=evidence,
                    )
        return outcome

    async def record_matching_life_action_receipt(
        self,
        day: DayRecord,
        action_type: str,
        receipt: dict[str, Any],
        *,
        now: datetime.datetime | None = None,
    ) -> LifeActionOutcome | None:
        """为当前尚未结算的同类型动作提交回执。

        Args:
            day: 当前日记录。
            action_type: 已确认的工具或外部行为类型。
            receipt: 回执内容。
            now: 结算时间。

        Returns:
            匹配并结算后的动作结果；没有明确计划动作时返回空。
        """

        raw_actions = str((day.meta or {}).get("planned_life_actions") or "")
        try:
            planned_actions = json.loads(raw_actions) if raw_actions else []
        except (TypeError, ValueError, json.JSONDecodeError):
            planned_actions = []
        settlements = str((day.meta or {}).get(ACTION_SETTLEMENT_META_KEY) or "")
        try:
            settled = json.loads(settlements) if settlements else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            settled = {}
        candidates = []
        for raw in planned_actions if isinstance(planned_actions, list) else []:
            action = LifeActionIntent.from_value(raw)
            if (
                action.action_type != action_type
                or not action.action_id
                or action.action_id in settled
                or action.timeline_index is None
                or not 0 <= action.timeline_index < len(day.timeline)
            ):
                continue
            state = day.timeline[action.timeline_index].execution_state
            if state in {"skipped", "cancelled", "completed", "expired"}:
                continue
            candidates.append(action)
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                0
                if day.timeline[item.timeline_index].execution_state == "active"
                else 1,
                item.timeline_index,
            )
        )
        return await self.record_life_action_receipt(
            day, candidates[0].action_id, receipt, now=now
        )

    async def settle_and_persist_life_action(
        self,
        day: DayRecord,
        intent: LifeActionIntent | dict[str, Any],
        *,
        now: datetime.datetime | None = None,
        fact_source: str = "life_action",
        fact_evidence: str = "",
        receipt_status: str = "confirmed",
    ) -> LifeActionOutcome:
        """结算动作并同步保存日状态、动作结果和决策轨迹。

        Args:
            day: 动作发生的当日日记录。
            intent: 结构化生活动作意图。
            now: 结算时间，缺省时使用插件时钟。
            fact_source: 结算后的世界事实来源。
            fact_evidence: 覆盖动作默认说明的事实证据。
            receipt_status: 触发结算的回执状态。

        Returns:
            已持久化的幂等动作结果。
        """
        action = LifeActionIntent.from_value(intent)
        preserve_outfit_fact = self._planned_outfit_action_is_superseded(day, action)
        outcome = self.settle_life_action(day, action, now=now)
        await self.archive.save_day(day)

        save_outcome = getattr(self.archive, "save_life_action_outcome", None)
        if callable(save_outcome) and action.action_id and action.action_type:
            await save_outcome(
                {
                    "action_id": action.action_id,
                    "date": day.date,
                    "action_type": action.action_type,
                    "target": action.target,
                    "preconditions": {
                        "all": [item.as_dict() for item in action.preconditions]
                    },
                    "effects": {
                        "requested": [item.as_dict() for item in action.effects],
                        "settled": outcome.state_changes,
                    },
                    "status": outcome.status,
                    "reason": outcome.reason,
                    "evidence": [action.evidence] if action.evidence else [],
                    "started_at": action.requested_at,
                    "committed_at": outcome.committed_at,
                }
            )

        save_trace = getattr(self.archive, "save_decision_trace", None)
        if callable(save_trace) and action.action_id:
            await save_trace(
                {
                    "trace_id": f"life_action:{action.action_id}",
                    "scope": f"day:{day.date}",
                    "stage": "settled",
                    "reason_code": f"action_{outcome.status}",
                    "decision": outcome.status,
                    "evidence": [action.evidence] if action.evidence else [],
                    "outcome": outcome.reason,
                }
            )
        if outcome.status == "committed":
            domain_service = getattr(self, "domains", None)
            apply_domain = getattr(domain_service, "apply_action", None)
            if callable(apply_domain):
                try:
                    await apply_domain(
                        day,
                        action,
                        outcome,
                        receipt_status=receipt_status,
                    )
                    await self.archive.save_day(day)
                except Exception as exc:
                    logger.warning(
                        "[日常生活] 生活动作已结算，但领域记录暂未写入，"
                        f"后续巡检会自动补写：{action.action_type}；{exc}"
                    )
            if not preserve_outfit_fact:
                await self.sync_day_world_facts(
                    day,
                    observed_at=outcome.committed_at,
                    source=fact_source,
                    source_id=action.action_id,
                    evidence=fact_evidence or outcome.evidence,
                )
        return outcome

    async def settle_completed_planned_actions(
        self,
        day: DayRecord,
        *,
        now: datetime.datetime | None = None,
    ) -> list[LifeActionOutcome]:
        """结算时间轴已经确认完成的显式日程动作。

        Args:
            day: 已经过时间轴执行态校准的当日日记录。
            now: 结算时间，缺省时使用插件时钟。

        Returns:
            本轮找到的动作结果，已经结算的动作以重放结果返回。
        """
        raw_actions = str((day.meta or {}).get("planned_life_actions") or "")
        try:
            planned_actions = json.loads(raw_actions) if raw_actions else []
        except (TypeError, ValueError, json.JSONDecodeError):
            planned_actions = []
        expiration_text = str((day.meta or {}).get(ACTION_EXPIRATION_META_KEY) or "")
        try:
            expirations = json.loads(expiration_text) if expiration_text else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            expirations = {}
        if not isinstance(expirations, dict):
            expirations = {}
        outcomes = []
        changed = False
        for raw_action in planned_actions if isinstance(planned_actions, list) else []:
            action = LifeActionIntent.from_value(raw_action)
            if (
                not action.action_id
                or action.action_type not in LIFE_ACTION_TYPES
                or action.timeline_index is None
                or not 0 <= action.timeline_index < len(day.timeline)
            ):
                continue
            timeline_item = day.timeline[action.timeline_index]
            legacy_expiration = expirations.get(action.action_id)
            repair_outfit_expiration = (
                action.action_type == "change_outfit"
                and timeline_item.execution_state == "expired"
                and isinstance(legacy_expiration, dict)
                and str(legacy_expiration.get("status") or "").strip().lower()
                == "expired"
            )
            if repair_outfit_expiration:
                expirations.pop(action.action_id, None)
                timeline_item.execution_state = "completed"
                timeline_item.execution_reason = "换装由虚拟生活时间轴自动结算"
                timeline_item.execution_evidence = (
                    timeline_item.execution_evidence
                    or action.evidence
                    or action.action_id
                )
                timeline_item.execution_updated_at = (now or life_now()).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                changed = True
            elif action.action_id in expirations:
                continue
            if timeline_item.execution_state != "completed":
                continue
            domain_service = getattr(self, "domains", None)
            should_simulate = getattr(domain_service, "should_simulate", None)
            if callable(should_simulate) and should_simulate(action):
                simulated = await self.record_life_action_receipt(
                    day,
                    action.action_id,
                    {
                        "receipt_id": f"simulation:{action.action_id}",
                        "status": "simulated",
                        "source": "timeline_simulation",
                        "source_id": action.action_id,
                        "occurred_at": (now or life_now()).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "evidence": [
                            timeline_item.execution_evidence
                            or action.evidence
                            or "虚拟生活时间轴已完成该内部动作"
                        ],
                    },
                    now=now,
                )
                if simulated is not None:
                    outcomes.append(simulated)
                continue
            expired_at = (now or life_now()).strftime("%Y-%m-%d %H:%M:%S")
            outcome = LifeActionOutcome(
                action_id=action.action_id,
                action_type=action.action_type,
                status="expired",
                reason="计划时间已经结束，但没有收到可验证的执行回执",
                committed_at=expired_at,
                timeline_index=action.timeline_index,
                evidence=timeline_item.execution_evidence or action.evidence,
            )
            outcomes.append(outcome)
            timeline_item.execution_state = "expired"
            timeline_item.execution_reason = outcome.reason
            timeline_item.execution_evidence = outcome.evidence
            timeline_item.execution_updated_at = expired_at
            expirations[action.action_id] = outcome.as_dict()
            changed = True
            save_outcome = getattr(self.archive, "save_life_action_outcome", None)
            if callable(save_outcome):
                await save_outcome(
                    {
                        "action_id": action.action_id,
                        "date": day.date,
                        "action_type": action.action_type,
                        "target": action.target,
                        "preconditions": {
                            "all": [item.as_dict() for item in action.preconditions]
                        },
                        "effects": {
                            "requested": [item.as_dict() for item in action.effects]
                        },
                        "status": "expired",
                        "reason": outcome.reason,
                        "evidence": [outcome.evidence] if outcome.evidence else [],
                        "started_at": action.requested_at,
                        "committed_at": expired_at,
                    }
                )
            save_trace = getattr(self.archive, "save_decision_trace", None)
            if callable(save_trace):
                await save_trace(
                    {
                        "trace_id": f"life_action:{action.action_id}:expired",
                        "scope": f"day:{day.date}",
                        "stage": "expired",
                        "reason_code": "action_expired_without_receipt",
                        "decision": "expired",
                        "evidence": [outcome.evidence] if outcome.evidence else [],
                        "outcome": outcome.reason,
                    }
                )
        if changed:
            day.meta[ACTION_EXPIRATION_META_KEY] = json.dumps(
                expirations, ensure_ascii=False, separators=(",", ":")
            )
            await self.archive.save_day(day)
        return outcomes

    def extract_schedule_anchors(
        self,
        day: DayRecord,
        *,
        maximum: int = 6,
    ) -> list[ScheduleAnchor]:
        """从日时间轴均匀提炼四至六个稳定锚点。

        时间轴不足四项时保留全部真实项目，不补造活动；超过上限时保留
        首尾并在中间等距取样，不分析活动文本。

        Args:
            day: 需要提炼的当日日记录。
            maximum: 最多保留的锚点数，约束在四至六之间。

        Returns:
            按时间排序的结构化锚点。
        """
        maximum = max(4, min(6, int(maximum)))
        candidates: list[tuple[int, int]] = []
        for index, item in enumerate(day.timeline):
            try:
                parsed = datetime.datetime.strptime(item.time, "%H:%M")
            except (TypeError, ValueError):
                continue
            candidates.append((parsed.hour * 60 + parsed.minute, index))
        candidates.sort(key=lambda item: (item[0], item[1]))

        if len(candidates) > maximum:
            chosen_positions = {
                round(position * (len(candidates) - 1) / (maximum - 1))
                for position in range(maximum)
            }
            candidates = [
                item
                for position, item in enumerate(candidates)
                if position in chosen_positions
            ]

        anchors = []
        for _, source_index in candidates:
            item = day.timeline[source_index]
            anchors.append(
                ScheduleAnchor(
                    anchor_id=f"{day.date}:{source_index}",
                    time=item.time,
                    activity=item.activity,
                    status=item.status,
                    source_index=source_index,
                    execution_state=item.execution_state,
                    evidence=item.execution_evidence,
                )
            )
        day.meta[SCHEDULE_ANCHOR_META_KEY] = json.dumps(
            [item.as_dict() for item in anchors],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return anchors

    def refine_upcoming_anchors(
        self,
        day: DayRecord,
        *,
        now: datetime.datetime | None = None,
        horizon_minutes: int = 180,
    ) -> list[ScheduleAnchor]:
        """把临近锚点细化为即将开始或准备中状态。

        Args:
            day: 当前日记录。
            now: 当前时间，缺省时使用插件时钟。
            horizon_minutes: 向前细化的分钟窗口。

        Returns:
            位于当前时刻至窗口末端之间的锚点副本。
        """
        current_time = now or life_now()
        horizon_minutes = max(1, min(720, int(horizon_minutes)))
        refined = []
        for anchor in self.extract_schedule_anchors(day):
            try:
                anchor_time = datetime.datetime.strptime(
                    f"{day.date} {anchor.time}", "%Y-%m-%d %H:%M"
                )
            except (TypeError, ValueError):
                continue
            remaining = (
                anchor_time - current_time.replace(tzinfo=None)
            ).total_seconds() / 60
            if 0 <= remaining <= horizon_minutes and anchor.execution_state in {
                "planned",
                "active",
            }:
                refined.append(
                    ScheduleAnchor(
                        **{
                            **anchor.as_dict(),
                            "refinement_state": (
                                "ready" if remaining <= 30 else "near_term"
                            ),
                        }
                    )
                )
        day.meta["near_term_anchors"] = json.dumps(
            [item.as_dict() for item in refined],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return refined

    def replan_future_anchors(
        self,
        day: DayRecord,
        replacements: list[ScheduleAnchor | dict[str, Any]],
        *,
        now: datetime.datetime | None = None,
        affected_anchor_ids: list[str] | None = None,
    ) -> PlanRevision:
        """只替换明确指定且尚未开始的未来日程锚点。

        Args:
            day: 当前日记录。
            replacements: 带 replaces_anchor_id 的替换锚点。
            now: 当前时间，缺省时使用插件时钟。
            affected_anchor_ids: 允许修改的原锚点编号白名单。

        Returns:
            原子校验并应用后的局部重排结果。
        """
        current_time = (now or life_now()).replace(tzinfo=None)
        revised_at = current_time.strftime("%Y-%m-%d %H:%M:%S")
        normalized = [ScheduleAnchor.from_value(item) for item in replacements]
        allowed_ids = set(affected_anchor_ids or [])
        if not allowed_ids:
            allowed_ids = {
                item.replaces_anchor_id
                for item in normalized
                if item.replaces_anchor_id
            }
        anchor_map = {
            item.anchor_id: item for item in self.extract_schedule_anchors(day)
        }
        if not normalized:
            return PlanRevision(reason="没有提供替换锚点", revised_at=revised_at)
        if len(normalized) != len(allowed_ids):
            return PlanRevision(
                reason="替换锚点与 affected_anchor_ids 不是一一对应",
                revised_at=revised_at,
            )

        prepared: dict[int, ScheduleAnchor] = {}
        for replacement in normalized:
            original_id = replacement.replaces_anchor_id
            original = anchor_map.get(original_id)
            if original_id not in allowed_ids or original is None:
                return PlanRevision(
                    reason=f"锚点不在允许修改范围：{original_id or '空'}",
                    revised_at=revised_at,
                )
            if original.source_index in prepared:
                return PlanRevision(
                    reason="同一锚点不能在一次重排中替换两次",
                    revised_at=revised_at,
                )
            timeline_item = day.timeline[original.source_index]
            if timeline_item.execution_state not in {"planned"}:
                return PlanRevision(
                    reason=f"锚点已经开始或收束：{original_id}",
                    revised_at=revised_at,
                )
            try:
                old_time = datetime.datetime.strptime(
                    f"{day.date} {timeline_item.time}", "%Y-%m-%d %H:%M"
                )
                new_time = datetime.datetime.strptime(
                    f"{day.date} {replacement.time}", "%Y-%m-%d %H:%M"
                )
            except (TypeError, ValueError):
                return PlanRevision(
                    reason=f"锚点时间格式无效：{original_id}",
                    revised_at=revised_at,
                )
            if old_time <= current_time or new_time <= current_time:
                return PlanRevision(
                    reason=f"只能重排尚未开始的未来锚点：{original_id}",
                    revised_at=revised_at,
                )
            if not replacement.activity:
                return PlanRevision(
                    reason=f"替换锚点缺少 activity：{original_id}",
                    revised_at=revised_at,
                )
            prepared[original.source_index] = replacement

        proposed_times = []
        for index, item in enumerate(day.timeline):
            value = prepared[index].time if index in prepared else item.time
            try:
                parsed = datetime.datetime.strptime(value, "%H:%M")
            except (TypeError, ValueError):
                continue
            proposed_times.append(parsed.hour * 60 + parsed.minute)
        if proposed_times != sorted(proposed_times) or len(proposed_times) != len(
            set(proposed_times)
        ):
            return PlanRevision(
                reason="重排后的时间轴必须严格递增且不能重叠",
                revised_at=revised_at,
            )

        changed_indexes = []
        applied_ids = []
        for source_index, replacement in prepared.items():
            timeline_item = day.timeline[source_index]
            timeline_item.time = replacement.time
            timeline_item.activity = replacement.activity
            timeline_item.status = replacement.status or timeline_item.status
            timeline_item.execution_reason = "日程局部重排"
            timeline_item.execution_evidence = replacement.evidence
            timeline_item.execution_updated_at = revised_at
            changed_indexes.append(source_index)
            applied_ids.append(replacement.replaces_anchor_id)

        try:
            revision = int(str(day.meta.get("schedule_revision") or "0")) + 1
        except (TypeError, ValueError):
            revision = 1
        day.meta["schedule_revision"] = str(revision)
        day.meta["schedule_last_replanned_at"] = revised_at
        self.extract_schedule_anchors(day)
        return PlanRevision(
            status="applied",
            reason="已只更新指定的未来锚点",
            applied_anchor_ids=applied_ids,
            changed_indexes=changed_indexes,
            revised_at=revised_at,
        )

    def evaluate_reflection_threshold(
        self,
        signal: ReflectionSignal | dict[str, Any],
        *,
        now: datetime.datetime | None = None,
        threshold: float = 65.0,
        last_reflection_at: str = "",
        cooldown_hours: float = 12.0,
    ) -> ReflectionDecision:
        """按数值信号和冷却期决定是否生成生活反思。

        Args:
            signal: 重要度、新奇度、情绪强度和复现度评分。
            now: 当前时间，缺省时使用插件时钟。
            threshold: 触发阈值，约束在零至一百。
            last_reflection_at: 上一次反思的 ISO 时间。
            cooldown_hours: 两次反思之间的最短小时数。

        Returns:
            带加权分、证据和下次可触发时间的决策。
        """
        item = ReflectionSignal.from_value(signal)
        threshold = round(max(0.0, min(100.0, float(threshold))), 2)
        cooldown_hours = max(0.0, min(168.0, float(cooldown_hours)))
        score = round(
            item.importance * 0.35
            + item.novelty * 0.25
            + item.emotional_intensity * 0.25
            + item.recurrence * 0.15,
            2,
        )
        current_time = (now or life_now()).replace(tzinfo=None)
        next_eligible_at = ""
        if last_reflection_at:
            try:
                previous = datetime.datetime.fromisoformat(last_reflection_at).replace(
                    tzinfo=None
                )
            except (TypeError, ValueError):
                previous = None
            if previous is not None:
                eligible = previous + datetime.timedelta(hours=cooldown_hours)
                next_eligible_at = eligible.isoformat(timespec="seconds")
                if current_time < eligible:
                    return ReflectionDecision(
                        score=score,
                        threshold=threshold,
                        reason="仍处于反思冷却期",
                        next_eligible_at=next_eligible_at,
                        evidence=item.evidence,
                    )
        if not item.evidence:
            return ReflectionDecision(
                score=score,
                threshold=threshold,
                reason="缺少可追溯证据，不生成反思",
                next_eligible_at=next_eligible_at,
            )
        should_reflect = score >= threshold
        return ReflectionDecision(
            should_reflect=should_reflect,
            score=score,
            threshold=threshold,
            reason=(
                "综合评分达到反思阈值" if should_reflect else "综合评分未达到反思阈值"
            ),
            next_eligible_at=next_eligible_at,
            evidence=item.evidence,
        )


__all__ = [
    "ACTION_SETTLEMENT_META_KEY",
    "SCHEDULE_ANCHOR_META_KEY",
    "LifeActionMixin",
]
