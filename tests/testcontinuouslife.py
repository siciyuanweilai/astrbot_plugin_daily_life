import asyncio
import copy
import datetime
import json
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from componentfixtures import LifeActionComponent, ProactiveSendComponent
from core.life.body import ContinuousBody
from core.life.continuity import execution_context
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
from core.models import LifeActionIntent
from core.prompts import LIFE_ACTION_SCOPE_RULES
from core.runtime.continuous import next_decision_time
from core.runtime.status import StateRefreshService, _StateRefreshSpec
from core.runtime.timer import LifeRhythmClock
from support import (
    DailyLifeRuntime,
    DayRecord,
    LifeArchive,
    LifeSettings,
    LifeState,
    TimelineItem,
)


class Composer(LifeActionComponent):
    def __init__(self, archive, domains):
        self.archive = archive
        self.domains = domains
        self._style_catalog_context = AsyncMock(return_value="")

    async def sync_day_world_facts(self, *args, **kwargs):
        return []

    def _compute_sleep_continuity(self, *args):
        return 0, 0, 0


class Runtime(DailyLifeRuntime):
    def __init__(self, archive, now):
        self.archive = archive
        self.now = now
        self.date = now.strftime("%Y-%m-%d")
        self.config = LifeSettings.from_dict({})
        self.domains = LifeDomainService(self.config.domains, archive)
        self.composer = Composer(archive, self.domains)
        self.composer.config = self.config
        self.continuous_life._decide = AsyncMock(return_value={})
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
    def test_only_actual_meal_reduces_hunger(self):
        for action_type in ("cook", "order_food"):
            with self.subTest(action_type=action_type):
                body = ContinuousBody(hunger=80)
                body.complete(action_type, {})
                self.assertEqual(body.hunger, 80)
        body = ContinuousBody(hunger=80)
        body.complete("meal", {})
        self.assertEqual(body.hunger, 15)

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
        self.assertEqual(goal["steps"][0]["status"], "awaiting_review")
        self.assertEqual([step["id"] for step in ready_steps(goal)], ["基础"])
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
        await self.runtime.continuous_life._check_continuous_life_once()

    async def asyncTearDown(self):
        await self.runtime.continuous_life.close()
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
            decision["new_actions"] = [{
                "owner": "self",
                "action_type": action_type,
                "target": "进行自主练习",
                "duration_minutes": duration,
                "payload": payload or {},
            }]
        await self.runtime.continuous_life._apply_continuous_decision(
            day, world, decision, self.runtime.now
        )
        return (await self.archive.get_continuous_life()).get("run")

    async def tick(self, minutes=1):
        self.runtime.now += datetime.timedelta(minutes=minutes)
        await self.runtime.continuous_life._check_continuous_life_once()

    async def test_document_study_requires_actual_reading_receipt(self):
        source = await self.archive.memory_life.import_source(scope="global", title="真实资料", content="这一段的原始依据")
        await self.start(duration=1, payload={"knowledge_source_id":source["id"]})
        await self.tick(1)
        world = await self.archive.get_continuous_life()
        self.assertFalse(world.get("run"))
        self.assertEqual((await self.archive.memory_life.library(["global"]))[0]["read_count"],0)
        self.assertNotEqual(world["kernel"]["action_outcomes"][-1]["status"],"committed")

    async def test_clock_updates_survive_completed_action_replay(self):
        day = await self.archive.get_day(self.runtime.date)
        specs = [
            ("00:10", "收拾桌面", "chore", "daily_plan"),
            ("00:20", "多煮一份面", "cook", "commitment"),
            ("00:35", "一起吃面", "meal", "commitment"),
            ("00:50", "整理练习笔记", "study", "daily_plan"),
            ("01:30", "给植物浇水", "chore", "daily_plan"),
        ]
        day.timeline = [
            TimelineItem(time=at, activity=title, duration_minutes=5)
            for at, title, _, _ in specs
        ]
        actions = [
            LifeActionIntent(
                action_id=f"clock-replay:{index}", action_type=kind,
                target=title, timeline_index=index, duration_minutes=5, source=source,
            )
            for index, (_, title, kind, source) in enumerate(specs)
        ]
        day.timeline[0].execution_state = "completed"
        day.timeline[0].execution_evidence = "已执行并收好桌面"
        day.meta.update(
            planned_life_actions=json.dumps([action.as_dict() for action in actions]),
            auto_life_next_check_at="2026-10-07 02:00",
            auto_outfit_context="stable",
        )
        await self.archive.save_day(day)
        await self.runtime.composer.settle_completed_planned_actions(day, now=self.now)

        def start(latest, world):
            latest.timeline[3].execution_state = "active"
            world["run"] = {
                "date": day.date, "status": "running",
                "action": actions[3].as_dict(), "reason": "正在整理笔记",
                "started_at": "2026-10-07T00:50:00", "active_seconds": 60,
            }

        await self.archive.mutate_continuous_life(day.date, start)
        runtime = self.runtime
        runtime.reconcile_due_commitments_for_day = AsyncMock(return_value=False)
        runtime.refresh_state_for_day = AsyncMock()
        runtime._schedule_outfit_scene_sync_needed = lambda *_: False
        runtime._synchronize_outfit_with_schedule = lambda *_: False
        runtime._part_refresh_component._outfit_context_signature = lambda *_: "stable"

        for _ in range(2):
            updated = await runtime._run_autonomous_life_check(
                day.date, self.now, source="auto", detail="状态巡检",
                status_reason="timeline_execution", update_weather=False,
            )
            stored = await self.archive.get_day(day.date)
            expected = ["completed", "elapsed", "elapsed", "active", "planned"]
            self.assertEqual([item.execution_state for item in updated.timeline], expected)
            self.assertEqual([item.execution_state for item in stored.timeline], expected)
            sessions = {
                item["action_id"]: item["status"]
                for item in await self.archive.get_activity_sessions(limit=0)
            }
            self.assertEqual([sessions[action.action_id] for action in actions], expected)
            self.assertEqual(await self.archive.get_life_action_receipts(
                action_id=actions[1].action_id
            ), [])
        runtime.refresh_state_for_day.assert_not_awaited()

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

    async def test_current_action_and_timed_followup_keep_separate_execution_evidence(self):
        runtime = self.runtime
        target = "从长椅起身到步道饮水点接半杯温水，小口喝完，把晒热的胳膊在树荫里凉一凉"
        next_step = "01:05 沿树影走回去"
        future = LifeActionIntent.from_value({
            "action_id": "return-walk", "action_type": "move", "target": "走回去",
            "timeline_index": 0, "duration_minutes": 10,
            "payload": {"origin": "饮水点", "destination": "街口"},
        })
        day = await self.archive.get_day(runtime.date)
        day.timeline = [TimelineItem(time="01:05", activity="沿树影走回去", duration_minutes=10)]
        day.meta["planned_life_actions"] = json.dumps([future.as_dict()], ensure_ascii=False)
        await self.archive.save_day(day)
        runtime.composer._get_persona = AsyncMock(return_value="角色")
        runtime.composer._collect_recent_chat_context = AsyncMock(return_value="无")
        runtime.composer._cleanup_conversation = AsyncMock()
        runtime.get_text_provider = AsyncMock(return_value=object())
        runtime.call_text_model = AsyncMock(return_value=json.dumps({
            "decision": "start", "reason": "刚走了一段路，先喝点水",
            "new_actions": [{
                "owner": "self", "action_type": "drink", "target": target,
                "duration_minutes": 2, "payload": {"volume_ml": 250, "next_step": next_step},
            }],
        }, ensure_ascii=False))
        world = await self.archive.get_continuous_life()
        decision = await runtime.continuous_decision.choose(day, world, runtime.now)
        prompt = runtime.call_text_model.call_args.args[1]
        self.assertIn(LIFE_ACTION_SCOPE_RULES, prompt)
        inputs = json.loads(prompt.split("【眼前内容】\n", 1)[1])
        self.assertEqual(inputs["timeline"][0]["at"], "2026-10-07T01:05:00")
        await runtime.continuous_life._apply_continuous_decision(day, world, decision, runtime.now)
        world = await self.archive.get_continuous_life()
        action_id = world["run"]["action"]["action_id"]
        context = execution_context(world)
        current = next(line for line in context.splitlines() if line.startswith("当前自主行动："))
        self.assertIn(target, current)
        self.assertNotIn("01:05", current)
        self.assertIn(f"后续意向（尚未执行，届时重新判断）：{next_step}", context)

        await self.tick(2)
        world = await self.archive.get_continuous_life()
        completed = world["history"][-1]
        self.assertEqual(completed["action"]["target"], target)
        self.assertEqual(completed["status"], "completed")
        outcome = next(item for item in world["kernel"]["action_outcomes"] if item["action_id"] == action_id)
        self.assertEqual(outcome["target"], target)
        self.assertEqual(outcome["next_step"], next_step)
        self.assertEqual(outcome["next_step_source"], "action_plan")
        self.assertEqual(len(await self.archive.get_life_action_receipts(action_id=action_id)), 1)
        await self.tick(4)
        saved = await self.archive.get_day(runtime.date)
        future_node = next(item for item in saved.timeline if item.time == "01:05")
        self.assertNotEqual(future_node.execution_state, "completed")
        self.assertEqual(await self.archive.get_life_action_receipts(action_id=future.action_id), [])
        world = await self.archive.get_continuous_life()
        self.assertEqual(len(world["history"]), 1)

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
        await self.runtime.continuous_life._check_continuous_life_once()
        self.runtime.now += datetime.timedelta(seconds=30)
        await self.runtime.continuous_life._check_continuous_life_once()
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
            next_decision_time(day, world, self.now, refresh_minutes=30),
            self.now + datetime.timedelta(minutes=4),
        )

    async def test_routine_decisions_follow_config_even_during_ordinary_chat(self):
        await self.start(duration=120)
        self.runtime.config.state.refresh_minutes = 10
        decide = self.runtime.continuous_life._decide
        decide.reset_mock()
        decide.return_value = {"decision": "continue", "reason": "继续当前练习"}
        for minute in range(1, 10):
            self.runtime.now = self.now + datetime.timedelta(minutes=minute)
            await self.runtime.note_continuous_chat_exchange(
                SimpleNamespace(message_id=str(minute), unified_msg_origin="chat")
            )
            await self.runtime.continuous_life._check_continuous_life_once()
        decide.assert_not_awaited()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["active_seconds"], 540)
        await self.tick()
        decide.assert_awaited_once()

    async def test_failed_and_invalid_decisions_wait_for_shared_interval(self):
        self.runtime.config.state.refresh_minutes = 15
        decide = self.runtime.continuous_life._decide
        decide.reset_mock()
        decide.return_value = {"decision": "wait"}
        await self.archive.mutate_continuous_life(
            self.runtime.date, lambda day, world: world.pop("next_decision_at", None)
        )
        await self.runtime.continuous_life._check_continuous_life_once()
        for _ in range(14):
            await self.tick()
        decide.assert_awaited_once()
        decide.side_effect = TimeoutError()
        await self.tick()
        self.assertEqual(decide.await_count, 2)
        for _ in range(14):
            await self.tick()
        self.assertEqual(decide.await_count, 2)
        await self.tick()
        self.assertEqual(decide.await_count, 3)

    async def test_conversation_action_is_reviewed_immediately_once_without_false_completion(self):
        await self.start(duration=120)
        self.runtime.config.state.refresh_minutes = 30
        decide = self.runtime.continuous_life._decide
        decide.reset_mock()
        decide.return_value = {"decision": "continue", "reason": "先完成当前练习再安排"}
        event = SimpleNamespace(message_id="action-1", unified_msg_origin="chat")
        for _ in range(2):
            result = await self.runtime.continuous_life.request_conversation_action(
                event, "想先完成这一段练习再去休息",
            )
            self.assertIn("尚不代表完成", result)
        world = await self.archive.get_continuous_life()
        self.assertEqual(len(world["conversation_action_ids"]), 1)
        self.assertEqual(world["conversation_action_request"]["status"], "pending")
        self.assertEqual(world["run"]["status"], "running")
        await self.runtime.continuous_life._check_continuous_life_once()
        decide.assert_awaited_once()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["conversation_action_request"]["status"], "reviewed")
        self.assertEqual(world["run"]["status"], "running")
        await self.runtime.continuous_life._check_continuous_life_once()
        decide.assert_awaited_once()

    async def test_new_scene_and_schedule_bypass_routine_interval(self):
        await self.start(duration=120)
        decide = self.runtime.continuous_life._decide
        decide.reset_mock()
        await self.archive.mutate_day(
            self.runtime.date, lambda day: setattr(day, "outfit", "换好的外出穿搭")
        )
        await self.tick()
        decide.assert_awaited_once()
        await self.tick()
        decide.assert_awaited_once()

        def invite(day):
            day.timeline.append(TimelineItem(
                time="01:20", activity="新确认的电影约定", duration_minutes=90,
            ))

        await self.archive.mutate_day(self.runtime.date, invite)
        await self.tick()
        self.assertEqual(decide.await_count, 2)

    async def test_significant_body_change_is_reviewed_before_routine_interval(self):
        await self.start(duration=120)
        decide = self.runtime.continuous_life._decide
        decide.reset_mock()
        await self.archive.mutate_continuous_life(
            self.runtime.date, lambda day, world: world["body"].update(thirst=90)
        )
        await self.tick()
        decide.assert_awaited_once()
        await self.tick()
        decide.assert_awaited_once()

    async def test_action_completion_settles_without_waiting_for_routine_interval(self):
        self.runtime.config.state.refresh_minutes = 120
        run = await self.start(duration=2)
        decide = self.runtime.continuous_life._decide
        decide.reset_mock()
        await self.tick()
        decide.assert_not_awaited()
        await self.tick()
        decide.assert_awaited_once()
        self.assertEqual(len(await self.archive.get_life_action_receipts(
            action_id=run["action"]["action_id"],
        )), 1)

    async def test_deferred_schedule_waits_for_confirmed_new_time(self):
        day = await self.archive.get_day(self.runtime.date)
        item = TimelineItem(time="01:04", activity="准备睡觉", duration_minutes=60)
        day.timeline = [item]
        day.meta.update(
            rest_delay_date=day.date,
            rest_delay_targets=json.dumps([[item.time, item.activity]]),
            rest_delay_until="2026-10-07T01:12:00",
        )
        self.assertEqual(
            next_decision_time(day, {}, self.now, refresh_minutes=30),
            self.now + datetime.timedelta(minutes=12),
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

    async def test_restart_gap_preserves_action_without_crediting_unobserved_time(self):
        await self.start(duration=5)
        await self.tick()
        await self.archive.aclose()
        self.archive = LifeArchive(self.path)
        self.runtime = Runtime(
            self.archive, self.runtime.now + datetime.timedelta(hours=3)
        )
        await self.runtime.continuous_life._check_continuous_life_once()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "running")
        self.assertEqual(world["run"]["observation_state"], "pending")
        self.assertEqual(world["run"]["active_seconds"], 60)
        self.assertEqual(world["body"]["uncertain_minutes"], 180)
        self.assertEqual(await self.archive.get_life_action_outcomes(), [])

        await self.tick()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["active_seconds"], 60)
        self.assertEqual(world["body"]["uncertain_minutes"], 181)
        day = await self.archive.get_day(self.runtime.date)
        await self.runtime.continuous_life._apply_continuous_decision(
            day, world, {"decision": "continue", "reason": "确认仍在练习"}, self.runtime.now
        )
        confirmed = await self.archive.get_continuous_life()
        self.assertEqual(confirmed["run"]["observation_state"], "observed")
        self.assertEqual(confirmed["run"]["active_seconds"], 60)
        self.assertTrue(confirmed["run"]["observation_gap"])
        kinds = [event["kind"] for event in confirmed["kernel"]["events"]]
        self.assertIn("observation_gap", kinds)
        self.assertIn("action_reconfirmed", kinds)
        self.assertNotIn("action_pause", kinds)
        await self.tick()
        self.assertEqual(
            (await self.archive.get_continuous_life())["run"]["active_seconds"], 120
        )

    async def test_quick_restart_also_requires_reconfirming_the_action(self):
        await self.start(duration=5)
        await self.tick()
        other = Runtime(self.archive, self.runtime.now + datetime.timedelta(seconds=10))
        await other.continuous_life._check_continuous_life_once()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "running")
        self.assertEqual(world["run"]["observation_state"], "pending")
        self.assertEqual(world["run"]["active_seconds"], 60)
        self.assertAlmostEqual(world["body"]["uncertain_minutes"], 10 / 60)

    async def test_observer_gap_preserves_last_confirmed_sleep_until_real_reply(self):
        await self.start("rest", duration=360, rest_kind="sleep")
        for _ in range(46):
            await self.tick()
        before = await self.archive.get_continuous_life()
        self.assertTrue(before["sleeping"])
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).state.sleep.depth, "deep_sleep"
        )

        await self.tick(4)
        await self.tick()
        world = await self.archive.get_continuous_life()
        self.assertTrue(world["sleeping"])
        self.assertEqual(world["run"]["status"], "running")
        self.assertEqual(world["run"]["observation_state"], "pending")
        self.assertEqual(world["run"]["active_seconds"], before["run"]["active_seconds"])
        day = await self.archive.get_day(self.runtime.date)
        self.assertEqual(day.state.sleep.depth, "deep_sleep")
        self.assertIn("当前进展待确认", day.meta["continuous_life_context"])
        self.assertNotIn("暂停", world["run"]["reason"])
        self.assertEqual(await self.archive.get_life_action_outcomes(), [])

        await self.runtime.note_continuous_chat_exchange(
            SimpleNamespace(message_id="wake-after-gap", unified_msg_origin="current-chat")
        )
        awake = await self.archive.get_continuous_life()
        self.assertFalse(awake["sleeping"])
        self.assertEqual(awake["run"]["status"], "paused")
        self.assertEqual(awake["run"]["observation_state"], "observed")
        self.assertEqual(awake["run"]["active_seconds"], before["run"]["active_seconds"])
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).state.sleep.depth, "awake"
        )

    async def test_reconfirmation_must_pass_action_validation(self):
        await self.start(duration=5)
        await self.tick(4)
        world = await self.archive.get_continuous_life()
        day = await self.archive.get_day(self.runtime.date)
        with patch.object(
            self.runtime.continuous_life, "_validate_action",
            AsyncMock(return_value=(False, "当前条件不再满足")),
        ):
            await self.runtime.continuous_life._apply_continuous_decision(
                day, world, {"decision": "continue", "reason": "继续练习"}, self.runtime.now
            )
        after = await self.archive.get_continuous_life()
        self.assertEqual(after["run"]["observation_state"], "pending")
        self.assertEqual(after["run"]["active_seconds"], 0)
        self.assertEqual(await self.archive.get_life_action_outcomes(), [])

    async def test_pending_sleep_does_not_deepen_from_unobserved_time(self):
        await self.start("rest", duration=360, rest_kind="sleep")
        await self.tick()
        before = await self.archive.get_continuous_life()
        await self.tick(60)
        world = await self.archive.get_continuous_life()
        self.assertTrue(world["sleeping"])
        self.assertEqual(world["run"]["active_seconds"], before["run"]["active_seconds"])
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).state.sleep.depth, "light_sleep"
        )
        self.assertEqual(world["kernel"]["agency"]["observation_state"], "pending")

    async def test_decision_before_a_gap_does_not_reconfirm_unobserved_progress(self):
        await self.start(duration=5)
        day = await self.archive.get_day(self.runtime.date)
        world = await self.archive.get_continuous_life()
        self.runtime.now += datetime.timedelta(minutes=4)
        await self.runtime.continuous_life._apply_continuous_decision(
            day, world, {"decision": "continue", "reason": "继续练习"}, self.runtime.now
        )
        await self.runtime.continuous_life._check_continuous_life_once()
        after = await self.archive.get_continuous_life()
        self.assertEqual(after["run"]["observation_state"], "pending")
        self.assertEqual(after["run"]["active_seconds"], 0)
        self.assertNotIn(
            "action_reconfirmed", [event["kind"] for event in after["kernel"]["events"]]
        )

    async def test_resume_does_not_count_pause_time(self):
        await self.start(duration=3)
        await self.tick()
        day = await self.archive.get_day(self.runtime.date)
        world = await self.archive.get_continuous_life()
        await self.runtime.continuous_life._apply_continuous_decision(
            day, world, {"decision": "pause", "reason": "先聊一会"}, self.runtime.now
        )
        await self.tick(10)
        day = await self.archive.get_day(self.runtime.date)
        world = await self.archive.get_continuous_life()
        await self.runtime.continuous_life._apply_continuous_decision(
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
        await self.runtime.continuous_life._check_continuous_life_once()
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
                await self.runtime.continuous_life._check_continuous_life_once()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "settling")
        self.assertEqual(world["goals"][0]["steps"][0]["practice_minutes"], 0)
        self.runtime.composer._save_action_receipt = original
        await self.tick()
        await self.runtime.continuous_life._finish_continuous_action(run, self.runtime.now)
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
                await self.runtime.continuous_life._check_continuous_life_once()
        self.assertEqual((await self.archive.get_pantry_items())[0]["quantity"], 1)
        await self.tick()
        self.assertEqual((await self.archive.get_pantry_items())[0]["quantity"], 1)
        meals = await self.archive.get_meal_records()
        self.assertEqual(len(meals), 1)
        self.assertEqual(meals[0]["action_id"], run["action"]["action_id"])
        self.assertEqual(meals[0]["status"], "prepared")

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

    async def test_invite_outfit_stays_pending_until_execution_finishes(self):
        from core.life.itinerary import apply_edited_timeline
        action = LifeActionIntent.from_value({
            "action_id": "invite-outfit", "action_type": "change_outfit", "target": "外出连衣裙",
            "timeline_index": 0, "duration_minutes": 2, "source": "invite",
            "effects": [{"field": "energy", "operation": "add", "value": -2}],
        })
        def plan(day):
            apply_edited_timeline(day, [TimelineItem(time="01:00", activity="换衣服")],
                                  {"_planned_life_actions": [action.as_dict()]})
        await self.archive.mutate_day(self.runtime.date, plan)
        await self.start(planned=action)
        await self.tick()
        self.assertEqual((await self.archive.get_day(self.runtime.date)).outfit, "居家服")
        self.assertEqual(await self.archive.get_life_action_receipts(action_id=action.action_id), [])
        await self.tick()
        day = await self.archive.get_day(self.runtime.date)
        self.assertEqual(day.outfit, "外出连衣裙")
        self.assertEqual(day.timeline[0].execution_state, "completed")
        receipts = await self.archive.get_life_action_receipts(action_id=action.action_id)
        self.assertEqual(len(receipts), 1)
        await self.tick()
        self.assertEqual(len(await self.archive.get_life_action_receipts(action_id=action.action_id)), 1)

    async def test_invalid_effect_is_rejected_before_execution_starts(self):
        action = LifeActionIntent.from_value({
            "action_id": "invalid-effect", "action_type": "study", "target": "自主练习",
            "timeline_index": 0, "duration_minutes": 2,
            "effects": [{"field": "interaction_capacity", "operation": "add", "value": 5}],
        })
        def plan(day):
            day.timeline = [TimelineItem(time="01:00", activity="自主练习")]
            day.meta["planned_life_actions"] = json.dumps([action.as_dict()])
        await self.archive.mutate_day(self.runtime.date, plan)
        self.assertIsNone(await self.start(planned=action))
        day = await self.archive.get_day(self.runtime.date)
        self.assertNotEqual(day.timeline[0].execution_state, "active")
        await self.tick(2)
        self.assertEqual(await self.archive.get_life_action_receipts(action_id=action.action_id), [])
        self.assertEqual(await self.archive.get_life_action_outcomes(), [])

    async def _plan_partial_wardrobe_change(self):
        from core.life.itinerary import apply_edited_timeline
        items = []
        for kind, name in (("outfit", "针织上衣与长裤"), ("footwear", "拖鞋"), ("footwear", "板鞋")):
            item = await self.archive.upsert_style_catalog_item({
                "kind": kind, "description": name, "confidence": 0.95,
                "source_image_hash": name * 16,
            })
            items.append(item)
        main, old_shoes, shoes = items
        await self.archive.adopt_wardrobe_items(
            [item.id for item in items], event_id="own", at=str(self.now), reason="已有衣物",
        )
        action = LifeActionIntent.from_value({
            "action_id": "planned-shoes", "action_type": "change_outfit",
            "target": "针织上衣与长裤；板鞋", "timeline_index": 0, "duration_minutes": 2,
            "payload": {
                "outfit_components": {"footwear": {"state": "worn", "description": "板鞋"}},
                "catalog_reference_ids": [shoes.id], "scene_category": "public",
            },
        })
        day = await self.archive.get_day(self.runtime.date)
        day.outfit = "针织上衣与长裤；拖鞋"
        day.meta.update({
            "style_catalog_reference_ids": f"{main.id},{old_shoes.id}",
            "outfit_fact_confirmed_at": str(self.now), "style": "甜美居家混搭",
            "outfit_scene_category": "home",
            "outfit_components": json.dumps({
                "main_clothing": {"state": "worn", "description": "针织上衣与长裤"},
                "footwear": {"state": "worn", "description": "拖鞋"},
            }),
        })
        apply_edited_timeline(day, [TimelineItem(time="01:00", activity="换鞋出门")],
                              {"_planned_life_actions": [action.as_dict()]})
        await self.archive.save_day(day, require_wearable=True)
        await self.archive.start_wardrobe_care(
            [main.id], decision="dirty", event_id="dirty-main", at=str(self.now),
            observer="test", duration_minutes=0, reason="脱下后清洗",
        )
        self.assertIsNotNone(await self.start(planned=action))
        return main, old_shoes, shoes

    async def test_executor_changes_shoes_without_rewearing_dirty_main(self):
        main, old_shoes, shoes = await self._plan_partial_wardrobe_change()
        await self.tick(2)
        day = await self.archive.get_day(self.runtime.date)
        self.assertEqual(day.outfit, "针织上衣与长裤；板鞋")
        self.assertNotIn("style", day.meta)
        self.assertEqual(set(day.meta["style_catalog_reference_ids"].split(",")),
                         {str(main.id), str(shoes.id)})
        snapshot = (await self.archive.get_wardrobe_snapshot())["items"]
        self.assertTrue(snapshot[str(main.id)]["currently_worn"])
        self.assertFalse(snapshot[str(old_shoes.id)]["currently_worn"])
        world = await self.archive.get_continuous_life()
        self.assertNotIn("run", world)
        self.assertEqual(world["history"][-1]["status"], "completed")
        await self.tick()
        self.assertEqual(len(await self.archive.get_life_action_receipts(action_id="planned-shoes")), 1)

    async def test_executor_rechecks_laundry_inside_outfit_commit(self):
        main, old_shoes, shoes = await self._plan_partial_wardrobe_change()
        original = self.runtime.continuous_life._validate_action
        async def send_to_wash_after_check(action, **kwargs):
            result = await original(action, **kwargs)
            self.assertTrue(await self.archive.start_wardrobe_care(
                [shoes.id], decision="dirty", event_id="dirty-before-commit",
                at=str(self.runtime.now), observer="test", duration_minutes=0,
                reason="校验后弄湿鞋子",
            ))
            self.assertTrue(await self.archive.start_wardrobe_care(
                [shoes.id], decision="washing", event_id="wash-before-commit",
                at=str(self.runtime.now), observer="test", duration_minutes=60,
                reason="校验后送洗",
            ))
            return result
        self.runtime.continuous_life._validate_action = send_to_wash_after_check
        await self.tick(2)
        day = await self.archive.get_day(self.runtime.date)
        self.assertEqual(day.outfit, "针织上衣与长裤；拖鞋")
        self.assertEqual(day.meta["style_catalog_reference_ids"], f"{main.id},{old_shoes.id}")
        self.assertEqual(day.outfit_history, {})
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["run"]["status"], "failed")
        await self.tick()
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["history"][-1]["status"], "failed")

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
        await self.runtime.continuous_life._apply_continuous_decision(
            day, world, {"decision": "resume", "reason": "尝试睡眠"}, self.runtime.now
        )
        self.assertEqual(
            (await self.archive.get_continuous_life())["run"]["status"], "paused"
        )

    async def test_cross_day_completion_keeps_original_day_and_shared_body(self):
        self.runtime.now = datetime.datetime(2026, 10, 7, 23, 59)
        await self.runtime.continuous_life._check_continuous_life_once()
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
                "new_actions": [{
                    "owner": "self",
                    "action_type": "study",
                    "target": "旧练习",
                    "duration_minutes": 5,
                }],
            }

        self.runtime.continuous_life._decide = decision
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

    async def test_close_cancels_active_check_and_rejects_new_checks(self):
        service = self.runtime.continuous_life
        started = asyncio.Event()

        async def wait_for_shutdown():
            started.set()
            await asyncio.Event().wait()

        service._check_continuous_life_once = AsyncMock(side_effect=wait_for_shutdown)
        pending = asyncio.create_task(self.runtime.check_continuous_life())
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(service.close(), 1)
        self.assertTrue(pending.cancelled())
        self.assertFalse(service._check_lock.locked())
        self.assertEqual(service._checks, set())
        await self.runtime.check_continuous_life()
        service._check_continuous_life_once.assert_awaited_once()

    async def test_check_lock_and_observer_belong_to_each_service_instance(self):
        other = Runtime(self.archive, self.runtime.now)
        first, second = self.runtime.continuous_life, other.continuous_life
        self.assertNotEqual(first._observer_id, second._observer_id)
        first_started, second_started, release = (
            asyncio.Event(), asyncio.Event(), asyncio.Event()
        )

        async def wait_first():
            first_started.set()
            await release.wait()

        async def wait_second():
            second_started.set()
            await release.wait()

        first._check_continuous_life_once = AsyncMock(side_effect=wait_first)
        second._check_continuous_life_once = AsyncMock(side_effect=wait_second)
        left = asyncio.create_task(self.runtime.check_continuous_life())
        right = asyncio.create_task(other.check_continuous_life())
        try:
            await asyncio.wait_for(first_started.wait(), 1)
            await asyncio.wait_for(second_started.wait(), 1)
            # 同一实例跳过重入，另一个实例仍能独立执行。
            await asyncio.wait_for(self.runtime.check_continuous_life(), 1)
            first._check_continuous_life_once.assert_awaited_once()
            second._check_continuous_life_once.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(left, right)
            await second.close()

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

    async def test_active_continuation_uses_compact_context_and_expands_for_new_needs(self):
        runtime = self.runtime
        runtime.composer._get_persona = AsyncMock(return_value="喜欢摄影的角色")
        runtime.composer._collect_recent_chat_context = AsyncMock(return_value="无")
        runtime.composer._cleanup_conversation = AsyncMock()
        runtime.get_text_provider = AsyncMock(return_value=object())
        runtime.call_text_model = AsyncMock(return_value='{"decision":"wait"}')
        day = await self.archive.get_day(runtime.date)
        world = await self.archive.get_continuous_life()
        initial = await runtime.continuous_decision.choose(day, world, runtime.now)
        world.update(goal_source_key=initial["_goal_source_key"], goal_review_at=runtime.now.isoformat(),
                     run={"status": "running", "action": {"action_id": "reading", "action_type": "study"}})
        runtime.composer._style_catalog_context.reset_mock()
        runtime.call_text_model.reset_mock()
        runtime.call_text_model.return_value = '{"decision":"continue","self_model_updates":{"invented":"不能越权修改"}}'
        result = await runtime.continuous_decision.choose(day, world, runtime.now)
        self.assertEqual(result["decision"], "continue")
        self.assertNotIn("self_model_updates", result)
        runtime.composer._style_catalog_context.assert_not_awaited()
        self.assertEqual(runtime.call_text_model.await_count, 1)
        prompt = runtime.call_text_model.call_args.args[1]
        data = json.loads(prompt.split("【眼前内容】\n", 1)[1])
        self.assertEqual(data["outfit"], "居家服")
        self.assertIn("body", data)
        self.assertEqual(list(data)[:4], ["persona", "preferences", "self_model", "body"])
        self.assertNotIn("focus", data)
        runtime.call_text_model.reset_mock()
        runtime.call_text_model.side_effect = ['{"decision":"expand","reason":"口渴了要喝水"}', '{"decision":"wait"}']
        await runtime.continuous_decision.choose(day, world, runtime.now)
        self.assertEqual(runtime.call_text_model.await_count, 2)
        runtime.composer._style_catalog_context.assert_awaited_once()
        full_data = json.loads(runtime.call_text_model.call_args.args[1].split("【眼前内容】\n", 1)[1])
        self.assertEqual(list(full_data)[:3], ["persona", "preferences", "self_model"])
        self.assertIn("body", full_data["world"])
        self.assertNotIn("focus", full_data)

    async def test_real_browsing_evidence_enters_shared_interests_slowly(self):
        from core.browsing.bookmarks import BrowseArchive, record_id
        runtime = self.runtime
        runtime.config.state.refresh_minutes = 20
        runtime.composer._get_persona = AsyncMock(return_value="喜欢探索的角色")
        runtime.composer._collect_recent_chat_context = AsyncMock(return_value="无")
        runtime.composer._cleanup_conversation = AsyncMock()
        runtime.get_text_provider = AsyncMock(return_value=object())
        runtime.call_text_model = AsyncMock(return_value='{"decision":"wait"}')
        day = await self.archive.get_day(runtime.date)
        world = await self.archive.get_continuous_life()
        before = await runtime.continuous_decision.choose(day, world, runtime.now)
        world.update(goal_source_key=before["_goal_source_key"], goal_review_at=runtime.now.isoformat())
        store = BrowseArchive(self.archive)
        for bvid, scope in (("BV1xx411c7mD", "global"), ("BV1yy411c7mD", "global"), ("BV1zz411c7mD", "private:a")):
            await store.save({
                "id": record_id(bvid, scope=scope), "scope": scope, "status": "unfinished",
                "created_at": runtime.now.isoformat(), "updated_at": runtime.now.isoformat(),
                "bvid": bvid, "page": 1, "title": "实验视频", "position": 60, "duration": 300,
                "evidence": [{"quote": "实际对照实验的字幕"}], "impression": "想再了解变量控制",
                "interest": "实验", "interest_delta": 0.1,
            })
        early = await runtime.continuous_decision.choose(day, world, runtime.now + datetime.timedelta(minutes=19))
        self.assertFalse(early["_goal_review_due"])
        after = await runtime.continuous_decision.choose(day, world, runtime.now + datetime.timedelta(minutes=20))
        self.assertTrue(after["_goal_review_due"])
        references = sorted(source for source in after["_sources"] if source.startswith("browsing:"))
        self.assertEqual(len(references), 2)
        self.assertNotIn("browsing:" + record_id("BV1zz411c7mD", scope="private:a"), references)
        update = {"owner": "self", "field": "interest", "id": "experiments", "text": "好奇实验方法", "reason": "真实浏览后的持续好奇"}
        for _ in range(3):
            apply_self_model_updates(world, [{**update, "source_ids": references[:1]}], sources=after["_sources"], now=runtime.now)
        interest = world["kernel"]["self_model"]["interests"][0]
        self.assertEqual((interest["support_count"], interest["status"]), (1, "tentative"))
        apply_self_model_updates(world, [{**update, "source_ids": references[1:]}], sources=after["_sources"], now=runtime.now)
        self.assertEqual((interest["support_count"], interest["status"]), (2, "supported"))
        prompt = runtime.call_text_model.call_args.args[1]
        self.assertIn("实际对照实验的字幕", prompt)

    async def test_goal_review_reuses_evidence_and_batches_new_chat_with_shared_interval(self):
        runtime = self.runtime
        runtime.config.state.refresh_minutes = 20
        runtime.composer._get_persona = AsyncMock(return_value="喜欢摄影的角色")
        runtime.composer._collect_recent_chat_context = AsyncMock(return_value="无")
        runtime.composer._cleanup_conversation = AsyncMock()
        runtime.get_text_provider = AsyncMock(return_value=object())
        runtime.call_text_model = AsyncMock(return_value='{"decision":"wait"}')
        day = await self.archive.get_day(runtime.date)
        world = await self.archive.get_continuous_life()
        initial = await runtime.continuous_decision.choose(day, world, runtime.now)
        self.assertTrue(initial["_goal_review_due"])
        world.update(goal_source_key=initial["_goal_source_key"],
                     goal_review_at=runtime.now.isoformat())
        stable = await runtime.continuous_decision.choose(
            day, world, runtime.now + datetime.timedelta(days=2),
        )
        self.assertFalse(stable["_goal_review_due"])

        runtime._read_recent_context_messages = AsyncMock(return_value=[{
            "role": "user", "content": "你之前的摄影练习还想继续吗", "message_id": "new",
        }])
        world.update(last_chat_scope="chat", chat_events=["chat:new"])
        early = await runtime.continuous_decision.choose(
            day, world, runtime.now + datetime.timedelta(minutes=19),
        )
        self.assertFalse(early["_goal_review_due"])
        ready = await runtime.continuous_decision.choose(
            day, world, runtime.now + datetime.timedelta(minutes=20),
        )
        self.assertTrue(ready["_goal_review_due"])

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
        payload = await runtime.continuous_decision.choose(day, world, runtime.now)
        self.assertIn("刚才说好的事情有变化", runtime.call_text_model.call_args.args[1])
        self.assertIn("chat:current-chat:new", payload["_sources"])

    async def test_delayed_preparation_remains_available_for_semantic_decision(self):
        runtime = self.runtime
        runtime.now += datetime.timedelta(minutes=12)
        runtime.composer._get_persona = AsyncMock(return_value="角色")
        runtime.composer._collect_recent_chat_context = AsyncMock(return_value="等我换件衣服")
        runtime.composer._cleanup_conversation = AsyncMock()
        runtime.get_text_provider = AsyncMock(return_value=object())
        runtime.call_text_model = AsyncMock(return_value='{"decision":"wait","reason":"先判断是否仍要出门"}')
        day = await self.archive.get_day(runtime.date)
        day.timeline = [TimelineItem(time="01:00", activity="换衣", execution_state="elapsed")]
        day.meta["planned_life_actions"] = json.dumps([{
            "action_id": "delayed", "action_type": "change_outfit", "target": "裙子",
            "timeline_index": 0, "duration_minutes": 3,
        }])
        world = await self.archive.get_continuous_life()
        result = await runtime.continuous_decision.choose(day, world, runtime.now)
        self.assertEqual(result["_candidates"][0]["action_id"], "delayed")
        self.assertGreaterEqual(result["_candidates"][0]["delay_minutes"], 12)
        self.assertEqual(result["decision"], "wait")
        self.assertEqual(await self.archive.get_life_action_receipts(), [])
        for state in ("completed", "cancelled", "skipped", "expired"):
            day.timeline[0].execution_state = state
            result = await runtime.continuous_decision.choose(day, world, runtime.now)
            self.assertEqual(result["_candidates"], [])

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
        service = StateRefreshService(
            config=lambda: runtime.config.state,
            archive=lambda: self.archive,
            compute_sleep=runtime.composer._compute_sleep_continuity,
            replan=AsyncMock(),
            sync_world_facts=runtime.composer.sync_day_world_facts,
            text_provider=AsyncMock(),
            call_text_model=AsyncMock(),
            close_text_session=AsyncMock(),
            event_was_recalled=lambda event, **kwargs: False,
            sync_kernel=runtime.sync_continuous_kernel_state,
            notify_status=runtime.mark_page_status_changed,
        )
        old_day = await self.archive.get_day(runtime.date)
        await self.archive.mutate_day(
            runtime.date, lambda day: setattr(day, "outfit", "新睡衣")
        )
        await self.tick()
        body = (await self.archive.get_continuous_life())["body"]
        spec = _StateRefreshSpec(runtime.date, runtime.now, "chat", "交谈", None, False)
        current = await service._commit_state_refresh(
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
        runtime = ProactiveSendComponent()
        runtime._state_refresh_in_quiet_hours = lambda now: True
        runtime.media_request_is_current_turn = lambda event: True
        unsolicited = SimpleNamespace(_daily_life_quiet_guard=True)
        agreed = SimpleNamespace()
        self.assertFalse(runtime._proactive_send_is_current(unsolicited))
        self.assertTrue(runtime._proactive_send_is_current(agreed))
