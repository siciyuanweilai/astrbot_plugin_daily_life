import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.config.options import LifeSettings
from core.runtime.voicecall.manager import (
    VoiceCallInvite,
    VoiceCallManager,
    VoiceCallTranscriptTurn,
)
from core.runtime.voicecall.voicegateway import (
    _VoiceCallBridge,
    _decode_o20_frame,
    _encode_o20_frame,
    _transcript_page_html,
    voice_gateway_start_error_detail,
)
from core.runtime.voicecall import voicegateway as voicegateway_module
from core.runtime.voicecall.web import VOICE_CALL_PAGE
from core.runtime.voicecall.toolbridge import (
    VoiceCallToolBridge,
    VoiceCallToolEvent,
    _chain_text,
)


class _Event:
    unified_msg_origin = "aiocqhttp:private:scope"

    @staticmethod
    def get_sender_id():
        return "user"

    @staticmethod
    def get_sender_name():
        return "测试用户"


class _Runtime:
    def __init__(self, config):
        self.config = config

    async def get_share_context(self, _scope):
        return {"season": "夏季", "recent": ["喝水"]}

    async def get_persona_text(self, _scope):
        return "说话轻快、熟悉用户，偶尔嘴硬但会照顾对方。"


class _NestedSender:
    user_id = "nested-user"
    nickname = "嵌套用户"


class _NestedMessage:
    sender = _NestedSender()


class _NestedEvent:
    message_obj = _NestedMessage()

    @staticmethod
    def get_platform_name():
        return "test-platform"

    @staticmethod
    def get_message_type():
        return "private"


class _OneBotAvatarEvent:
    unified_msg_origin = "aiocqhttp:private:scope"

    @staticmethod
    def get_sender_id():
        return "10001"

    @staticmethod
    def get_sender_name():
        return "通话用户"

    @staticmethod
    def get_self_id():
        return "20002"

    @staticmethod
    def get_self_name():
        return "通话机器人"


