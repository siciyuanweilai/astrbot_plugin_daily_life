from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import support  # noqa: F401  # 将插件目录加入模块搜索路径
from core.security import is_public_http_url, is_public_http_url_async
from core.sight.clip import SightClip
from core.sight.flight import SightFlight, sight_prepare_key


class ExternalInputSafetyTest(unittest.IsolatedAsyncioTestCase):
    def test_static_public_url_guard(self):
        self.assertTrue(is_public_http_url("https://example.com/image.png"))
        self.assertFalse(is_public_http_url("http://127.0.0.1/image.png"))
        self.assertFalse(is_public_http_url("http://169.254.169.254/latest/meta-data"))
        self.assertFalse(is_public_http_url("file:///etc/passwd"))
        self.assertFalse(is_public_http_url("https://user:pass@example.com/a"))

    async def test_async_guard_keeps_public_host(self):
        with patch(
            "core.security.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ):
            self.assertTrue(
                await is_public_http_url_async("https://example.com/image.png")
            )

    async def test_async_guard_rejects_empty_dns_result(self):
        with patch("core.security.socket.getaddrinfo", return_value=[]):
            self.assertFalse(
                await is_public_http_url_async("https://example.com/image.png")
            )

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
