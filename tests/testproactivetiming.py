"""验证普通回复、闲时续话和随心裁定之间的异步边界。"""

import asyncio
import datetime
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from runtimehelpers import (
    DayRecord,
    Event,
    LifeSettings,
    LifeState,
    RuntimeAsyncHelpers,
    TimelineItem,
)


class ProactiveTimingTest(RuntimeAsyncHelpers, unittest.IsolatedAsyncioTestCase):
    def private_event(self, text="刚才的电影还挺值得聊聊", message_id="m1"):
        event = Event(
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id=message_id,
        )
        event.message_str = text
        return event

    async def test_normal_reply_replaces_timer_and_evaluates_once(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._runtime_initialized = True
        runtime._part_proactive_scope_component._proactive_candidate_delay_seconds = lambda *args: 0.02
        evaluated = asyncio.Event()
        observed = []

        async def evaluate(key, candidate, now):
            observed.append(dict(candidate))
            runtime._proactive_idle_candidates.pop(key)
            evaluated.set()

        runtime._evaluate_idle_candidate = evaluate
        event = self.private_event()
        key = event.unified_msg_origin
        base = datetime.datetime(2026, 5, 24, 12)
        try:
            runtime.note_proactive_activity(event, now=base)
            original_task = runtime._proactive_idle_tasks[key]
            runtime.note_proactive_bot_reply(event, now=base + datetime.timedelta(minutes=1))
            await asyncio.wait_for(evaluated.wait(), 1)
            self.assertTrue(original_task.cancelled())
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]["state"], "after_ordinary_reply")
            self.assertEqual(observed[0]["pending_count"], 0)
            self.assertIsNone(observed[0]["next_evaluation_at"])
            self.assertGreater(
                observed[0]["private_opportunity_at"],
                base + datetime.timedelta(minutes=6),
            )
            self.assertNotEqual(observed[0]["private_opportunity_at"], base + datetime.timedelta(minutes=61))
            self.assertEqual(runtime._proactive_idle_tasks, {})
        finally:
            await runtime._cancel_proactive_idle_tasks()

    async def test_short_follow_up_updates_context_and_silence_window(self):
        runtime, _ = self._make_proactive_runtime([])
        base = datetime.datetime(2026, 5, 24, 12)
        event = self.private_event()
        key = event.unified_msg_origin
        runtime.note_proactive_activity(self.private_event("嗯"), now=base)
        self.assertNotIn(key, runtime._proactive_idle_candidates)
        runtime.note_proactive_activity(event, now=base)
        runtime.note_proactive_bot_reply(event, now=base + datetime.timedelta(minutes=1))
        runtime.note_proactive_activity(self.private_event("嗯", "m2"), now=base + datetime.timedelta(minutes=30))
        candidate = runtime._proactive_idle_candidates[key]
        self.assertEqual(candidate["message"], "嗯")
        self.assertEqual(candidate["pending_count"], 1)
        self.assertEqual(candidate["recent_messages"][-1]["content"], "嗯")
        self.assertEqual(candidate["last_bot_reply_at"], base + datetime.timedelta(minutes=1))
        await runtime._refresh_private_proactive_timing(
            candidate, base + datetime.timedelta(minutes=30)
        )
        self.assertIsNone(candidate["next_evaluation_at"])
        self.assertFalse(runtime._part_proactive_flow_component._idle_candidate_ready(candidate, base + datetime.timedelta(minutes=31)))
        self.assertTrue(runtime._proactive_allowed_for_event(runtime._proactive_candidate_event(candidate), continuing=True))

    async def test_normal_reply_invalidates_inflight_idle_decision(self):
        runtime, _ = self._make_proactive_runtime([])
        event = self.private_event()
        key = event.unified_msg_origin
        base = datetime.datetime(2026, 5, 24, 12)
        runtime.note_proactive_activity(event, now=base)
        original = runtime._proactive_idle_candidates[key]

        async def decide(*args, **kwargs):
            runtime.note_proactive_bot_reply(event, now=base + datetime.timedelta(minutes=61))
            return {"should_reply": True, "decision": "reply", "reply_text": "已过时的续话"}

        runtime.evaluate_proactive_reply = decide
        runtime._send_proactive_message = AsyncMock(return_value=True)
        await runtime._evaluate_idle_candidate(key, original, base + datetime.timedelta(minutes=60))
        runtime._send_proactive_message.assert_not_awaited()
        self.assertIsNot(runtime._proactive_idle_candidates[key], original)
        self.assertNotIn("evaluation_in_progress", runtime._proactive_idle_candidates[key])
        self.assertEqual(runtime._proactive_lifecycle_snapshot(key)["state"], "candidate")

    async def test_old_send_completion_does_not_remove_new_candidate(self):
        for sent in (False, True):
            with self.subTest(sent=sent):
                runtime, _ = self._make_proactive_runtime([])
                event = self.private_event()
                key = event.unified_msg_origin
                base = datetime.datetime(2026, 5, 24, 12)
                runtime.note_proactive_activity(event, now=base)
                original = runtime._proactive_idle_candidates[key]
                runtime.evaluate_proactive_reply = AsyncMock(return_value={
                    "should_reply": True, "decision": "reply", "reply_text": "旧续话",
                })

                async def send(*args, **kwargs):
                    runtime.note_proactive_activity(self.private_event("另一件事", "m2"), now=base + datetime.timedelta(minutes=61))
                    return sent

                runtime._send_proactive_message = send
                runtime._commit_proactive_decision = AsyncMock()
                runtime._save_pending_reply_effect = AsyncMock()
                runtime._track_proactive_reply_effect = lambda *args: None
                await runtime._evaluate_idle_candidate(key, original, base + datetime.timedelta(minutes=60))
                self.assertEqual(runtime._proactive_idle_candidates[key]["message_id"], "m2")
                self.assertEqual(runtime._proactive_lifecycle_snapshot(key)["state"], "candidate")

    async def test_idle_reply_can_send_after_normal_reply(self):
        runtime, _ = self._make_proactive_runtime([
            '{"should_reply":true,"expression_review":{"passed":true},"confidence":0.95,'
            '"benefit":90,"timeliness":90,"continuity":90,"disruption":10,"uncertainty":10,'
            '"decision":"reply","reply_text":"刚看到一张那个电影的幕后照","reason":"有新的话题延续"}'
        ])
        event = self.private_event()
        base = datetime.datetime(2026, 5, 24, 12)
        runtime.note_proactive_activity(event, now=base)
        runtime.note_proactive_bot_reply(event, now=base + datetime.timedelta(minutes=1))
        candidate = runtime._proactive_idle_candidates[event.unified_msg_origin]
        await runtime._refresh_private_proactive_timing(
            candidate, base + datetime.timedelta(minutes=1)
        )
        due_at = candidate["private_opportunity_at"]
        await runtime.evaluate_idle_proactive_candidates(now=due_at - datetime.timedelta(seconds=1))
        self.assertEqual(runtime.context.sent_messages, [])
        await runtime.evaluate_idle_proactive_candidates(now=due_at + datetime.timedelta(seconds=1))
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["刚看到一张那个电影的幕后照"])
        ongoing = runtime._proactive_idle_candidates[event.unified_msg_origin]
        self.assertEqual(ongoing["pending_count"], 0)
        self.assertGreater(ongoing["revision"], 2)
        self.assertIsNone(ongoing["next_evaluation_at"])

    async def test_private_timing_stays_stable_across_polls_and_varies_between_turns(
        self,
    ):
        runtime, provider = self._make_proactive_runtime([])
        base = datetime.datetime(2026, 5, 24, 12)
        event = self.private_event()
        runtime.note_proactive_activity(event, now=base)
        candidate = runtime._proactive_idle_candidates[event.unified_msg_origin]
        await runtime._refresh_private_proactive_timing(candidate, base)
        first = candidate["private_opportunity_at"]
        await runtime._refresh_private_proactive_timing(
            candidate, base + datetime.timedelta(minutes=1)
        )
        self.assertEqual(candidate["private_opportunity_at"], first)
        runtime.note_proactive_activity(
            self.private_event(message_id="m2"),
            now=base + datetime.timedelta(minutes=2),
        )
        current = runtime._proactive_idle_candidates[event.unified_msg_origin]
        await runtime._refresh_private_proactive_timing(
            current, base + datetime.timedelta(minutes=2)
        )
        self.assertNotEqual(
            current["private_opportunity_at"] - current["last_activity_at"],
            first - base,
        )
        self.assertEqual(provider.prompts, [])

    async def test_life_state_changes_move_the_same_contact_opportunity(self):
        runtime, _ = self._make_proactive_runtime([])
        base = datetime.datetime(2026, 5, 24, 12)
        day = DayRecord(
            date="2026-05-24",
            state=LifeState(
                busyness=95,
                sleepiness=90,
                interaction_capacity=10,
                social=20,
                updated_at=base.isoformat(),
            ),
        )
        runtime._proactive_current_day = AsyncMock(return_value=(day.date, False, day))
        event = self.private_event()
        runtime.note_proactive_activity(event, now=base)
        candidate = runtime._proactive_idle_candidates[event.unified_msg_origin]
        await runtime._refresh_private_proactive_timing(candidate, base)
        occupied_due = candidate["private_opportunity_at"]
        day.state = LifeState(
            busyness=10,
            sleepiness=10,
            interaction_capacity=90,
            social=80,
            updated_at=base.isoformat(),
        )
        await runtime._refresh_private_proactive_timing(candidate, base)
        self.assertLess(candidate["private_opportunity_at"], occupied_due)
        self.assertGreaterEqual(
            candidate["private_opportunity_at"], base + datetime.timedelta(minutes=5)
        )

    async def test_sleeping_private_timer_notices_a_new_life_window(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._runtime_initialized = True
        current = datetime.datetime(2026, 5, 24, 12)
        base = current
        day = DayRecord(
            date="2026-05-24",
            state=LifeState(busyness=95, sleepiness=90, interaction_capacity=10),
        )
        runtime._proactive_current_day = AsyncMock(return_value=(day.date, False, day))
        sleeps, evaluations = [], []
        event = self.private_event()

        async def advance(seconds):
            nonlocal current
            sleeps.append(seconds)
            current += datetime.timedelta(seconds=seconds)
            day.state = LifeState(busyness=10, sleepiness=10, interaction_capacity=90)
            day.timeline = [TimelineItem(time="12:10", activity="午后散步")]

        async def evaluate(key, candidate, now):
            evaluations.append(now)
            runtime._proactive_idle_candidates.pop(key)

        runtime.evaluate_proactive_reply = AsyncMock()
        runtime._evaluate_idle_candidate = evaluate
        with (
            patch("core.runtime.proactive.scope.life_now", side_effect=lambda: current),
            patch("core.runtime.proactive.scope.asyncio.sleep", side_effect=advance),
        ):
            runtime.note_proactive_activity(event, now=base)
            task = runtime._proactive_idle_tasks[event.unified_msg_origin]
            await asyncio.wait_for(task, 1)
        self.assertEqual(sleeps[0], 300)
        self.assertEqual(len(evaluations), 1)
        self.assertGreater(evaluations[0], base + datetime.timedelta(minutes=10))
        self.assertLess(evaluations[0], base + datetime.timedelta(minutes=20))
        runtime.evaluate_proactive_reply.assert_not_awaited()

    async def test_life_transition_can_be_considered_before_reference_interval(self):
        runtime, provider = self._make_proactive_runtime([])
        base = datetime.datetime(2026, 5, 24, 12)
        day = DayRecord(
            date="2026-05-24",
            timeline=[TimelineItem(time="12:15", activity="午后散步")],
        )
        runtime._proactive_current_day = AsyncMock(return_value=(day.date, False, day))
        event = self.private_event()
        runtime.note_proactive_activity(event, now=base)
        candidate = runtime._proactive_idle_candidates[event.unified_msg_origin]
        await runtime._refresh_private_proactive_timing(candidate, base)
        self.assertLess(
            candidate["private_opportunity_at"], base + datetime.timedelta(minutes=30)
        )
        self.assertGreater(
            candidate["private_opportunity_at"], base + datetime.timedelta(minutes=15)
        )
        self.assertFalse(
            runtime._part_proactive_flow_component._idle_candidate_ready(
                candidate, base + datetime.timedelta(minutes=14)
            )
        )
        self.assertTrue(
            runtime._part_proactive_flow_component._idle_candidate_ready(
                candidate, base + datetime.timedelta(minutes=20)
            )
        )
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(provider.prompts, [])
        day.state = LifeState(busyness=95, sleepiness=90, interaction_capacity=10)
        await runtime._refresh_private_proactive_timing(candidate, base)
        self.assertFalse(
            runtime._part_proactive_flow_component._idle_candidate_ready(
                candidate, base + datetime.timedelta(minutes=20)
            )
        )

    async def test_cancelled_or_old_day_transitions_do_not_advance_contact(self):
        runtime, _ = self._make_proactive_runtime([])
        base = datetime.datetime(2026, 5, 24, 12)
        candidate = {
            "target_scope": self.private_event().unified_msg_origin,
            "last_activity_at": base,
        }
        await runtime._refresh_private_proactive_timing(candidate, base)
        reference_due = candidate["private_opportunity_at"]
        for date, execution in (("2026-05-24", "cancelled"), ("2026-05-23", "planned")):
            with self.subTest(date=date, execution=execution):
                day = DayRecord(
                    date=date,
                    timeline=[
                        TimelineItem(
                            time="12:15", activity="散步", execution_state=execution
                        )
                    ],
                )
                runtime._proactive_current_day = AsyncMock(
                    return_value=(date, False, day)
                )
                await runtime._refresh_private_proactive_timing(candidate, base)
                self.assertEqual(candidate["private_opportunity_at"], reference_due)

    async def test_private_revisit_and_idle_share_one_opportunity(self):
        runtime, _ = self._make_proactive_runtime([])
        base = datetime.datetime(2026, 5, 24, 12)
        event = self.private_event()
        runtime.note_proactive_activity(event, now=base)
        candidate = runtime._proactive_idle_candidates[event.unified_msg_origin]
        runtime._read_recent_context_messages = AsyncMock(
            return_value=[
                {
                    "role": "assistant",
                    "content": "等会儿再聊",
                    "timestamp": base.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp(),
                }
            ]
        )
        runtime.build_memos_hidden_context = AsyncMock(return_value="")
        await runtime._refresh_private_proactive_timing(candidate, base)
        memory = await runtime._part_proactive_revisit_component._private_revisit_memory_context(
            event.unified_msg_origin, None, "阿林", base
        )
        self.assertTrue(memory["too_recent"])
        self.assertEqual(
            memory["minimum_idle_seconds"],
            int((candidate["private_opportunity_at"] - base).total_seconds()),
        )

    async def test_private_revisit_does_not_compete_with_an_active_idle_candidate(self):
        runtime, provider = self._make_proactive_runtime([])
        runtime.config.proactive.private_revisit_enabled = True
        event = self.private_event()
        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 12))
        component = runtime._part_proactive_revisit_component
        component._get_recent_private_targets = AsyncMock(
            return_value=[SimpleNamespace()]
        )
        runtime._resolve_private_target_umo = lambda *_: event.unified_msg_origin
        component._evaluate_private_revisit_payload = AsyncMock()
        await runtime.evaluate_private_revisit_candidates()
        component._evaluate_private_revisit_payload.assert_not_awaited()
        self.assertEqual(provider.prompts, [])

    async def test_background_poll_respects_a_deferred_decision(self):
        runtime, _ = self._make_proactive_runtime([])
        base = datetime.datetime(2026, 5, 24, 12)
        event = self.private_event()
        runtime.note_proactive_activity(event, now=base)
        runtime.evaluate_proactive_reply = AsyncMock(
            return_value={
                "should_reply": False,
                "decision": "wait",
                "retry_after": 3600,
            }
        )
        due_at = base + datetime.timedelta(hours=3)
        await runtime.evaluate_idle_proactive_candidates(now=due_at)
        self.assertEqual(runtime.evaluate_proactive_reply.await_count, 1)
        await runtime.evaluate_idle_proactive_candidates(
            now=due_at + datetime.timedelta(minutes=5)
        )
        self.assertEqual(runtime.evaluate_proactive_reply.await_count, 1)
        await runtime.evaluate_idle_proactive_candidates(
            now=due_at + datetime.timedelta(hours=1)
        )
        self.assertEqual(runtime.evaluate_proactive_reply.await_count, 2)

    async def test_observing_private_chat_backs_off_instead_of_rechecking_every_five_minutes(
        self,
    ):
        runtime, _ = self._make_proactive_runtime([])
        base = datetime.datetime(2026, 5, 24, 12)
        event = self.private_event()
        runtime.note_proactive_activity(event, now=base)
        runtime.evaluate_proactive_reply = AsyncMock(
            return_value={"should_reply": False, "decision": "observe"}
        )
        now = base + datetime.timedelta(hours=3)
        await runtime.evaluate_idle_proactive_candidates(now=now)
        candidate = runtime._proactive_idle_candidates[event.unified_msg_origin]
        first_hold = candidate["next_evaluation_at"] - now
        self.assertGreater(first_hold, datetime.timedelta(minutes=5))
        await runtime.evaluate_idle_proactive_candidates(
            now=now + datetime.timedelta(minutes=5)
        )
        self.assertEqual(runtime.evaluate_proactive_reply.await_count, 1)
        second_at = candidate["next_evaluation_at"]
        await runtime.evaluate_idle_proactive_candidates(now=second_at)
        self.assertGreater(candidate["next_evaluation_at"] - second_at, first_hold)

    async def test_timer_and_background_poll_do_not_evaluate_same_candidate_twice(self):
        runtime, _ = self._make_proactive_runtime([])
        base = datetime.datetime(2026, 5, 24, 12)
        event = self.private_event()
        runtime.note_proactive_activity(event, now=base)
        candidate = runtime._proactive_idle_candidates[event.unified_msg_origin]
        started, release = asyncio.Event(), asyncio.Event()

        async def evaluate(*args, **kwargs):
            started.set()
            await release.wait()
            return {"should_reply": False, "decision": "skip"}

        runtime.evaluate_proactive_reply = AsyncMock(side_effect=evaluate)
        task = asyncio.create_task(
            runtime._evaluate_idle_candidate(
                event.unified_msg_origin, candidate, base + datetime.timedelta(hours=3)
            )
        )
        await asyncio.wait_for(started.wait(), 1)
        try:
            await runtime.evaluate_idle_proactive_candidates(
                now=base + datetime.timedelta(hours=3)
            )
            self.assertEqual(runtime.evaluate_proactive_reply.await_count, 1)
        finally:
            release.set()
            await task

    async def test_semantic_decision_survives_old_deadline_without_blocking_loop(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._init_response_gate_state()
        runtime.config = LifeSettings.from_dict({"response_gate_config": {"private_enabled": True}})
        progressed = asyncio.Event()

        async def model(*args, **kwargs):
            await asyncio.sleep(2.6)
            return '{"action":"observe","confidence":0.95,"reason":"话题已自然结束"}'

        async def other_conversation():
            await asyncio.sleep(0.02)
            progressed.set()

        runtime.call_text_model = model
        other = asyncio.create_task(other_conversation())
        task = asyncio.create_task(runtime.apply_response_gate_for_event(self.private_event()))
        await asyncio.wait_for(progressed.wait(), 0.5)
        self.assertFalse(task.done())
        decision = await task
        await other
        self.assertEqual(decision["action"], "observe")
        self.assertTrue(decision["semantic"])
        self.assertEqual(runtime._response_gate_semantic_metrics["timeout"], 0)
        self.assertEqual(runtime._response_gate_semantic_metrics["fallback"], 0)

    async def test_timeout_cancels_model_and_closes_session(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._init_response_gate_state()
        runtime.config = LifeSettings.from_dict({
            "response_gate_config": {"semantic_timeout_seconds": 1},
        })
        cancelled = asyncio.Event()

        async def model(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        runtime.call_text_model = model
        runtime.close_text_session = AsyncMock()
        result = await asyncio.wait_for(
            runtime._part_response_gate_apply_component._response_gate_semantic_decision(
                self.private_event(), score=0.9, reasons=[], pending_count=1, wait_state=None,
            ),
            timeout=2,
        )
        self.assertIsNone(result)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(runtime._response_gate_semantic_metrics["timeout"], 1)
        runtime.close_text_session.assert_awaited_once()

    async def test_gate_timeout_is_independent_of_segmentation_timeout(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._init_response_gate_state()
        runtime.config = LifeSettings.from_dict({
            "response_gate_config": {"semantic_timeout_seconds": 2},
            "chat_style_config": {"semantic_timeout_seconds": 0.3},
        })

        async def model(*args, **kwargs):
            await asyncio.sleep(1.05)
            return '{"action":"observe","confidence":0.95}'

        runtime.call_text_model = model
        decision = await runtime._part_response_gate_apply_component._response_gate_semantic_decision(
            self.private_event(), score=0.9, reasons=[], pending_count=1, wait_state=None,
        )
        self.assertEqual(decision["action"], "observe")
        self.assertEqual(runtime._response_gate_semantic_metrics["timeout"], 0)

    async def test_gate_and_idle_models_are_selected_and_called_independently(self):
        for gate_id, idle_id in (
            ("gate-model", "idle-model"),
            ("shared-model", "shared-model"),
            ("", "idle-model"),
        ):
            with self.subTest(gate=gate_id, idle=idle_id):
                runtime, _ = self._make_proactive_runtime([])
                runtime._init_response_gate_state()
                runtime.config.response_gate.provider = gate_id
                runtime.config.proactive.provider = idle_id
                providers = {key: object() for key in {gate_id, idle_id}}
                runtime.get_text_provider = AsyncMock(side_effect=lambda key: providers[key])
                runtime.call_text_model = AsyncMock(side_effect=[
                    '{"action":"observe","confidence":0.95}',
                    '{"should_reply":false,"decision":"observe","confidence":0.95}',
                ])
                runtime.close_text_session = AsyncMock()
                runtime._proactive_readiness_check = AsyncMock(return_value={"should_evaluate": True})
                runtime._build_proactive_prompt = AsyncMock(return_value="闲时回复语义裁定")
                runtime._part_proactive_flow_component._record_proactive_decision = AsyncMock()

                decision = await runtime._part_response_gate_apply_component._response_gate_semantic_decision(
                    self.private_event(), score=0.9, reasons=[], pending_count=1, wait_state=None,
                )
                idle = await runtime.evaluate_proactive_reply(
                    self.private_event(), now=datetime.datetime(2026, 5, 24, 12),
                )

                self.assertEqual(decision["action"], "observe")
                self.assertEqual(idle["decision"], "observe")
                self.assertEqual(runtime.get_text_provider.await_args_list, [call(gate_id), call(idle_id)])
                self.assertEqual(runtime.call_text_model.await_count, 2)
                gate_call, idle_call = runtime.call_text_model.await_args_list
                self.assertIs(gate_call.args[0], providers[gate_id])
                self.assertIs(idle_call.args[0], providers[idle_id])
                self.assertEqual(gate_call.kwargs["primary_provider_id"], gate_id)
                self.assertEqual(idle_call.kwargs["primary_provider_id"], idle_id)
                self.assertNotEqual(gate_call.args[2], idle_call.args[2])
                self.assertEqual(runtime.close_text_session.await_count, 2)

    async def test_stale_semantic_result_cannot_change_interaction(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._init_response_gate_state()
        runtime.apply_interaction_turn_decision = AsyncMock()

        async def model(*args, **kwargs):
            runtime._part_response_gate_apply_component._response_gate_superseded_decision = lambda event: {"superseded": True}
            return '{"action":"reply","confidence":0.95,"interaction":{"decision":"set","mode":"co_present","confidence":0.95}}'

        runtime.call_text_model = model
        result = await runtime._part_response_gate_apply_component._response_gate_semantic_decision(
            self.private_event(), score=0.9, reasons=[], pending_count=1, wait_state=None,
        )
        self.assertIsNone(result)
        runtime.apply_interaction_turn_decision.assert_not_awaited()

    async def test_semantic_prompt_includes_recent_assistant_reply(self):
        runtime, provider = self._make_proactive_runtime(['{"action":"observe","confidence":0.95}'])
        runtime._init_response_gate_state()
        runtime.structured_recent_history_messages = lambda *args, **kwargs: [
            {"role": "assistant", "name": "角色", "content": "那我们晚上再聊", "message_id": "a1"},
            {"role": "user", "content": "好", "message_id": "m1"},
        ]
        await runtime._part_response_gate_apply_component._response_gate_semantic_decision(
            self.private_event("好"), score=0.9, reasons=[], pending_count=1, wait_state=None,
        )
        self.assertIn('"role": "assistant"', provider.prompts[0])
        self.assertIn("那我们晚上再聊", provider.prompts[0])



    async def test_private_observation_continues_after_two_autonomous_turns(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime.config.proactive.private_idle_minutes = 5
        runtime.config.proactive.private_talk_frequency = 0.85
        base = datetime.datetime(2026, 5, 24, 12)
        event = self.private_event()
        key = event.unified_msg_origin
        runtime.note_proactive_activity(event, now=base)
        runtime.evaluate_proactive_reply = AsyncMock(side_effect=[
            {"should_reply": True, "decision": "reply", "reply_text": "刚把照片洗出来了"},
            {"should_reply": True, "decision": "reply", "reply_text": "有一张光线特别喜欢"},
        ])
        runtime._send_proactive_message = AsyncMock(return_value=True)
        runtime._commit_proactive_decision = AsyncMock()
        runtime._save_pending_reply_effect = AsyncMock()
        runtime._track_proactive_reply_effect = lambda *args: None
        for offset in (30, 60):
            now = base + datetime.timedelta(minutes=offset)
            with patch("core.runtime.proactive.decide.flow.life_now", return_value=now):
                await runtime.evaluate_idle_proactive_candidates(now=now)
            self.assertIn(key, runtime._proactive_idle_candidates)
            self.assertEqual(runtime._proactive_idle_candidates[key]["last_activity_at"], now)
            self.assertEqual(runtime._proactive_lifecycle_snapshot(key)["state"], "cooldown")
        self.assertEqual(runtime._send_proactive_message.await_count, 2)
        self.assertEqual(runtime._proactive_idle_candidates[key]["pending_count"], 0)

    async def test_temporary_private_silence_is_not_negative_feedback(self):
        runtime, _ = self._make_proactive_runtime([])
        key = self.private_event().unified_msg_origin
        now = datetime.datetime(2026, 5, 24, 12)
        for _ in range(10):
            runtime._update_proactive_air_after_decision(
                key, {"decision": "observe", "reason": "还没有新的想法"}, now, sent=False
            )
        state = runtime._get_proactive_air_state(key)
        self.assertEqual(state["quiet_count"], 0)
        self.assertEqual(state["silence_inertia"], 0)
        self.assertLessEqual(runtime._proactive_air_delay_remaining(key, now), 300)
        state["last_effect"] = "negative"
        runtime._update_proactive_air_after_decision(
            key, {"decision": "wait", "reason": "对方明确希望先安静"}, now, sent=False
        )
        self.assertEqual(state["last_effect"], "negative")
        self.assertGreater(state["silence_inertia"], 0)

    async def test_private_revisit_interprets_naive_history_in_life_timezone(self):
        runtime, _ = self._make_proactive_runtime([])
        now = datetime.datetime(2026, 5, 24, 12)
        runtime._read_recent_context_messages = AsyncMock(return_value=[
            {"role": "assistant", "content": "刚回家", "timestamp": "2026-05-24T11:30:00"}
        ])
        runtime.build_memos_hidden_context = AsyncMock(return_value="")
        memory = await runtime._part_proactive_revisit_component._private_revisit_memory_context(
            self.private_event().unified_msg_origin, None, "阿林", now
        )
        self.assertEqual(memory["silence_seconds"], 1800)

    async def test_private_revisit_does_not_evaluate_during_quiet_hours(self):
        runtime, provider = self._make_proactive_runtime([])
        runtime.config.proactive.private_revisit_enabled = True
        runtime.config.state.quiet_hours = "00:00-06:30"
        targets = AsyncMock(return_value=[])
        runtime._part_proactive_revisit_component._get_recent_private_targets = targets
        with patch("core.runtime.spine.pulse.life_now", return_value=datetime.datetime(2026, 5, 24, 1)):
            await runtime.run_private_revisit_check()
        targets.assert_not_awaited()
        self.assertEqual(provider.prompts, [])


    async def test_delivery_status_controls_retry_and_actual_text_memory(self):
        for status in ("deferred", "unknown", "partial"):
            with self.subTest(status=status):
                runtime, _ = self._make_proactive_runtime([])
                event = self.private_event()
                key = event.unified_msg_origin
                base = datetime.datetime(2026, 5, 24, 12)
                runtime.note_proactive_activity(event, now=base)
                candidate = runtime._proactive_idle_candidates[key]
                runtime.evaluate_proactive_reply = AsyncMock(return_value={
                    "should_reply": True, "decision": "reply", "reply_text": "第一句\n第二句",
                })

                async def send(*args, **kwargs):
                    kwargs["send_payload"]["_delivery"] = {
                        "status": status, "reason_code": "test_delivery", "reason": "测试传输结果",
                        "sent_text": "第一句" if status == "partial" else "",
                    }
                    return status == "partial"

                runtime._send_proactive_message = send
                runtime._commit_proactive_decision = AsyncMock()
                runtime._save_pending_reply_effect = AsyncMock()
                runtime._track_proactive_reply_effect = Mock()
                runtime._update_proactive_air_after_decision = Mock()
                await runtime._evaluate_idle_candidate(key, candidate, base + datetime.timedelta(minutes=60))
                if status == "partial":
                    self.assertEqual(runtime._commit_proactive_decision.await_args.args[3], "第一句")
                    self.assertEqual(runtime._save_pending_reply_effect.await_args.args[3], "第一句")
                    self.assertEqual(candidate["state"], "after_proactive_reply")
                else:
                    runtime._commit_proactive_decision.assert_not_awaited()
                    runtime._save_pending_reply_effect.assert_not_awaited()
                    runtime._update_proactive_air_after_decision.assert_not_called()
                    if status == "deferred":
                        self.assertIs(runtime._proactive_idle_candidates[key], candidate)
                        self.assertGreater(candidate["next_evaluation_at"], base + datetime.timedelta(minutes=60))
                    else:
                        self.assertNotIn(key, runtime._proactive_idle_candidates)



class IdleReplyTimeoutTest(RuntimeAsyncHelpers, unittest.IsolatedAsyncioTestCase):
    now = datetime.datetime(2026, 5, 24, 12)
    proposal = (
        '{"should_reply":true,"expression_review":{"passed":true},"confidence":0.95,'
        '"benefit":90,"timeliness":90,"continuity":90,"disruption":10,"uncertainty":10,'
        '"decision":"reply","reply_text":"刚看到一张那个电影的幕后照","reason":"有新进展"}'
    )

    def make_runtime(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime.config.proactive.semantic_timeout_seconds = 1
        runtime._proactive_readiness_check = AsyncMock(return_value={"should_evaluate": True})
        runtime._build_proactive_prompt = AsyncMock(return_value="闲时回复裁定")
        runtime._read_recent_context_messages = AsyncMock(return_value=[])
        runtime.call_text_model = AsyncMock(return_value=self.proposal)
        runtime.close_text_session = AsyncMock()
        runtime._part_proactive_flow_component._record_proactive_decision = AsyncMock()
        runtime.composer._build_person_fact_context = AsyncMock()
        runtime.composer._audit_person_payload = AsyncMock(return_value=SimpleNamespace(
            unresolved=False, payload=json.loads(self.proposal),
        ))
        event = Event(
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m1",
        )
        event.message_str = "刚才的电影还挺值得聊聊"
        return runtime, event

    async def test_timeout_covers_preparation_model_and_both_audits(self):
        for stage in (
            "interaction", "readiness", "provider", "prompt", "model",
            "person_audit", "continuity_audit",
        ):
            with self.subTest(stage=stage):
                runtime, event = self.make_runtime()
                entered = asyncio.Event()
                cancelled = asyncio.Event()

                async def blocked(*args, **kwargs):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()

                if stage == "interaction":
                    runtime.resolve_interaction_context = blocked
                elif stage == "readiness":
                    runtime._proactive_readiness_check = blocked
                elif stage == "provider":
                    runtime._get_proactive_provider = blocked
                elif stage == "prompt":
                    runtime._build_proactive_prompt = blocked
                elif stage == "model":
                    runtime.call_text_model = blocked
                elif stage == "person_audit":
                    runtime.composer._audit_person_payload = blocked
                else:
                    runtime._read_recent_context_messages = AsyncMock(return_value=[
                        {"role": "user", "content": event.message_str},
                    ])
                    runtime._audit_proactive_continuity = blocked

                task = asyncio.create_task(runtime.evaluate_proactive_reply(event, now=self.now))
                await asyncio.wait_for(entered.wait(), 0.5)
                self.assertFalse(task.done())
                result = await asyncio.wait_for(task, 1.5)
                self.assertEqual(result["reason_code"], "evaluation_timeout")
                self.assertFalse(result["should_reply"])
                self.assertEqual(result["reply_text"], "")
                self.assertTrue(cancelled.is_set())
                self.assertEqual(runtime.context.sent_messages, [])
                if stage in {"prompt", "model", "person_audit", "continuity_audit"}:
                    runtime.close_text_session.assert_awaited_once()
                else:
                    runtime.close_text_session.assert_not_awaited()

    async def test_generation_and_audit_share_one_deadline(self):
        runtime, event = self.make_runtime()
        audit_entered = asyncio.Event()
        audit_cancelled = asyncio.Event()

        async def generate(*args, **kwargs):
            await asyncio.sleep(0.65)
            return self.proposal

        async def audit(*args, **kwargs):
            audit_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                audit_cancelled.set()

        runtime.call_text_model = generate
        runtime.composer._audit_person_payload = audit
        started = asyncio.get_running_loop().time()
        result = await runtime.evaluate_proactive_reply(event, now=self.now)
        elapsed = asyncio.get_running_loop().time() - started

        self.assertTrue(audit_entered.is_set())
        self.assertTrue(audit_cancelled.is_set())
        self.assertEqual(result["reason_code"], "evaluation_timeout")
        self.assertLess(elapsed, 1.5)
        runtime._part_proactive_flow_component._record_proactive_decision.assert_not_awaited()
        runtime.close_text_session.assert_awaited_once()

    async def test_timeout_abandons_candidate_without_sending_and_new_turn_can_succeed(self):
        runtime, event = self.make_runtime()

        async def blocked(*args, **kwargs):
            await asyncio.Event().wait()

        runtime.call_text_model = blocked
        runtime.note_proactive_activity(event, now=self.now)
        await runtime.evaluate_idle_proactive_candidates(
            now=self.now + datetime.timedelta(minutes=61),
        )
        scope = event.unified_msg_origin
        self.assertNotIn(scope, runtime._proactive_idle_candidates)
        self.assertEqual(runtime.context.sent_messages, [])
        state = runtime._proactive_lifecycle_snapshot(scope)
        self.assertEqual(state["state"], "abandoned")
        self.assertIn("超时", state["reason"])
        self.assertEqual(await runtime.archive.get_life_episodes(10), [])

        runtime.call_text_model = AsyncMock(return_value=self.proposal)
        result = await runtime.evaluate_proactive_reply(event, now=self.now)
        self.assertTrue(result["should_reply"])
        self.assertEqual(result["reply_text"], json.loads(self.proposal)["reply_text"])

    async def test_unload_cancellation_propagates_and_closes_session(self):
        runtime, event = self.make_runtime()
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        runtime.call_text_model = blocked
        task = asyncio.create_task(runtime.evaluate_proactive_reply(event, now=self.now))
        await asyncio.wait_for(entered.wait(), 0.5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        runtime.close_text_session.assert_awaited_once()

    async def test_stuck_cleanup_does_not_keep_timed_out_turn_alive(self):
        runtime, event = self.make_runtime()
        cancelled = asyncio.Event()

        async def blocked(*args, **kwargs):
            await asyncio.Event().wait()

        async def cleanup(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        runtime.call_text_model = blocked
        runtime.close_text_session = cleanup
        result = await asyncio.wait_for(
            runtime.evaluate_proactive_reply(event, now=self.now), 2.5,
        )
        self.assertEqual(result["reason_code"], "evaluation_timeout")
        self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
