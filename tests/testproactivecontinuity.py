import unittest
from unittest.mock import AsyncMock, patch

from runtimehelpers import (
    Event,
    RuntimeAsyncHelperMixin,
    async_return,
    datetime,
    json,
    types,
)


class ProactiveContinuityTest(
    RuntimeAsyncHelperMixin, unittest.IsolatedAsyncioTestCase
):
    scope = "aiocqhttp:FriendMessage:10001"

    def save_history(self, runtime, messages, scope=None):
        target = scope or self.scope
        runtime.context.conversation_manager.current_ids[target] = "current"
        runtime.context.conversation_manager.conversations[target] = (
            types.SimpleNamespace(history=messages)
        )

    async def test_partial_cache_backfills_saved_topic_without_duplicate_last_reply(
        self,
    ):
        runtime, _ = self._make_proactive_runtime()
        history = [
            {"role": "user", "content": "今天面试没有通过，有点失落。", "name": "阿林"},
            {"role": "assistant", "content": "先缓一缓，我陪你。"},
            {"role": "user", "content": "嗯", "name": "阿林"},
        ]
        self.save_history(runtime, history)
        event = Event(
            unified_msg_origin=self.scope, sender_id="10001", sender_name="阿林"
        )
        event.message_str = "嗯"
        runtime.note_structured_incoming_message(event)
        messages = await runtime._read_recent_context_messages(self.scope)
        self.assertEqual(
            [item["content"] for item in messages],
            [item["content"] for item in history],
        )
        self.assertEqual(messages[-1]["user_id"], "10001")
        prompt = await runtime._build_recent_context_for_proactive(self.scope)
        self.assertIn("今天面试没有通过", prompt)
        self.assertIn("我: 先缓一缓，我陪你。", prompt)

    async def test_saved_reply_after_cached_user_stays_last_and_other_scope_is_excluded(
        self,
    ):
        runtime, _ = self._make_proactive_runtime()
        self.save_history(
            runtime,
            [
                {"role": "user", "content": "明天再聊吧"},
                {"role": "assistant", "content": "好，晚安"},
            ],
        )
        self.save_history(
            runtime,
            [{"role": "user", "content": "其他人的私聊"}],
            "aiocqhttp:FriendMessage:20002",
        )
        event = Event(
            unified_msg_origin=self.scope, sender_id="10001", sender_name="阿林"
        )
        event.message_str = "明天再聊吧"
        runtime.note_structured_incoming_message(event)
        messages = await runtime._read_recent_context_messages(self.scope)
        self.assertEqual(
            [item["content"] for item in messages], ["明天再聊吧", "好，晚安"]
        )

    async def test_merge_preserves_distinct_repeated_messages_and_speakers(self):
        runtime, _ = self._make_proactive_runtime()
        saved = [
            {"role": "user", "content": "嗯", "timestamp": "100", "user_id": "1"},
            {"role": "assistant", "content": "那先休息", "timestamp": "110"},
            {"role": "user", "content": "嗯", "timestamp": "120", "user_id": "1"},
        ]
        cached = [
            {**saved[-1], "reply_to_content": "那先休息"},
            {"role": "user", "content": "嗯", "timestamp": "121", "user_id": "2"},
        ]
        messages = runtime._merge_recent_context_messages(saved, cached)
        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[2]["reply_to_content"], "那先休息")
        self.assertEqual(messages[-1]["user_id"], "2")
        self.assertEqual(
            runtime._merge_recent_context_messages(saved[:1], cached[:1]),
            [saved[0], cached[0]],
        )
        named = {"role": "user", "content": "嗯", "user_id": "阿林", "name": "阿林"}
        other = {"role": "user", "content": "嗯", "user_id": "2", "name": "小雨"}
        self.assertEqual(
            runtime._merge_recent_context_messages([named], [other]), [named, other]
        )

    async def test_backfill_ignores_system_and_tool_messages_without_losing_chat(self):
        runtime, _ = self._make_proactive_runtime()
        history = [
            {"role": "user", "content": "我想安静一会儿"},
            {"role": "assistant", "content": "好，我在"},
            *[
                {"role": role, "content": "天气晴，主动聊出游"}
                for role in ("system", "developer", "tool", "function")
                for _ in range(4)
            ],
        ]
        self.save_history(runtime, history)
        messages = await runtime._read_recent_context_messages(self.scope, limit=2)
        self.assertEqual(
            [m["content"] for m in messages], ["我想安静一会儿", "好，我在"]
        )

    async def test_saved_history_failure_keeps_current_cache(self):
        runtime, _ = self._make_proactive_runtime()
        runtime.note_structured_bot_message(self.scope, "那就先休息")
        with patch(
            "core.runtime.proactive.procontext.SavedHistoryReader.fetch",
            new=AsyncMock(side_effect=RuntimeError("offline")),
        ):
            messages = await runtime._read_recent_context_messages(self.scope)
        self.assertEqual([m["content"] for m in messages], ["那就先休息"])

    async def test_history_keeps_end_of_message_and_requested_ten_messages(self):
        runtime, _ = self._make_proactive_runtime()
        text = "先说一下今天的事情。" * 30 + "不过我现在想自己静静，今晚不聊了。"
        runtime.note_structured_bot_message(self.scope, text)
        messages = runtime.structured_recent_history_messages(self.scope)
        self.assertEqual(messages[-1]["content"], text)
        self.assertIn("今晚不聊了", runtime._format_recent_context_messages(messages))
        messages = [{"role": "user", "content": f"第{i}条消息"} for i in range(10)]
        context = runtime._format_recent_context_messages(messages)
        self.assertIn("第0条消息", context)
        self.assertIn("第9条消息", context)
        messages = [
            {"role": "user", "content": "开头" + "很长的细节" * 1000 + "结尾不要打扰"}
            for _ in range(10)
        ]
        context = runtime._format_recent_context_messages(messages)
        self.assertIn("[中间省略]", context)
        self.assertTrue(context.endswith("结尾不要打扰"))
        self.assertLess(len(context), 6200)
        messages[-1]["reply_to_content"] = "先前的一大段引用" * 500
        self.assertIn("结尾不要打扰", runtime._format_recent_context_messages(messages))

    def proposal(self, text):
        return json.dumps(
            {
                "should_reply": True,
                "expression_review": {"passed": True},
                "decision": "reply",
                "confidence": 0.95,
                "reason": "想继续关心",
                "reply_text": text,
                "benefit": 90,
                "timeliness": 90,
                "continuity": 90,
                "disruption": 0,
                "uncertainty": 0,
            },
            ensure_ascii=False,
        )

    async def test_idle_reply_reviews_context_and_declines_disconnected_candidate(self):
        runtime, provider = self._make_proactive_runtime(
            [
                self.proposal("我刚刚吃了甜点，你吃了吗？"),
                '{"valid":false,"reason":"忽略面试失落和想静静的收尾","conflicts":["突然聊甜点"]}',
            ]
        )
        runtime.composer._audit_person_payload = None
        runtime._proactive_readiness_check = lambda *args, **kwargs: async_return(
            {"should_evaluate": True}
        )
        self.save_history(
            runtime,
            [
                {"role": "user", "content": "面试没过，我想自己静静。"},
                {"role": "assistant", "content": "好，需要我时再叫我。"},
            ],
        )
        event = Event(
            unified_msg_origin=self.scope, sender_id="10001", sender_name="阿林"
        )
        event.message_str = "面试没过，我想自己静静。"
        result = await runtime.evaluate_proactive_reply(
            event, now=datetime.datetime(2026, 9, 20, 18, 0)
        )
        self.assertFalse(result["should_reply"])
        self.assertEqual(result["reply_text"], "")
        self.assertEqual(result["reason_code"], "continuity_audit_failed")
        self.assertFalse(result["voice_call_intent"]["should_invite"])
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("需要我时再叫我", provider.prompts[-1])
        self.assertEqual(runtime.context.sent_messages, [])

    async def test_idle_reply_keeps_short_continuation_after_successful_review(self):
        reply = "那就早点休息"
        runtime, provider = self._make_proactive_runtime(
            [
                self.proposal(reply),
                '{"valid":true,"reason":"承接疲惫的表达","conflicts":[]}',
            ]
        )
        runtime.composer._audit_person_payload = None
        runtime._proactive_readiness_check = lambda *args, **kwargs: async_return(
            {"should_evaluate": True}
        )
        self.save_history(runtime, [{"role": "user", "content": "今天忙完感觉好累"}])
        event = Event(
            unified_msg_origin=self.scope, sender_id="10001", sender_name="阿林"
        )
        event.message_str = "今天忙完感觉好累"
        result = await runtime.evaluate_proactive_reply(
            event, now=datetime.datetime(2026, 9, 20, 18, 0)
        )
        self.assertTrue(result["should_reply"])
        self.assertEqual(result["reply_text"], reply)
        self.assertEqual(len(provider.prompts), 2)
        self.assertEqual(runtime.context.sent_messages, [])

    async def test_private_revisit_reviews_untimed_history_and_preserves_grounded_reply(
        self,
    ):
        for valid in (False, True):
            with self.subTest(valid=valid):
                runtime, provider = self._make_proactive_runtime(
                    [
                        self.proposal("面试那件事，现在好些了吗？"),
                        json.dumps(
                            {
                                "valid": valid,
                                "reason": "承接面试话题" if valid else "时机不合适",
                                "conflicts": [],
                            }
                        ),
                    ]
                )
                runtime.composer._audit_person_payload = None
                self.save_history(
                    runtime,
                    [
                        {"role": "user", "content": "面试没过，有点失落。"},
                        {"role": "assistant", "content": "先缓缓"},
                    ],
                )
                relationship = types.SimpleNamespace(
                    name="阿林",
                    id="10001",
                    notes=[],
                    relationship_story="",
                    persona_hint="",
                )
                result = await runtime._evaluate_private_revisit_payload(
                    self.scope,
                    relationship=relationship,
                    now=datetime.datetime(2026, 9, 20, 18, 0),
                )
                self.assertEqual(result["should_reply"], valid)
                self.assertEqual(len(provider.prompts), 2)
                self.assertIn("面试没过", provider.prompts[-1])
                self.assertEqual(
                    result["reply_text"], "面试那件事，现在好些了吗？" if valid else ""
                )

    async def test_audit_failure_does_not_send_candidate(self):
        runtime, provider = self._make_proactive_runtime(
            [RuntimeError("temporarily unavailable")]
        )
        valid, _ = await runtime._audit_proactive_continuity(
            payload={"reply_text": "还在吗"},
            recent_context="对方: 我睡了\n我: 晚安",
            provider=provider,
            provider_id="proactive-model",
        )
        self.assertFalse(valid)
        self.assertEqual(runtime.context.sent_messages, [])
