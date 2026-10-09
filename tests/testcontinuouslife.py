import asyncio
import copy
import datetime
import json
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from support import DayRecord, LifeArchive, LifeSettings, LifeState, TimelineItem

from core.life.body import ContinuousBody
from core.life.domain import LifeDomainService
from core.life.goals import (
    apply_goal_decisions,
    credit_goal,
    normalize_steps,
    ready_steps,
)
from core.life.presence import (
    apply_action_reflections,
    apply_self_model_updates,
    kernel_context,
    record_action_outcome,
    record_event,
    sync_kernel_from_day,
    update_self_model,
)
from core.life.settlement import LifeActionMixin
from core.models import LifeActionIntent
from core.runtime.continuous import ContinuousLifeMixin, next_decision_time
from core.runtime.proactive.send import ProactiveSendMixin
from core.runtime.status import StatusMixin, _StateRefreshSpec
from core.runtime.timer import LifeRhythmClock


class Composer(LifeActionMixin):
    def __init__(self, archive, domains):
        self.archive = archive
        self.domains = domains

    async def sync_day_world_facts(self, *args, **kwargs):
        return []

    def _compute_sleep_continuity(self, *args):
        return 0, 0, 0


class Runtime(ContinuousLifeMixin):
    def __init__(self, archive, now):
        self.archive = archive
        self.now = now
        self.date = now.strftime("%Y-%m-%d")
        self.config = LifeSettings.from_dict({})
        self.domains = LifeDomainService(self.config.domains, archive)
        self.composer = Composer(archive, self.domains)
        self._continuous_life_decision = AsyncMock(return_value={})
        self.mark_page_status_changed = AsyncMock()

    def _runtime_now(self):
        return self.now

    async def resolve_injection_target(self, now):
        return self.date, False

    @staticmethod
    def _event_message_id(event):
        return event.message_id

    @staticmethod
    def _event_session_id(event):
        return event.unified_msg_origin


def goal_payload():
    return {
        "new_goals": [
            {
                "owner": "self",
                "source_id": "persona",
                "title": "练习摄影构图",
                "reason": "角色设定明确喜爱摄影",
                "skill": "构图练习",
                "steps": [
                    {
                        "id": "基础",
                        "title": "练习画面平衡",
                        "action_type": "study",
                        "required_minutes": 2,
                    },
                    {
                        "id": "进阶",
                        "title": "练习光线构图",
                        "action_type": "study",
                        "required_minutes": 3,
                        "depends_on": ["基础"],
                    },
                ],
            }
        ],
    }


class ContinuousBodyTest(unittest.TestCase):
    def test_changes_depend_on_elapsed_time_not_poll_count(self):
        one = ContinuousBody(energy=40, hunger=10, thirst=10)
        many = copy.deepcopy(one)
        one.advance(35, activity="exercise", intensity=2.4)
        for _ in range(70):
            many.advance(0.5, activity="exercise", intensity=2.4)
        for field in (
            "energy",
            "fatigue",
            "hunger",
            "thirst",
            "sleep_pressure",
            "social_battery",
        ):
            self.assertAlmostEqual(getattr(one, field), getattr(many, field), places=3)

    def test_sleep_recovers_while_exercise_increases_burden(self):
        sleeping = ContinuousBody(energy=30, fatigue=60, sleep_pressure=80)
        exercising = copy.deepcopy(sleeping)
        sleeping.advance(90, activity="rest", sleeping=True)
        exercising.advance(90, activity="exercise", intensity=3)
        self.assertGreater(sleeping.energy, exercising.energy)
        self.assertLess(sleeping.fatigue, exercising.fatigue)
        self.assertLess(sleeping.sleep_pressure, exercising.sleep_pressure)
        self.assertGreater(exercising.thirst, sleeping.thirst)

    def test_drinking_volume_and_invalid_numeric_input(self):
        small = ContinuousBody(thirst=70)
        large = copy.deepcopy(small)
        small.complete("drink", {"volume_ml": 20})
        large.complete("drink", {"volume_ml": 250})
        self.assertGreater(small.thirst, large.thirst)
        body = ContinuousBody.from_value(
            {"energy": float("nan"), "uncertain_minutes": "invalid"}
        )
        json.dumps(body.as_dict(), allow_nan=False)


