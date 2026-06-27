import sys
import types
import unittest
from pathlib import Path

from support import Event  # noqa: F401


PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from astrbot_plugin_daily_life.main import DailyLifePlugin  # noqa: E402
from astrbot_plugin_daily_life.core.runtime.voice import VoiceSwitchMixin  # noqa: E402


class PluginToolContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_decorating_result_applies_voice_switch_before_send(self):
        calls = []

        async def apply_voice_switch_before_send(event):
            calls.append(event)
            return False

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            suppress_intermediate_tool_result=lambda event: False,
            hold_life_video_final_text=lambda event: False,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
        )
        event = Event()

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [event])

    async def test_decorating_result_suppresses_intermediate_tool_text(self):
        calls = []

        async def apply_voice_switch_before_send(event):
            calls.append(event)
            return False

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            suppress_intermediate_tool_result=lambda event: True,
            hold_life_video_final_text=lambda event: False,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
        )
        event = Event()

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [])

    async def test_decorating_result_holds_life_video_final_text(self):
        calls = []

        async def apply_voice_switch_before_send(event):
            calls.append(event)
            return False

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            suppress_intermediate_tool_result=lambda event: False,
            hold_life_video_final_text=lambda event: True,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
        )
        event = Event()

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [])

    async def test_llm_response_stops_recalled_event_before_history_save(self):
        calls = []
        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            stop_recalled_event_before_history=lambda event: calls.append(event) or True,
        )
        event = Event()

        await plugin.on_llm_response(event, types.SimpleNamespace())

        self.assertEqual(calls, [event])

    async def test_runtime_does_not_suppress_generic_active_agent_intermediate_result(self):
        class Runtime(VoiceSwitchMixin):
            pass

        class Runner:
            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()
        event = Event()
        event.set_result(event.chain_result(["工具调用前旁白"]))

        from astrbot_plugin_daily_life.core.runtime import voice as voice_module

        old_follow_up = voice_module._astrbot_follow_up
        voice_module._astrbot_follow_up = types.SimpleNamespace(
            _ACTIVE_AGENT_RUNNERS={event.unified_msg_origin: runner}
        )
        try:
            suppressed = runtime.suppress_intermediate_tool_result(event)
        finally:
            voice_module._astrbot_follow_up = old_follow_up

        self.assertFalse(suppressed)
        self.assertIsNotNone(event.get_result())

    async def test_runtime_suppresses_voice_tool_preface_only(self):
        class Runtime(VoiceSwitchMixin):
            pass

        class Runner:
            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()
        event = Event()
        event.set_result(event.chain_result(["我懒得打字了，直接给你发条语音。"]))

        from astrbot_plugin_daily_life.core.runtime import voice as voice_module

        old_follow_up = voice_module._astrbot_follow_up
        voice_module._astrbot_follow_up = types.SimpleNamespace(
            _ACTIVE_AGENT_RUNNERS={event.unified_msg_origin: runner}
        )
        try:
            suppressed = runtime.suppress_intermediate_tool_result(event)
        finally:
            voice_module._astrbot_follow_up = old_follow_up

        self.assertTrue(suppressed)
        self.assertIsNone(event.get_result())

    async def test_runtime_keeps_image_and_video_tool_preface(self):
        class Runtime(VoiceSwitchMixin):
            pass

        class Runner:
            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()

        from astrbot_plugin_daily_life.core.runtime import voice as voice_module

        old_follow_up = voice_module._astrbot_follow_up
        voice_module._astrbot_follow_up = types.SimpleNamespace(
            _ACTIVE_AGENT_RUNNERS={}
        )
        try:
            for text in ("我拍一张现在的生活照给你看。", "我生成一段街角短视频。"):
                event = Event()
                voice_module._astrbot_follow_up._ACTIVE_AGENT_RUNNERS = {event.unified_msg_origin: runner}
                event.set_result(event.chain_result([text]))
                self.assertFalse(runtime.suppress_intermediate_tool_result(event))
                self.assertIsNotNone(event.get_result())
        finally:
            voice_module._astrbot_follow_up = old_follow_up

    async def test_after_message_sent_updates_proactive_and_voice_switch_log_state(self):
        calls = []
        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_proactive_bot_reply=lambda event: calls.append(("proactive", event)),
            note_voice_switch_text_result=lambda event: calls.append(("voice_switch", event)),
        )
        event = Event()

        await plugin.after_message_sent(event)

        self.assertEqual(calls, [("proactive", event), ("voice_switch", event)])

    async def test_invite_tool_uses_invite_details(self):
        calls = []

        async def accept_user_invite(event, text):
            calls.append((event, text))
            return "已接受"

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(accept_user_invite=accept_user_invite)
        event = Event()

        result = await plugin.tool_accept_user_invite(event, invite_details="下午一起出门闲逛")

        self.assertEqual(result, "已接受")
        self.assertEqual(calls, [(event, "下午一起出门闲逛")])

    async def test_memo_tool_uses_memo_details(self):
        calls = []

        async def add_memo_for_tomorrow(event, text):
            calls.append((event, text))
            return "已记录"

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(add_memo_for_tomorrow=add_memo_for_tomorrow)
        event = Event()

        result = await plugin.tool_add_memo_for_tomorrow(event, memo_details="明天下午去书店")

        self.assertEqual(result, "已记录")
        self.assertEqual(calls, [(event, "明天下午去书店")])

    async def test_media_tools_use_declared_args(self):
        calls = []

        async def life_image_generate(event, text):
            calls.append(("image", event, text))
            return "图片已发送。"

        async def edit_life_image(event, text, reference):
            calls.append(("edit_image", event, text, reference))
            return "图片已根据参考图生成。"

        async def life_video_generate(event, text):
            calls.append(("video", event, text))
            return "视频生成已开始"

        async def life_voice_generate(event, text, emotion="", emotion_category="", user_requested=False, decision_reason=""):
            calls.append(("voice", event, text, emotion, emotion_category, user_requested, decision_reason))
            return None

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            life_image_generate=life_image_generate,
            edit_life_image=edit_life_image,
            life_video_generate=life_video_generate,
            life_voice_generate=life_voice_generate,
        )
        event = Event()

        image_result = await plugin.tool_life_image_generate(event, prompt="雨夜生活照")
        edit_result = await plugin.tool_edit_life_image(
            event,
            prompt="改成咖啡店生活照",
            reference_image="https://example.com/ref.png",
        )
        video_result = await plugin.tool_life_video_generate(event, prompt="书店门口短视频")
        voice_result = await plugin.tool_life_voice_generate(
            event,
            text="我困啦",
            emotion="困倦",
            emotion_category="neutral",
            user_requested=True,
            decision_reason="用户想听我直接说出来",
        )

        self.assertEqual(image_result, "图片已发送。")
        self.assertEqual(edit_result, "图片已根据参考图生成。")
        self.assertEqual(video_result, "视频生成已开始")
        self.assertIsNone(voice_result)
        self.assertEqual(
            calls,
            [
                ("image", event, "雨夜生活照"),
                ("edit_image", event, "改成咖啡店生活照", "https://example.com/ref.png"),
                ("video", event, "书店门口短视频"),
                ("voice", event, "我困啦", "困倦", "neutral", True, "用户想听我直接说出来"),
            ],
        )

    def test_tool_docstrings_use_stable_args_schema(self):
        invite_doc = DailyLifePlugin.tool_accept_user_invite.__doc__ or ""
        memo_doc = DailyLifePlugin.tool_add_memo_for_tomorrow.__doc__ or ""
        image_doc = DailyLifePlugin.tool_life_image_generate.__doc__ or ""
        edit_image_doc = DailyLifePlugin.tool_edit_life_image.__doc__ or ""
        video_doc = DailyLifePlugin.tool_life_video_generate.__doc__ or ""
        voice_doc = DailyLifePlugin.tool_life_voice_generate.__doc__ or ""

        self.assertIn("Args:", invite_doc)
        self.assertIn("invite_details(string)", invite_doc)
        self.assertIn("Args:", memo_doc)
        self.assertIn("memo_details(string)", memo_doc)
        self.assertIn("prompt(string)", image_doc)
        self.assertIn("reference_image(string)", edit_image_doc)
        self.assertIn("prompt(string)", video_doc)
        self.assertIn("text(string)", voice_doc)
        self.assertIn("emotion_category(string)", voice_doc)
        self.assertIn("user_requested(bool)", voice_doc)
        self.assertIn("decision_reason(string)", voice_doc)
        self.assertIn("作为本轮最终回复", voice_doc)
        self.assertIn("不要先输出同句文字", voice_doc)
        self.assertIn("第一人称 decision_reason", voice_doc)
        self.assertIn("不要再用文字重复同一句内容", voice_doc)
