import datetime
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import support
from support import DayRecord, LifeSettings, Event, DailyLifeCommandCenter
from core.runtime.actionreply import (
    ActionReplyService, RECEIPTS_ATTR, EDIT_ATTR, TOOLS_ATTR,
    note_action_reply_tool, sync_action_reply_history,
)


class ActionReplyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.day = DayRecord(date="2026-10-31", outfit="淡黄上衣配白裙",
            meta={"outfit_fact_confirmed_at": "2026-10-31 12:00:00"})
        self.runtime = SimpleNamespace(config=LifeSettings.from_dict({}),
            _runtime_now=lambda: datetime.datetime(2026, 10, 31, 14),
            resolve_injection_target=AsyncMock(return_value=(self.day.date, False)),
            archive=SimpleNamespace(get_day=AsyncMock(return_value=self.day),
                                    get_continuous_life=AsyncMock(return_value={})),
            get_text_provider=AsyncMock(return_value=object()),
            call_text_model=AsyncMock(), close_text_session=AsyncMock(),
            event_was_recalled=lambda event: False,
            continuous_turn_event_is_current=lambda event: True)
        self.event = SimpleNamespace(message_str="你愿意换一套出门吗？",
            _daily_life_expression_context={"conversation": "对方希望一起出门"})
        self.response = SimpleNamespace(completion_text="好呀，我这就换上那套淡紫长裙。")
        self.execute = AsyncMock()
        self.service = ActionReplyService(self.runtime)

    def replies(self, *plans):
        self.runtime.call_text_model.side_effect = [json.dumps(p, ensure_ascii=False) for p in plans]

    async def test_accepted_action_runs_before_rephrasing_with_receipt(self):
        async def execute(event, **kwargs):
            self.day.outfit = "淡紫长裙配米白开衫"
            setattr(event, RECEIPTS_ATTR, [{"action": kwargs["action"], "status": "completed"}])
        self.execute.side_effect = execute
        self.replies({"decision": "act", "action": "update_outfit", "detail": "换那套淡紫长裙准备出门"},
                     {"decision": "revise", "text": "换好了，开衫也披上了。走吧～"})
        await self.service.reconcile(self.event, self.response, self.execute)
        self.execute.assert_awaited_once()
        last = self.runtime.call_text_model.call_args.args[1]
        self.assertIn("淡紫长裙配米白开衫", last)
        self.assertIn('"phase": "after"', last)
        self.assertEqual(self.response.completion_text, "换好了，开衫也披上了。走吧～")
        await self.service.reconcile(self.event, self.response, self.execute)
        self.execute.assert_awaited_once()

    async def test_false_past_claim_is_corrected_without_inventing_execution(self):
        self.response.completion_text = "真换好了，浅蓝方领短袖配白裙。"
        self.replies({"decision": "revise", "text": "还没换成浅蓝那件，身上还是淡黄上衣配白裙。"})
        await self.service.reconcile(self.event, self.response, self.execute)
        self.execute.assert_not_awaited()
        self.assertIn("还没", self.response.completion_text)

    async def test_history_updates_only_matching_final_reply_and_preserves_other_parts(self):
        setattr(self.event, EDIT_ATTR, ("已经换好。", "还没换好。"))
        thought = SimpleNamespace(type="think", think="reasoning")
        old = SimpleNamespace(role="assistant", content=[SimpleNamespace(type="text", text="上一轮")])
        last = SimpleNamespace(role="assistant", content=[thought,
            SimpleNamespace(type="text", text="已经"), SimpleNamespace(type="text", text="换好。")])
        ctx = SimpleNamespace(messages=[old, last])
        sync_action_reply_history(self.event, ctx)
        self.assertEqual(last.content[1].text, "还没换好。")
        self.assertIs(last.content[0], thought)
        self.assertEqual(old.content[0].text, "上一轮")
        self.assertEqual(len(last.content), 2)
        last.content[1].text = "另一个插件修改后的正文"
        sync_action_reply_history(self.event, ctx)
        self.assertEqual(last.content[1].text, "另一个插件修改后的正文")

    async def test_refusal_and_ordinary_chat_are_preserved(self):
        for draft in ["今天就想穿这套，先不换啦。", "我也想你。", "下次有机会再穿给你看。"]:
            with self.subTest(draft=draft):
                self.event._daily_life_action_reply_done = False
                self.response.completion_text = draft
                self.replies({"decision": "keep"})
                await self.service.reconcile(self.event, self.response, self.execute)
                self.assertEqual(self.response.completion_text, draft)
        self.execute.assert_not_awaited()

    async def test_existing_attempt_never_executes_again(self):
        setattr(self.event, RECEIPTS_ATTR, [{"action": "update_outfit", "status": "pending"}])
        self.replies({"decision": "act", "action": "update_outfit", "detail": "换装"},
                     {"decision": "revise", "text": "还没换好，想穿的那套还在洗。"})
        await self.service.reconcile(self.event, self.response, self.execute)
        self.execute.assert_not_awaited()
        self.assertIn("还没换好", self.response.completion_text)

    async def test_unknown_execution_or_failed_rewrite_does_not_reuse_promise(self):
        self.replies({"decision": "act", "action": "update_outfit", "detail": "换装"})
        self.execute.side_effect = RuntimeError("unknown")
        await self.service.reconcile(self.event, self.response, self.execute)
        self.assertIn("没确认", self.response.completion_text)
        self.execute.assert_awaited_once()

    async def test_unavailable_review_after_tool_does_not_emit_unverified_completion(self):
        setattr(self.event, RECEIPTS_ATTR, [{"action": "update_outfit", "status": "unknown"}])
        self.response.completion_text = "都换好了。"
        self.runtime.call_text_model.side_effect = TimeoutError()
        await self.service.reconcile(self.event, self.response, self.execute)
        self.assertIn("没确认", self.response.completion_text)

    async def test_unavailable_review_preserves_returned_life_action_results(self):
        cases = [
            ("returned", "穿搭已更新：淡紫长裙。"),
            ("pending", "本轮换装未完成，当前衣物尚未改变。"),
            ("failed", "穿搭更新失败。"),
            ("returned", "此会话无权限，未执行生活调整。"),
        ]
        self.runtime.call_text_model.side_effect = TimeoutError()
        for status, result in cases:
            with self.subTest(status=status, result=result):
                event = SimpleNamespace(message_str="换好了吗",
                    _daily_life_expression_context={"conversation": "穿搭"})
                setattr(event, RECEIPTS_ATTR, [{"action": "update_outfit", "status": status,
                                              "result": result}])
                response = SimpleNamespace(completion_text="已经换好了。")
                await self.service.reconcile(event, response, self.execute)
                self.assertEqual(response.completion_text, result)
        self.execute.assert_not_awaited()

    async def test_external_action_cannot_be_executed(self):
        original = self.response.completion_text
        self.replies({"decision": "act", "action": "send_message", "detail": "给其他人发消息"})
        await self.service.reconcile(self.event, self.response, self.execute)
        self.execute.assert_not_awaited()
        self.assertEqual(self.response.completion_text, original)

    async def test_other_tools_keep_results_without_life_only_review(self):
        cases = [
            ("sub2api_manage", "已测 deepseek-chat，上游测试失败。"),
            ("sub2api_manage", "充值已成功，余额增加 10 元。"),
            ("sub2api_manage", "权限不足，未执行充值。"),
            ("sub2api_manage", "请求结果不明，请先核对余额。"),
            ("sub2api_query", "已查询，图片 0 张、视频 0 条。"),
            ("life_web_search", "查到了，检索结果如下。"),
            ("life_video_generate", "视频任务已提交，仍在生成。"),
            ("life_image_generate", "图片生成失败。"),
            ("life_commitment", "这项约定已经记下。"),
            ("another_plugin_tool", "操作已完成。"),
        ]
        self.replies({"decision": "revise", "text": "还没做，正窝在沙发上。"})
        for name, draft in cases:
            with self.subTest(tool=name, draft=draft):
                event = SimpleNamespace(message_str="处理一下",
                    _daily_life_expression_context={"activity": "坐在沙发上"})
                note_action_reply_tool(event, SimpleNamespace(name=name))
                response = SimpleNamespace(completion_text=draft)
                await self.service.reconcile(event, response, self.execute)
                self.assertEqual(response.completion_text, draft)
                self.assertFalse(hasattr(event, EDIT_ATTR))
        self.runtime.call_text_model.assert_not_awaited()
        self.runtime.archive.get_continuous_life.assert_not_awaited()
        self.execute.assert_not_awaited()

    async def test_markers_are_per_event_and_do_not_copy_sensitive_tool_fields(self):
        tool = SimpleNamespace(name="sub2api_manage", arguments={"password": "SECRET"},
                               result={"code": "SECRET"})
        note_action_reply_tool(self.event, tool)
        note_action_reply_tool(self.event, tool)
        self.assertEqual(getattr(self.event, TOOLS_ATTR), frozenset({"sub2api_manage"}))
        self.assertNotIn("SECRET", repr(vars(self.event)))
        await self.service.reconcile(self.event, self.response, self.execute)
        self.runtime.call_text_model.assert_not_awaited()
        other_event = SimpleNamespace(message_str="现在穿什么？",
            _daily_life_expression_context={"conversation": "穿搭"})
        self.replies({"decision": "keep"})
        await self.service.reconcile(other_event, self.response, self.execute)
        self.runtime.call_text_model.assert_awaited_once()

    async def test_life_tool_without_receipt_does_not_invent_or_retry_an_action(self):
        note_action_reply_tool(self.event, SimpleNamespace(name="life_adjust"))
        original = self.response.completion_text
        await self.service.reconcile(self.event, self.response, self.execute)
        self.runtime.call_text_model.assert_not_awaited()
        self.execute.assert_not_awaited()
        self.assertEqual(self.response.completion_text, original)

    async def test_life_tool_with_its_own_receipt_still_checks_life_facts(self):
        note_action_reply_tool(self.event, SimpleNamespace(name="life_adjust"))
        setattr(self.event, RECEIPTS_ATTR, [{"action": "update_outfit", "status": "pending"}])
        self.replies({"decision": "revise", "text": "还在准备，尚未换好。"})
        await self.service.reconcile(self.event, self.response, self.execute)
        self.runtime.call_text_model.assert_awaited_once()
        self.execute.assert_not_awaited()
        self.assertEqual(self.response.completion_text, "还在准备，尚未换好。")

    async def test_mixed_tool_turn_cannot_lose_external_result_on_review_failure(self):
        for name in ["life_adjust", "sub2api_manage"]:
            note_action_reply_tool(self.event, SimpleNamespace(name=name))
        setattr(self.event, RECEIPTS_ATTR, [{"action": "update_outfit", "status": "pending"}])
        self.runtime.call_text_model.side_effect = TimeoutError()
        self.response.completion_text = "测试已完成但上游失败，衣服还在准备。"
        await self.service.reconcile(self.event, self.response, self.execute)
        self.runtime.call_text_model.assert_not_awaited()
        self.execute.assert_not_awaited()
        self.assertEqual(self.response.completion_text, "测试已完成但上游失败，衣服还在准备。")

    async def test_new_message_during_review_cancels_execution(self):
        async def review(*args, **kwargs):
            self.runtime.continuous_turn_event_is_current = lambda event: False
            return json.dumps({"decision": "act", "action": "update_outfit", "detail": "换装"})
        self.runtime.call_text_model.side_effect = review
        await self.service.reconcile(self.event, self.response, self.execute)
        self.execute.assert_not_awaited()

    async def test_tool_call_chunk_and_uninjected_turn_are_skipped(self):
        for attrs in [{"is_chunk": True}, {"tools_call_name": ["life_adjust"]}, {"role": "tool"}]:
            response = SimpleNamespace(completion_text="这就去换", **attrs)
            await self.service.reconcile(self.event, response, self.execute)
        self.event._daily_life_expression_context = {}
        await self.service.reconcile(self.event, self.response, self.execute)
        self.runtime.call_text_model.assert_not_awaited()

    async def test_permission_still_applies_and_denial_is_recorded(self):
        command = DailyLifeCommandCenter(self.runtime)
        command.permission_denial = lambda *args, **kwargs: "此会话无权限"
        result = await command.adjust_life(self.event, "update_outfit", detail="换装")
        self.assertEqual(result, "此会话无权限")
        self.assertEqual(getattr(self.event, RECEIPTS_ATTR)[0]["result"], "此会话无权限")