class LifeKernelTest(unittest.TestCase):
    def test_event_ledger_is_idempotent_and_context_is_shared(self):
        world = {"body": {"energy": 55, "social_battery": 68}}
        day = DayRecord(
            date="2026-10-07",
            outfit="居家服",
            weather="晴",
            state=LifeState(mood="平静", mood_score=72, interaction_capacity=64),
        )
        sync_kernel_from_day(day, world)
        self.assertTrue(
            record_event(
                world,
                kind="conversation",
                source_id="scope:message-1",
                summary="完成一次真实对话交换",
            )
        )
        self.assertFalse(
            record_event(
                world,
                kind="conversation",
                source_id="scope:message-1",
                summary="重复交换不会新增事实",
            )
        )
        context = kernel_context(world)
        self.assertIn("统一生命内核", context)
        self.assertIn("完成一次真实对话交换", context)
        self.assertEqual(len(world["kernel"]["events"]), 1)
        self.assertEqual(len(world["kernel"]["autobiography"]), 1)

    def test_planned_artifact_is_not_an_observed_result_and_reflection_is_grounded(
        self,
    ):
        now = datetime.datetime(2026, 10, 7, 10)
        world = {"body": {"energy": 60}}
        action = {
            "action_id": "study-1",
            "action_type": "study",
            "target": "学习构图",
            "duration_minutes": 3,
            "payload": {
                "thread_id": "photography",
                "artifact": "预期笔记",
                "obstacle": "可能被打断",
                "next_step": "练习留白",
                "close_thread": True,
            },
        }
        run = {"action": action, "active_seconds": 180, "body_before": {"energy": 65}}
        first = record_action_outcome(
            world, action, status="committed", completed_at=now, run=run
        )
        record_action_outcome(
            world, action, status="committed", completed_at=now, run=run
        )
        self.assertEqual(len(world["kernel"]["action_outcomes"]), 1)
        self.assertEqual(first["observed_minutes"], 3)
        self.assertIsNone(first["artifact"])
        self.assertEqual(first["obstacle"], "")
        self.assertEqual(world["kernel"]["open_threads"][0]["status"], "open")
        self.assertEqual(world["kernel"]["open_threads"][0]["practice_minutes"], 3)
        self.assertEqual(
            world["kernel"]["causal_traces"][0]["before"]["body"]["energy"], 65
        )
        apply_action_reflections(
            world,
            [{"action_id": "invented", "summary": "不存在的经历"}],
            allowed_action_ids={"study-1"},
            now=now,
        )
        self.assertNotIn("reflection", first)
        apply_action_reflections(
            world,
            [
                {
                    "action_id": "study-1",
                    "summary": "本次练习已结束，继续试不同留白",
                    "next_step": "练习留白",
                    "artifact": {
                        "kind": "practice_note",
                        "content": "把主体放在边缘，比较留白方向。",
                    },
                }
            ],
            allowed_action_ids={"study-1"},
            now=now,
        )
        self.assertFalse(first["reflection"]["observed"])
        self.assertEqual(first["artifact"]["source"], "post_action_generation")
        self.assertIn("已保存的本轮数字笔记", kernel_context(world))

    def test_self_model_requires_role_ownership_and_new_evidence(self):
        world = {}
        now = datetime.datetime(2026, 10, 7)
        update_self_model(
            world,
            preferences=[{"id": 1, "content": "喜欢摄影"}],
            focus=[{"id": 2, "label": "构图练习"}],
            skills={"构图": {"practice_minutes": 3, "sessions": 1}},
            now=now,
        )
        model = world["kernel"]["self_model"]
        self.assertEqual(model["interests"], [])
        self.assertFalse(model["capabilities"][0]["verified_mastery"])
        update = {
            "owner": "self",
            "field": "interest",
            "id": "photography",
            "text": "喜欢摄影构图",
            "source_ids": ["preference:1"],
            "reason": "角色自己的已有偏好",
        }
        apply_self_model_updates(world, [update], sources={"preference:1"}, now=now)
        apply_self_model_updates(world, [update], sources={"preference:1"}, now=now)
        self.assertEqual(model["interests"][0]["support_count"], 1)
        self.assertEqual(model["interests"][0]["status"], "tentative")
        apply_self_model_updates(
            world,
            [{**update, "source_ids": ["action:study-1"]}],
            sources={"action:study-1"},
            now=now,
        )
        self.assertEqual(model["interests"][0]["status"], "supported")
        apply_self_model_updates(
            world,
            [
                {**update, "owner": "user", "id": "user-wish"},
                {**update, "id": "no-evidence", "source_ids": ["missing"]},
            ],
            sources={"preference:1"},
            now=now,
        )
        self.assertEqual(len(model["interests"]), 1)


