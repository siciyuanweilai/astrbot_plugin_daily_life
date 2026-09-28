import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import support  # noqa: F401
from core.archive import LifeArchive
from core.interface.view import PageViewMixin
from core.life.rest_delay import (
    activate_rest_delay,
    format_rest_delay_hint,
    parse_rest_delay_request,
)
from core.life.tools import (
    get_current_timeline_status,
    reconcile_timeline_execution,
    timeline_item_datetime,
)
from core.models import DayRecord, LifeState, TimelineItem
from core.runtime.live import DailyLifeRuntime
from runtimehelpers import RuntimeAsyncHelperMixin
from support import LifeSettings, ProviderRequest, Event


def make_day(date="2026-09-25", time="20:30"):
    return DayRecord(
        date=date,
        timeline=[
            TimelineItem(time="19:00", activity="收拾餐桌", duration_minutes=20),
            TimelineItem(time=time, activity="回屋准备睡觉"),
        ],
    )


class RestDelayTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 9, 25, 20, 50)
        self.day = make_day()
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.config = LifeSettings.from_dict({})

    def test_request_recognition_and_negative_cases(self):
        for text, minutes in [
            ("你就不能再晚点睡吗", 60),
            ("不要这么早睡", 60),
            ("再陪我聊半小时", 30),
            ("再聊二十分钟", 20),
            ("再聊五分钟", 5),
            ("先别睡，再聊两小时", 120),
        ]:
            with self.subTest(text=text):
                self.assertEqual(parse_rest_delay_request(text), minutes)
        for text in [
            "我今晚晚点睡",
            "别晚点睡",
            "不要再聊了",
            "今晚早点休息",
            "他说明天晚点睡",
            "你昨天说晚点睡",
            "如果晚点睡会怎样",
            "我晚点休息",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(parse_rest_delay_request(text))

    def test_all_contexts_share_deferred_time(self):
        self.assertTrue(activate_rest_delay(self.day, "你就不能再晚点睡吗", self.now))
        current, next_item = get_current_timeline_status(
            self.day.timeline, self.now, self.day.date, meta=self.day.meta
        )
        self.assertIsNone(current)
        self.assertIs(next_item, self.day.timeline[1])
        text = self.runtime.build_hidden_life_context(self.day, self.now, False)
        self.assertIn("21:50", text)
        self.assertIn("HiddenRestDelay", text)
        self.assertNotIn("当前: 20:30", text)
        self.assertNotIn("正在: 回屋准备睡觉", text)
        prompt = self.runtime._build_state_update_prompt(self.day, self.now, "chat")
        self.assertIn("下一项安排：09-25 21:50", prompt)
        self.assertIn("顺延结束只是再次评估", prompt)
        self.assertEqual(self.day.timeline[1].time, "20:30")
        page = PageViewMixin()._page_day(self.day, self.now, False)
        self.assertIsNone(page["current"])
        self.assertEqual(page["next"]["time"], "21:50")
        self.assertEqual(page["timeline"][1]["time"], "21:50")
        self.assertEqual(self.day.timeline[1].time, "20:30")

    def test_window_expires_exactly_without_a_sweep(self):
        activate_rest_delay(self.day, "再聊五分钟", self.now)
        until = self.now + datetime.timedelta(minutes=5)
        current, _ = get_current_timeline_status(
            self.day.timeline,
            until - datetime.timedelta(seconds=1),
            self.day.date,
            meta=self.day.meta,
        )
        self.assertIsNone(current)
        current, _ = get_current_timeline_status(
            self.day.timeline, until, self.day.date, meta=self.day.meta
        )
        self.assertIs(current, self.day.timeline[1])
        self.assertEqual(format_rest_delay_hint(self.day, until), "")
        self.assertNotIn(
            "正在: 回屋准备睡觉",
            self.runtime.build_hidden_activity_hint(self.day, until, False)[1],
        )
        reconcile_timeline_execution(
            self.day.timeline, until, self.day.date, meta=self.day.meta
        )
        self.assertEqual(self.day.timeline[1].execution_state, "active")
        self.assertNotEqual(self.day.timeline[1].execution_state, "completed")

    def test_cross_midnight_uses_full_datetime(self):
        day = make_day(time="23:30")
        now = datetime.datetime(2026, 9, 25, 23, 50)
        activate_rest_delay(day, "晚点睡", now)
        next_day = datetime.datetime(2026, 9, 26, 0, 20)
        reconcile_timeline_execution(day.timeline, next_day, day.date, meta=day.meta)
        self.assertEqual(day.timeline[1].execution_state, "planned")
        text = self.runtime.build_hidden_life_context(day, next_day, True)
        self.assertIn("09-26 00:50", text)
        self.assertIn("HiddenRestDelay", text)
        self.assertNotIn("当前: 23:30", text)
        after = datetime.datetime(2026, 9, 26, 0, 50)
        reconcile_timeline_execution(day.timeline, after, day.date, meta=day.meta)
        self.assertEqual(day.timeline[1].execution_state, "active")

    def test_repeated_request_cannot_extend_forever(self):
        activate_rest_delay(self.day, "晚点睡", self.now, event_key="session:1")
        self.assertFalse(
            activate_rest_delay(
                self.day,
                "晚点睡",
                self.now + datetime.timedelta(minutes=20),
                event_key="session:1",
            )
        )
        self.assertTrue(
            activate_rest_delay(
                self.day,
                "晚点睡",
                self.now + datetime.timedelta(minutes=100),
                event_key="session:2",
            )
        )
        self.assertEqual(
            timeline_item_datetime(
                self.day.timeline[1], self.day.date, meta=self.day.meta
            ),
            self.now + datetime.timedelta(minutes=120),
        )
        self.assertFalse(
            activate_rest_delay(
                self.day,
                "晚点睡",
                self.now + datetime.timedelta(minutes=121),
                event_key="session:3",
            )
        )

    def test_cancel_with_go_to_sleep(self):
        activate_rest_delay(self.day, "晚点睡", self.now)
        now = self.now + datetime.timedelta(minutes=10)
        self.assertTrue(activate_rest_delay(self.day, "那现在去睡吧", now))
        self.assertEqual(format_rest_delay_hint(self.day, now), "")
        self.assertIs(
            get_current_timeline_status(
                self.day.timeline, now, self.day.date, meta=self.day.meta
            )[0],
            self.day.timeline[1],
        )

    def test_unrelated_and_terminal_nodes_are_untouched(self):
        for activity in ["起床洗漱", "回屋看电影", "躺在沙发上聊天"]:
            with self.subTest(activity=activity):
                day = make_day()
                day.timeline[1].activity = activity
                day.timeline[1].status = "困倦想睡"
                self.assertFalse(activate_rest_delay(day, "晚点睡", self.now))
        for state in ["completed", "expired", "skipped", "cancelled"]:
            with self.subTest(state=state):
                day = make_day()
                day.timeline[1].execution_state = state
                self.assertFalse(activate_rest_delay(day, "晚点睡", self.now))
        day = make_day(time="23:30")
        self.assertFalse(activate_rest_delay(day, "再聊一会", self.now))

    def test_reordering_or_replacing_timeline_does_not_defer_wrong_node(self):
        activate_rest_delay(self.day, "晚点睡", self.now)
        self.day.timeline.reverse()
        current, next_item = get_current_timeline_status(
            self.day.timeline, self.now, self.day.date, meta=self.day.meta
        )
        self.assertIsNone(current)
        self.assertEqual(next_item.activity, "回屋准备睡觉")
        self.day.timeline[0] = TimelineItem(time="20:30", activity="准备看电影")
        current, _ = get_current_timeline_status(
            self.day.timeline, self.now, self.day.date, meta=self.day.meta
        )
        self.assertEqual(current.activity, "准备看电影")
        self.assertEqual(format_rest_delay_hint(self.day, self.now), "")

    def test_rest_sequence_is_deferred_together(self):
        self.day.timeline = [
            TimelineItem(time="20:20", activity="上床休息"),
            TimelineItem(time="20:40", activity="关灯睡觉"),
        ]
        activate_rest_delay(self.day, "晚点睡", self.now)
        current, next_item = get_current_timeline_status(
            self.day.timeline, self.now, self.day.date, meta=self.day.meta
        )
        self.assertIsNone(current)
        self.assertIsNotNone(next_item)
        self.assertTrue(
            all(item.execution_state == "planned" for item in self.day.timeline)
        )

    def test_fatigue_and_next_commitment_limit_delay(self):
        self.day.state = LifeState(sleepiness=90, energy=10)
        original = self.day.state.as_dict()
        activate_rest_delay(self.day, "晚点睡", self.now)
        self.assertEqual(
            timeline_item_datetime(
                self.day.timeline[1], self.day.date, meta=self.day.meta
            ),
            self.now + datetime.timedelta(minutes=15),
        )
        self.assertEqual(self.day.state.as_dict(), original)
        day = make_day()
        day.timeline.append(TimelineItem(time="21:10", activity="线上会议"))
        activate_rest_delay(day, "晚点睡", self.now)
        self.assertEqual(
            timeline_item_datetime(day.timeline[1], day.date, meta=day.meta).strftime(
                "%H:%M"
            ),
            "21:10",
        )

    def test_last_rest_window_moves_by_same_amount(self):
        self.day.meta["life_window_end"] = "21:00"
        activate_rest_delay(self.day, "晚点睡", self.now)
        after = datetime.datetime(2026, 9, 25, 22, 25)
        reconcile_timeline_execution(
            self.day.timeline,
            after,
            self.day.date,
            meta=self.day.meta,
            timeline_end="21:00",
        )
        self.assertEqual(self.day.timeline[1].execution_state, "elapsed")


class RestDelayPersistenceTest(
    RuntimeAsyncHelperMixin, unittest.IsolatedAsyncioTestCase
):
    async def test_private_revisit_reads_same_deferred_window(self):
        runtime, _ = self._make_proactive_runtime()
        now = datetime.datetime(2026, 9, 25, 20, 50)
        day = make_day()
        activate_rest_delay(day, "晚点睡", now)
        await runtime.archive.save_day(day)
        text, available = await runtime._private_revisit_life_context(
            "aiocqhttp:FriendMessage:1", now
        )
        self.assertTrue(available)
        self.assertIn("21:50", text)
        self.assertIn("原定休息暂缓", text)
        self.assertNotIn("正在: 回屋准备睡觉", text)
        self.assertNotIn("当前: 20:30", text)

    async def test_atomic_update_uses_latest_state_and_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = LifeArchive(Path(tmp) / "life.db")
            runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
            runtime.archive = archive
            runtime.mark_page_status_changed = AsyncMock()
            day = make_day()
            await archive.save_day(day)
            stale = await archive.get_day(day.date)

            def newer_state(latest):
                latest.state = LifeState(
                    energy=65, sleepiness=40, summary="新的身体状态"
                )

            await archive.mutate_day(day.date, newer_state)
            now = datetime.datetime(2026, 9, 25, 20, 50)
            saved = await runtime._apply_rest_delay_message(
                stale, "晚点睡", now, event_key="private:1"
            )
            loaded = await archive.get_day(day.date)
            self.assertEqual(loaded.state.summary, "新的身体状态")
            self.assertEqual(loaded.meta, saved.meta)
            self.assertEqual(loaded.timeline[1].execution_state, "planned")
            self.assertIn("21:50", format_rest_delay_hint(loaded, now))
            runtime.mark_page_status_changed.assert_awaited_once_with("state")
            revision = loaded.revision
            again = await runtime._apply_rest_delay_message(
                stale,
                "晚点睡",
                now + datetime.timedelta(minutes=10),
                event_key="private:1",
            )
            self.assertEqual(again.revision, revision)
            archive.close()

    async def test_normal_injection_records_request_before_building_context(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.mark_page_status_changed = AsyncMock()
        now = datetime.datetime(2026, 9, 25, 20, 50)
        runtime._life_injection_now = lambda: now
        day = make_day()
        runtime.resolve_injection_target = AsyncMock(return_value=(day.date, False))
        runtime.ensure_injection_day_data = AsyncMock(return_value=day)

        async def mutate(_date, operation):
            operation(day)
            return day

        runtime.archive = type(
            "Archive", (), {"mutate_day": staticmethod(AsyncMock(side_effect=mutate))}
        )()
        runtime.maybe_update_injection_outfit = AsyncMock(return_value=day)
        runtime._schedule_chat_state_refresh = lambda *args: None

        async def build(data, *args):
            return format_rest_delay_hint(data, now)

        runtime._build_available_life_context = build
        runtime.build_chat_style_injection_context = AsyncMock(return_value="")
        runtime.friend_reference_injection_context = lambda *args: ""
        runtime._voice_expression_channel_enabled = lambda *args: False
        runtime._append_visual_input_anchor = lambda *args: None
        runtime._append_video_input_anchor = lambda *args: None
        runtime.event_was_recalled = lambda *args: False
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:1", message_id="r1")
        event.message_str = "你就不能再晚点睡吗"
        req = ProviderRequest()
        await runtime.inject_life_context(req, event)
        self.assertIn("21:50", req.system_prompt)
        self.assertTrue(getattr(event, "_daily_life_rest_timing_applied"))
        await runtime.inject_life_context(ProviderRequest(), event)
        group_event = Event(
            group_id="100", unified_msg_origin="aiocqhttp:GroupMessage:100"
        )
        group_event.message_str = "再聊两小时"
        await runtime.inject_life_context(ProviderRequest(), group_event)
        runtime.event_was_recalled = lambda *args: True
        await runtime.inject_life_context(ProviderRequest(), Event())
        runtime.archive.mutate_day.assert_awaited_once()
