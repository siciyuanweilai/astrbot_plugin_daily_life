"""平台通知不代表新聊天消息，不能取消回复或改写收发事实。"""

import asyncio
import copy
import datetime
import types
import unittest
from collections import UserDict
from unittest.mock import AsyncMock, Mock

from support import DailyLifeRuntime, Event, LifeSettings

# isort: split

from core.runtime.background import BackgroundTaskScheduler
from core.runtime.elapsed import RECEIVED_AT, ConversationTimingService
from core.sources.events import is_chat_message_event
from testplugin import DailyLifePlugin


def incoming(text="", message_id="notice", *, post_type="notice", group_id=""):
    event = Event(
        unified_msg_origin=(
            f"aiocqhttp:GroupMessage:{group_id}"
            if group_id else "aiocqhttp:FriendMessage:10001"
        ),
        sender_id="10001", message_id=message_id, group_id=group_id,
    )
    event.message_str = text
    event.message_obj.raw_message = {"post_type": post_type}
    return event


def runtime():
    value = DailyLifeRuntime.__new__(DailyLifeRuntime)
    value.config = LifeSettings.from_dict({"chat_style_config": {
        "continuous_turn_wait_seconds": 0,
        "private_casual_max_chars": 15,
    }})
    value._init_continuous_turn_state()
    value._semantic_segment_init_state()
    return value