class ContinuousGoalsTest(unittest.TestCase):
    now = datetime.datetime(2026, 10, 7, 1)

    def make_world(self):
        world = {}
        apply_goal_decisions(world, goal_payload(), sources={"persona"}, now=self.now)
        return world

    def test_stages_require_dependencies_and_real_practice_is_credited_once(self):
        world = self.make_world()
        goal = world["goals"][0]
        run = {
            "goal_id": goal["id"],
            "step_id": "进阶",
            "active_seconds": 120,
            "action": {"action_id": "a", "action_type": "study"},
        }
        credit_goal(world, run, self.now)
        self.assertEqual(goal["steps"][1]["practice_minutes"], 0)
        run["step_id"] = "基础"
        credit_goal(world, run, self.now)
        credit_goal(world, run, self.now)
        self.assertEqual(goal["steps"][0]["status"], "completed")
        self.assertEqual([step["id"] for step in ready_steps(goal)], ["进阶"])
        self.assertEqual(world["skills"]["构图练习"]["sessions"], 1)

    def test_replan_keeps_completed_and_partially_practised_stages(self):
        world = self.make_world()
        goal = world["goals"][0]
        run = {
            "goal_id": goal["id"],
            "step_id": "基础",
            "active_seconds": 120,
            "action": {"action_id": "a", "action_type": "study"},
        }
        credit_goal(world, run, self.now)
        first = copy.deepcopy(goal["steps"][0])
        update = {
            "goal_updates": [
                {
                    "goal_id": goal["id"],
                    "status": "blocked",
                    "evidence_id": "persona",
                    "reason": "需要先准备练习资料",
                    "remaining_steps": [
                        {
                            "id": "替代",
                            "title": "补充练习",
                            "action_type": "study",
                            "required_minutes": 3,
                            "depends_on": ["基础"],
                        }
                    ],
                }
            ]
        }
        apply_goal_decisions(world, update, sources={"persona"}, now=self.now)
        self.assertEqual(goal["steps"][0], first)
        self.assertEqual(goal["steps"][1]["id"], "替代")
        self.assertEqual(goal["status"], "blocked")

    def test_unproven_goals_and_cyclic_or_unknown_dependencies_are_rejected(self):
        payload = goal_payload()
        payload["new_goals"][0]["source_id"] = "state:today"
        world = {}
        apply_goal_decisions(world, payload, sources={"state:today"}, now=self.now)
        self.assertEqual(world["goals"], [])
        steps = goal_payload()["new_goals"][0]["steps"]
        steps[0]["depends_on"] = ["进阶"]
        self.assertEqual(normalize_steps(steps), [])
        steps[0]["depends_on"] = ["不存在"]
        self.assertEqual(normalize_steps(steps), [])


class ContinuousLifeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = f"{self.temp.name}/life.db"
        self.archive = LifeArchive(self.path)
        self.now = datetime.datetime(2026, 10, 7, 1)
        self.runtime = Runtime(self.archive, self.now)
        await self.archive.save_day(
            DayRecord(
                date=self.runtime.date,
                outfit="居家服",
                state=LifeState(energy=65, sleepiness=30),
            )
        )
        await self.runtime._check_continuous_life_once()

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.temp.cleanup()

    async def start(
        self,
        action_type="study",
        duration=2,
        *,
        payload=None,
        planned=None,
        **decision_fields,
    ):
        day = await self.archive.get_day(self.runtime.date)
        world = await self.archive.get_continuous_life()
        decision = {
            "decision": "start",
            "reason": "根据当前需求自主决定",
            "_sources": {"persona"},
            **decision_fields,
        }
        if planned:
            decision.update(
                action_id=planned.action_id, _candidates=[planned.as_dict()]
            )
        else:
            decision["new_action"] = {
                "owner": "self",
                "action_type": action_type,
                "target": "进行自主练习",
                "duration_minutes": duration,
                "payload": payload or {},
            }
        await self.runtime._apply_continuous_decision(
            day, world, decision, self.runtime.now
        )
        return (await self.archive.get_continuous_life()).get("run")

    async def tick(self, minutes=1):
        self.runtime.now += datetime.timedelta(minutes=minutes)
        await self.runtime._check_continuous_life_once()

    async def test_no_chat_action_runs_and_completes_with_process_evidence(self):
        run = await self.start()
        action_id = run["action"]["action_id"]
        await self.tick()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "running")
        self.assertEqual(world["run"]["active_seconds"], 60)
        self.assertEqual(
            await self.archive.get_life_action_receipts(action_id=action_id), []
        )
        await self.tick()
        world = await self.archive.get_continuous_life()
        self.assertNotIn("run", world)
        self.assertEqual(world["history"][-1]["status"], "completed")
        receipts = await self.archive.get_life_action_receipts(action_id=action_id)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].status, "simulated")
        self.assertEqual(len(await self.archive.get_life_action_outcomes()), 1)
        events = (await self.archive.get_continuous_life())["kernel"]["events"]
        self.assertEqual(
            [item["kind"] for item in events],
            ["action_started", "action_outcome", "action_completed"],
        )
        self.assertEqual(world["kernel"]["action_outcomes"][0]["observed_minutes"], 2)
        self.assertEqual(
            len(await self.archive.get_continuous_life_history(kind="outcome")), 1
        )
        memory = await self.archive.search_long_term_memories("进行自主练习", limit=5)
        self.assertTrue(
            any(item.source_table == "continuous_life_entries" for item in memory)
        )
        day = await self.archive.get_day(self.runtime.date)
        self.assertIn(action_id, day.meta["planned_life_actions"])
        self.assertIn("已完成", day.meta["continuous_life_context"])

    async def test_time_passing_without_selection_does_not_complete_a_plan(self):
        action = LifeActionIntent.from_value(
            {
                "action_id": "p",
                "action_type": "meal",
                "target": "吃饭",
                "timeline_index": 0,
                "duration_minutes": 2,
            }
        )

        def plan(day):
            day.timeline = [
                TimelineItem(time="01:00", activity="吃饭", duration_minutes=2)
            ]
            day.meta["planned_life_actions"] = json.dumps([action.as_dict()])

        await self.archive.mutate_day(self.runtime.date, plan)
        await self.tick(15)
        day = await self.archive.get_day(self.runtime.date)
        self.assertNotEqual(day.timeline[0].execution_state, "completed")
        self.assertEqual(await self.archive.get_life_action_receipts(action_id="p"), [])

    async def test_selected_plan_uses_existing_execution_states_and_completes(self):
        action = LifeActionIntent.from_value(
            {
                "action_id": "plan",
                "action_type": "study",
                "target": "练习构图",
                "timeline_index": 0,
                "duration_minutes": 2,
            }
        )

        def plan(day):
            day.timeline = [
                TimelineItem(time="01:00", activity="练习构图", duration_minutes=2)
            ]
            day.meta["planned_life_actions"] = json.dumps([action.as_dict()])

        await self.archive.mutate_day(self.runtime.date, plan)
        await self.start(planned=action)
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).timeline[0].execution_state,
            "active",
        )
        await self.tick()
        await self.tick()
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).timeline[0].execution_state,
            "completed",
        )
        self.assertEqual(
            len(await self.archive.get_life_action_receipts(action_id="plan")), 1
        )

    async def test_clock_moving_backwards_does_not_credit_an_interval_twice(self):
        await self.start(duration=5)
        await self.tick()
        self.runtime.now -= datetime.timedelta(seconds=30)
        await self.runtime._check_continuous_life_once()
        self.runtime.now += datetime.timedelta(seconds=30)
        await self.runtime._check_continuous_life_once()
        self.assertEqual(
            (await self.archive.get_continuous_life())["run"]["active_seconds"], 60
        )

    async def test_incoming_schedule_is_considered_before_long_action_check_interval(
        self,
    ):
        day = await self.archive.get_day(self.runtime.date)
        day.timeline = [
            TimelineItem(time="01:04", activity="已确认一起看电影", duration_minutes=90)
        ]
        world = {
            "run": {
                "status": "running",
                "action": {"duration_minutes": 60},
                "active_seconds": 0,
            }
        }
        self.assertEqual(
            next_decision_time(day, world, self.now),
            self.now + datetime.timedelta(minutes=4),
        )

    async def test_external_meal_receipt_updates_body_once_and_is_atomic(self):
        day = await self.archive.get_day(self.runtime.date)
        action = LifeActionIntent.from_value(
            {
                "action_id": "meal-confirmed",
                "action_type": "meal",
                "target": "吃过午饭",
                "evidence": "明确执行回执",
            }
        )
        await self.runtime.composer.settle_and_persist_life_action(
            day, action, now=self.now
        )
        first = await self.archive.get_continuous_life()
        await self.runtime.composer.settle_and_persist_life_action(
            day, action, now=self.now
        )
        second = await self.archive.get_continuous_life()
        self.assertEqual(first["body"], second["body"])
        self.assertLess(first["body"]["hunger"], 25)

    async def test_manual_clear_removes_goals_without_erasing_current_body(self):
        await self.archive.mutate_continuous_life(
            self.runtime.date,
            lambda day, world: apply_goal_decisions(
                world, goal_payload(), sources={"persona"}, now=self.now
            ),
        )
        before = await self.archive.get_continuous_life()
        await self.archive.clear_storage_category("experience")
        cleared = await self.archive.get_continuous_life()
        self.assertNotIn("goals", cleared)
        self.assertEqual(before["body"], cleared["body"])
        self.assertNotIn(
            "continuous_life_context",
            (await self.archive.get_day(self.runtime.date)).meta,
        )

    async def test_restart_gap_pauses_and_does_not_credit_unobserved_time(self):
        await self.start(duration=5)
        await self.tick()
        await self.archive.aclose()
        self.archive = LifeArchive(self.path)
        self.runtime = Runtime(
            self.archive, self.runtime.now + datetime.timedelta(hours=3)
        )
        await self.runtime._check_continuous_life_once()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "paused")
        self.assertEqual(world["run"]["active_seconds"], 60)
        self.assertEqual(world["body"]["uncertain_minutes"], 180)
        self.assertEqual(await self.archive.get_life_action_outcomes(), [])

    async def test_quick_restart_also_requires_reconfirming_the_action(self):
        await self.start(duration=5)
        await self.tick()
        other = Runtime(self.archive, self.runtime.now + datetime.timedelta(seconds=10))
        await other._check_continuous_life_once()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "paused")
        self.assertEqual(world["run"]["active_seconds"], 60)
        self.assertAlmostEqual(world["body"]["uncertain_minutes"], 10 / 60)

    async def test_resume_does_not_count_pause_time(self):
        await self.start(duration=3)
        await self.tick()
        day = await self.archive.get_day(self.runtime.date)
        world = await self.archive.get_continuous_life()
        await self.runtime._apply_continuous_decision(
            day, world, {"decision": "pause", "reason": "先聊一会"}, self.runtime.now
        )
        await self.tick(10)
        day = await self.archive.get_day(self.runtime.date)
        world = await self.archive.get_continuous_life()
        await self.runtime._apply_continuous_decision(
            day, world, {"decision": "resume", "reason": "继续练习"}, self.runtime.now
        )
        await self.tick()
        self.assertEqual(
            (await self.archive.get_continuous_life())["run"]["active_seconds"], 120
        )
        event_kinds = [
            item["kind"]
            for item in (await self.archive.get_continuous_life())["kernel"]["events"]
        ]
        self.assertIn("action_pause", event_kinds)
        self.assertIn("action_resume", event_kinds)

    async def test_cross_day_thread_survives_and_does_not_repeat_practice(self):
        await self.start(
            payload={"thread_id": "photography", "next_step": "继续观察留白"}
        )
        await self.tick()
        await self.tick()
        next_date = "2026-10-08"
        await self.archive.save_day(DayRecord(date=next_date, state=LifeState()))
        self.runtime.date = next_date
        self.runtime.now = datetime.datetime(2026, 10, 8, 10)
        await self.runtime._check_continuous_life_once()
        world = await self.archive.get_continuous_life()
        thread = world["kernel"]["open_threads"][0]
        self.assertEqual(thread["id"], "photography")
        self.assertEqual(thread["practice_minutes"], 2)
        self.assertIn(
            "继续观察留白",
            (await self.archive.get_day(next_date)).meta["continuous_life_context"],
        )
        self.assertEqual(
            len(
                await self.archive.get_continuous_life_history(
                    kind="outcome", date="2026-10-07"
                )
            ),
            1,
        )

    async def test_archival_history_survives_checkpoint_trimming_and_atomic_rollback(
        self,
    ):
        for index in range(3):

            def record(day, world):
                for number in range(70):
                    record_event(
                        world,
                        kind="test_fact",
                        source_id=f"fact:{index}:{number}",
                        at=self.now,
                        summary="真实归档事实",
                    )

            await self.archive.mutate_continuous_life(self.runtime.date, record)
        world = await self.archive.get_continuous_life()
        self.assertEqual(len(world["kernel"]["autobiography"]), 160)
        self.assertEqual(
            len(await self.archive.get_continuous_life_history(limit=200)), 200
        )

        def reject(day, stored):
            record_event(stored, kind="test_fact", source_id="rollback", at=self.now)
            raise RuntimeError("回滚")

        with self.assertRaises(RuntimeError):
            await self.archive.mutate_continuous_life(self.runtime.date, reject)
        self.assertFalse(
            any(
                item.get("source_id") == "rollback"
                for item in await self.archive.get_continuous_life_history(limit=200)
            )
        )

    async def test_v19_database_upgrades_without_losing_day_or_checkpoint(self):
        before = await self.archive.get_continuous_life()
        await self.archive.aclose()
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE continuous_life_entries")
            connection.execute("UPDATE meta SET value='19' WHERE key='schema_version'")
        self.archive = LifeArchive(self.path)
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).outfit, "居家服"
        )
        self.assertEqual(
            (await self.archive.get_continuous_life())["body"], before["body"]
        )
        self.assertEqual(await self.archive.get_continuous_life_history(), [])

    async def test_archived_event_is_immutable_after_checkpoint_forgets_it(self):
        def record_original(day, world):
            record_event(
                world,
                kind="conversation",
                source_id="stable-exchange",
                at=self.now,
                summary="原始事实",
            )

        await self.archive.mutate_continuous_life(self.runtime.date, record_original)

        def replay(day, world):
            world["kernel"]["events"] = []
            world["kernel"]["autobiography"] = []
            record_event(
                world,
                kind="conversation",
                source_id="stable-exchange",
                at=self.now + datetime.timedelta(days=1),
                summary="重放不能改写事实",
            )

        await self.archive.mutate_continuous_life(self.runtime.date, replay)
        entries = await self.archive.get_continuous_life_history(kind="event")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["summary"], "原始事实")
        self.assertEqual(entries[0]["at"], self.now.isoformat())
        checkpoint = await self.archive.get_continuous_life()
        self.assertEqual(checkpoint["kernel"]["events"][0]["summary"], "原始事实")
        self.assertNotIn(
            "重放不能改写事实",
            (await self.archive.get_day(self.runtime.date)).meta[
                "continuous_life_context"
            ],
        )

    async def test_committed_checkpoint_recovers_receipts_and_goal_progress_once(self):
        await self.archive.mutate_continuous_life(
            self.runtime.date,
            lambda day, world: apply_goal_decisions(
                world, goal_payload(), sources={"persona"}, now=self.now
            ),
        )
        world = await self.archive.get_continuous_life()
        goal_id = world["goals"][0]["id"]
        run = await self.start(goal_id=goal_id, step_id="基础")
        await self.tick()
        original = self.runtime.composer._save_action_receipt
        with patch.object(
            self.runtime.composer,
            "_save_action_receipt",
            AsyncMock(side_effect=RuntimeError("模拟进程中断")),
        ):
            self.runtime.now += datetime.timedelta(minutes=1)
            with self.assertRaises(RuntimeError):
                await self.runtime._check_continuous_life_once()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "settling")
        self.assertEqual(world["goals"][0]["steps"][0]["practice_minutes"], 0)
        self.runtime.composer._save_action_receipt = original
        await self.tick()
        await self.runtime._finish_continuous_action(run, self.runtime.now)
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["goals"][0]["steps"][0]["practice_minutes"], 2)
        self.assertEqual(world["skills"]["构图练习"]["sessions"], 1)
        self.assertEqual(
            len(
                await self.archive.get_life_action_receipts(
                    action_id=run["action"]["action_id"]
                )
            ),
            1,
        )

    async def test_cook_consumes_stock_once_and_recovery_does_not_charge_again(self):
        await self.archive.adjust_pantry_item("鸡蛋", 2, unit="个")
        run = await self.start(
            "cook",
            duration=1,
            payload={"ingredients": [{"name": "鸡蛋", "quantity": 1, "unit": "个"}]},
        )
        with patch.object(
            self.runtime.composer,
            "_save_action_receipt",
            AsyncMock(side_effect=RuntimeError("中断")),
        ):
            self.runtime.now += datetime.timedelta(minutes=1)
            with self.assertRaises(RuntimeError):
                await self.runtime._check_continuous_life_once()
        self.assertEqual((await self.archive.get_pantry_items())[0]["quantity"], 1)
        await self.tick()
        self.assertEqual((await self.archive.get_pantry_items())[0]["quantity"], 1)
        meals = await self.archive.get_meal_records()
        self.assertEqual(len(meals), 1)
        self.assertEqual(meals[0]["action_id"], run["action"]["action_id"])

    async def test_stock_removed_during_cooking_does_not_create_a_meal(self):
        await self.archive.adjust_pantry_item("鸡蛋", 1, unit="个")
        await self.start(
            "cook",
            duration=1,
            payload={"ingredients": [{"name": "鸡蛋", "quantity": 1, "unit": "个"}]},
        )
        await self.archive.adjust_pantry_item("鸡蛋", -1, unit="个")
        await self.tick()
        self.assertEqual(await self.archive.get_meal_records(), [])
        self.assertEqual(
            (await self.archive.get_continuous_life())["history"][-1]["status"],
            "failed",
        )

    async def test_new_user_outfit_prevents_running_plan_from_overwriting_it(self):
        action = LifeActionIntent.from_value(
            {
                "action_id": "outfit",
                "action_type": "change_outfit",
                "target": "外出服",
                "timeline_index": 0,
                "duration_minutes": 2,
            }
        )

        def plan(day):
            day.timeline = [
                TimelineItem(time="01:00", activity="换外出服", duration_minutes=2)
            ]
            day.meta["planned_life_actions"] = json.dumps([action.as_dict()])

        await self.archive.mutate_day(self.runtime.date, plan)
        await self.start(planned=action)

        def instruction(day):
            day.outfit = "刚换的睡衣"
            day.meta.update(
                outfit_fact_source="user_instruction",
                outfit_fact_confirmed_at="2026-10-07 01:01:00",
            )

        await self.archive.mutate_day(self.runtime.date, instruction)
        await self.tick()
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).outfit, "刚换的睡衣"
        )
        self.assertEqual(
            (await self.archive.get_continuous_life())["history"][-1]["status"],
            "cancelled",
        )

    async def test_replaced_timeline_is_not_cancelled_by_stale_action(self):
        action = LifeActionIntent.from_value(
            {
                "action_id": "old",
                "action_type": "study",
                "target": "练习",
                "timeline_index": 0,
                "duration_minutes": 2,
            }
        )

        def plan(day):
            day.timeline = [
                TimelineItem(time="01:00", activity="练习", duration_minutes=2)
            ]
            day.meta["planned_life_actions"] = json.dumps([action.as_dict()])

        await self.archive.mutate_day(self.runtime.date, plan)
        await self.start(planned=action)

        def replace(day):
            day.timeline = [
                TimelineItem(time="01:00", activity="新安排", duration_minutes=30)
            ]
            day.meta["planned_life_actions"] = "[]"

        await self.archive.mutate_day(self.runtime.date, replace)
        await self.tick()
        self.assertNotEqual(
            (await self.archive.get_day(self.runtime.date)).timeline[0].execution_state,
            "cancelled",
        )

    async def test_rest_delay_blocks_spontaneous_sleep_and_pauses_existing_sleep(self):
        await self.start("rest", duration=30, rest_kind="sleep")
        await self.archive.mutate_day(
            self.runtime.date,
            lambda day: day.meta.update(rest_delay_until="2026-10-07 02:00:00"),
        )
        await self.tick()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "paused")
        self.assertFalse(world["sleeping"])
        day = await self.archive.get_day(self.runtime.date)
        await self.runtime._apply_continuous_decision(
            day, world, {"decision": "resume", "reason": "尝试睡眠"}, self.runtime.now
        )
        self.assertEqual(
            (await self.archive.get_continuous_life())["run"]["status"], "paused"
        )

    async def test_cross_day_completion_keeps_original_day_and_shared_body(self):
        self.runtime.now = datetime.datetime(2026, 10, 7, 23, 59)
        await self.runtime._check_continuous_life_once()
        await self.start(duration=2)
        self.runtime.date = "2026-10-08"
        await self.archive.save_day(
            DayRecord(date=self.runtime.date, state=LifeState())
        )
        await self.tick()
        await self.tick()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["history"][-1]["date"], "2026-10-07")
        current = await self.archive.get_day("2026-10-08")
        self.assertEqual(current.state.energy, round(world["body"]["energy"]))

    async def test_slow_decision_does_not_block_chat_and_stale_result_is_rejected(self):
        begun, release = asyncio.Event(), asyncio.Event()

        async def decision(*args):
            begun.set()
            await release.wait()
            return {
                "decision": "start",
                "reason": "旧决定",
                "new_action": {
                    "owner": "self",
                    "action_type": "study",
                    "target": "旧练习",
                    "duration_minutes": 5,
                },
            }

        self.runtime._continuous_life_decision = decision
        await self.archive.mutate_continuous_life(
            self.runtime.date, lambda day, world: world.pop("next_decision_at", None)
        )
        pending = asyncio.create_task(self.runtime.check_continuous_life())
        await asyncio.wait_for(begun.wait(), 1)
        try:
            await asyncio.wait_for(
                self.runtime.note_continuous_chat_exchange(
                    SimpleNamespace(message_id="1", unified_msg_origin="chat")
                ),
                0.5,
            )
        finally:
            release.set()
            await pending
        self.assertNotIn("run", await self.archive.get_continuous_life())

    async def test_real_reply_wakes_sleep_without_losing_observed_rest(self):
        await self.start("rest", duration=30, rest_kind="sleep")
        self.runtime.now += datetime.timedelta(seconds=45)
        event = SimpleNamespace(message_id="wake", unified_msg_origin="current-chat")
        await self.runtime.note_continuous_chat_exchange(event)
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "paused")
        self.assertEqual(world["run"]["active_seconds"], 45)
        self.assertFalse(world["sleeping"])
        self.assertEqual(world["last_chat_scope"], "current-chat")
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).state.sleep.depth, "awake"
        )
        await self.runtime.note_continuous_chat_exchange(event)
        self.assertEqual(world, await self.archive.get_continuous_life())

    async def test_current_chat_is_evidence_even_when_not_a_reference_conversation(
        self,
    ):
        runtime = self.runtime
        await runtime.note_continuous_chat_exchange(
            SimpleNamespace(message_id="new", unified_msg_origin="current-chat")
        )
        runtime.composer._get_persona = AsyncMock(return_value="喜爱摄影的角色")
        runtime.composer._collect_recent_chat_context = AsyncMock(return_value="无")
        runtime.composer._cleanup_conversation = AsyncMock()
        runtime._read_recent_context_messages = AsyncMock(
            return_value=[
                {
                    "role": "user",
                    "content": "刚才说好的事情有变化，想先聊聊",
                    "message_id": "new",
                }
            ]
        )
        runtime.get_text_provider = AsyncMock(return_value=object())
        runtime.call_text_model = AsyncMock(
            return_value='{"decision":"wait","reason":"先重新确认安排"}'
        )
        day = await self.archive.get_day(runtime.date)
        world = await self.archive.get_continuous_life()
        payload = await ContinuousLifeMixin._continuous_life_decision(
            runtime, day, world, runtime.now
        )
        self.assertIn("刚才说好的事情有变化", runtime.call_text_model.call_args.args[1])
        self.assertIn("chat:current-chat:new", payload["_sources"])

    async def test_media_and_unplanned_location_actions_cannot_be_simulated(self):
        for action_type in (
            "photo",
            "video",
            "social",
            "chat",
            "move",
            "travel",
            "change_outfit",
        ):
            self.assertIsNone(await self.start(action_type))
        self.assertEqual(await self.archive.get_life_action_receipts(), [])

    async def test_state_refresh_preserves_body_and_concurrent_user_outfit(self):
        runtime = self.runtime
        runtime._state_refresh_recalled = lambda event: False
        runtime._persist_state_side_records = AsyncMock()
        runtime._apply_state_continuity = StatusMixin._apply_state_continuity
        old_day = await self.archive.get_day(runtime.date)
        await self.archive.mutate_day(
            runtime.date, lambda day: setattr(day, "outfit", "新睡衣")
        )
        await self.tick()
        body = (await self.archive.get_continuous_life())["body"]
        spec = _StateRefreshSpec(runtime.date, runtime.now, "chat", "交谈", None, False)
        current = await StatusMixin._commit_state_refresh(
            runtime,
            old_day,
            {"state": {"energy": 5, "sleepiness": 95, "mood_score": 62}},
            spec,
        )
        self.assertEqual(current.outfit, "新睡衣")
        self.assertEqual(current.state.energy, round(body["energy"]))
        self.assertEqual(current.state.sleepiness, round(body["sleep_pressure"]))


class QuietLifeTest(unittest.IsolatedAsyncioTestCase):
    async def test_clock_keeps_life_job_when_chat_state_updates_are_disabled(self):
        task = AsyncMock()
        clock = LifeRhythmClock(
            LifeSettings.from_dict({"state_config": {"enabled": False}}),
            task,
            task,
            continuous_life_task=task,
        )
        clock.start()
        try:
            jobs = getattr(clock.scheduler, "jobs", None)
            ids = (
                set(jobs)
                if isinstance(jobs, dict)
                else {job.id for job in clock.scheduler.get_jobs()}
            )
            self.assertIn("continuous_life", ids)
            self.assertNotIn("auto_life_check", ids)
        finally:
            clock.stop()

    async def test_quiet_guard_blocks_unsolicited_but_allows_agreed_delivery(self):
        runtime = ProactiveSendMixin()
        runtime._state_refresh_in_quiet_hours = lambda now: True
        runtime.media_request_is_current_turn = lambda event: True
        unsolicited = SimpleNamespace(_daily_life_quiet_guard=True)
        agreed = SimpleNamespace()
        self.assertFalse(runtime._proactive_send_is_current(unsolicited))
        self.assertTrue(runtime._proactive_send_is_current(agreed))
