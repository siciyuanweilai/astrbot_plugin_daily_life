"""验证异步媒体交付延续原请求的身份边界，不靠替换回复里的代词。"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import support  # noqa: F401 - 先安装 AstrBot 测试替身。

# isort: split

from core.runtime.channel.image import RuntimeImageMediaComponent
from core.runtime.channel.motion import RuntimeVideoMediaComponent
from core.runtime.channel.perspective import (
    bind_media_reply_context,
    media_reply_context,
    media_reply_context_text,
    restore_media_reply_context,
)
from core.runtime.channel.suite import RuntimePhotoSuiteMediaComponent
from core.runtime.channel.summary import RuntimeMediaCommonComponent
from runtimehelpers import DailyLifeRuntime, Event


class MediaPerspectiveTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.persona = "生活与口吻设定。" * 160 + "我的名字是小岚，聊天对象是阿遥。"
        self.host = SimpleNamespace(
            get_persona_text=AsyncMock(return_value=self.persona),
            _event_current_image_request_text=lambda event: event.message_str,
            _event_session_id=lambda event: event.unified_msg_origin,
            get_text_provider=AsyncMock(return_value=object()),
            call_text_model=AsyncMock(
                return_value='{"reply_text":"这阵晚风拍得真好。"}'
            ),
            _parse_life_video_reply_text=RuntimeMediaCommonComponent._parse_delivered_media_reply,
            _parse_delivered_media_reply=RuntimeMediaCommonComponent._parse_delivered_media_reply,
            _media_error_summary=str,
            media=SimpleNamespace(
                image=SimpleNamespace(
                    friend_reference_options=lambda: [
                        {"profile_id": "10001", "display_name": "阿遥"}
                    ],
                )
            ),
        )

    @staticmethod
    def event(sender="10001", *, group=False):
        return SimpleNamespace(
            unified_msg_origin="aiocqhttp:GroupMessage:group-9"
            if group
            else f"aiocqhttp:FriendMessage:{sender}",
            message_str="现在可以拍合影视频了，你再试试看",
            get_sender_id=lambda: sender,
            get_sender_name=lambda: "阿遥",
        )

    async def test_group_frame_binds_character_and_current_addressee(self):
        event = self.event()
        value = await bind_media_reply_context(self.host, event, "group", ["10001"])
        self.assertEqual(value["画面人物对应"][0]["对话身份"], "说话人（我）")
        self.assertEqual(value["画面人物对应"][1]["对话身份"], "当前聊天对象（你）")
        self.assertEqual(value["角色是否在画面中"], "是")
        self.assertEqual(value["用户原话"], event.message_str)
        self.assertIn("我的名字是小岚", media_reply_context_text(event))
        self.host.call_text_model.assert_not_awaited()

    async def test_other_friend_does_not_become_current_addressee(self):
        event = self.event()
        value = await bind_media_reply_context(self.host, event, "group", ["10002"])
        self.assertEqual(value["当前聊天对象"]["id"], "10001")
        self.assertNotEqual(value["画面人物对应"][1]["对话身份"], "当前聊天对象（你）")

    async def test_subjective_name_is_kept_separate_from_person_identity(self):
        self.host.archive = SimpleNamespace(get_relationship=AsyncMock(return_value=SimpleNamespace(
            name="Sylus", alias="旧称呼", subjective_name="这位群友", user_id="10002", platform="aiocqhttp")))
        value = await bind_media_reply_context(self.host, self.event(), "group", ["10002"])
        person = value["画面人物对应"][1]
        self.assertEqual(person["姓名或称呼"], "Sylus")
        self.assertEqual(person["习惯称呼"], "这位群友")
        self.assertNotEqual(person["对话身份"], "当前聊天对象（你）")

    async def test_group_chat_uses_sender_instead_of_group_delivery_id(self):
        event = self.event(group=True)
        value = await bind_media_reply_context(self.host, event, "group", ["group-9"])
        self.assertEqual(value["当前聊天对象"]["id"], "10001")
        self.assertNotEqual(value["画面人物对应"][1]["对话身份"], "当前聊天对象（你）")
        event.get_sender_id = lambda: "10002"
        value = await bind_media_reply_context(self.host, event, "group", ["10001"])
        self.assertEqual(value["当前聊天对象"]["id"], "10002")
        self.assertNotEqual(value["画面人物对应"][1]["对话身份"], "当前聊天对象（你）")

    async def test_explicit_relationship_account_resolves_profile_alias(self):
        self.host.archive = SimpleNamespace(
            get_relationship=AsyncMock(
                return_value=SimpleNamespace(
                    name="阿遥",
                    alias="",
                    subjective_name="",
                    user_id="10001",
                    platform="aiocqhttp",
                )
            )
        )
        value = await bind_media_reply_context(
            self.host, self.event(), "group", ["friend-profile"]
        )
        self.assertEqual(value["画面人物对应"][1]["对话身份"], "当前聊天对象（你）")
        self.host.archive.get_relationship.return_value.platform = "different-platform"
        value = await bind_media_reply_context(
            self.host, self.event(), "group", ["friend-profile"]
        )
        self.assertNotEqual(value["画面人物对应"][1]["对话身份"], "当前聊天对象（你）")

    async def test_unidentified_picture_and_scene_do_not_assume_character_presence(
        self,
    ):
        for route, expected in (("free", "未确认"), ("scene", "否")):
            with self.subTest(route=route):
                value = await bind_media_reply_context(
                    self.host, self.event(), route, ["10001"]
                )
                self.assertEqual(value["角色是否在画面中"], expected)
                self.assertEqual(value["画面人物对应"], [])

    async def test_private_origin_fallback_does_not_use_group_id(self):
        for group, expected in ((False, "10001"), (True, "")):
            event = self.event(group=group)
            del event.get_sender_id
            value = await bind_media_reply_context(self.host, event, "group", ["10001"])
            self.assertEqual(value["当前聊天对象"]["id"], expected)

    async def test_all_media_reply_models_receive_same_original_identity_context(self):
        event = self.event()
        await bind_media_reply_context(self.host, event, "group", ["10001"])
        # 模拟长任务中会话配置或原消息改变，已受理任务仍使用自己的身份快照。
        self.host.get_persona_text.return_value = "另一个当前人设。"
        event.message_str = "后来的其他请求"
        video = RuntimeVideoMediaComponent(self.host)
        common = RuntimeMediaCommonComponent(self.host)
        suite = RuntimePhotoSuiteMediaComponent(self.host)
        await video._generate_life_video_followup_text(event, "江边合影", "视频已送达")
        await common._generate_delivered_media_reply(
            event.unified_msg_origin,
            media_name="生活照片",
            request_text="江边合影",
            delivery_text="照片已送达",
            source_event=event,
        )
        await suite._photo_suite_followup_text(
            event,
            prompt="江边合影",
            sent_count=3,
            requested_count=3,
            total_count=3,
            failed_indexes=[],
            is_retry=False,
            error="",
        )
        self.assertEqual(self.host.call_text_model.await_count, 3)
        for call in self.host.call_text_model.await_args_list:
            prompt = call.args[1]
            self.assertIn("说话人（我）", prompt)
            self.assertIn("当前聊天对象（你）", prompt)
            self.assertIn("我的名字是小岚", prompt)
            self.assertIn("现在可以拍合影视频了", prompt)
            self.assertNotIn("后来的其他请求", prompt)
            self.assertNotIn("另一个当前人设", prompt)

    async def test_media_snapshot_keeps_independent_context_after_serialization(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        original = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        value = await bind_media_reply_context(
            self.host, self.event(), "group", ["10001"]
        )
        restore_media_reply_context(original, value)
        snapshot = runtime._snapshot_media_event(original)
        original._daily_life_media_reply_context["画面人物对应"].clear()
        self.assertEqual(len(media_reply_context(snapshot)["画面人物对应"]), 2)
        restored = SimpleNamespace()
        restore_media_reply_context(
            restored, json.loads(json.dumps(media_reply_context(snapshot)))
        )
        self.assertEqual(media_reply_context(restored), value)
        snapshot._daily_life_media_reply_context["当前聊天对象"]["id"] = "new-sender"
        self.assertEqual(media_reply_context(restored)["当前聊天对象"]["id"], "10001")

    async def test_automatic_character_detection_reaches_image_followup_context(self):
        event = self.event()
        await bind_media_reply_context(self.host, event, "free")
        self.host._image_prompt_aspect_ratio = lambda prompt: ""
        self.host._image_prompt_resolution = lambda prompt: ""
        self.host._direct_life_image_payload = AsyncMock(
            return_value=SimpleNamespace(
                contains_character=True,
                needs_character_reference=False,
            )
        )
        self.host._life_character_reference_image = lambda: ""
        self.host._resolve_life_identity_profiles = AsyncMock(return_value={})
        self.host._generate_life_image_with_policy_retry = AsyncMock(
            return_value="image"
        )
        component = RuntimeImageMediaComponent(self.host)

        self.assertEqual(
            await component.generate_life_image_asset(event, "我的窗边生活照"), "image"
        )
        self.assertEqual(media_reply_context(event)["角色是否在画面中"], "是")
        self.assertEqual(
            media_reply_context(event)["画面人物对应"][0]["对话身份"], "说话人（我）"
        )

    async def test_missing_context_stays_unknown_and_retains_full_persona(self):
        event = SimpleNamespace()
        restore_media_reply_context(event, "旧任务没有身份快照")
        self.assertEqual(media_reply_context(event), {})
        text = media_reply_context_text(event, self.persona)
        self.assertIn("我的名字是小岚", text)
        self.assertNotIn("当前聊天对象（你）", text)
