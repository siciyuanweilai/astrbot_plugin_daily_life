# ruff: noqa: I001
import asyncio
import json
import types
import unittest
from unittest.mock import patch

from support import Context, DailyLifeRuntime, Event, LifeSettings, Provider
from core.runtime.delivery import BackgroundTextMode
from core.runtime.expression import remember_expression_context


class SemanticSegmentTest(unittest.TestCase):
    def test_short_lead_fallback_sends_clean_parts_without_t2i_threshold(self):
        source = "少贫嘴，喜欢我也抵消不了刚才扣的敷衍分。"
        responses = (
            json.dumps({"segments": [{"text": source}]}, ensure_ascii=False),
            "{}",
        )
        for response in responses:
            with self.subTest(response=response):
                runtime = self._runtime(response)
                runtime.config.chat_style.private_casual_max_chars = 10
                runtime.context = Context(Provider([]), config={"t2i": False})
                event = Event(unified_msg_origin="aiocqhttp:FriendMessage:1")
                event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

                self.assertTrue(asyncio.run(runtime.apply_semantic_segment_before_send(event)))
                self.assertTrue(asyncio.run(runtime.send_chat_style_segments_if_needed(event)))
                self.assertEqual(
                    [message.chain[0].text for message in event.sent_messages],
                    ["少贫嘴", "喜欢我也抵消不了刚才扣的敷衍分"],
                )

    def test_single_reply_cleans_punctuation_even_when_plan_fails(self):
        source = "明天再聊吧。"
        responses = (
            json.dumps({"segments": [{"text": source}]}, ensure_ascii=False),
            "{}",
        )
        for response in responses:
            with self.subTest(response=response):
                runtime = self._runtime(response)
                runtime.context = Context(Provider([]), config={"t2i": False})
                event = Event()
                event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

                self.assertTrue(asyncio.run(runtime.apply_semantic_segment_before_send(event)))
                self.assertEqual(event.get_result().chain[0].text, "明天再聊吧")

    def test_existing_text_parts_do_not_require_another_model_call(self):
        runtime = self._runtime("{}")
        event = Event()
        event.set_result(event.chain_result([
            types.SimpleNamespace(text="今天有点累"),
            types.SimpleNamespace(text="先休息一下"),
        ]))
        self.assertTrue(asyncio.run(runtime.apply_semantic_segment_before_send(event)))
        self.assertEqual(runtime.composer.prompts, [])
        self.assertEqual([item.text for item in event.get_result().chain],
                         ["今天有点累", "先休息一下"])

    def test_semantic_timeout_keeps_natural_delivery(self):
        runtime = self._runtime("{}")
        runtime.context = Context(Provider([]), config={"t2i": False})

        async def slow_model(*args, **kwargs):
            await asyncio.Event().wait()

        runtime.composer._call_llm_text = slow_model
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:1")
        event._daily_life_enrichment_seconds = 7.99
        event.message_str = "聊聊今天"
        event.set_result(
            event.chain_result([types.SimpleNamespace(text="今天先休息一下。晚点再出门。")])
        )
        with patch("core.runtime.reply._semantic_timeout_seconds", return_value=0.01):
            asyncio.run(asyncio.wait_for(runtime.apply_semantic_segment_before_send(event), 0.2))
        self.assertEqual(runtime.semantic_segment_status()["metrics"]["planning_failed"], 1)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["今天先休息一下", "晚点再出门"],
        )
        self.assertFalse(hasattr(event, "_daily_life_expression_unavailable_reason"))
        self.assertEqual(event._daily_life_enrichment_seconds, 7.99)
        self.assertTrue(asyncio.run(runtime.send_chat_style_segments_if_needed(event)))
        self.assertEqual(
            [message.chain[0].text for message in event.sent_messages],
            ["今天先休息一下", "晚点再出门"],
        )

    def test_semantic_timeout_still_cleans_single_reply_punctuation(self):
        for source in (
            "上次不知道是谁走一千多步就喊腿酸的？",
            "你倒打一耙的本事真是越来越熟练了。",
        ):
            for cleanup_enabled in (True, False):
                with self.subTest(source=source, cleanup_enabled=cleanup_enabled):
                    runtime = self._runtime("{}")
                    runtime.config.chat_style.punctuation_cleanup_enabled = cleanup_enabled
                    runtime.config.chat_style.private_casual_max_chars = 10
                    runtime.context = Context(
                        Provider([]), config={"t2i": True, "t2i_word_threshold": 300}
                    )

                    async def slow_model(*args, **kwargs):
                        await asyncio.Event().wait()

                    runtime.composer._call_llm_text = slow_model
                    event = Event(unified_msg_origin="aiocqhttp:FriendMessage:1")
                    event._daily_life_enrichment_seconds = 7.99
                    event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

                    async def decorate():
                        await runtime.apply_semantic_segment_before_send(event)
                        runtime.apply_chat_style_before_send(event)

                    with patch("core.runtime.reply._semantic_timeout_seconds", return_value=0.01):
                        asyncio.run(asyncio.wait_for(decorate(), 0.2))
                    expected = source[:-1] if cleanup_enabled else source
                    self.assertEqual(event.get_result().chain[0].text, expected)
                    self.assertIsNone(getattr(event, runtime._SEMANTIC_SEGMENT_PLAN_ATTR, None))
                    self.assertEqual(runtime.semantic_segment_status()["metrics"]["planning_failed"], 1)

    def test_configured_timeout_allows_voice_plan_after_three_seconds(self):
        runtime = self._runtime("{}")
        runtime.context = Context(Provider([]), config={"t2i": False})
        runtime.config.chat_style.semantic_timeout_seconds = 8
        runtime.config.voice_generation.expression_timeout_seconds = 8
        runtime.config.voice_generation.enabled = True
        runtime.config.voice_generation.smart_switch_probability = 100
        source = "我有点想你啦"
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:1")
        event._daily_life_enrichment_seconds = 7.99
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))
        synthesize = unittest.mock.AsyncMock(return_value=types.SimpleNamespace(path="voice.mp3"))
        runtime.media = types.SimpleNamespace(voice=types.SimpleNamespace(synthesize=synthesize))

        async def slow_model(*args, **kwargs):
            await asyncio.sleep(3.05)
            return json.dumps({
                "segments": [{"text": source}], "channel": "voice", "confidence": 0.9,
                "reason": "亲近的语气适合直接说出来",
            }, ensure_ascii=False)

        runtime.composer._call_llm_text = slow_model

        async def decorate():
            heartbeat = asyncio.create_task(asyncio.sleep(0.02))
            runtime.mark_voice_switch_available(event)
            await runtime.apply_semantic_segment_before_send(event)
            self.assertTrue(heartbeat.done())
            self.assertTrue(await runtime.apply_voice_switch_before_send(event))

        asyncio.run(asyncio.wait_for(decorate(), 4))
        synthesize.assert_awaited_once()
        self.assertEqual(runtime.semantic_segment_status()["metrics"]["planning_failed"], 0)
        self.assertEqual(event._daily_life_enrichment_seconds, 7.99)

    def test_compact_boundaries_preserve_unicode_and_reject_ambiguous_or_missing_text(self):
        runtime = self._runtime("{}")
        validate = runtime._part_semantic_segment_runtime_component._semantic_segment_validate_payload
        source = "刚下雨啦🌧️我去关窗，等会儿一起看电影"
        plan = validate({"segments": [{"end_after": "刚下雨啦🌧️"}, {"end_after": "等会儿一起看电影"}]}, source)
        self.assertIsNotNone(plan)
        self.assertEqual("".join(p.text for p in plan.segments), source)
        self.assertIsNone(validate({"segments": [{"end_after": "刚下雨啦🌧️"}]}, source))
        self.assertIsNone(validate({"segments": [{"end_after": "晚安"}]}, "晚安，晚安"))
        self.assertIsNone(validate({"segments": [{"end_after": "没有的原文"}]}, source))

    def test_configured_timeout_is_the_only_segmentation_deadline(self):
        source = "我有点想你啦"
        for seconds in (8, 12):
            with self.subTest(seconds=seconds):
                runtime = self._runtime(json.dumps({"segments": [{"text": source}]}))
                runtime.config.chat_style.semantic_timeout_seconds = seconds
                event = Event()
                event._daily_life_enrichment_seconds = 8
                event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))
                with patch("core.runtime.reply.asyncio.timeout", wraps=asyncio.timeout) as timeout:
                    asyncio.run(runtime.apply_semantic_segment_before_send(event))
                timeout.assert_called_once_with(seconds)
                self.assertEqual(runtime.semantic_segment_status()["metrics"]["segmented"], 1)

    def _voice_reply(self, source, semantic_response="{}"):
        expression_payload = json.loads(semantic_response)
        expression_payload.pop("segments", None)
        runtime = self._runtime(
            semantic_response, expression_response=json.dumps(expression_payload)
        )
        runtime.context = Context(Provider([]), config={"t2i": False})
        runtime.config.voice_generation.enabled = True
        runtime.config.voice_generation.smart_switch_probability = 80
        synthesize = unittest.mock.AsyncMock(return_value=types.SimpleNamespace(path="voice.mp3"))
        runtime.media = types.SimpleNamespace(voice=types.SimpleNamespace(synthesize=synthesize))
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:1")
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))
        return runtime, event, synthesize

    def test_voice_judgment_includes_short_and_already_segmented_replies(self):
        for texts in (("想你啦",), ("今天有点累", "想听你说说话")):
            with self.subTest(texts=texts):
                response = json.dumps({
                    "segments": [{"text": text} for text in texts],
                    "channel": "voice", "confidence": 0.99,
                })
                runtime, event, synthesize = self._voice_reply(texts[0], response)
                runtime.config.voice_generation.smart_switch_probability = 100
                event.set_result(event.chain_result([types.SimpleNamespace(text=text) for text in texts]))

                async def run():
                    runtime.mark_voice_switch_available(event)
                    await runtime.apply_semantic_segment_before_send(event)
                    self.assertEqual([item.text for item in event.get_result().chain], list(texts))
                    return await runtime.apply_voice_switch_before_send(event)

                self.assertTrue(asyncio.run(run()))
                self.assertEqual(len(runtime.composer.prompts), 1)
                synthesize.assert_awaited_once()

    def test_expression_timeout_keeps_text_without_probability_or_retry(self):
        for timeout_kind in ("deadline", "provider"):
            for probability in (0, 80, 100):
                with self.subTest(timeout_kind=timeout_kind, probability=probability):
                    runtime, event, synthesize = self._voice_reply("我有点想你啦。")
                    runtime.config.voice_generation.smart_switch_probability = probability
                    remember_expression_context(event, state="困倦，已经躺下，想轻声说晚安。")
                    completed = []

                    async def timeout_model(*args, **kwargs):
                        try:
                            if timeout_kind == "provider":
                                raise asyncio.TimeoutError
                            await asyncio.Event().wait()
                        finally:
                            completed.append(True)

                    runtime.composer._call_llm_text = unittest.mock.AsyncMock(side_effect=timeout_model)

                    async def decorate():
                        runtime.mark_voice_switch_available(event)
                        await runtime.apply_semantic_segment_before_send(event)
                        return await runtime.apply_voice_switch_before_send(event)

                    with (
                        patch("core.runtime.reply._semantic_timeout_seconds", return_value=0.01),
                        patch("core.runtime.voice.modality.expression_timeout_seconds", return_value=0.01),
                        patch("core.runtime.voice.limit.random.random") as draw,
                    ):
                        changed = asyncio.run(asyncio.wait_for(decorate(), 0.2))

                    self.assertFalse(changed)
                    expected_calls = 1 if probability == 0 else 2
                    self.assertEqual(runtime.composer._call_llm_text.await_count, expected_calls)
                    self.assertEqual(completed, [True] * expected_calls)
                    item = runtime._voice_switch_round_store()[event.unified_msg_origin]
                    synthesize.assert_not_awaited()
                    draw.assert_not_called()
                    self.assertEqual(event.get_result().chain[0].text, "我有点想你啦")
                    self.assertNotIn("probability_result", item)
                    if probability:
                        self.assertEqual(item["text_reason"], "表达判断超时。")

    def test_model_text_decision_precedes_probability(self):
        source = "晚点我再跟你说。"
        runtime, event, synthesize = self._voice_reply(source, json.dumps({
            "segments": [{"text": source}], "channel": "text", "confidence": 0.95,
        }, ensure_ascii=False))
        runtime.config.voice_generation.smart_switch_probability = 100
        event.message_str = "我在开会，不方便听语音"
        remember_expression_context(event, preferences="喜欢用语音聊天")

        async def decorate():
            runtime.mark_voice_switch_available(event)
            await runtime.apply_semantic_segment_before_send(event)
            return await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(asyncio.run(decorate()))
        synthesize.assert_not_awaited()
        self.assertNotIn("probability_result", runtime._voice_switch_round_store()[event.unified_msg_origin])

    def test_missing_expression_plan_preserves_structural_text(self):
        for source in ("点这里看 https://example.com", "今天的事情很多。" * 25, "1. 先出门\n2. 再买东西"):
            with self.subTest(source=source):
                runtime, event, synthesize = self._voice_reply(source)
                runtime.config.voice_generation.smart_switch_probability = 100
                runtime.mark_voice_switch_available(event)

                self.assertFalse(asyncio.run(runtime.apply_voice_switch_before_send(event)))
                synthesize.assert_not_awaited()
                self.assertEqual(event.get_result().chain[0].text, source)

    def test_contextual_voice_plan_passes_tone_and_obeys_frequency(self):
        source = "这才乖，快闭上眼，晚安啦。"
        response = json.dumps({
            "segments": [{"text": source}], "channel": "voice", "confidence": 0.93,
            "emotion": "困倦中轻柔亲近的晚安", "emotion_category": "neutral", "voice_style": "soft",
        }, ensure_ascii=False)
        for draw, cooldown, expected in ((0.1, False, True), (0.9, False, False), (0.1, True, False)):
            with self.subTest(draw=draw, cooldown=cooldown):
                runtime, event, synthesize = self._voice_reply(source, response)
                event.message_str = "我戴着耳机，听你说完晚安就睡"
                remember_expression_context(
                    event, state="困倦 85/100；已经躺在床上，轻松亲近。",
                    preferences="睡前喜欢听轻柔的语音，工作时只收文字。",
                    conversation="对方：耳机戴好了\n自己：我也躺下了",
                )
                if cooldown:
                    runtime._part_voice_switch_gate_component._voice_switch_next_chain_limit = lambda: 2
                    runtime._mark_voice_switch_channel(event, "语音")
                    runtime._mark_voice_switch_channel(event, "语音")

                async def decorate():
                    runtime.mark_voice_switch_available(event)
                    await runtime.apply_semantic_segment_before_send(event)
                    return await runtime.apply_voice_switch_before_send(event)

                with patch("core.runtime.voice.limit.random.random", return_value=draw):
                    self.assertEqual(asyncio.run(decorate()), expected)
                self.assertEqual(len(runtime.composer.prompts), 1 if cooldown else 2)
                if cooldown:
                    synthesize.assert_not_awaited()
                    self.assertFalse(any("待判断表达" in p for p in runtime.composer.prompts))
                    continue
                prompt = next(p for p in runtime.composer.prompts if "待判断表达" in p)
                self.assertIn("困倦 85/100", prompt)
                self.assertIn("工作时只收文字", prompt)
                self.assertIn("耳机戴好了", prompt)
                self.assertIn(event.message_str, prompt)
                if expected:
                    synthesize.assert_awaited_once_with(
                        source, emotion="困倦中轻柔亲近的晚安",
                        emotion_category="neutral", voice_style="soft",
                    )
                else:
                    synthesize.assert_not_awaited()

    def test_contextual_voice_result_is_discarded_when_reply_changes(self):
        source = "我有点想你啦。"
        runtime, event, synthesize = self._voice_reply(source, json.dumps({
            "segments": [{"text": source}], "channel": "voice", "confidence": 0.9,
        }, ensure_ascii=False))
        runtime.config.voice_generation.smart_switch_probability = 100

        async def superseded_voice(*args, **kwargs):
            runtime.note_semantic_segment_incoming_message(event)
            return types.SimpleNamespace(path="voice.mp3")

        synthesize.side_effect = superseded_voice

        async def decorate():
            runtime.mark_voice_switch_available(event)
            await runtime.apply_semantic_segment_before_send(event)
            return await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(asyncio.run(decorate()))
        synthesize.assert_awaited_once()
        self.assertIsNone(event.get_result())
        self.assertFalse(hasattr(event, "_daily_life_pending_voice_receipt"))

    def test_semantic_deadline_also_limits_provider_lookup(self):
        runtime = self._runtime("{}")
        cancelled = []

        async def slow_provider(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        runtime.composer._get_provider = slow_provider
        with patch("core.runtime.reply._semantic_timeout_seconds", return_value=0.01):
            plan = asyncio.run(asyncio.wait_for(
                runtime.plan_semantic_segments_for_text("hello", target_scope="scope"), 0.2
            ))
        self.assertFalse(plan.valid)
        self.assertEqual(plan.source, "semantic_timeout")
        self.assertEqual(cancelled, [True])
        self.assertEqual(runtime.composer.prompts, [])
        self.assertEqual(runtime.semantic_segment_status()["metrics"]["planning_failed"], 1)

    def test_cancelled_expression_does_not_fallback_or_continue_sending(self):
        runtime = self._runtime("{}")
        event = Event()
        event.set_result(event.chain_result([types.SimpleNamespace(text="今天先休息一下。")]))

        async def check_cancellation():
            started = asyncio.Event()

            async def slow_model(*args, **kwargs):
                started.set()
                await asyncio.Event().wait()

            runtime.composer._call_llm_text = slow_model
            task = asyncio.create_task(runtime.apply_semantic_segment_before_send(event))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(asyncio.wait_for(check_cancellation(), 0.2))
        self.assertEqual(event.sent_messages, [])
        self.assertFalse(hasattr(event, "_daily_life_expression_unavailable_reason"))
        self.assertEqual(event.get_result().chain[0].text, "今天先休息一下。")
        self.assertEqual(runtime.semantic_segment_status()["metrics"]["planning_failed"], 0)

    def test_natural_delivery_keeps_completed_voice_expression_plan(self):
        source = "今天有点累了。让我先靠一会儿。"
        runtime = self._runtime(json.dumps({
            "segments": [{"text": source}], "channel": "voice",
            "confidence": 0.9, "reason": "语气适合说出来",
        }, ensure_ascii=False))
        runtime.config.chat_style.private_casual_max_chars = 10
        runtime.config.voice_generation.enabled = True
        runtime.context = Context(Provider([]), config={"t2i": False})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:1")
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

        asyncio.run(runtime.apply_semantic_segment_before_send(event))

        self.assertTrue(getattr(event, "_daily_life_natural_fallback_active", False))
        self.assertIsNone(getattr(event, runtime._SEMANTIC_SEGMENT_PLAN_ATTR, None))
        plan = runtime._semantic_expression_plan_from_event(event)
        self.assertEqual(plan.channel, "voice")
        decision = runtime._judge_voice_switch_channel(
            event, runtime._voice_switch_reply_text_from_event(event)
        )
        self.assertEqual(decision["channel"], "voice")

    def test_empty_plan_cannot_bypass_single_reply_cleanup(self):
        from core.runtime.reply import SemanticSegmentPlan

        runtime = self._runtime("{}")
        runtime.context = Context(Provider([]), config={"t2i": False})

        async def empty_plan(*args, **kwargs):
            return SemanticSegmentPlan(())

        runtime._part_semantic_segment_runtime_component._semantic_segment_plan_text = empty_plan
        event = Event()
        event.set_result(event.chain_result([types.SimpleNamespace(text="明天再聊吧。")]))
        self.assertTrue(asyncio.run(runtime.apply_semantic_segment_before_send(event)))
        self.assertEqual(event.get_result().chain[0].text, "明天再聊吧")
        self.assertIsNone(getattr(event, runtime._SEMANTIC_SEGMENT_PLAN_ATTR, None))

    def _runtime(
        self, semantic_response, *, semantic_provider="", daily_life_provider="",
        expression_response=None,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "rhythm_config": {
                    "llm_provider": daily_life_provider,
                },
                "chat_style_config": {
                    "enabled": True,
                    "semantic_provider": semantic_provider,
                    "semantic_max_segments": 3,
                    "segment_delay_range": "0,0",
                },
            }
        )
        runtime._semantic_segment_init_state()

        class Composer:
            def __init__(self):
                self.provider_ids = []
                self.call_options = []
                self.prompts = []

            async def _get_provider(self, provider_id=""):
                self.provider_ids.append(provider_id)
                return object()

            async def _call_llm_text(self, provider, prompt, session_id, **kwargs):
                self.prompts.append(prompt)
                self.call_options.append(kwargs)
                if expression_response is not None and "待判断表达" in prompt:
                    return expression_response
                return semantic_response

        runtime.composer = Composer()
        return runtime

    def test_empty_semantic_provider_uses_system_default_instead_of_daily_life_model(
        self,
    ):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "hello", "relation": "standalone", "pause": "none"}
                    ]
                }
            ),
            daily_life_provider="daily-life-model",
        )

        plan = asyncio.run(
            runtime.plan_semantic_segments_for_text("hello", target_scope="scope")
        )

        self.assertTrue(plan.valid)
        self.assertEqual(runtime.config.llm_provider, "daily-life-model")
        self.assertEqual(runtime.composer.provider_ids, [""])
        self.assertEqual(runtime.composer.call_options[0]["primary_provider_id"], "")

    def test_disabled_chat_style_does_not_call_semantic_provider(self):
        runtime = self._runtime(
            '{"segments":[{"text":"hello","relation":"standalone","pause":"none"}]}'
        )
        runtime.config.chat_style.enabled = False

        plan = asyncio.run(
            runtime.plan_semantic_segments_for_text(
                "hello", target_scope="aiocqhttp:FriendMessage:10001"
            )
        )

        self.assertFalse(plan.valid)
        self.assertEqual(plan.text, "hello")
        self.assertEqual(runtime.composer.provider_ids, [])

    def _background_runtime(self, response, *, enabled=True):
        runtime = self._runtime(response)
        runtime.config.chat_style.enabled = enabled
        runtime.context = Context(Provider([]))
        runtime.note_structured_bot_message = lambda *args, **kwargs: None
        return runtime

    def test_background_text_uses_semantic_segments(self):
        runtime = self._background_runtime(
            '{"segments":[{"text":"先这样。","relation":"lead","pause":"short"},'
            '{"text":"后面再说。","relation":"closing","pause":"none"}]}'
        )

        sent = asyncio.run(
            runtime.send_background_text(
                "aiocqhttp:FriendMessage:10001",
                "先这样。后面再说。",
                mode=BackgroundTextMode.EXPRESSIVE,
                source="video",
            )
        )

        self.assertTrue(sent)
        self.assertEqual(
            [chain.items for _, chain in runtime.context.sent_messages],
            [["先这样。"], ["后面再说。"]],
        )

    def test_background_text_respects_explicit_reply_lines(self):
        runtime = self._background_runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "好像是有点好呀，",
                            "relation": "lead",
                            "pause": "short",
                        },
                        {
                            "text": "拍六张。",
                            "relation": "closing",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )

        sent = asyncio.run(
            runtime.send_background_text(
                "aiocqhttp:FriendMessage:10001",
                "好像是有点\n好呀，拍六张。\n",
                mode=BackgroundTextMode.EXPRESSIVE,
                source="proactive",
            )
        )

        self.assertTrue(sent)
        self.assertEqual(
            [chain.items for _, chain in runtime.context.sent_messages],
            [["好像是有点"], ["好呀，拍六张。"]],
        )
        self.assertIn("原文行布局", runtime.composer.prompts[0])

    def test_background_cancellation_reports_proactive_completion_separately(self):
        for source in ("proactive", "share"):
            with self.subTest(source=source):
                runtime = self._background_runtime(
                    '{"segments":[{"text":"先说一句。","pause":"none"},'
                    '{"text":"后面这句。","pause":"none"}]}'
                )
                runtime._proactive_send_is_current = lambda event: True
                sent_parts = []

                def interrupt(text):
                    sent_parts.append(text)
                    runtime.note_semantic_segment_incoming_message(
                        Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
                    )

                completed = asyncio.run(runtime.send_background_text(
                    "aiocqhttp:FriendMessage:10001", "先说一句。后面这句。",
                    mode=BackgroundTextMode.EXPRESSIVE, source=source, on_sent=interrupt,
                ))
                self.assertEqual(completed, source != "proactive")
                self.assertEqual(sent_parts, ["先说一句。"])
                self.assertEqual(len(runtime.context.sent_messages), 1)

    def test_background_tool_text_respects_lines_and_cleans_punctuation(self):
        runtime = self._background_runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "江边这套对岸灯挺好看。",
                            "relation": "standalone",
                            "pause": "none",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.context = Context(
            Provider([]), config={"t2i": False, "t2i_word_threshold": 120}
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        sent = asyncio.run(
            runtime.send_background_text(
                event.unified_msg_origin,
                "江边这套\n对岸灯挺好看。",
                mode=BackgroundTextMode.EXPRESSIVE,
                source_event=event,
                source="send_message_to_user",
            )
        )

        self.assertTrue(sent)
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(
            [chain.items for chain in event.sent_messages],
            [["江边这套"], ["对岸灯挺好看"]],
        )

    def test_background_text_falls_back_to_natural_segments(self):
        runtime = self._background_runtime("not json")
        text = "窝被窝里呢，头发还半干。刚在翻今天拍的那些照片，灯下看着还挺好看。"

        sent = asyncio.run(
            runtime.send_background_text(
                "aiocqhttp:FriendMessage:10001",
                text,
                mode=BackgroundTextMode.EXPRESSIVE,
                source="video",
            )
        )

        self.assertTrue(sent)
        messages = [chain.items for _, chain in runtime.context.sent_messages]
        self.assertGreaterEqual(len(messages), 2)
        self.assertEqual("".join(item[0] for item in messages), text)

    def test_background_text_disabled_sends_original_once(self):
        runtime = self._background_runtime("not json", enabled=False)
        text = "先这样。后面再说。"

        sent = asyncio.run(
            runtime.send_background_text(
                "aiocqhttp:FriendMessage:10001",
                text,
                mode=BackgroundTextMode.EXPRESSIVE,
                source="video",
            )
        )

        self.assertTrue(sent)
        self.assertEqual(
            [chain.items for _, chain in runtime.context.sent_messages], [[text]]
        )

    def test_direct_background_text_skips_expression_and_addressing(self):
        runtime = self._background_runtime(
            '{"segments":[{"text":"状态：","relation":"lead","pause":"short"},'
            '{"text":"处理超时","relation":"continue","pause":"normal"}]}'
        )
        addressing_calls = []
        runtime.decorate_group_addressing_chain = lambda *args, **kwargs: (
            addressing_calls.append((args, kwargs))
        )
        text = "B站视频自动总结失败：专业总结超过视频理解总时间限制（300 秒）"

        sent = asyncio.run(
            runtime.send_background_text(
                "aiocqhttp:GroupMessage:group-test-001",
                text,
                mode=BackgroundTextMode.DIRECT,
                source="sight_failure",
            )
        )

        self.assertTrue(sent)
        self.assertEqual(runtime.composer.provider_ids, [])
        self.assertEqual(addressing_calls, [])
        self.assertEqual(runtime._semantic_segment_epochs, {})
        self.assertEqual(
            [chain.items for _, chain in runtime.context.sent_messages], [[text]]
        )
        self.assertFalse(hasattr(runtime, "send_expressed_text"))

    def test_explicit_semantic_provider_is_used_independently(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "hello", "relation": "standalone", "pause": "none"}
                    ]
                }
            ),
            semantic_provider="semantic-segment-model",
            daily_life_provider="daily-life-model",
        )

        plan = asyncio.run(
            runtime.plan_semantic_segments_for_text("hello", target_scope="scope")
        )

        self.assertTrue(plan.valid)
        self.assertEqual(runtime.composer.provider_ids, ["semantic-segment-model"])
        self.assertEqual(
            runtime.composer.call_options[0]["primary_provider_id"],
            "semantic-segment-model",
        )

    def test_invalid_semantic_result_uses_concise_warning(self):
        runtime = self._runtime("not json")

        with patch("core.runtime.reply.logger.debug") as debug:
            plan = asyncio.run(
                runtime.plan_semantic_segments_for_text("hello", target_scope="scope")
            )

        self.assertFalse(plan.valid)
        self.assertEqual(
            runtime.semantic_segment_status()["metrics"]["planning_failed"], 1
        )
        self.assertEqual(
            runtime.semantic_segment_status()["metrics"]["fallback_single"], 0
        )
        debug.assert_any_call("[日常生活] 模型语义分段返回无效，已改用自然分段")

    def test_model_timeout_is_distinct_from_configured_deadline(self):
        runtime = self._runtime("")

        async def timeout(*args, **kwargs):
            raise asyncio.TimeoutError

        runtime.composer._call_llm_text = timeout
        with patch("core.runtime.reply.logger.debug") as debug:
            plan = asyncio.run(
                runtime.plan_semantic_segments_for_text("hello", target_scope="scope")
            )

        self.assertFalse(plan.valid)
        self.assertEqual(plan.source, "provider_timeout")
        self.assertEqual(plan.reason, "语义分段超时。")
        debug.assert_any_call("[日常生活] 模型语义分段超时，已改用自然分段")

    def test_semantic_exception_does_not_log_provider_details(self):
        runtime = self._runtime("")

        async def fail(*args, **kwargs):
            raise RuntimeError("provider unavailable")

        runtime.composer._call_llm_text = fail
        with (
            patch("core.runtime.reply.logger.debug") as debug,
            patch("core.runtime.reply.logger.warning") as warning,
        ):
            plan = asyncio.run(
                runtime.plan_semantic_segments_for_text("hello", target_scope="scope")
            )

        self.assertFalse(plan.valid)
        self.assertIn(
            unittest.mock.call("[日常生活] 模型语义分段调用异常，已改用自然分段"),
            debug.call_args_list,
        )
        self.assertFalse(any("provider unavailable" in str(call) for call in debug.call_args_list))
        warning.assert_not_called()

    def test_semantic_segmenter_requests_short_complete_expression_actions(self):
        parts = [
            "先顺着这条街慢慢逛，边走边拍。",
            "等下热气再退一点，就去街角那家糖水铺。",
            "你看到哪儿顺眼，我们也可以临时拐过去。",
        ]
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": parts[0], "relation": "lead", "pause": "normal"},
                        {"text": parts[1], "relation": "continue", "pause": "normal"},
                        {"text": parts[2], "relation": "add", "pause": "none"},
                    ]
                },
                ensure_ascii=False,
            )
        )

        plan = asyncio.run(
            runtime.plan_semantic_segments_for_text(
                "".join(parts), target_scope="scope", length_hint=15
            )
        )

        self.assertEqual([segment.text for segment in plan.segments], parts)
        self.assertIn(
            "每个分段短而完整，只承载一个主要意思", runtime.composer.prompts[0]
        )
        self.assertIn('"segments"', runtime.composer.prompts[0])
        self.assertNotIn('"bubbles"', runtime.composer.prompts[0])
        self.assertIn("最多返回 3 个分段", runtime.composer.prompts[0])
        self.assertIn("当前场景单个分段参考长度约为 15 字", runtime.composer.prompts[0])

    def test_short_prompt_keeps_complete_reply_beyond_length_hint(self):
        parts = ["我也觉得挺好玩的。", "后面那段我们下次可以一起试试。"]
        for stance, message in [("play", "哈哈"), ("respond", "为什么？"), ("comfort", "有点难过")]:
            with self.subTest(stance=stance):
                runtime = self._runtime(json.dumps({
                    "segments": [{"text": text, "pause": "none"} for text in parts],
                    "stance": stance,
                }, ensure_ascii=False))
                runtime.config.chat_style.casual_max_chars = 10
                runtime.config.chat_style.private_casual_max_chars = 10
                runtime.config.chat_style.punctuation_cleanup_enabled = False
                event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
                event.message_str = message
                event.set_result(event.chain_result([
                    types.SimpleNamespace(text="".join(parts))
                ]))

                self.assertTrue(asyncio.run(runtime.apply_semantic_segment_before_send(event)))
                self.assertEqual("".join(item.text for item in event.get_result().chain), "".join(parts))
                self.assertTrue(asyncio.run(runtime.send_semantic_segments_if_needed(event)))
                self.assertEqual("".join(part for msg in event.sent_messages for part in msg.chain), "".join(parts))

    def test_semantic_segment_delay_uses_random_pause_range(self):
        runtime = self._runtime("{}")
        runtime.config.chat_style.segment_min_delay_seconds = 0.2
        runtime.config.chat_style.segment_max_delay_seconds = 2.2

        with patch("core.runtime.delay.random.uniform", return_value=0.73) as uniform:
            delay = runtime._part_semantic_segment_runtime_component._semantic_segment_delay_seconds(
                types.SimpleNamespace(text="附近那家云吞面行不行？", pause="normal")
            )

        self.assertEqual(delay, 0.73)
        low, high = uniform.call_args.args
        self.assertGreaterEqual(low, 0.62)
        self.assertLessEqual(high, 1.6)

    def test_semantic_segmenter_requires_exact_source_text(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "你呢", "relation": "lead", "pause": "short"},
                        {
                            "text": "今天不出去走走？",
                            "relation": "question",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        plan = asyncio.run(
            runtime.plan_semantic_segments_for_text(
                "你呢今天不出去走走？", target_scope="scope"
            )
        )
        self.assertEqual(
            [segment.text for segment in plan.segments], ["你呢", "今天不出去走走？"]
        )

        runtime.composer._call_llm_text = lambda *args, **kwargs: "{}"
        invalid = asyncio.run(
            runtime.plan_semantic_segments_for_text(
                "你呢今天不出去走走？", target_scope="scope"
            )
        )
        self.assertEqual(len(invalid.segments), 1)
        self.assertFalse(invalid.valid)

    def test_apply_and_send_uses_semantic_segments(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "你呢", "relation": "lead", "pause": "none"},
                        {
                            "text": "今天不出去走走？",
                            "relation": "question",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:1")
        refresh_calls = []
        runtime.schedule_pending_chat_state_refresh = lambda source_event: (
            refresh_calls.append(source_event) or True
        )
        event.message_str = "今天呢"
        event.set_result(
            event.chain_result([types.SimpleNamespace(text="你呢今天不出去走走？")])
        )

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["你呢", "今天不出去走走？"],
        )
        self.assertTrue(asyncio.run(runtime.send_semantic_segments_if_needed(event)))
        self.assertEqual(len(event.sent_messages), 2)
        self.assertEqual(
            [message.chain for message in event.sent_messages],
            [["你呢"], ["今天不出去走走？"]],
        )
        self.assertIsNone(event.get_result())
        self.assertEqual(refresh_calls, [event])

    def test_semantic_planning_keeps_natural_segment_delay(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "先这样。", "relation": "lead", "pause": "long"},
                        {
                            "text": "后面再说。",
                            "relation": "closing",
                            "pause": "long",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.config.chat_style.segment_min_delay_seconds = 3.0
        runtime.config.chat_style.segment_max_delay_seconds = 3.0
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10008")
        event.set_result(
            event.chain_result([types.SimpleNamespace(text="先这样。后面再说。")])
        )

        self.assertTrue(asyncio.run(runtime.apply_semantic_segment_before_send(event)))
        sleeps = []

        async def record_sleep(delay):
            sleeps.append(delay)

        with patch("core.runtime.reply.asyncio.sleep", new=record_sleep):
            sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(sent)
        self.assertEqual(sleeps, [3.0])
        self.assertEqual(len(event.sent_messages), 2)

    def test_semantic_send_failure_falls_back_to_natural_segment_delivery(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "出门前拍的",
                            "relation": "lead",
                            "pause": "short",
                        },
                        {
                            "text": "帽子已经戴上了",
                            "relation": "add",
                            "pause": "short",
                        },
                        {
                            "text": "你那边到了没",
                            "relation": "question",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.context = Context(
            Provider([]), config={"t2i": False, "t2i_word_threshold": 120}
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10004")
        event.set_result(
            event.chain_result(
                [types.SimpleNamespace(text="出门前拍的\n帽子已经戴上了\n你那边到了没")]
            )
        )
        self.assertTrue(asyncio.run(runtime.apply_semantic_segment_before_send(event)))

        original_send = event.send
        attempts = 0

        async def fail_first_send(message):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("首次分段发送失败")
            await original_send(message)

        event.send = fail_first_send
        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(sent)
        self.assertEqual(attempts, 4)
        self.assertEqual(
            [message.chain[0].text for message in event.sent_messages],
            ["出门前拍的", "帽子已经戴上了", "你那边到了没"],
        )
        self.assertIsNone(event.get_result())
        metrics = runtime.semantic_segment_status()["metrics"]
        self.assertEqual(metrics["failed"], 1)
        self.assertEqual(metrics["fallback_natural"], 1)

    def test_partial_semantic_send_failure_does_not_repeat_sent_segments(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "第一段", "relation": "lead", "pause": "short"},
                        {"text": "第二段", "relation": "add", "pause": "short"},
                        {
                            "text": "第三段",
                            "relation": "closing",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10005")
        event.set_result(
            event.chain_result([types.SimpleNamespace(text="第一段第二段第三段")])
        )
        self.assertTrue(asyncio.run(runtime.apply_semantic_segment_before_send(event)))

        original_send = event.send
        attempts = 0

        async def fail_second_send(message):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise RuntimeError("第二段发送失败")
            await original_send(message)

        event.send = fail_second_send
        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(sent)
        self.assertEqual(attempts, 2)
        self.assertEqual(
            [message.chain for message in event.sent_messages],
            [["第一段"]],
        )
        self.assertIsNone(event.get_result())
        metrics = runtime.semantic_segment_status()["metrics"]
        self.assertEqual(metrics["failed"], 1)
        self.assertEqual(metrics["fallback_natural"], 0)

    def test_failed_natural_retry_keeps_readable_default_reply(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "第一段", "relation": "lead", "pause": "short"},
                        {
                            "text": "第二段",
                            "relation": "closing",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10006")
        event.set_result(
            event.chain_result([types.SimpleNamespace(text="第一段\n第二段")])
        )
        self.assertTrue(asyncio.run(runtime.apply_semantic_segment_before_send(event)))

        attempts = 0

        async def always_fail(_message):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("发送失败")

        event.send = always_fail
        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertFalse(sent)
        self.assertEqual(attempts, 2)
        self.assertEqual(len(event.get_result().chain), 1)
        self.assertEqual(event.get_result().chain[0].text, "第一段\n第二段")

    def test_inline_markdown_is_cleaned_before_semantic_model(self):
        cleaned = "5 月 8 日已经复服了。现在还在运营。"
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "5 月 8 日已经复服了。",
                            "relation": "standalone",
                            "pause": "short",
                        },
                        {
                            "text": "现在还在运营。",
                            "relation": "closing",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.config.chat_style.punctuation_cleanup_enabled = False
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:10001")
        event.set_result(
            event.chain_result(
                [types.SimpleNamespace(text="**5 月 8 日已经复服了**。现在还在运营。")]
            )
        )

        cleaned_changed = runtime.apply_chat_plain_text_cleanup_before_send(event)
        segmented_changed = asyncio.run(
            runtime.apply_semantic_segment_before_send(event)
        )

        self.assertTrue(cleaned_changed)
        self.assertTrue(segmented_changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["5 月 8 日已经复服了。", "现在还在运营。"],
        )
        prompt_source = runtime.composer.prompts[0].split("回复原文：", 1)[1]
        self.assertEqual(prompt_source, cleaned)
        self.assertNotIn("**", prompt_source)

    def test_paragraph_breaks_do_not_invalidate_semantic_segment_plan(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "喝绿豆沙呢，门口坐着吹晚风。",
                            "relation": "standalone",
                            "pause": "normal",
                        },
                        {
                            "text": "你那碗还剩多少？",
                            "relation": "question",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10000", group_id="10000"
        )
        event.message_str = "在干嘛呢"
        event.set_result(
            event.chain_result(
                [
                    types.SimpleNamespace(
                        text="喝绿豆沙呢，门口坐着吹晚风。\n\n你那碗还剩多少？"
                    )
                ]
            )
        )

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(sent)
        self.assertEqual(
            [message.chain for message in event.sent_messages],
            [["喝绿豆沙呢，门口坐着吹晚风。"], ["你那碗还剩多少？"]],
        )
        self.assertNotIn("\n", runtime.composer.prompts[0].split("回复原文：", 1)[1])
        self.assertIsNone(event.get_result())

    def test_explicit_reply_lines_prevent_cross_line_semantic_split(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "好像是有点好呀，",
                            "relation": "lead",
                            "pause": "short",
                        },
                        {
                            "text": "拍六张。",
                            "relation": "closing",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.context = Context(
            Provider([]), config={"t2i": False, "t2i_word_threshold": 120}
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10002")
        event.set_result(
            event.chain_result(
                [types.SimpleNamespace(text="好像是有点\n好呀，拍六张。\n")]
            )
        )

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(sent)
        self.assertEqual(
            [message.chain for message in event.sent_messages],
            [["好像是有点"], ["好呀 拍六张"]],
        )
        self.assertIn("原文行布局", runtime.composer.prompts[0])

    def test_semantic_segments_over_send_cap_are_bundled_with_spaces(self):
        source = (
            "喜欢吗\n\n"
            "不喜欢\n\n"
            "别发那个\n\n"
            "一起睡也不代表什么都能拍、都能发\n\n"
            "边界还在\n\n"
            "困了，先睡了"
        )
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "喜欢吗", "relation": "question", "pause": "short"},
                        {"text": "不喜欢", "relation": "correction", "pause": "short"},
                        {"text": "别发那个", "relation": "turn", "pause": "short"},
                        {
                            "text": "一起睡也不代表什么都能拍、都能发",
                            "relation": "continue",
                            "pause": "normal",
                        },
                        {"text": "边界还在", "relation": "add", "pause": "short"},
                        {
                            "text": "困了，先睡了",
                            "relation": "closing",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.config.chat_style.semantic_max_segments = 5
        runtime.context = Context(
            Provider([]), config={"t2i": False, "t2i_word_threshold": 120}
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10003")
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(sent)
        self.assertEqual(
            [message.chain[0] for message in event.sent_messages],
            [
                "喜欢吗",
                "不喜欢",
                "别发那个",
                "一起睡也不代表什么都能拍 都能发",
                "边界还在 困了 先睡了",
            ],
        )
        self.assertEqual(runtime.semantic_segment_status()["metrics"]["segmented"], 1)
        self.assertEqual(
            runtime.semantic_segment_status()["metrics"]["fallback_natural"], 0
        )

    def test_long_reply_keeps_default_result_for_astrbot_t2i(self):
        runtime = self._runtime("{}")
        runtime.context = Context(
            Provider([]),
            config={
                "t2i": True,
                "t2i_word_threshold": 50,
            },
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:7")
        event.message_str = "详细说说"
        reply = (
            "这段回复会保留给文本转图像处理，不进入模型语义分段。"
            "因为完整文本已经超过 AstrBot 配置的字数阈值，应该继续使用默认发送流程。"
        )
        event.set_result(event.chain_result([types.SimpleNamespace(text=reply)]))

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))

        self.assertFalse(changed)
        self.assertEqual(runtime.composer.prompts, [])
        self.assertEqual(event.get_result().chain[0].text, reply)
        self.assertFalse(hasattr(event, runtime._SEMANTIC_SEGMENT_PLAN_ATTR))
        self.assertTrue(getattr(event, "_daily_life_t2i_default_send", False))

    def test_plain_text_under_t2i_threshold_cleans_punctuation_after_semantic_segmentation(
        self,
    ):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "先去喝绿豆沙啊，正餐回头再看。",
                            "relation": "lead",
                            "pause": "short",
                        },
                        {
                            "text": "附近那家云吞面行不行？",
                            "relation": "question",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.context = Context(
            Provider([]),
            config={"t2i": True, "t2i_word_threshold": 120},
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:8")
        source = "先去喝绿豆沙啊，正餐回头再看。附近那家云吞面行不行？"
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(sent)
        self.assertEqual(
            [message.chain for message in event.sent_messages],
            [["先去喝绿豆沙啊 正餐回头再看"], ["附近那家云吞面行不行"]],
        )

    def test_single_plain_text_segment_also_cleans_punctuation(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "你有想吃的吗？",
                            "relation": "question",
                            "pause": "none",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.context = Context(
            Provider([]),
            config={"t2i": True, "t2i_word_threshold": 120},
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:9")
        event.set_result(
            event.chain_result([types.SimpleNamespace(text="你有想吃的吗？")])
        )

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))

        self.assertTrue(changed)
        self.assertEqual(event.get_result().chain[0].text, "你有想吃的吗")

    def test_valid_but_unsplit_semantic_plan_uses_generic_natural_pause(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "煲仔饭！这个为什么不分段？",
                            "relation": "standalone",
                            "pause": "none",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.config.chat_style.private_casual_max_chars = 15
        runtime.context = Context(
            Provider([]), config={"t2i": False, "t2i_word_threshold": 120}
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:12")
        event.set_result(
            event.chain_result(
                [types.SimpleNamespace(text="煲仔饭！这个为什么不分段？")]
            )
        )

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(sent)
        self.assertEqual(
            [message.chain[0].text for message in event.sent_messages],
            ["煲仔饭", "这个为什么不分段"],
        )

    def test_punctuation_cleaning_preserves_ascii_url_syntax(self):
        cleanup_chars = LifeSettings.from_dict({}).chat_style.punctuation_cleanup_chars
        cleaned = DailyLifeRuntime._semantic_segment_clean_punctuation(
            "看看 https://example.com/a?x=1。",
            cleanup_chars,
        )

        self.assertEqual(cleaned, "看看 https://example.com/a?x=1")

    def test_punctuation_cleaning_preserves_ascii_pause_runs(self):
        cleanup_chars = LifeSettings.from_dict({}).chat_style.punctuation_cleanup_chars

        self.assertEqual(
            DailyLifeRuntime._semantic_segment_clean_punctuation(
                "嗯...睡吧。", cleanup_chars
            ),
            "嗯...睡吧",
        )
        self.assertEqual(
            DailyLifeRuntime._semantic_segment_clean_punctuation(
                "wait...", cleanup_chars
            ),
            "wait...",
        )

    def test_punctuation_cleaning_removes_explicit_ascii_pause_runs(self):
        self.assertEqual(
            DailyLifeRuntime._semantic_segment_clean_punctuation(
                "嗯...睡吧。", "...。"
            ),
            "嗯 睡吧",
        )
        self.assertEqual(
            DailyLifeRuntime._semantic_segment_clean_punctuation("嗯...", "..."),
            "嗯",
        )
        self.assertEqual(
            DailyLifeRuntime._semantic_segment_clean_punctuation("嗯.", "..."),
            "嗯.",
        )

    def test_semantic_segments_preserve_ascii_pause_runs_before_send(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "嗯...", "relation": "lead", "pause": "short"},
                        {
                            "text": "mua睡吧。",
                            "relation": "closing",
                            "pause": "normal",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        runtime.context = Context(
            Provider([]), config={"t2i": False, "t2i_word_threshold": 120}
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:13")
        event.set_result(
            event.chain_result([types.SimpleNamespace(text="嗯...\n\nmua\n\n睡吧。")])
        )

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(sent)
        self.assertEqual(
            [message.chain for message in event.sent_messages],
            [["嗯..."], ["mua"], ["睡吧"]],
        )

    def test_punctuation_cleaning_uses_custom_character_set(self):
        self.assertEqual(
            DailyLifeRuntime._semantic_segment_clean_punctuation(
                "你有想吃的吗？我都可以。", "，。"
            ),
            "你有想吃的吗？我都可以",
        )

    def test_invalid_semantic_plan_falls_back_to_natural_segments(self):
        runtime = self._runtime("{}")
        runtime.note_structured_sent_result = lambda event: None
        runtime.note_media_source_event = lambda event: None
        runtime.note_proactive_bot_reply = lambda event: None
        runtime.note_voice_switch_text_result = lambda event: None
        refresh_calls = []
        runtime.schedule_pending_chat_state_refresh = lambda source_event: (
            refresh_calls.append(source_event) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10")
        source = "先去喝绿豆沙。然后沿着河边走一会儿。"
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        structured_sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))
        natural_sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertFalse(structured_sent)
        self.assertTrue(natural_sent)
        self.assertEqual(len(event.sent_messages), 2)
        self.assertEqual(refresh_calls, [event])
        self.assertFalse(hasattr(event, runtime._SEMANTIC_SEGMENT_PLAN_ATTR))

    def test_invalid_semantic_plan_uses_soft_pause_natural_fallback(self):
        runtime = self._runtime("{}")
        runtime.config.chat_style.private_casual_max_chars = 15
        runtime.context = Context(
            Provider([]), config={"t2i": False, "t2i_word_threshold": 120}
        )
        runtime.note_structured_sent_result = lambda event: None
        runtime.note_media_source_event = lambda event: None
        runtime.note_proactive_bot_reply = lambda event: None
        runtime.note_voice_switch_text_result = lambda event: None
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:11")
        source = "煲仔饭！这个好，我想吃窝蛋牛肉的，必须带一点焦香的锅巴底~"
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        natural_sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(natural_sent)
        self.assertEqual(
            [message.chain[0].text for message in event.sent_messages],
            [
                "煲仔饭",
                "这个好 我想吃窝蛋牛肉的",
                "必须带一点焦香的锅巴底~",
            ],
        )

    def test_natural_fallback_preserves_late_strong_breaks_with_five_segment_cap(
        self,
    ):
        runtime = self._runtime("{}")
        runtime.config.chat_style.semantic_max_segments = 5
        runtime.config.chat_style.group_casual_max_chars = 30
        runtime.context = Context(
            Provider([]), config={"t2i": False, "t2i_word_threshold": 120}
        )
        runtime.note_structured_sent_result = lambda event: None
        runtime.note_media_source_event = lambda event: None
        runtime.note_proactive_bot_reply = lambda event: None
        runtime.note_voice_switch_text_result = lambda event: None
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        source = (
            "啊对！测试街道那边的测试农贸市场确实近多了，"
            "走过去也就一公里多，十几分钟就能到。\n\n"
            "我刚才懵了一下没反应过来。"
            "不过大半夜的你研究测试路线干嘛呀，明天打算去买菜下厨啦？"
        )
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        natural_sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(natural_sent)
        self.assertEqual(
            [message.chain[0].text for message in event.sent_messages],
            [
                "啊对",
                "测试街道那边的测试农贸市场确实近多了",
                "走过去也就一公里多 十几分钟就能到",
                "我刚才懵了一下没反应过来",
                "不过大半夜的你研究测试路线干嘛呀 明天打算去买菜下厨啦",
            ],
        )

    def test_natural_fallback_preserves_space_when_explicit_lines_are_bundled(self):
        runtime = self._runtime("{}")
        runtime.config.chat_style.semantic_max_segments = 5
        runtime.context = Context(
            Provider([]), config={"t2i": False, "t2i_word_threshold": 120}
        )
        runtime.note_structured_sent_result = lambda event: None
        runtime.note_media_source_event = lambda event: None
        runtime.note_proactive_bot_reply = lambda event: None
        runtime.note_voice_switch_text_result = lambda event: None
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10004")
        source = (
            "喜欢吗\n"
            "不喜欢\n"
            "别发那个\n"
            "一起睡也不代表什么都能拍、都能发\n"
            "边界还在\n"
            "困了，先睡了"
        )
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))
        sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(sent)
        self.assertEqual(
            [message.chain[0].text for message in event.sent_messages],
            [
                "喜欢吗",
                "不喜欢",
                "别发那个",
                "一起睡也不代表什么都能拍 都能发",
                "边界还在 困了 先睡了",
            ],
        )

    def test_send_uses_saved_plan_when_text_result_components_are_recombined(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "先说第一件事。", "relation": "lead", "pause": "none"},
                        {
                            "text": "再补充第二件事。",
                            "relation": "add",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:5")
        source = "先说第一件事。再补充第二件事。"
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))
        asyncio.run(runtime.apply_semantic_segment_before_send(event))
        event.set_result(event.chain_result([types.SimpleNamespace(text=source)]))

        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(sent)
        self.assertEqual(
            [message.chain for message in event.sent_messages],
            [["先说第一件事。"], ["再补充第二件事。"]],
        )
        self.assertIsNone(event.get_result())

    def test_voice_result_discards_pending_text_segments(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "第一句。", "relation": "lead", "pause": "none"},
                        {"text": "第二句。", "relation": "add", "pause": "none"},
                    ]
                },
                ensure_ascii=False,
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:6")
        event.set_result(
            event.chain_result([types.SimpleNamespace(text="第一句。第二句。")])
        )
        asyncio.run(runtime.apply_semantic_segment_before_send(event))
        event.set_result(event.chain_result([{"type": "record", "file": "voice.wav"}]))

        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertFalse(sent)
        self.assertEqual(event.sent_messages, [])
        self.assertIsNotNone(event.get_result())

    def test_group_addressing_is_applied_only_to_first_segment(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "第一句。", "relation": "lead", "pause": "none"},
                        {"text": "第二句。", "relation": "add", "pause": "none"},
                    ]
                },
                ensure_ascii=False,
            )
        )
        addressed_indexes = []

        def decorate(message, **kwargs):
            index = kwargs["segment_index"]
            addressed_indexes.append(index)
            if index == 0:
                message.chain.insert(0, {"type": "at", "qq": "10001"})

        runtime.decorate_group_addressing_chain = decorate
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10000",
            group_id="10000",
        )
        event.set_result(
            event.chain_result([types.SimpleNamespace(text="第一句。第二句。")])
        )
        asyncio.run(runtime.apply_semantic_segment_before_send(event))

        sent = asyncio.run(runtime.send_semantic_segments_if_needed(event))

        self.assertTrue(sent)
        self.assertEqual(addressed_indexes, [0, 1])
        self.assertEqual(event.sent_messages[0].chain[0]["type"], "at")
        self.assertEqual(event.sent_messages[0].chain[1], "第一句。")
        self.assertEqual(event.sent_messages[1].chain, ["第二句。"])

    def test_new_message_cancels_unsent_tail(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {"text": "先说一句", "relation": "lead", "pause": "none"},
                        {
                            "text": "后面这句不应该再发",
                            "relation": "add",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:2")
        event.set_result(
            event.chain_result(
                [types.SimpleNamespace(text="先说一句后面这句不应该再发")]
            )
        )
        asyncio.run(runtime.apply_semantic_segment_before_send(event))

        original_send = event.send

        async def send_and_interrupt(message):
            await original_send(message)
            runtime.note_semantic_segment_incoming_message(event)

        event.send = send_and_interrupt
        self.assertTrue(asyncio.run(runtime.send_semantic_segments_if_needed(event)))
        self.assertEqual(len(event.sent_messages), 1)
        self.assertEqual(runtime.semantic_segment_status()["metrics"]["cancelled"], 1)
        self.assertIsNone(event.get_result())

    def test_silent_tool_preface_never_enters_semantic_segmentation(self):
        runtime = self._runtime(
            json.dumps(
                {
                    "segments": [
                        {
                            "text": "这段不应规划",
                            "relation": "standalone",
                            "pause": "none",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:3")
        event.set_result(event.chain_result(["这段不应规划"]))
        runtime._is_active_agent_intermediate_result = lambda current: True
        self.assertTrue(runtime.suppress_intermediate_tool_result(event))

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))

        self.assertFalse(changed)
        self.assertIsNone(event.get_result())
        self.assertEqual(runtime.semantic_segment_status()["metrics"]["segmented"], 0)

    def test_visible_tool_waiting_line_skips_semantic_segmentation_without_being_cleared(
        self,
    ):
        runtime = self._runtime("{}")
        runtime._is_active_agent_intermediate_result = lambda event: True
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:4")
        event.set_result(event.chain_result(["等下哦，我看看今天的安排。"]))

        changed = asyncio.run(runtime.apply_semantic_segment_before_send(event))

        self.assertFalse(changed)
        self.assertIsNotNone(event.get_result())
        self.assertEqual(runtime.semantic_segment_status()["metrics"]["segmented"], 0)


if __name__ == "__main__":
    unittest.main()
