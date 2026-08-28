from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from core.config.options import LifeSettings
from core.runtime.voicecall.manager import (
    VoiceCallInvite,
    _rtc_function_calls,
)
from core.runtime.voicecall.videocall import (
    RtcVoiceCallManager,
    VoiceCallInvite as RtcVoiceCallInvite,
)
from core.runtime.voicecall.rtc import (
    RtcTokenBuilder,
    callback_signature_present,
    callback_signature_valid,
    decode_callback_message,
)
from core.runtime.voicecall.rtcweb import rtc_page


class _Runtime:
    def __init__(self, config):
        self.config = config


def test_rtc_token_matches_official_binary_shape() -> None:
    token = (
        RtcTokenBuilder("a" * 24, "secret", "room", "user")
        .add_privilege(RtcTokenBuilder.PRIV_PUBLISH_STREAM, 123)
        .add_privilege(RtcTokenBuilder.PRIV_SUBSCRIBE_STREAM, 123)
        .expire_time(123)
        .build()
    )
    assert token.startswith("001" + "a" * 24)
    assert len(token) > 27


def test_rtc_callback_unwraps_nested_message_and_keeps_signature() -> None:
    payload = decode_callback_message(
        {
            "Type": "tool_calls",
            "ServerMessageSignature": "callback-secret",
            "Message": json.dumps(
                {"tool_calls": [{"id": "call_1", "function": {"name": "demo"}}]}
            ),
        }
    )
    assert payload["Type"] == "tool_calls"
    assert payload["ServerMessageSignature"] == "callback-secret"
    assert payload["tool_calls"][0]["function"]["name"] == "demo"
    assert callback_signature_valid(payload, "callback-secret")


def test_rtc_callback_keeps_type_when_message_is_tool_call_array() -> None:
    payload = decode_callback_message(
        {
            "Type": "tool_calls",
            "TaskID": "task-1",
            "Message": json.dumps(
                [
                    {
                        "id": "call_weather",
                        "type": "function",
                        "function": {
                            "name": "life_weather",
                            "arguments": '{"city":"测试市"}',
                        },
                    }
                ]
            ),
        }
    )
    assert payload["Type"] == "tool_calls"
    assert payload["TaskID"] == "task-1"
    calls = _rtc_function_calls(payload)
    assert calls[0]["function"]["name"] == "life_weather"


def test_rtc_callback_extracts_nested_function_calls_without_event_name() -> None:
    payload = decode_callback_message(
        {
            "EventName": "VoiceChat",
            "Data": json.dumps(
                {
                    "ToolCalls": [
                        {
                            "ToolCallID": "call_weather",
                            "Function": {
                                "Name": "life_weather",
                                "Arguments": '{"city":"测试市"}',
                            },
                        }
                    ]
                }
            ),
        }
    )
    calls = _rtc_function_calls(payload)
    assert len(calls) == 1
    assert calls[0]["ToolCallID"] == "call_weather"
    assert calls[0]["Function"]["Name"] == "life_weather"


def test_rtc_callback_extracts_flat_function_name_shape() -> None:
    calls = _rtc_function_calls(
        decode_callback_message(
            {
                "EventName": "FunctionCall",
                "Message": json.dumps(
                    {
                        "ToolCallID": "call_weather",
                        "FunctionName": "life_weather",
                        "Arguments": '{"city":"测试市"}',
                    }
                ),
            }
        )
    )
    assert len(calls) == 1
    assert calls[0]["FunctionName"] == "life_weather"


