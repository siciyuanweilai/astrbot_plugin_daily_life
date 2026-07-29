import json
import unittest

from runtimehelpers import DailyLifeRuntime, Event, types

from core.runtime.reaction import (
    TOOL_REACTION_FAILED,
    TOOL_REACTION_PROCESSING,
    TOOL_REACTION_SUCCESS,
)
from core.outcome import ToolResultText


class ReactionBot:
    def __init__(self):
        self.calls = []

    async def set_msg_emoji_like(self, **kwargs):
        self.calls.append(kwargs)


class ToolReactionTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _runtime_event(message_id=42):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        bot = ReactionBot()
        event = Event(bot=bot, message_id=str(message_id))
        event.message_obj.message_id = int(message_id)
        event.message_obj.raw_message = {"message_id": int(message_id)}
        return runtime, event, bot

    async def test_sync_tool_waits_for_final_reply_delivery(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_image_generate")

        await runtime.note_tool_reaction_start(event, tool, {"prompt": "雨夜"})
        await runtime.note_tool_reaction_result(
            event,
            tool,
            {},
            json.dumps({"status": "sent", "media": "image"}),
        )

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls], [TOOL_REACTION_PROCESSING]
        )
        await runtime.note_tool_reaction_agent_done(
            event, types.SimpleNamespace(completion_text="拍好了", result_chain=None)
        )
        self.assertEqual(
            [item["emoji_id"] for item in bot.calls], [TOOL_REACTION_PROCESSING]
        )
        await runtime.note_tool_reaction_message_sent(event)

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_SUCCESS],
        )
        self.assertTrue(all(item["message_id"] == 42 for item in bot.calls))
        self.assertTrue(all(item["emoji_type"] == "1" for item in bot.calls))
        self.assertTrue(all(item["set"] is True for item in bot.calls))

    async def test_background_pending_waits_for_real_completion(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_video_generate")

        await runtime.note_tool_reaction_start(event, tool, {})
        await runtime.note_tool_reaction_result(
            event,
            tool,
            {},
            json.dumps({"status": "pending", "media": "video"}),
        )
        await runtime.note_tool_reaction_agent_done(event, None)
        self.assertEqual(
            [item["emoji_id"] for item in bot.calls], [TOOL_REACTION_PROCESSING]
        )

        await runtime.finish_tool_reaction(event, "life_video_generate", success=True)
        await runtime.finish_tool_reaction(event, "life_video_generate", success=True)

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_SUCCESS],
        )

    async def test_background_only_round_does_not_wait_for_held_agent_text(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_video_generate")

        await runtime.note_tool_reaction_start(event, tool, {})
        await runtime.note_tool_reaction_result(
            event,
            tool,
            {},
            json.dumps({"status": "pending", "media": "video"}),
        )
        await runtime.note_tool_reaction_agent_done(
            event, types.SimpleNamespace(completion_text="视频生成后再说", result_chain=None)
        )
        await runtime.finish_tool_reaction(event, "life_video_generate", success=True)

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_SUCCESS],
        )

    async def test_structured_search_failure_marks_failed(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_web_search")

        await runtime.note_tool_reaction_start(event, tool, {})
        await runtime.note_tool_reaction_result(
            event, tool, {}, json.dumps({"status": "error", "error": "超时"})
        )
        await runtime.note_tool_reaction_agent_done(
            event, types.SimpleNamespace(completion_text="没有搜到", result_chain=None)
        )
        self.assertEqual(
            [item["emoji_id"] for item in bot.calls], [TOOL_REACTION_PROCESSING]
        )
        await runtime.note_tool_reaction_message_sent(event)

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_FAILED],
        )

    async def test_repeated_searches_finish_after_last_result_and_reply(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_web_search")

        await runtime.note_tool_reaction_start(event, tool, {"query": "第一条"})
        await runtime.note_tool_reaction_result(
            event, tool, {}, json.dumps({"status": "ok", "answer": "一"})
        )
        await runtime.note_tool_reaction_start(event, tool, {"query": "第二条"})
        await runtime.note_tool_reaction_result(
            event, tool, {}, json.dumps({"status": "ok", "answer": "二"})
        )

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls], [TOOL_REACTION_PROCESSING]
        )
        await runtime.note_tool_reaction_agent_done(
            event, types.SimpleNamespace(completion_text="综合结果", result_chain=None)
        )
        self.assertEqual(
            [item["emoji_id"] for item in bot.calls], [TOOL_REACTION_PROCESSING]
        )
        await runtime.note_tool_reaction_message_sent(event)
        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_SUCCESS],
        )

    async def test_partial_search_failure_with_usable_result_finishes_successfully(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_web_search")

        await runtime.note_tool_reaction_start(event, tool, {"query": "失败查询"})
        await runtime.note_tool_reaction_result(
            event, tool, {}, json.dumps({"status": "error", "error": "超时"})
        )
        await runtime.note_tool_reaction_start(event, tool, {"query": "兜底查询"})
        await runtime.note_tool_reaction_result(
            event, tool, {}, json.dumps({"status": "ok", "answer": "可用结果"})
        )
        await runtime.note_tool_reaction_agent_done(
            event, types.SimpleNamespace(completion_text="已核实", result_chain=None)
        )
        await runtime.note_tool_reaction_message_sent(event)

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_SUCCESS],
        )

    async def test_background_and_search_wait_for_both_delivery_paths(self):
        runtime, event, bot = self._runtime_event()
        search = types.SimpleNamespace(name="life_web_search")
        video = types.SimpleNamespace(name="life_video_generate")

        await runtime.note_tool_reaction_start(event, search, {})
        await runtime.note_tool_reaction_result(
            event, search, {}, json.dumps({"status": "ok", "answer": "资料"})
        )
        await runtime.note_tool_reaction_start(event, video, {})
        await runtime.note_tool_reaction_result(
            event, video, {}, json.dumps({"status": "pending", "media": "video"})
        )
        await runtime.note_tool_reaction_agent_done(
            event, types.SimpleNamespace(completion_text="稍后发你", result_chain=None)
        )
        await runtime.note_tool_reaction_message_sent(event)
        self.assertEqual(
            [item["emoji_id"] for item in bot.calls], [TOOL_REACTION_PROCESSING]
        )

        await runtime.finish_tool_reaction(event, "life_video_generate", success=True)
        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_SUCCESS],
        )

    async def test_duplicate_final_callbacks_do_not_repeat_terminal_reaction(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_web_search")

        await runtime.note_tool_reaction_start(event, tool, {})
        await runtime.note_tool_reaction_result(
            event, tool, {}, json.dumps({"status": "ok", "answer": "结果"})
        )
        response = types.SimpleNamespace(completion_text="结果", result_chain=None)
        await runtime.note_tool_reaction_agent_done(event, response)
        await runtime.note_tool_reaction_agent_done(event, response)
        await runtime.note_tool_reaction_message_sent(event)
        await runtime.note_tool_reaction_message_sent(event)

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_SUCCESS],
        )

    async def test_duplicate_tool_result_is_ignored(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_web_search")
        result = json.dumps({"status": "ok", "answer": "结果"})

        await runtime.note_tool_reaction_start(event, tool, {})
        await runtime.note_tool_reaction_result(event, tool, {}, result)
        await runtime.note_tool_reaction_result(event, tool, {}, result)
        await runtime.note_tool_reaction_agent_done(
            event, types.SimpleNamespace(completion_text="结果", result_chain=None)
        )
        await runtime.note_tool_reaction_message_sent(event)

        state = next(iter(runtime._tool_reaction_states().values()))
        self.assertEqual(state["successes"], 1)
        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_SUCCESS],
        )

    async def test_data_tool_without_final_reply_finishes_failed(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_web_search")

        await runtime.note_tool_reaction_start(event, tool, {})
        await runtime.note_tool_reaction_result(
            event, tool, {}, json.dumps({"status": "ok", "answer": "结果"})
        )
        await runtime.note_tool_reaction_agent_done(event, None)

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_FAILED],
        )

    async def test_agent_error_overrides_successful_tool_result(self):
        runtime, event, bot = self._runtime_event()
        tool = types.SimpleNamespace(name="life_web_search")

        await runtime.note_tool_reaction_start(event, tool, {})
        await runtime.note_tool_reaction_result(
            event, tool, {}, json.dumps({"status": "ok", "answer": "结果"})
        )
        await runtime.note_tool_reaction_agent_done(
            event,
            types.SimpleNamespace(
                role="err", completion_text="最终回复生成失败", result_chain=None
            ),
        )
        await runtime.note_tool_reaction_message_sent(event)

        self.assertEqual(
            [item["emoji_id"] for item in bot.calls],
            [TOOL_REACTION_PROCESSING, TOOL_REACTION_FAILED],
        )

    async def test_contract_specific_results_do_not_use_generic_failure_matching(self):
        outcome = DailyLifeRuntime._tool_reaction_outcome

        self.assertEqual(
            outcome(
                "life_image_reverse_prompt",
                ToolResultText(
                    "反推完成，画面是雨夜生活照",
                    status="ok",
                    media="image_reverse_prompt",
                ),
            ),
            "success",
        )
        self.assertEqual(
            outcome(
                "life_video_understand",
                ToolResultText(
                    "画面已经看完了。",
                    status="ok",
                    media="video_understanding",
                ),
            ),
            "success",
        )
        self.assertEqual(
            outcome(
                "life_video_note",
                ToolResultText(
                    "总结已发送。", status="sent", media="video_note"
                ),
            ),
            "success",
        )
        self.assertEqual(
            outcome("life_web_fetch", '{"status":"ok","content":"正文"}'),
            "success",
        )
        self.assertEqual(
            outcome(
                "life_video_understand",
                ToolResultText(
                    "没有找到可理解的视频。",
                    status="failed",
                    media="video_understanding",
                ),
            ),
            "failed",
        )
        self.assertEqual(
            outcome(
                "life_photo_suite_generate",
                ToolResultText(
                    "照片组任务仍在运行。",
                    status="pending",
                    media="photo_suite",
                ),
            ),
            "pending",
        )
        wrapped = types.SimpleNamespace(
            content=[
                types.SimpleNamespace(
                    type="text", text='{"status":"ok","content":"正文"}'
                )
            ],
            isError=False,
        )
        self.assertEqual(outcome("life_web_fetch", wrapped), "success")
        wrapped.isError = True
        self.assertEqual(outcome("life_web_fetch", wrapped), "failed")

    async def test_unsupported_tool_and_platform_are_silent(self):
        runtime, event, bot = self._runtime_event()

        self.assertFalse(
            await runtime.note_tool_reaction_start(
                event, types.SimpleNamespace(name="life_query"), {}
            )
        )
        event.bot = object()
        self.assertFalse(
            await runtime.note_tool_reaction_start(
                event, types.SimpleNamespace(name="life_video_generate"), {}
            )
        )
        self.assertEqual(bot.calls, [])

    async def test_recalled_message_does_not_receive_reaction(self):
        runtime, event, bot = self._runtime_event()
        runtime._event_message_was_recalled = lambda current: True

        self.assertFalse(
            await runtime.note_tool_reaction_start(
                event, types.SimpleNamespace(name="life_video_generate"), {}
            )
        )
        self.assertFalse(
            await runtime.finish_tool_reaction(
                event, "life_video_generate", success=False
            )
        )
        self.assertEqual(bot.calls, [])


if __name__ == "__main__":
    unittest.main()
