import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import support  # noqa: F401
from support import Event
from componentfixtures import CaptureEventComponent, ProactiveContextComponent, StructuredContextComponent
from core.runtime.capture.batch import ChatMemoryBatchComponent
from core.runtime.elapsed import ConversationTimingService
from core.runtime.outbound import OutboundLogService
from core.runtime.proactive.procontext import ProactiveSyntheticEvent
from core.runtime.proactive.scope import ProactiveScopeComponent
from core.runtime.voicecall.manager import VoiceCallManager
from core.runtime.voicecall.videocall import RtcVoiceCallManager
from core.sources.botidentity import event_bot_id, get_qq_official_client, get_weixin_oc_instance


def official_event(bot=None, *, self_id="unknown_selfid", platform="qq_official", instance="official"):
    return Event(bot=bot, self_id=self_id, platform_name=platform,
                 unified_msg_origin=f"{instance}:FriendMessage:user")


def official_bot(name="官方昵称", account="5732691059880655589"):
    return SimpleNamespace(
        robot=SimpleNamespace(name=name, id=int(account), avatar="https://avatar.example.test/official.png"),
        api=SimpleNamespace(me=AsyncMock()),
        call_action=AsyncMock(),
    )


def platform_context(*clients):
    instances = [SimpleNamespace(id=key, name="qq_official", get_client=lambda bot=bot: bot)
                 for key, bot in clients]
    return SimpleNamespace(platform_manager=SimpleNamespace(get_insts=lambda: instances))


def weixin_event(account="test@im.bot"):
    event = Event(self_id="角色-WeiXin", platform_name="weixin_oc",
                  unified_msg_origin="角色-WeiXin:FriendMessage:user@im.wechat")
    event.platform = SimpleNamespace(id="角色-WeiXin", name="weixin_oc", account_id=account)
    return event


def outbound_service(context=None):
    def close_unscheduled(coro, **kwargs):
        coro.close()
        return False

    return OutboundLogService(
        context=context,
        timing=ConversationTimingService(
            archive=lambda: None, is_self_message=lambda event: False,
            session_id=lambda event: event.unified_msg_origin, is_group=lambda event: False,
        ),
        schedule=close_unscheduled,
    )


class StructuredRuntime(CaptureEventComponent, StructuredContextComponent, ProactiveContextComponent):
    def _proactive_is_self_message(self, event):
        return False


