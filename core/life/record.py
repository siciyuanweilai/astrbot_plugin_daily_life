import json

from ..labels import (
    plan_outfit_decision_label,
    schedule_intent_label,
    schedule_tone_label,
)
from ..models import (
    LIFE_ACTION_TYPES,
    CommitmentRecord,
    DayRecord,
    EventRecord,
    LifeActionIntent,
    LifeEpisodeRecord,
    MemoryEvidenceRecord,
)
from .signals import physiological_rhythm_log_from_state
from .surroundings import normalize_place_names


class DailyRecordMixin:
    async def _persist_generated_day(
        self,
        date_str: str,
        day: DayRecord,
        due_commitments: list[CommitmentRecord],
    ) -> None:
        if normalize_place_names(day.places):
            await self.archive.touch_places(date_str, day.places, source="daily")
        await self.archive.add_events(date_str, day.new_events)
        if due_commitments:
            await self.archive.add_events(
                date_str,
                [
                    EventRecord(
                        date=date_str,
                        summary=f"已将承诺安排进今日生活背景：{item.content}",
                        people=item.people,
                        place=item.place,
                        importance="high",
                        source="commitment",
                    )
                    for item in due_commitments
                ],
            )
        await self.archive.link_commitments_to_day(
            date_str, [item.id for item in due_commitments]
        )
        await self.archive.save_day(day)
        raw_actions = (day.meta or {}).get("planned_life_actions")
        try:
            planned_actions = json.loads(raw_actions) if raw_actions else []
        except (TypeError, ValueError, json.JSONDecodeError):
            planned_actions = []
        save_outcome = getattr(self.archive, "save_life_action_outcome", None)
        save_trace = getattr(self.archive, "save_decision_trace", None)
        for raw_action in planned_actions if isinstance(planned_actions, list) else []:
            action = LifeActionIntent.from_value(raw_action)
            if not action.action_id or action.action_type not in LIFE_ACTION_TYPES:
                continue
            if callable(save_outcome):
                await save_outcome(
                    {
                        "action_id": action.action_id,
                        "date": date_str,
                        "action_type": action.action_type,
                        "target": action.target,
                        "preconditions": {
                            "all": [item.as_dict() for item in action.preconditions]
                        },
                        "effects": {
                            "requested": [item.as_dict() for item in action.effects]
                        },
                        "status": "proposed",
                        "reason": "由当日分层日程提出，等待真实执行证据结算",
                        "evidence": [action.evidence] if action.evidence else [],
                        "started_at": action.requested_at,
                    }
                )
            if callable(save_trace):
                await save_trace(
                    {
                        "trace_id": f"life_action:{action.action_id}",
                        "scope": f"day:{date_str}",
                        "stage": "proposed",
                        "reason_code": "action_planned",
                        "decision": "proposed",
                        "evidence": [action.evidence] if action.evidence else [],
                        "outcome": "等待真实执行证据",
                    }
                )
        await self._persist_physiological_rhythm_log(
            date_str, day, source="daily_generation"
        )
        await self._persist_daily_experience(date_str, day, due_commitments)

    async def _persist_physiological_rhythm_log(
        self, date_str: str, day: DayRecord, *, source: str
    ) -> None:
        save_log = getattr(self.archive, "save_physiological_rhythm_log", None)
        if not callable(save_log):
            return
        item = physiological_rhythm_log_from_state(
            day.state, date=date_str, source=source
        )
        if item:
            await save_log(item)

    async def _persist_daily_experience(
        self,
        date_str: str,
        day: DayRecord,
        due_commitments: list[CommitmentRecord],
    ) -> None:
        meta = day.meta or {}
        title = meta.get("theme") or meta.get("schedule_intent") or "今日生活规划"
        summary_parts = [
            f"日程基调：{schedule_tone_label(meta.get('life_mode')) or '自主判断'}",
            f"日程倾向：{schedule_intent_label(meta.get('schedule_intent')) or '未标注'}",
            f"日程穿搭：{plan_outfit_decision_label(meta.get('plan_outfit_decision') or meta.get('outfit_decision')) or '未标注'}",
        ]
        if due_commitments:
            summary_parts.append(
                "承诺：" + "、".join(item.content for item in due_commitments[:3])
            )
        impact = meta.get("outfit_reason") or (day.state.summary if day.state else "")
        episode = await self.archive.save_life_episode(
            LifeEpisodeRecord(
                date=date_str,
                title=title,
                summary="；".join(summary_parts),
                kind="daily_plan",
                source="daily",
                related_people=sorted(
                    {person for item in due_commitments for person in item.people}
                ),
                related_places=[place.name for place in day.places],
                impact=impact,
                confidence=1.0,
                status="open",
            )
        )
        if episode:
            await self.archive.save_memory_evidence(
                MemoryEvidenceRecord(
                    target_type="life_episode",
                    target_id=str(episode.id),
                    evidence_type="daily_generation",
                    source_table="days",
                    source_id=date_str,
                    date=date_str,
                    summary=f"今日生成依据：{episode.summary}",
                    confidence=1.0,
                )
            )


__all__ = ["DailyRecordMixin"]
