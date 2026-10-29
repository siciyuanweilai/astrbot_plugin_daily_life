"""聊天图片理解与表情素材识别分别调用，并保持各自的等待与缓存边界。"""

import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from core.sight.visual import image_understanding_prompt
from core.telemetry import cache_prefix_version
from PIL import Image
from support import (
    DailyLifeRuntime,
    DataManager,
    EmojiAssetRecord,
    Event,
    LifeSettings,
    Provider,
)

EMOJI_RESULT = {
    "summary": "素材侧描述，不用于聊天",
    "is_emoji_asset": True,
    "asset_type": "sticker",
    "label": "挥手",
    "description": "打招呼",
    "emotions": ["开心"],
    "sendable": True,
    "standalone_reaction": True,
    "context_dependent": False,
    "information_dominant": False,
    "confidence": 0.95,
    "status": "ready",
}


class VisionSplitTest(unittest.IsolatedAsyncioTestCase):
    def runtime(self, image, emoji=None, *, collect=True):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "vision_config": {"provider": "image"},
                "emoji_vision_config": {"provider": "emoji"},
                "emoji_config": {"collect_chat_emojis": collect},
            }
        )
        runtime.archive = DataManager()
        closed = []
        providers = {"image": image, "emoji": emoji or image}

        class Composer:
            async def _get_provider(self, provider_id=""):
                return providers.get(provider_id, image)

            async def _cleanup_conversation(self, session_id):
                closed.append(session_id)

        runtime.composer = Composer()
        return runtime, closed

    def prepared_event(self, runtime, count=1, question="看看这些图片"):
        event = Event(message_id="pictures")
        event.message_str = question
        entries = [
            {
                "item": {"type": "image"},
                "fingerprint": f"image-{index}",
                "path": f"https://example.test/{index}.png",
                "state": "ready",
            }
            for index in range(count)
        ]
        event.message_items = [entry["item"] for entry in entries]
        event.message_obj.message = event.message_items
        setattr(event, runtime._PREPARED_VISUAL_MEDIA_ATTR, entries)
        runtime._part_emoji_gather_component._prepared_visual_media_is_gif = AsyncMock(
            return_value=False
        )
        return event

    async def test_concurrent_identical_images_share_one_request_and_apply_to_each_message(self):
        runtime, _ = self.runtime(Provider(provider_id="image"))
        started, release = asyncio.Event(), asyncio.Event()

        async def recognize(*args, **kwargs):
            started.set()
            await release.wait()
            return {"summary": "桌上有一只红苹果"}

        runtime._call_vision_payload = AsyncMock(side_effect=recognize)
        runtime._apply_visual_context_summary = Mock()
        jobs = [asyncio.create_task(runtime._describe_visual_context_with_vision(
            "https://example.test/apple.png", f"scope-{index}", f"message-{index}",
            "same-image", user_text="这是什么",
        )) for index in range(24)]
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.sleep(0)
            self.assertEqual(runtime._call_vision_payload.await_count, 1)
        finally:
            release.set()
            await asyncio.gather(*jobs)
        self.assertEqual(runtime._apply_visual_context_summary.call_count, 24)
        self.assertEqual({call.args[0] for call in runtime._apply_visual_context_summary.call_args_list},
                         {f"scope-{index}" for index in range(24)})
        await runtime._describe_visual_context_with_vision(
            "https://example.test/apple.png", "later", "later", "same-image", user_text="这是什么",
        )
        self.assertEqual(runtime._call_vision_payload.await_count, 1)

    async def test_cancelling_one_waiter_preserves_shared_image_request(self):
        runtime, _ = self.runtime(Provider(provider_id="image"))
        started, release = asyncio.Event(), asyncio.Event()

        async def recognize(*args, **kwargs):
            started.set()
            await release.wait()
            return {"summary": "图片内容"}

        runtime._call_vision_payload = AsyncMock(side_effect=recognize)
        args = ("https://example.test/a.png", "scope", "message", "same-image")
        first = asyncio.create_task(runtime._describe_visual_context_with_vision(*args))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(runtime._describe_visual_context_with_vision(*args))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        await second
        self.assertEqual(runtime._call_vision_payload.await_count, 1)
        self.assertTrue(runtime._visual_context_summary_cache())

    async def test_failed_request_can_retry_and_shutdown_cancels_shared_work(self):
        runtime, _ = self.runtime(Provider(provider_id="image"))
        runtime._call_vision_payload = AsyncMock(side_effect=[RuntimeError("失败"), {"summary": "重试成功"}])
        args = ("https://example.test/a.png", "scope", "message", "image")
        await runtime._describe_visual_context_with_vision(*args)
        self.assertFalse(runtime._visual_context_summary_cache())
        await runtime._describe_visual_context_with_vision(*args)
        self.assertEqual(runtime._call_vision_payload.await_count, 2)

        started, cancelled = asyncio.Event(), asyncio.Event()

        async def recognize(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        runtime._call_vision_payload = AsyncMock(side_effect=recognize)
        pending = asyncio.create_task(runtime._describe_visual_context_with_vision(
            "https://example.test/b.png", "scope", "message", "other-image",
        ))
        await asyncio.wait_for(started.wait(), 1)
        await runtime._close_visual_context_flights()
        await asyncio.gather(pending, return_exceptions=True)
        self.assertTrue(cancelled.is_set())
        await runtime._describe_visual_context_with_vision(*args)
        self.assertEqual(runtime._call_vision_payload.await_count, 1)

    async def test_changing_actual_model_invalidates_visual_and_negative_emoji_results(self):
        image = Provider(['{"summary":"旧模型理解"}', '{"summary":"新模型理解"}'], provider_id="image")
        emoji = Provider(['{"is_emoji_asset":false}', '{"is_emoji_asset":false}'], provider_id="emoji")
        image.model_name = emoji.model_name = "first-model"
        runtime, _ = self.runtime(image, emoji)
        args = ("https://example.test/a.png", "scope", "message", "image")
        await runtime._describe_visual_context_with_vision(*args)
        image.model_name = "second-model"
        await runtime._describe_visual_context_with_vision(*args)
        self.assertEqual(len(image.vision_prompts), 2)
        with tempfile.TemporaryDirectory() as directory:
            runtime.data_path = Path(directory) / "life.db"
            path = Path(directory) / "photo.png"
            Image.new("RGB", (30, 30), "red").save(path)
            await runtime._classify_plain_image_emoji_candidate(str(path), "scope", "first", "photo")
            self.assertTrue(await runtime._plain_image_emoji_review_cached("photo"))
            emoji.model_name = "second-model"
            self.assertFalse(await runtime._plain_image_emoji_review_cached("photo"))
            await runtime._classify_plain_image_emoji_candidate(str(path), "scope", "second", "photo")
            self.assertEqual(len(emoji.vision_prompts), 2)

    async def test_negative_review_is_checked_before_queue_and_empty_capture_skips_contact_lookup(self):
        runtime, _ = self.runtime(Provider(provider_id="image"))
        runtime.contact_resolver = types.SimpleNamespace(resolve_event_sender=AsyncMock())
        event = self.prepared_event(runtime, count=0)
        await runtime.maybe_collect_emoji_assets_from_event(event)
        runtime.contact_resolver.resolve_event_sender.assert_not_awaited()

        event = self.prepared_event(runtime)
        runtime._plain_image_emoji_review_cached = AsyncMock(return_value=True)
        runtime._schedule_background_task = Mock()
        await runtime.maybe_collect_emoji_assets_from_event(event, sender_name="测试用户")
        runtime._plain_image_emoji_review_cached.assert_awaited_once_with("image-0")
        runtime._schedule_background_task.assert_not_called()

    async def test_question_changes_keep_the_fixed_prompt_prefix(self):
        runtime, _ = self.runtime(Provider(provider_id="image"))
        first = image_understanding_prompt(runtime.config.vision, "这是什么？")
        second = image_understanding_prompt(runtime.config.vision, "图片里有什么文字？")
        self.assertNotEqual(first, second)
        self.assertTrue(cache_prefix_version(first, has_dynamic_section=True))
        self.assertEqual(cache_prefix_version(first, has_dynamic_section=True),
                         cache_prefix_version(second, has_dynamic_section=True))

    async def test_question_reuses_visible_facts_and_reopens_image_when_details_are_missing(self):
        image = Provider([json.dumps({"summary": "桌上红苹果", "visible_facts": "白桌上有一只红苹果"}),
                          json.dumps({"summary": "苹果是红色", "needs_visual_review": False}),
                          json.dumps({"summary": "看不清细节", "needs_visual_review": True}),
                          json.dumps({"summary": "标签写着 A12", "visible_facts": "苹果标签 A12"})], provider_id="image")
        runtime, closed = self.runtime(image)
        args = ("https://example.test/apple.png", "scope", "message", "same-image")
        await runtime._describe_visual_context_with_vision(*args, user_text="这是什么")
        await runtime._describe_visual_context_with_vision(*args, user_text="什么颜色")
        self.assertEqual((len(image.prompts), len(image.vision_prompts)), (2, 1))
        await runtime._describe_visual_context_with_vision(*args, user_text="标签上写着什么")
        self.assertEqual((len(image.prompts), len(image.vision_prompts)), (4, 2))
        self.assertEqual(len(closed), 4)
        runtime.config.vision.detail_level = "detailed"
        image.responses.append('{"summary":"重新读取原图"}')
        await runtime._describe_visual_context_with_vision(*args, user_text="什么颜色")
        self.assertEqual(len(image.vision_prompts), 3)

    async def test_empty_or_failed_visual_facts_are_not_reused(self):
        image = Provider(['{"summary":"只有问题摘要"}', '{"summary":"再次读取"}'], provider_id="image")
        runtime, _ = self.runtime(image)
        args = ("https://example.test/apple.png", "scope", "message", "same-image")
        await runtime._describe_visual_context_with_vision(*args, user_text="第一问")
        await runtime._describe_visual_context_with_vision(*args, user_text="第二问")
        self.assertEqual(len(image.vision_prompts), 2)

    async def test_disabled_image_understanding_keeps_emoji_collection_independent(
        self,
    ):
        image = Provider(provider_id="image")
        emoji = Provider([json.dumps(EMOJI_RESULT)], provider_id="emoji")
        runtime, _ = self.runtime(image, emoji)
        runtime.config.vision.enabled = False
        jobs = []
        runtime._schedule_background_task = lambda coro, **kwargs: (
            jobs.append(coro) or True
        )
        with tempfile.TemporaryDirectory() as directory:
            runtime.data_path = Path(directory) / "life.db"
            event = self.prepared_event(runtime)
            path = Path(directory) / "reaction.png"
            Image.new("RGB", (30, 30), "red").save(path)
            getattr(event, runtime._PREPARED_VISUAL_MEDIA_ATTR)[0]["path"] = str(path)
            self.assertFalse(runtime.schedule_visual_context_from_event(event))
            request = types.SimpleNamespace(image_urls=["original.png"])
            self.assertFalse(
                await runtime.bridge_animated_visual_for_llm_request(event, request)
            )
            await (
                runtime._part_emoji_gather_component._collect_visual_context_background(
                    event
                )
            )
            await runtime.maybe_collect_emoji_assets_from_event(
                event, sender_name=event.get_sender_name()
            )
            for job in jobs:
                await job
            self.assertEqual(image.vision_prompts, [])
            self.assertEqual(len(emoji.vision_prompts), 1)
            self.assertEqual(request.image_urls, ["original.png"])

    async def test_per_message_image_cap_and_separate_emoji_cap(self):
        image = Provider(['{"summary":"图片内容"}'] * 5, provider_id="image")
        runtime, _ = self.runtime(image)
        runtime.config.vision.max_images_per_message = 2
        runtime.config.emoji.review_batch_size = 4
        jobs = []
        runtime._schedule_background_task = lambda coro, **kwargs: (
            jobs.append(coro) or True
        )
        runtime._classify_plain_image_emoji_candidate = AsyncMock()
        event = self.prepared_event(runtime, count=5)
        await runtime._part_emoji_gather_component._collect_visual_context_background(
            event
        )
        self.assertEqual(len(image.vision_prompts), 2)
        await runtime.maybe_collect_emoji_assets_from_event(
            event, sender_name=event.get_sender_name()
        )
        for job in jobs:
            await job
        self.assertEqual(runtime._classify_plain_image_emoji_candidate.await_count, 4)
        # 另一条消息使用自己的额度，同一问题和图片则命中缓存。
        await runtime._part_emoji_gather_component._collect_visual_context_background(
            event
        )
        self.assertEqual(len(image.vision_prompts), 2)
        another = self.prepared_event(runtime, count=3, question="图片中的文字是什么？")
        await runtime._part_emoji_gather_component._collect_visual_context_background(
            another
        )
        self.assertEqual(len(image.vision_prompts), 4)

    async def test_gif_bridge_does_not_bypass_cap_or_hide_unrecognized_images(self):
        image = Provider(['{"summary":"第一张动图内容"}'], provider_id="image")
        runtime, _ = self.runtime(image, collect=False)
        runtime.config.vision.max_images_per_message = 1
        event = self.prepared_event(runtime, count=3)
        runtime._part_emoji_gather_component._prepared_visual_media_is_gif.return_value = True
        request = types.SimpleNamespace(image_urls=[], extra_user_content_parts=[])
        self.assertTrue(
            await runtime.bridge_animated_visual_for_llm_request(event, request)
        )
        self.assertEqual(len(image.vision_prompts), 1)
        self.assertIn("第一张动图内容", request.extra_user_content_parts[-1].text)
        self.assertIn(
            "另有 2 张动图内容尚未确认", request.extra_user_content_parts[-1].text
        )

    async def test_question_and_settings_changes_do_not_reuse_previous_summary(self):
        image = Provider(['{"summary":"识别结果"}'] * 6, provider_id="image")
        runtime, _ = self.runtime(image, collect=False)
        event = self.prepared_event(runtime, question="墙上的字写了什么？")
        gather = runtime._part_emoji_gather_component
        await gather._collect_visual_context_background(event)
        await gather._collect_visual_context_background(event)
        self.assertEqual(len(image.vision_prompts), 1)
        self.assertIn(event.message_str, image.vision_prompts[0]["prompt"])
        event.message_str = "房间里有什么家具？"
        await gather._collect_visual_context_background(event)
        runtime.config.vision.detail_level = "detailed"
        await gather._collect_visual_context_background(event)
        runtime.config.vision.gif_sample_frames = 4
        await gather._collect_visual_context_background(event)
        runtime.config.vision.provider = "another"
        await gather._collect_visual_context_background(event)
        self.assertEqual(len(image.vision_prompts), 5)

    async def test_detail_modes_preserve_requested_detail_in_chat_context(self):
        for mode, limit in (("brief", 180), ("auto", 600), ("detailed", 1200)):
            with self.subTest(mode=mode):
                image = Provider(
                    [json.dumps({"summary": "画面细节" * 400})], provider_id="image"
                )
                runtime, _ = self.runtime(image, collect=False)
                runtime.config.vision.detail_level = mode
                event = self.prepared_event(runtime)
                runtime.note_structured_incoming_message(event)
                await runtime._part_emoji_gather_component._collect_visual_context_background(
                    event
                )
                key = await runtime._visual_context_cache_key("image-0", event.message_str)
                summary = runtime._visual_context_summary_cache()[key]
                self.assertEqual(len(summary), limit + 3)
                self.assertIn(f"不超过 {limit} 字", image.vision_prompts[0]["prompt"])
                context = runtime.format_structured_message_context(event)
                self.assertIn("画面细节" * (limit // 4 - 1), context)

    async def test_configured_gif_sampling_does_not_change_emoji_sampling(self):
        runtime, _ = self.runtime(Provider(['{"summary":"内容"}'] * 2))
        runtime.config.vision.gif_sample_frames = 4
        with patch(
            "core.runtime.capture.emoji.vision.prepare_animated_image",
            return_value=("image", ""),
        ) as prepare:
            for settings in (runtime.config.vision, runtime.config.emoji_vision):
                await runtime._call_vision_payload(
                    "识别图片",
                    "image",
                    session_prefix="test",
                    task_label="测试",
                    settings=settings,
                )
        self.assertEqual(
            [call.kwargs["sample_frames"] for call in prepare.call_args_list], [4, 8]
        )

    async def test_different_models_do_not_overwrite_chat_summary(self):
        image = Provider(['{"summary":"小猫向镜头挥爪"}'], provider_id="image")
        emoji = Provider([json.dumps(EMOJI_RESULT)], provider_id="emoji")
        runtime, closed = self.runtime(image, emoji)
        await runtime._describe_visual_context_with_vision(
            "https://example.test/cat.png", "scope", "message", "cat"
        )
        asset = await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="cat",
                file_path="https://example.test/cat.png",
                source_kind="trusted",
                status="pending",
            )
        )
        await runtime._describe_emoji_asset_with_vision(asset)
        self.assertEqual(
            runtime._visual_context_summary_cache()[
                await runtime._visual_context_cache_key("cat")
            ],
            "小猫向镜头挥爪",
        )
        self.assertEqual(len(image.vision_prompts), 1)
        self.assertEqual(len(emoji.vision_prompts), 1)
        self.assertNotIn("is_emoji_asset", image.vision_prompts[0]["prompt"])
        self.assertIn("is_emoji_asset", emoji.vision_prompts[0]["prompt"])
        self.assertEqual(len(closed), 2)
        self.assertEqual(
            (await runtime.archive.get_emoji_asset_by_hash("cat")).status, "ready"
        )

    async def test_same_model_still_makes_two_independent_requests(self):
        provider = Provider(
            ['{"summary":"猫在挥爪"}', json.dumps(EMOJI_RESULT)], provider_id="shared"
        )
        runtime, closed = self.runtime(provider)
        runtime.config.vision.provider = runtime.config.emoji_vision.provider = "image"
        await runtime._describe_visual_context_with_vision(
            "https://example.test/cat.png", "scope", "message", "cat"
        )
        await runtime._describe_emoji_asset_with_vision(
            EmojiAssetRecord(
                file_hash="cat",
                file_path="https://example.test/cat.png",
                source_kind="trusted",
                status="pending",
            )
        )
        self.assertEqual(len(provider.vision_prompts), 2)
        self.assertNotEqual(closed[0], closed[1])
        self.assertTrue(closed[0].startswith("daily_life_visual_"))
        self.assertTrue(closed[1].startswith("daily_life_emoji_vision_"))

    async def test_image_timeout_does_not_shorten_emoji_budget(self):
        class TimedProvider(Provider):
            async def text_chat(self, prompt, session_id=None, **kwargs):
                await asyncio.sleep(0.08)
                return types.SimpleNamespace(completion_text=json.dumps(EMOJI_RESULT))

        provider = TimedProvider(provider_id="shared")
        runtime, closed = self.runtime(provider)
        runtime.config.vision.timeout_seconds = 0.04
        runtime.config.emoji_vision.timeout_seconds = 0.5
        results = await asyncio.gather(
            runtime._call_vision_payload(
                "图片理解",
                "https://example.test/cat.png",
                session_prefix="image",
                task_label="图片",
                settings=runtime.config.vision,
            ),
            runtime._call_vision_payload(
                "表情识别",
                "https://example.test/cat.png",
                session_prefix="emoji",
                task_label="表情",
                settings=runtime.config.emoji_vision,
            ),
            return_exceptions=True,
        )
        self.assertIsInstance(results[0], TimeoutError)
        self.assertEqual(results[1]["label"], "挥手")
        self.assertEqual(len(closed), 2)

    async def test_gif_context_finishes_while_emoji_recognition_is_waiting(self):
        started, release = asyncio.Event(), asyncio.Event()

        class WaitingProvider(Provider):
            async def text_chat(self, **kwargs):
                started.set()
                await release.wait()
                return types.SimpleNamespace(completion_text=json.dumps(EMOJI_RESULT))

        runtime, _ = self.runtime(
            Provider(['{"summary":"小猫挥爪打招呼"}'], provider_id="image"),
            WaitingProvider(provider_id="emoji"),
        )
        tasks = []

        def schedule(coro, label="", key=""):
            tasks.append(asyncio.create_task(coro))
            return True

        runtime._schedule_background_task = schedule
        with tempfile.TemporaryDirectory() as directory:
            runtime.data_path = Path(directory) / "life.db"
            path = Path(directory) / "cat.gif"
            frames = [Image.new("RGB", (30, 30), color) for color in ("red", "blue")]
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
            event = Event(
                unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="gif"
            )
            event.message_items = [{"type": "image", "path": str(path)}]
            event.message_obj.message = event.message_items
            runtime.note_structured_incoming_message(event)
            try:
                self.assertTrue(runtime.schedule_visual_context_from_event(event))
                await runtime.maybe_collect_emoji_assets_from_event(
                    event, sender_name=event.get_sender_name()
                )
                await asyncio.wait_for(started.wait(), 1)
                await asyncio.wait_for(
                    asyncio.shield(getattr(event, runtime._GIF_CONTEXT_FUTURE_ATTR)), 1
                )
                await tasks[0]
                self.assertEqual(len(tasks), 2)
                self.assertFalse(tasks[1].done())
                self.assertIn(
                    "小猫挥爪打招呼", runtime.format_structured_message_context(event)
                )
                release.set()
                await tasks[1]
                self.assertIn(
                    "小猫挥爪打招呼", runtime.format_structured_message_context(event)
                )
                assets = await runtime.archive.get_emoji_assets(10, status="ready")
                self.assertEqual(assets[0].label, "挥手")
            finally:
                release.set()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def test_cached_image_is_still_classified_when_collection_is_enabled(self):
        image = Provider([], provider_id="image")
        emoji = Provider([json.dumps(EMOJI_RESULT)], provider_id="emoji")
        runtime, _ = self.runtime(image, emoji)
        runtime._visual_context_summaries = {
            await runtime._visual_context_cache_key("cat"): "已有图片描述"
        }
        scheduled = []
        runtime._schedule_background_task = lambda coro, **kwargs: (
            scheduled.append(coro) or True
        )
        with tempfile.TemporaryDirectory() as directory:
            runtime.data_path = Path(directory) / "life.db"
            path = Path(directory) / "cat.png"
            Image.new("RGB", (30, 30), "red").save(path)
            event = Event(message_id="message")
            entry = {"item": {"type": "image"}, "fingerprint": "cat", "path": str(path)}
            setattr(event, runtime._PREPARED_VISUAL_MEDIA_ATTR, [entry])
            await runtime._part_emoji_gather_component._collect_one_visual_context(
                entry, "scope", "message"
            )
            await runtime.maybe_collect_emoji_assets_from_event(
                event, sender_name=event.get_sender_name()
            )
            for job in scheduled:
                await job
            self.assertEqual(image.vision_prompts, [])
            self.assertEqual(len(emoji.vision_prompts), 1)
            self.assertEqual(
                runtime._visual_context_summary_cache()[
                    await runtime._visual_context_cache_key("cat")
                ],
                "已有图片描述",
            )

    async def test_repeated_photo_does_not_repeat_successful_emoji_review(self):
        emoji = Provider(['{"is_emoji_asset":false}'], provider_id="emoji")
        runtime, _ = self.runtime(Provider(provider_id="image"), emoji)
        with tempfile.TemporaryDirectory() as directory:
            runtime.data_path = Path(directory) / "life.db"
            path = Path(directory) / "photo.png"
            Image.new("RGB", (30, 30), "red").save(path)
            for message in ("first", "second"):
                await runtime._classify_plain_image_emoji_candidate(
                    str(path), "scope", message, "photo"
                )
            self.assertEqual(len(emoji.vision_prompts), 1)
            self.assertEqual(await runtime.archive.get_emoji_assets(10), [])

    async def test_sticker_content_is_understood_with_collection_disabled(self):
        image = Provider(['{"summary":"小猫挥爪打招呼"}'], provider_id="image")
        emoji = Provider(provider_id="emoji")
        runtime, _ = self.runtime(image, emoji, collect=False)
        scheduled = []
        runtime._schedule_background_task = lambda coro, **kwargs: (
            scheduled.append(coro) or True
        )
        with tempfile.TemporaryDirectory() as directory:
            runtime.data_path = Path(directory) / "life.db"
            path = Path(directory) / "cat.png"
            Image.new("RGB", (30, 30), "red").save(path)
            event = Event(
                unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="sticker"
            )
            event.message_items = [{"type": "mface", "path": str(path)}]
            event.message_obj.message = event.message_items
            runtime.note_structured_incoming_message(event)
            self.assertTrue(runtime.schedule_visual_context_from_event(event))
            for job in scheduled:
                await job
            self.assertEqual(len(image.vision_prompts), 1)
            self.assertEqual(emoji.vision_prompts, [])
            self.assertIn(
                "小猫挥爪打招呼", runtime.format_structured_message_context(event)
            )
