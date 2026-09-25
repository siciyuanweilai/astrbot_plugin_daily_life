import asyncio
import time
import types
import unittest
from unittest.mock import Mock

from support import DailyLifeRuntime, Event, LifeSettings, ProviderRequest


class FrameworkStopEvent(Event):
    """Match AstrBot's stop_event behavior when no result exists yet."""

    def stop_event(self):
        super().stop_event()
        if self.get_result() is None:
            self.set_result(self.chain_result([]))


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
    def _event(text, message_id, event_type=Event):
        event = event_type(
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            sender_id="10001",
            message_id=message_id,
        )
        event.message_str = text
        return event

    async def test_continuous_private_messages_are_merged_into_latest_event(self):
        runtime = self._runtime()
        first = self._event("明天下雨", "m-first", FrameworkStopEvent)
        second = self._event("记得带伞出门", "m-second")

        self.assertTrue(runtime.note_continuous_turn_incoming(first))
        first_settle = asyncio.create_task(runtime.settle_continuous_turn(first))
        await asyncio.sleep(0.01)
        self.assertTrue(runtime.note_continuous_turn_incoming(second))

        self.assertFalse(await first_settle)
        self.assertTrue(first.is_stopped())
        self.assertTrue(first.call_llm)
        self.assertIsNone(first.get_result())
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

    @classmethod
    def _image_event(cls, message_id, *sources, text=""):
        event = cls._event(text, message_id)
        event.message_items.extend(
            {"type": "image", "url": source} for source in sources
        )
        return event

    async def test_text_then_image_replaces_waiting_text_with_complete_turn(self):
        runtime = self._runtime()
        first = self._event("帮我看看这张图", "text", FrameworkStopEvent)
        second = self._image_event("image", "https://example.com/photo.png")
        runtime.note_continuous_turn_incoming(first)
        waiting = asyncio.create_task(runtime.settle_continuous_turn(first))
        await asyncio.sleep(0)

        self.assertTrue(runtime.note_continuous_turn_incoming(second))
        self.assertTrue(await runtime.settle_continuous_turn(second))
        self.assertFalse(await waiting)
        self.assertTrue(first.is_stopped())
        self.assertIsNone(first.get_result())
        self.assertEqual(runtime.continuous_turn_message_count(second), 2)
        request = ProviderRequest(
            prompt="", image_urls=["https://example.com/photo.png"]
        )
        self.assertTrue(runtime.prepare_continuous_turn_llm_request(second, request))
        self.assertEqual(request.prompt, "帮我看看这张图\n[图片]")
        self.assertEqual(request.image_urls, ["https://example.com/photo.png"])
        self.assertEqual(len(second.get_messages()), 1)

    async def test_images_survive_later_text_and_more_images_in_arrival_order(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        first = self._image_event("first", "https://example.com/first.png")
        middle = self._event("比较这三张图片", "text")
        last = self._image_event(
            "last",
            "https://example.com/second.png",
            "https://example.com/third.png",
            text="这是后两张",
        )
        runtime.note_continuous_turn_incoming(first)
        self.assertTrue(await runtime.settle_continuous_turn(first))
        runtime.note_continuous_turn_incoming(middle)
        self.assertTrue(await runtime.settle_continuous_turn(middle))
        self.assertEqual(middle.get_messages(), first.get_messages())
        runtime.note_continuous_turn_incoming(last)
        self.assertTrue(await runtime.settle_continuous_turn(last))
        self.assertFalse(
            runtime.prepare_continuous_turn_llm_request(middle, ProviderRequest())
        )
        self.assertEqual(
            [item["url"] for item in last.get_messages()],
            [
                f"https://example.com/{name}.png"
                for name in ("first", "second", "third")
            ],
        )
        request = ProviderRequest(prompt=last.message_str)
        runtime.prepare_continuous_turn_llm_request(last, request)
        self.assertEqual(request.prompt, "[图片]\n比较这三张图片\n这是后两张")

    async def test_image_collection_waits_for_durable_cache_and_keeps_gif_metadata(
        self,
    ):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        first = self._image_event("image", "/temporary/animation.gif")
        second = self._event("这张动图是什么意思", "text")
        runtime.note_continuous_turn_incoming(first)
        runtime.note_continuous_turn_incoming(second)
        waiting = asyncio.create_task(runtime.settle_continuous_turn(second))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        entry = {"item": first.get_messages()[0], "path": "/cache/animation.gif"}
        setattr(first, runtime._PREPARED_VISUAL_MEDIA_ATTR, [entry])
        runtime.note_continuous_turn_media_ready(first)

        self.assertTrue(await asyncio.wait_for(waiting, timeout=0.2))
        self.assertEqual(
            second.get_messages(), [{"type": "image", "file": "/cache/animation.gif"}]
        )
        self.assertEqual(getattr(second, runtime._PREPARED_VISUAL_MEDIA_ATTR), [entry])

    async def test_image_follow_up_restarts_text_generation_with_actual_image(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        runtime._init_response_gate_state()
        first = self._event("照着这张参考图画", "text")
        second = self._image_event("image", "https://example.com/reference.png")
        runtime.note_continuous_turn_incoming(first)
        await runtime.settle_continuous_turn(first)
        runtime.prepare_continuous_turn_llm_request(first, ProviderRequest())
        runner = types.SimpleNamespace(
            run_context=types.SimpleNamespace(
                context=types.SimpleNamespace(event=first)
            ),
            request_stop=Mock(),
        )
        runtime._active_agent_runner = lambda _event: runner

        self.assertTrue(runtime.note_continuous_turn_incoming(second))
        runner.request_stop.assert_called_once_with()
        self.assertTrue(first.get_extra("agent_stop_requested"))
        self.assertTrue(first.is_stopped())
        self.assertFalse(runtime.continuous_turn_event_is_current(first))
        self.assertFalse(runtime.complete_continuous_turn(first))
        self.assertTrue(await runtime.settle_continuous_turn(second))
        decision = await runtime.evaluate_response_gate(second)
        self.assertEqual(decision["action"], "reply")
        self.assertTrue(decision["forced"])
        request = ProviderRequest(
            prompt="", image_urls=["https://example.com/reference.png"]
        )
        runtime.prepare_continuous_turn_llm_request(second, request)
        self.assertEqual(request.prompt, "照着这张参考图画\n[图片]")
        self.assertEqual(request.image_urls, ["https://example.com/reference.png"])

    async def test_completed_image_turn_and_duplicate_id_do_not_repeat_images(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        first = self._image_event("image", "https://example.com/photo.png")
        second = self._event("下一轮", "text")
        runtime.note_continuous_turn_incoming(first)
        runtime.note_continuous_turn_incoming(first)
        await runtime.settle_continuous_turn(first)
        self.assertEqual(runtime.continuous_turn_message_count(first), 1)
        self.assertEqual(len(first.get_messages()), 1)
        self.assertTrue(runtime.complete_continuous_turn(first))
        self.assertEqual(
            runtime._continuous_turn_batch(first.unified_msg_origin, "private").images,
            {},
        )
        runtime.note_continuous_turn_incoming(second)
        await runtime.settle_continuous_turn(second)
        self.assertEqual(runtime.continuous_turn_messages(second), ("下一轮",))
        self.assertEqual(second.get_messages(), [])

    async def test_image_follow_up_marks_generation_before_runner_registration(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        first = self._event("照着参考图画", "text")
        second = self._image_event("image", "https://example.com/reference.png")
        runtime.note_continuous_turn_incoming(first)
        await runtime.settle_continuous_turn(first)
        runtime.prepare_continuous_turn_llm_request(first, ProviderRequest())
        runtime._active_agent_runner = lambda _event: None

        runtime.note_continuous_turn_incoming(second)

        self.assertTrue(first.get_extra("agent_stop_requested"))
        self.assertTrue(first.is_stopped())
        runner = types.SimpleNamespace(
            run_context=types.SimpleNamespace(
                context=types.SimpleNamespace(event=first)
            ),
            request_stop=Mock(),
        )
        runtime._active_agent_runner = lambda _event: runner
        self.assertTrue(await runtime.settle_continuous_turn(second))
        runner.request_stop.assert_called_once_with()

    async def test_image_takeover_survives_a_further_text_follow_up(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        first = self._event("按参考图画", "text")
        second = self._image_event("image", "https://example.com/reference.png")
        third = self._event("颜色柔和一点", "tail")
        runtime.note_continuous_turn_incoming(first)
        await runtime.settle_continuous_turn(first)
        runtime.prepare_continuous_turn_llm_request(first, ProviderRequest())
        runtime.note_continuous_turn_incoming(second)
        runtime.note_continuous_turn_media_ready(second)
        runtime.note_continuous_turn_incoming(third)

        self.assertFalse(await runtime.settle_continuous_turn(second))
        self.assertTrue(await runtime.settle_continuous_turn(third))
        self.assertTrue(first.get_extra("agent_stop_requested"))
        request = ProviderRequest()
        self.assertTrue(runtime.prepare_continuous_turn_llm_request(third, request))
        self.assertEqual(request.prompt, "按参考图画\n[图片]\n颜色柔和一点")
        self.assertEqual(
            third.get_messages(),
            [{"type": "image", "url": "https://example.com/reference.png"}],
        )

    async def test_trimmed_messages_also_release_their_images(self):
        runtime = self._runtime(continuous_turn_wait_seconds=0)
        runtime._CONTINUOUS_TURN_MAX_MESSAGES = 2
        first = self._image_event("image", "https://example.com/old.png")
        runtime.note_continuous_turn_incoming(first)
        runtime.note_continuous_turn_media_ready(first)
        runtime.note_continuous_turn_incoming(self._event("保留这句", "second"))
        last = self._event("以及这句", "last")
        runtime.note_continuous_turn_incoming(last)
        self.assertTrue(await runtime.settle_continuous_turn(last))
        self.assertEqual(
            runtime.continuous_turn_messages(last), ("保留这句", "以及这句")
        )
        self.assertEqual(last.get_messages(), [])

    async def test_non_image_media_quotes_commands_and_disabled_images_stay_independent(
        self,
    ):
        runtime = self._runtime()
        for kind in ("record", "voice", "video", "file", "reply"):
            with self.subTest(kind=kind):
                event = self._image_event(
                    kind, "https://example.com/photo.png", text="看看"
                )
                event.message_items.append({"type": kind})
                self.assertFalse(runtime.note_continuous_turn_incoming(event))
        command = self._image_event("command", "https://example.com/photo.png")
        runtime._event_has_command_handler = lambda _event: True
        self.assertFalse(runtime.note_continuous_turn_incoming(command))
        disabled = self._runtime(continuous_turn_enabled=False)
        self.assertFalse(disabled.note_continuous_turn_incoming(command))

    async def test_group_images_only_join_same_sender_when_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                runtime = self._runtime(
                    continuous_turn_group_enabled=enabled,
                    continuous_turn_wait_seconds=0,
                )
                first = self._event("我的参考图", "text")
                second = self._image_event("image", "https://example.com/photo.png")
                other = self._event("另一个人的消息", "other")
                for event in (first, second, other):
                    event.unified_msg_origin = "aiocqhttp:GroupMessage:group"
                    event._group_id = "group"
                other._sender_id = "20002"
                self.assertEqual(runtime.note_continuous_turn_incoming(first), enabled)
                self.assertEqual(runtime.note_continuous_turn_incoming(other), enabled)
                self.assertEqual(runtime.note_continuous_turn_incoming(second), enabled)
                self.assertTrue(await runtime.settle_continuous_turn(second))
                if enabled:
                    self.assertEqual(
                        runtime.continuous_turn_messages(second),
                        ("我的参考图", "[图片]"),
                    )
                    self.assertTrue(runtime.continuous_turn_event_is_current(other))

    async def test_single_message_keeps_the_short_base_wait(self):
        runtime = self._runtime()
        event = self._event("只说这一句", "m-single")

        self.assertTrue(runtime.note_continuous_turn_incoming(event))
        batch = runtime._continuous_turn_batch(
            event.unified_msg_origin,
            "private",
        )

        self.assertIsNotNone(batch)
        self.assertAlmostEqual(batch.wait_seconds, 0.05, places=2)

    async def test_follow_up_wait_tracks_actual_message_cadence(self):
        runtime = self._runtime()
        first = self._event("先说半句", "m-first")
        second = self._event("再补半句", "m-second")

        self.assertTrue(runtime.note_continuous_turn_incoming(first))
        first_batch = runtime._continuous_turn_batch(
            first.unified_msg_origin,
            "private",
        )
        self.assertIsNotNone(first_batch)
        first_batch.last_at = time.monotonic() - 0.1

        self.assertTrue(runtime.note_continuous_turn_incoming(second))
        second_batch = runtime._continuous_turn_batch(
            second.unified_msg_origin,
            "private",
        )

        self.assertIsNotNone(second_batch)
        self.assertGreater(second_batch.wait_seconds, 0.05)
        self.assertLessEqual(second_batch.wait_seconds, 0.18)

    async def test_next_turn_reuses_the_session_cadence_without_merging_messages(self):
        runtime = self._runtime()
        first = self._event("第一轮先说", "m-first")
        second = self._event("第一轮补充", "m-second")
        next_turn = self._event("下一轮继续", "m-next")

        self.assertTrue(runtime.note_continuous_turn_incoming(first))
        first_batch = runtime._continuous_turn_batch(
            first.unified_msg_origin,
            "private",
        )
        self.assertIsNotNone(first_batch)
        first_batch.last_at = time.monotonic() - 0.1
        self.assertTrue(runtime.note_continuous_turn_incoming(second))
        self.assertTrue(await runtime.settle_continuous_turn(second))
        self.assertTrue(runtime.complete_continuous_turn(second))

        self.assertTrue(runtime.note_continuous_turn_incoming(next_turn))
        next_batch = runtime._continuous_turn_batch(
            next_turn.unified_msg_origin,
            "private",
        )

        self.assertIsNotNone(next_batch)
        self.assertGreater(next_batch.wait_seconds, 0.05)
        self.assertEqual(next_batch.messages, ["下一轮继续"])

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
