# ruff: noqa: I001

import copy
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from runtimehelpers import (
    Context,
    DailyLifeRuntime,
    Event,
    LifeSettings,
    Provider,
    RuntimeAsyncHelpers,
)
from PIL import Image

from core.runtime.channel.animation import GifRequestError, parse_gif_request
from core.runtime.channel.artifact import image_delivery_context


def payload(**options):
    return {
        "visual_prompt": "橘猫在客厅连续奔跑，前爪着地、腾空、后爪落地。",
        "frame_count": None,
        "aspect_ratio": None,
        "frame_duration_ms": None,
        **options,
    }


class GifRequestTest(RuntimeAsyncHelpers, unittest.IsolatedAsyncioTestCase):
    def test_defaults_and_explicit_unlimited_count(self):
        default = parse_gif_request(payload(), "让橘猫跑起来")
        self.assertEqual(
            (default.frame_count, default.aspect_ratio, default.frame_duration_ms),
            (9, "1:1", 200),
        )
        explicit = parse_gif_request(
            payload(
                frame_count={"value": 320, "evidence": "三百二十帧"},
                aspect_ratio={"value": "16:9", "evidence": "十六比九"},
                frame_duration_ms={"value": 350, "evidence": "350毫秒"},
            ),
            "做三百二十帧，十六比九，每帧350毫秒",
        )
        self.assertEqual(
            (explicit.frame_count, explicit.aspect_ratio, explicit.frame_duration_ms),
            (320, "16:9", 350),
        )

    def test_invalid_or_invented_options_are_not_silently_submitted(self):
        invalid = [
            {},
            payload(frame_count={"value": 3, "evidence": "三帧"}),
            payload(frame_count={"value": True, "evidence": "原话"}),
            payload(frame_count={"value": 1, "evidence": "原话"}),
            payload(aspect_ratio={"value": "invented", "evidence": "原话"}),
        ]
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(GifRequestError):
                parse_gif_request(result, "原话")

    def runtime(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        self._stub_media_director(runtime)
        runtime._media_director_call = AsyncMock(return_value=payload())
        runtime.stage_durable_media_delivery = AsyncMock(return_value=None)
        runtime.finalize_durable_media_delivery = AsyncMock()
        return runtime

    async def test_text_and_edit_ignore_model_guesses_and_keep_file_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            gif = Path(folder) / "actual.gif"
            Image.new("RGB", (16, 24), "red").save(
                gif,
                save_all=True,
                append_images=[Image.new("RGB", (16, 24), "blue")],
                duration=200,
            )
            for mode in ("text", "edit"):
                with self.subTest(mode=mode):
                    runtime = self.runtime()
                    runtime._send_delivered_media_followup = AsyncMock(
                        return_value=True
                    )
                    service = types.SimpleNamespace(
                        generate_image=AsyncMock(
                            return_value=types.SimpleNamespace(path=gif)
                        ),
                        edit_image=AsyncMock(
                            return_value=types.SimpleNamespace(path=gif)
                        ),
                    )
                    runtime.media = types.SimpleNamespace(image=service)
                    event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
                    event.message_str = "用选定通道做一只橘猫跑动的GIF"
                    marker = {"status": "pending", "action": "generate"}
                    event._daily_life_image_request = marker
                    component = runtime._part_runtime_image_media_component
                    options = {
                        "output_format": "gif",
                        "frame_count": 3,
                        "frame_duration_ms": 350,
                    }
                    if mode == "text":

                        async def work():
                            return await component._life_image_generate_inline(
                                event, "9:16，三帧橘猫跑动的分镜", **options
                            )
                    else:
                        event._daily_life_locked_edit_reference = str(gif)

                        async def work():
                            return await component._edit_life_image_inline(
                                event, "9:16，三帧橘猫跑动的分镜", **options
                            )

                    await component._background_image_delivery(
                        event, work, marker, "life_image_generate"
                    )
                    self.assertEqual(marker["status"], "sent")
                    call = (
                        service.generate_image if mode == "text" else service.edit_image
                    )
                    call.assert_awaited_once()
                    self.assertEqual(
                        call.await_args.args[0], payload()["visual_prompt"]
                    )
                    self.assertEqual(call.await_args.kwargs["frame_count"], 9)
                    self.assertEqual(call.await_args.kwargs["frame_duration_ms"], 200)
                    self.assertEqual(call.await_args.kwargs["aspect_ratio"], "1:1")
                    if mode == "edit":
                        self.assertFalse(
                            call.await_args.kwargs["preserve_reference_ratio"]
                        )
                    followup = runtime._send_delivered_media_followup.await_args.kwargs
                    self.assertEqual(followup["media_name"], "GIF 动图")
                    facts = json.loads(followup["delivery_text"])
                    self.assertEqual(facts["artifact"]["frame_count"], 2)
                    self.assertEqual(facts["artifact"]["format"], "GIF")
                    self.assertFalse(facts["visual_content_verified"])
                    self.assertEqual(facts["recipient_playback"], "unknown")
                    durable = runtime.stage_durable_media_delivery.await_args.kwargs
                    self.assertEqual(
                        durable["reply_context"]["delivery_text"],
                        followup["delivery_text"],
                    )

    async def test_parse_failure_never_calls_image_service(self):
        runtime = self.runtime()
        runtime._media_director_call.return_value = {}
        generate = AsyncMock()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=generate)
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "生成动图"
        with self.assertRaises(GifRequestError):
            await (
                runtime._part_runtime_image_media_component._life_image_generate_inline(
                    event, "猫奔跑", output_format="gif", frame_count=3
                )
            )
        generate.assert_not_awaited()

    async def test_suite_persists_options_and_reuses_them_without_reinterpretation(
        self,
    ):
        runtime = self.runtime()
        with tempfile.TemporaryDirectory() as folder:
            runtime.data_path = Path(folder) / "daily_life.db"

            # Stop only execution; request preparation and persistence run normally.
            def schedule(coroutine, **kwargs):
                coroutine.close()
                return True

            runtime._schedule_background_task = schedule
            event = Event(
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                message_id="gif-suite",
            )
            event.message_str = "做一组橘猫跑动的GIF"
            await runtime.life_photo_suite_generate(
                event,
                "9:16 三帧跑动",
                output_format="gif",
                frame_count=3,
                frame_duration_ms=350,
            )
            paths = list(Path(folder).rglob("manifest.json"))
            self.assertEqual(len(paths), 1)
            manifest = json.loads(paths[0].read_text())
            self.assertEqual(
                (
                    manifest["frame_count"],
                    manifest["aspect_ratio"],
                    manifest["frame_duration_ms"],
                ),
                (9, "1:1", 200),
            )
            saved = copy.deepcopy(manifest)
            runtime.generate_life_image_asset = AsyncMock()
            await runtime._photo_suite_generate_asset(event, saved, "橘猫奔跑")
            call = runtime.generate_life_image_asset.await_args
            self.assertEqual(call.args[2], "1:1")
            self.assertEqual(call.kwargs["frame_count"], 9)
            runtime._media_director_call.assert_awaited_once()

    async def test_delivery_format_is_actual_content_not_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fake.gif"
            Image.new("RGB", (5, 7)).save(path, format="PNG")
            result = await image_delivery_context(
                path, request_text="", media_name="图片"
            )
            facts = json.loads(result["delivery_text"])["artifact"]
            self.assertEqual((facts["format"], facts["frame_count"]), ("PNG", 1))
            self.assertEqual(result["media_name"], "图片")

    async def test_static_bypasses_gif_interpretation_and_keeps_ratio(self):
        runtime = self.runtime()
        runtime._media_director_call.side_effect = AssertionError(
            "static must bypass GIF planning"
        )
        generate = AsyncMock(
            return_value=types.SimpleNamespace(path=Path("static.png"))
        )
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=generate)
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "竖版 9:16 橘猫静态照片"
        result = await runtime._part_runtime_image_media_component._life_image_generate_inline(
            event, "猫"
        )
        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate.await_args.kwargs["aspect_ratio"], "9:16")
        self.assertNotIn("frame_count", generate.await_args.kwargs)
