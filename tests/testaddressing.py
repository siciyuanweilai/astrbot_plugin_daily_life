import asyncio
import types
import unittest
from unittest.mock import patch

from support import Context, Event, Provider
from astrbot.api.message_components import At, Reply

from core.config.options import LifeSettings
from core.runtime import DailyLifeRuntime
from core.runtime.style import _ChatStyleSegmentPlan


def component_kind(item):
    if isinstance(item, dict):
        return str(item.get("type") or item.get("kind") or "").lower()
    return str(getattr(item, "type", "") or item.__class__.__name__).lower()


def component_message_id(item):
    return str(
        getattr(item, "target_message_id", "")
        or getattr(item, "message_id", "")
        or getattr(item, "id", "")
        or (item.get("target_message_id") if isinstance(item, dict) else "")
        or (item.get("message_id") if isinstance(item, dict) else "")
        or (item.get("id") if isinstance(item, dict) else "")
        or ""
    )


class ChatAddressingTest(unittest.TestCase):
    def make_runtime(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.context = Context(Provider())
        return runtime

    def make_group_event(self, *, sender_id="u1", message_id="m1", text="找你一下", self_id="bot01"):
        event = Event(
            sender_id=sender_id,
            sender_name=f"用户{sender_id}",
            message_id=message_id,
            self_id=self_id,
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="测试群",
        )
        event.message_str = text
        return event

    def test_group_at_bot_does_not_always_quote(self):
        runtime = self.make_runtime()
        event = self.make_group_event()
        event.is_at_or_wake_command = True
        event.message_items = [At(user_id="bot01")]
        event.message_obj = types.SimpleNamespace(message=event.message_items)
        runtime.note_structured_incoming_message(event)
        event.set_result(event.chain_result([types.SimpleNamespace(text="在呢")]))

        changed = runtime.apply_group_addressing_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(component_kind(event.get_result().chain[0]), "simplenamespace")

    def test_group_reply_target_for_proactive_quotes_source(self):
        runtime = self.make_runtime()

        target = runtime._group_reply_target(
            target_scope="aiocqhttp:GroupMessage:10001",
            source_message_id="m-source",
            source="proactive",
        )

        self.assertEqual(target.mode, "quote_source")
        self.assertEqual(target.message_id, "m-source")

    def test_group_reply_target_uses_at_when_directed_but_message_id_missing(self):
        runtime = self.make_runtime()
        runtime.note_structured_incoming_message(self.make_group_event(sender_id="u2", message_id="m-old"))
        event = self.make_group_event(sender_id="u1", message_id="")
        event.is_at_or_wake_command = True
        event.message_items = [At(user_id="bot01")]
        event.message_obj = types.SimpleNamespace(message=event.message_items)
        runtime.note_structured_incoming_message(event)

        target = runtime._group_reply_target(
            target_scope="aiocqhttp:GroupMessage:10001",
            source_event=event,
        )

        self.assertEqual(target.mode, "at_sender")
        self.assertEqual(target.user_id, "u1")

    def test_group_reply_message_quotes_current_user_message(self):
        runtime = self.make_runtime()
        event = self.make_group_event(message_id="m2")
        event.message_items = [Reply(message_id="bot-old", target_message_sender_id="bot01")]
        event.message_obj = types.SimpleNamespace(message=event.message_items)
        runtime.note_structured_incoming_message(event)
        event.set_result(event.chain_result([types.SimpleNamespace(text="我看到了")]))

        changed = runtime.apply_group_addressing_before_send(event)

        self.assertTrue(changed)
        self.assertIn("reply", component_kind(event.get_result().chain[0]))
        self.assertEqual(component_message_id(event.get_result().chain[0]), "m2")

    def test_group_reply_keeps_long_text_plain_for_astrbot_t2i(self):
        runtime = self.make_runtime()
        runtime.context = Context(
            Provider(),
            config={
                "t2i": True,
                "t2i_word_threshold": 50,
            },
        )
        event = self.make_group_event(message_id="m2")
        event.message_items = [Reply(message_id="bot-old", target_message_sender_id="bot01")]
        event.message_obj = types.SimpleNamespace(message=event.message_items)
        runtime.note_structured_incoming_message(event)
        reply = (
            "这是一段比较长的群聊引用回复。"
            "如果这里先插入 Reply 组件，AstrBot 的文本转图像阶段会因为链首不是 Plain 而跳过。"
            "所以长文本要保持纯文本链，交给 AstrBot 默认发送链转成图片。"
        )
        event.set_result(event.chain_result([types.SimpleNamespace(text=reply)]))

        changed = runtime.apply_group_addressing_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(len(event.get_result().chain), 1)
        self.assertEqual(event.get_result().chain[0].text, reply)

    def test_group_ambiguous_at_bot_quotes_first_segment_only(self):
        runtime = self.make_runtime()
        for method_name in (
            "note_structured_sent_result",
            "note_media_source_event",
            "note_proactive_bot_reply",
            "note_voice_switch_text_result",
        ):
            setattr(runtime, method_name, lambda event: None)

        previous = self.make_group_event(sender_id="u2", message_id="m1", text="我先说一句")
        runtime.note_structured_incoming_message(previous)

        event = self.make_group_event(sender_id="u1", message_id="m2", text="你怎么看")
        event.is_at_or_wake_command = True
        event.message_items = [At(user_id="bot01")]
        event.message_obj = types.SimpleNamespace(message=event.message_items)
        runtime.note_structured_incoming_message(event)
        segments = ["先这样。", "后面再说。"]
        event.set_result(event.chain_result([types.SimpleNamespace(text=text) for text in segments]))
        pending = runtime._chat_style_pending_segments(
            [
                _ChatStyleSegmentPlan(raw_text=segments[0], text=segments[0], separator="。", break_kind="strong"),
                _ChatStyleSegmentPlan(raw_text=segments[1], text=segments[1], separator="。", break_kind="strong"),
            ]
        )
        setattr(event, runtime._CHAT_STYLE_PENDING_SEGMENTS_ATTR, pending)

        async def fake_sleep(seconds):
            return None

        with patch("core.runtime.style.asyncio.sleep", fake_sleep):
            sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertTrue(sent)
        self.assertEqual(len(event.sent_messages), 2)
        self.assertIn("reply", component_kind(event.sent_messages[0].chain[0]))
        self.assertEqual(component_message_id(event.sent_messages[0].chain[0]), "m2")
        self.assertNotIn("reply", component_kind(event.sent_messages[1].chain[0]))

    def test_group_proactive_reply_quotes_source_message(self):
        runtime = self.make_runtime()
        source = self.make_group_event(sender_id="u3", message_id="m-source", text="没人接话了吗")

        sent = asyncio.run(
            runtime._send_segmented_proactive_message(
                "aiocqhttp:GroupMessage:10001",
                "我接一下",
                source_event=source,
            )
        )

        self.assertTrue(sent)
        _, chain = runtime.context.sent_messages[0]
        self.assertIn("reply", component_kind(chain.chain[0]))
        self.assertEqual(component_message_id(chain.chain[0]), "m-source")

    def test_group_proactive_reply_records_explicit_source_message(self):
        runtime = self.make_runtime()
        source = self.make_group_event(sender_id="u3", message_id="m-source", text="没人接话了吗")
        runtime.note_structured_incoming_message(source)

        sent = asyncio.run(
            runtime._send_segmented_proactive_message(
                "aiocqhttp:GroupMessage:10001",
                "我接一下",
                source_message_id="m-source",
            )
        )

        self.assertTrue(sent)
        messages = runtime.structured_recent_history_messages("aiocqhttp:GroupMessage:10001", limit=2)
        self.assertEqual(messages[-1]["reply_to_id"], "m-source")
        self.assertEqual(messages[-1]["talking_to_name"], "用户u3")

    def test_private_reply_never_adds_group_addressing(self):
        runtime = self.make_runtime()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="m-private")
        event.message_str = "在吗"
        event.set_result(event.chain_result([types.SimpleNamespace(text="在呀")]))

        changed = runtime.apply_group_addressing_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(len(event.get_result().chain), 1)


if __name__ == "__main__":
    unittest.main()
