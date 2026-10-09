import datetime
import json
import sqlite3
from core.archive.migrations import SCHEMA_VERSION
import tempfile
import unittest

from core.interface.portal.line import PortalLineMixin
from core.interface.view import PageViewMixin
from core.life.audit import DailyLocationAuditMixin
from core.life.invite import InviteMixin
from core.life.outfit import OutfitMixin
from core.life.restdelay import activate_rest_delay
from core.life.settlement import LifeActionMixin
from core.life.tools import (
    get_current_timeline_status,
    reconcile_timeline_execution,
    timeline_item_datetime,
)
from core.models import (
    DayRecord,
    LifeState,
    TimelineItem,
)
from core.runtime.layer.text import LayerTextMixin
from core.runtime.veil import InjectVeilMixin
from support import LifeArchive


class MidnightTimelineTest(unittest.TestCase):
    def test_route_delay_retains_next_day_after_clock_wrap(self):
        items = [{"time": "21:25"}, {"time": "22:20"}]
        DailyLocationAuditMixin._shift_timeline(
            [{"item": item} for item in items],
            [21 * 60 + 25, 22 * 60 + 20],
            start_index=0,
            shift_minutes=112,
        )
        self.assertEqual([(item["time"], item["day_offset"]) for item in items], [("23:17", 0), ("00:12", 1)])
        self.assertEqual(timeline_item_datetime(items[1], "2026-10-03"), datetime.datetime(2026, 10, 4, 0, 12))

    def test_clock_does_not_expire_tomorrows_rest_this_morning(self):
        items = [
            TimelineItem(time="07:30", activity="醒来"),
            TimelineItem(time="23:17", activity="翻看照片"),
            TimelineItem(time="00:12", activity="准备休息", activity_kind="rest", duration_minutes=55),
        ]
        reconcile_timeline_execution(items, datetime.datetime(2026, 10, 3, 7, 42), "2026-10-03")
        self.assertEqual(items[-1].day_offset, 1)
        self.assertEqual(items[-1].execution_state, "planned")
        reconcile_timeline_execution(items, datetime.datetime(2026, 10, 4, 0, 20), "2026-10-03")
        self.assertEqual(items[-1].execution_state, "active")
        self.assertNotEqual(items[-1].execution_state, "completed")
        reconcile_timeline_execution(items, datetime.datetime(2026, 10, 4, 1, 10), "2026-10-03")
        self.assertEqual(items[-1].execution_state, "elapsed")

    def test_activity_spanning_midnight_keeps_its_duration(self):
        item = TimelineItem(time="23:50", activity="一起看电影", duration_minutes=60)
        reconcile_timeline_execution([item], datetime.datetime(2026, 10, 4, 0, 10), "2026-10-03")
        self.assertEqual(item.execution_state, "active")

    def test_invite_and_panel_preserve_next_day_order(self):
        items = [TimelineItem(time="23:17", activity="翻照片", day_offset=0), TimelineItem(time="00:12", activity="休息", day_offset=1)]
        past, future = InviteMixin._split_timeline_at(items, datetime.datetime(2026, 10, 3, 23, 30), "2026-10-03")
        self.assertEqual([item.time for item in past], ["23:17"])
        self.assertEqual([item.time for item in future], ["00:12"])
        edited, _, issue = InviteMixin._apply_timeline_edits(future, [{"operation": "replace", "target_time": "00:12", "item": {"time": "00:25", "activity": "晚点休息"}}])
        self.assertEqual(issue, "")
        self.assertEqual(edited[0].day_offset, 1)
        saved = PortalLineMixin._page_validate_timeline([item.as_dict() for item in reversed(items)])
        self.assertEqual([item.time for item in saved], ["23:17", "00:12"])

    def test_rest_delay_selects_next_day_node_without_sleeping_by_clock(self):
        item = TimelineItem(time="00:12", activity="准备休息", activity_kind="rest", day_offset=1)
        day = DayRecord(date="2026-10-03", timeline=[item], state=LifeState(energy=55, sleepiness=40))
        now = datetime.datetime(2026, 10, 4, 0, 5)
        self.assertTrue(activate_rest_delay(day, 30, now, target_times=["00:12"], evidence="双方还想继续聊天"))
        self.assertEqual(timeline_item_datetime(item, day.date, meta=day.meta), datetime.datetime(2026, 10, 4, 0, 35))
        self.assertEqual(item.execution_state, "planned")
        self.assertEqual(day.state.sleepiness, 40)

    def test_outfit_change_requires_completed_schedule_evidence(self):
        item = TimelineItem(time="00:12", activity="换上睡衣", day_offset=1, execution_state="elapsed")
        result = {"change_evidence": {"kind": "explicit_outfit_change", "source": "occurred_schedule", "timeline_time": "00:12", "quote": "换上睡衣"}}
        context = {"occurred_timeline_items": [item], "timeline_date": "2026-10-03", "old_meta": {}}
        self.assertEqual(OutfitMixin._verified_outfit_change_source(result, context), "")
        item.execution_state = "completed"
        self.assertEqual(OutfitMixin._verified_outfit_change_source(result, context), "occurred_schedule")

    def test_hidden_context_keeps_actual_date_and_plan_labels(self):
        item = TimelineItem(time="00:12", activity="准备休息", day_offset=1, execution_state="active")
        class HiddenTextHarness(InjectVeilMixin, LayerTextMixin):
            pass

        text = HiddenTextHarness()._format_hidden_schedule_window([item], datetime.datetime(2026, 10, 4, 0, 20), timeline_date="2026-10-03")
        self.assertIn("10-04 00:12", text)
        self.assertIn("当前计划", text)
        self.assertIn("不是执行证据", text)
        display = PageViewMixin()._page_day(DayRecord(date="2026-10-03", timeline=[item]), datetime.datetime(2026, 10, 4, 0, 20), False)
        self.assertEqual(display["timeline"][0]["display_time"], "10-04 00:12")
        self.assertEqual(display["timeline"][0]["scheduled_at"], "2026-10-04 00:12")

    def test_near_term_anchor_can_be_replanned_after_midnight(self):
        item = TimelineItem(time="00:12", activity="休息", day_offset=1)
        day = DayRecord(date="2026-10-03", timeline=[item])
        engine = LifeActionMixin()
        now = datetime.datetime(2026, 10, 4, 0, 5)
        anchors = engine.refine_upcoming_anchors(day, now=now)
        self.assertEqual(len(anchors), 1)
        result = engine.replan_future_anchors(day, [{"replaces_anchor_id": anchors[0].anchor_id, "time": "00:40", "activity": "聊完再休息"}], now=now)
        self.assertEqual(result.status, "applied")
        self.assertEqual(timeline_item_datetime(item, day.date), datetime.datetime(2026, 10, 4, 0, 40))


