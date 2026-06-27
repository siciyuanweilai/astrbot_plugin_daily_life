import datetime
import random
import unittest

from support import (
    get_time_period,
    get_current_timeline_status,
    resolve_business_now,
    resolve_daily_hint,
    resolve_daily_suggested,
)
from core.life.tools import get_matching_hairstyle
from core.life.surroundings import normalize_event_items, normalize_place_names


class LifeToolsTest(unittest.TestCase):
    def test_time_period_uses_eight_fine_grained_ranges(self):
        cases = [
            ((0, 0), "dawn"),
            ((5, 59), "dawn"),
            ((6, 0), "morning"),
            ((8, 59), "morning"),
            ((9, 0), "forenoon"),
            ((11, 59), "forenoon"),
            ((12, 0), "noon"),
            ((13, 59), "noon"),
            ((14, 0), "afternoon"),
            ((15, 59), "afternoon"),
            ((16, 0), "evening"),
            ((18, 59), "evening"),
            ((19, 0), "night"),
            ((21, 59), "night"),
            ((22, 0), "late_night"),
            ((23, 59), "late_night"),
        ]
        for (hour, minute), expected in cases:
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(
                    get_time_period(datetime.datetime(2026, 5, 24, hour, minute)),
                    expected,
                )

    def test_business_now_uses_schedule_time_boundary(self):
        now = datetime.datetime(2026, 5, 24, 6, 30)
        self.assertEqual(
            resolve_business_now("07:00", now).date(),
            datetime.date(2026, 5, 23),
        )
        self.assertEqual(
            resolve_business_now("07:00", now.replace(hour=7, minute=1)).date(),
            datetime.date(2026, 5, 24),
        )

    def test_current_timeline_before_first_item_returns_next_only(self):
        curr, next_item = get_current_timeline_status(
            [{"time": "09:00", "activity": "去奶茶店买草莓奶盖", "status": "期待"}],
            datetime.datetime(2026, 5, 24, 8, 0),
        )
        self.assertIsNone(curr)
        self.assertEqual(next_item["time"], "09:00")

    def test_current_timeline_status_sorts_unsorted_items(self):
        curr, next_item = get_current_timeline_status(
            [
                {"time": "18:00", "activity": "晚饭", "status": "放松"},
                {"time": "09:00", "activity": "早餐", "status": "清醒"},
                {"time": "14:00", "activity": "整理计划", "status": "专注"},
            ],
            datetime.datetime(2026, 5, 24, 15, 0),
        )
        self.assertEqual(curr["time"], "14:00")
        self.assertEqual(next_item["time"], "18:00")

    def test_current_timeline_status_anchors_extended_night_to_record_date(self):
        curr, next_item = get_current_timeline_status(
            [
                {"time": "18:20", "activity": "回家换居家服", "status": "放松"},
                {"time": "20:50", "activity": "洗完澡换睡衣", "status": "困倦"},
            ],
            datetime.datetime(2026, 6, 24, 0, 17),
            "2026-06-23",
        )
        self.assertEqual(curr["time"], "20:50")
        self.assertIsNone(next_item)

    def test_week_plan_daily_helpers_support_date_and_weekday_keys(self):
        plan = {
            "daily_hints": {
                "2026-05-24": "完整日期提示",
                "05-25": "月日提示",
                "monday": "周一提示",
                "weekend": "周末提示",
            },
            "suggested_activities": {
                "2026-05-24": ["约下午茶"],
                "monday": "整理书桌",
                "weekend": ["睡到自然醒", "看电影"],
            },
        }

        self.assertEqual(resolve_daily_hint(plan, datetime.datetime(2026, 5, 24)), "完整日期提示")
        self.assertEqual(resolve_daily_suggested(plan, datetime.datetime(2026, 5, 24)), "约下午茶")
        self.assertEqual(resolve_daily_hint(plan, datetime.datetime(2026, 5, 25)), "月日提示")
        self.assertEqual(resolve_daily_suggested(plan, datetime.datetime(2026, 5, 25)), "整理书桌")
        self.assertEqual(resolve_daily_hint(plan, datetime.datetime(2026, 5, 31)), "周末提示")

    def test_world_normalizers_use_current_field_names(self):
        self.assertEqual(
            normalize_place_names([{"name": "常去咖啡店"}, {"place": "旧地点字段"}, "字符串地点"]),
            ["常去咖啡店", "字符串地点"],
        )
        self.assertEqual(
            normalize_event_items(
                "2026-05-24",
                [
                    {"summary": "当前事件字段", "place": "常去咖啡店"},
                    {"content": "旧内容字段"},
                    {"event": "旧事件字段"},
                    "字符串事件",
                ],
            ),
            [
                {
                    "date": "2026-05-24",
                    "summary": "当前事件字段",
                    "people": [],
                    "place": "常去咖啡店",
                    "importance": "normal",
                    "source": "daily",
                },
                {
                    "date": "2026-05-24",
                    "summary": "字符串事件",
                    "people": [],
                    "place": "",
                    "importance": "normal",
                    "source": "daily",
                },
            ],
        )

    def test_get_matching_hairstyle_accepts_normalized_style_name(self):
        old_choice = random.choice
        random.choice = lambda items: items[0]
        try:
            style_map = {
                "甜美制服风": ["高马尾"],
                "元气休闲风": ["半扎发"],
            }
            self.assertEqual(
                get_matching_hairstyle(" 甜美 制服风 ", style_map, [], False),
                "高马尾",
            )
        finally:
            random.choice = old_choice

    def test_get_matching_hairstyle_uses_containment_before_fallback(self):
        old_choice = random.choice
        random.choice = lambda items: items[0]
        try:
            style_map = {
                "奶油黄·慵懒": ["低丸子头"],
                "薄荷绿·治愈": ["侧边编发"],
            }
            self.assertEqual(
                get_matching_hairstyle("奶油黄", style_map, [], False),
                "低丸子头",
            )
        finally:
            random.choice = old_choice