def test_rtc_tool_result_update_includes_client_user_id() -> None:
    config = LifeSettings.from_dict(
        {
            "realtime_voice_call_config": {"rtc_app_id": "app"},
        }
    )
    manager = RtcVoiceCallManager(_Runtime(config))
    invite = RtcVoiceCallInvite(
        "invite-1",
        "FriendMessage:test",
        "user-1",
        "测试用户",
        "",
        "",
        0,
        4_000_000_000,
        rtc_room_id="room-1",
        rtc_task_id="task-1",
        rtc_user_id="rtc-user-1",
    )

    api = type("Api", (), {})()
    api.update_voice_chat = AsyncMock()
    bridge = type("Bridge", (), {"call": AsyncMock(return_value="天气晴朗")})()
    manager._rtc_openapi = lambda: api
    manager.tool_bridge = lambda _invite: bridge

    asyncio.run(
        manager._handle_rtc_tool_callback(
            invite,
            {
                "ToolCalls": [
                    {
                        "ToolCallID": "call-1",
                        "FunctionName": "life_weather",
                        "Arguments": "{}",
                    }
                ]
            },
        )
    )

    body = api.update_voice_chat.await_args.args[0]
    assert body["UserId"] == "rtc-user-1"
    assert body["Command"] == "function"


def test_rtc_callback_accepts_callback_header_hmac() -> None:
    body = b'{"EventName":"VoiceChat"}'
    secret = "callback-secret"
    request_id = "req-1"
    timestamp = "1787480000"
    import hashlib
    import hmac

    signature = hmac.new(
        secret.encode(), f"{request_id}.{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    headers = {
        "X-Callback-Request-Id": request_id,
        "X-Callback-Timestamp": timestamp,
        "X-Callback-Signature": f"sha256={signature}",
    }
    assert callback_signature_present({}, headers)
    assert callback_signature_valid({}, secret, headers=headers, raw_body=body)


def test_rtc_callback_flattens_nested_status_data() -> None:
    payload = decode_callback_message(
        {
            "Type": "task_status",
            "data": {
                "Status": "failed",
                "ErrorCode": "NoPermission",
                "ErrorMessage": "任务不可用",
            },
        }
    )
    assert payload["Status"] == "failed"
    assert payload["ErrorCode"] == "NoPermission"
    assert payload["ErrorMessage"] == "任务不可用"


def test_rtc_callback_accepts_run_stage_and_nested_extra_error() -> None:
    payload = decode_callback_message(
        {
            "EventName": "VoiceChat",
            "RunStage": "ttsError",
            "ExtraInfo": json.dumps({"ErrorCode": "TTSUnavailable", "Message": "语音服务不可用"}),
        }
    )
    assert payload["RunStage"] == "ttsError"
    assert json.loads(payload["ExtraInfo"])["ErrorCode"] == "TTSUnavailable"


def test_static_rtc_callback_matches_task_or_room_id() -> None:
    manager = RtcVoiceCallManager.__new__(RtcVoiceCallManager)
    manager._invites = {
        "invite-1": RtcVoiceCallInvite(
            token_id="invite-1",
            scope="FriendMessage:test",
            user_id="user-1",
            user_name="测试用户",
            context="",
            greeting="",
            created_at=1,
            expires_at=4_000_000_000,
            rtc_room_id="room-1",
            rtc_task_id="task-1",
        )
    }

    assert manager._rtc_callback_identifiers({"Data": {"TaskID": "task-1"}}) == {"task-1"}
    assert manager._find_rtc_callback_invite({"RoomId": "room-1"}) is manager._invites["invite-1"]
    assert manager._find_rtc_callback_invite({"EventName": "VoiceChat"}) is manager._invites["invite-1"]


def test_realtime_settings_have_no_transport_selector() -> None:
    settings = LifeSettings.from_dict({}).realtime_voice_call
    assert not hasattr(settings, "transport")
    assert settings.rtc_sdk_url.endswith("/index.min.js")
    assert settings.rtc_video_enabled is False
    assert settings.rtc_vision_image_detail == "low"


