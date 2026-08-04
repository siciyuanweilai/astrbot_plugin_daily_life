import asyncio
import sys
import threading
import types
import unittest
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from unittest.mock import patch

from support import DailyLifeRuntime, Event  # noqa: F401


async def record_async(calls, name, event):
    calls.append((name, event))


def set_llm_result(event, text="正常回复"):
    event.set_result(event.chain_result([types.SimpleNamespace(text=text)]))
    return event


@asynccontextmanager
async def runtime_service_lease():
    yield


PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from astrbot_plugin_daily_life.main import DailyLifePlugin  # noqa: E402
from astrbot_plugin_daily_life import main as plugin_module  # noqa: E402
from astrbot_plugin_daily_life.core.runtime.voice import VoiceSwitchMixin  # noqa: E402
from astrbot_plugin_daily_life.core.runtime.reply import (  # noqa: E402
    SemanticSegmentRuntimeMixin,
)
from astrbot_plugin_daily_life.core.runtime.voice import preface as voice_preface_module  # noqa: E402
from astrbot_plugin_daily_life.core.runtime.voice.preface import (  # noqa: E402
    SILENT_TOOL_PREFACE_NAMES,
)  # noqa: E402


@contextmanager
def patched_follow_up_runners(runners):
    old_follow_up = voice_preface_module._astrbot_follow_up
    follow_up = types.SimpleNamespace(_ACTIVE_AGENT_RUNNERS=runners)
    voice_preface_module._astrbot_follow_up = follow_up
    try:
        yield follow_up
    finally:
        voice_preface_module._astrbot_follow_up = old_follow_up


class PluginLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def test_constructor_defers_runtime_and_database_creation(self):
        context = types.SimpleNamespace()

        plugin = DailyLifePlugin(context, {"chat_style_config": {"enabled": True}})

        self.assertIsNone(plugin.runtime)
        self.assertIsNone(plugin.commands)
        self.assertIs(plugin._plugin_context, context)

    async def test_initialize_offloads_database_preparation(self):
        context = types.SimpleNamespace()
        plugin = DailyLifePlugin(context, {})
        entered = threading.Event()
        release = threading.Event()
        runtime_calls = []

        def prepare_database():
            entered.set()
            release.wait(timeout=2)
            return Path("daily_life.db")

        class Runtime:
            def __init__(self, *args, **kwargs):
                runtime_calls.append((args, kwargs))
                self.initialized = False

            async def initialize(self):
                self.initialized = True

        plugin._prepare_database = prepare_database
        plugin._register_page_web_apis = lambda: None
        plugin._validate_runtime_contract = lambda *_args: None
        with (
            patch.object(plugin_module, "DailyLifeRuntime", Runtime),
            patch.object(
                plugin_module,
                "DailyLifeCommandCenter",
                lambda runtime: types.SimpleNamespace(runtime=runtime),
            ),
        ):
            task = asyncio.create_task(plugin.initialize())
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0)
            self.assertTrue(entered.is_set())
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            await task
            await plugin.initialize()

        self.assertTrue(plugin.runtime.initialized)
        self.assertIs(plugin.commands.runtime, plugin.runtime)
        self.assertEqual(runtime_calls[0][1], {"defer_start": True})

    async def test_initialize_rolls_back_runtime_and_routes_after_failure(self):
        context = types.SimpleNamespace(registered_web_apis=[("existing",)])
        plugin = DailyLifePlugin(context, {})
        plugin._prepare_database = lambda: Path("daily_life.db")
        plugin._validate_runtime_contract = lambda *_args: None
        runtimes = []

        class Runtime:
            def __init__(self, *args, **kwargs):
                self.initialized = False
                self.terminated = False
                runtimes.append(self)

            async def initialize(self):
                self.initialized = True

            async def terminate(self):
                self.terminated = True

        def fail_register():
            context.registered_web_apis.append(("partial",))
            raise RuntimeError("接口注册失败")

        plugin._register_page_web_apis = fail_register
        with (
            patch.object(plugin_module, "DailyLifeRuntime", Runtime),
            patch.object(
                plugin_module,
                "DailyLifeCommandCenter",
                lambda runtime: types.SimpleNamespace(runtime=runtime),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "接口注册失败"):
                await plugin.initialize()

            self.assertIsNone(plugin.runtime)
            self.assertIsNone(plugin.commands)
            self.assertTrue(runtimes[0].terminated)
            self.assertEqual(context.registered_web_apis, [("existing",)])

            plugin._register_page_web_apis = lambda: None
            await plugin.initialize()

        self.assertIs(plugin.commands.runtime, plugin.runtime)
        self.assertEqual(len(runtimes), 2)

    async def test_terminate_is_safe_before_and_after_initialize(self):
        plugin = DailyLifePlugin(types.SimpleNamespace(), {})
        await plugin.terminate()

        calls = []

        class Runtime:
            async def terminate(self):
                calls.append("terminated")

        plugin.runtime = Runtime()
        await plugin.terminate()

        self.assertEqual(calls, ["terminated"])


class PluginToolContractTest(unittest.IsolatedAsyncioTestCase):
    def test_runtime_contract_reports_missing_required_methods(self):
        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace()

        with self.assertRaisesRegex(RuntimeError, "缺少必要能力"):
            plugin._validate_runtime_contract()

    async def test_message_hook_schedules_chat_memory_outside_main_pipeline(self):
        calls = []
        scheduled = []

        async def capture(event):
            calls.append(("memory", event))

        def schedule(coro, **kwargs):
            scheduled.append((coro, kwargs))
            return True

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            capture_chat_memory_message=capture,
            _schedule_background_task=schedule,
        )
        event = Event()

        await plugin._capture_chat_memory(event)

        self.assertEqual(calls, [])
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][1]["category"], "chat")
        await scheduled[0][0]
        self.assertEqual(calls, [("memory", event)])

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
        event = set_llm_result(Event())

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [event])

    async def test_direct_command_feedback_skips_expression_pipeline(self):
        calls = []

        async def apply_voice_switch_before_send(event):
            calls.append(("voice", event))

        async def apply_semantic_segment_before_send(event):
            calls.append(("semantic", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            _event_has_command_handler=DailyLifeRuntime._event_has_command_handler,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
            apply_semantic_segment_before_send=apply_semantic_segment_before_send,
        )
        event = Event()
        original = event.chain_result(
            [types.SimpleNamespace(text="正在向当前会话生成并分享新闻 (网易热搜) ...")]
        )
        event.set_result(original)
        command_filter = type("CommandFilter", (), {})()
        handler = types.SimpleNamespace(event_filters=[command_filter])
        event.set_extra("activated_handlers", [handler])

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [])
        self.assertIs(event.get_result(), original)
        self.assertEqual(
            event.get_result().chain[0].text,
            "正在向当前会话生成并分享新闻 (网易热搜) ...",
        )

    async def test_builtin_agent_error_skips_expression_pipeline(self):
        calls = []

        async def apply_semantic_segment_before_send(event):
            calls.append(("semantic", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            apply_semantic_segment_before_send=apply_semantic_segment_before_send,
        )
        event = Event()
        original = types.SimpleNamespace(
            chain=[types.SimpleNamespace(text="LLM 响应错误：上游拒绝访问")],
            result_content_type="GENERAL_RESULT",
        )
        event.set_result(original)

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [])
        self.assertIs(event.get_result(), original)
        self.assertEqual(len(event.get_result().chain), 1)

    async def test_third_party_agent_error_skips_expression_pipeline(self):
        calls = []

        async def apply_semantic_segment_before_send(event):
            calls.append(("semantic", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            apply_semantic_segment_before_send=apply_semantic_segment_before_send,
        )
        event = Event()
        original = types.SimpleNamespace(
            chain=[types.SimpleNamespace(text="Agent Runner 执行失败")],
            result_content_type="AGENT_RUNNER_ERROR",
        )
        event.set_result(original)

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [])
        self.assertIs(event.get_result(), original)
        self.assertEqual(len(event.get_result().chain), 1)

    async def test_command_llm_response_still_uses_expression_pipeline(self):
        calls = []

        async def apply_voice_switch_before_send(event):
            calls.append(("voice", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            _event_has_command_handler=DailyLifeRuntime._event_has_command_handler,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
        )
        event = Event()
        command_filter = type("CommandFilter", (), {})()
        handler = types.SimpleNamespace(event_filters=[command_filter])
        event.set_extra("activated_handlers", [handler])

        await plugin.on_llm_response(event, types.SimpleNamespace())
        set_llm_result(event)
        await plugin.on_decorating_result(event)

        self.assertTrue(getattr(event, plugin._LLM_RESPONSE_SEEN_ATTR))
        self.assertEqual(calls, [("voice", event)])

    async def test_command_feedback_keeps_safety_stop_hooks_first(self):
        calls = []

        def suppress_intermediate_tool_result(event):
            calls.append(("stop", event))
            return True

        async def apply_voice_switch_before_send(event):
            calls.append(("voice", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            _event_has_command_handler=DailyLifeRuntime._event_has_command_handler,
            suppress_intermediate_tool_result=suppress_intermediate_tool_result,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
        )
        event = Event()
        command_filter = type("CommandFilter", (), {})()
        handler = types.SimpleNamespace(event_filters=[command_filter])
        event.set_extra("activated_handlers", [handler])

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [("stop", event)])

    async def test_decorating_result_applies_voice_switch_before_chat_style(self):
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
        event = set_llm_result(Event())

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [("voice", event), ("style", event)])

    async def test_decorating_result_cleans_chat_format_before_voice_and_segmentation(
        self,
    ):
        calls = []

        def apply_chat_plain_text_cleanup_before_send(event):
            calls.append(("cleanup", event))

        async def apply_voice_switch_before_send(event):
            calls.append(("voice", event))

        async def apply_semantic_segment_before_send(event):
            calls.append(("semantic", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            apply_chat_plain_text_cleanup_before_send=(
                apply_chat_plain_text_cleanup_before_send
            ),
            apply_voice_switch_before_send=apply_voice_switch_before_send,
            apply_semantic_segment_before_send=apply_semantic_segment_before_send,
        )
        event = set_llm_result(Event())

        await plugin.on_decorating_result(event)

        self.assertEqual(
            calls,
            [("cleanup", event), ("semantic", event), ("voice", event)],
        )

    async def test_decorating_result_skips_natural_segmentation_when_master_disabled(
        self,
    ):
        calls = []

        def apply_chat_style_before_send(event):
            calls.append(("style", event))

        async def apply_voice_switch_before_send(event):
            calls.append(("voice", event))

        async def send_chat_style_segments_if_needed(event):
            calls.append(("style_send", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            _semantic_segment_enabled=lambda: False,
            suppress_intermediate_tool_result=lambda event: False,
            hold_life_video_final_text=lambda event: False,
            apply_chat_style_before_send=apply_chat_style_before_send,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
            send_chat_style_segments_if_needed=send_chat_style_segments_if_needed,
        )
        event = set_llm_result(Event())

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, [("voice", event)])

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
        event = set_llm_result(Event())

        await plugin.on_decorating_result(event)

        self.assertEqual(
            calls, [("voice", event), ("style", event), ("send_segments", event)]
        )

    async def test_unified_expression_plan_runs_before_voice_replacement(self):
        calls = []

        async def apply_voice_switch_before_send(event):
            calls.append("voice")
            event.set_result(
                event.chain_result([{"type": "record", "file": "voice.wav"}])
            )
            return True

        async def apply_semantic_segment_before_send(event):
            calls.append("semantic")
            texts = [
                SemanticSegmentRuntimeMixin._semantic_segment_text(item).strip()
                for item in event.get_result().chain
            ]
            if texts and all(texts):
                calls.append("semantic_model")
            return False

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            suppress_intermediate_tool_result=lambda event: False,
            hold_life_video_final_text=lambda event: False,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
            apply_semantic_segment_before_send=apply_semantic_segment_before_send,
        )
        event = Event()
        event.set_result(event.chain_result([types.SimpleNamespace(text="直接说出来")]))

        await plugin.on_decorating_result(event)

        self.assertEqual(calls, ["semantic", "semantic_model", "voice"])

    async def test_decorating_result_sends_structured_text_before_general_addressing(
        self,
    ):
        calls = []

        async def apply_semantic_segment_before_send(event):
            calls.append(("plan", event))

        def apply_chat_style_before_send(event):
            calls.append(("style", event))

        async def apply_voice_switch_before_send(event):
            calls.append(("voice", event))

        async def send_semantic_segments_if_needed(event):
            calls.append(("structured_send", event))

        def apply_group_addressing_before_send(event):
            calls.append(("addressing", event))

        async def send_chat_style_segments_if_needed(event):
            calls.append(("style_send", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            apply_semantic_segment_before_send=apply_semantic_segment_before_send,
            apply_chat_style_before_send=apply_chat_style_before_send,
            apply_voice_switch_before_send=apply_voice_switch_before_send,
            send_semantic_segments_if_needed=send_semantic_segments_if_needed,
            apply_group_addressing_before_send=apply_group_addressing_before_send,
            send_chat_style_segments_if_needed=send_chat_style_segments_if_needed,
        )
        event = set_llm_result(Event())

        await plugin.on_decorating_result(event)

        self.assertEqual(
            [name for name, _ in calls],
            ["plan", "voice", "style", "structured_send", "addressing", "style_send"],
        )

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

    async def test_decorating_result_holds_photo_suite_final_text(self):
        calls = []

        async def apply_voice_switch_before_send(event):
            calls.append(event)
            return False

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            suppress_intermediate_tool_result=lambda event: False,
            hold_life_video_final_text=lambda event: False,
            hold_life_photo_suite_final_text=lambda event: True,
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
            stop_recalled_event_before_history=lambda event: (
                calls.append(event) or True
            ),
        )
        event = Event()

        await plugin.on_llm_response(event, types.SimpleNamespace())

        self.assertEqual(calls, [event])
        self.assertTrue(getattr(event, plugin._LLM_RESPONSE_SEEN_ATTR))

    async def test_llm_response_records_final_tool_reply_state(self):
        calls = []
        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_tool_final_response=lambda event, response: calls.append(
                (event, response)
            ),
        )
        event = Event()
        response = types.SimpleNamespace(tool_calls=[])

        await plugin.on_llm_response(event, response)

        self.assertEqual(calls, [(event, response)])

    async def test_tool_lifecycle_hooks_delegate_to_runtime(self):
        calls = []

        async def reaction_start(event, tool, args):
            calls.append(("reaction_start", event, tool, args))

        async def reaction_end(event, tool, args, result):
            calls.append(("reaction_end", event, tool, args, result))

        async def on_start(event, tool, args):
            calls.append(("start", event, tool, args))

        async def on_end(event, tool, args, result):
            calls.append(("end", event, tool, args, result))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_tool_reaction_start=reaction_start,
            note_tool_reaction_result=reaction_end,
            handle_llm_tool_start=on_start,
            handle_llm_tool_respond=on_end,
        )
        event = Event()
        tool = types.SimpleNamespace(name="life_voice_generate")
        args = {"text": "晚安"}
        result = types.SimpleNamespace()

        await plugin.on_using_llm_tool(event, tool, args)
        await plugin.on_llm_tool_respond(event, tool, args, result)

        self.assertEqual(
            calls,
            [
                ("reaction_start", event, tool, args),
                ("start", event, tool, args),
                ("end", event, tool, args, result),
                ("reaction_end", event, tool, args, result),
            ],
        )

    async def test_agent_done_delegates_reaction_round_completion(self):
        calls = []

        async def reaction_done(event, response):
            calls.append((event, response))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_tool_reaction_agent_done=reaction_done,
        )
        event = Event()
        response = types.SimpleNamespace(completion_text="完成")

        await plugin.on_agent_done(event, object(), response)

        self.assertEqual(calls, [(event, response)])

    async def test_agent_done_marks_structured_error_response(self):
        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace()
        event = Event()

        await plugin.on_agent_done(
            event,
            object(),
            types.SimpleNamespace(role="err", completion_text="请求失败"),
        )

        self.assertTrue(getattr(event, plugin._AGENT_ERROR_SEEN_ATTR))
        self.assertFalse(getattr(event, plugin._LLM_RESPONSE_SEEN_ATTR, False))

    async def test_after_message_sent_confirms_reaction_delivery_first(self):
        calls = []

        async def reaction_sent(event):
            calls.append(("reaction", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_tool_reaction_message_sent=reaction_sent,
            note_proactive_bot_reply=lambda event: calls.append(("proactive", event)),
            note_voice_switch_text_result=lambda event: calls.append(("voice", event)),
            schedule_pending_chat_state_refresh=lambda event: calls.append(
                ("refresh", event)
            ),
        )
        event = Event()
        setattr(event, plugin._LLM_RESPONSE_SEEN_ATTR, True)

        await plugin.after_message_sent(event)

        self.assertEqual(
            calls,
            [
                ("reaction", event),
                ("proactive", event),
                ("voice", event),
                ("refresh", event),
            ],
        )

    async def test_after_message_sent_error_skips_chat_state_updates(self):
        calls = []

        async def reaction_sent(event):
            calls.append(("reaction", event))

        async def regular_effect(event):
            calls.append(("effect", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_tool_reaction_message_sent=reaction_sent,
            capture_chat_memory_bot_reply=lambda event: record_async(
                calls, "memory", event
            ),
            _schedule_background_task=lambda *args, **kwargs: calls.append(
                ("schedule", args, kwargs)
            ),
            note_regular_reply_effect=regular_effect,
            note_proactive_bot_reply=lambda event: calls.append(("proactive", event)),
            note_voice_switch_text_result=lambda event: calls.append(("voice", event)),
            schedule_pending_chat_state_refresh=lambda event: calls.append(
                ("refresh", event)
            ),
        )
        event = Event()
        event.set_result(
            types.SimpleNamespace(
                chain=[types.SimpleNamespace(text="模型请求失败")],
                result_content_type="AGENT_RUNNER_ERROR",
            )
        )

        await plugin.after_message_sent(event)

        self.assertEqual(calls, [("reaction", event)])

    async def test_status_query_is_suppressed_after_search_in_same_turn(self):
        calls = []

        async def on_start(event, tool, args):
            calls.append((event, tool, args))

        async def unexpected_query(*args, **kwargs):
            self.fail("同轮外部检索后不应重复读取完整角色状态")

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(handle_llm_tool_start=on_start)
        plugin.commands = types.SimpleNamespace(query_life=unexpected_query)
        event = Event(message_id="message-1")

        await plugin.on_using_llm_tool(
            event,
            types.SimpleNamespace(name="life_web_search"),
            {"query": "current fact"},
        )
        result = await plugin.tool_life_query(event, target="status")

        self.assertIn("已有联网搜索结果", result)
        self.assertEqual(len(calls), 1)

    async def test_runtime_does_not_suppress_generic_active_agent_intermediate_result(
        self,
    ):
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
            await runtime.handle_llm_tool_start(
                event, types.SimpleNamespace(name="life_voice_generate")
            )

        self.assertTrue(suppressed)
        self.assertIsNone(event.get_result())
        self.assertEqual(event.sent_messages, [])

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

    async def test_runtime_does_not_suppress_without_active_tool_runner(
        self,
    ):
        class Runtime(VoiceSwitchMixin):
            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

        runtime = Runtime()
        event = Event()
        event.set_result(event.chain_result(["我用语音自然接住。"]))

        with patched_follow_up_runners({}):
            suppressed = runtime.suppress_intermediate_tool_result(event)

        self.assertFalse(suppressed)
        self.assertIsNotNone(event.get_result())

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
        event.set_result(
            event.chain_result(
                ["I will send a cute/proud emoji expressing mock annoyance."]
            )
        )

        with patched_follow_up_runners({event.unified_msg_origin: runner}):
            suppressed = runtime.suppress_intermediate_tool_result(event)
            await runtime.handle_llm_tool_start(
                event, types.SimpleNamespace(name="life_emoji_send")
            )

        self.assertTrue(suppressed)
        self.assertIsNone(event.get_result())
        self.assertEqual(event.sent_messages, [])

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
        runtime.mark_tool_outcome(event, "life_voice_generate", "sent")
        event.set_result(event.chain_result(["我懒得打字了，直接给你发条语音。"]))

        with patched_follow_up_runners({event.unified_msg_origin: runner}):
            suppressed = runtime.suppress_final_silent_tool_result(event)

        self.assertTrue(suppressed)
        self.assertIsNone(event.get_result())

    async def test_runtime_keeps_final_text_after_emoji_tool_is_used(self):
        class Runtime(VoiceSwitchMixin):
            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

        runtime = Runtime()
        event = Event()
        runtime.mark_tool_outcome(event, "life_emoji_send", "sent")
        runtime.note_tool_final_response(
            event, types.SimpleNamespace(completion_text="小鞭子也没用，我要睡了。")
        )
        event.set_result(event.chain_result(["小鞭子也没用，我要睡了。"]))

        suppressed = runtime.suppress_final_silent_tool_result(event)

        self.assertFalse(suppressed)
        self.assertIsNotNone(event.get_result())
        self.assertEqual(
            event.get_result().chain,
            ["小鞭子也没用，我要睡了。"],
        )
        self.assertEqual(runtime._tool_reply_round_store(), {})

    async def test_runtime_keeps_final_text_when_voice_tool_falls_back(self):
        class Runtime(VoiceSwitchMixin):
            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

        runtime = Runtime()
        event = Event()
        runtime.mark_tool_outcome(event, "life_voice_generate", "fallback")
        runtime.note_tool_final_response(
            event, types.SimpleNamespace(completion_text="那我还是打字吧")
        )
        event.set_result(event.chain_result(["那我还是打字吧"]))

        suppressed = runtime.suppress_final_silent_tool_result(event)

        self.assertFalse(suppressed)
        self.assertIsNotNone(event.get_result())
        self.assertEqual(runtime._tool_reply_round_store(), {})

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

        self.assertTrue(suppressed)
        self.assertIsNone(event.get_result())

    async def test_runtime_routes_visible_tool_preface_through_chat_expression(self):
        class Runtime(VoiceSwitchMixin):
            def __init__(self):
                self.expression_calls = []

            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

            async def send_background_text(
                self,
                scope,
                text,
                *,
                mode,
                source_event=None,
                source="background",
                **kwargs,
            ):
                self.expression_calls.append(
                    (scope, text, source_event, source, mode, kwargs)
                )
                await source_event.send(text)
                return True

        class Runner:
            tools_call_name = []

            def done(self):
                return False

        runner = Runner()
        runtime = Runtime()

        with patched_follow_up_runners({}) as follow_up:
            samples = (
                ("accept_user_invite", "等下哦，我看看今天的安排。"),
                ("add_memo_for_tomorrow", "等下哦，我记一下。"),
                ("life_image_generate", "我拍一张现在的生活照给你看。"),
                ("life_photo_suite_generate", "我给你多拍几张，等我一下。"),
                ("life_video_generate", "我生成一段街角短视频。"),
                ("life_video_understand", "我先看看这段视频。"),
            )
            for tool_name, text in samples:
                event = Event()
                runner.tools_call_name = [tool_name]
                follow_up._ACTIVE_AGENT_RUNNERS = {event.unified_msg_origin: runner}
                event.set_result(event.chain_result([text]))
                self.assertTrue(runtime.suppress_intermediate_tool_result(event))
                self.assertIsNone(event.get_result())
                await runtime.handle_llm_tool_start(
                    event, types.SimpleNamespace(name=tool_name)
                )
                self.assertEqual(event.sent_messages, [text])
                self.assertEqual(
                    runtime.expression_calls[-1][:4],
                    (event.unified_msg_origin, text, event, "tool_preface"),
                )
                self.assertEqual(runtime.expression_calls[-1][4].value, "expressive")
                state = runtime._tool_reply_round_store()[event.unified_msg_origin]
                self.assertTrue(state["preface_sent"])
                self.assertEqual(state["preface_channel"], "聊天表达")

        self.assertEqual(len(runtime.expression_calls), 6)

    async def test_runtime_keeps_mixed_tool_preface_as_original_message_chain(self):
        class Runtime(VoiceSwitchMixin):
            def __init__(self):
                self.expression_calls = []

            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

            async def send_background_text(self, *args, **kwargs):
                self.expression_calls.append((args, kwargs))
                return True

        class Runner:
            def done(self):
                return False

        runtime = Runtime()
        event = Event()
        original_chain = [
            "先看这张图。",
            {"type": "image", "file": "D:/tmp/preface.png"},
        ]
        event.set_result(event.chain_result(original_chain))

        with patched_follow_up_runners({event.unified_msg_origin: Runner()}):
            self.assertTrue(runtime.suppress_intermediate_tool_result(event))
            await runtime.handle_llm_tool_start(
                event, types.SimpleNamespace(name="life_image_generate")
            )

        self.assertEqual(runtime.expression_calls, [])
        self.assertEqual(len(event.sent_messages), 1)
        self.assertEqual(event.sent_messages[0].chain, original_chain)
        state = runtime._tool_reply_round_store()[event.unified_msg_origin]
        self.assertTrue(state["preface_sent"])
        self.assertEqual(state["preface_channel"], "原始消息链")

    def test_silent_tool_preface_allowlist_covers_direct_expression_and_decision_tools(
        self,
    ):
        self.assertEqual(
            SILENT_TOOL_PREFACE_NAMES,
            {
                "life_voice_generate",
                "life_emoji_send",
            },
        )

    def test_runtime_extracts_tool_names_from_response_tool_calls(self):
        response = types.SimpleNamespace(
            tool_calls=[
                types.SimpleNamespace(
                    function=types.SimpleNamespace(name="life_voice_generate")
                ),
                {"function": {"name": "life_image_generate"}},
            ]
        )

        self.assertEqual(
            VoiceSwitchMixin._coerce_tool_names(response),
            {"life_voice_generate", "life_image_generate"},
        )

    async def test_runtime_missing_scope_method_does_not_suppress_without_tool_state(
        self,
    ):
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

        self.assertTrue(suppressed)
        self.assertIsNone(event.get_result())

    async def test_after_message_sent_updates_proactive_and_voice_switch_log_state(
        self,
    ):
        calls = []
        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_proactive_bot_reply=lambda event: calls.append(("proactive", event)),
            note_voice_switch_text_result=lambda event: calls.append(
                ("voice_switch", event)
            ),
            schedule_pending_chat_state_refresh=lambda event: calls.append(
                ("state_refresh", event)
            ),
        )
        event = Event()
        setattr(event, plugin._LLM_RESPONSE_SEEN_ATTR, True)

        await plugin.after_message_sent(event)

        self.assertEqual(
            calls,
            [
                ("proactive", event),
                ("voice_switch", event),
                ("state_refresh", event),
            ],
        )

    async def test_message_hook_skips_response_gate_when_bili_summary_scheduled(self):
        calls = []

        async def apply_response_gate_for_event(event):
            calls.append(("gate", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_recalled_message=lambda event: False,
            prepare_visual_media_from_event=lambda event: record_async(
                calls, "prepare", event
            ),
            note_structured_incoming_message=lambda event: calls.append(
                ("structured", event)
            ),
            mark_alias_directed_event_as_wake=lambda event: calls.append(
                ("alias_wake", event)
            ),
            schedule_emoji_capture_from_event=lambda event: calls.append(
                ("emoji", event)
            ),
            capture_chat_memory_message=lambda event: record_async(
                calls, "memory", event
            ),
            schedule_visual_context_from_event=lambda event: calls.append(
                ("visual", event)
            ),
            schedule_video_context_from_event=lambda event: calls.append(
                ("video", event)
            ),
            schedule_bili_summary_from_event=lambda event: (
                calls.append(("bili", event)) or True
            ),
            note_proactive_activity=lambda event: calls.append(("proactive", event)),
            apply_response_gate_for_event=apply_response_gate_for_event,
        )
        event = Event()

        await plugin.on_message_for_proactive_reply(event)

        self.assertEqual(
            [name for name, _ in calls],
            ["prepare", "structured", "emoji", "visual", "video", "memory", "bili"],
        )

    async def test_message_hook_persists_memory_and_schedules_emoji_capture(self):
        calls = []

        async def apply_response_gate_for_event(event):
            calls.append(("gate", event))

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            note_recalled_message=lambda event: False,
            prepare_visual_media_from_event=lambda event: record_async(
                calls, "prepare", event
            ),
            note_structured_incoming_message=lambda event: calls.append(
                ("structured", event)
            ),
            mark_alias_directed_event_as_wake=lambda event: calls.append(
                ("alias_wake", event)
            ),
            schedule_emoji_capture_from_event=lambda event: calls.append(
                ("emoji", event)
            ),
            capture_chat_memory_message=lambda event: record_async(
                calls, "memory", event
            ),
            schedule_visual_context_from_event=lambda event: calls.append(
                ("visual", event)
            ),
            schedule_video_context_from_event=lambda event: calls.append(
                ("video", event)
            ),
            schedule_bili_summary_from_event=lambda event: (
                calls.append(("bili", event)) or False
            ),
            note_proactive_activity=lambda event: calls.append(("proactive", event)),
            apply_response_gate_for_event=apply_response_gate_for_event,
        )
        event = Event()

        await plugin.on_message_for_proactive_reply(event)

        self.assertEqual(
            [name for name, _ in calls],
            [
                "prepare",
                "structured",
                "emoji",
                "visual",
                "video",
                "memory",
                "bili",
                "alias_wake",
                "proactive",
                "gate",
            ],
        )

    async def test_invite_tool_uses_invite_details(self):
        calls = []

        async def accept_user_invite(event, text):
            calls.append((event, text))
            return "已接受"

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(accept_user_invite=accept_user_invite)
        event = Event()

        result = await plugin.tool_accept_user_invite(
            event, invite_details="下午一起出门闲逛"
        )

        self.assertEqual(result, "已接受")
        self.assertEqual(calls, [(event, "下午一起出门闲逛")])

    async def test_memo_tool_uses_memo_details(self):
        calls = []

        async def add_memo_for_tomorrow(event, text):
            calls.append((event, text))
            return "已记录"

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            add_memo_for_tomorrow=add_memo_for_tomorrow
        )
        event = Event()

        result = await plugin.tool_add_memo_for_tomorrow(
            event, memo_details="明天下午去书店"
        )

        self.assertEqual(result, "已记录")
        self.assertEqual(calls, [(event, "明天下午去书店")])

    async def test_life_natural_language_tools_delegate_to_command_center(self):
        calls = []

        async def query_life(event, target, *, days=7, date=""):
            calls.append(("query", event, target, days, date))
            return "查询结果"

        async def adjust_life(
            event, action, *, detail="", period="", schedule_time="", date=""
        ):
            calls.append(("adjust", event, action, detail, period, schedule_time, date))
            return "调整结果"

        async def manage_commitment(
            event, action, *, content="", commitment_id=0, target_date=""
        ):
            calls.append(
                ("commitment", event, action, content, commitment_id, target_date)
            )
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

        self.assertEqual(
            await plugin.tool_life_query(
                event, target="world", days="3", date="2026-05-24"
            ),
            "查询结果",
        )
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
        self.assertEqual(
            await plugin.tool_life_weather(event, city="测试市"), "天气结果"
        )
        self.assertEqual(
            await plugin.tool_life_review(event, action="generate", date="2026-05-24"),
            "复盘结果",
        )
        self.assertEqual(
            calls,
            [
                ("query", event, "world", 3, "2026-05-24"),
                ("adjust", event, "set_schedule_time", "", "", "07:30", "2026-05-24"),
                ("commitment", event, "reschedule", "", 4, "2026-06-01"),
                ("weather", event, "测试市"),
                ("review", event, "generate", "2026-05-24"),
            ],
        )

    async def test_map_natural_language_tools_delegate_structured_arguments(self):
        calls = []

        async def place_search(query, **kwargs):
            calls.append(("search", query, kwargs))
            return {"ok": True, "kind": "search"}

        async def route_plan(origin, destination, **kwargs):
            calls.append(("route", origin, destination, kwargs))
            return {"ok": True, "kind": "route"}

        async def place_detail(poi_id):
            calls.append(("detail", poi_id))
            return {"ok": True, "kind": "detail"}

        async def outing_plan(request, stops, **kwargs):
            calls.append(("outing", request, stops, kwargs))
            return {"ok": True, "kind": "outing"}

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            domains=types.SimpleNamespace(
                tool_place_search=place_search,
                tool_route_plan=route_plan,
                tool_place_detail=place_detail,
                tool_outing_plan=outing_plan,
            )
        )
        event = Event()

        search = await plugin.tool_life_place_search(
            event,
            "安静咖啡店",
            near="测试中心",
            category="咖啡厅",
            radius_meters="2000",
            limit="4",
        )
        route = await plugin.tool_life_route_plan(
            event, "测试起点", "测试终点", mode="compare"
        )
        detail = await plugin.tool_life_place_detail(event, "poi-1")
        outing = await plugin.tool_life_outing_plan(
            event,
            "逛书店再吃糖水",
            ["独立书店", "广式糖水"],
            start="测试起点",
            mode="walking",
            duration_minutes="180",
            max_stops="2",
        )

        self.assertEqual(search["kind"], "search")
        self.assertEqual(route["kind"], "route")
        self.assertEqual(detail["kind"], "detail")
        self.assertEqual(outing["kind"], "outing")
        self.assertEqual(
            calls,
            [
                (
                    "search",
                    "安静咖啡店",
                    {
                        "near": "测试中心",
                        "category": "咖啡厅",
                        "radius_meters": 2000,
                        "limit": 4,
                    },
                ),
                ("route", "测试起点", "测试终点", {"mode": "compare"}),
                ("detail", "poi-1"),
                (
                    "outing",
                    "逛书店再吃糖水",
                    ["独立书店", "广式糖水"],
                    {
                        "start": "测试起点",
                        "mode": "walking",
                        "duration_minutes": 180,
                        "max_stops": 2,
                    },
                ),
            ],
        )

    async def test_map_tools_are_hidden_when_map_service_is_unavailable(self):
        class Toolset:
            def __init__(self):
                self.names = [
                    "life_place_search",
                    "life_route_plan",
                    "life_place_detail",
                    "life_outing_plan",
                    "life_weather",
                ]

            def remove_tool(self, name):
                self.names = [item for item in self.names if item != name]

        async def inject_life_context(_req, _event):
            return None

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            search=types.SimpleNamespace(prepare_tools=lambda *_args, **_kwargs: None),
            config=types.SimpleNamespace(
                image_generation=types.SimpleNamespace(enabled=True),
                video_generation=types.SimpleNamespace(enabled=True),
                voice_generation=types.SimpleNamespace(enabled=True),
            ),
            domains=types.SimpleNamespace(map_tools_available=lambda: False),
            inject_life_context=inject_life_context,
        )
        request = types.SimpleNamespace(func_tool=Toolset())

        await plugin.on_llm_request(Event(), request)

        self.assertEqual(request.func_tool.names, ["life_weather"])

    async def test_web_search_tool_forwards_structured_source_and_dates(self):
        calls = []

        async def tool_search(query, depth, platform, **kwargs):
            calls.append((query, depth, platform, kwargs))
            return "搜索结果"

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            search=types.SimpleNamespace(tool_search=tool_search),
            runtime_service_lease=runtime_service_lease,
        )
        event = Event(message_id="message-1")

        result = await plugin.tool_life_web_search(
            event,
            query="最近的发布信息",
            depth="deep",
            platform="官方站点",
            source_scope="both",
            time_range="week",
            start_date="2026-07-01",
            end_date="2026-07-14",
            image_search=True,
            image_understanding=True,
        )

        self.assertEqual(result, "搜索结果")
        self.assertEqual(calls[0][:3], ("最近的发布信息", "deep", "官方站点"))
        self.assertEqual(
            calls[0][3],
            {
                "source_scope": "both",
                "time_range": "week",
                "start_date": "2026-07-01",
                "end_date": "2026-07-14",
                "image_search": True,
                "image_understanding": True,
                "topic": "general",
                "include_raw_content": False,
                "include_images": False,
                "include_image_descriptions": False,
                "include_domains": [],
                "exclude_domains": [],
                "country": "",
                "auto_parameters": False,
                "exact_match": False,
                "umo": event.unified_msg_origin,
                "turn_id": f"{event.unified_msg_origin}:message-1",
            },
        )

    async def test_media_tools_use_declared_args(self):
        calls = []

        async def life_image_generate(
            event,
            text,
            *,
            use_last_reverse_prompt=False,
            subject_route="free",
            current_outfit_change=False,
            current_outfit_instruction="",
            resolution="",
        ):
            calls.append(
                (
                    "image",
                    event,
                    text,
                    subject_route,
                    current_outfit_change,
                    current_outfit_instruction,
                    use_last_reverse_prompt,
                    resolution,
                )
            )
            return "图片已发送。"

        async def life_photo_suite_generate(
            event,
            text,
            *,
            count=4,
            reference_image="",
            continue_last_result=False,
            subject_route="free",
            participants=None,
            retry_indexes=None,
            resolution="",
        ):
            calls.append(
                (
                    "photo_suite",
                    event,
                    text,
                    count,
                    reference_image,
                    continue_last_result,
                    subject_route,
                    participants,
                    retry_indexes,
                    resolution,
                )
            )
            return "套图生成已开始"

        async def edit_life_image(
            event,
            text,
            reference,
            *,
            continue_last_result=False,
            generate_without_reference=False,
            resolution="",
        ):
            calls.append(
                (
                    "edit_image",
                    event,
                    text,
                    reference,
                    continue_last_result,
                    generate_without_reference,
                    resolution,
                )
            )
            return "图片已根据参考图生成。"

        async def life_image_reverse_prompt(
            event, reference, source_prompt="", profile=""
        ):
            calls.append(("reverse_image", event, reference, source_prompt, profile))
            return "图片反推提示词：雨夜生活照"

        async def life_video_generate(
            event,
            text,
            *,
            subject_route="free",
            participants=None,
            continue_last_result=False,
        ):
            calls.append(
                (
                    "video",
                    event,
                    text,
                    subject_route,
                    participants,
                    continue_last_result,
                )
            )
            return "视频生成已开始"

        async def life_video_understand(event, target):
            calls.append(("understand_video", event, target))
            return "视频理解完成"

        async def life_video_note(event, target, style):
            calls.append(("note_video", event, target, style))
            return "视频长文总结"

        async def life_voice_generate(
            event,
            text,
            emotion="",
            emotion_category="",
            user_requested=False,
            decision_reason="",
        ):
            calls.append(
                (
                    "voice",
                    event,
                    text,
                    emotion,
                    emotion_category,
                    user_requested,
                    decision_reason,
                )
            )
            return None

        async def life_emoji_send(
            event, *, intent="", emotion="", emotion_category="", decision_reason=""
        ):
            calls.append(
                ("emoji", event, intent, emotion, emotion_category, decision_reason)
            )
            return "表情已发送。"

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(
            life_image_generate=life_image_generate,
            life_photo_suite_generate=life_photo_suite_generate,
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
            resolution="4k",
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
        outfit_image_result = await plugin.tool_life_image_generate(
            event,
            prompt="站在门口准备出门",
            subject_route="current_character",
            current_outfit_change=True,
            current_outfit_instruction="换甜妹穿搭",
        )
        suite_result = await plugin.tool_life_photo_suite_generate(
            event,
            prompt="雨后公园散步",
            count=6,
            reference_image="https://example.com/scene.png",
            continue_last_result=True,
            subject_route="group",
            participants=["friend-1"],
            retry_indexes=[2, 4],
            resolution="2K",
        )
        edit_result = await plugin.tool_edit_life_image(
            event,
            prompt="改成咖啡店生活照",
            reference_image="https://example.com/ref.png",
            continue_last_result=True,
            resolution="1k",
        )
        reverse_result = await plugin.tool_life_image_reverse_prompt(
            event,
            reference_image="https://example.com/ref.png",
            source_prompt="保留雨夜氛围",
            profile="生活照",
        )
        video_result = await plugin.tool_life_video_generate(
            event,
            prompt="书店门口双人短视频",
            subject_route="group",
            participants=["friend-1"],
            continue_last_result=True,
        )
        video_understand_result = await plugin.tool_life_video_understand(
            event, target="D:/tmp/life.mp4"
        )
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
        self.assertEqual(outfit_image_result, "图片已发送。")
        self.assertEqual(suite_result, "套图生成已开始")
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
                ("image", event, "雨夜生活照", "scene", False, "", False, "4K"),
                ("image", event, "", "free", False, "", True, ""),
                ("image", event, "", "free", False, "", True, ""),
                (
                    "image",
                    event,
                    "站在门口准备出门",
                    "current_character",
                    True,
                    "换甜妹穿搭",
                    False,
                    "",
                ),
                (
                    "photo_suite",
                    event,
                    "雨后公园散步",
                    6,
                    "https://example.com/scene.png",
                    True,
                    "group",
                    ["friend-1"],
                    [2, 4],
                    "2K",
                ),
                (
                    "edit_image",
                    event,
                    "改成咖啡店生活照",
                    "https://example.com/ref.png",
                    True,
                    False,
                    "1K",
                ),
                (
                    "reverse_image",
                    event,
                    "https://example.com/ref.png",
                    "保留雨夜氛围",
                    "生活照",
                ),
                (
                    "video",
                    event,
                    "书店门口双人短视频",
                    "group",
                    ["friend-1"],
                    True,
                ),
                ("understand_video", event, "D:/tmp/life.mp4"),
                ("note_video", event, "D:/tmp/life.mp4", "detailed"),
                (
                    "voice",
                    event,
                    "我困啦",
                    "困倦",
                    "neutral",
                    True,
                    "用户想听我直接说出来",
                ),
                (
                    "emoji",
                    event,
                    "发送一张小丑自嘲表情",
                    "轻松调侃",
                    "happy",
                    "用户想要这张表情",
                ),
            ],
        )

    def test_tool_docstrings_use_stable_args_schema(self):
        invite_doc = DailyLifePlugin.tool_accept_user_invite.__doc__ or ""
        memo_doc = DailyLifePlugin.tool_add_memo_for_tomorrow.__doc__ or ""
        query_doc = DailyLifePlugin.tool_life_query.__doc__ or ""
        adjust_doc = DailyLifePlugin.tool_life_adjust.__doc__ or ""
        commitment_doc = DailyLifePlugin.tool_life_commitment.__doc__ or ""
        weather_doc = DailyLifePlugin.tool_life_weather.__doc__ or ""
        place_search_doc = DailyLifePlugin.tool_life_place_search.__doc__ or ""
        route_plan_doc = DailyLifePlugin.tool_life_route_plan.__doc__ or ""
        place_detail_doc = DailyLifePlugin.tool_life_place_detail.__doc__ or ""
        outing_plan_doc = DailyLifePlugin.tool_life_outing_plan.__doc__ or ""
        review_doc = DailyLifePlugin.tool_life_review.__doc__ or ""
        image_doc = DailyLifePlugin.tool_life_image_generate.__doc__ or ""
        suite_doc = DailyLifePlugin.tool_life_photo_suite_generate.__doc__ or ""
        edit_image_doc = DailyLifePlugin.tool_edit_life_image.__doc__ or ""
        reverse_image_doc = DailyLifePlugin.tool_life_image_reverse_prompt.__doc__ or ""
        video_doc = DailyLifePlugin.tool_life_video_generate.__doc__ or ""
        video_understand_doc = DailyLifePlugin.tool_life_video_understand.__doc__ or ""
        video_note_doc = DailyLifePlugin.tool_life_video_note.__doc__ or ""
        text_forward_doc = DailyLifePlugin.tool_life_text_forward.__doc__ or ""
        voice_doc = DailyLifePlugin.tool_life_voice_generate.__doc__ or ""
        emoji_doc = DailyLifePlugin.tool_life_emoji_send.__doc__ or ""

        self.assertIn("Args:", invite_doc)
        self.assertIn("invite_details(string)", invite_doc)
        self.assertNotIn("更新今天的后续时间轴", invite_doc)
        self.assertIn("简短、自然的等待语", invite_doc)
        self.assertIn("不能提前答应或拒绝", invite_doc)
        self.assertIn("Args:", memo_doc)
        self.assertIn("memo_details(string)", memo_doc)
        self.assertIn("简短、自然的等待语", memo_doc)
        self.assertIn("不能声称已经记录成功", memo_doc)
        self.assertIn("target(string)", query_doc)
        self.assertIn("action(string)", adjust_doc)
        self.assertIn("action(string)", commitment_doc)
        self.assertIn("city(string)", weather_doc)
        self.assertIn("query(string)", place_search_doc)
        self.assertIn("origin(string)", route_plan_doc)
        self.assertIn("poi_id(string)", place_detail_doc)
        self.assertIn("stops(list[string])", outing_plan_doc)
        self.assertIn("action(string)", review_doc)
        self.assertIn("prompt(string)", image_doc)
        self.assertIn("简短、自然的行动确认", image_doc)
        self.assertIn("prompt(string)", suite_doc)
        self.assertIn("count(int)", suite_doc)
        self.assertIn("retry_indexes(list[int])", suite_doc)
        self.assertIn("默认 3", suite_doc)
        self.assertIn("2 到 6 张", suite_doc)
        self.assertIn("不要为了生成套图而自行连续调用多次单图工具", suite_doc)
        self.assertIn("不能提前声称图片已经完成", image_doc)
        self.assertIn("subject_route(string)", image_doc)
        self.assertIn("current_character", image_doc)
        self.assertIn("当前角色作为人物 A、好友作为人物 B", image_doc)
        self.assertIn("未明确归属的单套穿搭默认只属于人物 A", image_doc)
        self.assertIn("不要根据姓名或昵称猜测性别", image_doc)
        self.assertIn("friend_scene_category(string)", image_doc)
        self.assertIn("current_outfit_change(bool)", image_doc)
        self.assertIn("current_outfit_instruction(string)", image_doc)
        self.assertIn("仅看效果图时必须为 false", image_doc)
        self.assertNotIn("friend_style_pool(string)", image_doc)
        self.assertNotIn("friend_outfit_decision(string)", image_doc)
        self.assertIn("服装属性和造型决定由插件根据场景推导", image_doc)
        self.assertIn("外出服回家后可以继续穿", image_doc)
        self.assertIn("friend_scene_category(string)", suite_doc)
        self.assertNotIn("friend_style_pool(string)", suite_doc)
        self.assertIn("use_last_reverse_prompt(bool)", image_doc)
        self.assertIn("resolution(string)", image_doc)
        self.assertIn("resolution(string)", suite_doc)
        self.assertIn("resolution(string)", edit_image_doc)
        self.assertIn("只能填 1K、2K 或 4K", image_doc)
        self.assertIn("上一条图片反推提示词原文", image_doc)
        self.assertIn("参考图", image_doc)
        self.assertIn("此参数不参与生成", image_doc)
        self.assertIn("reference_image(string)", edit_image_doc)
        self.assertIn("continue_last_result(bool)", edit_image_doc)
        self.assertIn("当前会话上一张成功生成的图片", edit_image_doc)
        self.assertIn("不能用它重新生成", edit_image_doc)
        self.assertIn("不能提前声称已经改好", edit_image_doc)
        self.assertIn("合影改图仍须把当前角色作为人物 A", edit_image_doc)
        self.assertIn("reference_image(string)", reverse_image_doc)
        self.assertIn("source_prompt(string)", reverse_image_doc)
        self.assertIn("profile(string)", reverse_image_doc)
        self.assertIn("通用超详细", reverse_image_doc)
        self.assertIn("CCD人像", reverse_image_doc)
        self.assertIn("古风特调", reverse_image_doc)
        self.assertIn("视觉封面", reverse_image_doc)
        self.assertIn("设计视觉", reverse_image_doc)
        self.assertIn("不生成图片", reverse_image_doc)
        self.assertIn("prompt(string)", video_doc)
        self.assertIn("subject_route(string)", video_doc)
        self.assertIn("participants(list[string])", video_doc)
        self.assertIn("friend_scene_category(string)", video_doc)
        self.assertNotIn("friend_style_pool(string)", video_doc)
        self.assertIn("continue_last_result(bool)", video_doc)
        self.assertIn("没有现成合影时", video_doc)
        self.assertIn("普通“一起拍个视频”", video_doc)
        self.assertIn("不要设置 continue_last_result", video_doc)
        self.assertIn("只有用户明确说", video_doc)
        self.assertIn("不能仅因上一轮刚发送过图片就设为 true", video_doc)
        self.assertIn("视频必须沿用首帧中两人的独立属性", video_doc)
        self.assertIn("简短、自然的行动确认", video_doc)
        self.assertIn("真实视频发送后再自然补一句", video_doc)
        self.assertIn("target(string)", video_understand_doc)
        self.assertIn(
            "调用前可以根据聊天语境先用角色口吻自然回应一句", video_understand_doc
        )
        self.assertNotIn("不要先输出", video_understand_doc)
        self.assertIn("target(string)", video_note_doc)
        self.assertIn("style(string)", video_note_doc)
        self.assertIn("文转图发送", video_note_doc)
        self.assertIn("index(int)", text_forward_doc)
        self.assertIn("合并转发", text_forward_doc)
        self.assertIn("不要在最终回复里复述原文", text_forward_doc)
        self.assertIn("text(string)", voice_doc)
        self.assertIn("emotion_category(string)", voice_doc)
        self.assertIn("user_requested(bool)", voice_doc)
        self.assertIn("decision_reason(string)", voice_doc)
        self.assertIn("intent(string)", emoji_doc)
        self.assertIn("emotion_category(string)", emoji_doc)
        self.assertIn("decision_reason(string)", emoji_doc)
        self.assertIn("作为本轮最终回复", voice_doc)
        self.assertIn("仅当用户明确要求", voice_doc)
        self.assertIn("user_requested=true", voice_doc)
        self.assertIn("普通聊天不要调用本工具", voice_doc)
        self.assertNotIn("模型自主判断语音更自然", voice_doc)
        self.assertIn("不要先输出同句文字", voice_doc)
        self.assertNotIn("第一人称 decision_reason", voice_doc)
        self.assertIn("不要再用文字重复同一句内容", voice_doc)
