import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.search.cache import SingleFlight, TimedCache


class TimedCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_expired_recent_entry_does_not_evict_live_older_entry(self):
        clock = Mock(return_value=0.0)
        with patch("core.search.cache.time", SimpleNamespace(monotonic=clock)):
            cache = TimedCache[str](max_items=2, ttl_seconds=10)
            await cache.set("expires_first", "旧结果")
            clock.return_value = 5.0
            await cache.set("live", "仍然有效")
            clock.return_value = 6.0
            self.assertEqual(await cache.get("expires_first"), "旧结果")
            clock.return_value = 11.0
            await cache.set("new", "新结果")

            self.assertIsNone(await cache.get("expires_first"))
            self.assertEqual(await cache.get("live"), "仍然有效")
            self.assertEqual(await cache.get("new"), "新结果")

    async def test_capacity_still_evicts_least_recent_valid_entry(self):
        cache = TimedCache[str](max_items=2, ttl_seconds=60)
        await cache.set("first", "甲")
        await cache.set("second", "乙")
        self.assertEqual(await cache.get("first"), "甲")
        await cache.set("third", "丙")
        self.assertIsNone(await cache.get("second"))
        self.assertEqual(await cache.get("first"), "甲")


class SingleFlightTest(unittest.IsolatedAsyncioTestCase):
    async def test_abandoned_failure_is_observed_and_next_request_can_retry(self):
        flight = SingleFlight[str]()
        entered = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()
        created = []

        async def fail():
            created.append(asyncio.current_task())
            entered.set()
            await release.wait()
            raise RuntimeError("测试失败")

        waiter = asyncio.create_task(flight.run("same", fail))
        await asyncio.wait_for(entered.wait(), timeout=1)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        created[0].add_done_callback(lambda _: completed.set())
        release.set()
        await asyncio.wait_for(completed.wait(), timeout=1)
        self.assertTrue(created[0].done())
        self.assertFalse(created[0]._log_traceback)

        async def succeed():
            return "已恢复"

        self.assertEqual(await flight.run("same", succeed), ("已恢复", False))
        await flight.close()
