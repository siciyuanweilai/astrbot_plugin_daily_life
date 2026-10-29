"""混合图片消息中的 GIF 摘要完成后即可继续本轮聊天。"""

import asyncio
import types
import unittest
from unittest.mock import AsyncMock

from support import DailyLifeRuntime, LifeSettings, Provider


class GifBridgeTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def runtime():
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        provider = Provider([], provider_id="vision-model")

        async def candidates(_provider_id=""):
            yield provider

        runtime.get_text_provider_candidates = candidates
        return runtime

    async def test_cached_gif_does_not_wait_for_pending_collection(self):
        runtime = self.runtime()
        runtime._part_emoji_gather_component._prepared_visual_media_from_event = AsyncMock(
            return_value=[{"fingerprint": "gif"}]
        )
        runtime._part_emoji_gather_component._prepared_visual_media_is_gif = AsyncMock(return_value=True)
        cache_key = await runtime._visual_context_cache_key("gif")
        runtime._visual_context_summary_cache = lambda: {cache_key: "已有动图描述"}
        runtime._part_emoji_gather_component._remove_gif_inputs_from_provider_request = AsyncMock(return_value=1)
        runtime._append_gif_visual_summary = (
            lambda req, summaries: setattr(req, "summaries", summaries)
        )
        runtime._part_emoji_gather_component._wait_for_gif_visual_context = AsyncMock(
            side_effect=AssertionError("已有摘要不应等待采集")
        )
        event, req = types.SimpleNamespace(), types.SimpleNamespace()
        self.assertTrue(await runtime.bridge_animated_visual_for_llm_request(event, req))
        self.assertEqual(req.summaries, ["已有动图描述"])
        runtime._part_emoji_gather_component._wait_for_gif_visual_context.assert_not_awaited()

    async def test_unscheduled_gif_collection_has_timeout(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._GIF_VISION_BRIDGE_TIMEOUT_SECONDS = 0.01
        cancelled = asyncio.Event()

        async def blocked(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        runtime._part_emoji_gather_component._collect_visual_context_background = blocked
        await asyncio.wait_for(
            runtime._part_emoji_gather_component._wait_for_gif_visual_context(types.SimpleNamespace()), 0.2
        )
        self.assertTrue(cancelled.is_set())

    async def test_static_image_analysis_does_not_delay_ready_gif(self):
        runtime = self.runtime()
        runtime.event_was_recalled = lambda event, log_skip=False: False
        runtime._event_session_id = lambda event: "scope"
        runtime._event_message_id = lambda event: "message"
        entries = [
            {"fingerprint": "static", "gif": False},
            {"fingerprint": "gif", "gif": True},
        ]

        async def prepared(event):
            return entries

        async def is_gif(entry):
            return entry["gif"]

        cache = {}
        static_started = asyncio.Event()
        release_static = asyncio.Event()

        async def collect_one(entry, scope, message_key, *, user_text=""):
            if entry["gif"]:
                cache[await runtime._visual_context_cache_key("gif", user_text)] = "动图内容"
            else:
                static_started.set()
                await release_static.wait()

        async def remove_gif(req, **kwargs):
            return 1

        runtime._part_emoji_gather_component._prepared_visual_media_from_event = prepared
        runtime._part_emoji_gather_component._prepared_visual_media_is_gif = is_gif
        runtime._visual_context_summary_cache = lambda: cache
        runtime._part_emoji_gather_component._collect_one_visual_context = collect_one
        runtime._part_emoji_gather_component._remove_gif_inputs_from_provider_request = remove_gif
        runtime._append_gif_visual_summary = (
            lambda req, summaries: setattr(req, "summaries", summaries)
        )
        event = types.SimpleNamespace()
        req = types.SimpleNamespace()
        loop = asyncio.get_running_loop()
        full = loop.create_future()
        gif = loop.create_future()
        setattr(event, runtime._VISUAL_CONTEXT_FUTURE_ATTR, full)
        setattr(event, runtime._GIF_CONTEXT_FUTURE_ATTR, gif)
        task = asyncio.create_task(runtime._part_emoji_gather_component._run_visual_context_task(event, full))
        try:
            self.assertTrue(
                await asyncio.wait_for(
                    runtime.bridge_animated_visual_for_llm_request(event, req), 1
                )
            )
            await asyncio.wait_for(static_started.wait(), 1)
            self.assertFalse(full.done())
            self.assertEqual(req.summaries, ["动图内容"])
        finally:
            release_static.set()
            await asyncio.wait_for(task, 1)
