"""故障恢复保留事实、学习证据和取消语义，不发送真实消息。"""

import asyncio
import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from support import LifeSettings
from core.runtime.actionreply import ActionReplyService, ReviewValidationError, RECEIPTS_ATTR
from core.runtime.gateway import ModelCallOptions, ModelGateway
from core.runtime.proactive.breeze.effect import AirEffectComponent
import testactionreply as action_base
import testmemosresilience as memos_base


class ActionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        action_base.ActionReplyTest.setUp(self)

    async def test_upstream_timeout_is_not_relabelled_as_json_error(self):
        self.runtime.call_text_model.side_effect = TimeoutError("private-prompt")
        with self.assertRaises(TimeoutError):
            await self.service._review({})
        self.assertTrue(self.runtime.call_text_model.await_args.kwargs["propagate_failures"])
        self.runtime.close_text_session.assert_awaited_once()

    async def test_gateway_forwards_failure_contract(self):
        composer = SimpleNamespace(_call_llm_text=AsyncMock(side_effect=ConnectionError()))
        with self.assertRaises(ConnectionError):
            await ModelGateway(composer).call(object(), "synthetic", "test", ModelCallOptions(propagate_failures=True))
        self.assertTrue(composer._call_llm_text.await_args.kwargs["propagate_failures"])

    async def test_invalid_contract_never_executes_or_leaks_raw_text(self):
        for payload, code in [([], "invalid_decision"), ({"decision": "revise", "text": {"secret": "private"}}, "missing_revision_text"),
                              ({"decision": "act", "action": [], "detail": "private"}, "invalid_action_contract")]:
            if isinstance(payload, list):
                payload = {"decision": payload}
            self.event._daily_life_action_reply_done = False
            self.runtime.call_text_model.return_value = json.dumps(payload)
            with patch("core.runtime.actionreply.logger.warning") as log:
                await self.service.reconcile(self.event, self.response, self.execute)
                message = log.call_args.args[0]
                self.assertIn(code, message)
                self.assertNotIn("private", message)
            self.execute.assert_not_awaited()

    async def test_cleanup_failure_does_not_erase_a_valid_review(self):
        self.runtime.call_text_model.return_value = '{"decision":"keep"}'
        self.runtime.close_text_session.side_effect = RuntimeError("cleanup")
        self.assertEqual(await self.service._review({}), {"decision": "keep"})

    async def test_lookup_and_cleanup_stalls_are_bounded(self):
        cancelled = []
        async def stuck(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        self.service.review_timeout = self.service.cleanup_timeout = 0.03
        self.runtime.get_text_provider.side_effect = stuck
        self.runtime.close_text_session.side_effect = stuck
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.service._review({}), 0.5)
        self.assertEqual(len(cancelled), 2)
        self.runtime.call_text_model.assert_not_awaited()

    async def test_cancelled_review_does_not_execute_or_retry(self):
        self.runtime.call_text_model.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.service.reconcile(self.event, self.response, self.execute)
        await self.service.reconcile(self.event, self.response, self.execute)
        self.execute.assert_not_awaited()
        self.runtime.call_text_model.assert_awaited_once()

    async def test_failed_final_review_keeps_real_action_receipt(self):
        self.runtime.call_text_model.side_effect = [
            '{"decision":"act","action":"update_outfit","detail":"更换衣服"}', TimeoutError()]
        async def execute(event, **kwargs):
            setattr(event, RECEIPTS_ATTR, [{"status": "completed", "result": "换好了，穿的是蓝色外套。"}])
        self.execute.side_effect = execute
        await self.service.reconcile(self.event, self.response, self.execute)
        self.assertEqual(self.response.completion_text, "换好了，穿的是蓝色外套。")
        self.execute.assert_awaited_once()


class EffectRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = SimpleNamespace(meta=lambda: SimpleNamespace(id="one", model="test"))
        self.context = SimpleNamespace(config=LifeSettings.from_dict({}),
            _get_proactive_provider=AsyncMock(return_value=self.provider),
            _reply_effect_source_labels=lambda source: ("reply", "reply"),
            call_text_model=AsyncMock(side_effect=TimeoutError("private-token")), close_text_session=AsyncMock())
        self.effect = AirEffectComponent(self.context)
        self.event = SimpleNamespace(message_str="后续消息")

    async def assess(self):
        return await self.effect._classify_reply_effect({}, self.event)

    async def test_failures_back_off_without_learning_and_new_provider_can_recover(self):
        with patch("core.runtime.proactive.breeze.effect.logger.debug") as log:
            for _ in range(3):
                result = await self.assess()
                self.assertFalse(result["observed"])
                self.assertEqual(result["confidence"], 0)
            self.assertEqual(self.context.call_text_model.await_count, 2)
            self.assertIn("timeout=15s", log.call_args.args[0])
            self.assertNotIn("private-token", log.call_args.args[0])
        self.context._get_proactive_provider.return_value = SimpleNamespace(meta=lambda: SimpleNamespace(id="two", model="test"))
        self.context.call_text_model.side_effect = None
        self.context.call_text_model.return_value = '{"outcome":"positive","confidence":0.9}'
        self.assertEqual((await self.assess())["outcome"], "positive")
        self.context._get_proactive_provider.return_value = self.provider
        for key in self.effect._effect_circuit._opened_until:
            self.effect._effect_circuit._opened_until[key] = 0.01
        self.assertTrue((await self.assess())["observed"])
        self.assertFalse(self.effect._effect_circuit._failures)

    async def test_cancellation_is_not_a_failed_observation(self):
        self.context.call_text_model.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.assess()
        self.assertFalse(self.effect._effect_circuit._failures)

    async def test_cleanup_does_not_mask_success_and_stays_bounded(self):
        self.effect._EFFECT_CLEANUP_SECONDS = 0.03
        self.context.call_text_model.side_effect = None
        self.context.call_text_model.return_value = '{"outcome":"neutral","confidence":0.8}'
        async def stuck(*args):
            await asyncio.Event().wait()
        self.context.close_text_session.side_effect = stuck
        self.assertTrue((await asyncio.wait_for(self.assess(), 0.5))["observed"])

    async def test_invalid_confidence_cannot_create_positive_learning(self):
        self.context.call_text_model.side_effect = None
        for value in [float("nan"), float("inf"), True, "invalid"]:
            self.context.call_text_model.return_value = json.dumps({"outcome": "positive", "confidence": value})
            self.assertEqual((await self.assess())["confidence"], 0)

    async def test_total_deadline_includes_provider_lookup(self):
        self.effect._EFFECT_TIMEOUT_SECONDS = 0.03
        async def stuck():
            await asyncio.Event().wait()
        self.context._get_proactive_provider.side_effect = stuck
        self.assertFalse((await asyncio.wait_for(self.assess(), 0.5))["observed"])
        self.context.call_text_model.assert_not_awaited()

    async def test_unobserved_feedback_keeps_watch_and_does_not_mutate_learning(self):
        watch = {"sent_at": datetime.datetime(2026, 11, 3, 12), "expression_profiles": []}
        watches = {"scope": watch}
        self.context._proactive_scope_key = lambda event: "scope"
        self.context._proactive_is_self_message = lambda event: False
        self.context._event_has_command_handler = lambda event: False
        self.context._proactive_feedback_watches = lambda: watches
        self.context._AIR_FEEDBACK_WINDOW_SECONDS = 1800
        self.context._safe_event_call = lambda *args: "user"
        self.context.archive = SimpleNamespace()
        self.effect._proactive_reply_was_accepted = lambda *args: True
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
            return {"observed": False}
        self.effect._classify_reply_effect = AsyncMock(side_effect=slow)
        self.effect._update_reply_effect_watch = AsyncMock()
        with patch("core.runtime.proactive.breeze.effect.settle_expression_feedback", new_callable=AsyncMock) as settle:
            task = asyncio.create_task(self.effect._observe_proactive_reply_effect(self.event, datetime.datetime(2026, 11, 3, 12, 1)))
            await entered.wait()
            await self.effect._observe_proactive_reply_effect(self.event, datetime.datetime(2026, 11, 3, 12, 2))
            release.set()
            await task
            settle.assert_not_awaited()
        self.assertIs(watches["scope"], watch)
        self.effect._classify_reply_effect.assert_awaited_once()
        self.effect._update_reply_effect_watch.assert_not_awaited()
        self.assertFalse(self.effect._effect_observations)

    async def test_feedback_in_progress_does_not_repeat_assessment_or_learning(self):
        now = datetime.datetime(2026, 11, 3, 12, 1)
        watch = {"sent_at": now - datetime.timedelta(seconds=60), "source": "regular_reply", "expression_profiles": []}
        watches = {"scope": watch}
        self.context._proactive_scope_key = lambda event: "scope"
        self.context._proactive_is_self_message = lambda event: False
        self.context._event_has_command_handler = lambda event: False
        self.context._proactive_feedback_watches = lambda: watches
        self.context._AIR_FEEDBACK_WINDOW_SECONDS = 1800
        self.context._safe_event_call = lambda *args: "user"
        self.context._event_message_id = lambda event: "message"
        self.context._get_proactive_air_state = lambda key: {}
        self.context.archive = SimpleNamespace()
        self.effect._proactive_reply_was_accepted = lambda *args: True
        self.effect._proactive_continuation_window = lambda *args: (0, 0, "")
        self.effect._classify_reply_effect = AsyncMock(return_value={
            "observed": True, "outcome": "neutral", "confidence": 0.8,
            "reason": "自然结束", "warmth": 50, "continuity": 50, "friction": 0})
        self.effect._update_reply_effect_watch = AsyncMock()
        self.effect._learn_repeated_reply_effect = AsyncMock()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed_settlement(*args):
            entered.set()
            await release.wait()
        with patch("core.runtime.proactive.breeze.effect.settle_expression_feedback", new_callable=AsyncMock) as settle:
            settle.side_effect = delayed_settlement
            task = asyncio.create_task(self.effect._observe_proactive_reply_effect(self.event, now))
            try:
                await asyncio.wait_for(entered.wait(), 0.5)
                await asyncio.wait_for(self.effect._observe_proactive_reply_effect(self.event, now), 0.5)
            finally:
                release.set()
                await task
            settle.assert_awaited_once()
        self.effect._classify_reply_effect.assert_awaited_once()
        self.effect._update_reply_effect_watch.assert_awaited_once()
        self.effect._learn_repeated_reply_effect.assert_awaited_once()
        self.assertFalse(self.effect._effect_observations)
        self.assertFalse(watches)


class MemOSQueueRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_client_error_is_contained_and_backed_off(self):
        service = memos_base.MemOSSubmissionTests().service()
        service.client.add_message.side_effect = RuntimeError("private-payload")
        self.assertFalse(await memos_base.MemOSSubmissionTests().submit(service))
        self.assertFalse(await memos_base.MemOSSubmissionTests().submit(service))
        self.assertNotIn("private", next(iter(service._recent_writes.values()))[1].error)
        service.client.add_message.assert_awaited_once()
        await service.close()

    async def test_pending_queue_is_bounded_and_shutdown_drains_it(self):
        service = memos_base.MemOSSubmissionTests().service()
        async def slow(payload):
            await asyncio.Event().wait()
        service.client.add_message.side_effect = slow
        tasks = [asyncio.create_task(memos_base.MemOSSubmissionTests().submit(service, user=str(i))) for i in range(40)]
        for _ in range(4):
            await asyncio.sleep(0)
        self.assertEqual(len(service._pending_writes), 32)
        self.assertEqual(sum(task.done() for task in tasks), 8)
        await service.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assertFalse(service._pending_writes)
