import asyncio
import base64
import datetime
import os
import random
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from support import (
    AltProvider,
    Context,
    DataManager,
    Event,
    LifeSettings,
    PersonaManager,
    Provider,
    ProviderRequest,
    DailyLifeRuntime,
    async_return,
)
from core.models import (
    BehaviorSceneRecord,
    BehaviorPatternRecord,
    CommitmentRecord,
    EmojiAssetRecord,
    ExpressionReviewRecord,
    ExpressionProfileRecord,
    FocusSlotRecord,
    LifeTermRecord,
    MemoryCorrectionRecord,
    ReplyEffectRecord,
    SessionMidSummaryRecord,
    TemporaryExpressionStateRecord,
)
from core.models import DayRecord, LifeState, TimelineItem, WeatherInfo
from core.prompts import (
    CORE_INTERNAL_SYSTEM_PROMPT,
    CORE_REASONING_ANTI_PATTERN_RULE,
    CORE_REASONING_FORBIDDEN_PATTERNS,
)
from core.media import GeminiImageService, SiliconFlowVoiceService
from core.memos import HostedMemOSService
from core.runtime.background import BackgroundTaskScheduler
from core.runtime.director import MediaPromptExtractionError


def image_generation_config(api_key: str = "image-key", **overrides):
    channel_overrides = {}
    for key in ("resolution", "aspect_ratio", "timeout_seconds"):
        if key in overrides:
            channel_overrides[key] = overrides.pop(key)
    config = {
        "enabled": True,
        "channels": [
            {
                "__template_key": "gemini",
                "api_url": "https://image.example",
                "api_key": api_key,
                "model": "gemini-3-pro-image-preview",
                **channel_overrides,
            }
        ],
    }
    config.update(overrides)
    return config


class LifeRuntimeTest(unittest.TestCase):
    def _response_gate_runtime(self, config=None, state=None):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(config or {})
        runtime.context = Context(Provider([]))
        runtime._init_response_gate_state()
        day = DayRecord(date="2026-05-24", state=state or LifeState())

        class Archive:
            async def get_day(self, date):
                return day

        runtime.archive = Archive()
        return runtime

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
        self.assertIn("用户标识：10001，昵称：测试用户乙", reminder)
        self.assertIn("当前时间：2026-06-27 18:05 (CST)，星期：周六", reminder)
        inserted = runtime.context.message_history_manager.inserts[-1]
        self.assertEqual(inserted.platform_id, "aiocqhttp")
        self.assertEqual(inserted.user_id, "10001")
        self.assertEqual(inserted.sender_id, "10001")
        self.assertEqual(inserted.sender_name, "测试用户乙")
        self.assertEqual(inserted.content["type"], "user")
        self.assertEqual(inserted.content["text"], "我刚到家了")

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

    def test_hidden_context_uses_daily_life_tag(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
        )

        self.assertIn("<daily_life>", text)
        self.assertIn("</daily_life>", text)
        self.assertIn("[HiddenActivityHint]", text)
        self.assertIn("[HiddenContextRules]", text)
        self.assertIn("隐藏上下文只用于保持角色处境", text)
        self.assertIn("[HiddenScheduleWindow]", text)
        self.assertIn("全天索引", text)
        self.assertNotIn("[HiddenScheduleMemory]", text)
        self.assertEqual(text.count("隐藏上下文只用于保持角色处境"), 1)
        self.assertNotIn("<expression_channel>", text)

    def test_hidden_context_can_include_expression_channel_when_voice_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 45,
                }
            }
        )
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
        )

        self.assertIn("<expression_channel>", text)
        self.assertIn("插件会在发送前用本地节奏算法判断是否转成语音", text)
        self.assertIn("文字始终是默认表达", text)
        self.assertIn("同一串可能一条就停", text)
        self.assertIn("用户没有明确要求语音时，不要主动调用 life_voice_generate", text)
        self.assertNotIn("record_life_text_decision", text)
        self.assertIn("life_voice_generate", text)
        self.assertIn("第一人称 decision_reason", text)
        self.assertIn("不要再用文字重复同一句", text)
        self.assertNotIn("speak_life_voice", text)
        self.assertIn("45.0%", text)

    def test_hidden_context_can_include_media_expression_when_image_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config()
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        text = runtime.build_hidden_expression_channel_hint(event)

        self.assertIn("<expression_channel>", text)
        self.assertIn("[HiddenMediaExpression]", text)
        self.assertIn("对话意图、当下状态和表达自然度", text)
        self.assertIn("不要靠固定词触发", text)
        self.assertIn("环境、动作细节、手边物件或氛围画面", text)
        self.assertNotIn("关系边界", text)
        self.assertNotIn("画面尺度", text)
        self.assertNotIn("睡前、换衣", text)
        self.assertIn("life_image_generate", text)
        self.assertNotIn("life_voice_generate", text)

    def test_media_expression_channel_does_not_mark_voice_switch_available(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config()
            }
        )
        runtime.archive = DataManager()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        day = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            day,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            expression_event=event,
        )

        self.assertIn("<expression_channel>", text)
        self.assertFalse(runtime.note_voice_switch_text_result(event))

    def test_hidden_context_can_include_video_expression_when_video_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(),
                "video_generation_config": {
                    "enabled": True,
                    "api_keys": ["video-key"],
                },
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        text = runtime.build_hidden_expression_channel_hint(event)

        self.assertIn("life_video_generate", text)
        self.assertIn("自动把那张图作为首帧/参考图", text)
        self.assertIn("视频生成慢且成本高", text)
        self.assertIn("普通“看看现在、发张照片、在干嘛”优先图片或文字", text)
        self.assertIn("非常强的场景需求", text)

    def test_hidden_media_cadence_reports_recent_media(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config()
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        runtime.note_life_media_sent(
            event,
            "图片",
            now=datetime.datetime.now() - datetime.timedelta(minutes=3),
        )

        text = runtime.build_hidden_expression_channel_hint(event)

        self.assertIn("[HiddenMediaCadence]", text)
        self.assertIn("发过图片", text)
        self.assertIn("连续 1 次", text)

    def test_hidden_expression_channel_can_be_disabled_for_text_chat(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_enabled": False,
                }
            }
        )

        text = runtime.build_hidden_expression_channel_hint(Event())

        self.assertEqual(text, "")

    def test_hidden_expression_channel_frontloads_voice_cadence(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 45,
                }
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        runtime._voice_switch_next_chain_limit = lambda: 3
        runtime._mark_voice_switch_channel(event, "语音", now=datetime.datetime.now())
        text = runtime.build_hidden_expression_channel_hint(event)

        self.assertIn("[HiddenVoiceCadence]", text)
        self.assertIn("可以自然接一条语音", text)
        self.assertIn("自然上限", text)

    def test_hidden_expression_channel_respects_voice_scope_lists(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "group_whitelist": ["10001"],
                    "group_blacklist": ["10002"],
                    "private_whitelist": ["123456"],
                    "private_blacklist": ["654321"],
                }
            }
        )
        allowed_group = Event(unified_msg_origin="aiocqhttp:GroupMessage:10001", group_id="10001")
        blocked_group = Event(unified_msg_origin="aiocqhttp:GroupMessage:10002", group_id="10002")
        other_group = Event(unified_msg_origin="aiocqhttp:GroupMessage:10003", group_id="10003")
        allowed_private = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")
        blocked_private = Event(unified_msg_origin="aiocqhttp:FriendMessage:654321", sender_id="654321")

        self.assertIn("<expression_channel>", runtime.build_hidden_expression_channel_hint(allowed_group))
        self.assertEqual(runtime.build_hidden_expression_channel_hint(blocked_group), "")
        self.assertEqual(runtime.build_hidden_expression_channel_hint(other_group), "")
        self.assertIn("<expression_channel>", runtime.build_hidden_expression_channel_hint(allowed_private))
        self.assertEqual(runtime.build_hidden_expression_channel_hint(blocked_private), "")

    def test_voice_switch_text_result_is_runtime_log_only(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                }
            }
        )
        runtime.archive = DataManager()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        self.assertTrue(runtime.mark_voice_switch_available(event))
        self.assertTrue(runtime.note_voice_switch_text_result(event))
        self.assertFalse(runtime.note_voice_switch_text_result(event))
        self.assertEqual(runtime.archive.action_decisions, {})

    def test_voice_switch_text_result_does_not_log_text_decision(self):
        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 35}}
        )
        runtime.archive = DataManager()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            runtime.mark_voice_switch_available(event)
            self.assertTrue(runtime.note_voice_switch_text_result(event))
        finally:
            messenger.logger.info = old_info

        self.assertEqual(messages, [])
        cadence = runtime._voice_switch_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["last_channel"], "文字")

    def test_voice_switch_text_result_consumes_internal_reason_silently(self):
        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 35}}
        )
        runtime.archive = DataManager()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            self.assertTrue(runtime.mark_voice_switch_available(event))
            runtime._voice_switch_round_store()[event.unified_msg_origin]["text_reason"] = "我这轮内容有几句铺垫，文字更容易读清楚。"
            self.assertTrue(runtime.note_voice_switch_text_result(event))
        finally:
            messenger.logger.info = old_info

        self.assertEqual(messages, [])
        self.assertEqual(runtime.archive.action_decisions, {})

    def test_voice_switch_used_by_tool_does_not_emit_text_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                }
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        self.assertTrue(runtime.mark_voice_switch_available(event))
        self.assertTrue(runtime.mark_voice_switch_used(event))
        self.assertFalse(runtime.note_voice_switch_text_result(event))

    def test_event_helpers_unwrap_tool_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        inner_event = Event(
            sender_name="小林",
            sender_id="10000001",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="测试群",
            message_id="abc123",
        )
        tool_context = types.SimpleNamespace(context=types.SimpleNamespace(event=inner_event))

        self.assertEqual(runtime._event_profile_id(tool_context), "10000001")
        self.assertEqual(runtime._event_platform_user(tool_context), ("aiocqhttp", "10000001"))
        self.assertEqual(runtime._event_group_meta(tool_context), ("10001", "测试群"))
        self.assertEqual(runtime._event_message_id(tool_context), "abc123")

    def test_hidden_context_can_include_world_memory(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            world_context="[HiddenPlaces]\n- 常去咖啡店：出现 2 次；写手帐",
        )

        self.assertIn("[HiddenWorldMemory]", text)
        self.assertIn("常去咖啡店", text)

    def test_hidden_world_memory_includes_pronoun_boundary(self):
        from core.life.surroundings import format_hidden_world_context

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        world_context = format_hidden_world_context(
            [
                {
                    "id": "10001",
                    "name": "小林",
                    "relationship_story": "她平时会记得我想去看展。",
                }
            ],
            [],
            [],
        )
        data = DayRecord(
            date="2026-05-24",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            world_context=world_context,
        )

        self.assertIn("称谓边界：人设线索优先", text)
        self.assertIn("不要把旧叙事里的他/她当成性别依据", text)
        self.assertIn("关系叙事：她平时会记得我想去看展。", text)

    def test_hidden_context_can_include_group_awareness(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            group_awareness_context=(
                "[HiddenGroupAwareness]\n"
                "- 看展小群: 平稳/偶尔看一眼/轻量判断; 参与欲35, 复杂度42, 理解88; Bob 准备看展\n"
                "[HiddenActionJudgement]\n"
                "- 保存记忆 [群友档案/已理解]: 观察为主；这条是 Bob 的信息"
            ),
        )

        self.assertIn("[HiddenGroupChatAwareness]", text)
        self.assertIn("看展小群", text)
        self.assertIn("这条是 Bob 的信息", text)

    def test_hidden_context_can_include_state(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            state=LifeState.from_value(
                {
                    "energy": 30,
                    "mood": "有点累",
                    "busyness": 80,
                    "social": 20,
                    "sleep": {"quality": 40, "summary": "昨晚睡得浅"},
                    "summary": "今天不太想出门",
                    "updated_at": "2026-05-24 12:00",
                    "source": "daily",
                }
            ),
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
        )

        self.assertIn("[HiddenState]", text)
        self.assertIn("体力 30/100", text)
        self.assertIn("今天不太想出门", text)
        self.assertNotIn("回复风格约束", text)
        self.assertNotIn("[HiddenAttentionState]", text)

    def test_hidden_context_keeps_fast_changing_parts_after_stable_daily_parts(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 45,
                }
            }
        )
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            memo="晚上记得取快递",
            meta={"theme": "雨后散步", "mood": "松弛"},
            state=LifeState.from_value(
                {
                    "energy": 30,
                    "mood": "有点累",
                    "summary": "今天不太想出门",
                }
            ),
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
        )

        self.assertLess(text.index("[HiddenContextRules]"), text.index("[HiddenAppearanceHint]"))
        self.assertLess(text.index("[HiddenAppearanceHint]"), text.index("[HiddenMoodHint]"))
        self.assertLess(text.index("[HiddenMoodHint]"), text.index("[HiddenScheduleWindow]"))
        self.assertLess(text.index("[HiddenScheduleWindow]"), text.index("[HiddenWeather]"))
        self.assertLess(text.index("[HiddenWeather]"), text.index("[HiddenMemoHint]"))
        self.assertLess(text.index("[HiddenMemoHint]"), text.index("[HiddenStatusHint]"))
        self.assertLess(text.index("[HiddenActivityHint]"), text.index("[HiddenTime]"))
        self.assertLess(text.index("[HiddenTime]"), text.index("[HiddenState]"))
        self.assertLess(text.index("</daily_life>"), text.index("<expression_channel>"))

    def test_hidden_schedule_window_keeps_compact_index_before_current_context(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            timeline=[
                TimelineItem(time="08:00", activity="起床洗漱", status="清醒"),
                TimelineItem(time="12:00", activity="在厨房煮清汤面", status="温和"),
                TimelineItem(time="20:30", activity="洗完碗整理餐桌", status="清爽"),
                TimelineItem(time="23:00", activity="关灯睡觉", status="放松"),
            ],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 19, 35),
            using_extended_night=False,
        )

        self.assertIn("[HiddenScheduleWindow]", text)
        self.assertIn("当前: 12:00 在厨房煮清汤面 [温和]", text)
        self.assertIn("接下来: 20:30 洗完碗整理餐桌 [清爽]", text)
        self.assertIn("全天索引", text)
        self.assertLess(text.index("全天索引"), text.index("当前: 12:00 在厨房煮清汤面 [温和]"))
        self.assertNotIn("[HiddenScheduleMemory]", text)

    def test_hidden_context_can_include_commitments(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            commitments=[CommitmentRecord(id=3, content="下次继续聊世界观设定", kind="followup", time_window="next_chat")],
        )

        self.assertIn("[HiddenCommitmentHint]", text)
        self.assertIn("下次继续聊世界观设定", text)

    def test_hidden_experience_context_can_include_pending_memory_corrections(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        text = runtime._format_hidden_experience_context(
            memory_corrections=[
                MemoryCorrectionRecord(
                    target_type="relationship",
                    target_id="10001",
                    correction="这条还没应用，应该继续提示。",
                    applied=False,
                )
            ]
        )

        self.assertIn("[HiddenMemoryCorrections]", text)
        self.assertIn("这条还没应用", text)

    def test_extended_night_uses_autonomous_life_mode(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            outfit="宽松白色长T恤",
            timeline=[TimelineItem(time="01:30", activity="还在窗边写设定", status="清醒")],
            meta={"life_mode": "late_night", "sleep_mode": "late_night"},
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 25, 1, 40),
            using_extended_night=True,
        )

        self.assertIn("late_night", text)
        self.assertIn("当前是否清醒仍按实时状态和时间轴判断", text)

