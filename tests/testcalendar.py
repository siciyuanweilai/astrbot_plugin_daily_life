import datetime
import unittest

from core.life.calendar import format_calendar_context, format_season_context
from support import DailyLifeRuntime, LifeSettings


class CalendarContextTests(unittest.TestCase):
    def test_qixi_and_summer_are_calendar_facts(self):
        calendar = format_calendar_context(datetime.date(2026, 8, 19))
        season = format_season_context(datetime.date(2026, 8, 19))

        self.assertIn("传统节日：七夕", calendar)
        self.assertNotIn("季节：", calendar)
        self.assertTrue(season.startswith("夏季；"))

    def test_missing_life_context_includes_calendar_facts(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})

        text = runtime.build_missing_life_context(
            datetime.datetime(2026, 8, 19, 12, 30),
            "2026-08-19",
            using_extended_night=False,
        )

        self.assertIn("[HiddenCalendar]", text)
        self.assertIn("[HiddenSeason]", text)
        self.assertIn("传统节日：七夕", text)
        self.assertLess(text.index("[HiddenTime]"), text.index("[HiddenCalendar]"))
        self.assertLess(text.index("[HiddenCalendar]"), text.index("[HiddenSeason]"))
        self.assertLess(text.index("[HiddenSeason]"), text.index("</daily_life>"))
