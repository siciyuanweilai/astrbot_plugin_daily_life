# ruff: noqa: I001
import datetime
import json
import tempfile
import unittest

from support import DayRecord, LifeArchive, LifeState, TimelineItem

from core.life.actions import LifeActionMixin
from core.life.assembly import DailyAssemblyMixin
from core.life.record import DailyRecordMixin
from core.life.rhythm import LifecycleMixin
from core.models import (
    EXTERNAL_RECEIPT_ACTION_TYPES,
    INTERNAL_SIMULATED_ACTION_TYPES,
    LIFE_ACTION_TYPES,
    LifeActionIntent,
    ReflectionSignal,
    WeatherInfo,
)


class LifeActionTest(unittest.TestCase):
    def setUp(self):
        self.engine = LifeActionMixin()

    def test_action_taxonomy_has_seventeen_complete_non_overlapping_types(self):
        self.assertEqual(len(LIFE_ACTION_TYPES), 17)
        self.assertEqual(len(INTERNAL_SIMULATED_ACTION_TYPES), 13)
        self.assertEqual(
            EXTERNAL_RECEIPT_ACTION_TYPES,
            {"social", "chat", "photo", "video"},
        )
        self.assertEqual(
            INTERNAL_SIMULATED_ACTION_TYPES | EXTERNAL_RECEIPT_ACTION_TYPES,
            LIFE_ACTION_TYPES,
        )
        self.assertFalse(
            INTERNAL_SIMULATED_ACTION_TYPES & EXTERNAL_RECEIPT_ACTION_TYPES
        )

    def test_action_contract_does_not_infer_type_from_text(self):
        action = LifeActionIntent.from_value(
            {
                "action_id": "a-1",
                "target": "去吃午饭并休息",
                "effects": [{"field": "energy", "operation": "add", "value": 8}],
            }
        )

        self.assertEqual(action.action_type, "")
        outcome = self.engine.settle_life_action(
            DayRecord(date="2026-08-01"),
            action,
            now=datetime.datetime(2026, 8, 1, 12, 0),
        )
        self.assertEqual(outcome.status, "rejected")
        self.assertIn("action_type", outcome.reason)

    def test_action_settlement_is_idempotent_and_updates_timeline(self):
        day = DayRecord(
            date="2026-08-01",
            state=LifeState(energy=60, stress=50, sleepiness=70),
            timeline=[TimelineItem(time="13:00", activity="午间调整")],
        )
        action = LifeActionIntent.from_value(
            {
                "action_id": "rest-1",
                "action_type": "rest",
                "timeline_index": 0,
                "evidence": "13:20 已完成",
            }
        )

        first = self.engine.settle_life_action(
            day, action, now=datetime.datetime(2026, 8, 1, 13, 20)
        )
        second = self.engine.settle_life_action(
            day, action, now=datetime.datetime(2026, 8, 1, 13, 25)
        )

        self.assertEqual(first.status, "committed")
        self.assertEqual(day.state.energy, 72)
        self.assertEqual(day.state.stress, 42)
        self.assertEqual(day.state.sleepiness, 58)
        self.assertEqual(day.timeline[0].execution_state, "completed")
        self.assertTrue(second.replayed)
        self.assertEqual(second.committed_at, "2026-08-01 13:20:00")
        self.assertEqual(day.state.energy, 72)
        stored = json.loads(day.meta["life_action_settlements"])
        self.assertEqual(stored["rest-1"]["status"], "committed")

    def test_failed_precondition_is_recorded_without_state_change(self):
        day = DayRecord(
            date="2026-08-01",
            state=LifeState(energy=35, focus=60),
        )
        action = {
            "action_id": "study-1",
            "action_type": "study",
            "preconditions": [
                {"field": "state.energy", "operator": "gte", "expected": 50}
            ],
        }

        outcome = self.engine.settle_life_action(
            day, action, now=datetime.datetime(2026, 8, 1, 14, 0)
        )

        self.assertEqual(outcome.status, "rejected")
        self.assertEqual(day.state.energy, 35)
        self.assertEqual(day.state.focus, 60)
        self.assertEqual(
            json.loads(day.meta["life_action_settlements"])["study-1"]["status"],
            "rejected",
        )

    def test_change_outfit_requires_explicit_target_and_settles_once(self):
        day = DayRecord(
            date="2026-08-01",
            outfit="居家穿搭",
            state=LifeState(mood_score=55),
        )

        rejected = self.engine.settle_life_action(
            day,
            {"action_id": "outfit-0", "action_type": "change_outfit"},
            now=datetime.datetime(2026, 8, 1, 16, 0),
        )
        committed = self.engine.settle_life_action(
            day,
            {
                "action_id": "outfit-1",
                "action_type": "change_outfit",
                "target": "浅黄色短袖和米白长裤",
            },
            now=datetime.datetime(2026, 8, 1, 16, 5),
        )

        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(committed.status, "committed")
        self.assertEqual(day.outfit, "浅黄色短袖和米白长裤")
        self.assertEqual(day.state.mood_score, 57)
        self.assertEqual(len(day.outfit_history), 1)
        self.assertEqual(day.meta["outfit_fact_source"], "life_action")
        self.assertEqual(day.meta["outfit_fact_confirmed_at"], "2026-08-01 16:05:00")
        self.assertEqual(day.meta["outfit_fact_evidence"], "outfit-1")

    def test_daily_plan_change_outfit_uses_action_target_as_current_fact(self):
        day = DayRecord(
            date="2026-08-01",
            outfit="日程生成时延续的居家服",
            state=LifeState(mood_score=50),
        )
        outcome = self.engine.settle_life_action(
            day,
            {
                "action_id": "outfit-plan-1",
                "action_type": "change_outfit",
                "target": "浅绿色短袖和米白色长裤",
                "source": "daily_plan",
            },
            now=datetime.datetime(2026, 8, 1, 8, 40),
        )

        self.assertEqual(outcome.status, "committed")
        self.assertEqual(day.outfit, "浅绿色短袖和米白色长裤")
        self.assertEqual(day.meta["outfit_fact_source"], "life_action")

    def test_action_preconditions_cover_life_weather_and_timeline_evidence(self):
        day = DayRecord(
            date="2026-08-01",
            outfit="轻便穿搭",
            time_period="下午",
            state=LifeState(energy=60),
            weather_info=WeatherInfo(condition="晴", temp=30),
            timeline=[TimelineItem(time="15:00", activity="休息")],
            meta={"current_place": "测试公园"},
        )
        action = {
            "action_id": "rest-evidence",
            "action_type": "rest",
            "timeline_index": 0,
            "preconditions": [
                {"field": "day.date", "operator": "eq", "expected": "2026-08-01"},
                {"field": "day.outfit", "operator": "ne", "expected": "居家穿搭"},
                {"field": "day.time_period", "operator": "in", "expected": ["下午", "晚上"]},
                {"field": "day.current_place", "operator": "present"},
                {"field": "weather.condition", "operator": "eq", "expected": "晴"},
                {"field": "weather.temp", "operator": "lte", "expected": 32},
                {"field": "state.energy", "operator": "gte", "expected": 50},
                {
                    "field": "timeline.execution_state",
                    "operator": "not_in",
                    "expected": ["cancelled", "skipped"],
                },
            ],
        }

        outcome = self.engine.settle_life_action(
            day, action, now=datetime.datetime(2026, 8, 1, 15, 0)
        )

        self.assertEqual(outcome.status, "committed")
        self.assertEqual(day.timeline[0].execution_state, "completed")

    def test_action_rejects_unknown_precondition_and_disallowed_effect(self):
        unknown = self.engine.settle_life_action(
            DayRecord(date="2026-08-01", state=LifeState(energy=50)),
            {
                "action_id": "rest-unknown",
                "action_type": "rest",
                "preconditions": [
                    {"field": "state.unknown", "operator": "present"}
                ],
            },
            now=datetime.datetime(2026, 8, 1, 15, 0),
        )
        disallowed = self.engine.settle_life_action(
            DayRecord(date="2026-08-01", state=LifeState(energy=50)),
            {
                "action_id": "rest-effect",
                "action_type": "rest",
                "effects": [
                    {"field": "interaction_capacity", "operation": "add", "value": 5}
                ],
            },
            now=datetime.datetime(2026, 8, 1, 15, 0),
        )

        self.assertEqual(unknown.status, "rejected")
        self.assertIn("前置条件未满足", unknown.reason)
        self.assertEqual(disallowed.status, "rejected")
        self.assertIn("不允许修改", disallowed.reason)

    def test_move_action_updates_place_and_clamps_explicit_effect(self):
        day = DayRecord(
            date="2026-08-01",
            state=LifeState(energy=95),
            meta={"current_place": "测试住处"},
        )
        outcome = self.engine.settle_life_action(
            day,
            {
                "action_id": "move-1",
                "action_type": "move",
                "target": "测试公园",
                "effects": [
                    {"field": "energy", "operation": "add", "value": 20}
                ],
            },
            now=datetime.datetime(2026, 8, 1, 16, 0),
        )

        self.assertEqual(outcome.status, "committed")
        self.assertEqual(day.state.energy, 100)
        self.assertEqual(day.meta["previous_place"], "测试住处")
        self.assertEqual(day.meta["current_place"], "测试公园")
        self.assertEqual([place.name for place in day.places], ["测试公园"])

    def test_extracts_at_most_six_evenly_distributed_anchors(self):
        day = DayRecord(
            date="2026-08-01",
            timeline=[
                TimelineItem(time=f"{hour:02d}:00", activity=f"活动 {hour}")
                for hour in range(8, 18)
            ],
        )

        anchors = self.engine.extract_schedule_anchors(day)

        self.assertEqual(len(anchors), 6)
        self.assertEqual(anchors[0].source_index, 0)
        self.assertEqual(anchors[-1].source_index, 9)
        self.assertEqual(anchors[0].activity, "活动 8")
        self.assertEqual(anchors[-1].activity, "活动 17")
        self.assertEqual(
            len(json.loads(day.meta["schedule_anchors"])),
            6,
        )

    def test_refines_only_upcoming_active_or_planned_anchors(self):
        day = DayRecord(
            date="2026-08-01",
            timeline=[
                TimelineItem(
                    time="09:00", activity="已完成事项", execution_state="completed"
                ),
                TimelineItem(time="10:20", activity="近期事项"),
                TimelineItem(time="12:30", activity="稍后事项"),
                TimelineItem(time="18:00", activity="远期事项"),
            ],
        )

        refined = self.engine.refine_upcoming_anchors(
            day,
            now=datetime.datetime(2026, 8, 1, 10, 0),
            horizon_minutes=180,
        )

        self.assertEqual([item.activity for item in refined], ["近期事项", "稍后事项"])
        self.assertEqual(refined[0].refinement_state, "ready")
        self.assertEqual(refined[1].refinement_state, "near_term")

    def test_local_replan_changes_only_allowed_future_anchors(self):
        day = DayRecord(
            date="2026-08-01",
            timeline=[
                TimelineItem(
                    time="09:00", activity="过去事项", execution_state="completed"
                ),
                TimelineItem(time="14:00", activity="原事项一", status="平稳"),
                TimelineItem(time="16:00", activity="原事项二", status="期待"),
                TimelineItem(time="19:00", activity="保留事项", status="放松"),
            ],
        )

        result = self.engine.replan_future_anchors(
            day,
            [
                {
                    "time": "16:30",
                    "activity": "调整后的事项",
                    "status": "从容",
                    "replaces_anchor_id": "2026-08-01:2",
                    "evidence": "临时安排变化",
                }
            ],
            now=datetime.datetime(2026, 8, 1, 13, 0),
            affected_anchor_ids=["2026-08-01:2"],
        )

        self.assertEqual(result.status, "applied")
        self.assertEqual(result.changed_indexes, [2])
        self.assertEqual(day.timeline[0].activity, "过去事项")
        self.assertEqual(day.timeline[1].activity, "原事项一")
        self.assertEqual(day.timeline[2].time, "16:30")
        self.assertEqual(day.timeline[2].activity, "调整后的事项")
        self.assertEqual(day.timeline[3].activity, "保留事项")
        self.assertEqual(day.meta["schedule_revision"], "1")

    def test_local_replan_rejects_past_anchor_atomically(self):
        day = DayRecord(
            date="2026-08-01",
            timeline=[
                TimelineItem(time="12:00", activity="已经开始"),
                TimelineItem(time="15:00", activity="未来事项"),
                TimelineItem(time="18:00", activity="晚间事项"),
                TimelineItem(time="21:00", activity="收尾事项"),
            ],
        )

        result = self.engine.replan_future_anchors(
            day,
            [
                {
                    "time": "13:00",
                    "activity": "不应生效",
                    "replaces_anchor_id": "2026-08-01:0",
                }
            ],
            now=datetime.datetime(2026, 8, 1, 13, 30),
        )

        self.assertEqual(result.status, "rejected")
        self.assertEqual(day.timeline[0].time, "12:00")
        self.assertEqual(day.timeline[0].activity, "已经开始")

    def test_reflection_requires_score_evidence_and_cooldown(self):
        signal = ReflectionSignal(
            importance=90,
            novelty=80,
            emotional_intensity=70,
            recurrence=50,
            evidence=["event:42", "feedback:7"],
        )
        now = datetime.datetime(2026, 8, 1, 20, 0)

        accepted = self.engine.evaluate_reflection_threshold(signal, now=now)
        cooling = self.engine.evaluate_reflection_threshold(
            signal,
            now=now,
            last_reflection_at="2026-08-01T15:00:00",
            cooldown_hours=12,
        )
        ungrounded = self.engine.evaluate_reflection_threshold(
            {"importance": 100, "novelty": 100, "emotional_intensity": 100},
            now=now,
        )

        self.assertTrue(accepted.should_reflect)
        self.assertEqual(accepted.score, 76.5)
        self.assertFalse(cooling.should_reflect)
        self.assertIn("冷却期", cooling.reason)
        self.assertFalse(ungrounded.should_reflect)
        self.assertIn("证据", ungrounded.reason)