class IncomingNoticeTest(unittest.IsolatedAsyncioTestCase):
    def test_event_type_handles_mapping_payloads_and_optional_metadata(self):
        self.assertFalse(is_chat_message_event(None))
        self.assertTrue(is_chat_message_event(types.SimpleNamespace()))
        for post_type in (None, "", "message", "message_sent", "notice", "request", "meta_event"):
            for location in ("message_obj", "event"):
                with self.subTest(post_type=post_type, location=location):
                    event = types.SimpleNamespace()
                    target = event
                    if location == "message_obj":
                        target = event.message_obj = types.SimpleNamespace()
                    target.raw_message = UserDict(post_type=post_type)
                    self.assertEqual(
                        is_chat_message_event(event),
                        post_type in (None, "", "message", "message_sent"),
                    )

    def test_notices_do_not_replace_turn_or_change_cadence(self):
        for group_id in ("", "20001"):
            for post_type in ("notice", "request", "meta_event"):
                with self.subTest(group_id=group_id, post_type=post_type):
                    life = runtime()
                    original = incoming("晚上好", "chat", post_type="message", group_id=group_id)
                    life.note_continuous_turn_incoming(original)
                    before = copy.deepcopy((
                        life._media_chat_tokens,
                        life._continuous_turn_revisions,
                        life._continuous_turn_cadence,
                    ))
                    # 有些适配器会给通知填入说明文字，仍应以平台事件类型为准。
                    notice = incoming("对方正在输入", post_type=post_type, group_id=group_id)
                    self.assertFalse(life.note_continuous_turn_incoming(notice))
                    self.assertTrue(life.media_request_is_current_turn(original))
                    self.assertEqual(before, (
                        life._media_chat_tokens,
                        life._continuous_turn_revisions,
                        life._continuous_turn_cadence,
                    ))

    def test_notices_do_not_change_segment_revision_or_pacing(self):
        life = runtime()
        original = incoming("晚上好", "chat", post_type="message")
        life.note_semantic_segment_incoming_message(original)
        before = copy.deepcopy((life._semantic_segment_revisions, life._chat_pacing_state))
        for location in ("message_obj", "event", "wrapped", "context", "outer_message"):
            with self.subTest(location=location):
                notice = incoming()
                if location == "event":
                    notice.raw_message = notice.message_obj.raw_message
                    del notice.message_obj.raw_message
                elif location == "wrapped":
                    notice = types.SimpleNamespace(event=notice, raw_message={})
                elif location == "context":
                    notice = types.SimpleNamespace(context=types.SimpleNamespace(event=notice))
                elif location == "outer_message":
                    notice = types.SimpleNamespace(event=notice, raw_message={"post_type": "message"})
                life.note_semantic_segment_incoming_message(notice)
                self.assertEqual(before, (life._semantic_segment_revisions, life._chat_pacing_state))

    async def test_notice_does_not_cancel_background_reply_but_real_message_does(self):
        for later_kind in ("notice", "message", "message_sent", "image", "unspecified"):
            with self.subTest(later_kind=later_kind):
                life = runtime()
                life.event_was_recalled = lambda event: False
                scheduler = BackgroundTaskScheduler(reply_limit=1, reply_backlog_limit=0)
                life._schedule_background_task = scheduler.schedule
                plugin = DailyLifePlugin.__new__(DailyLifePlugin)
                plugin.runtime = life
                plugin._record_reply_delivery = AsyncMock()
                entered, release = asyncio.Event(), asyncio.Event()

                async def prepare(event):
                    entered.set()
                    await release.wait()

                plugin._send_pipeline_apply = prepare
                original = incoming("晚上好", "chat", post_type="message")
                life.note_continuous_turn_incoming(original)
                original.set_result(original.chain_result([types.SimpleNamespace(text="刚忙完，晚上好呀")]))
                self.assertTrue(plugin._defer_reply_preparation(original))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    later = incoming(
                        "" if later_kind in {"notice", "image"} else "接着聊",
                        "later",
                        post_type="message" if later_kind == "image" else later_kind,
                    )
                    if later_kind == "image":
                        later.message_items.append({"type": "image", "url": "https://example.com/photo.png"})
                    elif later_kind == "unspecified":
                        del later.message_obj.raw_message
                    life.note_continuous_turn_incoming(later)
                    life.note_semantic_segment_incoming_message(later)
                    release.set()
                    await asyncio.gather(*scheduler.tasks)
                    expected = int(later_kind == "notice")
                    self.assertEqual(len(original.sent_messages), expected)
                    self.assertEqual(plugin._record_reply_delivery.await_count, expected)
                finally:
                    await scheduler.cancel_all()

    async def test_notice_does_not_cancel_unsent_segment_tail(self):
        life = runtime()
        for method in ("note_structured_sent_result", "note_media_source_event", "note_proactive_bot_reply", "note_voice_switch_text_result"):
            setattr(life, method, lambda event: None)
        original = incoming("晚上好", "chat", post_type="message")
        original.set_result(original.chain_result([types.SimpleNamespace(text="先说第一句。再说第二句。")]))
        self.assertTrue(life.apply_chat_style_before_send(original))
        send = original.send

        async def send_with_notice(message):
            await send(message)
            life.note_semantic_segment_incoming_message(incoming())

        original.send = send_with_notice
        self.assertTrue(await life.send_chat_style_segments_if_needed(original))
        self.assertEqual(len(original.sent_messages), 2)

    async def test_notice_is_not_saved_as_conversation_arrival(self):
        archive = types.SimpleNamespace(save_conversation_arrival=AsyncMock())
        clock = ConversationTimingService(
            archive=lambda: archive, is_self_message=lambda event: False,
            session_id=lambda event: event.unified_msg_origin,
            is_group=lambda event: False,
        )
        original = incoming("我回来了", "chat", post_type="message")
        clock.note_conversation_incoming(original)
        await clock.record_conversation_arrival(original)
        before = copy.deepcopy(clock._arrivals)
        notice = incoming()
        clock.note_conversation_incoming(notice)
        self.assertFalse(hasattr(notice, RECEIVED_AT))
        # 即使其他调用方预先加过时间戳，也不能持久化为一次聊天到达。
        setattr(notice, RECEIVED_AT, getattr(original, RECEIVED_AT) + 60)
        await clock.record_conversation_arrival(notice)
        self.assertEqual(clock._arrivals, before)
        archive.save_conversation_arrival.assert_awaited_once()

    def test_notice_does_not_reset_proactive_candidate_or_observe_reply(self):
        life = runtime()
        life._proactive_idle_candidates = {"scope": {"revision": 7, "message": "晚点再聊"}}
        before = copy.deepcopy(life._proactive_idle_candidates)
        life._schedule_background_task = Mock()
        life.note_proactive_activity(incoming(), now=datetime.datetime(2026, 10, 29, 12))
        self.assertEqual(life._proactive_idle_candidates, before)
        life._schedule_background_task.assert_not_called()

    async def test_plugin_skips_chat_hooks_for_notices_and_keeps_event_available(self):
        for post_type in ("notice", "request", "meta_event"):
            with self.subTest(post_type=post_type):
                plugin = DailyLifePlugin.__new__(DailyLifePlugin)
                plugin.runtime = types.SimpleNamespace(
                    note_recalled_message=Mock(return_value=False),
                    note_runtime_scope_activity=Mock(),
                    note_conversation_incoming=Mock(),
                    note_bili_browse_incoming=AsyncMock(),
                    note_proactive_activity=Mock(),
                )
                plugin._capture_chat_memory = AsyncMock()
                event = incoming(post_type=post_type)
                await plugin.on_message_for_proactive_reply(event)
                plugin.runtime.note_recalled_message.assert_called_once_with(event)
                plugin.runtime.note_runtime_scope_activity.assert_not_called()
                plugin.runtime.note_conversation_incoming.assert_not_called()
                plugin.runtime.note_bili_browse_incoming.assert_not_awaited()
                plugin.runtime.note_proactive_activity.assert_not_called()
                plugin._capture_chat_memory.assert_not_awaited()
                self.assertFalse(event.is_stopped())

    async def test_plugin_keeps_text_image_and_unspecified_platform_messages(self):
        for post_type in ("message", "message_sent", None):
            for image_only in (False, True):
                with self.subTest(post_type=post_type, image_only=image_only):
                    plugin = DailyLifePlugin.__new__(DailyLifePlugin)
                    plugin.runtime = types.SimpleNamespace(
                        note_recalled_message=lambda event: False,
                        note_continuous_turn_incoming=Mock(),
                        note_proactive_activity=Mock(),
                        apply_response_gate_for_event=AsyncMock(return_value=None),
                    )
                    event = incoming("" if image_only else "你好", post_type=post_type)
                    if post_type is None:
                        del event.message_obj.raw_message
                    if image_only:
                        event.message_items.append({"type": "image", "url": "https://example.com/photo.png"})
                    await plugin.on_message_for_proactive_reply(event)
                    plugin.runtime.note_continuous_turn_incoming.assert_called_once_with(event)
                    plugin.runtime.apply_response_gate_for_event.assert_awaited_once_with(event)

    async def test_recall_and_group_upload_keep_their_own_entrypoints(self):
        life = runtime()
        original = incoming("刚才那句撤回", "chat", post_type="message")
        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = life
        life.note_runtime_scope_activity = Mock()
        recall = incoming()
        recall.message_obj.raw_message.update(notice_type="friend_recall", message_id="chat")
        await plugin.on_message_for_proactive_reply(recall)
        self.assertTrue(life.event_was_recalled(original))
        life.note_runtime_scope_activity.assert_not_called()
        upload = incoming(group_id="20001")
        upload.message_obj.raw_message.update(notice_type="group_upload", file={"id": "file-1", "name": "video.mp4"})
        life.note_sight_group_upload_event = Mock()
        await plugin.on_platform_event_for_sight_upload(upload)
        life.note_sight_group_upload_event.assert_called_once_with(upload)