class BotIdentityTest(unittest.IsolatedAsyncioTestCase):
    async def test_official_log_uses_sdk_profile_without_network_lookup(self):
        bot = official_bot()
        event = official_event(bot)
        service = outbound_service()
        with patch("core.runtime.outbound.logger.info") as info:
            await service.log_outbound_message_async(["测试正文"], source_event=event)
        self.assertEqual(info.call_args.args[0], "[日常生活] 官方昵称/5732691059880655589: 测试正文")
        bot.api.me.assert_not_awaited()
        bot.call_action.assert_not_awaited()

    async def test_webhook_without_robot_property_queries_me_once(self):
        class WebhookBot:
            @property
            def robot(self):
                raise AttributeError("websocket state unavailable")

        bot = WebhookBot()
        bot.api = SimpleNamespace(me=AsyncMock(return_value={"username": "官方昵称", "id": "123"}))
        event = official_event(bot, platform="qq_official_webhook")
        service = outbound_service()
        for _ in range(2):
            self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "官方昵称/123")
        bot.api.me.assert_awaited_once_with()

    async def test_official_lookup_recovers_after_timeout_without_caching_placeholder(self):
        bot = SimpleNamespace(api=SimpleNamespace(me=AsyncMock(side_effect=[
            TimeoutError(), {"username": "恢复昵称", "id": "123"},
        ])))
        event = official_event(bot)
        service = outbound_service()
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "机器人")
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "恢复昵称/123")
        self.assertEqual(bot.api.me.await_count, 2)

    async def test_incomplete_official_response_is_retried(self):
        bot = SimpleNamespace(api=SimpleNamespace(me=AsyncMock(side_effect=[
            {"username": "已知昵称", "id": "unknown_selfid"},
            {"username": "已知昵称", "id": "123"},
        ])))
        service = outbound_service()
        event = official_event(bot)
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "已知昵称")
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "已知昵称/123")

    async def test_proactive_delivery_uses_exact_official_instance(self):
        wrong, target = official_bot("其他账号", "111"), official_bot("目标账号", "222")
        service = outbound_service(platform_context(("other", wrong), ("target", target)))
        event = ProactiveSyntheticEvent(message="测试", target_scope="target:FriendMessage:user",
                                       platform_name="target")
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "目标账号/222")
        wrong.api.me.assert_not_awaited()
        wrong.call_action.assert_not_awaited()

    async def test_replaced_official_client_refreshes_identity(self):
        service = outbound_service()
        event = official_event(official_bot("原昵称", "111"))
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "原昵称/111")
        event.bot = official_bot("新昵称", "222")
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "新昵称/222")

    async def test_weixin_log_uses_connection_label_and_login_account(self):
        event = weixin_event()
        service = outbound_service()
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "角色-WeiXin/test@im.bot")
        self.assertEqual(event_bot_id(event), "test@im.bot")
        event.platform.account_id = "changed@im.bot"
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "角色-WeiXin/changed@im.bot")
        event.platform.account_id = None
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "角色-WeiXin")
        self.assertEqual(event_bot_id(event), "")

    async def test_weixin_proactive_delivery_selects_matching_instance(self):
        target = weixin_event().platform
        other = SimpleNamespace(id="other", name="weixin_oc", account_id="other@im.bot")
        context = SimpleNamespace(platform_manager=SimpleNamespace(get_insts=lambda: [other, target]))
        service = outbound_service(context)
        event = ProactiveSyntheticEvent(message="测试", target_scope="角色-WeiXin:FriendMessage:user@im.wechat",
                                       platform_name="角色-WeiXin")
        self.assertEqual(await service.prepare_outbound_bot_name(source_event=event), "角色-WeiXin/test@im.bot")
        self.assertIsNone(get_weixin_oc_instance(context, scope="missing:FriendMessage:user"))
        self.assertIsNone(get_weixin_oc_instance(context, event=official_event(), scope=event.unified_msg_origin))
        self.assertIsNone(get_weixin_oc_instance(context, event=weixin_event(), scope="other:FriendMessage:user"))

    def test_weixin_without_login_never_stores_connection_label_as_id(self):
        event = weixin_event(account=None)
        event.message_obj.self_id = event.get_self_id()
        self.assertEqual(event_bot_id(event), "")
        del event.platform
        self.assertEqual(event_bot_id(event), "")

    def test_weixin_structured_history_uses_login_account(self):
        event = weixin_event()
        result = StructuredRuntime().note_structured_bot_message(
            event.unified_msg_origin, "测试回复", source_event=event)
        self.assertEqual(result.sender_id, "test@im.bot")

    def test_placeholder_ids_use_cached_profile_in_wrapped_events(self):
        for placeholder in ("unknown_selfid", "qq_official", "qq_official_webhook", ""):
            with self.subTest(placeholder=placeholder):
                event = official_event(official_bot(account="123"), self_id=placeholder)
                wrapped = SimpleNamespace(context=SimpleNamespace(event=event))
                self.assertEqual(event_bot_id(wrapped), "123")

    def test_genuine_event_id_is_preserved(self):
        event = official_event(official_bot(account="123"), self_id="event-id")
        self.assertEqual(event_bot_id(event), "event-id")

    def test_placeholder_without_profile_is_empty(self):
        event = official_event()
        event.message_obj.self_id = "unknown_selfid"
        self.assertEqual(event_bot_id(event), "")

    def test_resolution_never_borrows_other_platform_or_mismatched_instance(self):
        bot = official_bot(account="123")
        context = platform_context(("official", bot))
        self.assertIsNone(get_qq_official_client(context, scope="missing:FriendMessage:user"))
        self.assertIsNone(get_qq_official_client(context, event=Event(), scope="official:FriendMessage:user"))
        self.assertIsNone(get_qq_official_client(
            context, event=official_event(bot), scope="other:FriendMessage:user"))

    async def test_voice_and_video_profiles_use_official_name_and_avatar(self):
        event = official_event(official_bot())
        for manager_type in (VoiceCallManager, RtcVoiceCallManager):
            with self.subTest(manager=manager_type.__name__):
                manager = manager_type.__new__(manager_type)
                manager.runtime = SimpleNamespace(prepare_outbound_bot_name=AsyncMock(return_value="机器人"))
                self.assertEqual(await manager._event_bot_profile(event, event.unified_msg_origin),
                                 ("官方昵称", "https://avatar.example.test/official.png"))

    def test_structured_history_records_actual_bot_id(self):
        runtime = StructuredRuntime()
        event = official_event(official_bot(account="123"))
        result = runtime.note_structured_bot_message(event.unified_msg_origin, "测试回复", source_event=event)
        self.assertEqual(result.sender_id, "123")
        self.assertTrue(result.is_bot)

    def test_structured_history_does_not_store_placeholder_when_profile_missing(self):
        runtime = StructuredRuntime()
        event = official_event()
        result = runtime.note_structured_bot_message(event.unified_msg_origin, "测试回复", source_event=event)
        self.assertEqual(result.sender_id, "bot")

    def test_self_message_detection_uses_actual_official_id(self):
        component = ProactiveScopeComponent(SimpleNamespace(
            _safe_event_call=lambda event, method: getattr(event, method)(),
        ))
        event = official_event(official_bot(account="123"))
        event._sender_id = "123"
        self.assertTrue(component._proactive_is_self_message(event))
        event._sender_id = "user"
        self.assertFalse(component._proactive_is_self_message(event))

    async def test_reply_memory_records_actual_id_or_neutral_fallback(self):
        cases = (
            (official_event(official_bot(account="123")), "123"),
            (official_event(), "bot"),
            (weixin_event(), "test@im.bot"),
            (weixin_event(account=None), "bot"),
        )
        for event, expected in cases:
            with self.subTest(expected=expected):
                archive = SimpleNamespace(enqueue_chat_memory_message=AsyncMock(return_value=(1, False)))
                runtime = SimpleNamespace(
                    config=SimpleNamespace(memory=SimpleNamespace(enabled=True)), archive=archive,
                    contact_resolver=SimpleNamespace(resolve_event_sender=AsyncMock(return_value="用户")),
                    _event_has_command_handler=lambda event: False,
                    event_was_recalled=lambda event, **kwargs: False,
                    _structured_result_text=lambda event: ("回复内容", ""),
                    _event_context_meta=AsyncMock(return_value={"session_id": event.unified_msg_origin}),
                    format_structured_message_context=lambda scope, **kwargs: "",
                )
                await ChatMemoryBatchComponent(runtime).capture_chat_memory_bot_reply(event)
                snapshot = archive.enqueue_chat_memory_message.call_args.args[0]
                self.assertEqual(snapshot["sender_profile_id"], expected)
                self.assertEqual(snapshot["user_id"], expected)
                self.assertEqual(snapshot["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
