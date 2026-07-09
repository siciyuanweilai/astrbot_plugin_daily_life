import types
import unittest

from support import Event, LifeSettings
from core.runtime.capture.event import CaptureEventMixin
from core.runtime.proactive.procontext import ProactiveContextMixin
from core.runtime.structured import StructuredContextMixin


class At:
    def __init__(self, user_id, name=""):
        self.user_id = user_id
        self.target_user_nickname = name


class Reply:
    def __init__(self, message_id, sender_id="", sender_name="", content=""):
        self.message_id = message_id
        self.target_message_sender_id = sender_id
        self.target_message_sender_nickname = sender_name
        self.target_message_content = content


class Image:
    type = "image"

    def __init__(self, url="https://example.com/a.png"):
        self.url = url


class Runtime(CaptureEventMixin, StructuredContextMixin, ProactiveContextMixin):
    def _proactive_is_self_message(self, event):
        return False


class StructuredContextTest(unittest.TestCase):
    def make_group_event(self, *, sender_id, sender_name, message_id, text, items=None, card=""):
        event = Event(
            sender_id=sender_id,
            sender_name=sender_name,
            message_id=message_id,
            self_id="bot01",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="测试群",
        )
        event.message_str = text
        event.message_items = list(items or [])
        event.message_obj = types.SimpleNamespace(
            message=event.message_items,
            sender=types.SimpleNamespace(card=card),
            raw_message={"sender": {"card": card}},
        )
        return event

    def test_group_messages_keep_card_reply_and_addressee(self):
        runtime = Runtime()
        first = self.make_group_event(
            sender_id="u1",
            sender_name="昵称甲",
            card="群名片甲",
            message_id="m1",
            text="今天还去吃火锅吗",
        )
        second = self.make_group_event(
            sender_id="u2",
            sender_name="昵称乙",
            card="群名片乙",
            message_id="m2",
            text="去啊，等你",
            items=[Reply("m1")],
        )

        runtime.note_structured_incoming_message(first)
        message = runtime.note_structured_incoming_message(second)

        self.assertEqual(message.display_sender, "群名片乙")
        self.assertEqual(message.reply_to_id, "m1")
        self.assertEqual(message.reply_to_sender_id, "u1")
        self.assertEqual(message.talking_to_id, "u1")
        self.assertEqual(message.talking_to_name, "群名片甲")

    def test_at_bot_marks_message_as_talking_to_me(self):
        runtime = Runtime()
        event = self.make_group_event(
            sender_id="u1",
            sender_name="昵称甲",
            message_id="m1",
            text="你怎么看",
            items=[At("bot01", "机器人")],
        )

        message = runtime.note_structured_incoming_message(event)

        self.assertEqual(message.talking_to_id, "bot")
        self.assertEqual(message.talking_to_name, "我")

    def test_bot_alias_marks_message_as_talking_to_me(self):
        runtime = Runtime()
        runtime.config = LifeSettings.from_dict({"bot_identity_aliases": ["小助手"]})
        event = self.make_group_event(
            sender_id="u1",
            sender_name="昵称甲",
            message_id="m1-alias",
            text="小助手，你怎么看",
        )

        message = runtime.note_structured_incoming_message(event)

        self.assertEqual(message.talking_to_id, "bot")
        self.assertEqual(message.talking_to_name, "我")

    def test_bot_reply_is_written_back_to_same_flow(self):
        runtime = Runtime()
        event = self.make_group_event(
            sender_id="u2",
            sender_name="昵称乙",
            card="群名片乙",
            message_id="m2",
            text="去啊，等你",
        )
        runtime.note_structured_incoming_message(event)

        bot_message = runtime.note_structured_bot_message(
            "aiocqhttp:GroupMessage:10001",
            "那我晚点过去",
            source_event=event,
        )
        context = runtime.format_structured_message_context(event)

        self.assertTrue(bot_message.is_bot)
        self.assertEqual(bot_message.reply_to_id, "m2")
        self.assertIn('role="assistant"', context)
        self.assertIn("群名片乙", context)
        self.assertIn("那我晚点过去", context)

    def test_image_message_keeps_text_and_accepts_visual_summary(self):
        runtime = Runtime()
        event = self.make_group_event(
            sender_id="u2",
            sender_name="昵称乙",
            card="群名片乙",
            message_id="m-img",
            text="看这个",
            items=[Image()],
        )

        message = runtime.note_structured_incoming_message(event)
        self.assertIn("看这个 [图片]", message.content)

        runtime.update_structured_message_visual_summary(
            "aiocqhttp:GroupMessage:10001",
            "m-img",
            "桌上有一盘切好的水果",
        )
        context = runtime.format_structured_message_context(event)

        self.assertIn("看这个 [图片：桌上有一盘切好的水果]", context)
        self.assertEqual(message.visual_summary, "桌上有一盘切好的水果")

    def test_structured_history_export_keeps_speaker_and_addressee(self):
        runtime = Runtime()
        first = self.make_group_event(
            sender_id="u1",
            sender_name="昵称甲",
            card="群名片甲",
            message_id="m1",
            text="这个活动你去吗",
        )
        second = self.make_group_event(
            sender_id="u2",
            sender_name="昵称乙",
            card="群名片乙",
            message_id="m2",
            text="我去，你呢",
            items=[Reply("m1")],
        )

        runtime.note_structured_incoming_message(first)
        runtime.note_structured_incoming_message(second)

        messages = runtime.structured_recent_history_messages("aiocqhttp:GroupMessage:10001", limit=2)
        text = runtime._format_recent_context_messages(messages)

        self.assertEqual(messages[-1]["name"], "群名片乙")
        self.assertEqual(messages[-1]["talking_to_name"], "群名片甲")
        self.assertEqual(messages[-1]["reply_to_content"], "这个活动你去吗")
        self.assertIn("群名片乙 -> 群名片甲", text)
        self.assertIn("引用群名片甲", text)


if __name__ == "__main__":
    unittest.main()
