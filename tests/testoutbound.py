import asyncio
import functools
import types
import unittest
from unittest.mock import AsyncMock, patch

import support  # noqa: F401
from core.runtime.elapsed import ConversationTimingService
from core.runtime.outbound import OutboundLogService, message_outline
from support import Event
from core.runtime.proactive.procontext import ProactiveSyntheticEvent


def new_service():
    def close_unscheduled(coro, **kwargs):
        coro.close()
        return False

    return OutboundLogService(
        context=None,
        timing=ConversationTimingService(
            archive=lambda: None,
            is_self_message=lambda event: False,
            session_id=lambda event: event.unified_msg_origin,
            is_group=lambda event: False,
        ),
        schedule=close_unscheduled,
    )


class OutboundLogTest(unittest.TestCase):
    def test_framework_reply_records_receipt_without_repeating_body(self):
        runtime = new_service()
        event = Event()
        event.set_result(event.chain_result(["普通聊天回复"]))
        with (
            patch("core.runtime.outbound.logger.info") as info,
            patch("core.runtime.outbound.logger.debug") as debug,
            patch.object(runtime._timing, "note_conversation_sent") as note,
        ):
            self.assertTrue(runtime.log_outbound_result(event))

        info.assert_not_called()
        debug.assert_called_once_with("[日常生活] 普通回复发送回执已记录。")
        note.assert_called_once()
        self.assertFalse(runtime.log_outbound_result(event))

    def test_message_outline_keeps_text_and_labels_components(self):
        chain = types.SimpleNamespace(
            chain=[
                "先看这张图",
                {"type": "image", "file": "/tmp/private.png"},
                types.SimpleNamespace(type="record", file="/tmp/private.wav"),
            ]
        )

        self.assertEqual(message_outline(chain), "先看这张图[图片][语音]")

    def test_log_outbound_message_uses_unified_bot_format(self):
        runtime = new_service()
        event = Event(sender_name="测试用户", sender_id="test-user")
        chain = types.SimpleNamespace(chain=[types.SimpleNamespace(text="完整回复")])

        with patch("core.runtime.outbound.logger.info") as info:
            self.assertTrue(
                runtime.log_outbound_message(
                    chain,
                    scope=event.unified_msg_origin,
                    source_event=event,
                    source="respond",
                )
            )

        self.assertEqual(info.call_args.args[0], "[日常生活] 机器人: 完整回复")
        self.assertNotIn("测试用户", info.call_args.args[0])
        self.assertNotIn("test-user", info.call_args.args[0])
        self.assertEqual(info.call_args.kwargs["extra"]["category"], "user_chat")

    def test_async_outbound_log_uses_bot_instance_name_without_user_identity(self):
        runtime = new_service()
        event = Event(sender_name="测试用户", sender_id="test-user", self_id="test-bot")
        event.get_self_name = lambda: "测试机器人"
        chain = types.SimpleNamespace(chain=["完整回复"])

        with patch("core.runtime.outbound.logger.info") as info:
            self.assertTrue(
                asyncio.run(
                    runtime.log_outbound_message_async(
                        chain,
                        scope=event.unified_msg_origin,
                        source_event=event,
                        source="respond",
                    )
                )
            )

        self.assertEqual(info.call_args.args[0], "[日常生活] 测试机器人/test-bot: 完整回复")
        self.assertNotIn("测试用户", info.call_args.args[0])
        self.assertNotIn("test-user", info.call_args.args[0])

    def test_async_outbound_log_ignores_dynamic_api_callable_as_nickname(self):
        runtime = new_service()

        class Bot:
            nickname = functools.partial(lambda: "", "nickname")

            async def call_action(self, action, **params):
                self.last_call = (action, params)
                return {"data": {"nickname": "测试机器人", "user_id": 188852752}}

        event = Event(
            bot=Bot(),
            sender_name="测试用户",
            sender_id="test-user",
            self_id="",
        )
        chain = types.SimpleNamespace(chain=["完整回复"])

        with patch("core.runtime.outbound.logger.info") as info:
            self.assertTrue(
                asyncio.run(
                    runtime.log_outbound_message_async(
                        chain,
                        scope=event.unified_msg_origin,
                        source_event=event,
                        source="respond",
                    )
                )
            )

        self.assertEqual(info.call_args.args[0], "[日常生活] 测试机器人/188852752: 完整回复")

    def test_direct_body_is_deduplicated_from_final_result(self):
        runtime = new_service()
        event = Event(sender_name="测试用户", sender_id="test-user")
        event.set_result(event.chain_result(["已经发给你啦。"]))

        self.assertTrue(
            runtime.log_outbound_message(
                event.get_result().chain,
                scope=event.unified_msg_origin,
                source_event=event,
                source="preface",
            )
        )
        event.set_result(event.chain_result(["已经发给你啦。"]))
        self.assertFalse(runtime.log_outbound_result(event))


