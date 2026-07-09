import unittest

from runtimehelpers import *


class RuntimeProactiveTest(ResponseGateRuntimeMixin, unittest.TestCase):
    def test_response_gate_observes_quiet_group_message(self):
        state = LifeState(
            interaction_capacity=20,
            attention_openness=20,
            social=20,
            sleepiness=80,
            watch_state="peek",
            interrupt_level="high",
        )
        runtime = self._response_gate_runtime(
            {
                "response_gate_config": {
                    "group_talk_frequency": 0.0,
                    "no_reply_backoff_seconds": 0,
                }
            },
            state,
        )
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001", message_id="m1")
        event.message_str = "路过"

        decision = asyncio.run(
            runtime.apply_response_gate_for_event(
                event,
            )
        )

        self.assertEqual(decision["action"], "observe")
        self.assertTrue(event.call_llm)
    def test_response_gate_allows_group_directed_message(self):
        runtime = self._response_gate_runtime({"response_gate_config": {"group_talk_frequency": 0.0}})
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001", message_id="m2")
        event.message_str = "你看看这个"
        event.is_at_or_wake_command = True

        decision = asyncio.run(runtime.apply_response_gate_for_event(event))

        self.assertEqual(decision["action"], "reply")
        self.assertFalse(event.call_llm)
        self.assertTrue(decision["forced"])
    def test_response_gate_allows_group_alias_directed_message(self):
        runtime = self._response_gate_runtime(
            {
                "bot_identity_aliases": ["小助手"],
                "response_gate_config": {"group_talk_frequency": 0.0},
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001", message_id="m2-alias")
        event.message_str = "在干嘛呢，小助手"

        decision = asyncio.run(runtime.apply_response_gate_for_event(event))

        self.assertEqual(decision["action"], "reply")
        self.assertFalse(event.call_llm)
        self.assertTrue(decision["forced"])
        self.assertIn("明确指向我", decision["reason"])
    def test_alias_directed_message_marks_as_astrbot_wake(self):
        runtime = self._response_gate_runtime({"bot_identity_aliases": ["小助手"]})
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001", message_id="m2-alias")
        event.message_str = "在干嘛呢，小助手"

        marked = runtime.mark_alias_directed_event_as_wake(event)

        self.assertTrue(marked)
        self.assertTrue(event.is_at_or_wake_command)
        self.assertFalse(event.call_llm)
    def test_response_gate_group_quote_to_other_is_not_forced(self):
        runtime = self._response_gate_runtime(
            {
                "response_gate_config": {
                    "group_talk_frequency": 0.0,
                    "no_reply_backoff_seconds": 0,
                }
            }
        )
        first = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="测试群",
            sender_id="u1",
            sender_name="用户甲",
            message_id="m-a",
            self_id="bot01",
        )
        first.message_str = "我先说一句"
        runtime.note_structured_incoming_message(first)
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="测试群",
            sender_id="u2",
            sender_name="用户乙",
            message_id="m-b",
            self_id="bot01",
        )
        event.message_str = "我回你"
        event.message_items = [{"type": "reply", "target_message_id": "m-a"}]
        runtime.note_structured_incoming_message(event)

        decision = asyncio.run(runtime.apply_response_gate_for_event(event))

        self.assertEqual(decision["action"], "observe")
        self.assertNotIn("forced", decision)
    def test_response_gate_group_quote_to_bot_is_forced(self):
        runtime = self._response_gate_runtime({"response_gate_config": {"group_talk_frequency": 0.0}})
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="测试群",
            sender_id="u2",
            sender_name="用户乙",
            message_id="m-bot-reply",
            self_id="bot01",
        )
        event.message_str = "那你怎么看"
        event.message_items = [
            {
                "type": "reply",
                "target_message_id": "bot-old",
                "target_message_sender_id": "bot01",
                "target_message_content": "我刚才说过这个",
            }
        ]
        runtime.note_structured_incoming_message(event)

        decision = asyncio.run(runtime.apply_response_gate_for_event(event))

        self.assertEqual(decision["action"], "reply")
        self.assertTrue(decision["forced"])
        self.assertIn("引用/回复了我的消息", decision["reason"])
    def test_response_gate_private_auto_wake_is_not_forced(self):
        runtime = self._response_gate_runtime(
            {
                "response_gate_config": {
                    "private_talk_frequency": 0.0,
                    "no_reply_backoff_seconds": 0,
                }
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001", message_id="m2p")
        event.message_str = "在忙吗"
        event.is_at_or_wake_command = True

        decision = asyncio.run(runtime.apply_response_gate_for_event(event))

        self.assertEqual(decision["action"], "observe")
        self.assertTrue(event.call_llm)
        self.assertNotIn("forced", decision)
    def test_response_gate_private_observe_records_user_context(self):
        runtime = self._response_gate_runtime(
            {
                "response_gate_config": {
                    "private_talk_frequency": 0.0,
                    "no_reply_backoff_seconds": 0,
                }
            }
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            sender_id="10001",
            sender_name="测试用户乙",
            message_id="m2-record",
        )
        event.message_str = "我刚到家了"
        runtime.context.config = {
            "provider_settings": {
                "identifier": True,
                "datetime_system_prompt": True,
            },
            "timezone": "Asia/Shanghai",
        }

        fixed_now = datetime.datetime(
            2026,
            6,
            27,
            18,
            5,
            tzinfo=datetime.timezone(datetime.timedelta(hours=8), "CST"),
        )

        class FixedDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now.astimezone(tz) if tz else fixed_now

        with patch("core.runtime.past.datetime.datetime", FixedDateTime):
            decision = asyncio.run(runtime.apply_response_gate_for_event(event))

        self.assertEqual(decision["action"], "observe")
        history = runtime.context.conversation_manager.conversations[event.unified_msg_origin].history
        self.assertEqual(history[-1]["role"], "user")
        self.assertNotIn("name", history[-1])
        self.assertEqual(history[-1]["content"][0], {"type": "text", "text": "我刚到家了"})
        reminder = history[-1]["content"][1]["text"]
        self.assertIn("<system_reminder>", reminder)
        self.assertIn("User ID: 10001, Nickname: 测试用户乙", reminder)
        self.assertIn("Current datetime: 2026-06-27 18:05 (CST), Weekday: Saturday", reminder)
        inserted = runtime.context.message_history_manager.inserts[-1]
        self.assertEqual(inserted.platform_id, "aiocqhttp")
        self.assertEqual(inserted.user_id, "10001")
        self.assertEqual(inserted.sender_id, "10001")
        self.assertEqual(inserted.sender_name, "测试用户乙")
        self.assertEqual(inserted.content["type"], "user")
        self.assertEqual(inserted.content["text"], "我刚到家了")
        self.assertEqual(inserted.content["message"], [{"type": "plain", "text": "我刚到家了"}])
    def test_response_gate_private_observe_records_user_image_context(self):
        state = LifeState(
            mood_score=0,
            busyness=100,
            social=0,
            sleepiness=100,
            stress=100,
            interaction_capacity=0,
            attention_openness=0,
        )
        runtime = self._response_gate_runtime(
            {
                "response_gate_config": {
                    "private_talk_frequency": 0.0,
                    "no_reply_backoff_seconds": 0,
                }
            },
            state,
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            sender_id="10001",
            sender_name="测试用户乙",
            message_id="m2-image",
        )
        image_file = Path(tempfile.mkdtemp()) / "media_image_abc.png"
        image_file.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        image_path = str(image_file)
        event.message_str = "怎么样？"
        event.message_items.append({"type": "image", "file": image_path})

        decision = asyncio.run(runtime.apply_response_gate_for_event(event))

        self.assertEqual(decision["action"], "observe")
        history = runtime.context.conversation_manager.conversations[event.unified_msg_origin].history
        content = history[-1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "怎么样？"})
        self.assertIn({"type": "text", "text": f"[Image Attachment: path {image_path}]"}, content)
        self.assertTrue(content[-2]["text"].startswith("<system_reminder>"))
        self.assertEqual(content[-1]["type"], "image_url")
        self.assertTrue(content[-1]["image_url"]["url"].startswith("data:image/png;base64,"))

        inserted = runtime.context.message_history_manager.inserts[-1]
        self.assertEqual(inserted.content["text"], "怎么样？")
        self.assertEqual(inserted.content["message"][0], {"type": "plain", "text": "怎么样？"})
        self.assertEqual(inserted.content["message"][1]["type"], "image")
        self.assertEqual(inserted.content["message"][1]["filename"], "media_image_abc.png")
        self.assertTrue(
            inserted.content["message"][1]["embedded_url"].startswith("data:image/png;base64,")
        )
    def test_response_gate_private_observe_does_not_duplicate_user_context(self):
        runtime = self._response_gate_runtime(
            {
                "response_gate_config": {
                    "private_talk_frequency": 0.0,
                    "no_reply_backoff_seconds": 0,
                }
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001", message_id="m2-dedupe")
        event.message_str = "我刚到家了"

        asyncio.run(runtime.apply_response_gate_for_event(event))
        asyncio.run(runtime.apply_response_gate_for_event(event))

        history = runtime.context.conversation_manager.conversations[event.unified_msg_origin].history
        self.assertEqual(
            [
                DailyLifeRuntime._history_primary_text(item["content"])
                for item in history
                if item.get("role") == "user"
            ],
            ["我刚到家了"],
        )
        self.assertEqual(len(runtime.context.message_history_manager.inserts), 1)
    def test_response_gate_private_message_can_reply(self):
        runtime = self._response_gate_runtime({"response_gate_config": {"private_talk_frequency": 1.0}})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001", message_id="m3")
        event.message_str = "我刚到家了"

        decision = asyncio.run(runtime.apply_response_gate_for_event(event))

        self.assertEqual(decision["action"], "reply")
        self.assertFalse(event.call_llm)
    def test_response_gate_state_score_tracks_life_willingness(self):
        runtime = self._response_gate_runtime({"response_gate_config": {"private_talk_frequency": 0.5}})
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            sender_id="10001",
            message_id="m-state-score",
        )
        event.message_str = "I am thinking about continuing this chat"
        now = datetime.datetime(2026, 5, 24, 12, 0)
        warm_state = LifeState(
            mood_score=86,
            busyness=10,
            social=80,
            sleepiness=10,
            stress=10,
            interaction_capacity=80,
            attention_openness=82,
        )
        cold_state = LifeState(
            mood_score=22,
            busyness=85,
            social=15,
            sleepiness=85,
            stress=88,
            interaction_capacity=20,
            attention_openness=20,
        )

        warm_score, _ = runtime._response_gate_score(event, warm_state, pending_count=1, now=now)
        cold_score, _ = runtime._response_gate_score(event, cold_state, pending_count=1, now=now)

        self.assertGreaterEqual(warm_score, 0.9)
        self.assertLessEqual(cold_score, 0.2)
        self.assertGreater(warm_score - cold_score, 0.6)
    def test_response_gate_state_score_uses_physiological_rhythm(self):
        runtime = self._response_gate_runtime({"response_gate_config": {"private_talk_frequency": 0.5}})
        reasons = []
        low_rhythm_state = LifeState.from_value(
            {
                "interaction_capacity": 55,
                "attention_openness": 55,
                "mood_score": 60,
                "social": 50,
                "sleepiness": 30,
                "busyness": 30,
                "stress": 20,
                "physiological_rhythm": {
                    "social_battery": 18,
                    "body_condition": {"label": "身体发沉", "intensity": 76},
                },
            }
        )
        high_rhythm_state = LifeState.from_value(
            {
                "interaction_capacity": 55,
                "attention_openness": 55,
                "mood_score": 60,
                "social": 50,
                "sleepiness": 30,
                "busyness": 30,
                "stress": 20,
                "physiological_rhythm": {
                    "social_battery": 82,
                    "body_condition": {"label": "无明显不适", "intensity": 0},
                },
            }
        )

        low_delta = runtime._response_gate_state_delta(low_rhythm_state, False, reasons)
        high_reasons = []
        high_delta = runtime._response_gate_state_delta(high_rhythm_state, False, high_reasons)

        self.assertLess(low_delta, high_delta)
        self.assertIn("社交电量偏低", reasons)
        self.assertIn("身体负担较高", reasons)
        self.assertIn("社交电量较足", high_reasons)
    def test_response_gate_backoff_observes_repeated_group_noise(self):
        runtime = self._response_gate_runtime(
            {
                "response_gate_config": {
                    "group_talk_frequency": 0.0,
                    "no_reply_backoff_seconds": 60,
                    "no_reply_backoff_start_count": 1,
                }
            }
        )
        first = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001", message_id="m4")
        first.message_str = "第一句"
        second = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001", message_id="m5")
        second.message_str = "第二句"

        first_decision = asyncio.run(runtime.evaluate_response_gate(first, now=datetime.datetime(2026, 5, 24, 12, 0)))
        second_decision = asyncio.run(runtime.evaluate_response_gate(second, now=datetime.datetime(2026, 5, 24, 12, 0, 10)))

        self.assertEqual(first_decision["action"], "observe")
        self.assertEqual(second_decision["action"], "observe")
        self.assertIn("安静观察", second_decision["reason"])
    def test_response_gate_schema_is_configurable(self):
        config = LifeSettings.from_dict(
            {
                "response_gate_config": {
                    "group_talk_frequency": "0.25",
                    "private_talk_frequency": "0.9",
                    "min_interval_seconds": "8",
                }
            }
        )

        self.assertEqual(config.response_gate.group_talk_frequency, 0.25)
        self.assertEqual(config.response_gate.private_talk_frequency, 0.9)
        self.assertEqual(config.response_gate.min_interval_seconds, 8)


class RuntimeProactiveAsyncTest(RuntimeAsyncHelperMixin, unittest.IsolatedAsyncioTestCase):
    async def test_proactive_voice_sends_voice_without_duplicate_text(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "proactive_enabled": True,
                    "api_key": "sf-key",
                    "voice": "voice-1",
                }
            }
        )
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime.archive = DataManager()
        runtime._apply_proactive_send_timing = lambda payload: async_return(None)
        runtime._send_proactive_emoji_if_needed = lambda scope, payload: async_return(None)
        runtime._mark_failed_proactive_contact = lambda *args, **kwargs: async_return(None)

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "我困啦",
            "闲时回复发送失败",
            send_payload={"expression_intent": {"emotion": "困倦", "emotion_category": "neutral"}},
        )

        self.assertTrue(sent)
        self.assertEqual(voice_calls, [("我困啦", "困倦", "neutral")])
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertTrue(any(getattr(item, "file", "") == "voice.mp3" for item in runtime.context.sent_messages[0][1].items))
        self._assert_last_assistant_history(runtime, "aiocqhttp:FriendMessage:10001", "我困啦")
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_proactive_voice_failure_falls_back_to_text(self):
        async def fail_voice(*args, **kwargs):
            raise RuntimeError("语音服务暂时不可用")

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "proactive_enabled": True,
                    "api_key": "sf-key",
                    "voice": "voice-1",
                }
            }
        )
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=fail_voice
            )
        )
        runtime.archive = DataManager()
        runtime._apply_proactive_send_timing = lambda payload: async_return(None)
        runtime._send_proactive_emoji_if_needed = lambda scope, payload: async_return(None)
        runtime._mark_failed_proactive_contact = lambda *args, **kwargs: async_return(None)

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "我困啦",
            "闲时回复发送失败",
            send_payload={"expression_intent": {"emotion": "困倦", "emotion_category": "neutral"}},
        )

        self.assertTrue(sent)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["我困啦"])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_proactive_voice_sends_when_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "proactive_enabled": True,
                    "api_key": "sf-key",
                    "voice": "voice-1",
                }
            }
        )
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime.archive = DataManager()
        runtime._apply_proactive_send_timing = lambda payload: async_return(None)
        runtime._send_proactive_emoji_if_needed = lambda scope, payload: async_return(None)
        runtime._mark_failed_proactive_contact = lambda *args, **kwargs: async_return(None)

        sent = await runtime._send_proactive_message(
            "aiocqhttp:GroupMessage:10001",
            "我困啦",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(voice_calls, ["我困啦"])
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertTrue(any(getattr(item, "file", "") == "voice.mp3" for item in runtime.context.sent_messages[0][1].items))
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_directed_detection_ignores_listener_wake_flag(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        event.is_wake = True
        event.is_at_or_wake_command = False

        self.assertFalse(runtime._event_is_directed(event))

        event.is_at_or_wake_command = True
        self.assertTrue(runtime._event_is_directed(event))
    async def test_directed_detection_accepts_configured_bot_aliases(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"bot_identity_aliases": ["小助手", "SweetBot"]})

        chinese = Event(
            sender_name="阿林",
            sender_id="u1",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        chinese.message_str = "在干嘛呢，小助手"
        self.assertTrue(runtime._event_is_directed(chinese))

        ascii_hit = Event(
            sender_name="阿林",
            sender_id="u1",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        ascii_hit.message_str = "SweetBot 在吗"
        self.assertTrue(runtime._event_is_directed(ascii_hit))

        ascii_word = Event(
            sender_name="阿林",
            sender_id="u1",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        ascii_word.message_str = "SweetBotany 这个词好怪"
        self.assertFalse(runtime._event_is_directed(ascii_word))
    async def test_evaluate_proactive_reply_can_generate_short_reply(self):
        provider = Provider(
            [
                '{"should_reply": true, "confidence": 0.92, "decision": "reply", '
                '"reason": "群里聊到看展，顺手接一句", "inner_monologue": "想接话", '
                '"reply_strategy": "轻插话", "reply_text": "我也想去看这个展", "memory_note": "闲时续话"}'
            ],
            provider_id="proactive-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider, providers={"proactive-model": provider})
        runtime.config = LifeSettings.from_dict(
            {
                "rhythm_config": {"llm_provider": "default-model"},
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "cooldown_minutes": 10,
                    "min_confidence": 0.7,
                    "max_reply_length": 30,
                },
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        class Composer(LifeAutonomyMixin):
            def __init__(self, archive, config):
                self.archive = archive
                self.config = config

            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                return provider.responses[0]

            async def _cleanup_conversation(self, session_id):
                return None

            async def _get_persona(self):
                return "一个喜欢看展的人"

        runtime.composer = Composer(runtime.archive, runtime.config)
        runtime._proactive_last_reply_at = {}
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                state=LifeState.from_value(
                    {
                        "energy": 68,
                        "social": 74,
                        "interaction_capacity": 76,
                        "attention_openness": 70,
                        "interrupt_level": "ordinary",
                        "summary": "状态平稳，愿意轻松参与群聊",
                    }
                ),
                timeline=[TimelineItem(time="19:20", activity="在群里看大家聊看展", status="轻松")],
                meta={"theme": "轻松聊天", "mood": "有点想接话"},
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "这家展厅真的很适合周末去。"

        result = await runtime.evaluate_proactive_reply(event)

        self.assertTrue(result["should_reply"])
        self.assertEqual(result["reply_text"], "我也想去看这个展")
        self.assertEqual(len(await runtime.archive.get_recent_action_decisions(10)), 1)
        life_decisions = await runtime.archive.get_life_decisions(10)
        self.assertEqual(life_decisions[0].kind, "proactive_reply")
        self.assertEqual(life_decisions[0].source, "proactive_reply")
        life_decision_evidence = await runtime.archive.get_memory_evidence(target_type="life_decision", limit=10)
        self.assertEqual(life_decision_evidence[0].evidence_type, "decision")
        self.assertIn("群里聊到看展", life_decision_evidence[0].summary)
        self.assertEqual(len(await runtime.archive.get_life_episodes(10)), 1)
        self.assertEqual(len(await runtime.archive.get_memory_evidence(target_type="action_decision", limit=10)), 1)
    async def test_proactive_reply_absorbs_used_short_term_focus(self):
        runtime, _ = self._make_proactive_runtime(
            [
                '{"should_reply": true, "confidence": 0.91, "decision": "reply", '
                '"reason": "早睡恢复已经参与判断，只轻轻接一句不展开久聊", '
                '"target_topic": "早睡恢复", "reply_strategy": "轻量收束", '
                '"reply_text": "我今天会早点收住", "memory_note": "早睡恢复下的轻量续话"}'
            ],
            provider_id="proactive-model",
        )
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                state=LifeState.from_value(
                    {
                        "energy": 55,
                        "interaction_capacity": 64,
                        "attention_openness": 70,
                        "interrupt_level": "ordinary",
                        "summary": "能轻声接话，但不适合久聊",
                    }
                ),
            )
        )
        await runtime.archive.upsert_focus_slot(
            FocusSlotRecord(
                scope="",
                focus_key="early_sleep",
                label="早睡恢复",
                priority=90,
                reason="用户希望最近早点休息",
            )
        )
        await runtime.archive.upsert_focus_slot(
            FocusSlotRecord(
                scope="other_group",
                focus_key="early_sleep",
                label="早睡恢复",
                priority=95,
                reason="其它群的同名短期目标",
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="生活群",
        )
        event.message_str = "今天聊得挺晚了。"

        result = await runtime.evaluate_proactive_reply(event, now=datetime.datetime(2026, 5, 24, 21, 30))

        self.assertTrue(result["should_reply"])
        decisions = await runtime.archive.get_life_decisions(10)
        self.assertEqual(decisions[0].kind, "proactive_reply")
        focus_slots = await runtime.archive.get_focus_slots(10, active_only=False)
        global_slot = [item for item in focus_slots if item.scope == ""][0]
        other_slot = [item for item in focus_slots if item.scope == "other_group"][0]
        self.assertLess(global_slot.priority, 90)
        self.assertEqual(global_slot.expires_at, "2026-05-24")
        self.assertIn("已参与 2026-05-24 的proactive_reply决策", global_slot.reason)
        self.assertEqual(other_slot.priority, 95)
        self.assertEqual(other_slot.reason, "其它群的同名短期目标")
        focus_evidence = await runtime.archive.get_memory_evidence(target_type="focus", limit=10)
        self.assertEqual(focus_evidence[0].evidence_type, "decision")
        self.assertIn("早睡恢复", focus_evidence[0].summary)
    async def test_proactive_prompt_includes_recent_conversation_context(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.2, "decision": "observe", '
                '"reason": "还不够自然", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        target = "aiocqhttp:GroupMessage:10001"
        runtime.context.conversation_manager.conversations[target] = type(
            "Conversation",
            (),
            {
                "history": [
                    {"role": "user", "content": "这周末要不要去看展？", "name": "阿林"},
                    {"role": "assistant", "content": "可以呀，我想看看时间。"},
                    {"role": "user", "content": "我比较想下午去。", "name": "阿林"},
                ]
            },
        )()
        runtime.context.conversation_manager.current_ids[target] = "current"
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "min_confidence": 0.7,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_last_reply_at = {}
        await runtime.archive.save_day(DayRecord(date="2026-05-24"))
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin=target,
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "那就等下午的场次看看。"

        await runtime.evaluate_proactive_reply(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        prompt = provider.prompts[0]
        self.assertLess(prompt.index("角色人设摘要"), prompt.index("此刻日期时间：2026-05-24 12:00"))
        self.assertIn("刚才的对话余温（只作氛围参考，不要补答旧消息）", prompt)
        self.assertIn("阿林: 这周末要不要去看展？", prompt)
        self.assertIn("我: 可以呀，我想看看时间。", prompt)
        self.assertIn("阿林: 我比较想下午去。", prompt)
        self.assertIn("人物称谓与性别规则", prompt)
        self.assertIn("证据不足时", prompt)
        self.assertIn("这位群友", prompt)
    async def test_proactive_prompt_marks_saved_pronouns_as_non_gender_evidence(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.2, "decision": "observe", '
                '"reason": "还不够自然", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "min_confidence": 0.7,
                }
            }
        )
        runtime.archive = DataManager()
        await runtime.archive.save_day(DayRecord(date="2026-05-24"))
        await runtime.archive.touch_relationship(
            "u1",
            name="小林",
            date_str="2026-05-24",
            relationship_story="她平时会记得我想去看展。",
        )
        event = Event(
            sender_name="小林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "今天只是随便聊聊。"

        await runtime.evaluate_proactive_reply(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        prompt = provider.prompts[0]
        self.assertIn("关系印象：", prompt)
        self.assertIn("旧关系叙事、最近印象或记忆里的他/她不能当作性别依据", prompt)
        self.assertIn("她平时会记得我想去看展。", prompt)
    async def test_proactive_prompt_includes_air_state_and_target_candidates(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.2, "decision": "observe", '
                '"reason": "先观察", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "idle_minutes": 5,
                }
            }
        )
        runtime.archive = DataManager()
        await runtime.archive.save_day(DayRecord(date="2026-05-24"))
        first = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
            message_id="m1",
        )
        first.message_str = "这个展我还在犹豫要不要去。"
        second = Event(
            sender_name="小夏",
            sender_id="u2",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
            message_id="m2",
        )
        second.message_str = "下午场好像更舒服。"

        runtime.note_proactive_activity(first, now=datetime.datetime(2026, 5, 24, 12, 0))
        runtime.note_proactive_activity(second, now=datetime.datetime(2026, 5, 24, 12, 2))
        await runtime.evaluate_idle_proactive_candidates(now=datetime.datetime(2026, 5, 24, 12, 8))

        prompt = provider.prompts[0]
        self.assertIn("会话空气感", prompt)
        self.assertIn("可自然承接的候选消息", prompt)
        self.assertIn("target_message_id", prompt)
        self.assertIn("m1 · 12:00 · 阿林", prompt)
        self.assertIn("m2 · 12:02 · 小夏", prompt)
        self.assertIn("待看消息数：2", prompt)

    async def test_proactive_reply_uses_local_candidate_target_when_model_omits_target(self):
        runtime, provider = self._make_proactive_runtime(
            [
                json.dumps(
                    {
                        "should_reply": True,
                        "confidence": 0.95,
                        "decision": "reply",
                        "reason": "下午场这个话题自然可以接一句",
                        "reply_text": "那就下午场吧，听起来没那么赶。",
                        "expression_review": {"passed": True},
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "talk_frequency": 0.65,
                    "min_confidence": 0.7,
                }
            }
        )
        await runtime.archive.save_day(DayRecord(date="2026-05-24", state=LifeState(social=80, attention_openness=80)))
        event = Event(
            sender_name="小夏",
            sender_id="u2",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
            message_id="m2",
        )
        event.message_str = "下午场好像更舒服。"
        event.proactive_pending_count = 2
        event.proactive_recent_messages = [
            {"message_id": "m1", "sender_name": "阿林", "content": "这个展我还在犹豫要不要去。"},
            {"message_id": "m2", "sender_name": "小夏", "content": "下午场好像更舒服。"},
        ]

        result = await runtime.evaluate_proactive_reply(event, now=datetime.datetime(2026, 5, 24, 12, 8))

        self.assertTrue(result["should_reply"])
        self.assertEqual(result["target_message_id"], "m2")
        self.assertEqual(result["target_topic"], "下午场好像更舒服。")
        self.assertIn("本地候选承接", provider.prompts[0])
        self.assertIn("消息ID m2", provider.prompts[0])

    async def test_proactive_readiness_skips_model_when_state_is_cold(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"should_reply": true, "confidence": 0.99, "decision": "reply", "reply_text": "我来了"}'],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "talk_frequency": 0.2,
                }
            }
        )
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                state=LifeState(
                    mood_score=20,
                    busyness=90,
                    social=10,
                    sleepiness=90,
                    stress=90,
                    interaction_capacity=15,
                    attention_openness=15,
                    interrupt_level="high",
                    watch_state="peek",
                ),
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "路过一下"

        result = await runtime.evaluate_proactive_reply(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        self.assertFalse(result["should_reply"])
        self.assertEqual(result["decision"], "observe")
        self.assertIn("local_score", result)
        self.assertEqual(provider.prompts, [])
    async def test_proactive_readiness_allows_model_when_feedback_is_warm(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.2, "decision": "observe", '
                '"reason": "模型继续观察", "reply_text": ""}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_enabled": True,
                    "private_talk_frequency": 0.18,
                }
            }
        )
        scope = "aiocqhttp:FriendMessage:10001"
        await runtime.archive.save_day(DayRecord(date="2026-05-24", state=LifeState(social=72, attention_openness=70)))
        await runtime.archive.save_reply_effect(
            ReplyEffectRecord(
                scope=scope,
                reply_text="刚才闲时接了一句",
                outcome="positive",
                warmth=90,
                continuity=85,
                source="proactive_reply",
            )
        )
        await runtime.archive.upsert_behavior_scene(
            BehaviorSceneRecord(
                scope=scope,
                scene="闲时回复读空气",
                preferred_action="reply",
                outcome_hint="这段会话里闲时续话容易被接住",
                confidence=0.9,
                support_count=4,
                source="proactive_feedback",
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            platform_name="aiocqhttp",
            unified_msg_origin=scope,
        )
        event.message_str = "刚才那个话题我又想到一点。"

        result = await runtime.evaluate_proactive_reply(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        self.assertTrue(result["handled"])
        self.assertEqual(result["decision"], "observe")
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("闲时回复效果参考", provider.prompts[0])
    async def test_evaluate_proactive_reply_respects_cooldown(self):
        provider = Provider([], provider_id="proactive-model")
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider, providers={"proactive-model": provider})
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "cooldown_minutes": 10,
                }
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(lambda provider_id="": async_return(provider)),
                "_call_llm_text": lambda self, provider, prompt, session_id, empty_retries=0, primary_provider_id="": async_return("{}"),
                "_cleanup_conversation": staticmethod(lambda session_id: async_return(None)),
                "_get_persona": staticmethod(lambda: async_return("一个喜欢看展的人")),
            },
        )()
        runtime._proactive_last_reply_at = {"10001": datetime.datetime(2026, 5, 24, 12, 0)}
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "刚才那个展怎么样？"

        result = await runtime.evaluate_proactive_reply(event, now=datetime.datetime(2026, 5, 24, 12, 5))

        self.assertFalse(result["should_reply"])
        self.assertEqual(result["decision"], "cooldown")
    async def test_evaluate_proactive_reply_supports_private_message(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": true, "confidence": 0.9, "decision": "reply", '
                '"reason": "私聊里自然回应", "inner_monologue": "想回一句", '
                '"reply_strategy": "轻松回应", "reply_text": "那我也记一下这个点", "memory_note": "私聊闲时回应"}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_enabled": True,
                    "private_talk_frequency": 0.7,
                    "min_confidence": 0.7,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_last_reply_at = {}
        await runtime.archive.save_day(DayRecord(date="2026-05-24"))
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "刚才说的那个设定我觉得还挺适合继续写。"

        result = await runtime.evaluate_proactive_reply(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        self.assertTrue(result["handled"])
        self.assertTrue(result["should_reply"])
        self.assertEqual(result["reply_text"], "那我也记一下这个点")
        self.assertIn("私聊", provider.prompts[0])
        self.assertIn("闲时发言频率：0.70", provider.prompts[0])
        self.assertLess(provider.prompts[0].index("JSON 输出要求"), provider.prompts[0].index("【眼前内容】"))
        self.assertIn("我最近看到的内容", provider.prompts[0])
    async def test_note_proactive_activity_skips_command_and_stopped_events(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._proactive_idle_candidates = {}

        class CommandFilter:
            pass

        class Handler:
            event_filters = [CommandFilter()]

        command_event = Event(
            sender_name="阿林",
            sender_id="u1",
            unified_msg_origin="aiocqhttp:FriendMessage:u1",
        )
        command_event.message_str = "分享 视频空间测试"
        command_event.set_extra("activated_handlers", [Handler()])

        stopped_event = Event(
            sender_name="阿林",
            sender_id="u2",
            unified_msg_origin="aiocqhttp:FriendMessage:u2",
        )
        stopped_event.message_str = "这个话题等会儿再聊"
        stopped_event.stop_event()

        runtime.note_proactive_activity(command_event, now=datetime.datetime(2026, 5, 24, 12, 0))
        runtime.note_proactive_activity(stopped_event, now=datetime.datetime(2026, 5, 24, 12, 0))

        self.assertEqual(runtime._proactive_idle_candidates, {})
    async def test_idle_proactive_candidate_sends_after_silence(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": true, "confidence": 0.92, "decision": "reply", '
                '"reason": "群聊安静后自然续一句", "inner_monologue": "可以轻轻接", '
                '"reply_strategy": "轻插话", "reply_text": "这个展听起来确实挺适合慢慢逛", "memory_note": "沉默后接话"}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "idle_minutes": 20,
                    "cooldown_minutes": 10,
                    "min_confidence": 0.7,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_last_reply_at = {}
        runtime._proactive_idle_candidates = {}
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "这家展厅真的很适合周末去。"
        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        await runtime.evaluate_idle_proactive_candidates(now=datetime.datetime(2026, 5, 24, 12, 10))

        self.assertEqual(runtime.context.sent_messages, [])
        self.assertIn("10001", runtime._proactive_idle_candidates)

        await runtime.evaluate_idle_proactive_candidates(now=datetime.datetime(2026, 5, 24, 12, 21))

        self.assertEqual(len(runtime.context.sent_messages), 1)
        target, chain = runtime.context.sent_messages[0]
        self.assertEqual(target, "aiocqhttp:GroupMessage:10001")
        self.assertEqual(chain.items, ["这个展听起来确实挺适合慢慢逛"])
        self.assertEqual(runtime._proactive_idle_candidates, {})
        self.assertIn("会话已安静：21 分钟", provider.prompts[0])
        self._assert_last_assistant_history(runtime, "aiocqhttp:GroupMessage:10001", "这个展听起来确实挺适合慢慢逛")
    async def test_idle_proactive_wait_keeps_candidate_and_delays_retry(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.66, "decision": "wait", '
                '"reason": "话题像还没说完", "reply_text": "", "memory_note": "先等一下", '
                '"wait_reason": "等群里再补一句"}',
                '{"should_reply": true, "confidence": 0.92, "decision": "reply", '
                '"reason": "现在有自然落点", "reply_text": "下午场确实舒服点", "memory_note": ""}',
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "idle_minutes": 5,
                    "min_confidence": 0.7,
                }
            }
        )
        runtime.archive = DataManager()
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "我看看下午场还有没有票。"
        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        await runtime.evaluate_idle_proactive_candidates(now=datetime.datetime(2026, 5, 24, 12, 6))
        self.assertIn("10001", runtime._proactive_idle_candidates)
        self.assertEqual(runtime.context.sent_messages, [])

        await runtime.evaluate_idle_proactive_candidates(now=datetime.datetime(2026, 5, 24, 12, 9))
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("10001", runtime._proactive_idle_candidates)

        await runtime.evaluate_idle_proactive_candidates(now=datetime.datetime(2026, 5, 24, 12, 17))
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["下午场确实舒服点"])
        self.assertEqual(runtime._proactive_idle_candidates, {})
    async def test_idle_proactive_observe_keeps_candidate_and_holds_retry(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.4, "decision": "observe", '
                '"reason": "low energy, keep watching", "reply_text": "", "memory_note": ""}',
                '{"should_reply": true, "confidence": 0.92, "decision": "reply", '
                '"reason": "state is warmer now", "reply_text": "then we can keep it simple", "memory_note": ""}',
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "idle_minutes": 1,
                    "min_confidence": 0.7,
                }
            }
        )
        runtime.archive = DataManager()
        base_time = datetime.datetime(2026, 5, 24, 12, 0)
        event = Event(
            sender_name="friend",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="chat",
        )
        event.message_str = "maybe later"
        runtime.note_proactive_activity(event, now=base_time)

        await runtime.evaluate_idle_proactive_candidates(now=base_time + datetime.timedelta(minutes=2))

        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("10001", runtime._proactive_idle_candidates)
        hold_until = runtime._proactive_idle_candidates["10001"].get("observe_hold_until")
        self.assertIsInstance(hold_until, datetime.datetime)
        self.assertGreater(hold_until, base_time + datetime.timedelta(minutes=2))

        await runtime.evaluate_idle_proactive_candidates(now=base_time + datetime.timedelta(minutes=4))

        self.assertEqual(len(provider.prompts), 1)
        self.assertEqual(runtime.context.sent_messages, [])

        await runtime.evaluate_idle_proactive_candidates(now=base_time + datetime.timedelta(minutes=8))

        self.assertEqual(len(provider.prompts), 2)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["then we can keep it simple"])
        self.assertEqual(runtime._proactive_idle_candidates, {})
    async def test_proactive_observe_backoff_can_be_bypassed_by_new_messages(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.2, "decision": "observe", '
                '"reason": "不自然", "reply_text": "", "memory_note": ""}',
                '{"should_reply": false, "confidence": 0.2, "decision": "observe", '
                '"reason": "还是不自然", "reply_text": "", "memory_note": ""}',
                '{"should_reply": true, "confidence": 0.9, "decision": "reply", '
                '"reason": "新消息多了，有自然落点", "reply_text": "那就下午场吧", "memory_note": ""}',
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "idle_minutes": 1,
                    "min_confidence": 0.7,
                }
            }
        )
        runtime.archive = DataManager()
        base_time = datetime.datetime(2026, 5, 24, 12, 0)
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "这个展有点纠结。"
        runtime.note_proactive_activity(event, now=base_time)

        await runtime.evaluate_idle_proactive_candidates(now=base_time + datetime.timedelta(minutes=2))
        runtime.note_proactive_activity(event, now=base_time + datetime.timedelta(minutes=3))
        await runtime.evaluate_idle_proactive_candidates(now=base_time + datetime.timedelta(minutes=5))

        runtime.note_proactive_activity(event, now=base_time + datetime.timedelta(minutes=6))
        await runtime.evaluate_idle_proactive_candidates(now=base_time + datetime.timedelta(minutes=8))
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("10001", runtime._proactive_idle_candidates)

        for offset in range(7, 10):
            event.message_str = f"新消息 {offset}"
            runtime.note_proactive_activity(event, now=base_time + datetime.timedelta(minutes=offset))
        await runtime.evaluate_idle_proactive_candidates(now=base_time + datetime.timedelta(minutes=11))

        self.assertEqual(len(provider.prompts), 3)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["那就下午场吧"])
    async def test_proactive_prompt_marks_full_timeline_as_background(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.2, "decision": "observe", '
                '"reason": "当前还在外面吃东西，先不硬接", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "idle_minutes": 1,
                    "min_confidence": 0.7,
                }
            }
        )
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="奶白睡裙",
                timeline=[
                    TimelineItem(time="13:20", activity="坐在雨边长椅吃炸串", status="慵懒满足"),
                    TimelineItem(time="21:00", activity="洗完澡换睡裙准备睡前放松", status="困倦"),
                ],
                state=LifeState(summary="坐在雨边长椅吃炸串，想吃完再溜达"),
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="闲聊群",
        )
        event.message_str = "现在干嘛呢？"
        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 13, 40))

        await runtime.evaluate_idle_proactive_candidates(now=datetime.datetime(2026, 5, 24, 13, 42))

        prompt = provider.prompts[0]
        self.assertIn("此刻日期时间：2026-05-24 13:42", prompt)
        self.assertIn("此刻活动：📍 正在: 坐在雨边长椅吃炸串", prompt)
        self.assertIn("全天日程背景（连续性参考）：", prompt)
        self.assertLess(prompt.index("此刻活动："), prompt.index("全天日程背景（连续性参考）："))
        self.assertIn("21:00 - 洗完澡换睡裙准备睡前放松", prompt)
    async def test_proactive_prompt_uses_learned_expression_and_patterns(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.2, "decision": "observe", '
                '"reason": "先观察", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "idle_minutes": 1,
                    "min_confidence": 0.7,
                }
            }
        )
        await runtime.archive.upsert_expression_profile(
            ExpressionProfileRecord(
                scope="10001",
                profile_id="u1",
                label="阿林",
                tone="轻松短句",
                habits=["先接梗再补一句"],
                avoid=["不要突然长篇"],
                confidence=0.8,
            )
        )
        await runtime.archive.upsert_behavior_pattern(
            BehaviorPatternRecord(
                scope="10001",
                scene="熟人轻吐槽",
                pattern="短句顺着接比解释更自然。",
                suggested_action="reply",
                confidence=0.82,
                last_seen="2026-05-24",
            )
        )
        await runtime.archive.upsert_behavior_scene(
            BehaviorSceneRecord(
                scope="10001",
                scene="等群友补后续",
                cues=["话题还没落稳"],
                preferred_action="wait",
                avoid_action="抢话",
                confidence=0.75,
                last_seen="2026-05-24",
            )
        )
        await runtime.archive.save_reply_effect(
            ReplyEffectRecord(
                scope="aiocqhttp:GroupMessage:10001",
                reply_text="那我先蹲一下",
                outcome="ignored",
                evidence="接话后没人继续",
            )
        )
        await runtime.archive.save_expression_review(
            ExpressionReviewRecord(
                scope="aiocqhttp:GroupMessage:10001",
                passed=False,
                risk="过早接话",
                suggestion="再等一句",
            )
        )
        await runtime.archive.upsert_focus_slot(
            FocusSlotRecord(
                scope="10001",
                focus_key="ticket_followup",
                label="下午场门票",
                priority=72,
                reason="话题还没落稳",
            )
        )
        await runtime.archive.upsert_session_mid_summary(
            SessionMidSummaryRecord(
                session_id="aiocqhttp:GroupMessage:10001",
                scope_label="看展群",
                summary="群里正在聊下午场门票。",
                topic="下午场门票",
                mood="轻松等后续",
                participants=["阿林"],
            )
        )
        await runtime.archive.upsert_temporary_expression_state(
            TemporaryExpressionStateRecord(
                scope="10001",
                label="轻轻围观",
                tone="短句，少解释",
                reason="群里话题还没落稳",
                intensity=68,
            )
        )
        await runtime.archive.upsert_life_term(
            LifeTermRecord(
                term="蹲后续",
                meaning="先看同一话题后面有没有人继续接",
                scope="10001",
                scene="等群友补消息",
                examples=["这个先蹲后续"],
                familiarity=74,
                confidence=0.86,
                last_seen="2026-05-24",
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "下午场好像还行。"
        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        await runtime.evaluate_idle_proactive_candidates(now=datetime.datetime(2026, 5, 24, 12, 2))

        prompt = provider.prompts[0]
        self.assertIn("表达习惯参考", prompt)
        self.assertIn("轻松短句", prompt)
        self.assertIn("行为模式参考", prompt)
        self.assertIn("短句顺着接", prompt)
        self.assertIn("行为场景簇参考", prompt)
        self.assertIn("等群友补后续", prompt)
        self.assertIn("闲时回复效果参考", prompt)
        self.assertIn("接话后没人继续", prompt)
        self.assertIn("表达自然度参考", prompt)
        self.assertIn("过早接话", prompt)
        self.assertIn("自然度复核", prompt)
        self.assertIn("expression_review", prompt)
        self.assertIn("expression_intent", prompt)
        self.assertNotIn("越界", prompt)
        self.assertNotIn("过热", prompt)
        self.assertNotIn("缩回被窝", prompt)
        self.assertIn("短期注意槽", prompt)
        self.assertIn("会话中期摘要", prompt)
        self.assertIn("下午场门票", prompt)
        self.assertIn("此刻表达状态", prompt)
        self.assertIn("轻轻围观", prompt)
        self.assertIn("语言参考", prompt)
        self.assertIn("蹲后续", prompt)
    async def test_proactive_prompt_reads_session_scoped_persona(self):
        persona_manager = PersonaManager(
            prompt="全局默认人设，不应该命中。",
            scoped_prompts={
                "aiocqhttp:GroupMessage:10001": "会话级人设：我是雨天慢悠悠说话的人。",
            },
        )
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.2, "decision": "observe", '
                '"reason": "先观察", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
            persona_manager=persona_manager,
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "group_enabled": True,
                    "idle_minutes": 1,
                    "min_confidence": 0.7,
                }
            }
        )
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
        )
        event.message_str = "雨还挺大的。"
        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        await runtime.evaluate_idle_proactive_candidates(now=datetime.datetime(2026, 5, 24, 12, 2))

        self.assertEqual(persona_manager.calls[-1], "aiocqhttp:GroupMessage:10001")
        prompt = provider.prompts[0]
        self.assertIn("会话级人设：我是雨天慢悠悠说话的人。", prompt)
        self.assertNotIn("全局默认人设，不应该命中。", prompt)
    async def test_proactive_reply_effect_records_positive_feedback(self):
        runtime, _ = self._make_proactive_runtime([])
        key = "10001"
        event = Event(
            sender_name="阿林",
            sender_id="u1",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="看展群",
            message_id="m2",
        )
        event.message_str = "我觉得你说得对。"
        effect = await runtime.archive.save_reply_effect(
            ReplyEffectRecord(
                scope="aiocqhttp:GroupMessage:10001",
                reply_text="那我也觉得",
                outcome="pending",
            )
        )
        runtime._proactive_feedback_watch[key] = {
            "sent_at": datetime.datetime(2026, 5, 24, 12, 0),
            "target_scope": "aiocqhttp:GroupMessage:10001",
            "reason": "闲时续话",
            "reply_effect_id": effect.id,
        }

        await runtime._observe_proactive_reply_effect(event, datetime.datetime(2026, 5, 24, 12, 5))

        feedback = await runtime.archive.get_behavior_feedback(10)
        effects = await runtime.archive.get_reply_effects(10)
        self.assertEqual(feedback[0].result, "positive")
        self.assertEqual(feedback[0].target_id, "10001")
        self.assertEqual(effects[0].outcome, "positive")
        self.assertEqual(runtime._proactive_air_state[key]["last_effect"], "positive")
        self.assertEqual(runtime._proactive_feedback_watch, {})
    async def test_response_gate_relationship_delta_uses_familiarity(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._init_response_gate_state()
        now = datetime.datetime(2026, 5, 24, 12, 0)
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            sender_id="10001",
            sender_name="friend",
            message_id="m-relation",
        )
        event.message_str = "still thinking about that plan"
        for _ in range(8):
            await runtime.archive.touch_relationship(
                "10001",
                name="friend",
                note="recent easy chat",
                date_str="2026-05-24",
                platform="aiocqhttp",
                user_id="10001",
                subjective_name="close friend",
                subjective_tags=["easy", "familiar"],
                relationship_story="We talk casually and often continue the same topic.",
            )

        reasons: list[str] = []
        familiar_delta = await runtime._response_gate_relationship_delta(event, now, reasons)

        self.assertEqual(familiar_delta, 0.18)
        self.assertTrue(reasons)

        unknown = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:20002",
            sender_id="20002",
            sender_name="stranger",
            message_id="m-unknown",
        )
        unknown.message_str = "hello"
        self.assertEqual(await runtime._response_gate_relationship_delta(unknown, now, []), 0.0)
    async def test_response_gate_continues_after_proactive_reply_is_accepted(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._init_response_gate_state()
        runtime.config.response_gate.private_talk_frequency = 0.0
        runtime.config.response_gate.no_reply_backoff_seconds = 0
        scope = "aiocqhttp:FriendMessage:10001"
        key = scope
        accepted = Event(
            unified_msg_origin=scope,
            sender_id="10001",
            sender_name="friend",
            message_id="m-accept",
        )
        accepted.message_str = "accepted, what do you think?"
        runtime._proactive_feedback_watch[key] = {
            "sent_at": datetime.datetime(2026, 5, 24, 12, 0),
            "target_scope": key,
            "reason": "proactive",
        }

        runtime.note_proactive_activity(accepted, now=datetime.datetime(2026, 5, 24, 12, 1))
        decision = await runtime.evaluate_response_gate(
            accepted,
            now=datetime.datetime(2026, 5, 24, 12, 1),
        )

        self.assertEqual(decision["action"], "reply")
        self.assertTrue(decision["forced"])
        self.assertFalse(accepted.call_llm)

        follow = Event(
            unified_msg_origin=scope,
            sender_id="10001",
            sender_name="friend",
            message_id="m-follow",
        )
        follow.message_str = "follow up"

        follow_decision = await runtime.evaluate_response_gate(
            follow,
            now=datetime.datetime(2026, 5, 24, 12, 2),
        )

        self.assertEqual(follow_decision["action"], "reply")
        self.assertTrue(follow_decision["forced"])
        self.assertFalse(follow.call_llm)

        third = Event(
            unified_msg_origin=scope,
            sender_id="10001",
            sender_name="friend",
            message_id="m-third",
        )
        third.message_str = "third"

        third_decision = await runtime.evaluate_response_gate(
            third,
            now=datetime.datetime(2026, 5, 24, 12, 3),
        )

        self.assertEqual(third_decision["action"], "observe")
        self.assertFalse(third.call_llm)

        live = Event(
            unified_msg_origin=scope,
            sender_id="10001",
            sender_name="friend",
            message_id="m-live",
        )
        live.message_str = "live"
        live_now = datetime.datetime.now()
        runtime._response_gate_mark_continuation(scope, live_now, reason="live continuation")

        live_decision = await runtime.apply_response_gate_for_event(live)

        self.assertEqual(live_decision["action"], "reply")
        self.assertFalse(live.call_llm)
    async def test_response_gate_does_not_continue_after_cold_proactive_ack(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._init_response_gate_state()
        runtime.config.response_gate.private_talk_frequency = 0.0
        runtime.config.response_gate.no_reply_backoff_seconds = 0
        scope = "aiocqhttp:FriendMessage:10001"
        event = Event(
            unified_msg_origin=scope,
            sender_id="10001",
            sender_name="friend",
            message_id="m-cold",
        )
        event.message_str = "ok"
        runtime._proactive_feedback_watch[scope] = {
            "sent_at": datetime.datetime(2026, 5, 24, 12, 0),
            "target_scope": scope,
            "reason": "proactive",
        }

        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 12, 1))
        decision = await runtime.evaluate_response_gate(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 1),
        )

        self.assertEqual(decision["action"], "observe")
        self.assertFalse(event.call_llm)
    async def test_response_gate_uses_reply_effects_and_experience(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._init_response_gate_state()
        runtime.config.response_gate.private_talk_frequency = 0.4
        scope = "aiocqhttp:FriendMessage:10001"
        event = Event(
            unified_msg_origin=scope,
            sender_id="10001",
            sender_name="阿林",
            message_id="m-effect",
        )
        event.message_str = "刚才那个话题我还想继续聊"
        await runtime.archive.save_reply_effect(
            ReplyEffectRecord(
                scope=scope,
                reply_text="那我接着说一句",
                outcome="positive",
                warmth=85,
                continuity=90,
            )
        )
        await runtime.archive.upsert_behavior_scene(
            BehaviorSceneRecord(
                scope=scope,
                scene="熟人轻松续聊",
                preferred_action="reply",
                confidence=0.9,
                support_count=3,
            )
        )

        score, reasons = runtime._response_gate_score(
            event,
            LifeState(),
            pending_count=1,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )
        score += await runtime._response_gate_feedback_delta(scope, reasons)
        score += await runtime._response_gate_experience_delta(scope, event, reasons)

        self.assertGreater(score, 0.5)
        self.assertIn("近期接话反馈偏暖", reasons)
        self.assertIn("过往场景更适合接话", reasons)
    async def test_response_gate_batch_pressure_and_presence_penalty(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._init_response_gate_state()
        runtime.config.response_gate.private_talk_frequency = 0.4
        scope = "aiocqhttp:FriendMessage:10001"
        now = datetime.datetime(2026, 5, 24, 12, 0)
        event = Event(unified_msg_origin=scope, sender_id="10001", message_id="m-batch")
        event.message_str = "补充一句"
        runtime._response_gate_first_seen_at[scope] = now - datetime.timedelta(seconds=120)

        single_score, _ = runtime._response_gate_score(event, LifeState(), pending_count=1, now=now)
        batch_score, batch_reasons = runtime._response_gate_score(event, LifeState(), pending_count=3, now=now)

        self.assertGreater(batch_score, single_score)
        self.assertIn("这轮已经攒了几条消息", batch_reasons)

        runtime._response_gate_reply_times[scope] = [
            now - datetime.timedelta(seconds=240),
            now - datetime.timedelta(seconds=120),
            now - datetime.timedelta(seconds=30),
        ]
        penalized_score, penalty_reasons = runtime._response_gate_score(event, LifeState(), pending_count=3, now=now)

        self.assertLess(penalized_score, batch_score)
        self.assertIn("最近已经连续接了好几轮", penalty_reasons)
    async def test_proactive_silence_inertia_and_send_timing(self):
        runtime, _ = self._make_proactive_runtime([])
        key = "10001"
        now = datetime.datetime(2026, 5, 24, 12, 0)

        runtime._update_proactive_air_after_decision(
            key,
            {"decision": "observe", "reason": "话题还没落稳"},
            now,
            sent=False,
        )
        runtime._update_proactive_air_after_decision(
            key,
            {"decision": "wait", "reason": "等对方补一句"},
            now,
            sent=False,
        )

        state_text = runtime._format_proactive_air_state(key, now)
        self.assertIn("沉默惯性", state_text)
        self.assertIn("等对方补一句", state_text)
        self.assertEqual(runtime._proactive_send_delay_seconds({"send_timing": {"delay_seconds": 30}}), 12.0)
        self.assertEqual(runtime._proactive_send_delay_seconds({"send_timing": {"delay_seconds": "3.5"}}), 3.5)
        self.assertEqual(runtime._proactive_send_delay_seconds({}), 0.0)

        with patch("core.runtime.style.random.uniform", return_value=1.25):
            self.assertEqual(runtime._proactive_send_delay_seconds({"reply_text": "我听见了，不过现在真的快睡着了"}), 1.25)

    async def test_proactive_send_uses_plugin_natural_segments_when_framework_disabled(self):
        runtime, _ = self._make_proactive_runtime([])

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "刚洗完脸，头发还没干。被你一问又精神了，本来都准备把手机扣下了。",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertEqual(runtime.context.sent_messages[0][0], "aiocqhttp:FriendMessage:10001")
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["刚洗完脸，头发还没干。"])
        self.assertEqual(runtime.context.sent_messages[1][1].items, ["被你一问又精神了，本来都准备把手机扣下了。"])
        self._assert_last_assistant_history(
            runtime,
            "aiocqhttp:FriendMessage:10001",
            "刚洗完脸，头发还没干。被你一问又精神了，本来都准备把手机扣下了。",
        )
    async def test_proactive_send_uses_short_lead_delay_between_segments(self):
        runtime, _ = self._make_proactive_runtime([])
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        with patch("core.runtime.style.random.uniform", return_value=0.92), patch(
            "core.runtime.proactive.segment.asyncio.sleep",
            fake_sleep,
        ):
            sent = await runtime._send_proactive_message(
                "aiocqhttp:FriendMessage:10001",
                "你人呢，\n我看到前面好像有一家点心铺，\n快来帮我看看是不是这家。",
                "闲时回复发送失败",
            )

        self.assertTrue(sent)
        self.assertEqual(sleeps, [0.92])
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["你人呢"])
        self.assertEqual(runtime.context.sent_messages[1][1].items, ["我看到前面好像有一家点心铺，快来帮我看看是不是这家。"])
    async def test_proactive_send_ignores_framework_segmented_reply_config(self):
        runtime, _ = self._make_proactive_runtime(
            [],
            context_config={
                "platform_settings": {
                    "segmented_reply": {
                        "enable": True,
                        "only_llm_result": True,
                        "interval_method": "random",
                        "interval": "0,0",
                        "words_count_threshold": 100,
                        "split_mode": "regex",
                        "regex": r".*?[。？！~…]+|.+$",
                        "content_cleanup_rule": "",
                    }
                }
            },
        )

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "刚洗完脸，头发还没干。被你一问又精神了，本来都准备把手机扣下了。",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["刚洗完脸，头发还没干。"])
        self.assertEqual(runtime.context.sent_messages[1][1].items, ["被你一问又精神了，本来都准备把手机扣下了。"])
        self._assert_last_assistant_history(
            runtime,
            "aiocqhttp:FriendMessage:10001",
            "刚洗完脸，头发还没干。被你一问又精神了，本来都准备把手机扣下了。",
        )
    async def test_proactive_send_keeps_single_message_without_natural_breaks(self):
        runtime, _ = self._make_proactive_runtime([])

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "then we can keep it simple",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["then we can keep it simple"])
    async def test_private_revisit_uses_plugin_natural_segments_without_framework_config(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": true, "confidence": 0.9, "decision": "reply", '
                '"reason": "街边找店很自然", '
                '"reply_text": "你人呢，\\n我看到前面好像有一家点心铺，\\n快来帮我看看是不是这家。", '
                '"memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                    "max_reply_length": 120,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_private_last_revisit_at = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            note="刚聊到一起找点心铺",
            date_str="2026-05-24",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="friend",
            target_scope="aiocqhttp:FriendMessage:10001",
        )

        await runtime.evaluate_private_revisit_candidates()

        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["你人呢"])
        self.assertEqual(runtime.context.sent_messages[1][1].items, ["我看到前面好像有一家点心铺，快来帮我看看是不是这家。"])
        self._assert_last_assistant_history(
            runtime,
            "aiocqhttp:FriendMessage:10001",
            "你人呢，\n我看到前面好像有一家点心铺，\n快来帮我看看是不是这家。",
        )
    async def test_emoji_selection_requires_model_even_for_clear_local_rank(self):
        runtime, provider = self._make_proactive_runtime([], provider_id="proactive-model")
        runtime.composer._get_provider = lambda provider_id="": async_return(None)
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="sad-one",
                file_path="https://example.com/sad.png",
                label="委屈小表情",
                emotions=["category:sad", "委屈"],
                status="ready",
            )
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="happy-one",
                file_path="https://example.com/happy.png",
                label="开心小表情",
                emotions=["category:happy", "开心"],
                status="ready",
            )
        )

        emoji = await runtime._select_emoji_asset_for_intent(
            {"emotion_category": "sad", "emotion": "有点委屈", "send_emoji": True}
        )

        self.assertIsNone(emoji)
        self.assertEqual(provider.prompts, [])
    async def test_emoji_selection_does_not_use_hardcoded_emotion_aliases(self):
        runtime, provider = self._make_proactive_runtime([], provider_id="proactive-model")
        runtime.composer._get_provider = lambda provider_id="": async_return(None)
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="happy-cn-only",
                file_path="https://example.com/happy-cn.png",
                label="开心小表情",
                emotions=["开心"],
                status="ready",
            )
        )

        emoji = await runtime._select_emoji_asset_for_intent(
            {"emotion_category": "happy", "emotion": "轻松", "send_emoji": True}
        )

        self.assertIsNone(emoji)
        self.assertEqual(provider.prompts, [])
    async def test_emoji_selection_skips_ready_but_not_sendable_asset(self):
        runtime, provider = self._make_proactive_runtime([], provider_id="proactive-model")
        runtime.composer._get_provider = lambda provider_id="": async_return(None)
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="blocked-one",
                file_path="https://example.com/blocked.png",
                label="探头",
                emotions=["好奇", "围观"],
                status="ready",
                sendable=False,
            )
        )

        emoji = await runtime._select_emoji_asset_for_intent(
            {"emotion": "好奇", "emoji_intent": "围观", "send_emoji": True}
        )

        self.assertIsNone(emoji)
        self.assertEqual(provider.prompts, [])
    async def test_emoji_selection_uses_asset_description_terms(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"emoji_id": 1, "reason": "描述更贴近围观探头"}'],
            provider_id="proactive-model",
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="peek-from-description",
                file_path="https://example.com/peek-description.png",
                label="白色小人",
                description="适合偷偷围观、探头看一眼时使用",
                emotions=["日常"],
                status="ready",
            )
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="plain-one",
                file_path="https://example.com/plain.png",
                label="普通日常",
                description="普通打招呼",
                emotions=["日常"],
                status="ready",
            )
        )

        emoji = await runtime._select_emoji_asset_for_intent(
            {"emotion": "有点好奇", "emoji_intent": "围观一下", "send_emoji": True}
        )

        self.assertIsNotNone(emoji)
        self.assertEqual(emoji.file_hash, "peek-from-description")
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("候选表情", provider.prompts[0])
    async def test_emoji_selection_asks_model_only_for_close_candidates(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"emoji_id": 2, "reason": "第二个更贴近"}'],
            provider_id="proactive-model",
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=1,
                file_hash="curious-one",
                file_path="https://example.com/one.png",
                label="好奇一号",
                emotions=["好奇", "围观"],
                status="ready",
            )
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=2,
                file_hash="curious-two",
                file_path="https://example.com/two.png",
                label="好奇二号",
                emotions=["好奇", "围观"],
                status="ready",
            )
        )

        emoji = await runtime._select_emoji_asset_for_intent(
            {"emotion": "好奇", "emoji_intent": "围观", "send_emoji": True}
        )

        self.assertIsNotNone(emoji)
        self.assertEqual(emoji.file_hash, "curious-two")
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("候选表情", provider.prompts[0])
    async def test_emoji_selection_samples_close_candidates_for_model(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"emoji_id": 12, "reason": "这张更自然"}'],
            provider_id="proactive-model",
        )
        for index in range(1, 13):
            await runtime.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    id=index,
                    file_hash=f"curious-{index}",
                    file_path=f"https://example.com/curious-{index}.png",
                    label=f"围观{index}",
                    emotions=["好奇", "围观"],
                    status="ready",
                )
            )
        sample_calls = []

        def fake_sample(items, count):
            values = list(items)
            sample_calls.append(([int(getattr(item, "id", 0) or 0) for item in values], count))
            return values[-count:]

        with patch("core.runtime.proactive.gesture.random.sample", side_effect=fake_sample):
            emoji = await runtime._select_emoji_asset_for_intent(
                {"emotion": "好奇", "emoji_intent": "围观", "send_emoji": True}
            )

        self.assertIsNotNone(emoji)
        self.assertEqual(emoji.file_hash, "curious-12")
        self.assertEqual(sample_calls, [([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], 8)])
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn('"id": 12', provider.prompts[0])
        self.assertNotIn('"id": 4', provider.prompts[0])
    async def test_emoji_selection_does_not_randomize_without_provider(self):
        runtime, provider = self._make_proactive_runtime([], provider_id="proactive-model")
        runtime.composer._get_provider = lambda provider_id="": async_return(None)
        for index in range(1, 4):
            await runtime.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    id=index,
                    file_hash=f"peek-{index}",
                    file_path=f"https://example.com/peek-{index}.png",
                    label=f"探头{index}",
                    emotions=["探头", "围观"],
                    status="ready",
                )
            )

        emoji = await runtime._select_emoji_asset_for_intent(
            {"emotion": "探头", "emoji_intent": "围观", "send_emoji": True}
        )

        self.assertIsNone(emoji)
        self.assertEqual(provider.prompts, [])
    async def test_emoji_selection_uses_recent_expression_intents(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"emoji_id": 1, "reason": "最近表达意图指向探头围观"}'],
            provider_id="proactive-model",
        )
        scope = "aiocqhttp:FriendMessage:10001"
        await runtime.archive.save_expression_intent(
            {
                "scope": scope,
                "emotion": "轻轻围观",
                "emoji_intent": "探头",
                "action_intent": "看一眼",
                "source": "proactive_reply",
            }
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="peek-one",
                file_path="https://example.com/peek.png",
                label="探头围观",
                emotions=["探头", "围观"],
                status="ready",
            )
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="plain-one",
                file_path="https://example.com/plain.png",
                label="普通日常",
                emotions=["日常"],
                status="ready",
            )
        )

        emoji = await runtime._select_emoji_asset_for_intent({"emotion": "有点想接话", "send_emoji": True}, scope=scope)

        self.assertIsNotNone(emoji)
        self.assertEqual(emoji.file_hash, "peek-one")
        self.assertEqual(len(provider.prompts), 1)
    async def test_life_emoji_send_does_not_fallback_to_recent_asset(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"emoji_id": 0, "reason": "语义判断没有明确候选"}'],
            provider_id="proactive-model",
        )
        scope = "aiocqhttp:FriendMessage:10001"
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=1,
                file_hash="clown-one",
                file_path="https://example.com/clown.png",
                label="小丑自嘲",
                description="适合自嘲和尴尬微笑",
                emotions=["自嘲", "尴尬", "无奈"],
                source_scope=scope,
                status="ready",
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin=scope,
            message_id="m-send-emoji",
        )
        event.message_str = "那你能发这个表情包吗"

        result = await runtime.life_emoji_send(
            event,
            intent="发送一张开心表情",
            emotion="轻松递上",
            emotion_category="happy",
            decision_reason="用户想要一张开心表情",
        )

        self.assertEqual(result, "没有找到可发送的表情素材。")
        self.assertEqual(runtime.context.sent_messages, [])
        assets = await runtime.archive.get_emoji_assets(10, status="ready")
        self.assertEqual(assets[0].used_count, 0)
        intents = await runtime.archive.get_expression_intents(10, scope=scope)
        self.assertEqual(intents, [])
        self.assertEqual(provider.prompts, [])
    async def test_proactive_emoji_sends_from_expression_intent_without_cooldown(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"emoji_id": 1, "reason": "轻松偷笑适合"}',
                '{"emoji_id": 2, "reason": "惊讶瞪眼适合"}',
            ],
            provider_id="proactive-model",
        )
        scope = "aiocqhttp:FriendMessage:10001"
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=1,
                file_hash="smile-one",
                file_path="https://example.com/smile.png",
                label="偷笑",
                emotions=["轻松开心", "偷笑一下"],
                status="ready",
            )
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=2,
                file_hash="wow-one",
                file_path="https://example.com/wow.png",
                label="瞪眼",
                emotions=["惊讶", "惊讶瞪眼"],
                status="ready",
            )
        )

        await runtime._send_proactive_emoji_if_needed(
            scope,
            {
                "expression_intent": {
                    "send_emoji": True,
                    "emotion": "轻松开心",
                    "emotion_category": "happy",
                    "emoji_intent": "偷笑一下",
                    "action_intent": "顺手偷笑",
                    "reason": "主动回复本身需要一个轻松表情动作",
                }
            },
            source_event=Event(unified_msg_origin=scope, message_id="m-proactive-1"),
            source_message_id="m-proactive-1",
        )
        await runtime._send_proactive_emoji_if_needed(
            scope,
            {
                "expression_intent": {
                    "send_emoji": True,
                    "emotion": "惊讶",
                    "emotion_category": "neutral",
                    "emoji_intent": "惊讶瞪眼",
                    "action_intent": "瞪大眼睛",
                    "reason": "下一次主动表达也明确需要表情动作",
                }
            },
            source_event=Event(unified_msg_origin=scope, message_id="m-proactive-2"),
            source_message_id="m-proactive-2",
        )

        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertEqual(
            runtime.context.sent_messages[0][1].items,
            [{"type": "image", "url": "https://example.com/smile.png"}],
        )
        self.assertEqual(
            runtime.context.sent_messages[1][1].items,
            [{"type": "image", "url": "https://example.com/wow.png"}],
        )
        self.assertEqual(len(provider.prompts), 2)

    async def test_proactive_emoji_skips_same_asset_for_same_source_message(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"emoji_id": 1, "reason": "适合"}',
                '{"emoji_id": 1, "reason": "仍然适合"}',
            ],
            provider_id="proactive-model",
        )
        scope = "aiocqhttp:FriendMessage:10001"
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=1,
                file_hash="smile-one",
                file_path="https://example.com/smile.png",
                label="偷笑",
                emotions=["开心", "偷笑"],
                status="ready",
            )
        )
        payload = {
            "expression_intent": {
                "send_emoji": True,
                "emotion": "开心",
                "emotion_category": "happy",
                "emoji_intent": "偷笑",
                "action_intent": "轻轻偷笑",
                "reason": "这轮表达需要表情",
            }
        }
        event = Event(unified_msg_origin=scope, message_id="m-proactive-same")

        await runtime._send_proactive_emoji_if_needed(
            scope,
            payload,
            source_event=event,
            source_message_id="m-proactive-same",
        )
        await runtime._send_proactive_emoji_if_needed(
            scope,
            payload,
            source_event=event,
            source_message_id="m-proactive-same",
        )

        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(len(provider.prompts), 2)

    async def test_life_emoji_send_skips_same_asset_for_same_source_message(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"emoji_id": 1, "reason": "适合"}',
                '{"emoji_id": 1, "reason": "仍然适合"}',
            ],
            provider_id="proactive-model",
        )
        scope = "aiocqhttp:FriendMessage:10001"
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=1,
                file_hash="smile-one",
                file_path="https://example.com/smile.png",
                label="偷笑",
                emotions=["开心", "偷笑"],
                status="ready",
            )
        )
        event = Event(unified_msg_origin=scope, message_id="m-tool-emoji")
        base = datetime.datetime(2026, 5, 24, 12, 0)
        with patch("core.runtime.proactive.gesture.life_now", return_value=base):
            result = await runtime.life_emoji_send(
                event,
                intent="偷笑",
                emotion="开心",
                emotion_category="happy",
                decision_reason="用户明确要一个轻松表情",
            )

        self.assertEqual(result, "表情已发送：偷笑")
        self.assertEqual(len(runtime.context.sent_messages), 1)

        repeated = await runtime.life_emoji_send(
            event,
            intent="偷笑",
            emotion="开心",
            emotion_category="happy",
            decision_reason="同一条消息不重复发同一张",
        )

        self.assertEqual(repeated, "同一轮已发送过这个表情")
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(len(provider.prompts), 2)
    async def test_private_candidate_is_removed_after_normal_reply(self):
        runtime, _ = self._make_proactive_runtime([])
        runtime._proactive_idle_candidates = {}
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            platform_name="aiocqhttp",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "刚才那个设定我觉得还挺适合继续写。"

        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 12, 0))
        self.assertIn("aiocqhttp:FriendMessage:10001", runtime._proactive_idle_candidates)

        runtime.note_proactive_bot_reply(event, now=datetime.datetime(2026, 5, 24, 12, 1))

        self.assertEqual(runtime._proactive_idle_candidates, {})
    async def test_private_revisit_sends_message_to_private_target(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": true, "confidence": 0.9, "decision": "reply", '
                '"reason": "关系里有自然回访点", "reply_text": "刚想起你上次说的那个展，后来有新进展吗？", '
                '"memory_note": "主动回访看展话题"}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_last_reply_at = {}
        runtime._proactive_private_last_revisit_at = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            note="上次聊到想去看展",
            date_str="2026-05-24",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="friend",
            target_scope="aiocqhttp:FriendMessage:10001",
            persona_hint="男生，喜欢看展",
        )

        await runtime.evaluate_private_revisit_candidates()

        self.assertEqual(len(runtime.context.sent_messages), 1)
        target, chain = runtime.context.sent_messages[0]
        self.assertEqual(target, "aiocqhttp:FriendMessage:10001")
        self.assertEqual(chain.items, ["刚想起你上次说的那个展，后来有新进展吗？"])
        self._assert_last_assistant_history(
            runtime,
            "aiocqhttp:FriendMessage:10001",
            "刚想起你上次说的那个展，后来有新进展吗？",
        )
        inserted = runtime.context.message_history_manager.inserts[-1]
        self.assertEqual(inserted.platform_id, "aiocqhttp")
        self.assertEqual(inserted.user_id, "10001")
        self.assertEqual(inserted.sender_id, "assistant")
        self.assertEqual(inserted.content["type"], "assistant")
        self.assertEqual(inserted.content["message"], [{"type": "plain", "text": "刚想起你上次说的那个展，后来有新进展吗？"}])
        self.assertEqual(inserted.content["text"], "刚想起你上次说的那个展，后来有新进展吗？")
        self.assertIn("角色人设摘要", provider.prompts[0])
        self.assertIn("一个喜欢看展的人", provider.prompts[0])
        self.assertIn("隐藏上下文规则", provider.prompts[0])
        self.assertIn("current_role=当前角色", provider.prompts[0])
        self.assertIn("speaker=消息发送者", provider.prompts[0])
        self.assertIn("perspective=当前角色第一人称", provider.prompts[0])
        self.assertIn("message_owner=speaker", provider.prompts[0])
        self.assertIn("输出格式=JSON 对象本体", provider.prompts[0])
        self.assertNotIn("隐藏推理也必须站在“我”的角色视角判断", provider.prompts[0])
        self.assertNotIn("服务端隐藏推理的第一句必须以“我”开头", provider.prompts[0])
        self.assertNotIn("不得把内心独白", provider.prompts[0])
        self.assertNotIn("服务端隐藏推理必须像我的内心独白", provider.prompts[0])
        self.assertNotIn("不要写成审题报告、旁观说明、系统记录", provider.prompts[0])
        self.assertIn("current_role=当前角色", provider.system_prompts[0])
        self.assertIn("speaker=消息发送者", provider.system_prompts[0])
        self.assertIn("perspective=当前角色第一人称", provider.system_prompts[0])
        self.assertNotIn("隐藏推理第一句必须从“我”开始", provider.system_prompts[0])
        self.assertNotIn("禁止把隐藏推理", provider.system_prompts[0])
        self.assertNotIn("我们分析当前情况", provider.prompts[0])
        self.assertNotIn("我们分析当前情况", provider.system_prompts[0])
        self.assertIn("人物称谓与性别规则", provider.prompts[0])
        self.assertIn("人设线索：男生，喜欢看展", provider.prompts[0])
        self.assertIn("自然度复核", provider.prompts[0])
        self.assertIn("回访依据", provider.prompts[0])
        self.assertIn("聊天表达设置", provider.prompts[0])
        self.assertIn("私聊主动消息参考长度", provider.prompts[0])
        self.assertIn("自然打字等待", provider.prompts[0])
        self.assertNotIn("越界", provider.prompts[0])
        self.assertNotIn("缩回被窝", provider.prompts[0])
        self.assertNotIn("自我审查", provider.prompts[0])
        self.assertLess(provider.prompts[0].index("JSON 输出要求"), provider.prompts[0].index("【眼前内容】"))
        self.assertGreater(provider.prompts[0].index("一个喜欢看展的人"), provider.prompts[0].index("【眼前内容】"))
    async def test_private_revisit_respects_chat_style_length_budget(self):
        runtime, provider = self._make_proactive_runtime(
            [
                json.dumps(
                    {
                        "should_reply": True,
                        "confidence": 0.95,
                        "decision": "reply",
                        "reason": "关系里有自然回访点",
                        "reply_text": "刚才那张图你是认真的吗，我看那厚度顶多也就是你半个钱包的距离。",
                        "memory_note": "主动回访图片话题",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "casual_max_chars": 10,
                    "private_casual_max_chars": 10,
                    "proactive_max_chars": 10,
                },
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                },
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_last_reply_at = {}
        runtime._proactive_private_last_revisit_at = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            note="刚才发了一张调侃图片",
            date_str="2026-05-24",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="friend",
            target_scope="aiocqhttp:FriendMessage:10001",
        )

        await runtime.evaluate_private_revisit_candidates()

        self.assertEqual(runtime.context.sent_messages, [])
        self.assertIn("聊天表达设置", provider.prompts[0])
        self.assertIn("私聊主动消息参考长度约 10 字左右", provider.prompts[0])
    async def test_private_revisit_prompt_includes_recent_private_context(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.3, "decision": "observe", '
                '"reason": "没有自然落点", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        target = "aiocqhttp:FriendMessage:10001"
        runtime.context.conversation_manager.conversations[target] = type(
            "Conversation",
            (),
            {
                "history": [
                    {"role": "user", "content": "我这两天在改设定。", "name": "阿林"},
                    {"role": "assistant", "content": "那你慢慢来，不急。"},
                ]
            },
        )()
        runtime.context.conversation_manager.current_ids[target] = "current"
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_private_last_revisit_at = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            note="最近在改设定",
            date_str="2026-05-24",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="friend",
            target_scope=target,
        )

        await runtime.evaluate_private_revisit_candidates()

        prompt = provider.prompts[0]
        self.assertIn("回访依据", prompt)
        self.assertIn("当前锚点：近期私聊", prompt)
        self.assertIn("近期私聊：当前", prompt)
        self.assertIn("近期私聊片段", prompt)
        self.assertIn("阿林: 我这两天在改设定。", prompt)
        self.assertIn("我: 那你慢慢来，不急。", prompt)
    async def test_private_revisit_uses_recent_chat_as_anchor_before_memos(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.3, "decision": "observe", '
                '"reason": "最近是睡前拍照收尾，旧水果记忆不该单独拉回当前场景", '
                '"reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )

        class FakeMemOSClient:
            def __init__(self):
                self.available = True
                self.search_payloads = []

            async def search_memory(self, payload):
                self.search_payloads.append(payload)
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": {"memory_detail_list": [{"memory_value": "旧水果记忆：我切好水果喊对方出来吃"}]},
                        "error": "",
                    },
                )()

        target = "aiocqhttp:FriendMessage:10001"
        runtime.context.conversation_manager.conversations[target] = type(
            "Conversation",
            (),
            {
                "history": [
                    {"role": "user", "content": "睡了吗？拍张照看看", "name": "阿林"},
                    {"role": "assistant", "content": "照片都给你看了，看完赶紧睡。"},
                ]
            },
        )()
        runtime.context.conversation_manager.current_ids[target] = "current"
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                },
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                },
            }
        )
        runtime.memos = HostedMemOSService(runtime.config.memos)
        runtime.memos.client = FakeMemOSClient()
        runtime.archive = DataManager()
        runtime._proactive_private_last_revisit_at = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            note="之前有过切水果的小片段",
            date_str="2026-06-22",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="friend",
            target_scope=target,
        )

        await runtime.evaluate_private_revisit_candidates()

        prompt = provider.prompts[0]
        self.assertIn("回访依据", prompt)
        self.assertIn("当前锚点：近期私聊", prompt)
        self.assertIn("近期私聊：当前", prompt)
        self.assertIn("外部长期记忆：背景", prompt)
        self.assertIn("外部长期记忆参考", prompt)
        self.assertLess(prompt.index("近期私聊片段"), prompt.index("外部长期记忆参考"))
        self.assertIn("阿林: 睡了吗？拍张照看看", prompt)
        self.assertIn("我: 照片都给你看了，看完赶紧睡。", prompt)
        self.assertIn("旧水果记忆：我切好水果喊对方出来吃", prompt)
        payload = runtime.memos.client.search_payloads[0]
        self.assertIn("睡了吗？拍张照看看", payload["query"])
        self.assertIn("照片都给你看了", payload["query"])
        self.assertNotEqual(payload["query"], "阿林")
    async def test_private_revisit_does_not_query_memos_without_recent_chat(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.3, "decision": "observe", '
                '"reason": "没有最近真实互动支撑", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )

        class FakeMemOSClient:
            def __init__(self):
                self.available = True
                self.search_payloads = []

            async def search_memory(self, payload):
                self.search_payloads.append(payload)
                return type("Result", (), {"success": True, "data": {}, "error": ""})()

        target = "aiocqhttp:FriendMessage:10001"
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                },
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                },
            }
        )
        runtime.memos = HostedMemOSService(runtime.config.memos)
        runtime.memos.client = FakeMemOSClient()
        runtime.archive = DataManager()
        runtime._proactive_private_last_revisit_at = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            note="旧记忆里有切水果片段",
            date_str="2026-06-22",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="friend",
            target_scope=target,
        )

        await runtime.evaluate_private_revisit_candidates()

        self.assertEqual(runtime.memos.client.search_payloads, [])
        self.assertIn("暂无外部长期记忆参考。", provider.prompts[0])
    async def test_private_revisit_prompt_marks_saved_pronouns_as_non_gender_evidence(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.3, "decision": "observe", '
                '"reason": "没有自然落点", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        target = "aiocqhttp:FriendMessage:10001"
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_private_last_revisit_at = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="小林",
            note="最近聊过看展",
            date_str="2026-05-24",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="friend",
            target_scope=target,
            relationship_story="她平时会记得我想去看展。",
        )

        await runtime.evaluate_private_revisit_candidates()

        prompt = provider.prompts[0]
        self.assertIn("人设线索：无", prompt)
        self.assertIn("称谓边界：人设线索优先", prompt)
        self.assertIn("关系叙事或最近印象里零散出现的他/她不能当作性别依据", prompt)
        self.assertIn("关系叙事：她平时会记得我想去看展。", prompt)
    async def test_private_revisit_uses_complete_session_persona_text(self):
        late_hint = "林远是我的男死党，平时我叫他小林，关系是纯友谊。"
        persona = "。".join([f"普通人设片段{i}" for i in range(80)]) + "。" + late_hint
        persona_manager = PersonaManager(
            prompt="全局默认人设",
            scoped_prompts={"aiocqhttp:FriendMessage:10001": persona},
        )
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": false, "confidence": 0.3, "decision": "observe", '
                '"reason": "没有自然落点", "reply_text": "", "memory_note": ""}'
            ],
            provider_id="proactive-model",
            persona_manager=persona_manager,
        )
        target = "aiocqhttp:FriendMessage:10001"
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_private_last_revisit_at = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="小林",
            note="最近聊过雨天出门",
            date_str="2026-05-24",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="friend",
            target_scope=target,
        )

        await runtime.evaluate_private_revisit_candidates()

        self.assertEqual(persona_manager.calls[-1], target)
        prompt = provider.prompts[0]
        self.assertIn(late_hint, prompt)
        self.assertGreater(prompt.index(late_hint), prompt.index("角色人设摘要"))
    async def test_private_revisit_skips_group_only_relationship(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": true, "confidence": 0.9, "decision": "reply", '
                '"reason": "有回访点", "reply_text": "还在忙那个宏吗？", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_private_last_revisit_at = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="小丑",
            note="凌晨在群里问过魔兽宏",
            date_str="2026-06-19",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="group_member",
            group_id="20001",
            group_name="游戏群",
        )

        await runtime.evaluate_private_revisit_candidates()

        self.assertEqual(provider.prompts, [])
        self.assertEqual(runtime.context.sent_messages, [])
    async def test_private_revisit_marks_friend_contact_unreachable_after_not_friend_error(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": true, "confidence": 0.9, "decision": "reply", '
                '"reason": "有回访点", "reply_text": "还在忙那个宏吗？", "memory_note": ""}'
            ],
            provider_id="proactive-model",
        )
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {
                    "enabled": True,
                    "provider": "proactive-model",
                    "private_revisit_enabled": True,
                    "revisit_min_confidence": 0.8,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._proactive_private_last_revisit_at = {}
        target = "aiocqhttp:FriendMessage:10001"
        runtime.context.send_failures[target] = RuntimeError("发送失败，请先添加对方为好友")
        await runtime.archive.touch_relationship(
            "10001",
            name="小丑",
            note="凌晨问过魔兽宏",
            date_str="2026-06-19",
            platform="aiocqhttp",
            user_id="10001",
            contact_type="friend",
            target_scope=target,
        )

        await runtime.evaluate_private_revisit_candidates()

        contacts = await runtime.archive.get_reachable_relationship_contacts("10001", contact_type="friend")
        self.assertEqual(contacts, [])
        profile = (await runtime.archive.get_recent_relationships(1))[0]
        self.assertFalse(profile.contacts[0].is_reachable)
        self.assertEqual(profile.contacts[0].blocked_reason, "不是好友或当前不可私聊")
