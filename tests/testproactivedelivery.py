"""主动联系以平台送达事实记账，覆盖部分发送和不明确结果。"""

import types
import unittest
from unittest.mock import AsyncMock, patch

import support  # noqa: F401
from core.runtime.proactive.send import ProactiveSendComponent
from core.sources.dispatch import PermanentScopeDeliveryError


class ProactiveDeliveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = types.SimpleNamespace(
            _snapshot_proactive_send_event=lambda *args, **kwargs: types.SimpleNamespace(),
            _proactive_send_is_current=lambda event: True,
            can_send_for_source=lambda *args, **kwargs: True,
            _apply_proactive_send_timing=AsyncMock(),
            _send_segmented_proactive_message=AsyncMock(),
            _append_proactive_send_history=AsyncMock(),
            _send_proactive_emoji_if_needed=AsyncMock(),
            archive=types.SimpleNamespace(mark_relationship_contact_unreachable=AsyncMock()),
        )
        self.component = ProactiveSendComponent(self.context)
        self.component._send_proactive_voice_if_enabled = AsyncMock(return_value=False)
        self.payload = {"source": "proactive_commitment"}

    async def send(self):
        return await self.component._send_proactive_message(
            "aiocqhttp:FriendMessage:10001", "刚到家\n给你看照片", "发送失败",
            send_payload=self.payload,
        )

    async def test_partial_send_preserves_only_delivered_text(self):
        async def interrupted(*args, on_sent, **kwargs):
            on_sent("刚到家")
            raise TimeoutError("第二段结果未确认")

        self.context._send_segmented_proactive_message.side_effect = interrupted
        self.assertTrue(await self.send())
        self.assertEqual(self.payload["_delivery"]["status"], "partial")
        self.assertEqual(self.payload["_delivery"]["sent_text"], "刚到家")
        self.context.archive.mark_relationship_contact_unreachable.assert_not_awaited()

    async def test_history_failure_does_not_erase_confirmed_delivery(self):
        async def delivered(*args, on_sent, **kwargs):
            on_sent("刚到家")
            on_sent("给你看照片")
            return True

        self.context._send_segmented_proactive_message.side_effect = delivered
        self.context._append_proactive_send_history.side_effect = OSError("写入失败")
        self.assertTrue(await self.send())
        self.assertEqual(self.payload["_delivery"]["status"], "confirmed")
        self.assertEqual(self.payload["_delivery"]["sent_text"], "刚到家\n给你看照片")

    async def test_unknown_delivery_does_not_mark_contact_unreachable(self):
        self.context._send_segmented_proactive_message.side_effect = TimeoutError("超时")
        self.assertFalse(await self.send())
        self.assertEqual(self.payload["_delivery"]["status"], "unknown")
        self.context.archive.mark_relationship_contact_unreachable.assert_not_awaited()

    async def test_punctuation_cleanup_does_not_change_delivery_completion(self):
        async def delivered(*args, on_sent, **kwargs):
            on_sent("刚到家")
            on_sent("给你看照片")
            return True

        self.context._send_segmented_proactive_message.side_effect = delivered
        self.assertTrue(await self.component._send_proactive_message(
            "aiocqhttp:FriendMessage:10001", "刚到家。\n给你看照片。", "发送失败",
            send_payload=self.payload,
        ))
        self.assertEqual(self.payload["_delivery"]["status"], "confirmed")
        self.assertEqual(self.payload["_delivery"]["sent_text"], "刚到家\n给你看照片")
        self.context._append_proactive_send_history.assert_awaited_once_with(
            "aiocqhttp:FriendMessage:10001", "刚到家\n给你看照片"
        )

    async def test_cancelled_tail_retains_partial_history_without_extra_message(self):
        async def interrupted(*args, on_sent, **kwargs):
            on_sent("刚到家")
            return False

        self.context._send_segmented_proactive_message.side_effect = interrupted
        self.assertTrue(await self.send())
        self.assertEqual(self.payload["_delivery"]["status"], "partial")
        self.assertEqual(self.payload["_delivery"]["sent_text"], "刚到家")
        self.context._append_proactive_send_history.assert_awaited_once_with(
            "aiocqhttp:FriendMessage:10001", "刚到家"
        )
        self.context._send_proactive_emoji_if_needed.assert_not_awaited()

    async def test_only_platform_rejection_marks_contact_unreachable(self):
        self.context._send_segmented_proactive_message.side_effect = PermanentScopeDeliveryError(
            "平台明确拒绝", code="platform_rejected"
        )
        self.assertFalse(await self.send())
        self.assertEqual(self.payload["_delivery"]["status"], "failed")
        self.context.archive.mark_relationship_contact_unreachable.assert_awaited_once()

    async def test_new_message_interrupts_before_transport(self):
        self.context._proactive_send_is_current = lambda event: False
        self.assertFalse(await self.send())
        self.assertEqual(self.payload["_delivery"]["status"], "interrupted")
        self.context._send_segmented_proactive_message.assert_not_awaited()

    async def test_contact_rhythm_defers_without_failure(self):
        self.payload["source"] = "proactive_reply"
        with patch("core.runtime.proactive.send.contact_rhythm", new=AsyncMock(return_value={"ready": False})):
            self.assertFalse(await self.send())
        self.assertEqual(self.payload["_delivery"]["status"], "deferred")
        self.context._send_segmented_proactive_message.assert_not_awaited()
        self.context.archive.mark_relationship_contact_unreachable.assert_not_awaited()