class OutboundIdentityTest(unittest.IsolatedAsyncioTestCase):
    def service(self, *instances):
        service = new_service()
        service._context = types.SimpleNamespace(platform_manager=types.SimpleNamespace(
            get_insts=lambda: list(instances)))
        return service

    def instance(self, platform_id, name, account, protocol="aiocqhttp"):
        bot = types.SimpleNamespace(call_action=AsyncMock(return_value={
            "data": {"nickname": name, "user_id": account}}))
        return types.SimpleNamespace(id=platform_id, name=protocol, get_client=lambda: bot), bot

    async def test_private_revisit_uses_target_instance_for_every_segment(self):
        other, wrong_bot = self.instance("another", "另一个机器人", "222")
        target, bot = self.instance("生活-OneBot", "生活角色", "111")
        service = self.service(other, target)
        event = ProactiveSyntheticEvent(message="回访", target_scope="生活-OneBot:FriendMessage:333",
                                       platform_name="生活-OneBot", sender_id="333", sender_name="用户")
        with patch("core.runtime.outbound.logger.info") as info:
            for text in ("第一段", "第二段"):
                await service.log_outbound_message_async([text], source_event=event, source="private_revisit")
        self.assertEqual([c.args[0] for c in info.call_args_list],
                         ["[日常生活] 生活角色/111: 第一段", "[日常生活] 生活角色/111: 第二段"])
        bot.call_action.assert_awaited_once_with("get_login_info")
        wrong_bot.call_action.assert_not_awaited()

    async def test_lookup_failure_does_not_poison_later_logs(self):
        target, bot = self.instance("one", "角色", "111")
        service = self.service(target)
        bot.call_action.side_effect = [TimeoutError("暂时不可用"), {"nickname": "角色", "user_id": "111"}]
        self.assertEqual(await service.prepare_outbound_bot_name(scope="one:FriendMessage:333"), "机器人")
        self.assertEqual(await service.prepare_outbound_bot_name(scope="one:FriendMessage:333"), "角色/111")
        self.assertEqual(bot.call_action.await_count, 2)

    async def test_replaced_platform_client_refreshes_identity(self):
        target, old = self.instance("one", "旧角色", "111")
        service = self.service(target)
        self.assertEqual(await service.prepare_outbound_bot_name(scope="one:FriendMessage:333"), "旧角色/111")
        _, new = self.instance("one", "新角色", "444")
        target.get_client = lambda: new
        self.assertEqual(await service.prepare_outbound_bot_name(scope="one:FriendMessage:333"), "新角色/444")
        new.call_action.assert_awaited_once()

    async def test_synthetic_non_onebot_target_never_uses_other_account(self):
        other, wrong = self.instance("one", "QQ角色", "111")
        target, official = self.instance("official", "官方角色", "222", protocol="qq_official")
        service = self.service(other, target)
        event = ProactiveSyntheticEvent(message="回访", target_scope="official:FriendMessage:333",
                                       platform_name="official")
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "机器人")
        wrong.call_action.assert_not_awaited()
        official.call_action.assert_not_awaited()

    async def test_genuine_non_onebot_event_remains_protected(self):
        instance, bot = self.instance("one", "QQ角色", "111")
        service = self.service(instance)
        event = Event(self_id="official-id")
        event.get_platform_name = lambda: "qq_official"
        self.assertEqual(await service.prepare_outbound_bot_name(scope="one:FriendMessage:333", source_event=event),
                         "机器人/official-id")
        bot.call_action.assert_not_awaited()

    async def test_known_name_is_preserved_when_api_only_supplies_id(self):
        instance, bot = self.instance("one", "", "111")
        service = self.service(instance)
        event = Event(bot=bot, self_id="")
        event.get_self_name = lambda: "已知昵称"
        self.assertEqual(await service.prepare_outbound_bot_name(scope="one:FriendMessage:333", source_event=event),
                         "已知昵称/111")

    async def test_incomplete_success_can_be_completed_on_next_attempt(self):
        instance, bot = self.instance("one", "", "111")
        service = self.service(instance)
        self.assertEqual(await service.prepare_outbound_bot_name(scope="one:FriendMessage:333"), "机器人/111")
        bot.call_action.return_value = {"data": {"nickname": "恢复昵称", "user_id": "111"}}
        self.assertEqual(await service.prepare_outbound_bot_name(scope="one:FriendMessage:333"), "恢复昵称/111")

if __name__ == "__main__":
    unittest.main()