class _ArchiveStub:
    def __init__(self):
        self.saved_days = []
        self.saved_outcomes = []
        self.saved_traces = []

    async def save_day(self, day, *, replace=False):
        self.saved_days.append(day)

    async def save_life_action_outcome(self, outcome):
        self.saved_outcomes.append(outcome)
        return outcome

    async def save_decision_trace(self, trace):
        self.saved_traces.append(trace)
        return trace

    async def get_day(self, _date):
        return None

    async def add_life_event(self, event):
        return event

    async def touch_places(self, *_args, **_kwargs):
        return None

    async def add_events(self, *_args, **_kwargs):
        return None

    async def link_commitments_to_day(self, *_args, **_kwargs):
        return None


class _ComposerStub(LifecycleMixin, LifeActionMixin):
    def __init__(self):
        self.archive = _ArchiveStub()


class _RecordStub(DailyRecordMixin):
    def __init__(self):
        self.archive = _ArchiveStub()

    async def _persist_physiological_rhythm_log(self, *_args, **_kwargs):
        return None

    async def _persist_daily_experience(self, *_args, **_kwargs):
        return None


class LifeActionIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_external_receipt_commits_world_fact_and_keeps_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            try:
                composer = LifeActionMixin()
                composer.archive = archive
                day = DayRecord(
                    date="2026-08-01",
                    state=LifeState(energy=70, stress=30),
                    timeline=[TimelineItem(time="18:00", activity="去公园")],
                    meta={
                        "planned_life_actions": json.dumps(
                            [
                                {
                                    "action_id": "move-receipt-1",
                                    "action_type": "move",
                                    "target": "公园",
                                    "timeline_index": 0,
                                }
                            ],
                            ensure_ascii=False,
                        )
                    },
                )

                outcome = await composer.record_life_action_receipt(
                    day,
                    "move-receipt-1",
                    {
                        "receipt_id": "receipt:move:1",
                        "status": "confirmed",
                        "source": "location_probe",
                        "source_id": "probe:1",
                        "evidence": "已抵达公园",
                    },
                    now=datetime.datetime(2026, 8, 1, 18, 12),
                )

                self.assertEqual(outcome.status, "committed")
                self.assertEqual(day.meta["current_place"], "公园")
                receipts = await archive.get_life_action_receipts(
                    action_id="move-receipt-1"
                )
                self.assertEqual(receipts[0].source, "location_probe")
                facts = await archive.get_temporal_facts(
                    scope="global", subject="self", predicate="current_place"
                )
                self.assertEqual(facts[0].object_value, "公园")
            finally:
                archive.close()

    async def test_failed_receipt_skips_timeline_and_marks_replan_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            try:
                composer = LifeActionMixin()
                composer.archive = archive
                day = DayRecord(
                    date="2026-08-01",
                    timeline=[TimelineItem(time="20:00", activity="拍照")],
                    meta={
                        "planned_life_actions": json.dumps(
                            [
                                {
                                    "action_id": "photo-failed-1",
                                    "action_type": "photo",
                                    "timeline_index": 0,
                                }
                            ],
                            ensure_ascii=False,
                        )
                    },
                )

                outcome = await composer.record_life_action_receipt(
                    day,
                    "photo-failed-1",
                    {
                        "receipt_id": "receipt:photo:failed:1",
                        "status": "failed",
                        "source": "image_delivery",
                        "evidence": "接口超时",
                    },
                    now=datetime.datetime(2026, 8, 1, 20, 5),
                )

                self.assertEqual(outcome.status, "failed")
                self.assertEqual(day.timeline[0].execution_state, "skipped")
                pending = json.loads(day.meta["schedule_replan_pending"])
                self.assertEqual(pending["action_id"], "photo-failed-1")
                self.assertEqual(pending["status"], "failed")
            finally:
                archive.close()

    async def test_daily_generation_keeps_only_explicit_valid_action_proposals(self):
        assembler = DailyAssemblyMixin()
        day = assembler._day_from_generation(
            {
                "outfit": "清爽日常穿搭",
                "timeline": [
                    {"time": "10:00", "activity": "处理当天事项", "status": "专注"}
                ],
                "planned_actions": [
                    {
                        "action_id": "2026-08-01-work-1",
                        "action_type": "work",
                        "timeline_index": 0,
                        "effects": [
                            {"field": "energy", "operation": "add", "value": -8}
                        ],
                        "evidence": "时间轴第 0 项",
                    },
                    {
                        "action_id": "invalid-by-text",
                        "target": "文字里说去吃饭",
                        "timeline_index": 0,
                    },
                ],
            },
            date_str="2026-08-01",
            period="morning",
            weather_str="晴 30°C",
            weather_info={},
            meta={},
            memo="",
        )

        actions = json.loads(day.meta["planned_life_actions"])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_type"], "work")

        recorder = _RecordStub()
        await recorder._persist_generated_day("2026-08-01", day, [])
        self.assertEqual(recorder.archive.saved_outcomes[0]["status"], "proposed")
        self.assertEqual(recorder.archive.saved_traces[0]["stage"], "proposed")

    async def test_completed_timeline_settles_only_its_explicit_planned_action(self):
        composer = _ComposerStub()
        day = DayRecord(
            date="2026-08-01",
            state=LifeState(energy=70, stress=30, mood_score=55),
            timeline=[
                TimelineItem(
                    time="18:00",
                    activity="完成一项外出安排",
                    execution_state="completed",
                    execution_evidence="后台时钟确认节点已结束",
                )
            ],
            meta={
                "planned_life_actions": json.dumps(
                    [
                        {
                            "action_id": "2026-08-01-move-1",
                            "action_type": "move",
                            "timeline_index": 0,
                            "source": "daily_plan",
                        }
                    ],
                    ensure_ascii=False,
                )
            },
        )

        first = await composer.settle_completed_planned_actions(
            day, now=datetime.datetime(2026, 8, 1, 18, 30)
        )
        second = await composer.settle_completed_planned_actions(
            day, now=datetime.datetime(2026, 8, 1, 18, 35)
        )

        self.assertEqual(first[0].status, "expired")
        self.assertEqual(day.state.energy, 70)
        self.assertEqual(day.state.stress, 30)
        self.assertEqual(day.timeline[0].execution_state, "expired")
        self.assertEqual(second, [])

    async def test_persisted_settlement_writes_day_outcome_and_trace(self):
        composer = _ComposerStub()
        day = DayRecord(date="2026-08-01", state=LifeState(energy=70))

        outcome = await composer.settle_and_persist_life_action(
            day,
            {
                "action_id": "move-1",
                "action_type": "move",
                "evidence": "定位记录 18:20",
            },
            now=datetime.datetime(2026, 8, 1, 18, 20),
        )

        self.assertEqual(outcome.status, "committed")
        self.assertEqual(len(composer.archive.saved_days), 1)
        self.assertEqual(composer.archive.saved_outcomes[0]["action_id"], "move-1")
        self.assertEqual(
            composer.archive.saved_traces[0]["reason_code"], "action_committed"
        )

    async def test_daily_lifecycle_automatically_materializes_anchors(self):
        composer = _ComposerStub()
        day = DayRecord(
            date="2026-08-01",
            timeline=[
                TimelineItem(time=f"{hour:02d}:00", activity=f"安排 {hour}")
                for hour in range(8, 16)
            ],
        )

        await composer._apply_lifecycle_to_day(
            day,
            datetime.datetime(2026, 8, 1, 7, 0),
            {},
        )

        self.assertEqual(day.meta["schedule_planning_mode"], "hierarchical")
        self.assertEqual(day.meta["schedule_anchor_count"], "6")
        self.assertEqual(len(json.loads(day.meta["schedule_anchors"])), 6)

    async def test_nightly_review_does_not_overwrite_terminal_timeline_state(self):
        composer = _ComposerStub()
        day = DayRecord(
            date="2026-08-01",
            timeline=[
                TimelineItem(
                    time="08:00",
                    activity="等待外部回执的计划",
                    execution_state="expired",
                    execution_reason="没有收到可验证回执",
                    execution_evidence="动作结果记录为过期",
                    execution_updated_at="2026-08-01 20:00:00",
                )
            ],
        )

        await composer._apply_timeline_review_updates(
            day,
            {
                "timeline_updates": [
                    {
                        "item_index": 0,
                        "status": "skipped",
                        "reason": "模型尝试重新分类",
                        "evidence": "夜间复盘输出",
                    }
                ]
            },
        )

        self.assertEqual(day.timeline[0].execution_state, "expired")
        self.assertEqual(day.timeline[0].execution_reason, "没有收到可验证回执")
        self.assertEqual(day.timeline[0].execution_evidence, "动作结果记录为过期")


if __name__ == "__main__":
    unittest.main()
