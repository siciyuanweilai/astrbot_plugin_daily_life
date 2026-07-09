import datetime
import json
import types
import unittest

from support import (
    ActionBot,
    ContactNameResolver,
    ConversationManager,
    DirectActionBot,
    PlatformHistoryManager,
    PlatformInstance,
    PlatformManager,
    PersonaManager,
    make_composer,
)


class LifeHistoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_deep_history_supports_direct_bot_call_action(self):
        now_ts = int(datetime.datetime.now().timestamp())
        bot = DirectActionBot(
            {
                "messages": [
                    {
                        "message_id": 1,
                        "message_seq": 10,
                        "time": now_ts,
                        "sender": {"user_id": 123456, "nickname": "朋友"},
                        "raw_message": "今晚一起看电影吗",
                    }
                ]
            }
        )
        composer, *_ = make_composer()
        composer.context.platform_manager = PlatformManager(bot)

        messages = await composer._fetch_deep_history(123456, is_group=False, hours=1, max_count=10)

        self.assertEqual(len(messages), 1)
        self.assertEqual(bot.calls[0][0], "get_friend_msg_history")

    async def test_fetch_deep_history_skips_non_onebot_platforms(self):
        now_ts = int(datetime.datetime.now().timestamp())
        non_onebot = DirectActionBot({"messages": []})
        onebot = DirectActionBot(
            {
                "messages": [
                    {
                        "message_id": 1,
                        "message_seq": 10,
                        "time": now_ts,
                        "sender": {"user_id": 123456, "nickname": "朋友"},
                        "raw_message": "后面的 OneBot 才应该被调用",
                    }
                ]
            }
        )
        composer, *_ = make_composer()
        composer.context.platform_manager = PlatformManager(
            instances=[
                PlatformInstance(non_onebot, platform_id="webchat", platform_type="webchat"),
                PlatformInstance(onebot, platform_id="qq", platform_type="aiocqhttp"),
            ]
        )

        messages = await composer._fetch_deep_history(123456, is_group=False, hours=1, max_count=10)

        self.assertEqual(len(messages), 1)
        self.assertEqual(non_onebot.calls, [])
        self.assertEqual(onebot.calls[0][0], "get_friend_msg_history")

    async def test_reference_private_chat_uses_contact_alias_first(self):
        now_ts = int(datetime.datetime.now().timestamp())
        bot = ActionBot(
            {
                "get_friend_msg_history": {
                    "messages": [
                        {
                            "message_id": 1,
                            "message_seq": 10,
                            "time": now_ts,
                            "sender": {"user_id": 123456, "nickname": "QQ昵称", "card": "群名片"},
                            "raw_message": "今晚一起看电影吗",
                        }
                    ]
                },
                "get_stranger_info": {"remark": "QQ备注", "nickname": "QQ昵称"},
            }
        )
        composer, *_ = make_composer()
        composer.context.platform_manager = PlatformManager(bot)
        composer.contact_resolver = ContactNameResolver(
            composer.context,
            {"relationship_aliases": ["123456:本地称呼"]},
        )

        text = await composer._get_recent_chats("123456", is_group=False, hours=1, max_count=10)

        self.assertIn("本地称呼: 今晚一起看电影吗", text)
        self.assertNotIn("QQ备注:", text)
        self.assertEqual([call[0] for call in bot.calls], ["get_friend_msg_history"])

    async def test_reference_private_chat_includes_persona_hint(self):
        now_ts = int(datetime.datetime.now().timestamp())
        bot = ActionBot(
            {
                "get_friend_msg_history": {
                    "messages": [
                        {
                            "message_id": 1,
                            "message_seq": 10,
                            "time": now_ts,
                            "sender": {"user_id": 123456, "nickname": "QQ昵称"},
                            "raw_message": "周末打球吗",
                        }
                    ]
                }
            }
        )
        composer, *_ = make_composer(
            persona_manager=PersonaManager(prompt="我是女生。阿林是我的男死党，性格很爽朗，经常约我打球。")
        )
        composer.context.platform_manager = PlatformManager(bot)
        composer.contact_resolver = ContactNameResolver(
            composer.context,
            {"relationship_aliases": ["123456:阿林"]},
        )

        text = await composer._get_recent_chats(
            "123456",
            is_group=False,
            hours=1,
            max_count=10,
            persona=await composer._get_persona(),
        )

        self.assertIn("【参考对象人设线索】称呼：阿林；人设线索：我是女生。 阿林是我的男死党，性格很爽朗，经常约我打球。", text)
        self.assertIn("阿林: 周末打球吗", text)

    async def test_reference_private_chat_falls_back_to_onebot_remark_then_nickname(self):
        now_ts = int(datetime.datetime.now().timestamp())
        bot = ActionBot(
            {
                "get_friend_msg_history": {
                    "messages": [
                        {
                            "message_id": 1,
                            "message_seq": 10,
                            "time": now_ts,
                            "sender": {"user_id": 123456, "nickname": "消息昵称"},
                            "raw_message": "明天去书店吗",
                        }
                    ]
                },
                "get_stranger_info": {"remark": "QQ备注", "nickname": "QQ昵称"},
            }
        )
        composer, *_ = make_composer()
        composer.context.platform_manager = PlatformManager(bot)
        composer.contact_resolver = ContactNameResolver(composer.context, {"relationship_aliases": []})

        text = await composer._get_recent_chats("123456", is_group=False, hours=1, max_count=10)

        self.assertIn("QQ备注: 明天去书店吗", text)
        self.assertNotIn("QQ昵称:", text)
        self.assertEqual(
            [call[0] for call in bot.calls],
            ["get_friend_msg_history", "get_stranger_info"],
        )

    async def test_reference_private_chat_skips_when_no_reliable_name(self):
        now_ts = int(datetime.datetime.now().timestamp())
        bot = ActionBot(
            {
                "get_friend_msg_history": {
                    "messages": [
                        {
                            "message_id": 1,
                            "message_seq": 10,
                            "time": now_ts,
                            "sender": {"user_id": 123456, "nickname": "消息昵称"},
                            "raw_message": "这条不应进入日程参考",
                        }
                    ]
                },
                "get_stranger_info": {"remark": "", "nickname": ""},
            }
        )
        composer, *_ = make_composer()
        composer.context.platform_manager = PlatformManager(bot)
        composer.contact_resolver = ContactNameResolver(composer.context, {"relationship_aliases": []})

        text = await composer._get_recent_chats("123456", is_group=False, hours=1, max_count=10)

        self.assertEqual(text, "无")
        self.assertNotIn("消息昵称", text)

    async def test_weixin_oc_reference_reads_saved_platform_history(self):
        onebot = ActionBot({"get_friend_msg_history": {"messages": []}})
        records = [
            types.SimpleNamespace(
                content={"type": "user", "message": [{"type": "text", "data": {"text": "明天去看展吧"}}]},
                sender_id="o9test@im.wechat",
                sender_name="",
                created_at=datetime.datetime.now(),
            ),
            types.SimpleNamespace(
                content={"type": "bot", "message": [{"type": "plain", "text": "好呀，我把下午空出来"}]},
                sender_id="bot",
                sender_name="",
                created_at=datetime.datetime.now(),
            ),
        ]
        composer, *_ = make_composer()
        composer.context.platform_manager = PlatformManager(
            instances=[
                PlatformInstance(object(), platform_id="weixin_oc", platform_type="weixin_oc"),
                PlatformInstance(onebot, platform_id="qq", platform_type="aiocqhttp"),
            ]
        )
        composer.context.message_history_manager = PlatformHistoryManager(
            {("weixin_oc", "o9test@im.wechat"): records}
        )
        composer.contact_resolver = ContactNameResolver(
            composer.context,
            {"relationship_aliases": ["o9test@im.wechat:微信朋友"]},
        )

        text = await composer._get_recent_chats(
            "weixin_oc:FriendMessage:o9test@im.wechat",
            is_group=False,
            hours=24,
            max_count=10,
        )

        self.assertIn("微信朋友: 明天去看展吧", text)
        self.assertIn("我: 好呀，我把下午空出来", text)
        self.assertEqual(onebot.calls, [])

    async def test_weixin_oc_reference_includes_persona_hint(self):
        records = [
            types.SimpleNamespace(
                content={"type": "user", "message": [{"type": "text", "data": {"text": "周末打球吗"}}]},
                sender_id="o9test@im.wechat",
                sender_name="",
                created_at=datetime.datetime.now(),
            )
        ]
        composer, *_ = make_composer(
            persona_manager=PersonaManager(prompt="我是女生。阿林是我的男死党，性格很爽朗，经常约我打球。")
        )
        composer.context.platform_manager = PlatformManager(
            instances=[PlatformInstance(object(), platform_id="weixin_oc", platform_type="weixin_oc")]
        )
        composer.context.message_history_manager = PlatformHistoryManager(
            {("weixin_oc", "o9test@im.wechat"): records}
        )
        composer.contact_resolver = ContactNameResolver(
            composer.context,
            {"relationship_aliases": ["o9test@im.wechat:阿林"]},
        )

        text = await composer._get_recent_chats(
            "weixin_oc:FriendMessage:o9test@im.wechat",
            is_group=False,
            hours=24,
            max_count=10,
            persona=await composer._get_persona(),
        )

        self.assertIn("【参考对象人设线索】称呼：阿林；人设线索：我是女生。 阿林是我的男死党，性格很爽朗，经常约我打球。", text)
        self.assertIn("阿林: 周末打球吗", text)

    async def test_weixin_oc_bare_openid_uses_loaded_adapter_id(self):
        records = [
            types.SimpleNamespace(
                content={"type": "user", "message": [{"type": "plain", "text": "午后想去买甜点"}]},
                sender_id="o9test@im.wechat",
                sender_name="",
                created_at=datetime.datetime.now(),
            )
        ]
        history_manager = PlatformHistoryManager({("wx-main", "o9test@im.wechat"): records})
        composer, *_ = make_composer()
        composer.context.platform_manager = PlatformManager(
            instances=[PlatformInstance(object(), platform_id="wx-main", platform_type="weixin_oc")]
        )
        composer.context.message_history_manager = history_manager
        composer.contact_resolver = ContactNameResolver(
            composer.context,
            {"relationship_aliases": ["o9test@im.wechat:微信朋友"]},
        )

        text = await composer._get_recent_chats(
            "o9test@im.wechat",
            is_group=False,
            hours=24,
            max_count=10,
        )

        self.assertIn("微信朋友: 午后想去买甜点", text)
        self.assertEqual(history_manager.calls[0], ("wx-main", "o9test@im.wechat", 1, 10))

    async def test_weixin_oc_reference_falls_back_to_conversation_history(self):
        target_umo = "weixin_oc:FriendMessage:o9test@im.wechat"
        conversation = types.SimpleNamespace(
            history=json.dumps(
                [
                    {"role": "user", "content": [{"type": "text", "data": {"text": "晚上一起散步吗"}}]},
                    {"role": "assistant", "content": "可以呀，晚饭后见"},
                ],
                ensure_ascii=False,
            )
        )
        composer, *_ = make_composer()
        composer.context.message_history_manager = PlatformHistoryManager({})
        composer.context.conversation_manager = ConversationManager({target_umo: conversation})
        composer.contact_resolver = ContactNameResolver(
            composer.context,
            {"relationship_aliases": ["o9test@im.wechat:微信朋友"]},
        )

        text = await composer._get_recent_chats(
            target_umo,
            is_group=False,
            hours=24,
            max_count=10,
        )

        self.assertIn("微信朋友: 晚上一起散步吗", text)
        self.assertIn("我: 可以呀，晚饭后见", text)

    async def test_weixin_oc_group_reference_is_ignored(self):
        history_manager = PlatformHistoryManager(
            {("weixin_oc", "o9test@im.wechat"): [types.SimpleNamespace(content="不应读取")]}
        )
        composer, *_ = make_composer()
        composer.context.message_history_manager = history_manager

        text = await composer._get_recent_chats(
            "weixin_oc:GroupMessage:o9test@im.wechat",
            is_group=True,
            hours=24,
            max_count=10,
        )

        self.assertEqual(text, "无")
        self.assertEqual(history_manager.calls, [])