class VoiceCallSettingsTest(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_init_error_preserves_detail_for_retry(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "api_key": "api-key",
                    "speaker_id": "speaker",
                },
                "realtime_voice_call_config": {"enabled": True},
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        invite = VoiceCallInvite(
            "id",
            "scope",
            "u",
            "用户",
            "context",
            "",
            time.time(),
            time.time() + 120,
            accepted=True,
            state="connecting",
        )
        browser = type(
            "Browser",
            (),
            {"send_json": AsyncMock(), "close": AsyncMock()},
        )()
        bridge = _VoiceCallBridge(
            type("Gateway", (), {"manager": manager})(), browser, invite, "token"
        )

        class Upstream:
            async def receive(self, timeout):
                return type(
                    "Message",
                    (),
                    {
                        # aiohttp.WSMsgType.TEXT 的数值，避免测试本身依赖宿主环境。
                        "type": 1,
                        "data": json.dumps(
                            {
                                "type": "error",
                                "error": {
                                    "code": "invalid_session",
                                    "message": "音色不可用",
                                },
                            },
                            ensure_ascii=False,
                        ),
                    },
                )()

        bridge.upstream = Upstream()
        ws_types = SimpleNamespace(
            BINARY=2,
            CLOSE=8,
            CLOSING=256,
            CLOSED=257,
            ERROR=258,
            TEXT=1,
        )
        with unittest.mock.patch.object(
            voicegateway_module.aiohttp, "WSMsgType", ws_types, create=True
        ):
            with self.assertRaisesRegex(RuntimeError, "音色不可用"):
                await bridge._wait_for_session_ready()

        self.assertEqual(invite.end_reason, "上游服务错误：音色不可用")
        browser.send_json.assert_awaited_once()
        await manager.close()

    def test_retryable_upstream_status_is_not_terminal_in_voice_page(self):
        self.assertIn("payload.retryable === true", VOICE_CALL_PAGE)
        self.assertIn("正在恢复|恢复中", VOICE_CALL_PAGE)

    async def test_voice_call_end_control_is_available_without_external_tools(self):
        config = LifeSettings.from_dict({"realtime_voice_call_config": {}})
        runtime = _Runtime(config)
        manager = VoiceCallManager(runtime)

        schemas = manager.voice_tool_schemas(
            VoiceCallInvite("id", "scope", "u", "用户", "context", "", 0, 1)
        )

        self.assertEqual([item["name"] for item in schemas], ["life_voice_call_end"])
        self.assertIn("自然告别", schemas[0]["description"])
        await manager.close()

    async def test_voice_call_end_control_requests_the_current_bridge(self):
        config = LifeSettings.from_dict({"realtime_voice_call_config": {}})
        runtime = _Runtime(config)
        manager = VoiceCallManager(runtime)
        invite = VoiceCallInvite("id", "scope", "u", "用户", "context", "", 0, 1)
        invite.accepted = True
        invite.active = True
        invite.state = "active"

        class Bridge:
            reason = ""

            def request_hangup(self, reason):
                self.reason = reason

        bridge = Bridge()
        manager.attach_bridge(invite, bridge)
        result = await VoiceCallToolBridge(runtime, invite, manager=manager).call(
            "life_voice_call_end", {"reason": "用户已经道别"}
        )

        self.assertIn("已请求结束", result)
        self.assertEqual(bridge.reason, "用户已经道别")
        self.assertTrue(invite.hangup_requested)
        await manager.close()

    async def test_bot_hangup_waits_for_response_completion_before_closing(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite("id", "scope", "u", "用户", "context", "", 0, 1)
        invite.accepted = True
        invite.active = True
        invite.state = "active"
        browser = type(
            "Browser",
            (),
            {"send_json": AsyncMock(), "close": AsyncMock()},
        )()
        bridge = _VoiceCallBridge(
            type("Gateway", (), {"manager": manager})(), browser, invite, "token"
        )
        bridge.upstream = type("Upstream", (), {"closed": False, "send_str": AsyncMock()})()

        bridge.request_hangup("自然道别")
        watcher = asyncio.create_task(bridge._hangup_watch())
        await asyncio.sleep(0)
        self.assertEqual(browser.close.await_count, 0)
        bridge._response_finished.set()
        await asyncio.sleep(0)
        self.assertEqual(browser.close.await_count, 0)
        bridge._audio_playback_finished.set()
        await watcher

        self.assertEqual(invite.end_reason, "自然道别")
        browser.close.assert_awaited_once()
        self.assertIn(
            {"kind": "await_playback"},
            [call.args[0] for call in browser.send_json.await_args_list],
        )
        sent = [json.loads(call.args[0])["type"] for call in bridge.upstream.send_str.await_args_list]
        self.assertIn("session.close", sent)
        await manager.close()

    async def test_response_completion_is_reset_for_each_response_round(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite("id", "scope", "u", "用户", "context", "", 0, 1)
        browser = type("Browser", (), {"send_json": AsyncMock(), "close": AsyncMock()})()
        bridge = _VoiceCallBridge(
            type("Gateway", (), {"manager": manager})(), browser, invite, "token"
        )

        bridge._update_response_lifecycle({"type": "response.done"})
        self.assertTrue(bridge._response_finished.is_set())
        bridge._update_response_lifecycle({"type": "response.created"})
        self.assertFalse(bridge._response_finished.is_set())
        bridge._update_response_lifecycle({"type": "response.output_audio.started"})
        self.assertFalse(bridge._response_finished.is_set())
        bridge._update_response_lifecycle({"type": "response.completed"})
        self.assertTrue(bridge._response_finished.is_set())
        await manager.close()

    async def test_realtime_call_reuses_voice_settings_without_recording_options(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "api_key": "voice-api-key",
                    "speaker_id": "voice-speaker",
                    "speech_rate": 10,
                    "loudness_rate": -5,
                },
                "realtime_voice_call_config": {
                    "enabled": True,
                    "public_url": "https://voice.example.test",
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))

        self.assertFalse(hasattr(config.realtime_voice_call, "api_key"))
        self.assertFalse(hasattr(config.realtime_voice_call, "speaker_id"))
        self.assertEqual(manager.api_key, "voice-api-key")
        self.assertEqual(manager.speaker_id, "voice-speaker")
        await manager.close()

    async def test_invite_is_signed_single_use_and_context_is_compact(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "api_key": "api-key",
                    "speaker_id": "speaker",
                },
                "realtime_voice_call_config": {
                    "enabled": True,
                    "public_url": "https://voice.example.test",
                    "invite_expire_seconds": 30,
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        manager.gateway.start = AsyncMock()
        event = _Event()

        message = await manager.create_invite(event, greeting="你好")

        self.assertIn("https://voice.example.test/call/", message)
        token = message.rsplit("\n", 1)[-1].rsplit("/", 1)[-1]
        self.assertTrue(manager.peek_invite(token))
        self.assertIsNotNone(manager.pending_invite(token))
        invite = manager.claim_invite(token)
        self.assertIsNotNone(invite)
        self.assertFalse(manager.peek_invite(token))
        self.assertIsNone(manager.pending_invite(token))
        self.assertIsNone(manager.claim_invite(token))
        self.assertIn("实时语音通话", invite.context)
        self.assertIn("当前角色人设", invite.context)
        self.assertIn("说话轻快、熟悉用户", invite.context)
        self.assertIn("夏季", invite.context)
        self.assertIn("当前通话用户信息", invite.context)
        self.assertIn('"user_id":"user"', invite.context)
        self.assertIn('"nickname":"测试用户"', invite.context)
        manager.gateway.start.assert_awaited_once()
        await manager.close()

    async def test_event_identity_and_scope_fall_back_to_nested_sender(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))

        self.assertEqual(
            manager._event_identity(_NestedEvent()), ("nested-user", "嵌套用户")
        )
        self.assertEqual(
            manager._event_scope(_NestedEvent(), user_id="nested-user"),
            "test-platform:FriendMessage:nested-user",
        )

    async def test_call_page_profile_uses_platform_avatars_when_available(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        event = _OneBotAvatarEvent()

        self.assertEqual(
            manager._event_user_avatar_url(event, "10001"),
            "https://q.qlogo.cn/g?b=qq&nk=10001&s=100",
        )
        self.assertEqual(
            await manager._event_bot_profile(event, "aiocqhttp:private:scope"),
            ("通话机器人", "https://q.qlogo.cn/g?b=qq&nk=20002&s=100"),
        )
        invite = VoiceCallInvite(
            "id",
            "scope",
            "10001",
            "通话用户",
            "context",
            "",
            0,
            1,
            user_avatar_url="https://avatar.example.test/user.png",
            bot_name="通话机器人",
            bot_avatar_url="https://avatar.example.test/bot.png",
        )
        self.assertEqual(
            manager.page_profile_payload(invite),
            {
                "user": {
                    "name": "通话用户",
                    "avatar_url": "https://avatar.example.test/user.png",
                },
                "assistant": {
                    "name": "通话机器人",
                    "avatar_url": "https://avatar.example.test/bot.png",
                },
            },
        )

    async def test_context_marks_matching_relationship_as_current_user_data(self):
        runtime = _Runtime(LifeSettings.from_dict({}))

        async def get_share_context(_scope):
            return {
                "relationships": [
                    {
                        "id": "user",
                        "name": "测试用户",
                        "relationship_story": "经常一起讨论周末安排",
                    }
                ],
                "chat_summaries": [],
            }

        runtime.get_share_context = get_share_context
        manager = VoiceCallManager(runtime)

        context = await manager._build_context(
            "test-platform:FriendMessage:user",
            user_id="user",
            user_name="测试用户",
        )

        self.assertIn('"current_user"', context)
        self.assertIn('"relationship_story":"经常一起讨论周末安排"', context)
        self.assertIn("当前时间事实（通话建立时实时刷新", context)
        self.assertIn('"timezone":"Asia/Shanghai"', context)
        self.assertIn('"time_period"', context)

    async def test_same_scope_reuses_pending_invite(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "api_key": "api-key",
                    "speaker_id": "speaker",
                },
                "realtime_voice_call_config": {
                    "enabled": True,
                    "public_url": "https://voice.example.test",
                    "invite_expire_seconds": 120,
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        manager.gateway.start = AsyncMock()
        event = _Event()

        first = await manager.create_invite(event)
        second = await manager.create_invite(event)

        self.assertEqual(first.rsplit("\n", 1)[-1], second.rsplit("\n", 1)[-1])
        self.assertIn("秒内有效", first)
        self.assertIn("秒内有效", second)
        self.assertEqual(len(manager._invites), 1)
        self.assertEqual(manager.gateway.start.await_count, 2)
        await manager.close()

    async def test_proactive_invite_availability_is_private_and_capacity_bound(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "api_key": "api-key",
                    "speaker_id": "speaker",
                },
                "realtime_voice_call_config": {
                    "enabled": True,
                    "public_url": "https://voice.example.test",
                    "max_concurrent_calls": 1,
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        manager.gateway.start = AsyncMock()

        self.assertTrue(
            manager.proactive_invite_available("aiocqhttp:FriendMessage:user")
        )
        self.assertFalse(
            manager.proactive_invite_available("aiocqhttp:GroupMessage:group")
        )

        await manager.create_invite(_Event())
        self.assertFalse(
            manager.proactive_invite_available("aiocqhttp:private:scope")
        )
        await manager.close()

    async def test_unestablished_invite_can_be_retried_after_connection_failure(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "api_key": "api-key",
                    "speaker_id": "speaker",
                },
                "realtime_voice_call_config": {
                    "enabled": True,
                    "public_url": "https://voice.example.test",
                    "invite_expire_seconds": 120,
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        manager.gateway.start = AsyncMock()
        event = _Event()

        await manager.create_invite(event)
        token = next(iter(manager._invites))
        invite = manager._invites[token]
        signed_token = manager._token_for_invite(invite)
        claimed = manager.claim_invite(signed_token)
        self.assertIs(claimed, invite)
        manager.mark_connecting(invite)

        self.assertTrue(manager.reset_invite_for_retry(invite, "上游服务错误"))
        self.assertTrue(manager.peek_invite(signed_token))
        self.assertEqual(invite.state, "invited")
        self.assertFalse(invite.accepted)
        self.assertFalse(invite.active)
        await manager.close()

    async def test_ending_invite_without_active_session_can_be_retried(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite(
            token_id="retry-ending",
            scope="scope",
            user_id="user",
            user_name="测试用户",
            context="context",
            greeting="",
            created_at=time.time(),
            expires_at=time.time() + 120,
            accepted=True,
            state="ending",
        )
        self.assertTrue(manager.reset_invite_for_retry(invite, "上游返回错误"))
        self.assertEqual(invite.state, "invited")

    async def test_enabled_manager_can_prestart_gateway(self):
        config = LifeSettings.from_dict(
            {
                "realtime_voice_call_config": {"enabled": True},
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        manager.gateway.start = AsyncMock()

        await manager.start_if_enabled()

        manager.gateway.start.assert_awaited_once()
        await manager.close()

    async def test_reconfigure_closes_old_invite_when_connection_settings_change(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"api_key": "api-key", "speaker_id": "speaker"},
                "realtime_voice_call_config": {
                    "enabled": True,
                    "public_url": "https://voice.example.test",
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        manager.gateway.start = AsyncMock()
        manager.gateway.close = AsyncMock()
        invite_message = await manager.create_invite(_Event())
        self.assertIn("voice.example.test", invite_message)

        config.realtime_voice_call.public_url = "https://voice-new.example.test"
        await manager.reconfigure()

        self.assertEqual(manager._invites, {})
        manager.gateway.close.assert_awaited_once()
        self.assertEqual(manager.gateway.start.await_count, 2)
        self.assertEqual(manager.active_count, 0)

    async def test_reused_invite_checks_gateway_readiness(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"api_key": "api-key", "speaker_id": "speaker"},
                "realtime_voice_call_config": {
                    "enabled": True,
                    "public_url": "https://voice.example.test",
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        manager.gateway.start = AsyncMock()

        first_message = await manager.create_invite(_Event())
        manager.gateway.start.reset_mock()
        second_message = await manager.create_invite(_Event())

        self.assertEqual(first_message.rsplit("\n", 1)[-1], second_message.rsplit("\n", 1)[-1])
        manager.gateway.start.assert_awaited_once()

    async def test_invite_prefers_short_url_and_keeps_single_use_token(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"api_key": "api-key", "speaker_id": "speaker"},
                "weather_awareness": {"api_key": "weather-key"},
                "realtime_voice_call_config": {
                    "enabled": True,
                    "public_url": "https://voice.example.test",
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        manager.gateway.start = AsyncMock()
        manager._shorten_invite_url = AsyncMock(return_value="https://short.example.test/a")

        message = await manager.create_invite(_Event())

        self.assertIn("https://short.example.test/a", message)
        manager._shorten_invite_url.assert_awaited_once()
        self.assertIn("https://voice.example.test/call/", manager._shorten_invite_url.await_args.args[0])
        await manager.close()

    async def test_invite_falls_back_to_full_url_when_shortener_fails(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"api_key": "api-key", "speaker_id": "speaker"},
                "realtime_voice_call_config": {
                    "enabled": True,
                    "public_url": "https://voice.example.test",
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        manager.gateway.start = AsyncMock()
        manager._shorten_invite_url = AsyncMock(
            side_effect=lambda original_url: original_url
        )

        message = await manager.create_invite(_Event())

        self.assertIn("https://voice.example.test/call/", message)
        await manager.close()

    async def test_shortener_uses_weather_key_from_daily_life(self):
        config = LifeSettings.from_dict(
            {
                "realtime_voice_call_config": {
                    "short_url_enabled": True,
                },
                "weather_awareness": {"api_key": "daily-life-weather-key"},
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        seen = {}

        class _Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def text(self):
                return json.dumps({"data": {"short_url": "https://short.example.test/x"}})

        class _Session:
            closed = False

            def get(self, url, **kwargs):
                seen["url"] = url
                seen["params"] = kwargs["params"]
                return _Response()

        manager.gateway._session = _Session()

        result = await manager._shorten_invite_url("https://voice.example.test/call/full-token")

        self.assertEqual(result, "https://short.example.test/x")
        self.assertEqual(seen["params"]["apikey"], "daily-life-weather-key")
        self.assertEqual(seen["params"]["url"], "https://voice.example.test/call/full-token")

    async def test_expired_invite_is_rejected(self):
        config = LifeSettings.from_dict({})
        manager = VoiceCallManager(_Runtime(config))
        invite = VoiceCallInvite(
            token_id="expired",
            scope="scope",
            user_id="user",
            user_name="user",
            context="context",
            greeting="",
            created_at=time.time() - 10,
            expires_at=time.time() - 1,
        )
        manager._invites[invite.token_id] = invite
        token = manager._encode({"jti": invite.token_id, "exp": 0, "scope": invite.scope})
        self.assertFalse(manager.peek_invite(token))
        self.assertIsNone(manager.claim_invite(token))

    async def test_finish_invite_records_metadata_only_summary(self):
        config = LifeSettings.from_dict({})
        saved = []

        class _Archive:
            async def add_life_event(self, record):
                saved.append(record)

        runtime = _Runtime(config)
        runtime.archive = _Archive()
        manager = VoiceCallManager(runtime)
        invite = VoiceCallInvite(
            "id",
            "scope",
            "user",
            "name",
            "context",
            "",
            time.time() - 3,
            time.time() + 30,
        )
        invite.accepted = True
        invite.active = True
        invite.state = "active"
        invite.accepted_at = time.time() - 2
        invite.user_transcript = "不应写入摘要的原始内容"
        invite.bot_transcript = "不应写入摘要的原始内容"
        manager._invites[invite.token_id] = invite

        await manager.finish_invite(invite, reason="用户结束通话")

        self.assertEqual(invite.state, "ended")
        self.assertEqual(len(saved), 1)
        self.assertIn("实时语音通话状态", saved[0].detail)
        self.assertNotIn("不应写入摘要", saved[0].detail)
        self.assertEqual(invite.user_transcript, "")
        self.assertEqual(invite.bot_transcript, "")

    async def test_finished_call_keeps_a_short_read_only_transcript_window(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite(
            "finished-id",
            "scope",
            "user",
            "测试用户",
            "context",
            "",
            time.time() - 3,
            time.time() + 30,
        )
        invite.transcript_turns = [
            VoiceCallTranscriptTurn("user", "通话内容", finalized=True),
        ]
        manager._invites[invite.token_id] = invite
        token = manager._token_for_invite(invite)

        await manager.finish_invite(invite)

        self.assertIs(manager.transcript_invite(token), invite)
        self.assertGreater(invite.transcript_expires_at, time.time())
        invite.transcript_expires_at = time.time() - 1
        self.assertIsNone(manager.transcript_invite(token))

    async def test_active_call_transcript_remains_repeatably_readable_after_invite_expiry(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite(
            "active-id",
            "scope",
            "user",
            "测试用户",
            "context",
            "",
            time.time() - 180,
            time.time() - 60,
            accepted=True,
            active=True,
            state="active",
        )
        manager._invites[invite.token_id] = invite
        token = manager._token_for_invite(invite)

        self.assertIs(manager.transcript_invite(token), invite)
        self.assertIs(manager.transcript_invite(token), invite)
        self.assertIn(invite.token_id, manager._invites)

    async def test_finish_invite_persists_transcript_to_conversation_history(self):
        config = LifeSettings.from_dict({})
        calls = []

        class _HistoryRuntime(_Runtime):
            async def _append_turn_history(self, scope, event, user_text, assistant_text):
                calls.append((scope, event.get_sender_id(), event.get_sender_name(), user_text, assistant_text))
                return True

        runtime = _HistoryRuntime(config)
        manager = VoiceCallManager(runtime)
        invite = VoiceCallInvite(
            "id",
            "test-platform:FriendMessage:user",
            "user",
            "测试用户",
            "context",
            "",
            time.time() - 3,
            time.time() + 30,
        )
        invite.accepted = True
        invite.active = True
        invite.state = "active"
        invite.accepted_at = time.time() - 2
        invite.user_transcript = "用户的通话内容"
        invite.bot_transcript = "角色的通话回复"
        manager._invites[invite.token_id] = invite

        await manager.finish_invite(invite, reason="用户结束通话")

        self.assertEqual(
            calls,
            [
                (
                    "test-platform:FriendMessage:user",
                    "user",
                    "测试用户",
                    "用户的通话内容",
                    "角色的通话回复",
                )
            ],
        )
        self.assertTrue(invite.conversation_history_saved)

    async def test_transcript_delta_and_final_events_are_not_duplicated(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite("id", "scope", "u", "name", "context", "", 0, 1)

        manager.record_event(
            invite,
            {
                "type": "conversation.item.input_audio_transcription.started",
                "item_id": "user-1",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "user-1",
                "delta": "你好",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "user-1",
                "delta": "你好，在吗",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "user-1",
                "transcript": "你好，在吗",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "response.output_text.delta",
                "response_id": "assistant-1",
                "delta": "我在",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "response.output_text.delta",
                "response_id": "assistant-1",
                "delta": "我在呢",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "response.output_text.done",
                "response_id": "assistant-1",
                "text": "我在呢",
            },
        )

        self.assertEqual(invite.user_transcript, "你好，在吗")
        self.assertEqual(invite.bot_transcript, "我在呢")
        self.assertEqual(
            manager.transcript_payload(invite),
            [
                {"role": "user", "text": "你好，在吗", "finalized": True},
                {"role": "assistant", "text": "我在呢", "finalized": True},
            ],
        )

    async def test_transcript_merge_removes_repeated_asr_snapshot(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))

        self.assertEqual(
            manager._merge_transcript_text("在干嘛？", "在干嘛在干嘛？"),
            "在干嘛？",
        )
        self.assertEqual(
            manager._merge_transcript_text("你好", "你好，在吗"),
            "你好，在吗",
        )

    async def test_transcript_replaces_a_replayed_response_snapshot(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite("id", "scope", "u", "name", "context", "", 0, 1)
        prefix = "当然呀，上次你请我吃烤肠，这次换我"
        corrected = f"{prefix}请，刚好凑一对。"

        manager.record_event(
            invite,
            {
                "type": "response.output_text.delta",
                "response_id": "assistant-1",
                "delta": prefix,
            },
        )
        manager.record_event(
            invite,
            {
                "type": "response.output_text.delta",
                "response_id": "assistant-1",
                "delta": "刚好凑一对。",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "response.output_text.delta",
                "response_id": "assistant-1",
                "delta": corrected,
            },
        )
        manager.record_event(
            invite,
            {
                "type": "response.output_text.done",
                "response_id": "assistant-1",
                "text": corrected,
            },
        )

        self.assertEqual(manager.transcript_payload(invite), [
            {"role": "assistant", "text": corrected, "finalized": True},
        ])

    async def test_completed_transcript_drops_embedded_replayed_version(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        prefix = "白天太热就宅家吹冷气，傍晚凉快点了再去江边散步拍拍照，和之前一样"
        first_version = f"{prefix}舒服。"
        corrected = f"{prefix}舒服又自在。"

        self.assertEqual(
            manager._deduplicate_transcript_text(first_version + corrected),
            corrected,
        )

    async def test_transcript_turns_are_saved_in_speaking_order(self):
        calls = []

        class _HistoryRuntime(_Runtime):
            async def _append_turn_history(self, scope, _event, user_text, assistant_text):
                calls.append((scope, user_text, assistant_text))
                return True

        manager = VoiceCallManager(_HistoryRuntime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite(
            "id", "scope", "u", "name", "context", "", time.time() - 3, time.time() + 30
        )
        invite.transcript_turns = [
            VoiceCallTranscriptTurn("user", "第一句", finalized=True),
            VoiceCallTranscriptTurn("assistant", "第一句回复", finalized=True),
            VoiceCallTranscriptTurn("user", "第二句", finalized=True),
            VoiceCallTranscriptTurn("assistant", "第二句回复", finalized=True),
        ]
        manager._invites[invite.token_id] = invite

        await manager.finish_invite(invite)

        self.assertEqual(
            calls,
            [
                ("scope", "第一句", "第一句回复"),
                ("scope", "第二句", "第二句回复"),
            ],
        )
        self.assertTrue(invite.conversation_history_saved)

    async def test_user_interrupt_keeps_the_next_response_in_a_new_turn(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite("id", "scope", "u", "name", "context", "", 0, 1)

        manager.record_event(
            invite,
            {
                "type": "response.output_text.delta",
                "response_id": "assistant-1",
                "delta": "等一下，",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "conversation.item.input_audio_transcription.started",
                "item_id": "user-1",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "user-1",
                "transcript": "我先说完。",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "response.output_text.done",
                "response_id": "assistant-2",
                "text": "好，你说。",
            },
        )

        self.assertEqual(
            manager.transcript_payload(invite),
            [
                {"role": "assistant", "text": "等一下，", "finalized": True, "interrupted": True},
                {"role": "user", "text": "我先说完。", "finalized": True},
                {"role": "assistant", "text": "好，你说。", "finalized": True},
            ],
        )

    async def test_interrupted_transcript_only_uses_a_display_ellipsis(self):
        manager = VoiceCallManager(_Runtime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite("id", "scope", "u", "name", "context", "", 0, 1)

        manager.record_event(
            invite,
            {
                "type": "response.output_text.delta",
                "response_id": "assistant-1",
                "delta": "我刚想说",
            },
        )
        manager.record_event(
            invite,
            {
                "type": "conversation.item.input_audio_transcription.started",
                "item_id": "user-1",
            },
        )

        self.assertEqual(
            manager.transcript_payload(invite),
            [{"role": "assistant", "text": "我刚想说", "finalized": True, "interrupted": True}],
        )
        self.assertEqual(invite.transcript_turns[0].text, "我刚想说")

    async def test_interrupted_assistant_turn_is_written_with_one_ellipsis(self):
        calls = []

        class _HistoryRuntime(_Runtime):
            async def _append_turn_history(self, scope, _event, user_text, assistant_text):
                calls.append((scope, user_text, assistant_text))
                return True

        manager = VoiceCallManager(_HistoryRuntime(LifeSettings.from_dict({})))
        invite = VoiceCallInvite("id", "scope", "u", "name", "context", "", 0, 1)
        invite.transcript_turns = [
            VoiceCallTranscriptTurn("user", "你先说", finalized=True),
            VoiceCallTranscriptTurn(
                "assistant", "我刚想说", finalized=True, interrupted=True
            ),
            VoiceCallTranscriptTurn(
                "user", "我先说完", finalized=True
            ),
            VoiceCallTranscriptTurn(
                "assistant", "我其实已经说完了…", finalized=True, interrupted=True
            ),
        ]

        await manager._persist_transcript(invite)

        self.assertEqual(
            calls,
            [
                ("scope", "你先说", "我刚想说…"),
                ("scope", "我先说完", "我其实已经说完了…"),
            ],
        )

    async def test_voice_call_page_renders_turn_snapshots_only(self):
        self.assertIn("payload.kind === 'transcript'", VOICE_CALL_PAGE)
        self.assertIn("block-size: 100dvb", VOICE_CALL_PAGE)
        self.assertIn("overflow: hidden", VOICE_CALL_PAGE)
        self.assertIn(
            "max(clamp(44px, 8dvb, 68px), calc(env(safe-area-inset-top) + 24px))",
            VOICE_CALL_PAGE,
        )
        self.assertIn(
            '.call-screen[data-view="transcript"] {\n      grid-template-rows: minmax(0, 1fr);\n      overflow: hidden;',
            VOICE_CALL_PAGE,
        )
        self.assertIn(
            '.transcript-view {\n      display: grid;\n      grid-template-rows: auto minmax(0, 1fr);\n      block-size: 100%;\n      min-block-size: 0;',
            VOICE_CALL_PAGE,
        )
        self.assertIn(
            '.full-transcript::-webkit-scrollbar { display: none; }',
            VOICE_CALL_PAGE,
        )
        self.assertIn("id=\"lyricTrack\"", VOICE_CALL_PAGE)
        self.assertIn("id=\"transcriptOpen\"", VOICE_CALL_PAGE)
        self.assertIn("id=\"transcriptView\"", VOICE_CALL_PAGE)
        self.assertIn("id=\"fullTranscript\"", VOICE_CALL_PAGE)
        self.assertIn("justify-content: center;", VOICE_CALL_PAGE)
        self.assertIn("text-align: center;", VOICE_CALL_PAGE)
        self.assertNotIn("transcriptBack", VOICE_CALL_PAGE)
        self.assertNotIn("arrow-left", VOICE_CALL_PAGE)
        self.assertIn("openTranscriptView", VOICE_CALL_PAGE)
        self.assertIn("history.pushState", VOICE_CALL_PAGE)
        self.assertIn("popstate", VOICE_CALL_PAGE)
        self.assertNotIn("window.open(", VOICE_CALL_PAGE)
        self.assertNotIn("transcript-sheet", VOICE_CALL_PAGE)
        self.assertNotIn("call-header", VOICE_CALL_PAGE)
        self.assertIn("replaceTranscriptTurns(payload.turns)", VOICE_CALL_PAGE)
        self.assertNotIn("userTranscript +=", VOICE_CALL_PAGE)
        self.assertNotIn("botTranscript +=", VOICE_CALL_PAGE)
        self.assertIn("payload.kind === 'ready'", VOICE_CALL_PAGE)
        self.assertIn("payload.kind === 'gateway_ready'", VOICE_CALL_PAGE)
        self.assertIn("openSocket();", VOICE_CALL_PAGE)
        self.assertIn("if (sessionEstablished)", VOICE_CALL_PAGE)
        self.assertNotIn("正在自动重试", VOICE_CALL_PAGE)
        self.assertIn("id=\"voiceCallProfile\"", VOICE_CALL_PAGE)
        self.assertIn("profile-avatar", VOICE_CALL_PAGE)
        self.assertIn("voiceWallpaperEndpoints", VOICE_CALL_PAGE)
        self.assertIn("https://api.nycnm.cn/api/v2/bizhi1", VOICE_CALL_PAGE)
        self.assertIn("https://api.nycnm.cn/api/v2/bizhi2", VOICE_CALL_PAGE)
        self.assertIn('data-wallpaper="loading"', VOICE_CALL_PAGE)
        self.assertIn('visibility: hidden;', VOICE_CALL_PAGE)
        self.assertIn('wallpaperTimeoutMs = 4500', VOICE_CALL_PAGE)
        self.assertIn("finish('fallback')", VOICE_CALL_PAGE)
        self.assertIn('document.body.dataset.wallpaperVisible = \'true\';', VOICE_CALL_PAGE)
        self.assertIn('document.body.dataset.wallpaper = state;', VOICE_CALL_PAGE)
        self.assertIn("document.body.style.setProperty('--voice-wallpaper', value);", VOICE_CALL_PAGE)
        self.assertIn("loadVoiceWallpaper();", VOICE_CALL_PAGE)
        self.assertIn(".call-screen[data-wallpaper=\"ready\"]::before { opacity: 1; }", VOICE_CALL_PAGE)
        self.assertIn('body::before {', VOICE_CALL_PAGE)
        self.assertIn('body[data-wallpaper="ready"]::before { opacity: 1; }', VOICE_CALL_PAGE)
        self.assertIn('.call-screen { z-index: 1;', VOICE_CALL_PAGE)
        self.assertIn("linear-gradient(180deg, #0d101b38 0%, #11152245 54%, #13172466 100%)", VOICE_CALL_PAGE)
        self.assertIn("row.className = `transcript-turn ${role === 'user' ? 'user' : 'peer'}`;", VOICE_CALL_PAGE)
        self.assertIn("appendTranscriptAvatar(row, profile);", VOICE_CALL_PAGE)
        self.assertIn("bubble.className = 'transcript-bubble';", VOICE_CALL_PAGE)
        self.assertIn("text-align: left;", VOICE_CALL_PAGE)
        self.assertIn("turn.interrupted", VOICE_CALL_PAGE)
        self.assertIn("document.createTextNode('\\u2026')", VOICE_CALL_PAGE)
        self.assertIn("content.append(bubble);", VOICE_CALL_PAGE)
        self.assertIn("border: 1px solid #f1b6cd;", VOICE_CALL_PAGE)
        self.assertIn("background: #fff8fb;", VOICE_CALL_PAGE)
        self.assertIn(".transcript-turn.peer .transcript-bubble::after", VOICE_CALL_PAGE)
        self.assertNotIn("transcript-speaker", VOICE_CALL_PAGE)
        self.assertNotIn("lyric-line", VOICE_CALL_PAGE)
        self.assertNotIn("linear-gradient(180deg, #0d101be0 0%, #111522df 54%, #131724ef 100%)", VOICE_CALL_PAGE)
        self.assertNotIn(".lyric-preview::before", VOICE_CALL_PAGE)
        self.assertNotIn(".lyric-preview::after", VOICE_CALL_PAGE)
        self.assertNotIn("backdrop-filter", VOICE_CALL_PAGE)
        self.assertIn("id=\"mute\"", VOICE_CALL_PAGE)
        self.assertIn("startDuration()", VOICE_CALL_PAGE)
        self.assertIn("kind === 'await_playback'", VOICE_CALL_PAGE)
        self.assertIn("type: 'playback_finished'", VOICE_CALL_PAGE)
        self.assertIn(".call-note:empty { display: none; }", VOICE_CALL_PAGE)
        self.assertNotIn("音频不会在浏览器本地保存", VOICE_CALL_PAGE)
        self.assertNotIn("实时转写已开启", VOICE_CALL_PAGE)
        self.assertNotIn("通话开始后，转写会在这里显示", VOICE_CALL_PAGE)
        self.assertNotIn("lyric-empty", VOICE_CALL_PAGE)

    async def test_standalone_transcript_page_polls_read_only_snapshot(self):
        page = _transcript_page_html(
            '{"user":{"name":"你","avatar_url":""},"assistant":{"name":"对方","avatar_url":""}}',
            '[{"role":"user","text":"你好","finalized":true}]',
        )

        self.assertIn("通话转写", page)
        self.assertIn("/transcript-data/", page)
        self.assertIn("window.setInterval", page)
        self.assertIn("voiceCallTurns", page)
        self.assertIn("turn.interrupted", page)
        self.assertIn("document.createTextNode('\\u2026')", page)
        self.assertIn("text-align: left;", page)
        self.assertNotIn("className = 'speaker'", page)

    async def test_session_payload_uses_duplex_audio_formats(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"speaker_id": "voice"},
                "realtime_voice_call_config": {"model": "1.2.6.1"},
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        payload = manager.session_create_payload(
            VoiceCallInvite("id", "scope", "u", "name", "instructions", "", 0, 1)
        )
        session = payload["session"]
        self.assertEqual(session["model"], "1.2.6.1")
        self.assertEqual(session["audio"]["input"]["format"], {"type": "pcm", "rate": 16000})
        self.assertEqual(session["audio"]["output"]["format"], {"type": "pcm_s16le", "rate": 24000})
        self.assertEqual(session["audio"]["output"]["voice"], "voice")

    async def test_session_payload_keeps_official_capabilities_disabled_by_default(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"speaker_id": "voice"},
                "realtime_voice_call_config": {"model": "1.2.6.1"},
            }
        )
        manager = VoiceCallManager(_Runtime(config))

        payload = manager.session_create_payload(
            VoiceCallInvite("id", "scope", "u", "name", "instructions", "", 0, 1)
        )

        dialog_extra = payload["extension"]["dialog"]["extra"]
        self.assertEqual(
            dialog_extra,
            {"enable_volc_websearch": False, "enable_music": False},
        )

    async def test_session_payload_enables_official_internet_with_valid_settings(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"speaker_id": "voice"},
                "realtime_voice_call_config": {
                    "official_internet_enabled": True,
                    "official_internet_type": "web_agent",
                    "official_internet_api_key": "search-key",
                    "official_internet_bot_id": "bot-123",
                    "official_internet_result_count": 4,
                    "official_internet_no_result_message": "没有搜到相关内容。",
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))

        payload = manager.session_create_payload(
            VoiceCallInvite("id", "scope", "u", "name", "instructions", "", 0, 1)
        )

        self.assertEqual(
            payload["extension"]["dialog"]["extra"],
            {
                "enable_volc_websearch": True,
                "enable_music": False,
                "volc_websearch_type": "web_agent",
                "volc_websearch_api_key": "search-key",
                "volc_websearch_bot_id": "bot-123",
                "volc_websearch_result_count": 4,
                "volc_websearch_no_result_message": "暂时没有找到相关信息。",
            },
        )
        self.assertFalse(
            hasattr(
                config.realtime_voice_call,
                "official_internet_no_result_message",
            )
        )

    async def test_session_payload_disables_invalid_official_capabilities_safely(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"speaker_id": "voice"},
                "realtime_voice_call_config": {
                    "model": "doubao-seed-2-0-lite-260428",
                    "official_internet_enabled": True,
                    "official_internet_type": "web_agent",
                    "official_internet_api_key": "search-key",
                    "official_music_enabled": True,
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))

        payload = manager.session_create_payload(
            VoiceCallInvite("id", "scope", "u", "name", "instructions", "", 0, 1)
        )

        self.assertEqual(
            payload["extension"]["dialog"]["extra"],
            {"enable_volc_websearch": False, "enable_music": False},
        )

    async def test_session_payload_enables_official_music_for_supported_model(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"speaker_id": "voice"},
                "realtime_voice_call_config": {
                    "model": "1.2.1.1",
                    "official_music_enabled": True,
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))

        payload = manager.session_create_payload(
            VoiceCallInvite("id", "scope", "u", "name", "instructions", "", 0, 1)
        )

        self.assertTrue(payload["extension"]["dialog"]["extra"]["enable_music"])

    async def test_o20_session_payload_uses_binary_protocol_fields(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "api_key": "voice-key",
                    "speaker_id": "zh_female_vv_jupiter_bigtts",
                },
                "realtime_voice_call_config": {
                    "model": "1.2.1.1",
                    "official_music_enabled": True,
                },
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        payload = manager.o20_session_payload(
            VoiceCallInvite("id", "scope", "u", "name", "instructions", "", 0, 1)
        )
        self.assertEqual(payload["tts"]["speaker"], "zh_female_vv_jupiter_bigtts")
        self.assertEqual(payload["tts"]["audio_config"]["sample_rate"], 24000)
        self.assertEqual(payload["dialog"]["extra"]["input_mod"], "keep_alive")
        self.assertTrue(payload["dialog"]["extra"]["enable_music"])

    async def test_o20_binary_frame_round_trip(self):
        start = _encode_o20_frame(1, b"{}")
        self.assertEqual(
            list(start),
            [17, 20, 16, 0, 0, 0, 0, 1, 0, 0, 0, 2, 123, 125],
        )
        decoded = _decode_o20_frame(start)
        self.assertEqual(decoded["event_id"], 1)
        self.assertEqual(decoded["payload"], b"{}")
        audio = _encode_o20_frame(200, b"\x01\x02", session_id="session", audio=True)
        decoded_audio = _decode_o20_frame(audio)
        self.assertEqual(decoded_audio["event_id"], 200)
        self.assertEqual(decoded_audio["session_id"], "session")
        self.assertEqual(decoded_audio["payload"], b"\x01\x02")

    async def test_o20_invalid_speaker_error_explains_required_voice_resource(self):
        detail = _VoiceCallBridge._o20_error_detail(
            {
                "error_code": 55_000_001,
                "payload": b'{"message":"ClientError:InvalidSpeaker"}',
            }
        )

        self.assertIn("InvalidSpeaker", detail)
        self.assertIn("O2.0", detail)
        self.assertIn("官方音色 ID", detail)

    def test_gateway_start_error_identifies_listener_and_errno(self):
        settings = type(
            "Settings", (), {"listen_host": "127.0.0.1", "listen_port": 6186}
        )()
        detail = voice_gateway_start_error_detail(
            OSError(98, "Address already in use"), settings
        )

        self.assertIn("监听=127.0.0.1:6186", detail)
        self.assertIn("errno=98", detail)
        self.assertIn("Address already in use", detail)

    async def test_official_capabilities_change_runtime_configuration_signature(self):
        base = LifeSettings.from_dict(
            {"realtime_voice_call_config": {"model": "1.2.6.1"}}
        )
        configured = LifeSettings.from_dict(
            {
                "realtime_voice_call_config": {
                    "model": "1.2.6.1",
                    "official_internet_enabled": True,
                    "official_internet_api_key": "search-key",
                    "official_music_enabled": True,
                }
            }
        )
        manager = VoiceCallManager(_Runtime(base))
        self.assertNotEqual(
            manager._settings_signature(base.realtime_voice_call),
            manager._settings_signature(configured.realtime_voice_call),
        )

    async def test_voice_source_change_updates_runtime_configuration_signature(self):
        base = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "api_key": "voice-key",
                    "speaker_source": "preset",
                    "speaker_id": "voice",
                }
            }
        )
        configured = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "api_key": "voice-key",
                    "speaker_source": "cloned",
                    "speaker_id": "voice",
                }
            }
        )
        manager = VoiceCallManager(_Runtime(base))
        configured_manager = VoiceCallManager(_Runtime(configured))

        self.assertNotEqual(
            manager._settings_signature(base.realtime_voice_call),
            configured_manager._settings_signature(configured.realtime_voice_call),
        )

    async def test_session_payload_keeps_persona_instructions(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"speaker_id": "voice"},
                "realtime_voice_call_config": {"model": "1.2.6.1"},
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        invite = VoiceCallInvite(
            "id", "scope", "u", "name", "当前角色人设：自然回应", "", 0, 1
        )

        payload = manager.session_create_payload(invite)

        self.assertIn("当前角色人设", payload["session"]["instructions"])
        self.assertIn("先听用户说话", payload["session"]["instructions"])

    async def test_voice_call_starts_an_initial_response_after_connecting(self):
        class Upstream:
            closed = False

            def __init__(self):
                self.send_str = AsyncMock()

        bridge = _VoiceCallBridge(
            type("Gateway", (), {"manager": None})(),
            object(),
            VoiceCallInvite("id", "scope", "u", "name", "context", "", 0, 1),
            "token",
        )
        bridge.upstream = Upstream()

        self.assertFalse(bridge._should_start_initial_response())
        bridge.invite.greeting = "刚忙完，正好有空听你说说"
        self.assertTrue(bridge._should_start_initial_response())
        await bridge._start_initial_response()

        payload = json.loads(bridge.upstream.send_str.await_args.args[0])
        self.assertEqual(payload["type"], "response.create")
        self.assertTrue(payload["event_id"].startswith("event_call_"))

    async def test_model_1261_skips_incompatible_manual_initial_response(self):
        class Upstream:
            closed = False

            def __init__(self):
                self.send_str = AsyncMock()

        class Manager:
            settings = type("Settings", (), {"model": "1.2.6.1"})()
            uses_o20_protocol = False

        bridge = _VoiceCallBridge(
            type("Gateway", (), {"manager": Manager()})(),
            object(),
            VoiceCallInvite("id", "scope", "u", "name", "context", "你好，听得到吗？", 0, 1),
            "token",
        )
        bridge.upstream = Upstream()

        self.assertFalse(bridge._should_start_initial_response())
        await bridge._start_initial_response()
        bridge.upstream.send_str.assert_not_awaited()

    async def test_o20_initial_response_uses_say_hello_binary_event(self):
        class Upstream:
            closed = False

            def __init__(self):
                self.send_bytes = AsyncMock()

        class Manager:
            settings = type("Settings", (), {"model": "1.2.1.1"})()
            uses_o20_protocol = True

        bridge = _VoiceCallBridge(
            type("Gateway", (), {"manager": Manager()})(),
            object(),
            VoiceCallInvite("id", "scope", "u", "name", "context", "你好，听得到吗？", 0, 1),
            "token",
        )
        bridge.upstream = Upstream()
        await bridge._start_initial_response()

        packet = bridge.upstream.send_bytes.await_args.args[0]
        decoded = _decode_o20_frame(packet)
        self.assertEqual(decoded["event_id"], 300)
        self.assertEqual(decoded["session_id"], "id")
        self.assertEqual(json.loads(decoded["payload"])["content"], "你好，听得到吗？")

    async def test_session_payload_includes_optional_greeting(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"speaker_id": "voice"},
                "realtime_voice_call_config": {"context_turns": 1},
            }
        )
        manager = VoiceCallManager(_Runtime(config))
        payload = manager.session_create_payload(
            VoiceCallInvite("id", "scope", "u", "name", "context", "你好，听得到吗？", 0, 1)
        )
        self.assertIn("你好，听得到吗？", payload["session"]["instructions"])

    async def test_session_payload_exposes_registered_tools_only_when_enabled(self):
        config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"speaker_id": "voice"},
                "realtime_voice_call_config": {
                    "allow_function_calls": True,
                },
            }
        )

        class Tool:
            def __init__(self, name, active=True):
                self.name = name
                self.description = f"说明 {name}"
                self.parameters = {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                }
                self.active = active

        class Manager:
            def get_full_tool_set(self):
                return type(
                    "ToolSet",
                    (),
                    {
                        "tools": [
                            Tool("life_weather"),
                            Tool("life_voice_call_invite"),
                            Tool("inactive_tool", active=False),
                        ]
                    },
                )()

        runtime = _Runtime(config)
        runtime.context = type(
            "Context", (), {"get_llm_tool_manager": lambda self: Manager()}
        )()
        manager = VoiceCallManager(runtime)
        payload = manager.session_create_payload(
            VoiceCallInvite("id", "scope", "u", "name", "context", "", 0, 1)
        )

        tools = payload["session"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools], ["life_voice_call_end", "life_weather"]
        )
        self.assertEqual(payload["session"]["tool_choice"], "auto")
        self.assertIn("已注册的生活工具", payload["session"]["instructions"])

    async def test_voice_tool_bridge_result_text_and_function_event_parsing(self):
        self.assertEqual(_chain_text({"content": [{"text": "天气不错"}]}), "天气不错")
        event = VoiceCallToolEvent(
            VoiceCallInvite("id", "scope", "u", "用户", "context", "", 0, 1),
            _Runtime(LifeSettings.from_dict({})),
        )
        self.assertEqual(_chain_text(event.plain_result("已找到歌曲")), "已找到歌曲")

        class Record:
            pass

        event_result = event.chain_result([Record()])
        self.assertEqual(_chain_text(event_result), "已发送语音。")

        call_id, name, arguments, done = _VoiceCallBridge._function_call_parts(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "call-1",
                "name": "life_weather",
                "arguments": '{"city":"测试市"}',
            }
        )
        self.assertEqual((call_id, name, arguments, done), ("call-1", "life_weather", '{"city":"测试市"}', True))
        delta = _VoiceCallBridge._function_call_parts(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": "call-1",
                "name": "life_weather",
                "delta": '{"city":',
            }
        )
        self.assertEqual(delta, ("call-1", "life_weather", '{"city":', False))
        items_call = _VoiceCallBridge._function_call_parts(
            {
                "type": "response.function_call_arguments.done",
                "items": [
                    {
                        "type": "function_call",
                        "call_id": "call-2",
                        "name": "life_weather",
                        "arguments": '{"city":"测试市"}',
                    }
                ],
            }
        )
        self.assertEqual(
            items_call,
            ("call-2", "life_weather", '{"city":"测试市"}', True),
        )
        self.assertFalse(
            _VoiceCallBridge._is_function_call_completion(
                {"type": "response.function_call_arguments.delta"}
            )
        )
        self.assertTrue(
            _VoiceCallBridge._is_function_call_completion(
                {"type": "response.function_call_arguments.done"}
            )
        )

    async def test_function_call_created_event_is_joined_to_arguments_done(self):
        calls = []

        class ToolBridge:
            async def call(self, name, arguments):
                calls.append((name, arguments))
                return "天气不错。"

        class Manager:
            def tool_bridge(self, _invite):
                return ToolBridge()

        invite = VoiceCallInvite("id", "scope", "u", "用户", "context", "", 0, 1)
        browser = type("Browser", (), {"send_json": AsyncMock(), "close": AsyncMock()})()
        bridge = _VoiceCallBridge(
            type("Gateway", (), {"manager": Manager()})(), browser, invite, "token"
        )
        bridge.upstream = type(
            "Upstream", (), {"closed": False, "send_str": AsyncMock()}
        )()

        await bridge._handle_function_call(
            {
                "type": "conversation.item.created",
                "item": {
                    "type": "function_call",
                    "call_id": "call-end",
                    "name": "life_weather",
                },
            }
        )
        await bridge._handle_function_call(
            {
                "type": "response.function_call_arguments.done",
                "items": [
                    {
                        "type": "function_call",
                        "call_id": "call-end",
                        "name": "life_weather",
                        "arguments": '{"city":"测试市"}',
                    }
                ],
            }
        )

        self.assertEqual(calls, [("life_weather", {"city": "测试市"})])
        sent = [json.loads(call.args[0]) for call in bridge.upstream.send_str.await_args_list]
        tool_result = next(
            payload for payload in sent if payload["type"] == "conversation.item.create"
        )
        self.assertNotIn("response.create", [payload["type"] for payload in sent])
        self.assertEqual(
            tool_result["items"],
            [
                {
                    "type": "message",
                    "role": "tool",
                    "call_id": "call-end",
                    "content": [
                        {"type": "input_text", "text": "天气不错。"}
                    ],
                }
            ],
        )

        config = LifeSettings.from_dict(
            {"realtime_voice_call_config": {"allow_function_calls": True}}
        )
        class Manager:
            def get_full_tool_set(self):
                return type("ToolSet", (), {"tools": []})()

        runtime = _Runtime(config)
        runtime.context = type(
            "Context", (), {"get_llm_tool_manager": lambda self: Manager()}
        )()
        invite = VoiceCallInvite("id", "scope", "u", "用户", "context", "", 0, 1)
        self.assertEqual(
            [tool["name"] for tool in VoiceCallToolBridge(runtime, invite).schemas()],
            ["life_voice_call_end"],
        )

if __name__ == "__main__":
    unittest.main()