def test_realtime_settings_accept_rtc_credentials_without_transport_selector() -> None:
    settings = LifeSettings.from_dict(
        {
            "realtime_voice_call_config": {
                "rtc_app_id": "app",
                "rtc_app_key": "app-key",
                "rtc_access_key": "ak",
                "rtc_secret_key": "sk",
            }
        }
    ).realtime_voice_call
    assert settings.rtc_app_id == "app"
    assert settings.rtc_access_key == "ak"


def test_realtime_video_settings_are_clamped_and_normalized() -> None:
    settings = LifeSettings.from_dict(
        {
            "realtime_voice_call_config": {
                "rtc_video_enabled": True,
                "rtc_vision_image_detail": "HIGH",
                "rtc_vision_height": 99999,
                "rtc_vision_interval_ms": 1,
                "rtc_vision_images_limit": 99,
                "rtc_vision_auto_select": True,
            }
        }
    ).realtime_voice_call
    assert settings.rtc_video_enabled is True
    assert settings.rtc_vision_image_detail == "high"
    assert settings.rtc_vision_height == 1792
    assert settings.rtc_vision_interval_ms == 200
    assert settings.rtc_vision_images_limit == 10
    assert settings.rtc_vision_auto_select is True


def test_rtc_payload_enables_camera_snapshot_vision() -> None:
    config = LifeSettings.from_dict(
        {
            "voice_generation_config": {"speaker_id": "speaker"},
            "realtime_voice_call_config": {
                "public_url": "https://voice.example.test",
                "rtc_video_enabled": True,
                "rtc_vision_image_detail": "high",
                "rtc_vision_height": 720,
                "rtc_vision_interval_ms": 800,
                "rtc_vision_images_limit": 4,
            },
        }
    )
    manager = RtcVoiceCallManager(_Runtime(config))
    invite = RtcVoiceCallInvite("id", "scope", "user", "用户", "context", "", 0, 1)
    payload = manager._rtc_start_payload(invite)
    snapshot = payload["Config"]["LLMConfig"]["VisionConfig"]["SnapshotConfig"]
    assert payload["Config"]["LLMConfig"]["VisionConfig"]["Enable"] is True
    assert snapshot == {
        "StreamType": 0,
        "ImageDetail": "high",
        "Height": 720,
        "Interval": 800,
        "ImagesLimit": 4,
        "AutoSelect": False,
    }


def test_rtc_payload_uses_ai_voice_chat_provider_shapes() -> None:
    config = LifeSettings.from_dict(
        {
            "voice_generation_config": {
                "speaker_id": "S_demo",
                "speaker_source": "cloned",
            },
            "realtime_voice_call_config": {},
        }
    )
    manager = RtcVoiceCallManager(_Runtime(config))
    invite = RtcVoiceCallInvite("id", "scope", "user", "用户", "context", "", 0, 1)
    payload = manager._rtc_start_payload(invite)
    asr = payload["Config"]["ASRConfig"]
    tts = payload["Config"]["TTSConfig"]
    assert asr["ProviderParams"]["Credential"] == {
        "ApiResourceId": "volc.seedasr.sauc.duration"
    }
    assert asr["ProviderParams"]["StreamMode"] == 2
    assert json.loads(asr["ProviderParams"]["VolcanoASRParameters"]) == {
        "request": {"enable_nonstream": True}
    }
    assert asr["Provider"] == "volcano"
    assert asr["TurnDetectionMode"] == 0
    assert payload["Config"]["LLMConfig"]["ModelName"] == "Doubao-Seed-1.6｜250615"
    assert tts["Provider"] == "volcano_bidirection"
    assert tts["ProviderParams"]["ResourceId"] == "seed-icl-2.0"
    assert tts["ProviderParams"]["audio"] == {
        "voice_type": "S_demo",
        "speech_rate": 0,
    }
    assert "Credential" not in tts["ProviderParams"]
    assert "VolcanoTTSParameters" not in tts["ProviderParams"]
    assert "app" not in tts["ProviderParams"]


