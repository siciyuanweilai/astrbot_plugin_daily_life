# ruff: noqa: I001
import asyncio
import json
import types
import unittest
from unittest.mock import patch

from support import Context, DailyLifeRuntime, Event, LifeSettings, Provider
from core.runtime.delivery import BackgroundTextMode


class SemanticSegmentTest(unittest.TestCase):
    def _runtime(
        self, semantic_response, *, semantic_provider="", daily_life_provider=""
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

    def test_semantic_timeout_uses_concise_warning(self):
        runtime = self._runtime("")

        async def timeout(*args, **kwargs):
            raise asyncio.TimeoutError

        runtime.composer._call_llm_text = timeout
        with patch("core.runtime.reply.logger.debug") as debug:
            plan = asyncio.run(
                runtime.plan_semantic_segments_for_text("hello", target_scope="scope")
            )

        self.assertFalse(plan.valid)
        debug.assert_any_call("[日常生活] 模型语义分段超时，已改用自然分段")

    def test_semantic_exception_keeps_details_in_debug_log(self):
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
        debug.assert_any_call(
            "[日常生活] 模型语义分段调用异常详情：RuntimeError: provider unavailable"
        )
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

    def test_semantic_segment_delay_uses_random_pause_range(self):
        runtime = self._runtime("{}")
        runtime.config.chat_style.segment_min_delay_seconds = 0.2
        runtime.config.chat_style.segment_max_delay_seconds = 2.2

        with patch("core.runtime.delay.random.uniform", return_value=0.73) as uniform:
            delay = runtime._semantic_segment_delay_seconds(
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