class MidnightArchiveTest(unittest.IsolatedAsyncioTestCase):
    async def test_offset_round_trip_preserves_current_and_next_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = LifeArchive(directory + "/life.db")
            try:
                items = [TimelineItem(time="23:17", activity="翻照片"), TimelineItem(time="00:12", activity="休息")]
                await archive.save_day(DayRecord(date="2026-10-03", timeline=items))
                day = await archive.get_day("2026-10-03")
                self.assertEqual([item.day_offset for item in day.timeline], [None, 1])
                current, upcoming = get_current_timeline_status(day.timeline, datetime.datetime(2026, 10, 3, 23, 30), day.date)
                self.assertEqual(current.time, "23:17")
                self.assertEqual(upcoming.time, "00:12")
            finally:
                archive.close()

    async def test_migration_repairs_displaced_midnight_node_and_action_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/life.db"
            archive = LifeArchive(path)
            await archive.save_day(DayRecord(date="2026-10-03", meta={"life_window_start": "07:30", "planned_life_actions": json.dumps([{"action_id": "go-out", "timeline_index": 3}])}))
            archive.close()
            with sqlite3.connect(path) as connection:
                connection.execute("ALTER TABLE timelines DROP COLUMN day_offset")
                connection.execute("UPDATE meta SET value = '18' WHERE key = 'schema_version'")
                for index, (time, activity) in enumerate([("07:30", "醒来"), ("08:30", "准备出门"), ("00:12", "准备休息"), ("09:10", "出门"), ("23:17", "翻照片")]):
                    connection.execute("INSERT INTO timelines(date,sort_order,time,activity,execution_state,execution_updated_at) VALUES (?,?,?,?,?,?)", ("2026-10-03", index, time, activity, "elapsed", "2026-10-03 07:42"))
            migrated = LifeArchive(path)
            try:
                day = await migrated.get_day("2026-10-03")
                self.assertEqual([item.time for item in day.timeline], ["07:30", "08:30", "09:10", "23:17", "00:12"])
                self.assertEqual(day.timeline[-1].day_offset, 1)
                self.assertEqual(day.timeline[-1].execution_state, "planned")
                self.assertEqual(json.loads(day.meta["planned_life_actions"])[0]["timeline_index"], 2)
                self.assertEqual(migrated._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], str(SCHEMA_VERSION))
            finally:
                migrated.close()
