"""分享后追问、语义分流与解析插件已发送场景的隔离回归。"""
import datetime as dt
import json
import types
import unittest
from unittest.mock import AsyncMock, Mock

import testbilibrowsing as fixture
from support import Event
from core.browsing.shared import SharedVideos
from core.runtime.choice.incoming import ResponseGateEventComponent
from core.sight.bili import BiliTarget
from core.sight.bridge import SightComponent
from core.sight.clip import SightClip, SightInsight


class SharedVideoTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BrowsingTest.asyncSetUp
    asyncTearDown = fixture.BrowsingTest.asyncTearDown
    record = fixture.BrowsingTest.record
    metadata = fixture.BrowsingTest.metadata

    def event(self, text="前面发的视频讲的什么？", *, scope="private:a", message_id="followup"):
        return types.SimpleNamespace(scope=scope, message_str=text,
                                     message_obj=types.SimpleNamespace(message_id=message_id),
                                     get_sender_id=lambda: "user-a")

    async def remember(self, bvid=fixture.BV, **kwargs):
        event = self.event(bvid, **kwargs)
        await self.service.shared.remember(event, BiliTarget(bvid=bvid))
        return event

    async def test_followup_reuses_legacy_record_without_network_or_login(self):
        record = await self.record(scope="private:a", source="用户分享", evidence=[{"quote": "真实字幕"}])
        self.service.reader.require_login.side_effect = AssertionError("cache must work offline")
        result = json.loads(await self.service.understand(self.event()))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["video"]["id"], record["id"])
        self.service.reader.require_login.assert_not_awaited()

    async def test_pending_reference_survives_service_recreation(self):
        await self.remember(message_id="share")
        self.service.shared = SharedVideos(self.service)
        target, error = await self.service.shared.resolve(self.event())
        self.assertIsNone(error)
        self.assertEqual(target.bvid, fixture.BV)

    async def test_multiple_videos_require_selection_and_select_older(self):
        await self.remember(message_id="one")
        await self.remember(fixture.BV2, message_id="two")
        target, error = await self.service.shared.resolve(self.event())
        self.assertIsNone(target)
        self.assertEqual(error["status"], "ambiguous")
        selected = next(c for c in error["candidates"] if fixture.BV in c["url"])
        target, error = await self.service.shared.resolve(self.event(), selected_id=selected["id"])
        self.assertIsNone(error)
        self.assertEqual(target.bvid, fixture.BV)

    async def test_current_message_takes_precedence_over_recent_references(self):
        await self.remember(fixture.BV2)
        target, error = await self.service.shared.resolve(self.event(fixture.BV))
        self.assertIsNone(error)
        self.assertEqual(target.bvid, fixture.BV)

    async def test_quoted_component_chain_is_a_video_source(self):
        event = self.event()
        event.message_obj.message = [types.SimpleNamespace(chain=[types.SimpleNamespace(text=fixture.BV)])]
        target, error = await self.service.shared.resolve(event)
        self.assertIsNone(error)
        self.assertEqual(target.bvid, fixture.BV)

    async def test_cross_session_and_unrelated_global_records_are_not_fallbacks(self):
        private = await self.record(scope="private:other", source="用户分享", evidence=[{"quote": "私密内容"}])
        await self.record(scope="global", evidence=[{"quote": "自己浏览"}])
        await self.remember(scope="private:other")
        target, error = await self.service.shared.resolve(self.event())
        self.assertIsNone(target)
        self.assertEqual(error["status"], "not_found")
        target, error = await self.service.shared.resolve(self.event(), selected_id=private["id"])
        self.assertIsNone(target)
        self.assertEqual(error["status"], "unavailable")

    async def test_expired_reference_is_not_silently_reused(self):
        await self.remember()
        self.now += dt.timedelta(days=2)
        self.assertEqual(await self.service.shared.candidates(self.event()), [])

    async def test_recalled_share_does_not_reappear_through_legacy_record(self):
        await self.record(scope="private:a", source="用户分享", evidence=[{"quote": "旧内容"}])
        await self.remember(message_id="recalled")
        self.runtime._message_was_recalled = lambda scope, mid: mid == "recalled"
        self.assertEqual(await self.service.shared.candidates(self.event()), [])

    async def test_references_are_bounded_and_partitioned(self):
        for n in range(25):
            await self.remember(message_id=str(n))
        await self.remember(scope="private:other")
        rows = self.db._conn.execute("SELECT scope, COUNT(*) FROM continuous_life_entries WHERE kind='bili_shared_video' GROUP BY scope").fetchall()
        self.assertEqual(dict(rows), {"private:a": 20, "private:other": 1})

    async def test_matching_professional_insight_is_reused_by_followup(self):
        await self.remember()
        insight = SightInsight(SightClip(scope="private:a", source=f"https://www.bilibili.com/video/{fixture.BV}"),
                               summary="已核对的视频内容", transcript="字幕依据")
        self.runtime._sight_recent_for_event = AsyncMock(return_value=[insight])
        self.service._shared_video = AsyncMock(side_effect=AssertionError("do not reprocess"))
        result = await self.service.understand(self.event())
        self.assertIn("已核对的视频内容", result)
        self.service._shared_video.assert_not_awaited()

    async def test_wrong_page_insight_cannot_answer_selected_video(self):
        insight = SightInsight(SightClip(scope="private:a", source=f"https://www.bilibili.com/video/{fixture.BV}?p=2"), summary="第二集")
        self.runtime._sight_recent_for_event = AsyncMock(return_value=[insight])
        self.service._shared_video = AsyncMock(return_value=None)
        result = json.loads(await self.service.understand(self.event(fixture.BV)))
        self.assertEqual(result["status"], "unavailable")
        self.service._shared_video.assert_awaited_once()

    async def test_semantic_routes_are_exclusive_and_summary_works_after_media_send(self):
        scheduled = []
        def schedule(coro, **kwargs):
            coro.close()
            scheduled.append(kwargs)
            return True
        self.runtime._schedule_background_task = schedule
        self.runtime._suppress_default_llm = Mock()
        self.runtime._event_message_id = lambda event: event.message_obj.message_id
        self.runtime.event_was_recalled = lambda *a, **k: False
        self.runtime._send_bili_summary_background = AsyncMock()
        for mode in ("summary", "conversation", "observe"):
            with self.subTest(mode=mode):
                scheduled.clear()
                self.service._model.return_value = {"mode": mode}
                event = self.event(fixture.BV, message_id=mode)
                event._has_send_oper = True
                await self.service.incoming(event)
                self.assertEqual(event._daily_life_bili_route, mode)
                handled = SightComponent(self.runtime).schedule_bili_summary_from_event(event)
                self.assertEqual(handled, mode == "summary")
                self.assertEqual([r["label"] for r in scheduled], {
                    "summary": ["B站视频总结"], "conversation": ["理解分享的视频"], "observe": []}[mode])
                self.assertTrue(event._has_send_oper)

    async def test_classifier_failure_falls_back_to_conversation_not_summary(self):
        self.service._model.side_effect = TimeoutError()
        self.assertEqual(await self.service.shared.response_mode(self.event()), "conversation")

    async def test_failed_summary_schedule_does_not_suppress_chat(self):
        self.runtime._schedule_background_task = lambda coro, **k: (coro.close() or False)
        self.runtime._send_bili_summary_background = AsyncMock()
        self.runtime._event_message_id = lambda e: "id"
        self.runtime.event_was_recalled = lambda *a, **k: False
        self.runtime._suppress_default_llm = Mock()
        event = self.event(fixture.BV)
        self.assertFalse(SightComponent(self.runtime).schedule_bili_summary_from_event(event))
        self.runtime._suppress_default_llm.assert_not_called()
        self.assertTrue(event._daily_life_bili_natural)

    async def test_existing_summary_job_owns_reply_without_duplicate_schedule(self):
        self.runtime._background_scheduler = types.SimpleNamespace(keys={f"bili:private:a:{fixture.BV}:1"})
        self.runtime._schedule_background_task = Mock(side_effect=AssertionError("duplicate"))
        self.runtime._event_message_id = lambda e: "id"
        self.runtime.event_was_recalled = lambda *a, **k: False
        self.runtime._suppress_default_llm = Mock()
        self.assertTrue(SightComponent(self.runtime).schedule_bili_summary_from_event(self.event(fixture.BV)))
        self.runtime._suppress_default_llm.assert_called_once()

    async def test_explicit_summary_can_override_disabled_auto_default(self):
        self.runtime.config.sight.bili_auto_summary = False
        self.service._model.return_value = {"mode": "summary"}
        self.assertEqual(await self.service.shared.response_mode(self.event("请给我专业总结")), "summary")
        self.assertEqual(self.service._model.await_args.args[1]["default_mode"], "conversation")


class ParserCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    def test_gate_preserves_stop_command_self_and_unrelated_send_boundaries(self):
        runtime = types.SimpleNamespace(_event_has_command_handler=lambda e: e.command,
                                        _proactive_is_self_message=lambda e: e.self_message)
        gate = ResponseGateEventComponent(runtime)
        event = types.SimpleNamespace(_has_send_oper=True, command=False, self_message=False,
                                     is_stopped=lambda: False)
        self.assertTrue(gate._response_gate_should_skip(event))
        event._daily_life_bili_natural = True
        self.assertFalse(gate._response_gate_should_skip(event))
        event.command = True
        self.assertTrue(gate._response_gate_should_skip(event))
        event.command = False
        event.self_message = True
        self.assertTrue(gate._response_gate_should_skip(event))
        event.self_message = False
        event.is_stopped = lambda: True
        self.assertTrue(gate._response_gate_should_skip(event))

    async def test_explicit_request_keeps_conversation_and_send_flag_and_runs_once(self):
        from astrbot_plugin_daily_life.main import DailyLifePlugin
        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        conversation = types.SimpleNamespace(cid="cid", history="[]")
        manager = types.SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value="cid"),
                                        get_conversation=AsyncMock(return_value=conversation))
        plugin.context = types.SimpleNamespace(conversation_manager=manager)
        plugin.runtime = types.SimpleNamespace(event_was_recalled=lambda e: False,
                                               _suppress_default_llm=lambda e: setattr(e, "call_llm", True))
        event = Event()
        event.message_str = fixture.BV
        event._has_send_oper = True
        event._daily_life_bili_reply_pending = True
        event.request_llm = lambda **kw: kw
        results = [item async for item in plugin.on_bili_conversation_reply(event)]
        self.assertEqual(len(results), 1)
        self.assertIs(results[0]["conversation"], conversation)
        self.assertEqual(results[0]["prompt"], fixture.BV)
        self.assertTrue(event._has_send_oper)
        self.assertTrue(event.call_llm)
        self.assertEqual([item async for item in plugin.on_bili_conversation_reply(event)], [])

    async def test_stopped_or_recalled_event_does_not_dispatch(self):
        from astrbot_plugin_daily_life.main import DailyLifePlugin
        plugin = DailyLifePlugin.__new__(DailyLifePlugin)
        plugin.runtime = types.SimpleNamespace(event_was_recalled=lambda e: True)
        event = Event()
        event._daily_life_bili_reply_pending = True
        self.assertEqual([item async for item in plugin.on_bili_conversation_reply(event)], [])