class LifeRuntimeAsyncTest(unittest.IsolatedAsyncioTestCase):
    def _make_proactive_runtime(
        self,
        responses=(),
        *,
        provider_id="proactive-model",
        cooldown_minutes=10,
        mark_page_status_changed=None,
        context_config=None,
        persona_manager=None,
    ):
        provider = Provider(responses, provider_id=provider_id)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider, providers={provider_id: provider}, config=context_config, persona_manager=persona_manager)
        runtime.config = LifeSettings.from_dict(
            {
                "rhythm_config": {"llm_provider": "default-model"},
                "proactive_config": {
                    "enabled": True,
                    "provider": provider_id,
                    "cooldown_minutes": cooldown_minutes,
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
        context = runtime.context

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.get("content", "") if isinstance(resp, dict) else getattr(resp, "completion_text", "")

            async def _cleanup_conversation(self, session_id):
                return None

            async def _get_persona(self, umo=""):
                manager = getattr(context, "persona_manager", None)
                getter = getattr(manager, "get_default_persona_v3", None)
                if callable(getter):
                    persona = await getter(umo)
                    if isinstance(persona, dict):
                        return str(persona.get("prompt") or persona.get("system_prompt") or persona.get("content") or "")
                    for attr in ("prompt", "system_prompt", "content"):
                        text = str(getattr(persona, attr, "") or "").strip()
                        if text:
                            return text
                return "一个喜欢看展的人"

        runtime.composer = Composer()
        runtime._proactive_last_reply_at = {}
        runtime._proactive_idle_candidates = {}
        runtime._proactive_private_last_revisit_at = {}
        runtime._proactive_air_state = {}
        runtime._proactive_feedback_watch = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        if mark_page_status_changed:
            runtime.mark_page_status_changed = mark_page_status_changed
        return runtime, provider

    def _assert_last_assistant_history(self, runtime, scope, text):
        history = runtime.context.conversation_manager.conversations[scope].history
        self.assertEqual(history[-1], {"role": "assistant", "content": text})
        self.assertTrue(
            any(
                call[0] == "update_conversation" and call[1] == scope
                for call in runtime.context.conversation_manager.calls
            )
        )

    def _stub_media_director(self, runtime):
        runtime._direct_life_image_prompt = lambda event, prompt, *, reference=False: async_return(prompt)
        runtime._direct_life_video_prompt = lambda event, prompt: async_return(prompt)

    async def test_life_image_generate_sends_media(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "life.png"
        image_path.write_bytes(b"x" * 2048)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜生活照")

        self.assertIn("图片已发送。", result)
        self.assertIn("2.0 KB", result)
        self.assertIn("耗时", result)
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertIn({"type": "image", "file": str(image_path)}, runtime.context.sent_messages[0][1].items)
        self.assertFalse(
            any(call[0] == "update_conversation" for call in runtime.context.conversation_manager.calls)
        )
        cadence = runtime._media_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["last_media"], "图片")
        self.assertEqual(cadence["consecutive"], 1)

    async def test_life_image_generate_reports_empty_exception_type(self):
        async def fail_image(prompt):
            raise TimeoutError()

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace(generate_image=fail_image))
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜生活照")

        self.assertEqual(result, "图片生成失败：超时")
        self.assertEqual(runtime.context.sent_messages, [])

    async def test_recall_notice_cancels_life_image_send(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="42")
        recall_event = Event(unified_msg_origin=event.unified_msg_origin)
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "message_id": "42",
            "user_id": "10001",
        }

        self.assertTrue(runtime.note_recalled_message(recall_event))
        result = await runtime.life_image_generate(event, "雨夜生活照")

        self.assertEqual(result, "原消息已撤回，已取消图片发送。")
        self.assertEqual(runtime.context.sent_messages, [])

    async def test_recall_notice_clears_pending_result_and_runtime_context(self):
        runtime, _ = self._make_proactive_runtime([])
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001", message_id="77")
        event.message_str = "问一句问题"
        runtime.note_structured_incoming_message(event)
        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 12, 0))
        event.set_result(types.SimpleNamespace(chain=["准备回复"]))
        recall_event = Event(unified_msg_origin=event.unified_msg_origin, group_id="20001")
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "group_recall",
            "group_id": "20001",
            "message_id": "77",
            "user_id": "123456",
        }

        self.assertTrue(runtime.note_recalled_message(recall_event))
        self.assertTrue(runtime.suppress_recalled_event_result(event))

        self.assertIsNone(event.get_result())
        self.assertEqual(list(runtime._structured_scope_messages(event.unified_msg_origin)), [])
        self.assertEqual(runtime._proactive_idle_candidates, {})

    async def test_recall_notice_matches_message_obj_raw_message_id(self):
        runtime, _ = self._make_proactive_runtime([])
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="")
        event.message_obj.raw_message = {"message_id": 1801792361}
        event.set_result(types.SimpleNamespace(chain=["准备回复"]))
        recall_event = Event(unified_msg_origin=event.unified_msg_origin)
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "message_id": 1801792361,
            "user_id": "10001",
        }

        self.assertEqual(runtime._event_message_id(event), "1801792361")
        self.assertTrue(runtime.note_recalled_message(recall_event))
        self.assertTrue(runtime.suppress_recalled_event_result(event))
        self.assertIsNone(event.get_result())

    async def test_recall_notice_stops_event_before_astrbot_history_save(self):
        runtime, _ = self._make_proactive_runtime([])
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="407090562")
        event.set_result(types.SimpleNamespace(chain=["准备回复"]))
        recall_event = Event(unified_msg_origin=event.unified_msg_origin)
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "message_id": "407090562",
            "user_id": "10001",
        }

        self.assertTrue(runtime.note_recalled_message(recall_event))
        self.assertTrue(runtime.stop_recalled_event_before_history(event))

        self.assertTrue(event.is_stopped())
        self.assertIsNone(event.get_result())

    async def test_recalled_event_skips_chat_context_capture_background(self):
        runtime, _ = self._make_proactive_runtime([])
        calls = []
        logs = []
        import core.runtime.recall as recall_module
        original_logger = recall_module.logger
        recall_module.logger = types.SimpleNamespace(debug=lambda message: logs.append(str(message)))
        runtime.contact_resolver = types.SimpleNamespace(resolve_event_sender=lambda event: async_return("阿林"))
        runtime.maybe_collect_emoji_assets_from_event = lambda *args, **kwargs: calls.append("emoji") or async_return(None)
        runtime.maybe_capture_commitment_from_event = lambda *args, **kwargs: calls.append("commitment") or async_return(None)
        runtime.maybe_capture_chat_memory_from_event = lambda *args, **kwargs: calls.append("memory") or async_return(None)
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="m-local")
        event.message_str = "这种天，好热"
        recall_event = Event(unified_msg_origin=event.unified_msg_origin)
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "message_id": "m-local",
            "user_id": "10001",
        }

        try:
            runtime.note_recalled_message(recall_event)
            await runtime._capture_chat_context_background(event, datetime.datetime(2026, 6, 27, 13, 54))
            runtime.stop_recalled_event_before_history(event)
        finally:
            recall_module.logger = original_logger

        self.assertEqual(calls, [])
        skip_logs = [item for item in logs if "已跳过本轮回复与历史沉淀" in item]
        self.assertEqual(len(skip_logs), 1)

    async def test_injection_snapshot_uses_only_unapplied_memory_corrections(self):
        runtime, _ = self._make_proactive_runtime([])
        await runtime.archive.save_memory_correction(
            MemoryCorrectionRecord(
                target_type="relationship",
                target_id="applied",
                correction="已应用纠错不应继续注入。",
                applied=True,
            )
        )
        await runtime.archive.save_memory_correction(
            MemoryCorrectionRecord(
                target_type="relationship",
                target_id="pending",
                correction="未应用纠错仍需提示。",
                applied=False,
            )
        )

        snapshot = await runtime._gather_life_context_snapshot(use_cache=False)

        corrections = snapshot["memory_corrections"]
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0].target_id, "pending")

    async def test_life_image_generate_resolves_agent_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        wrapped_event = types.SimpleNamespace(context=types.SimpleNamespace(event=event))

        result = await runtime.life_image_generate(wrapped_event, "咖喱店生活照")

        self.assertIn("图片已发送。", result)
        self.assertIn("大小未知", result)
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)

    async def test_life_image_generate_uses_life_media_director_prompt(self):
        provider = Provider(
            [
                (
                    '{"subject":"窗边的我","scene":"雨夜客厅","composition":"半身生活照",'
                    '"scene_type":"家里","temperature_feel":"微凉","weather_condition":"小雨",'
                    '"frame_logic":"半身取景能看到抱枕、窗边和上半身居家穿搭",'
                    '"lighting":"暖色台灯","outfit":"宽松白色长T恤",'
                    '"outfit_logic":"人在客厅休息，只呈现半身可见的居家长T恤",'
                    '"action":"抱着抱枕看窗外",'
                    '"weather_vibe":"窗玻璃上有细雨水痕","mood":"慵懒治愈","constraints":"真实生活抓拍"}'
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime.archive.days["2026-05-24"] = DayRecord(
            date="2026-05-24",
            weather="小雨 20°C",
            outfit="宽松白色长T恤",
            timeline=[TimelineItem(time="20:10", activity="窝在客厅看窗外下雨", status="放松")],
            meta={"mood": "薄荷绿·治愈", "theme": "宅家充电的慵懒一日"},
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜沙发上随手拍")

        self.assertIn("图片已发送。", result)
        self.assertIn("大小未知", result)
        self.assertIn("雨夜客厅", image_prompts[0])
        self.assertIn("半身生活照", image_prompts[0])
        self.assertIn("场景类型：家里", image_prompts[0])
        self.assertIn("温感：微凉", image_prompts[0])
        self.assertIn("天气：小雨", image_prompts[0])
        self.assertIn("取景逻辑：半身取景能看到抱枕", image_prompts[0])
        self.assertIn("穿搭逻辑：人在客厅休息", image_prompts[0])
        self.assertNotIn("画面要求：雨夜沙发上随手拍", image_prompts[0])
        self.assertIn("当前生活上下文", provider.prompts[0])
        self.assertIn("scene_type", provider.prompts[0])
        self.assertIn("temperature_feel", provider.prompts[0])
        self.assertIn("frame_logic", provider.prompts[0])
        self.assertIn("outfit_logic", provider.prompts[0])
        self.assertIn("今日穿搭只属于主角本人", provider.prompts[0])
        self.assertLess(provider.prompts[0].index("当前生活上下文"), provider.prompts[0].index("原始画面要求"))
        self.assertEqual(runtime._life_media_last_images[event.unified_msg_origin], "life.png")

    async def test_life_image_generate_fails_when_director_returns_empty_payload(self):
        provider = Provider(["{}"])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        image_prompts = []
        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜沙发上随手拍")

        self.assertIn("图片生成失败：图片智能提取失败", result)
        self.assertEqual(image_prompts, [])
        self.assertEqual(runtime.context.sent_messages, [])

    async def test_media_director_marks_full_timeline_as_background(self):
        provider = Provider(
            [
                (
                    '{"subject":"雨天长椅上的我","scene":"街角小吃摊旁",'
                    '"composition":"半身生活照","lighting":"阴天柔光","outfit":"防雨外套和长裙",'
                    '"action":"拿着炸串看雨","weather_vibe":"细雨","mood":"慵懒满足","constraints":"真实抓拍"}'
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        runtime.archive.days[today] = DayRecord(
            date=today,
            weather="小雨 20°C",
            outfit="奶白睡裙",
            timeline=[
                TimelineItem(time="13:20", activity="坐在雨边长椅吃炸串", status="慵懒满足"),
                TimelineItem(time="21:00", activity="洗完澡换睡裙准备睡前放松", status="困倦"),
            ],
            state=LifeState(summary="坐在雨边长椅吃炸串，想吃完再溜达"),
        )

        async def fixed_media_day():
            fixed_now = datetime.datetime.strptime(f"{today} 13:25", "%Y-%m-%d %H:%M")
            return runtime.archive.days[today], fixed_now, False

        runtime._media_director_current_day = fixed_media_day

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        await runtime.life_image_generate(event, "拍一张现在的生活照")

        prompt = provider.prompts[0]
        self.assertIn("当前活动：坐在雨边长椅吃炸串", prompt)
        self.assertIn("全天日程背景（只作为背景", prompt)
        self.assertLess(prompt.index("当前活动：坐在雨边长椅吃炸串"), prompt.index("全天日程背景"))
        self.assertIn("21:00 - 洗完澡换睡裙准备睡前放松", prompt)

    async def test_media_director_uses_recent_chat_as_scene_anchor(self):
        provider = Provider(
            [
                (
                    '{"subject":"餐桌旁的我","scene":"家里餐桌旁","composition":"随手生活照",'
                    '"lighting":"室内暖光","outfit":"居家外套","action":"把切好的水果推到镜头前",'
                    '"weather_vibe":"","mood":"自然催促","constraints":"不要回到刚进门或翻钥匙的旧场景"}'
                )
            ]
        )
        scope = "aiocqhttp:FriendMessage:10001"
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.context.conversation_manager.current_ids[scope] = "current"
        runtime.context.conversation_manager.conversations[scope] = types.SimpleNamespace(
            history=[
                {"role": "assistant", "content": "快了，拐过弯就是。等会帮我拿包，我翻下钥匙。"},
                {"role": "assistant", "content": "水果切好了，快来吃，再不来我一个人全部解决掉。"},
                {"role": "user", "content": "拍张照看看"},
            ]
        )
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin=scope)
        event.message_str = "拍张照看看"

        await runtime.life_image_generate(event, "拍一张现在的生活照")

        prompt = provider.prompts[0]
        self.assertIn("最近对话场景锚点", prompt)
        self.assertIn("水果切好了", prompt)
        self.assertIn("拍张照看看", prompt)
        self.assertLess(prompt.rindex("当前生活上下文"), prompt.rindex("最近对话场景锚点"))
        self.assertLess(prompt.rindex("最近对话场景锚点"), prompt.rindex("原始画面要求"))
        self.assertFalse(
            any(call[0] == "update_conversation" for call in runtime.context.conversation_manager.calls)
        )

    async def test_edit_life_image_uses_explicit_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference: edit_calls.append((prompt, reference))
                or async_return(types.SimpleNamespace(path=Path("edited.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(event, "改成咖啡店生活照", "https://example.com/ref.png")

        self.assertIn("图片已根据参考图生成。", result)
        self.assertIn("大小未知", result)
        self.assertEqual(edit_calls, [("改成咖啡店生活照", "https://example.com/ref.png")])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertIn({"type": "image", "file": "edited.png"}, runtime.context.sent_messages[0][1].items)

    async def test_edit_life_image_uses_current_message_image_when_reference_empty(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference: edit_calls.append((prompt, reference))
                or async_return(types.SimpleNamespace(path=Path("edited.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [{"type": "image", "file": "D:/tmp/ref.png"}]
        event.message_obj.message = event.message_items

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertIn("图片已根据参考图生成。", result)
        self.assertIn("大小未知", result)
        self.assertEqual(edit_calls, [("换成雨夜房间氛围", "D:/tmp/ref.png")])

    async def test_edit_life_image_uses_quoted_image_when_reference_empty(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference: edit_calls.append((prompt, reference))
                or async_return(types.SimpleNamespace(path=Path("edited.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertIn("图片已根据参考图生成。", result)
        self.assertEqual(edit_calls, [("换成雨夜房间氛围", "https://example.com/quoted.png")])

    async def test_edit_life_image_requires_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertEqual(result, "没有找到参考图片。")

    async def test_life_video_generate_runs_in_background_and_sends_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "first-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        video_path = Path(tempfile.mkdtemp()) / "life.mp4"
        video_path.write_bytes(b"v" * 4096)
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"first-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url=str(video_path)))
            )
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "书店门口短视频")

        self.assertIn("视频生成已开始", result)
        self.assertEqual(scheduled[0][0], "生活视频生成")
        await scheduled[0][2]

        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertEqual(video_calls[0][1], b"first-frame")
        self.assertIn(
            {"type": "video", "file": str(video_path)},
            runtime.context.sent_messages[0][1].items,
        )
        structured = list(runtime._structured_scope_messages(event.unified_msg_origin))
        self.assertTrue(structured)
        self.assertIn("[视频已发送：4.0 KB，耗时", structured[-1].content)
        cadence = runtime._media_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["last_media"], "视频")
        self.assertEqual(cadence["consecutive"], 1)
        self.assertFalse(
            any(call[0] == "update_conversation" for call in runtime.context.conversation_manager.calls)
        )

    async def test_life_video_generate_resolves_agent_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "first-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"first-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            )
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")
        wrapped_event = types.SimpleNamespace(context=types.SimpleNamespace(event=event))

        result = await runtime.life_video_generate(wrapped_event, "咖喱店短视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertEqual(len(runtime.context.sent_messages), 1)

    async def test_life_video_generate_uses_directed_prompt_and_reference_image(self):
        provider = Provider(
            [
                (
                    '{"image":"傍晚书店门口的半身生活镜头",'
                    '"continuity":"保持上一张生活图里的人物身份、浅蓝外套、书店门口构图和主体位置",'
                    '"camera":"半身中近景，镜头缓慢推近，主体保持在画面中央偏右",'
                    '"motion":"手里的纸袋轻轻晃动，雨丝在路灯下微微发亮",'
                    '"sound":"街边细雨声和纸袋摩擦声"}'
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime.archive.days["2026-05-24"] = DayRecord(
            date="2026-05-24",
            weather="小雨",
            outfit="浅蓝外套",
            timeline=[TimelineItem(time="18:20", activity="从书店出来", status="轻松")],
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                _load_reference_image=lambda reference: async_return((b"image-bytes", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")
        event.message_items = [{"type": "image", "file": "D:/tmp/current.png"}]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(event, "傍晚从书店门口走出来")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertIn("画面：傍晚书店门口的半身生活镜头", video_calls[0][0])
        self.assertIn("连续性：保持上一张生活图里的人物身份", video_calls[0][0])
        self.assertIn("镜头：半身中近景，镜头缓慢推近", video_calls[0][0])
        self.assertIn("动态：手里的纸袋轻轻晃动", video_calls[0][0])
        self.assertIn("continuity", provider.prompts[0])
        self.assertIn("camera", provider.prompts[0])
        self.assertIn("景别、机位、构图重心、镜头运动和节奏", provider.prompts[0])
        self.assertIn("不要把镜头设计全塞进 motion", provider.prompts[0])
        self.assertIn("保持原图主体", provider.prompts[0])
        self.assertIn("嘴唇轻微自然开合", provider.prompts[0])
        self.assertIn("默认考虑环境声、动作声和一层很轻的氛围背景声", provider.prompts[0])
        self.assertIn("根据文案内容自然开口说话", provider.prompts[0])
        self.assertIn("符合人物状态和情绪", provider.prompts[0])
        self.assertIn("人物台词只作为短促的画面内声音元素", provider.prompts[0])
        self.assertIn("不要固定成某一种声线", provider.prompts[0])
        self.assertIn("背景声持续存在但不喧宾夺主", provider.prompts[0])
        self.assertEqual(video_calls[0][1], b"image-bytes")

    async def test_life_video_generate_uses_quoted_image_as_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        loaded_refs = []
        video_calls = []

        async def fail_generate_image(prompt):
            raise AssertionError("引用图片应直接作为视频首帧，不应自动生成首帧")

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=fail_generate_image,
                _load_reference_image=lambda reference: loaded_refs.append(reference)
                or async_return((b"quoted-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(event, "把这张转成视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(loaded_refs, ["https://example.com/quoted.png"])
        self.assertEqual(video_calls[0][1], b"quoted-frame")

    async def test_life_video_prompt_requires_camera_field(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        with self.assertRaises(MediaPromptExtractionError) as caught:
            runtime._media_video_prompt_from_payload(
                {
                    "image": "雨夜窗边半身镜头",
                    "continuity": "保持人物服装和窗边构图",
                    "motion": "雨滴在玻璃上缓慢滑落，人物轻轻眨眼",
                    "sound": "窗外雨声和很轻的室内背景声",
                }
            )

        self.assertIn("camera", str(caught.exception))

        with self.assertRaises(MediaPromptExtractionError) as caught:
            runtime._media_video_prompt_from_payload(
                {
                    "image": "雨夜窗边半身镜头",
                    "camera": "半身近景，镜头缓慢推近",
                    "motion": "雨滴在玻璃上缓慢滑落，人物轻轻眨眼",
                    "sound": "窗外雨声和很轻的室内背景声",
                }
            )

        self.assertIn("continuity", str(caught.exception))

    async def test_life_video_generate_ignores_previous_image_when_message_has_no_image(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "fresh-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfresh")
        loaded_refs = []
        image_prompts = []
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: loaded_refs.append(reference)
                or async_return((b"fresh-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4")),
            ),
        )
        runtime._life_media_last_images = {"aiocqhttp:GroupMessage:20001": "D:/tmp/old.png"}
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(loaded_refs, [str(image_path)])
        self.assertEqual(video_calls[0][1], b"fresh-frame")
        self.assertNotIn("D:/tmp/old.png", loaded_refs)
        self.assertTrue(image_prompts)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertIn(
            {"type": "video", "url": "https://example.com/life.mp4", "file": "https://example.com/life.mp4"},
            runtime.context.sent_messages[0][1].items,
        )

    async def test_life_video_generate_sends_first_frame_only_when_video_fails(self):
        provider = Provider(
            [
                '{"subject":"窗边的人","scene":"雨夜窗边","composition":"半身生活照"}',
                (
                    '{"image":"雨夜窗边",'
                    '"continuity":"保持首帧里的人物、睡衣和窗边构图",'
                    '"camera":"半身近景，镜头轻轻推近",'
                    '"motion":"人物轻轻眨眼，雨滴沿玻璃滑落",'
                    '"sound":"窗外雨声和很轻的室内背景声"}'
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            provider,
            persona_manager=PersonaManager(prompt="我是一个夜里说话会放轻声音的人。"),
        )
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime.archive.days["2026-05-24"] = DayRecord(
            date="2026-05-24",
            outfit="奶油色睡衣",
            timeline=[TimelineItem(time="23:50", activity="窝在被窝里准备睡觉", status="困")],
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

            async def _get_persona(self, umo=""):
                return "我是一个夜里说话会放轻声音的人。"

        runtime.composer = Composer()
        image_path = Path(tempfile.mkdtemp()) / "fallback-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfallback")

        async def fail_video(prompt, image_bytes=None):
            raise RuntimeError("TimeoutError")

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"fallback-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=fail_video,
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertIn({"type": "image", "file": str(image_path)}, runtime.context.sent_messages[0][1].items)
        self.assertEqual(runtime.context.sent_messages[1][0], event.unified_msg_origin)
        self.assertIn("这段没录成，先把刚拍到的这张给你看。", runtime.context.sent_messages[1][1].items)
        self.assertEqual(len(provider.prompts), 2)

    async def test_life_video_generate_falls_back_when_video_director_missing_fields(self):
        provider = Provider(
            [
                '{"subject":"窗边的人","scene":"雨夜窗边","composition":"半身生活照"}',
                '{"image":"雨夜窗边"}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        image_path = Path(tempfile.mkdtemp()) / "fallback-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfallback")
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"fallback-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4")),
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(video_calls, [])
        self.assertIn({"type": "image", "file": str(image_path)}, runtime.context.sent_messages[0][1].items)
        self.assertIn("这段没录成，先把刚拍到的这张给你看。", runtime.context.sent_messages[1][1].items)
        self.assertEqual(len(provider.prompts), 2)

    async def test_life_video_generate_fails_before_first_frame_when_image_director_empty(self):
        provider = Provider(["{}"])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        image_calls = []
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_calls.append(prompt)
                or async_return(types.SimpleNamespace(path=Path("first-frame.png"))),
                _load_reference_image=lambda reference: async_return((b"first-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4")),
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(image_calls, [])
        self.assertEqual(video_calls, [])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertIn("这段没录成。", runtime.context.sent_messages[0][1].items)
        self.assertEqual(len(provider.prompts), 1)

    async def test_life_video_final_text_waits_until_video_sent(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "first-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"first-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        await runtime.life_video_generate(event, "被窝里困样短视频")
        event.set_result(event.chain_result(["录了录了，好了自动发你\n拍完真睡了，晚安"]))

        self.assertTrue(runtime.hold_life_video_final_text(event))
        self.assertIsNone(event.get_result())
        self.assertEqual(runtime.context.sent_messages, [])

        await scheduled[0][2]

        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertIn(
            {"type": "video", "url": "https://example.com/life.mp4", "file": "https://example.com/life.mp4"},
            runtime.context.sent_messages[0][1].items,
        )
        self.assertEqual(runtime.context.sent_messages[1][0], event.unified_msg_origin)
        self.assertIn("录了录了，好了自动发你\n拍完真睡了，晚安", runtime.context.sent_messages[1][1].items)

    async def test_life_video_final_text_matches_same_message_across_event_wrappers(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "first-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"first-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        tool_event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="m-video")
        decorate_event = Event(unified_msg_origin=tool_event.unified_msg_origin, message_id="m-video")

        await runtime.life_video_generate(tool_event, "被窝里困样短视频")
        decorate_event.set_result(decorate_event.chain_result(["重新录上了，好了自动发你\n拍完真睡了，晚安"]))

        self.assertTrue(runtime.hold_life_video_final_text(decorate_event))
        self.assertIsNone(decorate_event.get_result())
        self.assertEqual(runtime.context.sent_messages, [])

        await scheduled[0][2]

        self.assertEqual(runtime.context.sent_messages[0][0], tool_event.unified_msg_origin)
        self.assertIn(
            {"type": "video", "url": "https://example.com/life.mp4", "file": "https://example.com/life.mp4"},
            runtime.context.sent_messages[0][1].items,
        )
        self.assertEqual(runtime.context.sent_messages[1][0], tool_event.unified_msg_origin)
        self.assertIn("重新录上了，好了自动发你\n拍完真睡了，晚安", runtime.context.sent_messages[1][1].items)

    async def test_life_video_final_text_matches_recent_scope_when_message_id_missing(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime._life_video_pending = {
            "aiocqhttp:FriendMessage:10001": {
                "token": "no-id:1",
                "event_id": 12345,
                "message_id": "",
                "created_at": asyncio.get_running_loop().time(),
                "final_text": "",
            }
        }
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.set_result(event.chain_result(["录好了，等下自动发你"]))

        self.assertTrue(runtime.hold_life_video_final_text(event))
        self.assertIsNone(event.get_result())
        self.assertEqual(
            runtime._life_video_pending["aiocqhttp:FriendMessage:10001"]["final_text"],
            "录好了，等下自动发你",
        )

    async def test_life_voice_generate_sends_voice_message(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "你快点睡。"
        runtime.context.config = {
            "provider_settings": {
                "identifier": True,
                "datetime_system_prompt": True,
            },
            "timezone": "Asia/Shanghai",
        }

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦", emotion_category="neutral")

        self.assertIsNone(result)
        self.assertEqual(voice_calls, [("我困啦", "困倦", "neutral")])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertTrue(any(getattr(item, "file", "") == "voice.mp3" for item in runtime.context.sent_messages[0][1].items))
        history = runtime.context.conversation_manager.conversations[event.unified_msg_origin].history
        self.assertEqual(history[-2]["role"], "user")
        self.assertEqual(history[-2]["content"][0], {"type": "text", "text": "你快点睡。"})
        self.assertIn("用户标识：123456，昵称：平台名", history[-2]["content"][1]["text"])
        self.assertIn("当前时间：", history[-2]["content"][1]["text"])
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_does_not_duplicate_existing_user_history(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        scope = "aiocqhttp:FriendMessage:10001"
        runtime.context.conversation_manager.conversations[scope] = types.SimpleNamespace(
            history=[{"role": "user", "content": "你快点睡。"}]
        )
        runtime.context.conversation_manager.current_ids[scope] = "current"
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin=scope)
        event.message_str = "你快点睡。"

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦", emotion_category="neutral")

        self.assertIsNone(result)
        history = runtime.context.conversation_manager.conversations[scope].history
        self.assertEqual(
            history,
            [
                {"role": "user", "content": "你快点睡。"},
                {"role": "assistant", "content": "我困啦"},
            ],
        )

    async def test_life_voice_generate_suppresses_normal_success_summary_log(self):
        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.context.config = {
            "provider_settings": {
                "identifier": True,
                "datetime_system_prompt": True,
            },
            "timezone": "Asia/Shanghai",
        }
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            await runtime.life_voice_generate(
                event,
                "我困啦",
                emotion="困倦",
                decision_reason="这句更适合小声说出来。",
            )
        finally:
            messenger.logger.info = old_info

        self.assertFalse(any("语音智能切换裁定：语音" in item for item in messages))
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_voice_switch_before_send_uses_local_structure_for_text_decision(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "ZBrush 遮罩怎么扩大？"
        event.set_result(
            types.SimpleNamespace(
                chain=[
                    types.SimpleNamespace(
                        text="你得去右侧工具栏找 Tool -> Masking，里面有个 Grow 按钮。\n"
                        "按住 Ctrl + Alt 点击它，再按你想绑定的键。"
                    )
                ]
            )
        )

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(provider.prompts, [])
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("英文名词或参数", item["text_reason"])

    async def test_voice_switch_before_send_uses_local_natural_score_for_text_decision(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 35}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "外面好玩吗？"
        event.set_result(
            types.SimpleNamespace(
                chain=[
                    types.SimpleNamespace(
                        text="我刚从外面绕了一圈回来，雨停了，路上人不多，空气还可以，等会儿先把东西放好再说。"
                    )
                ]
            )
        )

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(provider.prompts, [])
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertTrue(item["pre_send_checked"])
        self.assertIn("留在屏幕上读起来更清楚", item["text_reason"])

    async def test_voice_switch_before_send_can_replace_text_with_voice(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.context.config = {
            "provider_settings": {
                "identifier": True,
                "datetime_system_prompt": True,
            },
            "timezone": "Asia/Shanghai",
        }
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还没到吗？"
        reply = "别催啦，我马上到。"
        runtime.context.conversation_manager.conversations[event.unified_msg_origin] = types.SimpleNamespace(
            history=[{"role": "assistant", "content": reply}]
        )
        runtime.context.conversation_manager.current_ids[event.unified_msg_origin] = "current"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [(reply, "轻松亲近", "happy")])
        result = event.get_result()
        self.assertTrue(any(getattr(item, "file", "") == "voice.mp3" for item in result.chain))
        self.assertFalse(runtime.note_voice_switch_text_result(event))
        history = runtime.context.conversation_manager.conversations[event.unified_msg_origin].history
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"][0], {"type": "text", "text": "还没到吗？"})
        self.assertIn("用户标识：10001", history[0]["content"][1]["text"])
        self.assertEqual(history[1], {"role": "assistant", "content": reply})
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_voice_switch_short_wrapped_text_can_still_use_voice(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "那就这么定了？"
        reply = "嗯，\n雨天就这点好，节奏一下慢下来\n你那边呢，还在发呆没"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [(reply, "轻松亲近", "happy")])

    async def test_voice_switch_short_clipped_reply_can_use_angry_tone(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还没到吗？"
        reply = "别催啦，我马上到！"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [(reply, "语气稍冲", "angry")])

    async def test_voice_switch_soft_drooping_reply_can_use_sad_tone(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "你还好吗？"
        reply = "有点累了…我先缓缓。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [(reply, "低低慢声", "sad")])

    async def test_voice_switch_probability_gate_keeps_text(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 20}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还没到吗？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="别催啦，我马上到。")]))

        old_random = random.random
        random.random = lambda: 0.9
        try:
            runtime.mark_voice_switch_available(event)
            changed = await runtime.apply_voice_switch_before_send(event)
        finally:
            random.random = old_random

        self.assertFalse(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [])
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("语音留到更需要", item["text_reason"])

    async def test_voice_switch_can_continue_short_voice_chain_after_recent_voice(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 3
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还在外面吗？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="嗯，雨停了我就往回走。")]))
        runtime._mark_voice_switch_channel(event, "语音")

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, ["嗯，雨停了我就往回走。"])
        cadence = runtime._voice_switch_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["consecutive_voice"], 2)

    async def test_voice_switch_stops_after_voice_chain_limit(self):
        provider = Provider([
            '{"channel":"voice","reason":"我还想顺着刚才的语气接一句。",'
            '"emotion":"轻松","emotion_category":"happy","confidence":0.91}'
        ])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 2
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还没到吗？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="马上，别催啦。")]))
        for _ in range(2):
            runtime._mark_voice_switch_channel(event, "语音")

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(voice_calls, [])
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("连续发了几条语音", item["text_reason"])

    async def test_voice_switch_before_send_does_not_call_llm_decision(self):
        class Composer:
            async def _get_provider(self, provider_id=""):
                raise AssertionError("发送前本地裁定不应请求 provider")

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                raise AssertionError("发送前本地裁定不应调用大语言模型")

            async def _cleanup_conversation(self, session_id):
                raise AssertionError("发送前本地裁定不应创建临时会话")

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "ZBrush 遮罩怎么扩大？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="Tool -> Masking 里点 Grow，再自己绑定快捷键。")]))

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("英文名词或参数", item["text_reason"])

    async def test_life_voice_generate_resolves_agent_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        wrapped_event = types.SimpleNamespace(context=types.SimpleNamespace(event=event))

        result = await runtime.life_voice_generate(wrapped_event, "我困啦", emotion="困倦")

        self.assertIsNone(result)
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")

    async def test_life_voice_generate_respects_blacklist(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "private_blacklist": ["123456"],
                }
            }
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertEqual(result, "当前会话不可发送语音。")
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_logs_text_block_reason(self):
        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "private_blacklist": ["123456"],
                }
            }
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            await runtime.life_voice_generate(event, "我困啦", emotion="困倦")
        finally:
            messenger.logger.info = old_info

        self.assertTrue(any("语音智能切换裁定：文字" in item for item in messages))
        self.assertTrue(any("结果：被拦截" in item for item in messages))
        self.assertTrue(any("原因：当前会话不在语音允许范围内" in item for item in messages))

    async def test_life_voice_generate_respects_text_chat_switch(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_enabled": False,
                }
            }
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertIn("当前已关闭自动语音", result)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_allows_explicit_user_request_when_auto_switch_disabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_enabled": False,
                }
            }
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦", user_requested=True)

        self.assertIsNone(result)
        self.assertEqual(voice_calls, [("我困啦", "困倦", "")])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_auto_can_continue_short_voice_chain(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 3
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")
        runtime._mark_voice_switch_channel(event, "语音")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertIsNone(result)
        self.assertEqual(voice_calls, ["我困啦"])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        cadence = runtime._voice_switch_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["consecutive_voice"], 2)

    async def test_life_voice_generate_auto_respects_voice_chain_limit(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 1
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")
        runtime._mark_voice_switch_channel(event, "语音")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertIn("请直接用文字回复", result)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_user_request_bypasses_cadence_gate(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 0}}
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")
        runtime._mark_voice_switch_channel(event, "语音")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦", user_requested=True)

        self.assertIsNone(result)
        self.assertEqual(voice_calls, ["我困啦"])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")

    async def test_voice_generation_routes_emotion_to_voice_and_speed(self):
        posted_payloads = []

        class Response:
            status = 200
            headers = {"Content-Type": "audio/mpeg"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def read(self):
                return b"voice-bytes"

        class Session:
            closed = False

            def post(self, url, headers=None, json=None, timeout=None):
                posted_payloads.append(json)
                return Response()

        settings = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "api_key": "sf-key",
                    "voice": "voice-neutral",
                    "emotion_voice_map": "happy: voice-happy\nsad: voice-sad\n无奈中带点宠溺: voice-soft",
                    "emotion_speed_map": "happy: 1.35\nsad: 0.75\nneutral: 1.0\n无奈中带点宠溺: 0.95",
                }
            }
        ).voice_generation
        service = SiliconFlowVoiceService(settings, Path(tempfile.mkdtemp()))
        service._get_session = lambda: async_return(Session())

        await service.synthesize("好耶，今天很开心")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-neutral")
        self.assertEqual(posted_payloads[-1]["speed"], 1.0)
        self.assertEqual(posted_payloads[-1]["response_format"], "wav")
        self.assertNotIn("sample_rate", posted_payloads[-1])
        self.assertNotIn("gain", posted_payloads[-1])

        await service.synthesize("好耶，今天很开心", emotion="开心")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-neutral")
        self.assertEqual(posted_payloads[-1]["speed"], 1.0)

        await service.synthesize("好耶，今天很开心", emotion="开心", emotion_category="happy")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-happy")
        self.assertEqual(posted_payloads[-1]["speed"], 1.35)

        await service.synthesize("我还好", emotion="难过")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-neutral")
        self.assertEqual(posted_payloads[-1]["speed"], 1.0)

        await service.synthesize("我还好", emotion="难过", emotion_category="sad")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-sad")
        self.assertEqual(posted_payloads[-1]["speed"], 0.75)

        route = service._voice_route("无奈中带点宠溺")
        self.assertEqual(route["emotion"], "无奈中带点宠溺")
        self.assertEqual(route["voice"], "voice-soft")
        self.assertEqual(route["speed"], 0.95)

        await service.synthesize("行了行了，听到没", emotion="无奈中带点宠溺")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-soft")
        self.assertEqual(posted_payloads[-1]["speed"], 0.95)

        unknown_route = service._voice_route("困得有点撒娇")
        self.assertEqual(unknown_route["emotion"], "困得有点撒娇")
        self.assertEqual(unknown_route["emotion_category"], "")
        self.assertEqual(unknown_route["voice"], "voice-neutral")
        self.assertEqual(unknown_route["speed"], 1.0)

        category_route = service._voice_route("慵懒治愈", "happy")
        self.assertEqual(category_route["emotion"], "慵懒治愈")
        self.assertEqual(category_route["emotion_category"], "happy")
        self.assertEqual(category_route["voice"], "voice-happy")
        self.assertEqual(category_route["speed"], 1.35)

        await service.synthesize("慢慢醒一下", emotion="慵懒治愈", emotion_category="happy")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-happy")
        self.assertEqual(posted_payloads[-1]["speed"], 1.35)

        no_category_settings = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "api_key": "sf-key",
                    "voice": "voice-default",
                    "emotion_voice_map": "neutral: voice-neutral",
                    "emotion_speed_map": "neutral: 0.7",
                }
            }
        ).voice_generation
        no_category_service = SiliconFlowVoiceService(no_category_settings, Path(tempfile.mkdtemp()))
        no_category_route = no_category_service._voice_route("慵懒治愈")

        self.assertEqual(no_category_route["emotion"], "慵懒治愈")
        self.assertEqual(no_category_route["emotion_category"], "")
        self.assertEqual(no_category_route["voice"], "voice-default")
        self.assertEqual(no_category_route["speed"], 1.0)

    async def test_gemini_image_edit_sends_reference_image_part(self):
        posted_payloads = []
        output_bytes = b"\x89PNG\r\n\x1a\noutput"

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self):
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/png",
                                            "data": base64.b64encode(output_bytes).decode("ascii"),
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }

            async def text(self):
                return ""

        class Session:
            closed = False

            def post(self, url, json=None, headers=None, proxy=None, timeout=None):
                posted_payloads.append(json)
                return Response()

        reference = Path(tempfile.mkdtemp()) / "reference.png"
        reference.write_bytes(b"\x89PNG\r\n\x1a\nreference")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(
                    "gemini-key",
                    resolution="2K",
                    aspect_ratio="16:9",
                )
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()) / "daily_life.db")
        service._get_session = lambda: async_return(Session())

        generated = await service.edit_image("改成咖啡店生活照", str(reference))

        self.assertTrue(generated.path.exists())
        parts = posted_payloads[-1]["contents"][0]["parts"]
        self.assertIn("改成咖啡店生活照", parts[0]["text"])
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), reference.read_bytes())
        image_config = posted_payloads[-1]["generationConfig"]["imageConfig"]
        self.assertEqual(image_config["imageSize"], "2K")
        self.assertEqual(image_config["aspectRatio"], "16:9")
        response_image_config = posted_payloads[-1]["generationConfig"]["responseFormat"]["image"]
        self.assertEqual(response_image_config["imageSize"], "2K")
        self.assertEqual(response_image_config["aspectRatio"], "16:9")

    async def test_gemini_image_generation_can_attach_character_reference(self):
        posted_payloads = []
        output_bytes = b"\x89PNG\r\n\x1a\noutput"

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self):
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/png",
                                            "data": base64.b64encode(output_bytes).decode("ascii"),
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }

            async def text(self):
                return ""

        class Session:
            closed = False

            def post(self, url, json=None, headers=None, proxy=None, timeout=None):
                posted_payloads.append(json)
                return Response()

        temp_dir = Path(tempfile.mkdtemp())
        character = temp_dir / "character.png"
        character_side = temp_dir / "character-side.png"
        character.write_bytes(b"\x89PNG\r\n\x1a\ncharacter")
        character_side.write_bytes(b"\x89PNG\r\n\x1a\nside")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(
                    "gemini-key",
                    character_reference_images=[
                        {"path": str(character), "name": "正面参考.png"},
                        {"path": str(character_side), "name": "侧面参考.png"},
                    ],
                    character_reference_policy="auto",
                )
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()) / "daily_life.db")
        service._get_session = lambda: async_return(Session())

        await service.generate_image("角色坐在窗边看雨")

        parts = posted_payloads[-1]["contents"][0]["parts"]
        self.assertIn("如果画面包含角色本人", parts[0]["text"])
        self.assertEqual(parts[1]["text"], "下面 2 张图是角色形象参考图组，用于保持角色外貌一致。")
        self.assertIn("正面参考.png", parts[2]["text"])
        self.assertEqual(base64.b64decode(parts[3]["inlineData"]["data"]), character.read_bytes())
        self.assertIn("侧面参考.png", parts[4]["text"])
        self.assertEqual(base64.b64decode(parts[5]["inlineData"]["data"]), character_side.read_bytes())

    async def test_gemini_image_edit_keeps_scene_reference_and_character_reference_separate(self):
        posted_payloads = []
        output_bytes = b"\x89PNG\r\n\x1a\noutput"

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self):
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/png",
                                            "data": base64.b64encode(output_bytes).decode("ascii"),
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }

            async def text(self):
                return ""

        class Session:
            closed = False

            def post(self, url, json=None, headers=None, proxy=None, timeout=None):
                posted_payloads.append(json)
                return Response()

        temp_dir = Path(tempfile.mkdtemp())
        scene = temp_dir / "scene.png"
        character = temp_dir / "character.png"
        scene.write_bytes(b"\x89PNG\r\n\x1a\nscene")
        character.write_bytes(b"\x89PNG\r\n\x1a\ncharacter")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(
                    "gemini-key",
                    character_reference_images=[{"path": str(character), "name": "角色参考.png"}],
                    character_reference_policy="always",
                )
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()) / "daily_life.db")
        service._get_session = lambda: async_return(Session())

        await service.edit_image("保留姿势，换成咖啡店生活照", str(scene))

        parts = posted_payloads[-1]["contents"][0]["parts"]
        self.assertIn("优先保持角色", parts[0]["text"])
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), scene.read_bytes())
        self.assertEqual(parts[2]["text"], "下面 1 张图是角色形象参考图组，用于保持角色外貌一致。")
        self.assertIn("角色参考.png", parts[3]["text"])
        self.assertEqual(base64.b64decode(parts[4]["inlineData"]["data"]), character.read_bytes())

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
        runtime._proactive_segmented_reply_enabled = lambda scope: False
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
        runtime._proactive_segmented_reply_enabled = lambda scope: False
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

    async def test_proactive_voice_probability_can_skip_voice(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "proactive_enabled": True,
                    "proactive_probability": 0,
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
        runtime._proactive_segmented_reply_enabled = lambda scope: False
        runtime._send_proactive_emoji_if_needed = lambda scope, payload: async_return(None)
        runtime._mark_failed_proactive_contact = lambda *args, **kwargs: async_return(None)

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "我困啦",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["我困啦"])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_proactive_voice_respects_group_blacklist(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "proactive_enabled": True,
                    "group_blacklist": ["10001"],
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
        runtime._proactive_segmented_reply_enabled = lambda scope: False
        runtime._send_proactive_emoji_if_needed = lambda scope, payload: async_return(None)
        runtime._mark_failed_proactive_contact = lambda *args, **kwargs: async_return(None)

        sent = await runtime._send_proactive_message(
            "aiocqhttp:GroupMessage:10001",
            "我困啦",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["我困啦"])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    def test_proactive_voice_probability_boundaries(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        self.assertFalse(runtime._proactive_voice_probability_hit(types.SimpleNamespace(proactive_probability=0)))
        self.assertTrue(runtime._proactive_voice_probability_hit(types.SimpleNamespace(proactive_probability=100)))
        self.assertTrue(runtime._proactive_voice_probability_hit(types.SimpleNamespace(proactive_probability="bad")))

    async def test_maybe_capture_commitment_from_event_uses_llm_json(self):
        provider = Provider(
            [
                '{"has_commitment":true,"content":"明天提醒我买牛奶","kind":"reminder",'
                '"trigger_date":"2026-05-25","trigger_time":"","time_window":"",'
                '"people":[],"place":"","confidence":0.91}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "memos_config": {"enabled": True, "api_key": "key", "sync_selected_memory": True},
            }
        )
        runtime.memos = runtime._create_memos_service()
        scheduled = []

        def schedule(coro, label="", key=""):
            scheduled.append((label, key, coro))
            coro.close()
            return True

        runtime._schedule_background_task = schedule
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林")
        event.message_str = "明天提醒我买牛奶"

        saved = await runtime.maybe_capture_commitment_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertIsNotNone(saved)
        self.assertEqual(saved.content, "明天提醒我买牛奶")
        self.assertEqual(saved.trigger_date, "2026-05-25")
        self.assertIn("阿林", saved.people)
        self.assertEqual(scheduled[0][0], "MemOS 精选条目同步")
        self.assertTrue(scheduled[0][1].startswith("memos_items_"))
        self.assertIn("JSON 输出要求", provider.prompts[0])
        self.assertIn("缺少明确依据时使用空字符串", provider.prompts[0])
        self.assertIn("隐藏推理也必须站在“我”的角色视角判断", provider.prompts[0])
        self.assertEqual(provider.prompts[0].count("隐藏推理也必须站在“我”的角色视角判断"), 1)
        self.assertIn("人物称谓与性别规则", provider.prompts[0])
        self.assertIn("对方在人设中的线索：我是女生。 阿林是我的男死党", provider.prompts[0])
        self.assertIn("称谓校准：如果称谓与对方在人设中的线索冲突", provider.prompts[0])
        self.assertIn("隐藏推理、理由和输出字段都必须遵守人物称谓与性别规则", provider.prompts[0])
        self.assertIn("服务端隐藏推理的第一句必须以“我”开头", provider.prompts[0])
        self.assertEqual(provider.prompts[0].count("服务端隐藏推理的第一句必须以“我”开头"), 1)
        self.assertIn("不得把内心独白、旁白或“我……”句子写进最终可见输出", provider.prompts[0])
        self.assertEqual(provider.prompts[0].count("不得把内心独白、旁白或“我……”句子写进最终可见输出"), 1)
        self.assertIn("只写我此刻看到、想到、犹豫、决定和感受到的内容", provider.prompts[0])
        self.assertIn("不要使用复数主语、模型自述、系统自述", provider.prompts[0])
        self.assertIn(CORE_REASONING_ANTI_PATTERN_RULE, provider.prompts[0])
        self.assertIn(CORE_REASONING_FORBIDDEN_PATTERNS, provider.prompts[0])
        self.assertIn("不要把角色名括在“我（某某）”里", provider.prompts[0])
        self.assertIn("不要使用复数视角、规则审题、样本解析、任务说明等措辞", provider.prompts[0])
        self.assertIn("这是极短内部裁定，不需要展开分析", provider.prompts[0])
        self.assertIn("只能保留一句第一人称内心判断", provider.prompts[0])
        self.assertIn("隐藏推理只写我此刻的感受和判断；普通情绪表达不是未来约定。", provider.prompts[0])
        self.assertNotIn("我看到对方说想我了", provider.prompts[0])
        self.assertIn("隐藏推理第一句必须从“我”开始", provider.system_prompts[0])
        self.assertIn("禁止把隐藏推理、内心独白、解释、旁白或“我……”前置句写进最终可见输出", provider.system_prompts[0])
        self.assertIn("主语只用“我”", provider.system_prompts[0])
        self.assertIn(CORE_REASONING_ANTI_PATTERN_RULE, provider.system_prompts[0])
        self.assertIn("不要使用复数视角、规则审题、样本解析、任务说明等措辞", provider.system_prompts[0])
        self.assertNotIn("我们首先分析用户输入", provider.prompts[0])
        self.assertNotIn("好的，我需要判断", provider.prompts[0])
        self.assertNotIn("我们根据规则", provider.prompts[0])
        self.assertNotIn("我们分析当前情况", provider.prompts[0])
        self.assertNotIn("当前角色是我（深蓝）", provider.prompts[0])
        self.assertNotIn("我（深蓝）", provider.prompts[0])
        self.assertNotIn("我们分析当前情况", provider.system_prompts[0])
        self.assertNotIn("当前角色是我（深蓝）", provider.system_prompts[0])
        self.assertTrue(provider.prompts[0].startswith("隐藏推理口吻"))
        self.assertLess(provider.prompts[0].index("JSON 输出要求"), provider.prompts[0].index("【刚看到的聊天】"))
        self.assertLess(provider.prompts[0].index("说话人：阿林"), provider.prompts[0].index("当前日期时间：2026-05-24 12:00"))
        self.assertLess(provider.prompts[0].index("称谓校准：如果称谓与对方在人设中的线索冲突"), provider.prompts[0].index("日期换算：今天=2026-05-24"))
        self.assertGreater(provider.prompts[0].index("我刚看到的内容："), provider.prompts[0].index("【刚看到的聊天】"))
        self.assertIn("可见文本内容：明天提醒我买牛奶", provider.prompts[0])
        self.assertIn("真实图片组件：0 个", provider.prompts[0])

    async def test_commitment_capture_repairs_non_pure_json_reply(self):
        provider = Provider(
            [
                '我先判断这条是未来提醒。\n{"has_commitment":true,"content":"明天提醒我买牛奶","kind":"reminder",'
                '"trigger_date":"2026-05-25","trigger_time":"","time_window":"",'
                '"people":[],"place":"","confidence":0.91}',
                '{"has_commitment":true,"content":"明天提醒我买牛奶","kind":"reminder",'
                '"trigger_date":"2026-05-25","trigger_time":"","time_window":"","people":[],"place":"","confidence":0.91}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "阿林是我的男死党。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(sender_name="阿林")
        event.message_str = "明天提醒我买牛奶"

        saved = await runtime.maybe_capture_commitment_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertIsNotNone(saved)
        self.assertEqual(saved.content, "明天提醒我买牛奶")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("最终回复只能是一个 JSON 对象", provider.prompts[0])
        self.assertIn("第一个非空字符必须是 {", provider.prompts[1])
        self.assertIn("请把下面内容改写为严格 JSON 对象本体", provider.prompts[1])
        self.assertTrue(provider.prompts[1].startswith("隐藏推理口吻"))
        self.assertLess(provider.prompts[1].index("请把下面内容改写为严格 JSON 对象本体"), provider.prompts[1].index("【待修复 JSON】"))

    async def test_capture_prompts_treat_image_sent_text_as_text_only(self):
        provider = Provider(['{"has_commitment":false}', '{"worth_saving":false}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"min_message_length": 1},
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("测试用户乙"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。测试用户乙是我的男死党。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "测试用户乙是我的男死党。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="测试用户乙", sender_id="100000002")
        event.message_str = "[图片已发送]\n为什么带上这句回复"
        event.message_items = [{"type": "text", "data": {"text": event.message_str}}]

        commitment = await runtime.maybe_capture_commitment_from_event(
            event,
            now=datetime.datetime(2026, 6, 25, 18, 16),
        )
        summary = await runtime.maybe_capture_chat_memory_from_event(
            event,
            now=datetime.datetime(2026, 6, 25, 18, 16),
        )

        self.assertIsNone(commitment)
        self.assertIsNone(summary)
        self.assertEqual(len(provider.prompts), 2)
        for prompt in provider.prompts:
            self.assertIn("真实图片组件：0 个", prompt)
            self.assertIn("文本组件：1 个", prompt)
            self.assertIn("可见文本内容：[图片已发送]\n为什么带上这句回复", prompt)
            self.assertIn("不要把可见文本里的“[图片已发送]”“图片已发送”等字样当成图片", prompt)

    async def test_capture_skips_framework_command_events(self):
        provider = Provider(['{"has_commitment":true}', '{"worth_saving":true}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"min_message_length": 1},
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(lambda provider_id="": async_return(provider)),
                "_call_llm_text": staticmethod(lambda provider, prompt, session_id, **kwargs: async_return("{}")),
                "_cleanup_conversation": staticmethod(lambda session_id: async_return(None)),
                "learn_preferences_from_payload": staticmethod(lambda *args, **kwargs: async_return([])),
            },
        )()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")

        class CommandFilter:
            pass

        class Handler:
            event_filters = [CommandFilter()]

        event.message_str = "生活 状态"
        event.set_extra("activated_handlers", [Handler()])

        commitment = await runtime.maybe_capture_commitment_from_event(event)
        summary = await runtime.maybe_capture_chat_memory_from_event(event)

        self.assertIsNone(commitment)
        self.assertIsNone(summary)
        self.assertEqual(provider.prompts, [])

    async def test_collects_image_assets_and_uses_vision_provider(self):
        memory_provider = Provider([], provider_id="memory-model")
        vision_provider = Provider(
            ['{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇","围观"],"status":"ready"}'],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            memory_provider,
            providers={"memory-model": memory_provider, "vision-model": vision_provider},
        )
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"provider": "memory-model"},
                "vision_config": {"provider": "vision-model"},
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []

        def run_now(coro, label="", key=""):
            scheduled.append((label, key, coro))
            return True

        runtime._schedule_background_task = run_now
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展群",
            message_id="m-img",
        )
        event.message_items = [{"type": "image", "url": "https://example.com/peek.png"}]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
            sender_name="阿林",
        )
        self.assertEqual(len(scheduled), 1)
        await scheduled[0][2]

        assets = await runtime.archive.get_emoji_assets(10, status="ready")
        self.assertEqual(assets[0].label, "探头")
        self.assertEqual(assets[0].description, "适合轻轻围观的小表情")
        self.assertEqual(assets[0].source_scope, "20001")
        self.assertEqual(assets[0].source_message_id, "m-img")
        self.assertEqual(memory_provider.vision_prompts, [])
        self.assertEqual(vision_provider.vision_prompts[0]["image"], "https://example.com/peek.png")

    async def test_image_vision_updates_private_structured(self):
        vision_provider = Provider(
            [
                '{"summary":"桌上放着一盘切好的水果","label":"水果","description":"适合分享生活小吃","emotions":["日常"],"status":"ready"}'
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-private-img",
        )
        event.message_str = "看看这个"
        event.message_items = [{"type": "image", "url": "https://example.com/fruit.png"}]
        event.message_obj.message = event.message_items

        runtime.note_structured_incoming_message(event)
        self.assertTrue(runtime.schedule_visual_context_from_event(event))
        self.assertEqual(scheduled[0][0], "图片上下文识别")
        index = 0
        while index < len(scheduled):
            await scheduled[index][2]
            index += 1

        context = runtime.format_structured_message_context(event)
        self.assertIn("看看这个 [图片：桌上放着一盘切好的水果]", context)
        self.assertEqual(vision_provider.vision_prompts[0]["image"], "https://example.com/fruit.png")
        assets = await runtime.archive.get_emoji_assets(10)
        self.assertEqual(assets, [])

    async def test_collects_image_assets_copies_local_file_to_plugin_cache(self):
        vision_provider = Provider(
            ['{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇"],"status":"ready"}'],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "incoming.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")

            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:GroupMessage:20001",
                group_id="20001",
                group_name="看展群",
                message_id="m-img-local",
            )
            event.message_items = [{"type": "image", "path": str(source_path)}]
            event.message_obj.message = event.message_items

            await runtime.maybe_collect_emoji_assets_from_event(event)
            self.assertEqual(scheduled[0][0], "表情素材缓存与识别")
            await scheduled[0][2]

            assets = await runtime.archive.get_emoji_assets(10, status="ready")
            cached_path = Path(assets[0].file_path)
            self.assertTrue(cached_path.is_file())
            self.assertEqual(cached_path.parent, tmp_root / "emoji_assets")
            self.assertEqual(cached_path.read_bytes(), source_path.read_bytes())
            self.assertEqual(vision_provider.vision_prompts[0]["image"], str(cached_path))

    async def test_cleanup_emoji_asset_cache_removes_only_unreferenced_files(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            cache_dir = tmp_root / "emoji_assets"
            cache_dir.mkdir()
            referenced = cache_dir / "referenced.png"
            orphan = cache_dir / "orphan.png"
            referenced.write_bytes(b"\x89PNG\r\n\x1a\nreferenced")
            orphan.write_bytes(b"\x89PNG\r\n\x1a\norphan")
            old_time = (datetime.datetime.now() - datetime.timedelta(days=2)).timestamp()
            os.utime(orphan, (old_time, old_time))

            await runtime.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    file_hash="referenced",
                    file_path=str(referenced),
                    label="还在使用",
                    status="ready",
                )
            )

            deleted = await runtime.cleanup_emoji_asset_cache()

            self.assertEqual(deleted, 1)
            self.assertTrue(referenced.exists())
            self.assertFalse(orphan.exists())

    async def test_cleanup_emoji_asset_cache_keeps_fresh_orphan_file(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            cache_dir = tmp_root / "emoji_assets"
            cache_dir.mkdir()
            orphan = cache_dir / "fresh.png"
            orphan.write_bytes(b"\x89PNG\r\n\x1a\nfresh")

            deleted = await runtime.cleanup_emoji_asset_cache()

            self.assertEqual(deleted, 0)
            self.assertTrue(orphan.exists())

    async def test_failed_emoji_asset_is_not_rescheduled_by_same_image(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        runtime._schedule_background_task = lambda coro, label="", key="": False

        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="failed-hash",
                file_path="https://example.com/failed.png",
                status="failed",
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-failed",
        )
        event.message_items = [{"type": "image", "url": "https://example.com/failed.png"}]
        event.message_obj.message = event.message_items
        runtime._media_fingerprint = lambda payload: "failed-hash"

        await runtime.maybe_collect_emoji_assets_from_event(event)

        assets = await runtime.archive.get_emoji_assets(10)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].status, "failed")

    async def test_emoji_asset_records_message_id_and_source_url_separately(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-url",
        )
        event.message_items = [{"type": "image", "url": "https://example.com/source.png"}]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)

        assets = await runtime.archive.get_emoji_assets(10)
        self.assertEqual(assets[0].source_message_id, "m-img-url")
        self.assertEqual(assets[0].source_url, "https://example.com/source.png")
        for _, _, coro in scheduled:
            coro.close()

    async def test_maintain_emoji_assets_marks_missing_and_prunes_over_limit(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.data_path = Path(tempfile.gettempdir()) / "daily_life_test.db"
        runtime.EMOJI_ASSET_MAX_READY = 2

        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="missing",
                file_path=str(Path(tempfile.gettempdir()) / "not-exists.png"),
                status="ready",
            )
        )
        for index in range(4):
            await runtime.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    file_hash=f"ready-{index}",
                    file_path=f"https://example.com/{index}.png",
                    label=f"表情{index}",
                    status="ready",
                    used_count=index,
                )
            )

        result = await runtime.maintain_emoji_assets()

        assets = await runtime.archive.get_emoji_assets(limit=0)
        self.assertEqual(result["missing_marked"], 1)
        self.assertEqual(result["deleted_records"], 2)
        self.assertEqual(len([item for item in assets if item.status == "ready"]), 2)
        self.assertEqual((await runtime.archive.get_emoji_asset_by_hash("missing")).status, "missing")

    async def test_vision_provider_unset_uses_current_default_provider(self):
        default_provider = Provider(
            ['{"label":"默认识别","description":"默认模型识别的小表情","emotions":["轻松"],"status":"ready"}'],
            provider_id="default-model",
        )
        memory_provider = Provider([], provider_id="memory-model")
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            default_provider,
            providers={"memory-model": memory_provider},
        )
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"provider": "memory-model"},
                "vision_config": {"provider": ""},
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展群",
            message_id="m-img-default",
        )
        event.message_items = [{"type": "image", "url": "https://example.com/default.png"}]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)
        await scheduled[0][2]

        self.assertEqual(default_provider.vision_prompts[0]["image"], "https://example.com/default.png")
        self.assertEqual(memory_provider.vision_prompts, [])

    async def test_maybe_capture_chat_memory_from_event_saves_summary_and_relationship_point(self):
        provider = Provider(
            [
                '{"worth_saving":true,"brief":"阿林说周末想去展览馆",'
                '"long_summary":"阿林提到周末想一起看展，顺便去书店。",'
                '"people":["阿林"],'
                '"relationship_points":["阿林是男性死党，最近对看展很感兴趣"]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "周末我们去展览馆看展吧，结束后还能顺路逛书店。"

        saved = await runtime.maybe_capture_chat_memory_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        relationships = await runtime.archive.get_recent_relationships(10)
        summaries = await runtime.archive.get_recent_chat_summaries(10)
        self.assertIsNotNone(saved)
        self.assertEqual(summaries[0].brief, "阿林说周末想去展览馆")
        self.assertEqual(relationships[0].name, "阿林")
        self.assertEqual(relationships[0].platform, "aiocqhttp")
        self.assertEqual(relationships[0].user_id, "10001")
        self.assertEqual(relationships[0].memory_points[0].content, "阿林是男性死党，最近对看展很感兴趣")
        self.assertIn("通用记忆原则", provider.prompts[0])
        self.assertIn("群聊信息必须先判断归属", provider.prompts[0])
        self.assertLess(provider.prompts[0].index("通用记忆原则"), provider.prompts[0].index("【眼前内容】"))
        self.assertIn("身份边界", provider.prompts[0])
        self.assertIn("当前角色：我（不是消息发送者）", provider.prompts[0])
        self.assertIn("消息发送者/对方：阿林", provider.prompts[0])
        self.assertIn("对方 profile_id：10001", provider.prompts[0])
        self.assertLess(provider.prompts[0].index("对方已保存档案"), provider.prompts[0].index("当前日期时间：2026-05-24 12:00"))
        self.assertLess(provider.prompts[0].index("当前日期时间：2026-05-24 12:00"), provider.prompts[0].index("我刚看到对方发来的内容："))
        self.assertGreater(provider.prompts[0].index("我刚看到对方发来的内容："), provider.prompts[0].index("【眼前内容】"))
        self.assertIn("可见文本内容：周末我们去展览馆看展吧，结束后还能顺路逛书店。", provider.prompts[0])
        self.assertIn("真实图片组件：0 个", provider.prompts[0])

    async def test_chat_memory_capture_repairs_non_pure_json_reply(self):
        provider = Provider(
            [
                '我看到阿林在认真约我。\n{"worth_saving":true,"brief":"阿林说周末想去展览馆",'
                '"long_summary":"阿林提到周末想一起看展。","people":["阿林"],'
                '"relationship_points":["阿林最近对看展很感兴趣"]}',
                '{"worth_saving":true,"brief":"阿林说周末想去展览馆",'
                '"long_summary":"阿林提到周末想一起看展。","people":["阿林"],'
                '"relationship_points":["阿林最近对看展很感兴趣"]}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "阿林是我的男死党。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "周末我们去展览馆看展吧。"

        saved = await runtime.maybe_capture_chat_memory_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertIsNotNone(saved)
        self.assertEqual(saved.brief, "阿林说周末想去展览馆")
        self.assertIn("最终回复只能是一个 JSON 对象", provider.prompts[0])
        repair_prompts = [prompt for prompt in provider.prompts if "请把下面内容改写为严格 JSON 对象本体" in prompt]
        self.assertEqual(len(repair_prompts), 1)
        self.assertIn("第一个非空字符必须是 {", repair_prompts[0])
        self.assertTrue(repair_prompts[0].startswith("隐藏推理口吻"))
        self.assertLess(repair_prompts[0].index("请把下面内容改写为严格 JSON 对象本体"), repair_prompts[0].index("【待修复 JSON】"))

    async def test_chat_memory_calibrates_summary_before_persisting(self):
        provider = Provider(
            [
                '{"worth_saving":true,'
                '"brief":"阿林问我是不是只看到这些内容，像是在确认我有没有认真看她发的东西。",'
                '"long_summary":"阿林发来确认，像是在问我有没有认真看她刚发的资料。",'
                '"people":["阿林"],'
                '"subjective_impression":{"subjective_name":"阿林","tags":["会催我看资料"],'
                '"relationship_story":"她会用调侃语气确认我有没有认真看内容。",'
                '"impression_delta":"这次她又来确认我看没看完整。"},'
                '"relationship_points":["阿林会主动确认我有没有认真看她发的内容。"],'
                '"memory_targets":[{"profile_id":"10001","name":"阿林","subjective_name":"阿林",'
                '"relationship_story":"她会确认我有没有看她发的资料。",'
                '"points":["会确认我有没有认真看她发的内容。"],'
                '"note":"这次又确认我有没有看完整","source":"speaker"}]}',
                '{"needs_revision":true,"reason":"人设线索是男死党，记忆摘要和关系内容用了女性称谓。",'
                '"revised":{"brief":"阿林问我是不是只看到这些内容，像是在确认我有没有认真看他发的东西。",'
                '"long_summary":"阿林发来确认，像是在问我有没有认真看他刚发的资料。",'
                '"subjective_impression":{"subjective_name":"阿林","tags":["会催我看资料"],'
                '"relationship_story":"他会用调侃语气确认我有没有认真看内容。",'
                '"impression_delta":"这次他又来确认我看没看完整。"},'
                '"relationship_points":["阿林会主动确认我有没有认真看他发的内容。"],'
                '"memory_targets":[{"profile_id":"10001","name":"阿林","subjective_name":"阿林",'
                '"relationship_story":"他会确认我有没有看他发的资料。",'
                '"points":["会确认我有没有认真看他发的内容。"],'
                '"note":"这次又确认我有没有看完整","source":"speaker"}]}}',
                '{"needs_revision":false,"reason":"","relationship_story":""}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，会把有意思的资料发给我看。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，会把有意思的资料发给我看。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "你只看到这些内容吗？"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 22, 14, 58))

        relationship = await runtime.archive.get_relationship("10001")
        self.assertIsNotNone(saved)
        self.assertIn("看他发的东西", saved.brief)
        self.assertNotIn("她", saved.brief)
        self.assertIn("认真看他发的内容", relationship.memory_points[0].content)
        self.assertNotIn("她", relationship.relationship_story)
        self.assertIn("待审阅聊天记忆", provider.prompts[1])
        self.assertIn("这不是文本清洗，不要机械替换字词", provider.prompts[1])

    async def test_chat_memory_calibrates_target_memory_before_persisting(self):
        provider = Provider(
            [
                '{"worth_saving":true,'
                '"brief":"阿林提到柏舟又发了资料",'
                '"long_summary":"阿林在群里提到柏舟发资料的事。",'
                '"people":["阿林","柏舟"],'
                '"memory_targets":[{"profile_id":"name:柏舟","name":"柏舟",'
                '"persona_hint":"柏舟是我的男同学，常帮我整理资料。",'
                '"subjective_name":"柏舟","subjective_tags":["会整理资料"],'
                '"relationship_story":"她经常帮我整理资料。",'
                '"points":["柏舟会把她整理好的资料发给我。"],'
                '"note":"这次她又整理了资料","source":"mentioned_person"}]}',
                '{"needs_revision":true,"reason":"目标人设线索是男同学，目标记忆使用了女性称谓。",'
                '"revised":{"relationship_story":"他经常帮我整理资料。",'
                '"points":["柏舟会把他整理好的资料发给我。"],'
                '"note":"这次他又整理了资料"}}',
                '{"needs_revision":false,"reason":"","revised":{}}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()
        memos_items = []
        runtime.schedule_memos_selected_items = (
            lambda meta, items, **kwargs: memos_items.extend(items) or True
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。柏舟是我的男同学，常帮我整理资料。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "柏舟是我的男同学，常帮我整理资料。" if name == "柏舟" else ""

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="资料群",
            message_id="m-target-calibration",
        )
        event.message_str = "柏舟又把资料整理好了，回头我转给你。"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 22, 15, 20))

        relationship = await runtime.archive.get_relationship("name:柏舟")
        self.assertIsNotNone(relationship)
        self.assertIn("他经常帮我整理资料", relationship.relationship_story)
        self.assertIn("把他整理好的资料", relationship.memory_points[0].content)
        self.assertNotIn("她", relationship.relationship_story)
        self.assertFalse(any("她" in item for item in memos_items))
        self.assertTrue(any("把他整理好的资料" in item for item in memos_items))
        self.assertIn("待审阅目标记忆", provider.prompts[1])
        self.assertIn("不要机械替换字词", provider.prompts[1])

    async def test_chat_memory_calibrates_skipped_impression_before_persisting(self):
        provider = Provider(
            [
                '{"worth_saving":false,'
                '"subjective_impression":{"subjective_name":"阿林","tags":["爱催我"],'
                '"relationship_story":"她总会顺手催我看资料。",'
                '"impression_delta":"她又催我看资料。"}}',
                '{"needs_revision":true,"reason":"人设线索是男死党，主观印象用了女性称谓。",'
                '"revised":{"subjective_impression":{"subjective_name":"阿林","tags":["爱催我"],'
                '"relationship_story":"他总会顺手催我看资料。",'
                '"impression_delta":"他又催我看资料。"}}}',
                '{"needs_revision":false,"reason":"","revised":{}}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，会把有意思的资料发给我看。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "阿林是我的男死党，性格爽朗，会把有意思的资料发给我看。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "你又没认真看吧。"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 22, 15, 30))

        relationship = await runtime.archive.get_relationship("10001")
        self.assertIsNone(saved)
        self.assertIsNotNone(relationship)
        self.assertIn("他总会顺手催我看资料", relationship.relationship_story)
        self.assertIn("他又催我看资料", relationship.notes[-1].content)
        self.assertNotIn("她", relationship.relationship_story)
        self.assertIn("待审阅聊天记忆", provider.prompts[1])

    async def test_group_chat_memory_uses_sender_id_not_group_session(self):
        provider = Provider(
            [
                '{"worth_saving":true,"brief":"Alice discussed Diablo",'
                '"long_summary":"Alice discussed Diablo updates.",'
                '"people":["Alice"],'
                '"relationship_points":["Alice likes Diablo"]}',
                '{"worth_saving":true,"brief":"Bob discussed Zelda",'
                '"long_summary":"Bob discussed Zelda updates.",'
                '"people":["Bob"],'
                '"relationship_points":["Bob likes Zelda"]}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        group_umo = "aiocqhttp:GroupMessage:20001"
        alice = Event(sender_name="Alice", sender_id="10001", unified_msg_origin=group_umo)
        alice.message_str = "I want to keep watching Diablo updates."
        bob = Event(sender_name="Bob", sender_id="10002", unified_msg_origin=group_umo)
        bob.message_str = "I want to keep watching Zelda updates."

        await runtime.maybe_capture_chat_memory_from_event(alice, now=datetime.datetime(2026, 5, 24, 12, 0))
        await runtime.maybe_capture_chat_memory_from_event(bob, now=datetime.datetime(2026, 5, 24, 12, 1))

        relationships = {item.id: item for item in await runtime.archive.get_recent_relationships(10)}
        self.assertNotIn("20001", relationships)
        self.assertEqual(set(relationships), {"10001", "10002"})
        self.assertEqual(relationships["10001"].name, "Alice")
        self.assertEqual(relationships["10001"].user_id, "10001")
        self.assertEqual(relationships["10001"].memory_points[0].content, "Alice likes Diablo")
        self.assertEqual(relationships["10002"].name, "Bob")
        self.assertEqual(relationships["10002"].user_id, "10002")
        self.assertEqual(relationships["10002"].memory_points[0].content, "Bob likes Zelda")

    async def test_group_chat_memory_splits_mentioned_member_profile_and_records_decision(self):
        provider = Provider(
            [
                '{"worth_saving":true,'
                '"brief":"Alice 提到 Bob 最近在准备看展",'
                '"long_summary":"群聊里 Alice 说 Bob 最近在准备看展，还想找人一起去。",'
                '"people":["Alice","Bob"],'
                '"visibility":{"level":"focused","attention_level":80,"priority":"normal","is_directed_at_bot":false,"reason":"包含可沉淀的群友近况"},'
                '"group_environment":{"atmosphere":"平稳","topic":"Bob 准备看展","topic_owner":"target_user_topic","active_users":2,'
                '"is_multithread":false,"is_spam":false,"is_repetition":false,"is_discussing_bot":false,'
                '"suitable_to_join":"observe","bot_watch_state":"peek","participation_desire":35,"complexity_score":42,'
                '"understanding_confidence":88,"deep_analysis_needed":false,"summary":"群里在聊 Bob 的近况"},'
                '"action_decision":{"action":"save_memory","reason":"这是 Bob 的稳定近况，不应挂到 Alice 身上","confidence":0.92,'
                '"scene_type":"群友档案","topic_owner":"target_user_topic","understanding":"understood","deep_analysis":false,'
                '"inner_monologue":"这条是 Bob 的信息，不能挂到 Alice 身上。","reply_strategy":"观察为主，必要时轻轻接话。"},'
                '"relationship_points":[],'
                '"memory_targets":[{"profile_id":"name:Bob","name":"Bob","points":["Bob 最近在准备看展，可能想找人一起去。"],'
                '"note":"被 Alice 提到正在准备看展","source":"mentioned_person"}]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="Alice",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展小群",
            message_id="m1",
        )
        event.message_str = "Bob 最近在准备看展，好像还想找人一起去。"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        relationships = {item.id: item for item in await runtime.archive.get_recent_relationships(10)}
        environments = await runtime.archive.get_recent_group_environments(10)
        decisions = await runtime.archive.get_recent_action_decisions(10)
        visibility = await runtime.archive.get_recent_message_visibility(10)
        self.assertIsNotNone(saved)
        self.assertIn("10001", relationships)
        self.assertIn("name:Bob", relationships)
        self.assertEqual(relationships["10001"].memory_points, [])
        self.assertEqual(relationships["name:Bob"].memory_points[0].content, "Bob 最近在准备看展，可能想找人一起去。")
        self.assertEqual(environments[0].group_name, "看展小群")
        self.assertEqual(environments[0].topic_owner, "target_user_topic")
        self.assertEqual(environments[0].participation_desire, 35)
        self.assertEqual(environments[0].complexity_score, 42)
        self.assertEqual(environments[0].understanding_confidence, 88)
        self.assertEqual(decisions[0].action, "save_memory")
        self.assertIn("不应挂到 Alice", decisions[0].reason)
        self.assertIn("不能挂到 Alice", decisions[0].inner_monologue)
        self.assertIn("观察为主", decisions[0].reply_strategy)
        self.assertEqual(visibility[0].visibility, "focused")

    async def test_seen_but_ignored_leaves_subjective_trace_without_summary(self):
        provider = Provider(
            [
                '{"worth_saving":false,'
                '"visibility":{"level":"seen_but_ignored","attention_level":32,"priority":"low","is_directed_at_bot":false,'
                '"freshness":"recent","psychological_freshness":58,"reactivated_from_id":0,'
                '"reactivation_hint":"如果后面有人继续接这个话题再回看","reason":"扫到了但此刻不想接普通闲聊"},'
                '"group_environment":{"atmosphere":"平稳","topic":"随口闲聊","topic_owner":"shared_group_topic","active_users":2,'
                '"is_multithread":false,"is_spam":false,"is_repetition":false,"is_discussing_bot":false,'
                '"suitable_to_join":"observe","bot_watch_state":"skim_window","participation_desire":22,"complexity_score":10,'
                '"understanding_confidence":76,"deep_analysis_needed":false,"summary":"群里在轻松闲聊"},'
                '"action_decision":{"action":"observe","reason":"看见了但状态不想展开","confidence":0.8,'
                '"scene_type":"普通闲聊","topic_owner":"shared_group_topic","understanding":"understood","deep_analysis":false,'
                '"inner_monologue":"看到了，先不接，等话题自己过去。","reply_strategy":"继续观察"},'
                '"subjective_impression":{"subjective_name":"会让我多看一眼的人","tags":["有点吵但不讨厌"],'
                '"relationship_story":"有时候会被这个人拉回群聊注意力。","impression_delta":"这次看见了但略过，留下了一点回避感。"},'
                '"relationship_points":[],"memory_targets":[],"preference_points":[]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        await runtime.archive.save_day(DayRecord(date="2026-05-24"))
        event = Event(
            sender_name="Alice",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="测试群",
            message_id="m2",
        )
        event.message_str = "今天又是摸鱼的一天。"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 5))

        self.assertIsNone(saved)
        self.assertEqual(await runtime.archive.get_recent_chat_summaries(10), [])
        relationships = await runtime.archive.get_recent_relationships(10)
        visibility = await runtime.archive.get_recent_message_visibility(10)
        day = await runtime.archive.get_day("2026-05-24")
        self.assertEqual(relationships[0].subjective_name, "会让我多看一眼的人")
        self.assertEqual(relationships[0].subjective_tags, ["有点吵但不讨厌"])
        self.assertIn("回避感", relationships[0].notes[-1].content)
        self.assertEqual(visibility[0].visibility, "seen_but_ignored")
        self.assertEqual(visibility[0].psychological_freshness, 58)
        self.assertTrue(any("看见了但状态不想展开" in item for item in day.state_log))
        self.assertIn("人物称谓与性别规则", provider.prompts[0])
        self.assertIn("没有明确性别依据时用对方、这个人或称呼，不要写他/她", provider.prompts[0])
        self.assertIn("对方已保存档案", provider.prompts[0])

    async def test_chat_memory_prompt_uses_saved_persona_hint_for_pronoun_choice(self):
        provider = Provider(['{"worth_saving":false}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            date_str="2026-05-23",
            persona_hint="男生，喜欢看展",
            subjective_tags=["可靠"],
            relationship_story="我和阿林聊展览时比较放松，她总会提到新展。",
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "这周末想去看展。"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 5))

        prompt = provider.prompts[0]
        self.assertIn("对方已保存人设线索：男生，喜欢看展", prompt)
        self.assertIn("对方在人设中的线索：我是女生。 阿林是我的男死党", prompt)
        self.assertIn("对方已保存关系短标签：可靠", prompt)
        self.assertIn("对方已保存关系叙事：我和阿林聊展览时比较放松，她总会提到新展。", prompt)
        self.assertIn("称谓校准：对方在人设中的线索优先级最高", prompt)
        self.assertIn("对方已保存关系叙事、已保存印象或既有记忆里的性别称谓与它冲突", prompt)
        self.assertIn("性别与亲密称谓必须有明确依据", prompt)
        self.assertIn('即使最终只输出 {"worth_saving": false}', prompt)
        self.assertIn("服务端记录的隐藏推理也必须遵守这条人设线索", prompt)
        self.assertIn("不得在隐藏推理里继续使用与人设线索冲突的他/她", prompt)

    async def test_chat_memory_prompt_uses_neutral_pronoun_when_persona_match_fails(self):
        provider = Provider(
            [
                '{"matched":false,"name":"","persona_hint":"","confidence":0.0,"reason":""}',
                '{"worth_saving":false}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._persona_hint_cache = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="小林",
            date_str="2026-06-21",
            relationship_story="她平时会记得我想去看展。",
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self, umo=""):
                return "我是女生。林远是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return ""

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="小林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "今天只是随便聊两句。"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 21, 17, 30))

        persona_prompt = provider.prompts[0]
        memory_prompt = provider.prompts[1]
        self.assertIn("已保存关系叙事：她平时会记得我想去看展。", persona_prompt)
        self.assertIn("如果只是昵称相似、语气像、平台标识、旧印象或猜测，matched=false", persona_prompt)
        self.assertIn("对方已保存关系叙事：她平时会记得我想去看展。", memory_prompt)
        self.assertIn("本轮没有从当前人设提取到对方性别线索", memory_prompt)
        self.assertIn("已保存叙事里零散出现的他/她不能当作性别依据", memory_prompt)
        self.assertIn("隐藏推理和输出都必须使用中性称呼", memory_prompt)
        self.assertIn("隐藏推理也不能猜测性别", memory_prompt)

    async def test_relationship_calibration_rewrites_conflicting_saved_profile(self):
        provider = Provider(
            [
                '{"needs_revision":true,"reason":"已保存关系叙事把男死党写成女性称谓。",'
                '"subjective_name":"男死党阿林","subjective_tags":["可靠"],'
                '"relationship_story":"我和阿林熟到可以互相吐槽，他也会认真记住邀约。",'
                '"note":"按人设线索校准为男性死党。",'
                '"relationship_points":["阿林是我的男死党，喜欢看展。"]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._relationship_calibration_cache = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            date_str="2026-05-23",
            persona_hint="男生，死党",
            subjective_name="会让我多看一眼的人",
            subjective_tags=["可靠"],
            relationship_story="我和阿林熟到可以互相吐槽，她也会认真记住邀约。",
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()

        await runtime._calibrate_relationship_profile(
            "10001",
            "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。",
            "2026-05-24",
        )

        relationship = await runtime.archive.get_relationship("10001")
        self.assertEqual(relationship.subjective_name, "男死党阿林")
        self.assertEqual(relationship.relationship_story, "我和阿林熟到可以互相吐槽，他也会认真记住邀约。")
        self.assertEqual(relationship.notes[-1].content, "按人设线索校准为男性死党。")
        self.assertEqual(relationship.memory_points[-1].content, "阿林是我的男死党，喜欢看展。")
        self.assertIn("这不是文本清洗，不要机械替换字词", provider.prompts[0])
        self.assertIn("对方在人设中的线索：我是女生。阿林是我的男死党", provider.prompts[0])

    async def test_relationship_calibration_keeps_profile_when_llm_finds_no_conflict(self):
        provider = Provider(['{"needs_revision":false,"reason":"","relationship_story":""}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._relationship_calibration_cache = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            date_str="2026-05-23",
            persona_hint="男生，死党",
            relationship_story="我和阿林熟到可以互相吐槽，他也会认真记住邀约。",
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()

        await runtime._calibrate_relationship_profile(
            "10001",
            "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。",
            "2026-05-24",
        )

        relationship = await runtime.archive.get_relationship("10001")
        self.assertEqual(relationship.relationship_story, "我和阿林熟到可以互相吐槽，他也会认真记住邀约。")
        self.assertEqual(relationship.notes, [])
        self.assertEqual(relationship.memory_points, [])

    async def test_chat_memory_prompt_extracts_persona_hint_when_profile_is_empty(self):
        provider = Provider(
            [
                '{"worth_saving":false,'
                '"visibility":{"level":"seen"},'
                '"subjective_impression":{"relationship_story":"我和阿林打球聊天时比较放松。"}}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格很爽朗，经常约我打球。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格很爽朗，经常约我打球。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "周末一起打球吗？"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 5))

        prompt = provider.prompts[0]
        self.assertIn("对方在人设中的线索：我是女生。 阿林是我的男死党，性格很爽朗，经常约我打球。", prompt)
        self.assertIn("人物称谓与性别规则", prompt)
        self.assertIn("不要根据昵称、头像、平台标识、语气、表情、刻板印象或上下文习惯猜测性别", prompt)
        relationship = await runtime.archive.get_relationship("10001")
        self.assertEqual(relationship.persona_hint, "我是女生。 阿林是我的男死党，性格很爽朗，经常约我打球。")

    async def test_persona_hint_semantic_extract_repairs_alias_mismatch(self):
        provider = Provider(
            [
                '{"matched":true,"name":"林远","persona_hint":"林远是我的男死党，性格爽朗，经常约我看展。","confidence":0.86,"reason":"当前昵称是备注名，和人设里的林远对应同一位死党。"}',
                '{"worth_saving":false,'
                '"visibility":{"level":"seen"},'
                '"subjective_impression":{"subjective_name":"小林","tags":["可靠"],'
                '"relationship_story":"她总会记得我想去看展。","impression_delta":"这次又顺手来关心我。"}}',
                '{"needs_revision":true,"reason":"主观印象把男死党写成女性称谓。",'
                '"revised":{"subjective_impression":{"subjective_name":"小林","tags":["可靠"],'
                '"relationship_story":"他总会记得我想去看展。","impression_delta":"这次又顺手来关心我。"}}}',
                '{"needs_revision":true,"reason":"关系叙事把男死党写成女性称谓。",'
                '"subjective_name":"小林","subjective_tags":["可靠"],'
                '"relationship_story":"他总会记得我想去看展。",'
                '"note":"按对方人设线索校准为男性死党。",'
                '"relationship_points":["林远是我的男死党，性格爽朗，经常约我看展。"]}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._persona_hint_cache = {}
        runtime._relationship_calibration_cache = {}
        await runtime.archive.touch_relationship(
            "10000001",
            name="小林",
            date_str="2026-06-21",
            relationship_story="她平时会记得我想去看展。",
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("小林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self, umo=""):
                return "我是女生。林远是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return ""

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="小林",
            sender_id="10000001",
            unified_msg_origin="aiocqhttp:FriendMessage:10000001",
        )
        event.message_str = "周末还去看展吗？"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 21, 15, 0))

        relationship = await runtime.archive.get_relationship("10000001")
        self.assertEqual(relationship.persona_hint, "林远是我的男死党，性格爽朗，经常约我看展。")
        self.assertEqual(relationship.relationship_story, "他总会记得我想去看展。")
        self.assertEqual(relationship.notes[-1].content, "按对方人设线索校准为男性死党。")
        self.assertIn("当前角色人设", provider.prompts[0])
        self.assertIn("这不是关键词匹配", provider.prompts[0])
        self.assertIn("对方在人设中的线索：林远是我的男死党", provider.prompts[1])
        self.assertIn("对方在人设中的线索：林远是我的男死党", provider.prompts[2])
        self.assertIn("对方在人设中的线索：林远是我的男死党", provider.prompts[3])

    async def test_persona_hint_semantic_extract_sees_late_persona_sections(self):
        provider = Provider(
            [
                '{"matched":true,"name":"林远","persona_hint":"林远是我的家人兼死党，平常称呼小林，性别男。","confidence":0.92,"reason":"小林是备注称呼，和人设中的林远/小林对应。"}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._persona_hint_cache = {}
        runtime._relationship_calibration_cache = {}
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("小林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self, umo=""):
                return (
                    "我是女生。"
                    + "日常性格描述。" * 500
                    + "情感与家人：我的家人兼死党是“林远”（平常称呼“小林”），性别男，特别好的朋友，纯友谊。"
                )

            @staticmethod
            def _extract_reference_persona(persona, name):
                return ""

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="小林",
            sender_id="10000001",
            unified_msg_origin="aiocqhttp:FriendMessage:10000001",
        )
        event.message_str = "在忙什么呢？"

        hint = await runtime._extract_speaker_persona_hint("小林", event=event)

        self.assertEqual(hint, "林远是我的家人兼死党，平常称呼小林，性别男。")
        self.assertIn("情感与家人", provider.prompts[0])
        self.assertIn("候选称呼可能是备注、群昵称、简称或日常称呼", provider.prompts[0])

    async def test_private_memory_awareness_does_not_save_group_environment(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        payload = {
            "visibility": {"level": "seen", "reason": "私聊消息"},
            "group_environment": {"atmosphere": "亲密", "topic": "私聊亲密表达"},
            "action_decision": {"action": "observe", "reason": "只是私聊"},
        }
        meta = {
            "session_id": "aiocqhttp:FriendMessage:10001",
            "message_id": "m1",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "",
            "group_name": "",
            "date": "2026-05-24",
            "is_group": "false",
        }

        await runtime._save_memory_awareness_records(payload, meta)

        self.assertEqual(await runtime.archive.get_recent_group_environments(10), [])
        self.assertEqual((await runtime.archive.get_recent_message_visibility(10))[0].reason, "私聊消息")
        self.assertEqual((await runtime.archive.get_recent_action_decisions(10))[0].reason, "只是私聊")

    async def test_memory_awareness_skips_empty_visibility_and_decision_shells(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        await runtime.archive.save_day(DayRecord(date="2026-05-24"))
        meta = {
            "session_id": "aiocqhttp:GroupMessage:20001",
            "message_id": "m1",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "20001",
            "group_name": "测试群",
            "date": "2026-05-24",
            "is_group": "true",
        }

        await runtime._save_memory_awareness_records({"worth_saving": False}, meta)
        await runtime._save_memory_awareness_records(
            {
                "visibility": {"level": "seen", "attention_level": 0, "psychological_freshness": 0},
                "action_decision": {"action": "skip_memory", "confidence": 0},
            },
            meta,
        )

        self.assertEqual(await runtime.archive.get_recent_message_visibility(10), [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(10), [])
        self.assertEqual(await runtime.archive.get_recent_group_environments(10), [])
        await runtime._append_memory_decision_log(
            {"visibility": {"level": "seen"}, "action_decision": {"action": "skip_memory"}},
            meta,
            datetime.datetime(2026, 5, 24, 12, 0),
        )
        self.assertEqual((await runtime.archive.get_day("2026-05-24")).state_log, [])

    async def test_memory_awareness_keeps_effective_visibility_and_decision_results(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        meta = {
            "session_id": "aiocqhttp:GroupMessage:20001",
            "message_id": "m1",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "20001",
            "group_name": "测试群",
            "date": "2026-05-24",
            "is_group": "true",
        }
        payload = {
            "visibility": {
                "level": "seen_but_ignored",
                "attention_level": 32,
                "psychological_freshness": 58,
                "reason": "看见了但状态不想展开",
            },
            "action_decision": {
                "action": "observe",
                "reason": "先观察，不急着接话",
                "reply_strategy": "等话题自然落点",
            },
        }

        await runtime._save_memory_awareness_records(payload, meta)

        visibility = await runtime.archive.get_recent_message_visibility(10)
        decisions = await runtime.archive.get_recent_action_decisions(10)
        self.assertEqual(visibility[0].visibility, "seen_but_ignored")
        self.assertEqual(visibility[0].reason, "看见了但状态不想展开")
        self.assertEqual(decisions[0].action, "observe")
        self.assertEqual(decisions[0].reply_strategy, "等话题自然落点")

    async def test_memory_awareness_keeps_llm_text_without_cleanup(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        payload = {
            "visibility": {
                "level": "focused",
                "reason": "用户正在讨论机器人本身",
                "reactivation_hint": "后续如果@机器人再重新关注",
            },
            "group_environment": {
                "atmosphere": "玩梗",
                "topic": "机器人称呼梗",
                "summary": "群里在讨论机器人和用户之间的称呼",
                "is_discussing_bot": True,
            },
            "action_decision": {
                "action": "save_memory",
                "reason": "与机器人之间的称呼有延续价值",
                "inner_monologue": "他们在说机器人像神兽",
                "reply_strategy": "机器人先观察",
            },
            "subjective_impression": {
                "relationship_story": "与机器人之间有轻松玩梗关系",
                "impression_delta": "用户把机器人叫成神兽，留下轻松印象",
            },
            "life_terms": [
                {
                    "term": "神兽",
                    "meaning": "用来调侃机器人像需要投喂和照看的存在",
                    "evidence": "群里说与机器人之间的称呼",
                }
            ],
            "evidence_refs": [
                {
                    "target_type": "relationship",
                    "target_id": "10001",
                    "summary": "用户把机器人叫神兽",
                }
            ],
            "relationship_points": ["用户会用神兽调侃机器人"],
            "behavior_scenes": [
                {
                    "scene": "围观称呼梗",
                    "cues": ["群里调侃称呼"],
                    "preferred_action": "observe",
                    "avoid_action": "认真纠正",
                    "outcome_hint": "先看大家怎么接",
                    "confidence": 0.7,
                }
            ],
            "focus_slots": [
                {
                    "focus_key": "nickname_joke",
                    "label": "称呼梗",
                    "priority": 66,
                    "reason": "刚被群里提起",
                }
            ],
            "memory_corrections": [
                {
                    "target_type": "life_episode",
                    "target_id": "999",
                    "correction": "这个旧片段不应作为事实引用",
                    "evidence": "用户刚刚否认了旧说法",
                    "confidence": 0.8,
                }
            ],
            "expression_intent": {
                "emotion": "觉得好笑但先忍住",
                "emotion_category": "happy",
                "emoji_intent": "轻轻围观",
                "action_intent": "探头看一眼",
                "send_emoji": False,
                "reason": "此刻直接发文字会抢话",
            },
        }
        meta = {
            "session_id": "aiocqhttp:GroupMessage:20001",
            "message_id": "m1",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "20001",
            "group_name": "测试群",
            "platform": "aiocqhttp",
            "user_id": "10001",
            "date": "2026-05-24",
            "is_group": "true",
        }

        await runtime._save_memory_awareness_records(payload, meta)
        await runtime._save_subjective_impression(payload, meta)
        await runtime._save_experience_payload(payload, meta)

        relationship = (await runtime.archive.get_recent_relationships(10))[0]
        visibility = (await runtime.archive.get_recent_message_visibility(10))[0]
        environment = (await runtime.archive.get_recent_group_environments(10))[0]
        decision = (await runtime.archive.get_recent_action_decisions(10))[0]
        term = (await runtime.archive.get_life_terms(10))[0]
        evidence = (await runtime.archive.get_memory_evidence(target_type="relationship", limit=10))[0]
        scene = (await runtime.archive.get_behavior_scenes(10))[0]
        slot = (await runtime.archive.get_focus_slots(10, active_only=False))[0]
        correction = (await runtime.archive.get_memory_corrections(10))[0]
        intent = (await runtime.archive.get_expression_intents(10))[0]

        combined = "\n".join(
            [
                relationship.relationship_story,
                relationship.notes[-1].content,
                visibility.reason,
                visibility.reactivation_hint,
                environment.topic,
                environment.summary,
                decision.reason,
                decision.inner_monologue,
                decision.reply_strategy,
                term.meaning,
                term.evidence,
                evidence.summary,
            ]
        )
        self.assertIn("机器人", combined)
        self.assertIn("与机器人之间", combined)
        self.assertIn("讨论机器人", combined)
        self.assertIn("把机器人叫神兽", combined)
        self.assertEqual(scene.scene, "围观称呼梗")
        self.assertEqual(slot.label, "称呼梗")
        self.assertEqual(correction.correction, "这个旧片段不应作为事实引用")
        self.assertEqual(intent.emotion_category, "happy")
        self.assertEqual(intent.action_intent, "探头看一眼")

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
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(lambda provider_id="": async_return(provider)),
                "_call_llm_text": lambda self, provider, prompt, session_id, empty_retries=0, primary_provider_id="": async_return(provider.responses[0]),
                "_cleanup_conversation": staticmethod(lambda session_id: async_return(None)),
                "_get_persona": staticmethod(lambda: async_return("一个喜欢看展的人")),
            },
        )()
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
        self.assertEqual(len(await runtime.archive.get_life_episodes(10)), 1)
        self.assertEqual(len(await runtime.archive.get_memory_evidence(target_type="action_decision", limit=10)), 1)

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
        self.assertIn("刚才的对话余温（只作氛围参考，不要补答旧消息）", prompt)
        self.assertIn("阿林: 这周末要不要去看展？", prompt)
        self.assertIn("我: 可以呀，我想看看时间。", prompt)
        self.assertIn("阿林: 我比较想下午去。", prompt)
        self.assertIn("人物称谓与性别规则", prompt)
        self.assertIn("没有明确依据就用昵称、对方或这位群友", prompt)

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
        self.assertIn("全天日程背景：", prompt)
        self.assertIn("只作为背景，不要把稍后或夜间安排提前当成此刻状态", prompt)
        self.assertLess(prompt.index("此刻活动："), prompt.index("全天日程背景："))
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
        self.assertIn("顺口落点", prompt)
        self.assertIn("顺手表达", prompt)
        self.assertNotIn("越界", prompt)
        self.assertNotIn("过热", prompt)
        self.assertNotIn("缩回被窝", prompt)
        self.assertIn("短期注意槽", prompt)
        self.assertIn("会话中期摘要", prompt)
        self.assertIn("下午场门票", prompt)
        self.assertIn("此刻表达状态", prompt)
        self.assertIn("轻轻围观", prompt)
        self.assertIn("场景词参考", prompt)
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
        self.assertEqual(effects[0].outcome, "positive")
        self.assertEqual(runtime._proactive_air_state[key]["last_effect"], "positive")
        self.assertEqual(runtime._proactive_feedback_watch, {})

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

    async def test_proactive_send_keeps_single_message_when_segmented_reply_disabled(self):
        runtime, _ = self._make_proactive_runtime([])

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "第一句。第二句！",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        target, chain = runtime.context.sent_messages[0]
        self.assertEqual(target, "aiocqhttp:FriendMessage:10001")
        self.assertEqual(chain.items, ["第一句。第二句！"])
        self._assert_last_assistant_history(runtime, "aiocqhttp:FriendMessage:10001", "第一句。第二句！")

    async def test_proactive_send_uses_framework_segmented_reply_when_enabled(self):
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
            "第一句。第二句！",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["第一句。"])
        self.assertEqual(runtime.context.sent_messages[1][1].items, ["第二句！"])
        self._assert_last_assistant_history(runtime, "aiocqhttp:FriendMessage:10001", "第一句。第二句！")

    async def test_proactive_send_keeps_single_message_when_framework_segment_config_missing(self):
        runtime, _ = self._make_proactive_runtime([])

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "第一句。第二句！",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["第一句。第二句！"])

    async def test_private_revisit_preserves_newlines_for_framework_segment_config(self):
        runtime, provider = self._make_proactive_runtime(
            [
                '{"should_reply": true, "confidence": 0.9, "decision": "reply", '
                '"reason": "街边找店很自然", '
                '"reply_text": "你人呢，\\n我看到前面好像有一家点心铺，\\n快来帮我看看是不是这家。", '
                '"memory_note": ""}'
            ],
            provider_id="proactive-model",
            context_config={
                "platform_settings": {
                    "segmented_reply": {
                        "enable": True,
                        "only_llm_result": True,
                        "interval_method": "random",
                        "interval": "0,0",
                        "words_count_threshold": 100,
                        "split_mode": "regex",
                        "regex": r".+?(?:\n|$)",
                        "content_cleanup_rule": "",
                    }
                }
            },
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

        self.assertEqual(len(runtime.context.sent_messages), 3)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["你人呢，"])
        self.assertEqual(runtime.context.sent_messages[1][1].items, ["我看到前面好像有一家点心铺，"])
        self.assertEqual(runtime.context.sent_messages[2][1].items, ["快来帮我看看是不是这家。"])
        self._assert_last_assistant_history(
            runtime,
            "aiocqhttp:FriendMessage:10001",
            "你人呢，\n我看到前面好像有一家点心铺，\n快来帮我看看是不是这家。",
        )

    async def test_proactive_send_can_attach_selected_emoji_asset(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"emoji_id": 1, "reason": "这个表情适合轻轻围观"}'],
            provider_id="proactive-model",
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=1,
                file_hash="emoji-1",
                file_path="https://example.com/peek.png",
                label="探头",
                description="适合轻轻围观",
                emotions=["好奇", "围观"],
                status="ready",
            )
        )

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "我先探头看一眼。",
            "闲时回复发送失败",
            send_payload={
                "expression_intent": {
                    "emotion": "好奇",
                    "emoji_intent": "轻轻围观",
                    "action_intent": "探头",
                    "send_emoji": True,
                    "reason": "表情比补一句解释更自然",
                }
            },
        )

        self.assertTrue(sent)
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["我先探头看一眼。"])
        self.assertEqual(
            runtime.context.sent_messages[1][1].items,
            [{"type": "image", "url": "https://example.com/peek.png"}],
        )
        assets = await runtime.archive.get_emoji_assets(10, status="ready")
        self.assertEqual(assets[0].used_count, 1)
        self.assertIn("不要靠固定关键词", provider.prompts[0])
        self.assertIn("轻轻围观", provider.prompts[0])

    async def test_proactive_send_uses_cached_local_emoji_asset(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"emoji_id": 1, "reason": "这个表情适合轻轻围观"}'],
            provider_id="proactive-model",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            cached_path = Path(tmpdir) / "emoji_assets" / "peek.png"
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            cached_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")
            await runtime.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    id=1,
                    file_hash="emoji-1",
                    file_path=str(cached_path),
                    label="探头",
                    description="适合轻轻围观",
                    emotions=["好奇", "围观"],
                    status="ready",
                )
            )

            sent = await runtime._send_proactive_message(
                "aiocqhttp:FriendMessage:10001",
                "我先探头看一眼。",
                "闲时回复发送失败",
                send_payload={
                    "expression_intent": {
                        "emotion": "好奇",
                        "emoji_intent": "轻轻围观",
                        "action_intent": "探头",
                        "send_emoji": True,
                        "reason": "表情比补一句解释更自然",
                    }
                },
            )

            self.assertTrue(sent)
            self.assertEqual(
                runtime.context.sent_messages[1][1].items,
                [{"type": "image", "file": str(cached_path)}],
            )
            self.assertIn("轻轻围观", provider.prompts[0])

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
        self.assertEqual(inserted.content["text"], "刚想起你上次说的那个展，后来有新进展吗？")
        self.assertIn("角色人设摘要", provider.prompts[0])
        self.assertIn("一个喜欢看展的人", provider.prompts[0])
        self.assertIn("隐藏上下文规则", provider.prompts[0])
        self.assertIn("隐藏推理也必须站在“我”的角色视角判断", provider.prompts[0])
        self.assertIn("服务端隐藏推理的第一句必须以“我”开头", provider.prompts[0])
        self.assertIn("不得把内心独白、旁白或“我……”句子写进最终可见输出", provider.prompts[0])
        self.assertIn("只输出 JSON 对象本体", provider.prompts[0])
        self.assertEqual(provider.prompts[0].count("隐藏推理也必须站在“我”的角色视角判断"), 1)
        self.assertNotIn("服务端隐藏推理必须像我的内心独白", provider.prompts[0])
        self.assertNotIn("不要写成审题报告、旁观说明、系统记录", provider.prompts[0])
        self.assertIn("隐藏推理第一句必须从“我”开始", provider.system_prompts[0])
        self.assertIn("禁止把隐藏推理、内心独白、解释、旁白或“我……”前置句写进最终可见输出", provider.system_prompts[0])
        self.assertNotIn("我们分析当前情况", provider.prompts[0])
        self.assertNotIn("我们分析当前情况", provider.system_prompts[0])
        self.assertIn("人物称谓与性别规则", provider.prompts[0])
        self.assertIn("人设线索：男生，喜欢看展", provider.prompts[0])
        self.assertIn("自然度复核", provider.prompts[0])
        self.assertIn("真实近况承接", provider.prompts[0])
        self.assertIn("顺手表达", provider.prompts[0])
        self.assertNotIn("越界", provider.prompts[0])
        self.assertNotIn("缩回被窝", provider.prompts[0])
        self.assertNotIn("自我审查", provider.prompts[0])
        self.assertLess(provider.prompts[0].index("JSON 输出要求"), provider.prompts[0].index("【眼前内容】"))
        self.assertGreater(provider.prompts[0].index("一个喜欢看展的人"), provider.prompts[0].index("【眼前内容】"))

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
        self.assertIn("刚才的私聊余温（最高优先级，必须优先承接；没有自然落点就观察）", prompt)
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
        self.assertIn("刚才的私聊余温（最高优先级，必须优先承接；没有自然落点就观察）", prompt)
        self.assertIn("MemOS 外部长期记忆参考", prompt)
        self.assertLess(prompt.index("刚才的私聊余温"), prompt.index("MemOS 外部长期记忆参考"))
        self.assertIn("阿林: 睡了吗？拍张照看看", prompt)
        self.assertIn("我: 照片都给你看了，看完赶紧睡。", prompt)
        self.assertIn("旧水果记忆：我切好水果喊对方出来吃", prompt)
        self.assertIn("不能单独作为当前正在发生的动作、地点、物品或剧情", prompt)
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

    async def test_chat_memory_saves_experience_layer_records(self):
        provider = Provider(
            [
                '{"worth_saving":true,'
                '"brief":"群里把看展话题接了下去",'
                '"long_summary":"Alice 提到 Bob 准备看展，后面有人说可以蹲后续。",'
                '"people":["Alice","Bob"],'
                '"visibility":{"level":"focused","attention_level":72,"priority":"normal","is_directed_at_bot":false,"reason":"同一话题被重新接起"},'
                '"action_decision":{"action":"save_memory","reason":"话题有延续价值","confidence":0.88,"scene_type":"群友档案","understanding":"understood"},'
                '"life_episode":{"title":"看展话题重新被接起","summary":"群里再次聊到 Bob 看展，适合后续回看。","kind":"group","related_people":["Bob"],"impact":"看展话题近期更容易进入注意。","confidence":0.86},'
                '"evidence_refs":[{"target_type":"focus","target_id":"看展","evidence_type":"observation","summary":"多人继续接看展话题","confidence":0.8}],'
                '"behavior_feedback":[{"scene":"群聊观察","action":"save_memory","feedback":"保存这条能帮助后续接话","result":"positive","score":1.5,"reason":"话题被多人延续"}],'
                '"expression_profiles":[{"scope":"group:20001","profile_id":"10001","label":"Alice","tone":"轻松接梗","habits":["短句确认","偶尔顺手吐槽"],"avoid":["不要长篇解释"],"confidence":0.82,"evidence":"群里这次是轻量玩笑式接话"}],'
                '"behavior_patterns":[{"scope":"group:20001","scene":"同一话题被多人续上","pattern":"先观察自然落点，必要时短句接话。","suggested_action":"observe","confidence":0.8,"support_count":1,"score":1.2,"evidence":"看展话题被多人延续"}],'
                '"session_mid_summary":{"summary":"群里在接 Bob 看展的话题，可以继续蹲后续。","topic":"看展","mood":"轻松围观","participants":["Alice","Bob"],"message_count":3},'
                '"temporary_expression_state":{"scope":"group:20001","label":"轻轻围观","tone":"短句，少解释","reason":"话题正在自然延续，我不想抢话","intensity":66,"expires_at":"2026-05-25"},'
                '"focus_updates":[{"target_type":"topic","target_id":"看展","label":"看展话题","priority":76,"reason":"近期可能形成邀约","scope":"group:20001","enabled":true}],'
                '"life_terms":[{"term":"蹲后续","meaning":"先围观同一话题后续发展","scope":"group:20001","scene":"等同一话题继续","examples":["Bob 那个看展可以蹲后续"],"familiarity":70,"confidence":0.9,"evidence":"群里原话"}],'
                '"memory_boundary_hint":{"source_scope":"group:20001","target_scope":"private:10001","policy":"ask","reason":"群聊里的第三人信息不宜直接私聊引用"},'
                '"relationship_points":[],"memory_targets":[],"preference_points":[]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="Alice",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展小群",
            message_id="m3",
        )
        event.message_str = "Bob 那个看展可以蹲后续。"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 8))

        self.assertIsNotNone(saved)
        self.assertEqual((await runtime.archive.get_life_episodes(10))[0].title, "看展话题重新被接起")
        self.assertEqual((await runtime.archive.get_memory_evidence(target_type="focus", limit=10))[0].summary, "多人继续接看展话题")
        self.assertEqual((await runtime.archive.get_behavior_feedback(10))[0].result, "positive")
        self.assertEqual((await runtime.archive.get_expression_profiles(10))[0].tone, "轻松接梗")
        self.assertEqual((await runtime.archive.get_behavior_patterns(10))[0].scene, "同一话题被多人续上")
        self.assertEqual((await runtime.archive.get_session_mid_summaries(10))[0].topic, "看展")
        self.assertEqual((await runtime.archive.get_temporary_expression_states(10, active_only=False))[0].label, "轻轻围观")
        self.assertEqual((await runtime.archive.get_focus_targets(10))[0].label, "看展话题")
        self.assertEqual((await runtime.archive.get_life_terms(10))[0].term, "蹲后续")
        self.assertEqual((await runtime.archive.get_life_terms(10))[0].familiarity, 70)
        self.assertEqual((await runtime.archive.get_memory_boundaries(10))[0].policy, "ask")
        self.assertIn("life_episode", provider.prompts[0])
        self.assertIn("memory_boundary_hint", provider.prompts[0])
        self.assertIn("expression_profiles", provider.prompts[0])
        self.assertIn("behavior_patterns", provider.prompts[0])
        self.assertIn("session_mid_summary", provider.prompts[0])
        self.assertIn("temporary_expression_state", provider.prompts[0])

    async def test_chat_memory_prompt_includes_structured_quote_context(self):
        provider = Provider(['{"worth_saving":false}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="Alice",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展小群",
            message_id="m4",
        )
        event.quote = {"message": "Bob 说下午场还有票"}
        event.message_str = "那可以蹲一下。"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 10))

        self.assertIn("引用/回复上下文", provider.prompts[0])
        self.assertIn("Bob 说下午场还有票", provider.prompts[0])

    async def test_injection_resolves_sender_once_for_capture_tasks(self):
        gate = asyncio.Event()
        provider = Provider(
            [
                '{"has_commitment":false}',
                '{"worth_saving":true,"brief":"阿林想周末看展",'
                '"long_summary":"阿林提到周末想一起去看展。",'
                '"people":[],"relationship_points":[]}',
            ]
        )

        class Resolver:
            def __init__(self):
                self.calls = 0

            async def resolve_event_sender(self, event):
                self.calls += 1
                await gate.wait()
                return "阿林"

        class Composer:
            def __init__(self, provider):
                self.provider = provider

            async def _get_provider(self, provider_id=""):
                return self.provider

            async def generate_daily(self, date=None, force=False, target_hour=None, extra=None):
                day = DayRecord(
                    date=date.strftime("%Y-%m-%d"),
                    outfit="浅蓝外套",
                    timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
                )
                await runtime.archive.save_day(day)
                return day

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"min_message_length": 1},
                "state_config": {"enabled": False},
            }
        )
        runtime.archive = DataManager()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅蓝外套",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.contact_resolver = Resolver()
        runtime.composer = Composer(provider)
        runtime.resolve_injection_target = lambda now: async_return(("2026-05-24", False))
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "周末我们去展览馆看展吧。"
        req = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session"})()

        await runtime.inject_life_context(req, event)
        await asyncio.sleep(0)

        self.assertEqual(runtime.contact_resolver.calls, 1)
        self.assertEqual(len(provider.prompts), 0)
        self.assertIn("<daily_life>", req.system_prompt)
        gate.set()
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

        self.assertEqual(len(provider.prompts), 2)
        self.assertEqual(len(await runtime.archive.get_recent_chat_summaries(5)), 1)

    async def test_injection_media_expression_does_not_create_voice_round(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(),
                "state_config": {"enabled": False},
            }
        )
        runtime.archive = DataManager()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅蓝外套",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = types.SimpleNamespace()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return(event.get_sender_name())
        )
        runtime.resolve_injection_target = lambda now: async_return(("2026-05-24", False))
        runtime.maybe_collect_emoji_assets_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_commitment_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_chat_memory_from_event = lambda event, now, sender_name="": async_return(None)
        runtime._build_injection_memos_context = lambda event, message="": async_return("")
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: async_return({})

        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "看看你现在在干嘛"
        req = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session"})()

        await runtime.inject_life_context(req, event)

        self.assertIn("[HiddenMediaExpression]", req.system_prompt)
        self.assertNotIn("life_voice_generate", req.system_prompt)
        self.assertFalse(runtime.note_voice_switch_text_result(event))
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

    async def test_background_tasks_are_limited_to_avoid_framework_pressure(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._background_scheduler = BackgroundTaskScheduler(normal_limit=2, chat_limit=1)

        active = 0
        peak = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def job():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.set()
            await release.wait()
            active -= 1

        for index in range(4):
            self.assertTrue(runtime._schedule_background_task(job(), label="普通后台任务", key=f"task:{index}"))

        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertEqual(peak, 2)

        release.set()
        await asyncio.gather(*list(runtime._background_scheduler.tasks))
        self.assertEqual(runtime._background_scheduler.tasks, set())
        self.assertEqual(runtime._background_scheduler.keys, set())

    async def test_chat_capture_background_tasks_run_one_at_a_time(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._background_scheduler = BackgroundTaskScheduler(normal_limit=4, chat_limit=1)

        active = 0
        peak = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def job():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.set()
            await release.wait()
            active -= 1

        for index in range(3):
            runtime._schedule_background_task(job(), label="聊天记忆提炼", key=f"chat:{index}")

        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertEqual(peak, 1)

        release.set()
        await asyncio.gather(*list(runtime._background_scheduler.tasks))
        self.assertEqual(runtime._background_scheduler.tasks, set())
        self.assertEqual(runtime._background_scheduler.keys, set())

    async def test_injection_reuses_short_lived_snapshot_without_hiding_current_message(self):
        provider = Provider([])

        class CountingArchive(DataManager):
            def __init__(self):
                super().__init__()
                self.relationship_calls = 0

            async def get_recent_relationships(self, limit=8):
                self.relationship_calls += 1
                return await super().get_recent_relationships(limit)

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {},
                "state_config": {"enabled": False},
            }
        )
        runtime.archive = CountingArchive()
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            note="喜欢看展",
            date_str="2026-05-24",
        )
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅蓝外套",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler(normal_limit=4, chat_limit=1)
        runtime._injection_snapshot_cache = {}
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = type("Composer", (), {})()
        runtime.resolve_injection_target = lambda now: async_return(("2026-05-24", False))
        runtime.maybe_collect_emoji_assets_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_commitment_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_chat_memory_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        event1 = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event1.message_str = "周末去看展吧"
        event2 = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event2.message_str = "下午场也可以"
        req1 = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session_1"})()
        req2 = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session_2"})()

        await runtime.inject_life_context(req1, event1)
        await runtime.inject_life_context(req2, event2)

        self.assertEqual(runtime.archive.relationship_calls, 1)
        self.assertIn("喜欢看展", req2.system_prompt)
        self.assertIn("<daily_life>", req2.system_prompt)
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

    async def test_injection_missing_day_generates_in_background_with_anti_fabrication_context(self):
        generation_started = asyncio.Event()
        allow_generation = asyncio.Event()

        class Composer:
            def __init__(self):
                self.calls = 0

            async def generate_daily(self, date=None, force=False, target_hour=None, extra=None):
                self.calls += 1
                generation_started.set()
                await allow_generation.wait()
                day = DayRecord(
                    date=date.strftime("%Y-%m-%d"),
                    outfit="浅蓝外套",
                    timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
                )
                await runtime.archive.save_day(day)
                return day

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = Composer()
        runtime.mark_page_status_changed = lambda reason: async_return(1)
        runtime.resolve_injection_target = lambda now: async_return((now.strftime("%Y-%m-%d"), False))
        req = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session"})()

        await runtime.inject_life_context(req)
        await asyncio.wait_for(generation_started.wait(), timeout=1)

        self.assertEqual(runtime.composer.calls, 1)
        self.assertIn("<daily_life>", req.system_prompt)
        self.assertIn("[HiddenScheduleUnavailable]", req.system_prompt)
        self.assertIn("禁止编造今天正在做什么", req.system_prompt)
        self.assertNotIn("[HiddenScheduleMemory]", req.system_prompt)
        self.assertEqual(runtime.archive.days, {})

        allow_generation.set()
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

        self.assertEqual(len(runtime.archive.days), 1)

    async def test_injection_no_longer_runs_rule_based_period_update(self):
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="白天居家裙",
                time_period="afternoon",
                meta={"life_mode": "late_night", "sleep_mode": "late_night"},
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.calls = []

            async def update_outfit(self, date_str, period, current_time=None):
                self.calls.append((date_str, period, current_time))
                day = await self.archive.get_day(date_str)
                day.time_period = period
                day.outfit = "LLM 自主判断后的夜间状态"
                await self.archive.save_day(day)
                return day

        composer = Composer(archive)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._get_curr_period = lambda: "night"

        data = await runtime.maybe_update_injection_outfit(
            "2026-05-24",
            await archive.get_day("2026-05-24"),
            using_extended_night=False,
        )

        self.assertEqual(composer.calls, [])
        self.assertEqual(data.time_period, "afternoon")
        self.assertEqual(data.outfit, "白天居家裙")
        self.assertEqual(data.meta["life_mode"], "late_night")

    async def test_auto_life_update_asks_llm_on_refresh_interval(self):
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date=datetime.datetime.now().strftime("%Y-%m-%d"),
                outfit="白天居家裙",
                time_period="afternoon",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料")],
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.calls = []

            async def update_outfit(self, date_str, period, current_time=None):
                self.calls.append((date_str, period, current_time))
                day = await self.archive.get_day(date_str)
                day.time_period = period
                day.outfit = "LLM 自主检查后的状态"
                await self.archive.save_day(day)
                return day

        composer = Composer(archive)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True, "refresh_minutes": 30, "quiet_hours": ""},
            }
        )
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.try_update_weather = lambda today_str: async_return(None)
        runtime.resolve_injection_target = lambda now: async_return((datetime.datetime.now().strftime("%Y-%m-%d"), False))
        runtime._get_curr_period = lambda now=None: "afternoon"

        async def refresh_state_for_day(date_str, data, now, source="", detail="", force=False, notify_page=True):
            runtime.refresh_call = (date_str, source, detail, force, notify_page)
            return data

        runtime.refresh_state_for_day = refresh_state_for_day

        await runtime.check_autonomous_life_update()

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        stored = await archive.get_day(today)
        self.assertEqual(runtime.refresh_call[1], "auto")
        self.assertFalse(runtime.refresh_call[4])
        self.assertEqual(len(composer.calls), 1)
        self.assertEqual(composer.calls[0][:2], (today, "afternoon"))
        self.assertIsInstance(composer.calls[0][2], datetime.datetime)
        self.assertEqual(stored.outfit, "LLM 自主检查后的状态")
        self.assertIn("auto_life_last_checked_at", stored.meta)

        composer.calls.clear()
        await runtime.check_autonomous_life_update()
        self.assertEqual(composer.calls, [])

    async def test_auto_life_update_skips_quiet_hours(self):
        archive = DataManager()
        today = "2026-06-24"
        await archive.save_day(
            DayRecord(
                date=today,
                outfit="白天居家裙",
                time_period="afternoon",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料")],
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.calls = []

            async def update_outfit(self, date_str, period, current_time=None):
                self.calls.append((date_str, period, current_time))
                return await self.archive.get_day(date_str)

        composer = Composer(archive)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True, "refresh_minutes": 30, "quiet_hours": "00:00-06:30"},
            }
        )
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.try_update_weather = lambda today_str: async_return(None)
        runtime.resolve_injection_target = lambda now: async_return((today, False))
        runtime._get_curr_period = lambda now=None: "afternoon"
        runtime.refresh_state_for_day = lambda *args, **kwargs: async_return(None)

        quiet_now = datetime.datetime(2026, 6, 24, 1, 20)
        with patch("core.runtime.live.life_now", return_value=quiet_now):
            await runtime.check_autonomous_life_update()

        stored = await archive.get_day(today)
        self.assertEqual(composer.calls, [])
        self.assertNotIn("auto_life_last_checked_at", stored.meta)

    async def test_chat_state_refresh_runs_during_quiet_hours(self):
        archive = DataManager()
        today = "2026-06-24"
        await archive.save_day(
            DayRecord(
                date=today,
                outfit="白天居家裙",
                time_period="afternoon",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料")],
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.calls = []

            async def update_outfit(self, date_str, period, current_time=None):
                self.calls.append((date_str, period, current_time))
                return await self.archive.get_day(date_str)

        composer = Composer(archive)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True, "refresh_minutes": 30, "quiet_hours": "00:00-06:30"},
            }
        )
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.resolve_injection_target = lambda now: async_return((today, False))
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: async_return({})
        scheduled_tasks = []

        def schedule_background_task(coro, *args, **kwargs):
            if kwargs.get("key") == f"chat_state:{today}":
                scheduled_tasks.append((coro, kwargs))
            else:
                coro.close()
            return True

        runtime._schedule_background_task = schedule_background_task
        page_reasons = []
        runtime.mark_page_status_changed = lambda reason="": page_reasons.append(reason) or async_return(len(page_reasons))
        state_refresh_kwargs = []

        async def refresh_state_for_day(*args, **kwargs):
            state_refresh_kwargs.append(kwargs)
            return await archive.get_day(today)

        runtime.refresh_state_for_day = refresh_state_for_day
        runtime.try_update_weather = lambda *args, **kwargs: async_return(None)
        runtime._get_curr_period = lambda now=None: "night"

        class Event:
            message_str = "凌晨聊两句"

        quiet_now = datetime.datetime(2026, 6, 24, 1, 20)
        with patch("core.runtime.inject.life_now", return_value=quiet_now):
            await runtime.inject_life_context(ProviderRequest(session_id="user_session"), Event())

        self.assertEqual(len(composer.calls), 0)
        chat_state_tasks = [
            coro for coro, kwargs in scheduled_tasks if kwargs.get("key") == f"chat_state:{today}"
        ]
        self.assertEqual(len(chat_state_tasks), 1)
        await chat_state_tasks[0]
        self.assertEqual(len(composer.calls), 1)
        self.assertEqual(page_reasons, ["chat_state_refresh"])
        self.assertEqual(len(state_refresh_kwargs), 1)
        self.assertFalse(state_refresh_kwargs[0].get("notify_page"))

    async def test_chat_state_refresh_stops_before_save_when_source_recalled(self):
        provider_started = asyncio.Event()
        allow_provider = asyncio.Event()
        provider = Provider(['{"energy":22,"summary":"撤回后不应保存","mood":"闷热"}'])
        archive = DataManager()
        today = "2026-06-27"
        await archive.save_day(
            DayRecord(
                date=today,
                outfit="白天居家裙",
                time_period="afternoon",
                timeline=[TimelineItem(time="13:00", activity="在家休息")],
                state=LifeState(energy=60, summary="原状态", updated_at="2026-06-27 13:00"),
            )
        )
        saved_days = []
        original_save_day = archive.save_day

        async def save_day(day):
            saved_days.append((day.outfit, day.state.summary if day.state else "", dict(day.meta)))
            await original_save_day(day)

        archive.save_day = save_day

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, **kwargs):
                provider_started.set()
                await allow_provider.wait()
                return (await provider_arg.text_chat(prompt, session_id)).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            @staticmethod
            def _compute_sleep_continuity(previous, day):
                return (0.0, 0.0, float(day.state.energy or 0))

            async def learn_preferences_from_payload(self, *args, **kwargs):
                raise AssertionError("撤回后不应保存偏好")

            async def persist_life_events_from_payload(self, *args, **kwargs):
                raise AssertionError("撤回后不应保存生活事件")

            async def update_outfit(self, date_str, period, current_time=None, should_abort=None):
                raise AssertionError("撤回后不应继续判断穿搭")

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True, "refresh_minutes": 5, "quiet_hours": ""},
            }
        )
        runtime.archive = archive
        runtime.composer = Composer()
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.mark_page_status_changed = lambda *args, **kwargs: async_return(0)
        runtime._get_curr_period = lambda now=None: "afternoon"
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="m-hot")
        event.message_str = "这种天，好热"

        task = asyncio.create_task(
            runtime._refresh_state_for_chat_background(
                today,
                datetime.datetime(2026, 6, 27, 13, 54),
                source_event=event,
            )
        )
        await asyncio.wait_for(provider_started.wait(), timeout=1)
        recall_event = Event(unified_msg_origin=event.unified_msg_origin)
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "message_id": "m-hot",
            "user_id": "10001",
        }
        runtime.note_recalled_message(recall_event)
        allow_provider.set()
        await task

        stored = await archive.get_day(today)
        self.assertEqual(stored.state.summary, "原状态")
        self.assertEqual(stored.outfit, "白天居家裙")
        self.assertEqual(saved_days, [])

    async def test_accept_invite_schedules_outfit_update_after_timeline_change(self):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date=today,
                outfit="奶茶色居家裙",
                timeline=[TimelineItem(time="12:00", activity="在家看综艺", status="放松")],
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.outfit_calls = []

            async def handle_invite(self, *args, **kwargs):
                return (
                    "想顺势出门透透气",
                    [
                        TimelineItem(time="12:00", activity="在家看综艺", status="放松"),
                        TimelineItem(time="15:00", activity="和阿林去书店闲逛", status="期待"),
                    ],
                    {"decision": "accept", "accept": True},
                )

            async def learn_preferences_from_payload(self, *args, **kwargs):
                return None

            async def persist_life_events_from_payload(self, *args, **kwargs):
                return None

            async def update_outfit(self, date_str, period, current_time=None):
                day = await self.archive.get_day(date_str)
                self.outfit_calls.append((date_str, period, current_time, day.timeline[-1].activity))
                day.outfit = "适合外出的轻便穿搭"
                day.meta["outfit_decision"] = "outdoor"
                await self.archive.save_day(day)
                return day

        scheduled = []
        page_reasons = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.memos = runtime._create_memos_service()
        runtime.archive = archive
        runtime.composer = Composer(archive)
        runtime.generation_lock = asyncio.Lock()
        runtime.contact_resolver = types.SimpleNamespace(resolve_event_sender=lambda event: async_return("阿林"))
        runtime.remember_interaction = lambda *args, **kwargs: async_return(None)
        runtime.refresh_state_for_day = lambda date_str, data, now, **kwargs: async_return(data)
        runtime._get_curr_period = lambda now=None: "afternoon"
        runtime.mark_page_status_changed = lambda reason="": page_reasons.append(reason) or async_return(1)
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

        event = Event()
        event.message_str = "下午一起出门闲逛"
        reply = await runtime.accept_user_invite(event, "下午一起出门闲逛")

        self.assertIn("已把与【阿林】的邀约加入接下来的安排", reply)
        outfit_tasks = [item for item in scheduled if item[0] == "邀约穿搭判断"]
        self.assertEqual(len(outfit_tasks), 1)
        self.assertEqual(outfit_tasks[0][:2], ("邀约穿搭判断", ""))
        stored = await archive.get_day(today)
        self.assertEqual(stored.timeline[-1].activity, "和阿林去书店闲逛")

        await outfit_tasks[0][2]

        stored = await archive.get_day(today)
        self.assertEqual(stored.outfit, "适合外出的轻便穿搭")
        self.assertEqual(len(runtime.composer.outfit_calls), 1)
        self.assertEqual(runtime.composer.outfit_calls[0][0], today)
        self.assertEqual(runtime.composer.outfit_calls[0][1], "afternoon")
        self.assertIsInstance(runtime.composer.outfit_calls[0][2], datetime.datetime)
        self.assertEqual(runtime.composer.outfit_calls[0][3], "和阿林去书店闲逛")
        self.assertEqual(page_reasons, ["invite_outfit_update"])

    async def test_apply_config_rebuilds_runtime_and_saves_config(self):
        class Config(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.saved = 0

            def save_config(self):
                self.saved += 1

        class WeatherClient:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        async def daily_task():
            return None

        async def weekly_task():
            return None

        async def auto_task():
            return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.raw_config = Config({"rhythm_config": {"schedule_time": "07:00"}})
        runtime.generation_lock = asyncio.Lock()
        runtime.config = LifeSettings.from_dict(runtime.raw_config)
        runtime.archive = DataManager()
        runtime.weather_client = WeatherClient()
        runtime.composer = object()
        runtime.rhythm = type(
            "Rhythm",
            (),
            {
                "stopped": False,
                "stop": lambda self: setattr(self, "stopped", True),
            },
        )()
        runtime.run_daily_refresh = daily_task
        runtime.run_weekly_refresh = weekly_task
        runtime.check_autonomous_life_update = auto_task

        await runtime.apply_config(
            {
                "rhythm_config": {
                    "schedule_time": "08:25",
                },
                "weather_awareness": {"default_city": "上海"},
                "state_config": {"enabled": False, "refresh_minutes": 45},
            }
        )

        self.assertEqual(runtime.raw_config.saved, 1)
        self.assertEqual(runtime.raw_config["rhythm_config"]["schedule_time"], "08:25")
        self.assertEqual(runtime.config.schedule_time, "08:25")
        self.assertEqual(runtime.config.weather.default_city, "上海")
        self.assertFalse(runtime.config.state.enabled)
        self.assertTrue(runtime.weather_client is not None)
        self.assertTrue(runtime.rhythm.scheduler.running)

    async def test_refresh_state_uses_selected_state_provider(self):
        async def cleanup(session_id):
            return None
        async def learn_preferences(payload, *, date_str, source):
            return []
        async def persist_events(payload, *, date_str, source):
            return []
        async def get_provider(provider_id=""):
            if provider_id == "selected":
                return selected_provider
            return default_provider
        async def call_llm_text(provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
            resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
            return resp.get("content", "") if isinstance(resp, dict) else getattr(resp, "completion_text", "")

        default_provider = Provider([])
        selected_provider = AltProvider(
            [
                '{"energy":28,"mood":"困倦但还算平静","busyness":75,"social":18,'
                '"sleep":{"quality":35,"summary":"睡眠不足还在影响精神"},'
                '"summary":"今天更想低负担地待着"}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(default_provider, selected_provider)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"provider": "selected", "refresh_minutes": 5},
                "rhythm_config": {"llm_provider": ""},
            }
        )
        runtime.archive = DataManager()
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_call_llm_text": staticmethod(call_llm_text),
                "_cleanup_conversation": staticmethod(cleanup),
                "_compute_sleep_continuity": staticmethod(lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))),
                "learn_preferences_from_payload": staticmethod(learn_preferences),
                "persist_life_events_from_payload": staticmethod(persist_events),
            },
        )()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                weather="北京 晴 20°C",
                timeline=[TimelineItem(time="12:00", activity="在家休息", status="慢")],
                state=LifeState(updated_at="2026-05-24 11:00"),
            )
        )

        data = await runtime.refresh_state_for_day(
            "2026-05-24",
            now=datetime.datetime(2026, 5, 24, 12, 0),
            source="chat",
            force=True,
        )

        self.assertEqual(len(default_provider.prompts), 0)
        self.assertEqual(len(selected_provider.prompts), 1)
        self.assertIn("当前状态", selected_provider.prompts[0])
        self.assertIn("【通用自主原则】", selected_provider.prompts[0])
        self.assertIn("【通用状态行为原则】", selected_provider.prompts[0])
        self.assertIn("缺少明确依据时使用空字符串", selected_provider.prompts[0])
        self.assertLess(selected_provider.prompts[0].index("【通用自主原则】"), selected_provider.prompts[0].index("【眼前内容】"))
        self.assertGreater(selected_provider.prompts[0].index("当前状态"), selected_provider.prompts[0].index("【眼前内容】"))
        self.assertEqual(data.state.energy, 28)
        self.assertEqual(data.state.source, "chat")
        self.assertEqual(data.state.updated_at, "2026-05-24 12:00")
        self.assertTrue(data.state_log)

    async def test_refresh_state_unset_provider_uses_current_default_provider(self):
        async def cleanup(session_id):
            return None
        async def learn_preferences(payload, *, date_str, source):
            return []
        async def persist_events(payload, *, date_str, source):
            return []
        async def get_provider(provider_id=""):
            if provider_id == "generation-model":
                return generation_provider
            return default_provider
        async def call_llm_text(provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
            resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
            return resp.get("content", "") if isinstance(resp, dict) else getattr(resp, "completion_text", "")

        default_provider = AltProvider(
            [
                '{"energy":36,"mood":"慢慢回神","busyness":20,"social":40,'
                '"sleep":{"quality":60,"summary":"略有困意"},'
                '"summary":"当前默认模型刷新状态"}'
            ],
            provider_id="default-model",
        )
        generation_provider = AltProvider([], provider_id="generation-model")
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(default_provider, providers={"generation-model": generation_provider})
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"provider": "", "refresh_minutes": 5},
                "rhythm_config": {"llm_provider": "generation-model"},
            }
        )
        runtime.archive = DataManager()
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_call_llm_text": staticmethod(call_llm_text),
                "_cleanup_conversation": staticmethod(cleanup),
                "_compute_sleep_continuity": staticmethod(lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))),
                "learn_preferences_from_payload": staticmethod(learn_preferences),
                "persist_life_events_from_payload": staticmethod(persist_events),
            },
        )()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                timeline=[TimelineItem(time="12:00", activity="在家休息", status="慢")],
                state=LifeState(updated_at="2026-05-24 11:00"),
            )
        )

        data = await runtime.refresh_state_for_day(
            "2026-05-24",
            now=datetime.datetime(2026, 5, 24, 12, 0),
            source="chat",
            force=True,
        )

        self.assertEqual(data.state.summary, "当前默认模型刷新状态")
        self.assertEqual(len(default_provider.prompts), 1)
        self.assertEqual(len(generation_provider.prompts), 0)

    async def test_refresh_state_keeps_daily_plan_meta_separate(self):
        provider = Provider(
            [
                (
                    '{"energy":45,"summary":"状态稳定","life_mode":"late_night",'
                    '"sleep_mode":"late_night",'
                    '"meta":{"life_mode":"sleeping","sleep_mode":"asleep"}}'
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True},
            }
        )
        runtime.archive = DataManager()
        async def mark_page_status_changed(_kind):
            return None

        runtime.mark_page_status_changed = mark_page_status_changed
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料")],
                meta={"life_mode": "resting", "sleep_mode": "normal"},
            )
        )

        async def get_provider(provider_id=""):
            return provider

        async def call_llm_text(provider_arg, prompt, session_id, **kwargs):
            return (await provider_arg.text_chat(prompt, session_id)).completion_text

        async def cleanup(session_id):
            return None

        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_call_llm_text": staticmethod(call_llm_text),
                "_cleanup_conversation": staticmethod(cleanup),
                "_compute_sleep_continuity": staticmethod(lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))),
                "learn_preferences_from_payload": staticmethod(lambda *args, **kwargs: []),
                "persist_life_events_from_payload": staticmethod(lambda *args, **kwargs: []),
            },
        )()

        data = await runtime.refresh_state_for_day(
            "2026-05-24",
            now=datetime.datetime(2026, 5, 24, 12, 0),
            force=True,
        )

        self.assertEqual(data.meta["life_mode"], "resting")
        self.assertEqual(data.meta["sleep_mode"], "normal")
        self.assertEqual(data.meta["sleep_debt"], "0")
        self.assertEqual(data.meta["energy_carryover"], "45")