def test_rtc_payload_does_not_require_asr_tts_credentials() -> None:
    config = LifeSettings.from_dict(
        {
            "voice_generation_config": {"speaker_id": "BV_demo", "api_key": "voice-key"},
            "realtime_voice_call_config": {},
        }
    )
    manager = RtcVoiceCallManager(_Runtime(config))
    invite = RtcVoiceCallInvite("id", "scope", "user", "用户", "context", "", 0, 1)
    payload = manager._rtc_start_payload(invite)
    asr = payload["Config"]["ASRConfig"]["ProviderParams"]
    tts = payload["Config"]["TTSConfig"]["ProviderParams"]
    assert "AppId" not in asr
    assert "AccessToken" not in asr
    assert "app" not in tts
    assert "APIKey" not in tts


def test_rtc_payload_uses_speech_rate_for_regular_tts() -> None:
    config = LifeSettings.from_dict(
        {
            "voice_generation_config": {
                "speaker_id": "BV_demo",
                "speaker_source": "preset",
                "speech_rate": 12,
            },
            "realtime_voice_call_config": {},
        }
    )
    manager = RtcVoiceCallManager(_Runtime(config))
    invite = RtcVoiceCallInvite("id", "scope", "user", "用户", "context", "", 0, 1)
    provider_params = manager._rtc_start_payload(invite)["Config"]["TTSConfig"]["ProviderParams"]
    assert provider_params["ResourceId"] == "seed-tts-2.0"
    assert provider_params["audio"] == {
        "voice_type": "BV_demo",
        "speech_rate": 12,
    }


def test_rtc_error_classifies_tts_resource_mismatch() -> None:
    message = 'receive Error message (code=55000000): {"error":"resource ID is mismatched with speaker related resource"}'
    assert RtcVoiceCallManager._rtc_error_type(message) == "tts_resource_mismatch"
    assert "复刻音色不匹配" in RtcVoiceCallManager._rtc_error_message(message)


def test_rtc_page_contains_optional_camera_track_flow() -> None:
    page = rtc_page({"assistant": {"name": "对方"}}, "https://rtc.example.test/sdk.js")
    assert "https://api.nycnm.cn/api/v2/bizhi1" in page
    assert "https://api.nycnm.cn/api/v2/bizhi2" in page
    assert "voice_call=" in page
    assert "body[data-wallpaper=\"ready\"]::before" in page
    assert "const resetStartButton = () =>" in page
    assert "const markEndedButton = () =>" in page
    assert "markEndedButton(); setStatus(finalStatus)" in page
    assert "start.hidden = true" in page
    assert ".start-button[hidden] { display: none; }" in page
    assert "resetStartButton(); start.disabled=false; end.disabled=true" in page
    assert "await finish(false, '通话已结束', finalHint)" in page
    assert page.count('<div class="meter"') == 1
    assert page.count('<div class="meter" aria-hidden="true"><i></i>') == 1
    assert page.count('<i></i>') >= 5
    assert "video_enabled" in page
    assert "startVideoCapture" in page
    assert "setVideoCaptureDevice" in page
    assert "switchCamera = async" in page
    assert "dblclick" in page
    assert "双击切换前后摄像头" in page
    assert "header-badge" not in page
    assert "data-lucide=\"video\"" not in page
    assert "data-facing=\"environment\"" in page
    assert "publishStream(api.MediaType.VIDEO)" in page
    assert "fetch('/rtc/start/'" in page
    assert "已进入房间，等待 Bot 接入" not in page
    assert "stopVideoCapture" in page
    assert "async (event, publishedMediaType)" in page
    assert "typeof event === 'string' || typeof event === 'number'" in page
    assert "playbackBlocked" in page
    assert "浏览器阻止了自动播放" in page
    assert "onLocalAudioPropertiesReport" in page
    assert "onRemoteAudioPropertiesReport" in page
    assert "已收到你的声音，等待 Bot 回应" in page
    assert "请先说一句，Bot 会回应" in page
