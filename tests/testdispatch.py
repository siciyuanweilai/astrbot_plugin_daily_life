import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.sources.dispatch import send_message_to_scope


def _metadata(platform_id: str, platform_type: str):
    return SimpleNamespace(id=platform_id, name=platform_type)


class DispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_weixin_friend_uses_weixin_adapter_with_duplicate_platform_id(self):
        sender = AsyncMock()
        qq = SimpleNamespace(meta=lambda: _metadata("测试角色", "aiocqhttp"))
        weixin = SimpleNamespace(
            meta=lambda: _metadata("测试角色", "weixin_oc"),
            send_by_session=sender,
        )
        fallback = AsyncMock()
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(get_insts=lambda: [qq, weixin]),
            send_message=fallback,
        )
        chain = SimpleNamespace(items=[{"type": "text", "text": "测试消息"}])

        sent = await send_message_to_scope(
            context,
            "测试角色:FriendMessage:test-user@im.wechat",
            chain,
        )

        self.assertTrue(sent)
        sender.assert_awaited_once()
        session, delivered = sender.await_args.args
        self.assertEqual(session.platform_name, "测试角色")
        self.assertEqual(session.session_id, "test-user@im.wechat")
        self.assertEqual(getattr(session.message_type, "value", ""), "FriendMessage")
        self.assertIs(delivered, chain)
        fallback.assert_not_awaited()

    async def test_weixin_group_uses_group_message_session(self):
        sender = AsyncMock()
        weixin = SimpleNamespace(
            meta=lambda: _metadata("微信平台", "weixin_oc"),
            send_by_session=sender,
        )
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(get_insts=lambda: [weixin]),
            send_message=AsyncMock(),
        )

        sent = await send_message_to_scope(
            context,
            "微信平台:GroupMessage:test-room@chatroom",
            SimpleNamespace(items=[]),
        )

        self.assertTrue(sent)
        session = sender.await_args.args[0]
        self.assertEqual(session.session_id, "test-room@chatroom")
        self.assertEqual(getattr(session.message_type, "value", ""), "GroupMessage")

    async def test_weixin_does_not_fall_back_to_another_platform_when_unavailable(self):
        fallback = AsyncMock()
        qq = SimpleNamespace(meta=lambda: _metadata("测试角色", "aiocqhttp"))
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(get_insts=lambda: [qq]),
            send_message=fallback,
        )

        sent = await send_message_to_scope(
            context,
            "测试角色:FriendMessage:test-user@im.wechat",
            SimpleNamespace(items=[]),
        )

        self.assertFalse(sent)
        fallback.assert_not_awaited()

    async def test_non_weixin_scope_uses_context_sender(self):
        sender = AsyncMock(return_value=None)
        context = SimpleNamespace(send_message=sender)
        chain = SimpleNamespace(items=[])

        sent = await send_message_to_scope(
            context,
            "aiocqhttp:FriendMessage:10001",
            chain,
        )

        self.assertTrue(sent)
        sender.assert_awaited_once_with("aiocqhttp:FriendMessage:10001", chain)


if __name__ == "__main__":
    unittest.main()
