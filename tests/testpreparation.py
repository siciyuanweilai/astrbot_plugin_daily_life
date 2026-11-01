import asyncio
import types
import unittest
from unittest.mock import AsyncMock

from support import Event, LifeSettings
from core.life.planner import LifeBackgroundComposer
from core.life.reliability import ProviderCircuit
from core.runtime.background import BackgroundTaskScheduler
from core.runtime.preparation import ReplyPreparationService
from core.telemetry import ModelCacheMetrics


class ReplyPreparationTest(unittest.IsolatedAsyncioTestCase):
    def make_service(self, prepare, *, current=lambda event: True):
        self.scheduler = BackgroundTaskScheduler(reply_limit=1, reply_backlog_limit=0)
        self.delivered = AsyncMock()
        self.completed = []
        return ReplyPreparationService(
            schedule=self.scheduler.schedule, prepare=prepare,
            delivered=self.delivered, is_current=current, complete=self.completed.append,
        )

    def event(self):
        event = Event()
        event.set_result(event.chain_result([types.SimpleNamespace(text="晚安啦")]))
        return event

    async def test_slow_preparation_releases_pipeline_and_sends_once(self):
        release = asyncio.Event()
        entered = asyncio.Event()

        async def prepare(event):
            entered.set()
            await release.wait()
            event.get_result().chain[0].text = "轻声说晚安"

        service = self.make_service(prepare)
        event = self.event()
        self.assertTrue(service.defer(event))
        self.assertTrue(service.defer(event))
        self.assertIsNone(event.get_result())
        await asyncio.wait_for(entered.wait(), 0.2)
        self.assertFalse(event.sent_messages)
        release.set()
        await asyncio.gather(*self.scheduler.tasks)
        self.assertEqual(len(event.sent_messages), 1)
        self.assertEqual(event.sent_messages[0].chain[0].text, "轻声说晚安")
        self.delivered.assert_awaited_once()
        self.assertEqual(len(self.completed), 1)

    async def test_new_turn_or_recall_suppresses_late_result(self):
        for reason in ("new_turn", "recall"):
            with self.subTest(reason=reason):
                release = asyncio.Event()
                state = {"current": True}

                async def prepare(event):
                    await release.wait()

                service = self.make_service(prepare, current=lambda event: state["current"])
                event = self.event()
                service.defer(event)
                await asyncio.sleep(0)
                state["current"] = False
                release.set()
                await asyncio.gather(*self.scheduler.tasks)
                self.assertFalse(event.sent_messages)
                self.delivered.assert_not_awaited()

    async def test_capacity_keeps_original_for_framework_and_unload_cancels_job(self):
        service = self.make_service(AsyncMock(side_effect=lambda event: None))
        first, second = self.event(), self.event()
        self.assertTrue(service.defer(first))
        self.assertFalse(service.defer(second))
        self.assertEqual(second.get_result().chain[0].text, "晚安啦")
        await self.scheduler.cancel_all()
        self.assertFalse(first.sent_messages)

    async def test_cancellation_without_receipt_does_not_complete_the_turn(self):
        entered = asyncio.Event()

        async def prepare(event):
            entered.set()
            await asyncio.Event().wait()

        service = self.make_service(prepare)
        event = self.event()
        service.defer(event)
        await asyncio.wait_for(entered.wait(), 0.2)
        await self.scheduler.cancel_all()
        self.assertEqual(len(self.completed), 0)
        self.assertFalse(event.sent_messages)
        self.delivered.assert_not_awaited()

    async def test_outer_timeout_records_request_and_propagates_cancellation(self):
        async def slow(*args, **kwargs):
            await asyncio.Event().wait()

        composer = LifeBackgroundComposer.__new__(LifeBackgroundComposer)
        composer.config = LifeSettings.from_dict({})
        composer._provider_circuit = ProviderCircuit()
        composer.model_cache_metrics = ModelCacheMetrics()
        provider = types.SimpleNamespace(meta=lambda: types.SimpleNamespace(id="test"), text_chat=AsyncMock(side_effect=slow))
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(0.01):
                await composer._call_llm_text(provider, "测试", "test", empty_retries=0)
        rows = composer.model_cache_metrics.snapshot()["models"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["requests"], 1)
        self.assertEqual(rows[0]["cancelled_requests"], 1)
        provider.text_chat.assert_awaited_once()
