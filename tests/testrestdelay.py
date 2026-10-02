import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import support  # noqa: F401
from core.archive import LifeArchive
from core.life.restdelay import activate_rest_delay, format_rest_delay_hint
from core.life.tools import get_current_timeline_status
from core.models import DayRecord, LifeState, TimelineItem
from core.runtime.live import DailyLifeRuntime
from support import Event


def make_day():
    return DayRecord(
        date="2026-09-25",
        timeline=[
            TimelineItem(time="19:00", activity="收拾餐桌", activity_kind="other"),
            TimelineItem(time="20:30", activity="回屋准备睡觉", activity_kind="rest"),
        ],
    )


class RestDelayTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 9, 25, 20, 50)
        self.day = make_day()

    def test_selected_rest_node_is_deferred_without_changing_original_time(self):
        self.assertTrue(
            activate_rest_delay(
                self.day, 60, self.now, target_times=["20:30"],
                evidence="再聊一会", event_key="private:1",
            )
        )
        current, next_item = get_current_timeline_status(
            self.day.timeline, self.now, self.day.date, meta=self.day.meta
        )
        self.assertIs(current, self.day.timeline[0])
        self.assertNotEqual(self.day.timeline[1].execution_state, "active")
        self.assertIs(next_item, self.day.timeline[1])
        self.assertEqual(self.day.timeline[1].time, "20:30")
        self.assertIn("21:50", format_rest_delay_hint(self.day, self.now))
        self.assertFalse(
            activate_rest_delay(
                self.day, 60, self.now, target_times=["20:30"],
                event_key="private:1",
            )
        )

    def test_unselected_and_terminal_nodes_are_untouched(self):
        self.assertFalse(activate_rest_delay(self.day, 30, self.now, target_times=[]))
        self.assertFalse(
            activate_rest_delay(self.day, 30, self.now, target_times=["19:00"])
        )
        self.day.timeline[1].execution_state = "completed"
        self.assertFalse(
            activate_rest_delay(self.day, 30, self.now, target_times=["20:30"])
        )

    def test_end_delay_and_fatigue_limit(self):
        self.day.state = LifeState(energy=15, sleepiness=90)
        self.assertTrue(
            activate_rest_delay(self.day, 90, self.now, target_times=["20:30"])
        )
        self.assertIn("21:05", format_rest_delay_hint(self.day, self.now))
        later = self.now + datetime.timedelta(minutes=5)
        self.assertTrue(activate_rest_delay(self.day, 0, later))
        self.assertEqual(format_rest_delay_hint(self.day, later), "")


class RestTimingToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_update_uses_latest_day_and_event_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = LifeArchive(Path(tmp) / "life.db")
            day = make_day()
            await archive.save_day(day)
            runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
            runtime.archive = archive
            runtime.mark_page_status_changed = AsyncMock()
            now = datetime.datetime(2026, 9, 25, 20, 50)
            runtime._runtime_now = lambda: now
            runtime.resolve_injection_target = AsyncMock(return_value=(day.date, False))
            async def current_day(*_):
                return await archive.get_day(day.date)

            runtime.ensure_injection_day_data = AsyncMock(side_effect=current_day)
            event = Event(unified_msg_origin="private:1", message_id="r1")
            event.message_str = "再陪我聊会"
            result = await runtime.apply_rest_timing(event, 60, ["20:30"])
            self.assertEqual(result, "休息安排已调整。")
            saved = await archive.get_day(day.date)
            self.assertEqual(saved.timeline[1].activity_kind, "rest")
            self.assertIn("21:50", format_rest_delay_hint(saved, now))
            self.assertEqual(
                await runtime.apply_rest_timing(event, 60, ["20:30"]),
                "这段休息安排目前无法调整，请根据当前状态自然回应。",
            )
            runtime.mark_page_status_changed.assert_awaited_once_with("state")
            archive.close()
