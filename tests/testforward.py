import json
import sys
import types
import unittest
from pathlib import Path

from support import Event

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from astrbot_plugin_daily_life.core.runtime.forward import (  # noqa: E402
    TextForwardMixin,
    TextImageSource,
)
from astrbot_plugin_daily_life.core.runtime.style import (  # noqa: E402
    ChatStyleRuntimeMixin,
)
from astrbot_plugin_daily_life.main import DailyLifePlugin  # noqa: E402


class _SendConfigContext:
    def __init__(self, config=None):
        self.config = dict(config or {})

    def get_config(self, _scope=None):
        return dict(self.config)


class _ForwardRuntime(TextForwardMixin, ChatStyleRuntimeMixin):
    def __init__(self, config=None):
        self.context = _SendConfigContext(config)
        self._init_t2i_forward_cache()


class TextForwardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = _ForwardRuntime(
            {"t2i": True, "t2i_word_threshold": 50}
        )

    @staticmethod
    def _image_result(event):
        event.set_result(event.chain_result([{"type": "image", "file": "result.png"}]))

    def _commit(self, event, text):
        event._daily_life_t2i_default_send = True
        event._daily_life_t2i_source_text = text
        self._image_result(event)
        return self.runtime.note_t2i_image_sent(event)

    def test_t2i_candidate_captures_exact_source_text(self):
        event = Event()
        source = "第一段，保留标点。\n第二段？"

        kept = self.runtime._chat_style_should_keep_default_send(event, source * 4)

        self.assertTrue(kept)
        self.assertEqual(event._daily_life_t2i_source_text, source * 4)
        self.assertEqual(self.runtime._t2i_forward_cache, {})

    def test_send_pipeline_capture_does_not_depend_on_chat_style_enabled(self):
        event = Event()
        source = "关闭聊天表达后也要保留的长文本。" * 5
        event.set_result(event.chain_result([source]))

        captured = self.runtime.capture_t2i_source_before_send(event)

        self.assertTrue(captured)
        self.assertEqual(event._daily_life_t2i_source_text, source)

    def test_successful_image_send_commits_without_automatic_forward(self):
        event = Event(message_id="reply-1")
        source = "原文，标点和\n换行都保留。"

        committed = self._commit(event, source)

        self.assertTrue(committed)
        self.assertEqual(event.sent_messages, [])
        records = self.runtime._t2i_forward_cache[event.unified_msg_origin]
        self.assertEqual(records[0].text, source)
        self.assertEqual(records[0].message_id, "reply-1")
        self.assertFalse(hasattr(event, "_daily_life_t2i_source_text"))

    def test_plain_fallback_does_not_commit(self):
        event = Event()
        event._daily_life_t2i_default_send = True
        event._daily_life_t2i_source_text = "转图失败后的原文"
        event.set_result(event.chain_result(["转图失败后的原文"]))

        committed = self.runtime.note_t2i_image_sent(event)

        self.assertFalse(committed)
        self.assertEqual(self.runtime._t2i_forward_cache, {})
        self.assertFalse(hasattr(event, "_daily_life_t2i_source_text"))

    async def test_latest_and_previous_records_send_exact_onebot_forward(self):
        event = Event(self_id="10000")
        self._commit(event, "第一条，原样。")
        self._commit(event, "第二条\n也原样？")

        payload = json.loads(await self.runtime.forward_t2i_text(event, index=2))

        self.assertEqual(payload["status"], "sent")
        self.assertEqual(len(event.sent_messages), 1)
        message = event.sent_messages[0]
        nodes = message.chain[0]
        self.assertEqual(len(nodes.nodes), 1)
        node = nodes.nodes[0]
        self.assertEqual(node.uin, "10000")
        self.assertEqual(str(node.content[0]), "第一条，原样。")

    async def test_group_and_private_sessions_are_isolated(self):
        private = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        group = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")
        self._commit(private, "私聊原文")
        self._commit(group, "群聊原文")

        await self.runtime.forward_t2i_text(private)
        await self.runtime.forward_t2i_text(group)

        private_text = str(private.sent_messages[0].chain[0].nodes[0].content[0])
        group_text = str(group.sent_messages[0].chain[0].nodes[0].content[0])
        self.assertEqual(private_text, "私聊原文")
        self.assertEqual(group_text, "群聊原文")

    async def test_expired_record_is_unavailable(self):
        event = Event()
        self.runtime._t2i_forward_cache[event.unified_msg_origin] = [
            TextImageSource(
                text="过期原文",
                created_at=self.runtime._t2i_forward_now()
                - self.runtime._T2I_FORWARD_TTL_SECONDS
                - 1,
            )
        ]

        payload = json.loads(await self.runtime.forward_t2i_text(event))

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(event.sent_messages, [])

    async def test_cache_keeps_only_five_recent_records(self):
        event = Event()
        for index in range(7):
            self._commit(event, f"原文{index}")

        records = self.runtime._t2i_forward_cache[event.unified_msg_origin]

        self.assertEqual(
            [record.text for record in records],
            ["原文2", "原文3", "原文4", "原文5", "原文6"],
        )
        await self.runtime.forward_t2i_text(event, index=5)
        text = str(event.sent_messages[0].chain[0].nodes[0].content[0])
        self.assertEqual(text, "原文2")

    async def test_unsupported_platform_does_not_send(self):
        event = Event(
            platform_name="weixin_oc",
            unified_msg_origin="weixin_oc:FriendMessage:10001",
        )
        self._commit(event, "微信原文")

        payload = json.loads(await self.runtime.forward_t2i_text(event))

        self.assertEqual(payload["status"], "unsupported")
        self.assertEqual(event.sent_messages, [])

    async def test_missing_record_is_unavailable(self):
        event = Event()

        payload = json.loads(await self.runtime.forward_t2i_text(event))

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(event.sent_messages, [])

    async def test_forward_failure_does_not_fall_back_to_plain_text(self):
        class FailedEvent(Event):
            async def send(self, message):
                raise RuntimeError("send failed")

        event = FailedEvent()
        self._commit(event, "不可泄漏的原文")

        payload = json.loads(await self.runtime.forward_t2i_text(event))

        self.assertEqual(payload["status"], "error")
        self.assertEqual(event.sent_messages, [])

    async def test_plugin_tool_delegates_requested_index(self):
        calls = []

        async def forward_t2i_text(event, *, index=1):
            calls.append((event, index))
            return "sent"

        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(forward_t2i_text=forward_t2i_text)
        event = Event()

        result = await plugin.tool_life_text_forward(event, index=2)

        self.assertEqual(result, "sent")
        self.assertEqual(calls, [(event, 2)])
