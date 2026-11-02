import asyncio
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import support  # noqa: F401 - 导入运行时模块前先安装 AstrBot 测试替身。

# isort: split

from core.media.picture.polling import ImageTaskError
from core.outcome import ToolResultText
from core.runtime.background import BackgroundTaskScheduler
from core.runtime.channel import image as image_module
from core.runtime.delivery import BackgroundTextMode
from core.sight.clip import SightClip, SightInsight
from runtimehelpers import (
    Context,
    DailyLifeRuntime,
    Event,
    LifeSettings,
    Provider,
    RuntimeAsyncHelpers,
    async_return,
)


class MediaBackgroundTest(RuntimeAsyncHelpers, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.context = Context(Provider([]))
        self.runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"quiet_hours": ""},
                "chat_style_config": {
                    "continuous_turn_wait_seconds": 0,
                    "continuous_turn_max_wait_seconds": 0,
                }
            }
        )
        self.runtime._init_continuous_turn_state()
        self.runtime._semantic_segment_enabled = lambda: False
        self._stub_media_director(self.runtime)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "life.png"
        self.path.write_bytes(b"synthetic image")
        self.runtime.data_path = self.path.parent / "daily_life.db"
        self.video_path = Path(directory.name) / "life.mp4"
        self.video_path.write_bytes(b"synthetic video")
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.video_started = asyncio.Event()
        self.video_release = asyncio.Event()
        self.calls = []

        async def generate(prompt, **options):
            self.calls.append(("generate", prompt, options))
            self.started.set()
            await self.release.wait()
            return types.SimpleNamespace(path=self.path)

        async def edit(prompt, reference, **options):
            self.calls.append(("edit", prompt, reference, options))
            self.started.set()
            await self.release.wait()
            return types.SimpleNamespace(path=self.path)

        async def video(prompt, image_bytes=None, **options):
            self.calls.append(("video", prompt, image_bytes))
            self.video_started.set()
            await self.video_release.wait()
            return types.SimpleNamespace(url=str(self.video_path))

        self.runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=generate,
                edit_image=edit,
                _load_reference_image=lambda reference: async_return(
                    (b"frame", "image/png")
                ),
            ),
            video=types.SimpleNamespace(generate_video=video),
        )
        self.addAsyncCleanup(self.runtime._cancel_background_tasks)

    @staticmethod
    def event(message_id="42", text="拍张生活照", image=""):
        event = Event(message_id=message_id)
        event.message_str = text
        if image:
            event.message_items.append({"type": "image", "file": image})
        return event

    async def begin_turn(self, event):
        self.runtime.note_continuous_turn_incoming(event)
        self.assertTrue(await self.runtime.settle_continuous_turn(event))
        request = types.SimpleNamespace(prompt=event.message_str, system_prompt="")
        self.assertTrue(
            self.runtime.prepare_continuous_turn_llm_request(event, request)
        )
        return request

    async def finish_tasks(self):
        self.release.set()
        self.video_release.set()
        await asyncio.wait_for(
            asyncio.gather(
                *list(self.runtime._background_scheduler_for_runtime().tasks)
            ),
            timeout=2,
        )

    async def test_image_returns_pending_and_new_chat_runs_before_generation_finishes(
        self,
    ):
        first = self.event()
        await self.begin_turn(first)
        result = await asyncio.wait_for(
            self.runtime.life_image_generate(first, "雨夜窗边生活照"), timeout=0.5
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        await asyncio.wait_for(self.started.wait(), timeout=0.5)
        self.assertEqual(first.sent_messages, [])

        later = self.event("43", "继续聊刚才那件事")
        request = await asyncio.wait_for(self.begin_turn(later), timeout=0.5)
        self.assertEqual(request.prompt, later.message_str)
        self.assertEqual(
            self.runtime.continuous_turn_messages(later), (later.message_str,)
        )
        later.set_result(later.chain_result(["好，接着说。 "]))
        self.assertFalse(self.runtime.hold_life_image_final_text(later))
        self.assertIsNotNone(later.get_result())
        self.assertFalse(self.release.is_set())
        await self.finish_tasks()
        self.assertEqual(len(first.sent_messages), 1)
        self.assertEqual(first._daily_life_image_request["status"], "sent")
        self.assertEqual(later.sent_messages, [])

    async def test_image_director_wait_also_runs_outside_chat_tool(self):
        started = asyncio.Event()
        release = asyncio.Event()
        original = self.runtime._direct_life_image_payload

        async def director(*args, **kwargs):
            started.set()
            await release.wait()
            return await original(*args, **kwargs)

        self.runtime._direct_life_image_payload = director
        first = self.event()
        await self.begin_turn(first)
        result = await asyncio.wait_for(
            self.runtime.life_image_generate(first, "窗边照片"), timeout=0.5
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        await asyncio.wait_for(started.wait(), timeout=0.5)
        await asyncio.wait_for(
            self.begin_turn(self.event("43", "你吃午饭了吗")), timeout=0.5
        )
        self.assertEqual(self.calls, [])
        release.set()
        await self.finish_tasks()

    async def test_group_image_repairable_parameters_are_checked_before_background_acceptance(self):
        first = self.event(text="拍个合影看看")
        await self.begin_turn(first)
        self.runtime._current_life_appearance_snapshot = AsyncMock(return_value="")
        options = {
            "subject_route": "group",
            "participants": ["profile:friend"],
            "friend_outfit": "白色短袖搭配深灰工装长裤",
            "friend_hair": "黑色自然短发",
            "friend_scene_category": "outdoor",
        }
        for changed, expected in (
            ({"friend_scene_category": "street_walk"}, ["friend_scene_category"]),
            ({"friend_scene_category": ""}, ["friend_scene_category"]),
            ({"friend_outfit": ""}, ["friend_outfit"]),
            ({"friend_hair": ""}, ["friend_hair"]),
        ):
            with self.subTest(changed=changed):
                result = json.loads(await self.runtime.life_image_generate(
                    first, "人物 A 和人物 B 在街边随手自拍", **(options | changed)
                ))
                self.assertEqual(result["status"], "needs_parameters")
                self.assertEqual(result["required_parameters"], expected)
                self.assertFalse(hasattr(first, "_daily_life_image_request"))
                self.assertEqual(self.calls, [])
        self.runtime._current_life_appearance_snapshot.assert_not_awaited()

        async def group_image(prompt, participants, aspect_ratio="", **kwargs):
            self.calls.append(("group", prompt, participants))
            self.started.set()
            await self.release.wait()
            return types.SimpleNamespace(path=self.path)

        self.runtime.media.image.generate_group_image = group_image
        self.runtime._resolve_life_identity_profiles = AsyncMock(return_value={})
        self.runtime._build_person_fact_injection_context = AsyncMock(return_value="")
        prepare_look = AsyncMock(wraps=self.runtime._prepare_friend_daily_look)
        self.runtime._prepare_friend_daily_look = prepare_look
        accepted = json.loads(await self.runtime.life_image_generate(
            first, "人物 A 和人物 B 在街边随手自拍", **options
        ))
        self.assertEqual(accepted["status"], "pending")
        await asyncio.wait_for(self.started.wait(), 0.5)
        await asyncio.wait_for(self.begin_turn(self.event("43", "123 茄子")), 0.5)
        await self.finish_tasks()
        self.assertEqual(first._daily_life_image_request["status"], "sent")
        self.assertEqual(len(first.sent_messages), 1)
        self.assertEqual(self.calls[0][0], "group")
        self.assertIn(options["friend_outfit"], self.calls[0][1])
        self.assertEqual(self.calls[0][2], options["participants"])
        prepare_look.assert_awaited_once()

    async def test_group_image_missing_participant_does_not_start_background_task(self):
        first = self.event(text="拍张合影")
        for participants in ([], ["friend:1", "friend:2"]):
            result = await self.runtime.life_image_generate(
                first, "街边合影", subject_route="group", participants=participants
            )
            self.assertIn("选择一位", result)
            self.assertFalse(hasattr(first, "_daily_life_image_request"))
        self.assertEqual(self.calls, [])

    async def test_group_image_omitted_participant_is_locked_before_background_generation(self):
        first = self.event(text="拍张我们两个的合影")
        await self.begin_turn(first)
        profile_id = first.get_sender_id()
        self.runtime.media.image.friend_reference_options = lambda: [
            {"profile_id": profile_id, "display_name": "当前好友"}
        ]
        self.runtime._current_life_appearance_snapshot = AsyncMock(return_value="")
        self.runtime._resolve_life_identity_profiles = AsyncMock(return_value={})
        self.runtime._build_person_fact_injection_context = AsyncMock(return_value="")

        async def group_image(prompt, participants, aspect_ratio="", **kwargs):
            self.calls.append(("group", participants))
            self.started.set()
            await self.release.wait()
            return types.SimpleNamespace(path=self.path)

        self.runtime.media.image.generate_group_image = group_image
        result = json.loads(await self.runtime.life_image_generate(
            first,
            "人物 A 与人物 B 的客厅双人自拍",
            subject_route="group",
            friend_outfit="灰色短袖和家居长裤",
            friend_hair="黑色自然短发",
            friend_scene_category="home",
        ))
        self.assertEqual(result["status"], "pending")
        first._sender_id = "another-user"
        await asyncio.wait_for(self.started.wait(), 0.5)
        await self.finish_tasks()
        self.assertEqual(self.calls, [("group", [profile_id])])
        self.assertEqual(first._daily_life_image_request["status"], "sent")
        self.assertEqual(len(first.sent_messages), 1)

    async def test_image_invalid_request_never_returns_pending_or_releases_chat(self):
        self.runtime._release_media_chat_turn = Mock()
        self.runtime._historical_life_appearance_snapshot = AsyncMock(return_value="")
        self.runtime._part_runtime_image_media_component._resolve_last_reverse_image_prompt_async = AsyncMock(return_value=("", ""))
        for options, expected in (
            ({"subject_route": "current_character", "historical_target": "昨天"}, "历史造型"),
            ({"current_outfit_change": True}, "subject_route"),
            ({"current_outfit_change": True, "subject_route": "current_character",
              "historical_target": "昨天"}, "历史回现不会改变"),
            ({"use_last_reverse_prompt": True}, "反推提示词"),
        ):
            with self.subTest(options=options):
                event = self.event()
                result = await self.runtime.life_image_generate(event, "窗边生活照", **options)
                self.assertIn(expected, result)
                self.assertFalse(hasattr(event, "_daily_life_image_request"))
        empty = self.event(text="")
        self.assertIn("没有收到", await self.runtime.life_image_generate(empty, ""))
        self.runtime._release_media_chat_turn.assert_not_called()
        self.assertEqual(self.calls, [])

    async def test_historical_image_uses_accepted_snapshot_after_background_wait(self):
        first = self.event(text="看看昨天的穿搭")
        self.runtime._historical_life_appearance_snapshot = AsyncMock(
            side_effect=["昨天记录的浅灰棉质睡裙", "后来才写入的其他穿搭"]
        )
        self.runtime._part_runtime_image_media_component._execute_image_generation_plan = AsyncMock(
            return_value=image_module.ImageGenerationExecution(
                generated=types.SimpleNamespace(path=self.path), directed_prompt="昨天的照片"
            )
        )
        result = await self.runtime.life_image_generate(
            first, "客厅生活照", subject_route="current_character", historical_target="昨天"
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        await self.finish_tasks()
        plan = self.runtime._part_runtime_image_media_component._execute_image_generation_plan.call_args.args[1]
        self.assertIn("昨天记录的浅灰棉质睡裙", plan.prompt)
        self.assertNotIn("后来才写入", plan.prompt)
        self.runtime._historical_life_appearance_snapshot.assert_awaited_once()
        self.assertEqual(first._daily_life_image_request["status"], "sent")

    async def test_edit_missing_reference_is_repairable_before_acceptance(self):
        first = self.event(text="把这张调亮一点")
        for options, expected in (
            ({}, "请先发送"),
            ({"continue_last_result": True}, "没有可继续修改"),
            ({"reference_image": "missing.png"}, "没有可继续修改"),
            ({"generate_without_reference": True, "participants": ["profile:friend"]},
             "life_image_generate"),
            ({"participants": ["profile:friend", "profile:other"]}, "选择一位"),
        ):
            with self.subTest(options=options):
                result = await self.runtime.edit_life_image(first, "柔和一点", **options)
                self.assertIn(expected, result)
                self.assertFalse(hasattr(first, "_daily_life_image_request"))
                self.assertEqual(self.calls, [])
        first.message_items.append({"type": "image", "file": str(self.path)})
        result = await self.runtime.edit_life_image(first, "柔和一点")
        self.assertEqual(json.loads(result)["status"], "pending")
        await self.finish_tasks()
        self.assertEqual(first._daily_life_image_request["status"], "sent")

    async def test_native_video_without_source_is_rejected_before_acceptance(self):
        first = self.event(text="看看视频里是什么")
        self.runtime._sight_clips_from_event_async = AsyncMock(return_value=[])
        self.runtime._sight_recent_for_event = AsyncMock(return_value=[])
        self.runtime._submit_background_tool = Mock()
        result = await self.runtime._submit_native_video_tool(first, "")
        self.assertEqual(result.status, "failed")
        self.assertIn("没有找到", result)
        self.runtime._submit_background_tool.assert_not_called()

    async def test_edit_keeps_original_reference_while_later_sticker_is_processed(self):
        first = self.event(text="把这张调亮一点", image=str(self.path))
        await self.begin_turn(first)
        result = await asyncio.wait_for(
            self.runtime.edit_life_image(first, "窗边照片调亮"), timeout=0.5
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        await asyncio.wait_for(self.started.wait(), timeout=0.5)
        first.message_items[0]["file"] = "later-mutated-image.png"
        first.message_str = "被修改的旧事件文本"
        later = self.event(
            "43", "给你一个表情包", image="https://example.com/sticker.png"
        )
        await asyncio.wait_for(self.begin_turn(later), timeout=0.5)
        self.assertTrue(later.get_messages())
        self.assertFalse(first.get_extra("agent_stop_requested", False))
        self.assertEqual(self.calls[0][2], str(self.path))
        self.assertFalse(self.runtime.media_request_is_current_turn(first))
        await self.finish_tasks()
        self.assertEqual(len(first.sent_messages), 1)
        self.assertEqual(later.sent_messages, [])

    async def test_edit_locks_last_result_before_background_queue_changes_it(self):
        first = self.event(text="继续改刚才那张")
        self.runtime._remember_life_image_for_scope(first.unified_msg_origin, self.path)
        result = await self.runtime.edit_life_image(
            first, "柔和一点", continue_last_result=True
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        another = self.path.with_name("another.png")
        another.write_bytes(b"another synthetic image")
        self.runtime._remember_life_image_for_scope(first.unified_msg_origin, another)
        await asyncio.wait_for(self.started.wait(), timeout=0.5)
        self.assertEqual(self.calls[0][2], str(self.path))
        await self.finish_tasks()

    async def test_cached_reference_survives_original_temporary_file_cleanup(self):
        first = self.event(text="照着这张画", image="/temporary/soon-deleted.png")
        setattr(
            first,
            self.runtime._PREPARED_VISUAL_MEDIA_ATTR,
            [{"item": first.message_items[0], "path": str(self.path)}],
        )
        result = await self.runtime.edit_life_image(first, "调整照片")
        self.assertEqual(json.loads(result)["status"], "pending")
        first.message_items.clear()
        await asyncio.wait_for(self.started.wait(), timeout=0.5)
        self.assertEqual(self.calls[0][2], str(self.path))
        await self.finish_tasks()

    async def test_image_prompt_and_appearance_are_frozen_before_queue(self):
        first = self.event(text="拍张现在的照片")
        self.runtime._current_life_appearance_snapshot = lambda route: async_return(
            "蓝色外套"
        )
        self.runtime._align_current_appearance_scene_prompt = (
            lambda prompt, *args, **kwargs: async_return(prompt)
        )
        result = await self.runtime.life_image_generate(
            first, "角色在窗边", subject_route="current_character"
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        first.message_str = "后来的红色裙子"
        self.runtime._life_media_source_events = {
            first.unified_msg_origin: {
                "text": "后来的一段非常详细而且完全不同的红色裙子图片生成要求" * 10,
                "timestamp": time.monotonic(),
            }
        }
        self.runtime._current_life_appearance_snapshot = lambda route: async_return(
            "红色裙子"
        )
        await asyncio.wait_for(self.started.wait(), timeout=0.5)
        self.assertIn("蓝色外套", self.calls[0][1])
        self.assertNotIn("红色裙子", self.calls[0][1])
        await self.finish_tasks()

    async def test_image_completion_does_not_remove_new_runner_tools(self):
        first = self.event()
        later = self.event("43", "再聊聊")
        runner = types.SimpleNamespace(
            run_context=types.SimpleNamespace(
                context=types.SimpleNamespace(event=first)
            )
        )
        runners = {first.unified_msg_origin: runner}
        with patch.object(
            image_module,
            "_astrbot_follow_up",
            types.SimpleNamespace(_ACTIVE_AGENT_RUNNERS=runners),
        ):
            await self.runtime.life_image_generate(first, "窗边照片")
            self.assertTrue(runner._daily_life_direct_image_tools_sent)
            new_runner = types.SimpleNamespace(
                run_context=types.SimpleNamespace(
                    context=types.SimpleNamespace(event=later)
                )
            )
            runners[first.unified_msg_origin] = new_runner
            await self.finish_tasks()
            self.assertFalse(hasattr(new_runner, "_daily_life_direct_image_tools_sent"))

    async def test_media_submission_releases_unconsumed_framework_followups(self):
        first = self.event()
        await self.begin_turn(first)
        ticket = types.SimpleNamespace(resolved=asyncio.Event(), consumed=False)
        runners = {}

        def resolve_followups():
            ticket.resolved.set()

        runner = types.SimpleNamespace(
            run_context=types.SimpleNamespace(
                context=types.SimpleNamespace(event=first)
            ),
            _resolve_unconsumed_follow_ups=Mock(side_effect=resolve_followups),
            request_stop=Mock(),
        )
        runners[first.unified_msg_origin] = runner

        def unregister(scope, expected):
            if runners.get(scope) is expected:
                runners.pop(scope)

        follow_up = types.SimpleNamespace(
            _ACTIVE_AGENT_RUNNERS=runners,
            unregister_active_runner=Mock(side_effect=unregister),
        )
        self.runtime._follow_up_module = lambda: follow_up
        with patch.object(image_module, "_astrbot_follow_up", follow_up):
            result = await self.runtime.life_image_generate(first, "窗边照片")
            self.assertEqual(json.loads(result)["status"], "pending")
            self.assertTrue(ticket.resolved.is_set())
            self.assertFalse(ticket.consumed)
            runner._resolve_unconsumed_follow_ups.assert_called_once_with()
            follow_up.unregister_active_runner.assert_called_once_with(
                first.unified_msg_origin, runner
            )
            runner.request_stop.assert_not_called()
            self.assertNotIn(first.unified_msg_origin, runners)
            await asyncio.wait_for(
                self.begin_turn(self.event("43", "继续聊")), timeout=0.5
            )
            await self.finish_tasks()

    async def test_media_release_leaves_another_turn_runner_registered(self):
        first = self.event()
        later = self.event("43", "下一轮")
        runner = types.SimpleNamespace(
            run_context=types.SimpleNamespace(
                context=types.SimpleNamespace(event=later)
            ),
            _resolve_unconsumed_follow_ups=Mock(),
        )
        follow_up = types.SimpleNamespace(
            _ACTIVE_AGENT_RUNNERS={first.unified_msg_origin: runner},
            unregister_active_runner=Mock(),
        )
        self.runtime._follow_up_module = lambda: follow_up
        with patch.object(image_module, "_astrbot_follow_up", follow_up):
            self.runtime._release_media_chat_turn(first)
        runner._resolve_unconsumed_follow_ups.assert_not_called()
        follow_up.unregister_active_runner.assert_not_called()

    async def test_ordinary_image_followup_still_stops_original_text_runner(self):
        first = self.event(text="按照参考图画")
        await self.begin_turn(first)
        runner = types.SimpleNamespace(
            run_context=types.SimpleNamespace(
                context=types.SimpleNamespace(event=first)
            ),
            request_stop=Mock(),
        )
        follow_up = types.SimpleNamespace(
            _ACTIVE_AGENT_RUNNERS={first.unified_msg_origin: runner}
        )
        later = self.event("43", "", image=str(self.path))
        with patch.object(image_module, "_astrbot_follow_up", follow_up):
            self.assertTrue(self.runtime.note_continuous_turn_incoming(later))
        runner.request_stop.assert_called_once_with()
        self.assertTrue(first.get_extra("agent_stop_requested"))

    async def test_image_final_text_hold_applies_only_to_original_tool_turn(self):
        first = self.event()
        later = self.event("43", "继续聊")
        await self.runtime.life_image_generate(first, "窗边照片")
        for completed in (False, True):
            if completed:
                await self.finish_tasks()
            first.set_result(first.chain_result(["原轮次的确认语"]))
            self.assertTrue(self.runtime.hold_life_image_final_text(first))
            self.assertIsNone(first.get_result())
            later.set_result(later.chain_result(["新轮次的回复"]))
            self.assertFalse(self.runtime.hold_life_image_final_text(later))
            self.assertIsNotNone(later.get_result())

    async def test_duplicate_pending_image_does_not_start_or_report_sent(self):
        first = self.event()
        await self.runtime.life_image_generate(first, "窗边照片")
        result = await self.runtime.edit_life_image(first, "调整刚才那张")
        self.assertEqual(json.loads(result)["status"], "pending")
        self.assertTrue(json.loads(result)["deduplicated"])
        self.assertEqual(len(self.runtime._background_scheduler_for_runtime().tasks), 1)
        await self.finish_tasks()

    async def test_full_media_queue_rejects_image_without_creating_coroutine_leak(self):
        self.runtime._background_scheduler = BackgroundTaskScheduler(
            video_limit=1, video_backlog_limit=0
        )
        first = self.event()
        await self.runtime.life_image_generate(first, "窗边照片")
        later = self.event("43", "另一张照片")
        result = await self.runtime.life_image_generate(later, "花园照片")
        self.assertEqual(json.loads(result)["status"], "failed")
        self.assertFalse(hasattr(later, "_daily_life_image_request"))
        later.set_result(later.chain_result(["可以继续聊天。 "]))
        self.assertFalse(self.runtime.hold_life_image_final_text(later))
        await self.finish_tasks()

    async def test_recalled_image_cancels_delivery_and_followup(self):
        first = self.event()
        await self.runtime.life_image_generate(first, "窗边照片")
        self.runtime.send_message_if_not_recalled = lambda *args, **kwargs: (
            async_return(False)
        )
        await self.finish_tasks()
        self.assertEqual(first._daily_life_image_request["status"], "cancelled")
        self.assertEqual(first.sent_messages, [])

    async def test_image_failure_keeps_new_chat_and_does_not_send_old_error(self):
        async def fail(*args, **kwargs):
            raise RuntimeError("synthetic generation failure")

        self.runtime.media.image.generate_image = fail
        first = self.event()
        await self.begin_turn(first)
        await self.runtime.life_image_generate(first, "窗边照片")
        later = self.event("43", "继续刚才的话题")
        await self.begin_turn(later)
        await self.finish_tasks()
        self.assertEqual(first._daily_life_image_request["status"], "failed")
        self.assertEqual(first.sent_messages, [])

    def prepare_failure_reply(self, reply="这张没拍成，有点可惜。"):
        self.runtime.get_text_provider = AsyncMock(return_value=object())
        self.runtime.get_persona_text = AsyncMock(return_value="我是李知恬，说话轻松自然。")
        self.runtime.call_text_model = AsyncMock(
            return_value=json.dumps({"reply_text": reply}, ensure_ascii=False)
        )
        self.runtime._append_assistant_history = AsyncMock()
        self.runtime.__dict__.pop("send_background_text", None)
        self.runtime.send_background_text = AsyncMock(
            wraps=self.runtime.send_background_text
        )

    async def test_image_failure_uses_role_reply_and_records_actual_sent_text(self):
        self.prepare_failure_reply()
        self.runtime.media.image.generate_image = AsyncMock(
            side_effect=RuntimeError("HTTP 500：测试失败正文")
        )
        first = self.event(text="拍张窗边的照片")
        await self.begin_turn(first)
        result = await self.runtime.life_image_generate(first, "窗边照片")
        self.assertEqual(json.loads(result)["status"], "pending")
        await self.finish_tasks()

        self.assertEqual(first._daily_life_image_request["status"], "failed")
        self.assertEqual(first.sent_messages[0].items, ["这张没拍成，有点可惜。"])
        self.runtime.call_text_model.assert_awaited_once()
        prompt = self.runtime.call_text_model.call_args.args[1]
        self.assertIn("拍张窗边的照片", prompt)
        self.assertIn("李知恬", prompt)
        self.assertEqual(
            self.runtime.send_background_text.call_args.kwargs["mode"],
            BackgroundTextMode.EXPRESSIVE,
        )
        self.runtime._append_assistant_history.assert_awaited_once_with(
            first.unified_msg_origin, "这张没拍成，有点可惜。"
        )

    async def test_background_tool_returned_and_raised_failures_use_natural_reply(self):
        for task_name, tool_name, raises in (
            ("视频理解", "life_video_understand", False),
            ("视频总结", "life_video_note", True),
            ("衣橱学习", "life_style_learn", False),
        ):
            with self.subTest(task_name=task_name):
                self.prepare_failure_reply("这次没弄好，有点可惜。")
                first = self.event(text="帮我看看这个")
                marker = {"status": "pending", "media": "test"}
                self.runtime.finish_tool_reaction = AsyncMock()
                work = AsyncMock(
                    side_effect=RuntimeError("测试内部异常") if raises else None,
                    return_value=ToolResultText("测试内部失败", status="failed"),
                )
                await self.runtime._part_runtime_media_common_component._deliver_background_tool(
                    first, work, marker, tool_name, task_name
                )
                self.assertEqual(marker["status"], "failed")
                self.assertEqual(first.sent_messages[0].items, ["这次没弄好，有点可惜。"])
                self.assertIn(task_name, self.runtime.call_text_model.call_args.args[1])
                self.runtime.finish_tool_reaction.assert_awaited_once_with(
                    first, tool_name, success=False
                )

    async def test_image_needs_parameters_asks_in_role_instead_of_sending_json(self):
        self.prepare_failure_reply("你今天穿什么呀？我还不知道呢。")
        first = self.event(text="拍张我们的合影")
        result = json.dumps(
            {"status": "needs_parameters", "missing": ["friend_outfit", "friend_hair"]}
        )
        await self.runtime._part_runtime_image_media_component._background_image_delivery(
            first,
            AsyncMock(return_value=result),
            {"status": "pending", "action": "generate"},
            "life_image_generate",
        )
        self.assertEqual(first.sent_messages[0].items, ["你今天穿什么呀？我还不知道呢。"])
        self.assertIn("friend_outfit", self.runtime.call_text_model.call_args.args[1])

    async def test_failure_reply_does_not_block_new_chat_or_send_after_new_message(self):
        self.prepare_failure_reply()

        async def reply(*args, **kwargs):
            self.started.set()
            await self.release.wait()
            return '{"reply_text":"这张没拍好。"}'

        self.runtime.call_text_model.side_effect = reply
        self.runtime.media.image.generate_image = AsyncMock(side_effect=RuntimeError("测试失败"))
        first = self.event()
        await self.begin_turn(first)
        await self.runtime.life_image_generate(first, "窗边照片")
        await asyncio.wait_for(self.started.wait(), 0.5)
        await asyncio.wait_for(self.begin_turn(self.event("43", "继续聊")), 0.5)
        await self.finish_tasks()
        self.assertEqual(first.sent_messages, [])
        self.runtime._append_assistant_history.assert_not_awaited()

    async def test_failure_reply_recall_during_model_wait_cancels_send(self):
        self.prepare_failure_reply()
        first = self.event()

        async def reply(*args, **kwargs):
            self.runtime.can_send_for_source = lambda *args, **kwargs: False
            return '{"reply_text":"这次没拍成。"}'

        self.runtime.call_text_model.side_effect = reply
        sent = await self.runtime._send_background_failure_reply(
            first.unified_msg_origin, first, task_name="图片生成", failure_reason="测试失败"
        )
        self.assertFalse(sent)
        self.assertEqual(first.sent_messages, [])

    async def test_failure_reply_model_unavailable_keeps_internal_error_out_of_fallback(self):
        for raw in ("", "不是协议格式", '{"reply_text":"","reason":"内部分析"}'):
            with self.subTest(raw=raw):
                self.prepare_failure_reply()
                self.runtime.call_text_model.return_value = raw
                first = self.event()
                await self.runtime._send_background_failure_reply(
                    first.unified_msg_origin,
                    first,
                    task_name="图片生成",
                    failure_reason="https://provider.invalid：HTTP 500",
                )
                self.assertEqual(first.sent_messages[0].items, ["这次没弄成，有点可惜。"])
        self.prepare_failure_reply()
        self.runtime.get_text_provider.return_value = None
        first = self.event()
        await self.runtime._send_background_failure_reply(
            first.unified_msg_origin, first, task_name="衣橱学习", failure_reason="测试失败"
        )
        self.runtime.call_text_model.assert_not_awaited()
        self.assertEqual(first.sent_messages[0].items, ["这次没弄成，有点可惜。"])

    async def test_failure_reply_timeout_cancels_model_without_holding_background_slot(self):
        self.prepare_failure_reply()
        cancelled = asyncio.Event()

        async def hang(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.runtime.call_text_model.side_effect = hang
        timeout = asyncio.timeout
        with patch("core.runtime.channel.summary.asyncio.timeout", side_effect=lambda _: timeout(0.01)):
            first = self.event()
            await asyncio.wait_for(
                self.runtime._send_background_failure_reply(
                    first.unified_msg_origin, first, task_name="视频总结", failure_reason="测试失败"
                ),
                0.5,
            )
        self.assertTrue(cancelled.is_set())
        self.assertEqual(first.sent_messages[0].items, ["这次没弄成，有点可惜。"])

    async def test_failure_reply_task_cancel_does_not_send_fallback(self):
        self.prepare_failure_reply()
        self.runtime.call_text_model.side_effect = asyncio.CancelledError()
        first = self.event()
        with self.assertRaises(asyncio.CancelledError):
            await self.runtime._send_background_failure_reply(
                first.unified_msg_origin, first, task_name="图片生成", failure_reason="测试失败"
            )
        self.assertEqual(first.sent_messages, [])

    async def test_failure_reply_delivery_error_does_not_retry_or_append_history(self):
        self.prepare_failure_reply()
        self.runtime.send_background_text.side_effect = RuntimeError("测试发送失败")
        first = self.event()
        sent = await self.runtime._send_background_failure_reply(
            first.unified_msg_origin, first, task_name="图片编辑", failure_reason="测试失败"
        )
        self.assertFalse(sent)
        self.runtime.send_background_text.assert_awaited_once()
        self.runtime._append_assistant_history.assert_not_awaited()

    async def test_sight_failure_is_discarded_if_new_chat_arrives_during_segmentation(self):
        from core.runtime.reply import SegmentPart, SemanticSegmentPlan

        self.prepare_failure_reply("这段我没看清，没法给你总结。")
        self.runtime._semantic_segment_enabled = lambda: True
        first = self.event()
        await self.begin_turn(first)

        async def segment(*args, **kwargs):
            await self.begin_turn(self.event("43", "继续聊"))
            return SemanticSegmentPlan((SegmentPart("这段我没看清，没法给你总结。"),), valid=True)

        self.runtime._part_semantic_segment_runtime_component._semantic_segment_plan_text = segment
        sent = await self.runtime._send_background_failure_reply(
            first.unified_msg_origin,
            first,
            task_name="B站视频总结",
            failure_reason="没有抽取到可用画面",
            source="sight_failure",
        )
        self.assertFalse(sent)
        self.assertEqual(first.sent_messages, [])

    async def test_background_cancellation_is_clean(self):
        first = self.event()
        await self.runtime.life_image_generate(first, "窗边照片")
        await asyncio.wait_for(self.started.wait(), timeout=0.5)
        await self.runtime._cancel_background_tasks()
        self.assertEqual(first._daily_life_image_request["status"], "cancelled")
        self.assertEqual(first.sent_messages, [])

    async def test_video_allows_new_chat_and_later_sticker_does_not_change_first_frame(
        self,
    ):
        first = self.event(text="拍个生活视频")
        await self.begin_turn(first)
        self.release.set()
        result = await self.runtime.life_video_generate(first, "窗边挥手视频")
        self.assertEqual(json.loads(result)["status"], "pending")
        await asyncio.wait_for(self.video_started.wait(), timeout=0.5)
        later = self.event("43", "继续聊", image="https://example.com/sticker.png")
        await asyncio.wait_for(self.begin_turn(later), timeout=0.5)
        later.set_result(later.chain_result(["好，继续。 "]))
        self.assertFalse(self.runtime.hold_life_video_final_text(later))
        self.assertFalse(first.get_extra("agent_stop_requested", False))
        self.assertEqual(
            [call for call in self.calls if call[0] == "video"][0][2], b"frame"
        )
        await self.finish_tasks()
        self.assertEqual(len(first.sent_messages), 1)
        self.assertEqual(later.sent_messages, [])

    async def test_video_and_suite_appearance_alignment_runs_in_background(self):
        for media_type in ("video", "suite"):
            with self.subTest(media_type=media_type):
                started = asyncio.Event()
                release = asyncio.Event()

                async def align(prompt, source_request, route):
                    started.set()
                    await release.wait()
                    return "窗边自然挥手"

                self.runtime._current_life_appearance_snapshot = lambda route: (
                    async_return("蓝色外套")
                )
                self.runtime._align_current_appearance_scene_prompt = align
                self.runtime._photo_suite_plan = lambda event, prompt, count, **kwargs: (
                    async_return(
                        self.runtime._photo_suite_fallback_plan(
                            prompt, count, "current_character"
                        )
                    )
                )
                self.runtime.data_path = self.path.parent / "daily_life.db"
                first = self.event(f"request-{media_type}")
                await self.begin_turn(first)
                tool = (
                    self.runtime.life_video_generate
                    if media_type == "video"
                    else self.runtime.life_photo_suite_generate
                )
                result = await asyncio.wait_for(
                    tool(first, "窗边挥手", subject_route="current_character"),
                    timeout=0.5,
                )
                self.assertEqual(json.loads(result)["status"], "pending")
                await asyncio.wait_for(started.wait(), timeout=0.5)
                await asyncio.wait_for(
                    self.begin_turn(self.event(f"later-{media_type}", "继续聊天")),
                    timeout=0.5,
                )
                self.assertEqual(self.calls, [])
                await self.runtime._cancel_background_tasks()

    async def test_suite_locks_last_image_before_waiting_for_background_slot(self):
        self.runtime.data_path = self.path.parent / "daily_life.db"
        first = self.event()
        await self.runtime.life_image_generate(first, "窗边照片")
        await asyncio.wait_for(self.started.wait(), timeout=0.5)
        suite = self.event("43", "沿用上一张拍一套")
        self.runtime._remember_life_image_for_scope(suite.unified_msg_origin, self.path)
        captures = []

        async def prepare(event, *args, **kwargs):
            captures.append(event._daily_life_locked_suite_reference)
            raise asyncio.CancelledError

        self.runtime._photo_suite_prepare_generation = prepare
        result = await self.runtime.life_photo_suite_generate(
            suite, "窗边套图", continue_last_result=True
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        another = self.path.with_name("another.png")
        another.write_bytes(b"another synthetic image")
        self.runtime._remember_life_image_for_scope(suite.unified_msg_origin, another)
        self.release.set()
        await asyncio.gather(
            *list(self.runtime._background_scheduler_for_runtime().tasks),
            return_exceptions=True,
        )
        self.assertEqual(captures, [str(self.path)])

    async def test_video_and_suite_holds_are_bound_to_original_event_only(self):
        first = self.event()
        later = self.event("43", "随便聊两句")
        wrapped = types.SimpleNamespace(context=types.SimpleNamespace(event=first))
        self.runtime._part_runtime_video_media_component._register_life_video_request(
            first.unified_msg_origin, "窗边视频", wrapped
        )
        self.runtime._photo_suite_register_request(
            first.unified_msg_origin, "窗边套图", wrapped, self.path.parent
        )
        for holder in (
            self.runtime.hold_life_video_final_text,
            self.runtime.hold_life_photo_suite_final_text,
        ):
            first.set_result(first.chain_result(["原始请求确认语"]))
            self.assertTrue(holder(first))
            later.set_result(later.chain_result(["后续正常回复"]))
            self.assertFalse(holder(later))
            self.assertIsNotNone(later.get_result())

    def prepare_suite_planner(self):
        self.runtime._photo_suite_plan = lambda event, prompt, count, **kwargs: (
            async_return(self.runtime._photo_suite_fallback_plan(prompt, count, "free"))
        )
        self.runtime.get_persona_text = AsyncMock(return_value="自然简短地接话。")
        self.runtime._photo_suite_send_followup = AsyncMock(return_value=True)

    async def test_suite_unconfirmed_submissions_finish_and_record_truthful_outcome(self):
        self.prepare_suite_planner()
        calls = []

        async def uncertain(*args, **kwargs):
            calls.append(args)
            raise ImageTaskError("提交结果未确认", request_id=f"trace-{len(calls)}", phase="提交")

        self.runtime.media.image.generate_image = uncertain
        self.runtime.finish_tool_reaction = AsyncMock()
        event = self.event("suite-unknown", "拍套图看看")
        await self.begin_turn(event)
        result = await self.runtime.life_photo_suite_generate(event, "窗边生活抓拍")
        self.assertEqual(json.loads(result)["status"], "pending")
        await self.finish_tasks()
        path, manifest = await self.runtime._photo_suite_latest_manifest(event.unified_msg_origin)
        self.assertEqual(manifest["status"], "unconfirmed")
        self.assertEqual([s["status"] for s in manifest["shots"]], ["unconfirmed"] * 3)
        self.assertEqual([s["attempts"] for s in manifest["shots"]], [1] * 3)
        self.assertEqual(len(calls), 3)
        self.assertEqual(event.sent_messages, [])
        self.runtime._photo_suite_send_followup.assert_awaited_once()
        kwargs = self.runtime._photo_suite_send_followup.await_args.kwargs
        self.assertEqual((kwargs["pending_count"], kwargs["unconfirmed_count"]), (0, 3))
        self.runtime.finish_tool_reaction.assert_awaited_once()
        reaction = self.runtime.finish_tool_reaction.await_args
        self.assertEqual(reaction.args[0].message_id, event.message_id)
        self.assertEqual(reaction.args[1], "life_photo_suite_generate")
        self.assertFalse(reaction.kwargs["success"])
        facts = "\n".join(m.content for m in self.runtime._structured_scope_messages(event.unified_msg_origin))
        self.assertIn("已登记原任务查询 0 张", facts)
        self.assertIn("提交结果未确认且无自动恢复 3 张", facts)

        copied = self.event("suite-unknown", "拍套图看看")
        repeated = await self.runtime.life_photo_suite_generate(copied, "窗边生活抓拍")
        self.assertEqual(json.loads(repeated)["status"], "unconfirmed")
        self.assertEqual(len(calls), 3)

    async def test_suite_partial_success_preserves_unconfirmed_slots_and_sends_only_new_images(self):
        self.prepare_suite_planner()
        original = self.runtime.media.image.generate_image
        attempts = 0

        async def generate(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise ImageTaskError("提交回执未取得", request_id="trace-2")
            return await original(*args, **kwargs)

        self.runtime.media.image.generate_image = generate
        event = self.event("suite-partial", "拍套图看看")
        await self.begin_turn(event)
        await self.runtime.life_photo_suite_generate(event, "窗边生活抓拍")
        await self.finish_tasks()
        _, manifest = await self.runtime._photo_suite_latest_manifest(event.unified_msg_origin)
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual([s["status"] for s in manifest["shots"]], ["sent", "unconfirmed", "sent"])
        self.assertEqual(len(event.sent_messages), 2)
        self.assertEqual(attempts, 3)
        self.assertEqual(self.runtime._photo_suite_send_followup.await_args.kwargs["unconfirmed_count"], 1)

    async def test_suite_retry_does_not_resubmit_positions_still_under_original_query(self):
        self.prepare_suite_planner()
        event = self.event("suite-original", "拍套图看看")
        await self.runtime.life_photo_suite_generate(event, "窗边生活抓拍")
        await self.finish_tasks()
        path, manifest = await self.runtime._photo_suite_latest_manifest(event.unified_msg_origin)
        manifest["status"] = "partial"
        manifest["shots"][1].update(status="pending", path="", task_id="original-task")
        await self.runtime._photo_suite_write_manifest(path, manifest)
        result = await self.runtime.life_photo_suite_generate(self.event("suite-retry", "重试第二张"), retry_indexes=[2])
        self.assertEqual(json.loads(result)["pending_indexes"], [2])
        await self.finish_tasks()
        self.assertEqual(len(self.calls), 3)

    async def test_suite_unconfirmed_outcome_reaches_model_and_natural_reply(self):
        self.prepare_suite_planner()
        del self.runtime._photo_suite_send_followup
        self.runtime.get_text_provider = AsyncMock(return_value=object())
        self.runtime.call_text_model = AsyncMock(return_value='{"reply_text":"这次照片没拿到，先不吊你胃口啦"}')
        self.runtime.send_background_text = AsyncMock(return_value=True)
        self.runtime._append_assistant_history = AsyncMock()
        event = self.event("suite-reply", "拍套图看看")
        await self.begin_turn(event)
        sent = await self.runtime._photo_suite_send_followup(
            event.unified_msg_origin, event, prompt="窗边生活抓拍", sent_count=0,
            requested_count=3, total_count=3, failed_indexes=[1, 2, 3],
            is_retry=False, unconfirmed_count=3,
        )
        self.assertTrue(sent)
        prompt = self.runtime.call_text_model.await_args.args[1]
        self.assertIn("提交结果未确认且没有自动恢复：3 张", prompt)
        self.runtime.send_background_text.assert_awaited_once()
        self.assertEqual(self.runtime.send_background_text.await_args.args[1], "这次照片没拿到，先不吊你胃口啦")

    async def test_suite_failure_during_later_chat_records_result_without_interrupting_chat(self):
        self.prepare_suite_planner()
        del self.runtime._photo_suite_send_followup
        started, release = asyncio.Event(), asyncio.Event()

        async def uncertain(*args, **kwargs):
            started.set()
            await release.wait()
            raise ImageTaskError("提交结果未确认", phase="提交")

        self.runtime.media.image.generate_image = uncertain
        self.runtime.send_background_text = AsyncMock(return_value=True)
        first = self.event("suite-first", "拍套图看看")
        await self.begin_turn(first)
        await self.runtime.life_photo_suite_generate(first, "窗边生活抓拍")
        await asyncio.wait_for(started.wait(), timeout=0.5)
        later = self.event("suite-later", "我们接着聊电影")
        await asyncio.wait_for(self.begin_turn(later), timeout=0.5)
        release.set()
        await self.finish_tasks()
        self.runtime.send_background_text.assert_not_awaited()
        _, manifest = await self.runtime._photo_suite_latest_manifest(first.unified_msg_origin)
        self.assertEqual(manifest["status"], "unconfirmed")
        self.assertEqual(later.sent_messages, [])

    async def test_later_affection_does_not_create_another_suite(self):
        self.prepare_suite_planner()
        first = self.event("suite-first", "拍套图看看")
        await self.begin_turn(first)
        await self.runtime.life_photo_suite_generate(first, "暖光卧室生活抓拍")
        await asyncio.wait_for(self.started.wait(), timeout=0.5)
        later = self.event("suite-later", "就知道你最好了，mua")
        await self.begin_turn(later)
        later.set_result(later.chain_result(["收到你的亲亲啦"]))
        self.assertFalse(self.runtime.hold_life_photo_suite_final_text(later))
        self.assertIsNotNone(later.get_result())
        await self.finish_tasks()

        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(first.sent_messages), 3)
        self.assertEqual(later.sent_messages, [])
        root = self.path.parent / "generated" / "images" / "suites"
        manifests = [json.loads(p.read_text()) for p in root.glob("*/manifest.json")]
        self.assertEqual(
            [m["status"] for m in manifests], ["completed"]
        )
        _, latest = await self.runtime._photo_suite_latest_manifest(
            first.unified_msg_origin
        )
        self.assertEqual(latest["source_message_id"], "suite-first")

    async def test_explicit_same_theme_new_suite_is_allowed_after_delivery(self):
        self.prepare_suite_planner()
        first = self.event("suite-first", "拍套图看看")
        await self.begin_turn(first)
        await self.runtime.life_photo_suite_generate(first, "暖光卧室生活抓拍")
        await self.finish_tasks()
        later = self.event("suite-new", "再给我拍一套同样场景的")
        await self.begin_turn(later)
        self.runtime._media_director_call = AsyncMock(
            return_value={"create_new": True, "evidence_message_id": "suite-new"}
        )
        await self.runtime.life_photo_suite_generate(later, "暖光卧室生活抓拍")
        await self.finish_tasks()
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(len(first.sent_messages), 3)
        self.assertEqual(len(later.sent_messages), 3)
        self.runtime._media_director_call.assert_not_awaited()

    async def test_same_words_in_new_message_generate_suite_without_second_decision(self):
        self.prepare_suite_planner()
        first = self.event("suite-old", "拍套图看看")
        await self.begin_turn(first)
        await self.runtime.life_photo_suite_generate(first, "暖光卧室生活抓拍")
        await self.finish_tasks()
        path, manifest = await self.runtime._photo_suite_latest_manifest(first.unified_msg_origin)
        manifest["status"] = "partial"
        manifest["shots"][-1].update(status="failed", path="", error="生成失败")
        await self.runtime._photo_suite_write_manifest(path, manifest)
        self.runtime._media_director_call = AsyncMock(side_effect=TimeoutError())
        later = self.event("suite-current", "拍套图看看")
        await self.begin_turn(later)
        result = await asyncio.wait_for(
            self.runtime.life_photo_suite_generate(later, "暖光卧室生活抓拍"),
            timeout=0.5,
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        await self.finish_tasks()
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(len(later.sent_messages), 3)
        self.runtime._media_director_call.assert_not_awaited()

    async def test_suite_planning_wait_does_not_block_later_chat(self):
        self.prepare_suite_planner()
        first = self.event("suite-first", "拍套图看看")
        await self.begin_turn(first)
        planning = asyncio.Event()
        planned = asyncio.Event()

        async def plan(event, prompt, count, **kwargs):
            planning.set()
            await planned.wait()
            return self.runtime._photo_suite_fallback_plan(prompt, count, "free")

        self.runtime._photo_suite_plan = plan
        await asyncio.wait_for(
            self.runtime.life_photo_suite_generate(first, "暖光卧室生活抓拍"),
            timeout=0.5,
        )
        await asyncio.wait_for(planning.wait(), timeout=0.5)
        newest = self.event("suite-chat", "我们接着说刚才那件事")
        request = await asyncio.wait_for(self.begin_turn(newest), timeout=0.5)
        self.assertEqual(request.prompt, newest.message_str)
        newest.set_result(newest.chain_result(["好呀 接着说"]))
        self.assertFalse(self.runtime.hold_life_photo_suite_final_text(newest))
        self.assertEqual(self.calls, [])
        planned.set()
        await self.finish_tasks()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(first.sent_messages), 3)
        self.assertEqual(newest.sent_messages, [])

    async def test_copied_original_message_does_not_generate_again_after_cache_reset(self):
        self.prepare_suite_planner()
        first = self.event("suite-original", "拍套图看看")
        await self.begin_turn(first)
        await self.runtime.life_photo_suite_generate(first, "暖光卧室生活抓拍")
        await self.finish_tasks()
        self.runtime._life_photo_suite_last_tasks = {}
        copied = self.event("suite-original", "拍套图看看")
        await self.begin_turn(copied)
        result = await self.runtime.life_photo_suite_generate(copied, "暖光卧室生活抓拍")
        self.assertEqual(json.loads(result)["status"], "completed")
        self.assertTrue(json.loads(result)["deduplicated"])
        await self.finish_tasks()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(copied.sent_messages, [])

    async def test_failed_duplicate_reports_failure_without_submitting_again(self):
        self.prepare_suite_planner()
        first = self.event("suite-original", "拍套图看看")
        await self.begin_turn(first)
        await self.runtime.life_photo_suite_generate(first, "暖光卧室生活抓拍")
        await self.finish_tasks()
        path, manifest = await self.runtime._photo_suite_latest_manifest(first.unified_msg_origin)
        manifest["status"] = "failed"
        for shot in manifest["shots"]:
            shot.update(status="failed", path="", error="生成失败")
        await self.runtime._photo_suite_write_manifest(path, manifest)
        copied = self.event("suite-original", "拍套图看看")
        await self.begin_turn(copied)
        result = await self.runtime.life_photo_suite_generate(copied, "暖光卧室生活抓拍")
        self.assertEqual(json.loads(result)["status"], "failed")
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(copied.sent_messages, [])

    async def test_older_message_duplicate_does_not_replace_latest_suite(self):
        self.prepare_suite_planner()
        first = self.event("suite-original", "拍套图看看")
        await self.begin_turn(first)
        await self.runtime.life_photo_suite_generate(first, "暖光卧室生活抓拍")
        await self.finish_tasks()
        newer = self.event("suite-new", "再拍一套")
        await self.begin_turn(newer)
        await self.runtime.life_photo_suite_generate(newer, "窗边生活抓拍")
        await self.finish_tasks()
        copied = self.event("suite-original", "拍套图看看")
        await self.begin_turn(copied)
        result = await self.runtime.life_photo_suite_generate(copied, "暖光卧室生活抓拍")
        self.assertTrue(json.loads(result)["deduplicated"])
        _, latest = await self.runtime._photo_suite_latest_manifest(first.unified_msg_origin)
        self.assertEqual(latest["source_message_id"], "suite-new")
        self.assertEqual(len(self.calls), 6)

    async def test_concurrent_copies_of_same_message_submit_only_one_suite(self):
        self.prepare_suite_planner()
        first = self.event("suite-original", "拍套图看看")
        copied = self.event("suite-original", "拍套图看看")
        await self.begin_turn(first)
        results = await asyncio.wait_for(
            asyncio.gather(
                self.runtime.life_photo_suite_generate(first, "暖光卧室生活抓拍"),
                self.runtime.life_photo_suite_generate(copied, "暖光卧室生活抓拍"),
            ),
            timeout=0.5,
        )
        self.assertEqual([json.loads(value)["status"] for value in results], ["pending", "pending"])
        await self.finish_tasks()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(first.sent_messages) + len(copied.sent_messages), 3)

    async def test_text_followup_invalidates_media_reply_even_in_same_revision(self):
        first = self.event()
        await self.begin_turn(first)
        later = self.event("43", "补充一句")
        self.runtime.note_continuous_turn_incoming(later)
        self.assertTrue(self.runtime.continuous_turn_event_is_current(first))
        self.assertFalse(self.runtime.media_request_is_current_turn(first))

    async def test_media_reply_guard_works_when_continuous_turn_is_disabled(self):
        self.runtime.config = LifeSettings.from_dict(
            {"chat_style_config": {"continuous_turn_enabled": False}}
        )
        first = self.event()
        later = self.event("43", "新的消息")
        self.runtime.note_continuous_turn_incoming(first)
        snapshot = self.runtime._snapshot_media_event(first)
        self.runtime.note_continuous_turn_incoming(later)
        self.assertFalse(self.runtime.media_request_is_current_turn(snapshot))

    async def test_new_message_during_media_followup_generation_prevents_send(self):
        first = self.event()
        later = self.event("43", "新的话题")
        await self.begin_turn(first)

        async def reply(*args, **kwargs):
            self.runtime.note_continuous_turn_incoming(later)
            return "原请求的收尾文字"

        self.runtime._part_runtime_media_common_component._generate_delivered_media_reply = reply
        self.runtime._part_runtime_video_media_component._generate_life_video_followup_text = reply
        result = await self.runtime._send_delivered_media_followup(
            first.unified_msg_origin,
            media_name="生活照片",
            request_text=first.message_str,
            delivery_text="照片已送达",
            source_event=first,
        )
        self.assertFalse(result)
        self.assertEqual(first.sent_messages, [])
        self.assertFalse(
            await self.runtime._part_runtime_video_media_component._send_life_video_followup(
                first.unified_msg_origin, "视频", "已送达", first, "request"
            )
        )

    async def test_send_pipeline_stops_stale_media_text_but_keeps_regular_background_text(
        self,
    ):
        first = self.event()
        later = self.event("43", "新的消息")
        self.runtime.note_continuous_turn_incoming(first)
        self.runtime.note_continuous_turn_incoming(later)
        self.assertFalse(
            await self.runtime.send_background_text(
                first.unified_msg_origin,
                "旧图片补话",
                mode=BackgroundTextMode.DIRECT,
                source_event=first,
                source="image_followup",
            )
        )
        self.assertTrue(
            await self.runtime.send_background_text(
                first.unified_msg_origin,
                "正常后台消息",
                mode=BackgroundTextMode.DIRECT,
                source_event=first,
                source="background",
            )
        )
        self.assertEqual(len(first.sent_messages), 1)

    async def test_image_reaction_pending_only_finishes_after_real_delivery(self):
        first = self.event()
        tool = types.SimpleNamespace(name="life_image_generate")
        await self.runtime.note_tool_reaction_start(first, tool, {})
        result = await self.runtime.life_image_generate(first, "窗边照片")
        await self.runtime.note_tool_reaction_result(first, tool, {}, result)
        await self.runtime.note_tool_reaction_agent_done(first, None)
        state = self.runtime.tool_reactions._tool_reaction_states()[
            self.runtime.tool_reactions._tool_reaction_key(first)
        ]
        self.assertEqual(state["pending_background"], 1)
        self.assertFalse(state["finalized"])
        await self.finish_tasks()
        self.assertTrue(state["finalized"])
        self.assertEqual(state["status"], "success")

    async def test_image_delivery_before_tool_callback_does_not_remain_pending(self):
        first = self.event()
        tool = types.SimpleNamespace(name="life_image_generate")
        await self.runtime.note_tool_reaction_start(first, tool, {})
        result = await self.runtime.life_image_generate(first, "窗边照片")
        await self.finish_tasks()
        await self.runtime.note_tool_reaction_result(first, tool, {}, result)
        await self.runtime.note_tool_reaction_agent_done(first, None)
        state = self.runtime.tool_reactions._tool_reaction_states()[
            self.runtime.tool_reactions._tool_reaction_key(first)
        ]
        self.assertEqual(state["pending_background"], 0)
        self.assertTrue(state["finalized"])
        self.assertEqual(state["status"], "success")

    def prepare_proactive_voice(self, *, fail=False):
        from proactivegroundingfixture import GroundingStub

        # 本组验证语音生命周期；实际语义审核独立由 testproactivegrounding 覆盖。
        self.runtime.proactive_grounding = GroundingStub()

        async def synthesize(*args, **kwargs):
            self.started.set()
            await self.release.wait()
            if fail:
                raise RuntimeError("synthetic voice failure")
            return types.SimpleNamespace(path=self.path)

        self.runtime.media.voice = types.SimpleNamespace(synthesize=synthesize)
        self.runtime._part_proactive_send_component._proactive_voice_available = lambda scope: True
        self.runtime._apply_proactive_send_timing = lambda payload: async_return(None)
        self.runtime._note_voice_expression_decision = lambda **kwargs: async_return(
            None
        )
        self.runtime._append_proactive_send_history = lambda *args, **kwargs: (
            async_return(None)
        )
        self.runtime._send_proactive_emoji_if_needed = lambda *args: async_return(None)
        return {
            "source": "proactive_reply",
            "expression_intent": {
                "channel": "voice",
                "confidence": 0.98,
                "reason": "自然接刚才的话题",
            },
        }

    async def test_proactive_voice_discards_stale_success_and_failure_without_text_fallback(
        self,
    ):
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.started.clear()
                self.release.clear()
                payload = self.prepare_proactive_voice(fail=fail)
                first = self.event(f"original-{fail}", "旧话题")
                self.runtime.note_continuous_turn_incoming(first)
                sending = asyncio.create_task(
                    self.runtime._send_proactive_message(
                        first.unified_msg_origin,
                        "接着旧话题说一句",
                        "synthetic failure",
                        source_event=first,
                        send_payload=payload,
                    )
                )
                await asyncio.wait_for(self.started.wait(), 0.5)
                later = self.event(f"later-{fail}", "嗯", image=str(self.path))
                self.runtime.note_continuous_turn_incoming(later)
                self.release.set()
                self.assertFalse(await asyncio.wait_for(sending, 0.5))
                self.assertEqual(first.sent_messages, [])
                self.assertEqual(self.runtime.context.sent_messages, [])

    async def test_current_proactive_voice_still_delivers(self):
        payload = self.prepare_proactive_voice()
        first = self.event()
        self.runtime.note_continuous_turn_incoming(first)
        self.release.set()
        self.assertTrue(
            await self.runtime._send_proactive_message(
                first.unified_msg_origin,
                "自然接一句",
                "synthetic failure",
                source_event=first,
                send_payload=payload,
            )
        )
        self.assertEqual(len(first.sent_messages), 1)

    async def test_synthetic_proactive_candidate_is_invalidated_by_short_message(self):
        payload = self.prepare_proactive_voice()
        first = self.event()
        self.runtime.note_continuous_turn_incoming(first)
        synthetic = self.runtime._proactive_candidate_event(
            {
                "target_scope": first.unified_msg_origin,
                "message_id": first.message_id,
                "message": first.message_str,
            }
        )
        self.runtime.note_continuous_turn_incoming(self.event("43", "嗯"))
        self.assertFalse(
            await self.runtime._send_proactive_message(
                first.unified_msg_origin,
                "旧话题补话",
                "synthetic failure",
                source_event=synthetic,
                send_payload=payload,
            )
        )
        self.assertFalse(self.started.is_set())

    async def test_proactive_without_source_event_detects_new_chat_while_synthesizing(
        self,
    ):
        payload = self.prepare_proactive_voice()
        payload["source"] = "proactive_commitment"
        first = self.event()
        sending = asyncio.create_task(
            self.runtime._send_proactive_message(
                first.unified_msg_origin,
                "自然接一句",
                "synthetic failure",
                send_payload=payload,
            )
        )
        await asyncio.wait_for(self.started.wait(), 0.5)
        self.runtime.note_continuous_turn_incoming(first)
        self.release.set()
        self.assertFalse(await asyncio.wait_for(sending, 0.5))
        self.assertEqual(self.runtime.context.sent_messages, [])

    async def test_proactive_text_pipeline_rechecks_after_preparing_reply(self):
        first = self.event()
        self.runtime.note_continuous_turn_incoming(first)
        snapshot = self.runtime._snapshot_proactive_send_event(
            first.unified_msg_origin, first
        )
        self.runtime.note_continuous_turn_incoming(self.event("43", "嗯"))
        self.assertFalse(
            await self.runtime.send_background_text(
                first.unified_msg_origin,
                "过期主动补话",
                mode=BackgroundTextMode.DIRECT,
                source_event=snapshot,
                source="proactive",
            )
        )
        self.assertEqual(first.sent_messages, [])

    def prepare_style_learning(self):
        captures = []

        async def learn(event, image, **options):
            captures.append((image, options))
            self.started.set()
            await self.release.wait()
            return [
                types.SimpleNamespace(
                    id=len(captures),
                    kind="outfit",
                    title="测试造型",
                    description="测试造型",
                    source_image_hash=image,
                    image_path=image,
                )
            ]

        self.runtime._learn_style_catalog_image = learn
        self.runtime._append_assistant_history = lambda *args: async_return(None)
        return captures

    async def test_multi_image_learning_returns_pending_and_keeps_all_original_references(
        self,
    ):
        captures = self.prepare_style_learning()
        second = self.path.with_name("second.png")
        second.write_bytes(b"second image")
        first = self.event(text="学这两张衣服", image=str(self.path))
        first.message_items.append({"type": "image", "file": str(second)})
        await self.begin_turn(first)
        result = await asyncio.wait_for(
            self.runtime.life_style_learn(first, note="原始要求"), 0.5
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        await asyncio.wait_for(self.started.wait(), 0.5)
        later = self.event("43", "继续聊", image="https://example.com/sticker.png")
        await asyncio.wait_for(self.begin_turn(later), 0.5)
        first.message_items.clear()
        first.set_result(first.chain_result(["提前声称学会了"]))
        self.assertTrue(self.runtime.hold_background_tool_final_text(first))
        later.set_result(later.chain_result(["正常聊天回复"]))
        self.assertFalse(self.runtime.hold_background_tool_final_text(later))
        await self.finish_tasks()
        self.assertEqual(
            [value[0] for value in captures], [str(self.path), str(second)]
        )
        self.assertTrue(all(value[1]["note"] == "原始要求" for value in captures))
        self.assertEqual(len(first.sent_messages), 1)
        self.assertIn("已收入视觉衣橱", str(first.sent_messages[0].items))
        self.assertEqual(
            self.runtime._background_tool_request(first, "life_style_learn")["status"],
            "sent",
        )

    async def test_learning_explicit_image_list_does_not_reuse_current_image_for_every_item(
        self,
    ):
        captures = self.prepare_style_learning()
        first = self.event(image=str(self.path))
        images = ["https://example.com/a.png", "https://example.com/b.png"]
        await self.runtime.life_style_learn(first, reference_images=images)
        images.clear()
        await self.finish_tasks()
        self.assertEqual(
            [value[0] for value in captures],
            ["https://example.com/a.png", "https://example.com/b.png"],
        )

    async def test_learning_uses_cached_message_images_after_source_cleanup(self):
        captures = self.prepare_style_learning()
        first = self.event(image="/temporary/removed.png")
        setattr(
            first,
            self.runtime._PREPARED_VISUAL_MEDIA_ATTR,
            [{"item": first.message_items[0], "path": str(self.path)}],
        )
        await self.runtime.life_style_learn(first)
        first.message_items.clear()
        await self.finish_tasks()
        self.assertEqual([value[0] for value in captures], [str(self.path)])

    def prepare_video_note(self, first):
        clip = SightClip(
            scope=first.unified_msg_origin,
            message_id=first.message_id,
            source=str(self.video_path),
            metadata={"title": "原始视频"},
        )
        captures = []

        async def understand(event, locked_clip, **kwargs):
            captures.append(locked_clip)
            self.started.set()
            await self.release.wait()
            return SightInsight(
                clip=locked_clip, summary="原视频的内容", transcript="原视频的转写正文"
            )

        self.runtime._sight_clips_from_event_async = lambda *args: async_return([clip])
        self.runtime._understand_sight_clip = understand
        self.runtime._compose_sight_note_with_timeout = lambda insight, **kwargs: (
            async_return("# 原视频总结\n原始内容")
        )
        self.runtime._cache_sight_note_markdown = lambda insight, *args, **kwargs: (
            async_return(insight)
        )
        self.runtime._render_sight_note_image = lambda *args: async_return(
            str(self.path)
        )
        self.runtime._record_sight_delivery_metrics = lambda *args: None
        self.runtime._log_sight_professional_metrics = lambda *args: None
        return clip, captures

    async def test_long_video_note_is_background_and_later_video_cannot_replace_source(
        self,
    ):
        first = self.event(text="把这个视频详细总结成图")
        clip, captures = self.prepare_video_note(first)
        await self.begin_turn(first)
        result = await asyncio.wait_for(self.runtime.life_video_note(first), 0.5)
        self.assertEqual(json.loads(result)["status"], "pending")
        await asyncio.wait_for(self.started.wait(), 0.5)
        clip.source = "https://example.com/changed.mp4"
        clip.metadata["title"] = "被修改的视频"
        later = self.event("43", "继续聊")
        later.message_items.append(
            {"type": "video", "file": "https://example.com/new.mp4"}
        )
        await asyncio.wait_for(self.begin_turn(later), 0.5)
        await self.finish_tasks()
        self.assertEqual(captures[0].metadata["original_source"], str(self.video_path))
        self.assertEqual(Path(captures[0].source).read_bytes(), b"synthetic video")
        self.assertEqual(captures[0].metadata["title"], "原始视频")
        self.assertEqual(len(first.sent_messages), 1)
        self.assertEqual(later.sent_messages, [])
        self.assertEqual(
            self.runtime._background_tool_request(first, "life_video_note")["status"],
            "sent",
        )

    async def test_video_note_composition_also_runs_outside_chat_turn(self):
        first = self.event()
        clip, _ = self.prepare_video_note(first)
        self.runtime._understand_sight_clip = lambda *args, **kwargs: async_return(
            SightInsight(clip=clip, transcript="转写正文")
        )

        async def compose(*args, **kwargs):
            self.started.set()
            await self.release.wait()
            return "# 视频总结"

        self.runtime._compose_sight_note_with_timeout = compose
        await self.begin_turn(first)
        self.assertEqual(
            json.loads(
                await asyncio.wait_for(self.runtime.life_video_note(first), 0.5)
            )["status"],
            "pending",
        )
        await asyncio.wait_for(self.started.wait(), 0.5)
        await asyncio.wait_for(self.begin_turn(self.event("43", "继续聊")), 0.5)
        await self.finish_tasks()
        self.assertEqual(len(first.sent_messages), 1)

    async def test_background_tool_deduplication_and_reaction_wait_for_delivery(self):
        self.prepare_style_learning()
        first = self.event(image=str(self.path))
        tool = types.SimpleNamespace(name="life_style_learn")
        await self.runtime.note_tool_reaction_start(first, tool, {})
        result = await self.runtime.life_style_learn(first)
        duplicate = await self.runtime.life_style_learn(first)
        self.assertTrue(json.loads(duplicate)["deduplicated"])
        self.assertEqual(len(self.runtime._background_scheduler_for_runtime().tasks), 1)
        await self.runtime.note_tool_reaction_result(first, tool, {}, result)
        await self.runtime.note_tool_reaction_agent_done(first, None)
        state = self.runtime.tool_reactions._tool_reaction_states()[
            self.runtime.tool_reactions._tool_reaction_key(first)
        ]
        self.assertEqual(state["pending_background"], 1)
        self.assertFalse(state["finalized"])
        await self.finish_tasks()
        self.assertTrue(state["finalized"])
        self.assertEqual(state["status"], "success")

    async def test_background_tool_fast_delivery_before_callback_settles_correctly(
        self,
    ):
        self.prepare_style_learning()
        first = self.event(image=str(self.path))
        tool = types.SimpleNamespace(name="life_style_learn")
        await self.runtime.note_tool_reaction_start(first, tool, {})
        result = await self.runtime.life_style_learn(first)
        await self.finish_tasks()
        await self.runtime.note_tool_reaction_result(first, tool, {}, result)
        await self.runtime.note_tool_reaction_agent_done(first, None)
        state = self.runtime.tool_reactions._tool_reaction_states()[
            self.runtime.tool_reactions._tool_reaction_key(first)
        ]
        self.assertEqual(state["pending_background"], 0)
        self.assertEqual(state["status"], "success")

    async def test_background_learning_full_queue_does_not_hide_new_turn(self):
        self.prepare_style_learning()
        self.runtime._background_scheduler = BackgroundTaskScheduler(
            vision_limit=1, vision_backlog_limit=0
        )
        first = self.event(image=str(self.path))
        await self.runtime.life_style_learn(first)
        later = self.event("43", image=str(self.path))
        result = await self.runtime.life_style_learn(later)
        self.assertEqual(json.loads(result)["status"], "failed")
        self.assertIsNone(
            self.runtime._background_tool_request(later, "life_style_learn")
        )
        later.set_result(later.chain_result(["后续正常回复"]))
        self.assertFalse(self.runtime.hold_background_tool_final_text(later))
        await self.finish_tasks()

    async def test_background_failure_does_not_interrupt_new_chat(self):
        first = self.event()
        self.prepare_video_note(first)

        async def fail(*args, **kwargs):
            self.started.set()
            await self.release.wait()
            raise RuntimeError("synthetic understanding failure")

        self.runtime._understand_sight_clip = fail
        await self.begin_turn(first)
        await self.runtime.life_video_note(first)
        await asyncio.wait_for(self.started.wait(), 0.5)
        await self.begin_turn(self.event("43", "继续聊"))
        await self.finish_tasks()
        self.assertEqual(first.sent_messages, [])
        self.assertEqual(
            self.runtime._background_tool_request(first, "life_video_note")["status"],
            "failed",
        )

    async def test_queued_learning_cancellation_clears_pending_request_states(self):
        captures = self.prepare_style_learning()
        self.runtime._background_scheduler = BackgroundTaskScheduler(
            vision_limit=1, vision_backlog_limit=2
        )
        first = self.event(image=str(self.path))
        queued = self.event("43", image=str(self.path))
        await self.runtime.life_style_learn(first)
        await asyncio.wait_for(self.started.wait(), 0.5)
        await self.runtime.life_style_learn(queued)
        await self.runtime._cancel_background_tasks()
        self.assertEqual(len(captures), 1)
        for event in (first, queued):
            self.assertEqual(
                self.runtime._background_tool_request(event, "life_style_learn")[
                    "status"
                ],
                "cancelled",
            )
            self.assertEqual(event.sent_messages, [])

    async def test_video_note_keeps_local_source_after_framework_cleanup_and_same_cache_key(
        self,
    ):
        first = self.event()
        clip, captures = self.prepare_video_note(first)
        original_key = clip.key
        await self.runtime.life_video_note(first)
        await asyncio.wait_for(self.started.wait(), 0.5)
        self.video_path.unlink()
        self.assertEqual(captures[0].key, original_key)
        self.assertEqual(Path(captures[0].source).read_bytes(), b"synthetic video")
        await self.finish_tasks()
        self.assertEqual(len(first.sent_messages), 1)

    async def test_explicit_learning_reference_uses_durable_prepared_copy(self):
        captures = self.prepare_style_learning()
        first = self.event(image="/temporary/removed.png")
        setattr(
            first,
            self.runtime._PREPARED_VISUAL_MEDIA_ATTR,
            [{"item": first.message_items[0], "path": str(self.path)}],
        )
        result = await self.runtime.life_style_learn(
            first, reference_image="/temporary/removed.png"
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        await self.finish_tasks()
        self.assertEqual([value[0] for value in captures], [str(self.path)])

    async def test_recalled_video_note_cancels_result_delivery(self):
        first = self.event()
        self.prepare_video_note(first)
        await self.runtime.life_video_note(first)
        await asyncio.wait_for(self.started.wait(), 0.5)
        notice = Event(unified_msg_origin=first.unified_msg_origin)
        notice.message_obj.raw_message = {
            "post_type": "notice", "notice_type": "friend_recall",
            "message_id": self.runtime._event_message_id(first),
        }
        self.runtime.note_recalled_message(notice)
        await self.finish_tasks()
        self.assertEqual(first.sent_messages, [])
        self.assertEqual(
            self.runtime._background_tool_request(first, "life_video_note")["status"],
            "cancelled",
        )

    async def test_video_note_record_failure_after_send_does_not_report_delivery_failure(
        self,
    ):
        first = self.event()
        self.prepare_video_note(first)

        def fail_metrics(*args):
            raise RuntimeError("synthetic metrics failure")

        self.runtime._record_sight_delivery_metrics = fail_metrics
        await self.runtime.life_video_note(first)
        await self.finish_tasks()
        self.assertEqual(len(first.sent_messages), 1)
        self.assertEqual(
            self.runtime._background_tool_request(first, "life_video_note")["status"],
            "sent",
        )

    async def test_cached_video_note_request_keeps_original_insight(self):
        first = self.event()
        clip, _ = self.prepare_video_note(first)
        insight = SightInsight(clip=clip, transcript="原来的正文", summary="原来的内容")
        self.runtime._sight_clips_from_event_async = lambda *args: async_return([])
        self.runtime._sight_recent_for_event = lambda *args, **kwargs: async_return(
            [insight]
        )
        captures = []

        async def compose(locked, **kwargs):
            captures.append(locked.summary)
            return "# 视频总结"

        self.runtime._compose_sight_note_with_timeout = compose
        await self.runtime.life_video_note(first)
        insight.summary = "后来的内容"
        await self.finish_tasks()
        self.assertEqual(captures, ["原来的内容"])
