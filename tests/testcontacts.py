import asyncio
import types
import unittest

from support import ActionBot, ContactNameResolver, Event, OneBot, PersonaManager


class ContactNameResolverTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_alias_overrides_event_and_onebot_name(self):
        bot = OneBot({"remark": "QQ备注", "nickname": "QQ昵称"})
        resolver = ContactNameResolver(None, {"relationship_aliases": ["123456:本地称呼"]})

        name = await resolver.resolve_event_sender(Event(bot=bot))

        self.assertEqual(name, "本地称呼")
        self.assertEqual(bot.calls, [])

    async def test_local_alias_is_plain_contact_name(self):
        resolver = ContactNameResolver(None, {"relationship_aliases": ["123456:阿林"]})

        name = await resolver.resolve_event_sender(Event())

        self.assertEqual(name, "阿林")

    async def test_onebot_remark_used_when_alias_missing(self):
        bot = OneBot({"remark": "QQ备注", "nickname": "QQ昵称"})
        resolver = ContactNameResolver(None, {"relationship_aliases": []})

        name = await resolver.resolve_event_sender(Event(bot=bot))

        self.assertEqual(name, "QQ备注")
        self.assertEqual(bot.calls[0], ("get_stranger_info", {"user_id": 123456}))

    async def test_onebot_remark_is_cached(self):
        bot = OneBot({"remark": "QQ备注", "nickname": "QQ昵称"})
        resolver = ContactNameResolver(None, {"relationship_aliases": []})
        event = Event(bot=bot)

        first = await resolver.resolve_event_sender(event)
        second = await resolver.resolve_event_sender(event)

        self.assertEqual(first, "QQ备注")
        self.assertEqual(second, "QQ备注")
        self.assertEqual(len(bot.calls), 1)

    async def test_onebot_remark_concurrent_requests_share_pending_lookup(self):
        class SlowOneBot(OneBot):
            async def call_action(self, action, **params):
                await asyncio.sleep(0)
                return await super().call_action(action, **params)

        bot = SlowOneBot({"remark": "QQ备注", "nickname": "QQ昵称"})
        resolver = ContactNameResolver(None, {"relationship_aliases": []})
        event = Event(bot=bot)

        first, second = await asyncio.gather(
            resolver.resolve_event_sender(event),
            resolver.resolve_event_sender(event),
        )

        self.assertEqual(first, "QQ备注")
        self.assertEqual(second, "QQ备注")
        self.assertEqual(len(bot.calls), 1)

    async def test_tool_context_event_uses_nested_sender_name(self):
        resolver = ContactNameResolver(None, {"relationship_aliases": []})
        event = Event(sender_id="10000001", sender_name="小林", unified_msg_origin="aiocqhttp:FriendMessage:10000001")
        tool_context = types.SimpleNamespace(context=types.SimpleNamespace(event=event))

        name = await resolver.resolve_event_sender(tool_context)

        self.assertEqual(name, "小林")

    async def test_weixin_oc_uses_local_alias_before_persona_name(self):
        context = types.SimpleNamespace(persona_manager=PersonaManager("人格称呼"))
        resolver = ContactNameResolver(
            context,
            {"relationship_aliases": ["o9test@im.wechat:本地微信称呼"]},
        )
        event = Event(
            sender_id="o9test@im.wechat",
            sender_name="o9test@im.wechat",
            platform_name="weixin_oc",
            unified_msg_origin="weixin_oc:FriendMessage:o9test@im.wechat",
        )

        name = await resolver.resolve_event_sender(event)

        self.assertEqual(name, "本地微信称呼")

    async def test_weixin_oc_falls_back_to_persona_name_not_openid(self):
        context = types.SimpleNamespace(persona_manager=PersonaManager("人格称呼"))
        resolver = ContactNameResolver(context, {"relationship_aliases": []})
        event = Event(
            sender_id="o9test@im.wechat",
            sender_name="o9test@im.wechat",
            platform_name="weixin_oc",
            unified_msg_origin="weixin_oc:FriendMessage:o9test@im.wechat",
        )

        name = await resolver.resolve_event_sender(event)

        self.assertEqual(name, "人格称呼")

    async def test_onebot_group_name_can_be_resolved(self):
        bot = ActionBot({"get_group_info": {"group_name": "看展小群"}})
        resolver = ContactNameResolver(None, {"relationship_aliases": []})
        event = Event(
            bot=bot,
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
        )

        name = await resolver.resolve_group_name("20001", event=event)

        self.assertEqual(name, "看展小群")
        self.assertEqual(bot.calls[0], ("get_group_info", {"group_id": 20001}))
