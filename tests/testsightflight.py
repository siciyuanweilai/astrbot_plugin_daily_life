from __future__ import annotations

import asyncio
import unittest

import support  # noqa: F401  # 将插件目录加入模块搜索路径
from core.sight.clip import SightClip
from core.sight.flight import SightFlight, sight_prepare_key


class SightFlightTest(unittest.IsolatedAsyncioTestCase):
    async def test_flight_shares_one_factory(self):
        flight = SightFlight()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"prepared": True}

        first = asyncio.create_task(flight.run("same-media", factory))
        await started.wait()
        second = asyncio.create_task(flight.run("same-media", factory))
        release.set()
        self.assertEqual(await first, {"prepared": True})
        self.assertEqual(await second, {"prepared": True})
        self.assertEqual(calls, 1)
        await flight.close()

    def test_prepare_key_is_independent_of_conversation_scope(self):
        first = SightClip(source="https://example.com/video.mp4", scope="private:1")
        second = SightClip(source="https://example.com/video.mp4", scope="private:2")
        self.assertEqual(sight_prepare_key(first), sight_prepare_key(second))
