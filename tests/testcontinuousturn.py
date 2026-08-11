import asyncio
import time
import unittest

from support import DailyLifeRuntime, Event, LifeSettings, ProviderRequest


class ContinuousTurnTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _runtime(**overrides):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "continuous_turn_wait_seconds": 0.05,
                    "continuous_turn_max_wait_seconds": 0.3,
                    **overrides,
                }
            }
        )
        runtime._init_continuous_turn_state()
        return runtime

    @staticmethod
    def _event(text, message_id):
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            sender_id="10001",
            message_id=message_id,
        )
        event.message_str = text
        return event

    async def test_continuous_private_messages_are_merged_into_latest_event(self):
        runtime = self._runtime()
        first = self._event("明天下雨", "m-first")
        second = self._event("记得带伞出门", "m-second")

        self.assertTrue(runtime.note_continuous_turn_incoming(first))
        first_settle = asyncio.create_task(runtime.settle_continuous_turn(first))
        await asyncio.sleep(0.01)
        self.assertTrue(runtime.note_continuous_turn_incoming(second))

        self.assertFalse(await first_settle)
        self.assertTrue(first.is_stopped())
        self.assertTrue(await runtime.settle_continuous_turn(second))
        self.assertEqual(
            runtime.continuous_turn_messages(second),
            ("明天下雨", "记得带伞出门"),
        )
        self.assertGreaterEqual(
            runtime.continuous_turn_intentional_wait_seconds(second), 0.04
        )

        request = ProviderRequest(prompt=second.message_str)
        self.assertTrue(runtime.prepare_continuous_turn_llm_request(second, request))
        self.assertEqual(request.prompt, "明天下雨\n记得带伞出门")
        self.assertIn("同一个话轮", request.system_prompt)

    async def test_new_message_joins_an_event_that_started_generating(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        first = self._event("第一条", "m-first")
        second = self._event("补充一条", "m-second")

        runtime.note_continuous_turn_incoming(first)
        self.assertTrue(await runtime.settle_continuous_turn(first))
        request = ProviderRequest(prompt=first.message_str)
        self.assertTrue(runtime.prepare_continuous_turn_llm_request(first, request))
        runtime.note_continuous_turn_incoming(second)

        self.assertTrue(runtime.continuous_turn_event_is_current(first))
        self.assertTrue(runtime.continuous_turn_event_is_inflight_follow_up(second))
        self.assertTrue(await runtime.settle_continuous_turn(second))
        self.assertFalse(first.is_stopped())
        self.assertTrue(runtime.prepare_continuous_turn_llm_request(first, request))

    async def test_inflight_follow_up_bypasses_a_second_response_gate_decision(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        runtime._init_response_gate_state()
        first = self._event("先拍一张", "m-first")
        second = self._event("拍套图的", "m-second")

        runtime.note_continuous_turn_incoming(first)
        self.assertTrue(await runtime.settle_continuous_turn(first))
        request = ProviderRequest(prompt=first.message_str)
        self.assertTrue(runtime.prepare_continuous_turn_llm_request(first, request))
        runtime.note_continuous_turn_incoming(second)
        self.assertTrue(await runtime.settle_continuous_turn(second))

        decision = await runtime.evaluate_response_gate(second)

        self.assertEqual(decision["action"], "reply")
        self.assertTrue(decision["forced"])
        self.assertIn("接续正在生成", decision["reason"])

    async def test_completed_turn_does_not_leak_into_the_next_turn(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        first = self._event("第一轮", "m-first")
        second = self._event("第二轮", "m-second")

        runtime.note_continuous_turn_incoming(first)
        self.assertTrue(await runtime.settle_continuous_turn(first))
        self.assertTrue(runtime.complete_continuous_turn(first))
        runtime.note_continuous_turn_incoming(second)
        self.assertTrue(await runtime.settle_continuous_turn(second))

        self.assertEqual(runtime.continuous_turn_messages(second), ("第二轮",))

    async def test_semantic_wait_never_exceeds_the_turn_deadline(self):
        runtime = self._runtime(
            continuous_turn_wait_seconds=0,
        )
        runtime.config.chat_style.continuous_turn_max_wait_seconds = 0.08
        event = self._event("我可能还会继续说", "m-first")
        runtime.note_continuous_turn_incoming(event)
        self.assertTrue(await runtime.settle_continuous_turn(event))

        started = time.monotonic()
        self.assertEqual(
            await runtime.wait_continuous_turn_after_semantic(event), "reply"
        )
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.04)
        self.assertLess(elapsed, 0.2)
        self.assertGreaterEqual(
            runtime.continuous_turn_intentional_wait_seconds(event), 0.04
        )

    async def test_response_gate_wait_becomes_one_reply_at_the_deadline(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        runtime.config.chat_style.continuous_turn_max_wait_seconds = 0.06
        runtime._init_response_gate_state()
        event = self._event("先等我补充", "m-first")
        runtime.note_continuous_turn_incoming(event)
        self.assertTrue(await runtime.settle_continuous_turn(event))

        async def evaluate(_event):
            return {"action": "wait", "confidence": 0.9, "reason": "像是还没说完"}

        runtime.evaluate_response_gate = evaluate
        runtime.note_conversation_turn_decision = lambda *_args: None
        started = time.monotonic()
        decision = await runtime.apply_response_gate_for_event(event)

        self.assertEqual(decision["action"], "reply")
        self.assertTrue(decision["continuous_turn_waited"])
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertFalse(event.call_llm)

    async def test_disabling_semantic_wait_does_not_restore_legacy_long_wait(self):
        runtime = self._runtime(
            continuous_turn_wait_seconds=0,
            continuous_turn_semantic_enabled=False,
        )
        runtime._init_response_gate_state()
        event = self._event("这一条按时间收束", "m-first")
        runtime.note_continuous_turn_incoming(event)
        self.assertTrue(await runtime.settle_continuous_turn(event))

        async def evaluate(_event):
            return {"action": "wait", "confidence": 0.9, "reason": "模型建议等待"}

        runtime.evaluate_response_gate = evaluate
        runtime.note_conversation_turn_decision = lambda *_args: None
        started = time.monotonic()
        decision = await runtime.apply_response_gate_for_event(event)

        self.assertEqual(decision["action"], "reply")
        self.assertLess(time.monotonic() - started, 0.05)

    async def test_superseded_semantic_decision_does_not_record_a_reply(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        runtime._init_response_gate_state()
        first = self._event("第一条", "m-first")
        second = self._event("补充一条", "m-second")
        runtime.note_continuous_turn_incoming(first)
        self.assertTrue(await runtime.settle_continuous_turn(first))

        async def semantic(*_args, **_kwargs):
            runtime.note_continuous_turn_incoming(second)
            return {"action": "reply", "confidence": 0.9, "reason": "可以回复"}

        runtime._response_gate_semantic_decision = semantic
        decision = await runtime.evaluate_response_gate(first)

        self.assertTrue(decision["superseded"])
        self.assertTrue(first.is_stopped())
        self.assertEqual(runtime._response_gate_last_reply_at, {})

    async def test_disabled_continuous_turn_keeps_independent_processing(self):
        runtime = self._runtime(continuous_turn_enabled=False)
        event = self._event("普通消息", "m-first")

        self.assertFalse(runtime.note_continuous_turn_incoming(event))
        self.assertTrue(await runtime.settle_continuous_turn(event))
        self.assertEqual(runtime.continuous_turn_messages(event), ())

    async def test_group_collection_is_opt_in(self):
        runtime = self._runtime()
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:test-group",
            group_id="test-group",
            sender_id="10001",
            message_id="m-group",
        )
        event.message_str = "群聊消息"

        self.assertFalse(runtime.note_continuous_turn_incoming(event))
