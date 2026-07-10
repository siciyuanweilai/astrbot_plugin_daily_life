import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

from support import Event  # noqa: F401


async def record_async(calls, name, event):
    calls.append((name, event))


PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from astrbot_plugin_daily_life.main import DailyLifePlugin  # noqa: E402
from astrbot_plugin_daily_life.core.runtime.voice import VoiceSwitchMixin  # noqa: E402
from astrbot_plugin_daily_life.core.runtime.voice import preface as voice_preface_module  # noqa: E402
from astrbot_plugin_daily_life.core.runtime.voice.preface import SILENT_TOOL_PREFACE_NAMES  # noqa: E402


@contextmanager
def patched_follow_up_runners(runners):
    old_follow_up = voice_preface_module._astrbot_follow_up
    follow_up = types.SimpleNamespace(_ACTIVE_AGENT_RUNNERS=runners)
    voice_preface_module._astrbot_follow_up = follow_up
    try:
        yield follow_up
    finally:
        voice_preface_module._astrbot_follow_up = old_follow_up


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

    async def test_decorating_result_applies_chat_style_before_voice_switch(self):
        calls = []

        def apply_chat_style_before_send(event):
            calls.append(("style", event))
            return True

        async def apply_voice_switch_before_send(event):
            calls.append(("voice", event))
            return False

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            suppress_intermediate_tool_result=lambda event: False,
            hold_life_video_final_text=lambda event: False,
            apply_chat_style_before_send=apply_chat_style_before_send,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
        )
        event = Event()

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [("style", event), ("voice", event)])

    async def test_decorating_result_sends_chat_style_segments_after_voice_switch(self):
        calls = []

        def apply_chat_style_before_send(event):
            calls.append(("style", event))
            return True

        async def apply_voice_switch_before_send(event):
            calls.append(("voice", event))
            return False

        async def send_chat_style_segments_if_needed(event):
            calls.append(("send_segments", event))
            return True

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            suppress_intermediate_tool_result=lambda event: False,
            hold_life_video_final_text=lambda event: False,
            apply_chat_style_before_send=apply_chat_style_before_send,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
            send_chat_style_segments_if_needed=send_chat_style_segments_if_needed,
        )
        event = Event()

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [("style", event), ("voice", event), ("send_segments", event)])

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

    async def test_decorating_result_does_not_hold_plain_video_progress_text(self):
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
        event.set_result(event.chain_result(["视频生成要稍微等等，我已经开始跑了。"]))

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [event])
        self.assertIsNotNone(event.get_result())

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
            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

        class Runner:
            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()
        event = Event()
        event.set_result(event.chain_result(["工具调用前旁白"]))

        with patched_follow_up_runners({event.unified_msg_origin: runner}):
            suppressed = runtime.suppress_intermediate_tool_result(event)

        self.assertFalse(suppressed)
        self.assertIsNotNone(event.get_result())

    async def test_runtime_suppresses_voice_tool_preface_before_tool_runs(self):
        class Runtime(VoiceSwitchMixin):
            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

        class Runner:
            tools_call_name = ["life_voice_generate"]

            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()
        event = Event()
        event.set_result(event.chain_result(["说明：我打算用语音答应倒垃圾。"]))

        with patched_follow_up_runners({event.unified_msg_origin: runner}):
            suppressed = runtime.suppress_intermediate_tool_result(event)

        self.assertTrue(suppressed)
        self.assertIsNone(event.get_result())

    async def test_runtime_suppresses_emoji_tool_preface_before_tool_runs(self):
        class Runtime(VoiceSwitchMixin):
            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

        class Runner:
            tools_call_name = ["life_emoji_send"]

            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()
        event = Event()
        event.set_result(event.chain_result(['I will send a cute/proud emoji expressing mock annoyance.']))

        with patched_follow_up_runners({event.unified_msg_origin: runner}):
            suppressed = runtime.suppress_intermediate_tool_result(event)

        self.assertTrue(suppressed)
        self.assertIsNone(event.get_result())


    async def test_runtime_suppresses_after_voice_tool_is_used(self):
        class Runtime(VoiceSwitchMixin):
            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

        class Runner:
            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()
        event = Event()
        runtime.mark_voice_switch_available(event)
        runtime.mark_voice_switch_used(event)
        event.set_result(event.chain_result(["我懒得打字了，直接给你发条语音。"]))

        with patched_follow_up_runners({event.unified_msg_origin: runner}):
            suppressed = runtime.suppress_intermediate_tool_result(event)

        self.assertTrue(suppressed)
        self.assertIsNone(event.get_result())

    async def test_runtime_does_not_suppress_voice_word_without_tool_state(self):
        class Runtime(VoiceSwitchMixin):
            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

        class Runner:
            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()
        event = Event()
        event.set_result(event.chain_result(["我懒得打字了，直接给你发条语音。"]))

        with patched_follow_up_runners({event.unified_msg_origin: runner}):
            suppressed = runtime.suppress_intermediate_tool_result(event)

        self.assertFalse(suppressed)
        self.assertIsNotNone(event.get_result())

    async def test_runtime_keeps_image_and_video_tool_preface(self):
        class Runtime(VoiceSwitchMixin):
            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

        class Runner:
            tools_call_name = []

            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()

        with patched_follow_up_runners({}) as follow_up:
            samples = (
                ("life_image_generate", "我拍一张现在的生活照给你看。"),
                ("life_video_generate", "我生成一段街角短视频。"),
            )
            for tool_name, text in samples:
                event = Event()
                runner.tools_call_name = [tool_name]
                follow_up._ACTIVE_AGENT_RUNNERS = {event.unified_msg_origin: runner}
                event.set_result(event.chain_result([text]))
                self.assertFalse(runtime.suppress_intermediate_tool_result(event))
                self.assertIsNotNone(event.get_result())

    def test_silent_tool_preface_allowlist_only_voice_and_emoji(self):
        self.assertEqual(SILENT_TOOL_PREFACE_NAMES, {"life_voice_generate", "life_emoji_send"})

    def test_runtime_extracts_tool_names_from_response_tool_calls(self):
        response = types.SimpleNamespace(
            tool_calls=[
                types.SimpleNamespace(function=types.SimpleNamespace(name="life_voice_generate")),
                {"function": {"name": "life_image_generate"}},
            ]
        )

        self.assertEqual(
            VoiceSwitchMixin._tool_names_from_llm_response(response),
            {"life_voice_generate", "life_image_generate"},
        )

    async def test_runtime_missing_scope_method_does_not_suppress_without_tool_state(self):
        class Runtime(VoiceSwitchMixin):
            pass

        class Runner:
            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()
        event = Event()
        event.set_result(event.chain_result(["工具调用前旁白"]))

        with patched_follow_up_runners({event.unified_msg_origin: runner}):
            suppressed = runtime.suppress_intermediate_tool_result(event)

        self.assertFalse(suppressed)
        self.assertIsNotNone(event.get_result())

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

    async def test_message_hook_skips_response_gate_when_bili_summary_scheduled(self):
        calls = []

        async def apply_response_gate_for_event(event):
            calls.append(("gate", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_recalled_message=lambda event: False,
            note_structured_incoming_message=lambda event: calls.append(("structured", event)),
            mark_alias_directed_event_as_wake=lambda event: calls.append(("alias_wake", event)),
            schedule_emoji_capture_from_event=lambda event: calls.append(("emoji", event)),
            capture_chat_memory_message=lambda event: record_async(calls, "memory", event),
            schedule_visual_context_from_event=lambda event: calls.append(("visual", event)),
            schedule_video_context_from_event=lambda event: calls.append(("video", event)),
            schedule_bili_summary_from_event=lambda event: calls.append(("bili", event)) or True,
            note_proactive_activity=lambda event: calls.append(("proactive", event)),
            apply_response_gate_for_event=apply_response_gate_for_event,
        )
        event = Event()

        await plugin.on_message_for_proactive_reply(event)

        self.assertEqual(
            [name for name, _ in calls],
            ["structured", "emoji", "visual", "video", "memory", "bili"],
        )

    async def test_message_hook_persists_memory_and_schedules_emoji_capture(self):
        calls = []

        async def apply_response_gate_for_event(event):
            calls.append(("gate", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_recalled_message=lambda event: False,
            note_structured_incoming_message=lambda event: calls.append(("structured", event)),
            mark_alias_directed_event_as_wake=lambda event: calls.append(("alias_wake", event)),
            schedule_emoji_capture_from_event=lambda event: calls.append(("emoji", event)),
            capture_chat_memory_message=lambda event: record_async(calls, "memory", event),
            schedule_visual_context_from_event=lambda event: calls.append(("visual", event)),
            schedule_video_context_from_event=lambda event: calls.append(("video", event)),
            schedule_bili_summary_from_event=lambda event: calls.append(("bili", event)) or False,
            note_proactive_activity=lambda event: calls.append(("proactive", event)),
            apply_response_gate_for_event=apply_response_gate_for_event,
        )
        event = Event()

        await plugin.on_message_for_proactive_reply(event)

        self.assertEqual(
            [name for name, _ in calls],
            ["structured", "emoji", "visual", "video", "memory", "bili", "alias_wake", "proactive", "gate"],
        )

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

    async def test_life_natural_language_tools_delegate_to_command_center(self):
        calls = []

        async def query_life(event, target, *, days=7, date=""):
            calls.append(("query", event, target, days, date))
            return "查询结果"

        async def adjust_life(event, action, *, detail="", period="", schedule_time="", date=""):
            calls.append(("adjust", event, action, detail, period, schedule_time, date))
            return "调整结果"

        async def manage_commitment(event, action, *, content="", commitment_id=0, target_date=""):
            calls.append(("commitment", event, action, content, commitment_id, target_date))
            return "承诺结果"

        async def query_weather(event, city=""):
            calls.append(("weather", event, city))
            return "天气结果"

        async def review_life(event, action="show", date=""):
            calls.append(("review", event, action, date))
            return "复盘结果"

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.commands = types.SimpleNamespace(
            query_life=query_life,
            adjust_life=adjust_life,
            manage_commitment=manage_commitment,
            query_weather=query_weather,
            review_life=review_life,
        )
        event = Event()

        self.assertEqual(await plugin.tool_life_query(event, target="world", days="3", date="2026-05-24"), "查询结果")
        self.assertEqual(
            await plugin.tool_life_adjust(
                event,
                action="set_schedule_time",
                detail="",
                period="",
                schedule_time="07:30",
                date="2026-05-24",
            ),
            "调整结果",
        )
        self.assertEqual(
            await plugin.tool_life_commitment(
                event,
                action="reschedule",
                content="",
                commitment_id="4",
                target_date="2026-06-01",
            ),
            "承诺结果",
        )
        self.assertEqual(await plugin.tool_life_weather(event, city="佛山"), "天气结果")
        self.assertEqual(await plugin.tool_life_review(event, action="generate", date="2026-05-24"), "复盘结果")
        self.assertEqual(
            calls,
            [
                ("query", event, "world", 3, "2026-05-24"),
                ("adjust", event, "set_schedule_time", "", "", "07:30", "2026-05-24"),
                ("commitment", event, "reschedule", "", 4, "2026-06-01"),
                ("weather", event, "佛山"),
                ("review", event, "generate", "2026-05-24"),
            ],
        )

    async def test_media_tools_use_declared_args(self):
        calls = []

        async def life_image_generate(event, text, *, use_last_reverse_prompt=False, subject_route="free"):
            calls.append(("image", event, text, subject_route, use_last_reverse_prompt))
            return "图片已发送。"

        async def edit_life_image(event, text, reference, *, generate_without_reference=False):
            calls.append(("edit_image", event, text, reference, generate_without_reference))
            return "图片已根据参考图生成。"

        async def life_image_reverse_prompt(event, reference, source_prompt="", profile=""):
            calls.append(("reverse_image", event, reference, source_prompt, profile))
            return "图片反推提示词：雨夜生活照"

        async def life_video_generate(event, text):
            calls.append(("video", event, text))
            return "视频生成已开始"

        async def life_video_understand(event, target):
            calls.append(("understand_video", event, target))
            return "视频理解完成"

        async def life_video_note(event, target, style):
            calls.append(("note_video", event, target, style))
            return "视频长文总结"

        async def life_voice_generate(event, text, emotion="", emotion_category="", user_requested=False, decision_reason=""):
            calls.append(("voice", event, text, emotion, emotion_category, user_requested, decision_reason))
            return None

        async def life_emoji_send(event, *, intent="", emotion="", emotion_category="", decision_reason=""):
            calls.append(("emoji", event, intent, emotion, emotion_category, decision_reason))
            return "表情已发送。"

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            life_image_generate=life_image_generate,
            edit_life_image=edit_life_image,
            life_image_reverse_prompt=life_image_reverse_prompt,
            life_video_generate=life_video_generate,
            life_video_understand=life_video_understand,
            life_video_note=life_video_note,
            life_voice_generate=life_voice_generate,
            life_emoji_send=life_emoji_send,
        )
        event = Event()

        image_result = await plugin.tool_life_image_generate(
            event,
            prompt="雨夜生活照",
            subject_route="scene",
        )
        image_reverse_result = await plugin.tool_life_image_generate(
            event,
            prompt="",
            use_last_reverse_prompt=True,
        )
        image_reverse_prompt_ignored_result = await plugin.tool_life_image_generate(
            event,
            prompt="should be ignored",
            use_last_reverse_prompt=True,
        )
        edit_result = await plugin.tool_edit_life_image(
            event,
            prompt="改成咖啡店生活照",
            reference_image="https://example.com/ref.png",
        )
        reverse_result = await plugin.tool_life_image_reverse_prompt(
            event,
            reference_image="https://example.com/ref.png",
            source_prompt="保留雨夜氛围",
            profile="生活照",
        )
        video_result = await plugin.tool_life_video_generate(event, prompt="书店门口短视频")
        video_understand_result = await plugin.tool_life_video_understand(event, target="D:/tmp/life.mp4")
        video_note_result = await plugin.tool_life_video_note(
            event,
            target="D:/tmp/life.mp4",
            style="detailed",
        )
        voice_result = await plugin.tool_life_voice_generate(
            event,
            text="我困啦",
            emotion="困倦",
            emotion_category="neutral",
            user_requested=True,
            decision_reason="用户想听我直接说出来",
        )
        emoji_result = await plugin.tool_life_emoji_send(
            event,
            intent="发送一张小丑自嘲表情",
            emotion="轻松调侃",
            emotion_category="happy",
            decision_reason="用户想要这张表情",
        )

        self.assertEqual(image_result, "图片已发送。")
        self.assertEqual(image_reverse_result, "图片已发送。")
        self.assertEqual(image_reverse_prompt_ignored_result, "图片已发送。")
        self.assertEqual(edit_result, "图片已根据参考图生成。")
        self.assertEqual(reverse_result, "图片反推提示词：雨夜生活照")
        self.assertEqual(video_result, "视频生成已开始")
        self.assertEqual(video_understand_result, "视频理解完成")
        self.assertEqual(video_note_result, "视频长文总结")
        self.assertIsNone(voice_result)
        self.assertEqual(emoji_result, "表情已发送。")
        self.assertEqual(
            calls,
            [
                ("image", event, "雨夜生活照", "scene", False),
                ("image", event, "", "free", True),
                ("image", event, "", "free", True),
                ("edit_image", event, "改成咖啡店生活照", "https://example.com/ref.png", False),
                ("reverse_image", event, "https://example.com/ref.png", "保留雨夜氛围", "生活照"),
                ("video", event, "书店门口短视频"),
                ("understand_video", event, "D:/tmp/life.mp4"),
                ("note_video", event, "D:/tmp/life.mp4", "detailed"),
                ("voice", event, "我困啦", "困倦", "neutral", True, "用户想听我直接说出来"),
                ("emoji", event, "发送一张小丑自嘲表情", "轻松调侃", "happy", "用户想要这张表情"),
            ],
        )

    def test_tool_docstrings_use_stable_args_schema(self):
        invite_doc = DailyLifePlugin.tool_accept_user_invite.__doc__ or ""
        memo_doc = DailyLifePlugin.tool_add_memo_for_tomorrow.__doc__ or ""
        query_doc = DailyLifePlugin.tool_life_query.__doc__ or ""
        adjust_doc = DailyLifePlugin.tool_life_adjust.__doc__ or ""
        commitment_doc = DailyLifePlugin.tool_life_commitment.__doc__ or ""
        weather_doc = DailyLifePlugin.tool_life_weather.__doc__ or ""
        review_doc = DailyLifePlugin.tool_life_review.__doc__ or ""
        image_doc = DailyLifePlugin.tool_life_image_generate.__doc__ or ""
        edit_image_doc = DailyLifePlugin.tool_edit_life_image.__doc__ or ""
        reverse_image_doc = DailyLifePlugin.tool_life_image_reverse_prompt.__doc__ or ""
        video_doc = DailyLifePlugin.tool_life_video_generate.__doc__ or ""
        video_understand_doc = DailyLifePlugin.tool_life_video_understand.__doc__ or ""
        video_note_doc = DailyLifePlugin.tool_life_video_note.__doc__ or ""
        voice_doc = DailyLifePlugin.tool_life_voice_generate.__doc__ or ""
        emoji_doc = DailyLifePlugin.tool_life_emoji_send.__doc__ or ""

        self.assertIn("Args:", invite_doc)
        self.assertIn("invite_details(string)", invite_doc)
        self.assertIn("Args:", memo_doc)
        self.assertIn("memo_details(string)", memo_doc)
        self.assertIn("target(string)", query_doc)
        self.assertIn("action(string)", adjust_doc)
        self.assertIn("action(string)", commitment_doc)
        self.assertIn("city(string)", weather_doc)
        self.assertIn("action(string)", review_doc)
        self.assertIn("prompt(string)", image_doc)
        self.assertIn("subject_route(string)", image_doc)
        self.assertIn("current_character", image_doc)
        self.assertIn("use_last_reverse_prompt(bool)", image_doc)
        self.assertIn("上一条图片反推提示词原文", image_doc)
        self.assertIn("参考图", image_doc)
        self.assertIn("此参数不参与生成", image_doc)
        self.assertIn("reference_image(string)", edit_image_doc)
        self.assertIn("reference_image(string)", reverse_image_doc)
        self.assertIn("source_prompt(string)", reverse_image_doc)
        self.assertIn("profile(string)", reverse_image_doc)
        self.assertIn("通用超详细", reverse_image_doc)
        self.assertIn("CCD人像", reverse_image_doc)
        self.assertIn("古风特调", reverse_image_doc)
        self.assertIn("不生成图片", reverse_image_doc)
        self.assertIn("prompt(string)", video_doc)
        self.assertIn("target(string)", video_understand_doc)
        self.assertIn("target(string)", video_note_doc)
        self.assertIn("style(string)", video_note_doc)
        self.assertIn("文转图发送", video_note_doc)
        self.assertIn("text(string)", voice_doc)
        self.assertIn("emotion_category(string)", voice_doc)
        self.assertIn("user_requested(bool)", voice_doc)
        self.assertIn("decision_reason(string)", voice_doc)
        self.assertIn("intent(string)", emoji_doc)
        self.assertIn("emotion_category(string)", emoji_doc)
        self.assertIn("decision_reason(string)", emoji_doc)
        self.assertIn("作为本轮最终回复", voice_doc)
        self.assertIn("不要先输出同句文字", voice_doc)
        self.assertIn("第一人称 decision_reason", voice_doc)
        self.assertIn("不要再用文字重复同一句内容", voice_doc)
