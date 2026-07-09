import unittest

from runtimehelpers import *


class RuntimeStateTest(unittest.TestCase):
    def test_hidden_context_uses_daily_life_tag(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
        )

        self.assertIn("<daily_life>", text)
        self.assertIn("</daily_life>", text)
        self.assertIn("[HiddenActivityHint]", text)
        self.assertIn("[HiddenContextRules]", text)
        self.assertIn("隐藏上下文只用于保持角色处境", text)
        self.assertIn("[HiddenScheduleWindow]", text)
        self.assertIn("全天索引", text)
        self.assertNotIn("[HiddenScheduleMemory]", text)
        self.assertEqual(text.count("隐藏上下文只用于保持角色处境"), 1)
        self.assertNotIn("<expression_channel>", text)
    def test_event_helpers_unwrap_tool_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        inner_event = Event(
            sender_name="小林",
            sender_id="10000001",
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
            group_name="测试群",
            message_id="abc123",
        )
        tool_context = types.SimpleNamespace(context=types.SimpleNamespace(event=inner_event))

        self.assertEqual(runtime._event_profile_id(tool_context), "10000001")
        self.assertEqual(runtime._event_platform_user(tool_context), ("aiocqhttp", "10000001"))
        self.assertEqual(runtime._event_group_meta(tool_context), ("10001", "测试群"))
        self.assertEqual(runtime._event_message_id(tool_context), "abc123")
    def test_hidden_context_can_include_group_awareness(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            group_awareness_context=(
                "[HiddenGroupAwareness]\n"
                "- 看展小群: 平稳/偶尔看一眼/轻量判断; 参与欲35, 复杂度42, 理解88; Bob 准备看展\n"
                "[HiddenActionJudgement]\n"
                "- 保存记忆 [群友档案/已理解]: 观察为主；这条是 Bob 的信息"
            ),
        )

        self.assertIn("[HiddenGroupChatAwareness]", text)
        self.assertIn("看展小群", text)
        self.assertIn("这条是 Bob 的信息", text)
    def test_hidden_context_can_include_state(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            state=LifeState.from_value(
                {
                    "energy": 30,
                    "mood": "有点累",
                    "busyness": 80,
                    "social": 20,
                    "sleep": {"quality": 40, "summary": "昨晚睡得浅"},
                    "summary": "今天不太想出门",
                    "updated_at": "2026-05-24 12:00",
                    "source": "daily",
                }
            ),
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
        )

        self.assertIn("[HiddenState]", text)
        self.assertIn("体力 30/100", text)
        self.assertIn("今天不太想出门", text)
        self.assertNotIn("回复风格约束", text)
        self.assertNotIn("[HiddenAttentionState]", text)
    def test_hidden_context_keeps_fast_changing_parts_after_stable_daily_parts(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 45,
                }
            }
        )
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            memo="晚上记得取快递",
            meta={"theme": "雨后散步", "mood": "松弛"},
            state=LifeState.from_value(
                {
                    "energy": 30,
                    "mood": "有点累",
                    "summary": "今天不太想出门",
                }
            ),
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            world_context="[HiddenPlaces]\n- 常去咖啡店：出现 2 次",
            group_awareness_context="[HiddenGroupAwareness]\n- 测试群：轻量观察",
            experience_context="[HiddenLifeEpisode]\n- 昨天雨后散步",
            memos_context="[Memo]\n- 喜欢靠窗座位",
            recent_video="[Video]\n- 刚总结过一个咖啡店视频",
            structured="[Message]\n- 小林: 今天还去咖啡店吗？",
        )

        self.assertLess(text.index("[HiddenContextRules]"), text.index("[HiddenAppearanceHint]"))
        self.assertLess(text.index("[HiddenChatStyle]"), text.index("[HiddenAppearanceHint]"))
        self.assertLess(text.index("[HiddenAppearanceHint]"), text.index("[HiddenMoodHint]"))
        self.assertLess(text.index("[HiddenMoodHint]"), text.index("[HiddenScheduleWindow]"))
        self.assertLess(text.index("[HiddenScheduleWindow]"), text.index("[HiddenWeather]"))
        self.assertLess(text.index("[HiddenWeather]"), text.index("[HiddenMemoHint]"))
        self.assertLess(text.index("[HiddenMemoHint]"), text.index("[HiddenWorldMemory]"))
        self.assertLess(text.index("[HiddenWorldMemory]"), text.index("[HiddenLifeExperience]"))
        self.assertLess(text.index("[HiddenLifeExperience]"), text.index("[HiddenExternalMemory]"))
        self.assertLess(text.index("[HiddenExternalMemory]"), text.index("[HiddenStatusHint]"))
        self.assertLess(text.index("[HiddenStatusHint]"), text.index("[HiddenActivityHint]"))
        self.assertLess(text.index("[HiddenActivityHint]"), text.index("[HiddenTime]"))
        self.assertLess(text.index("[HiddenTime]"), text.index("[HiddenState]"))
        self.assertLess(text.index("[HiddenState]"), text.index("[HiddenGroupChatAwareness]"))
        self.assertLess(text.index("[HiddenGroupChatAwareness]"), text.index("[HiddenRecentVideoUnderstanding]"))
        self.assertLess(text.index("[HiddenRecentVideoUnderstanding]"), text.index("[HiddenStructuredConversation]"))
        self.assertLess(text.index("[HiddenStructuredConversation]"), text.index("</daily_life>"))
        self.assertLess(text.index("</daily_life>"), text.index("<expression_channel>"))
    def test_hidden_schedule_window_keeps_compact_index_before_current_context(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            timeline=[
                TimelineItem(time="08:00", activity="起床洗漱", status="清醒"),
                TimelineItem(time="12:00", activity="在厨房煮清汤面", status="温和"),
                TimelineItem(time="20:30", activity="洗完碗整理餐桌", status="清爽"),
                TimelineItem(time="23:00", activity="关灯睡觉", status="放松"),
            ],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 19, 35),
            using_extended_night=False,
        )

        self.assertIn("[HiddenScheduleWindow]", text)
        self.assertIn("当前: 12:00 在厨房煮清汤面 [温和]", text)
        self.assertIn("接下来: 20:30 洗完碗整理餐桌 [清爽]", text)
        self.assertIn("全天索引", text)
        self.assertLess(text.index("全天索引"), text.index("当前: 12:00 在厨房煮清汤面 [温和]"))
        self.assertNotIn("[HiddenScheduleMemory]", text)
    def test_hidden_experience_context_can_include_physiological_rhythm_history(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        text = runtime._format_hidden_experience_context(
            physiological_rhythm_logs=[
                PhysiologicalRhythmLogRecord(
                    date="2026-07-05",
                    body_label="普通瞬时疲惫",
                    body_intensity=12,
                    summary="只是一小段 transient",
                    lifecycle_kind="transient",
                ),
                PhysiologicalRhythmLogRecord(
                    date="2026-07-05",
                    body_label="轻微疲惫",
                    body_intensity=36,
                    social_battery=42,
                    attention_state="低刺激更舒服",
                    summary="适合低强度恢复",
                    lifecycle_kind="short_term",
                )
            ],
            physiological_rhythm_trend={"summary": "近7天平均身体负荷 36/100"},
        )

        self.assertIn("[HiddenPhysiologicalRhythm]", text)
        self.assertIn("轻微疲惫", text)
        self.assertIn("近7天平均身体负荷", text)
        self.assertNotIn("普通瞬时疲惫", text)
    def test_state_update_prompt_omits_empty_rhythm_trend_context(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-07-05",
            weather="多云",
            timeline=[TimelineItem(time="12:00", activity="午后休息", status="平稳")],
            state=LifeState.from_value({"summary": "状态平稳"}),
        )

        prompt = runtime._build_state_update_prompt(
            data,
            datetime.datetime(2026, 7, 5, 12, 0),
            "test",
            rhythm_context="",
        )

        self.assertNotIn("近期生理节律：\n暂无", prompt)
        self.assertNotIn("近期生理节律：\n\n", prompt)
    def test_extended_night_uses_autonomous_life_mode(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            outfit="宽松白色长T恤",
            timeline=[TimelineItem(time="01:30", activity="还在窗边写设定", status="清醒")],
            meta={"life_mode": "late_night", "sleep_mode": "late_night"},
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 25, 1, 40),
            using_extended_night=True,
        )

        self.assertIn("late_night", text)
        self.assertIn("当前是否清醒仍按实时状态和时间轴判断", text)


class RuntimeStateAsyncTest(RuntimeAsyncHelperMixin, unittest.IsolatedAsyncioTestCase):
    async def test_resolve_injection_target_uses_today_log_when_extended_night_has_no_yesterday(self):
        from core.runtime.mirror import tempo

        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"schedule_time": "07:00"})
        runtime.archive = DataManager()
        now = datetime.datetime(2026, 7, 5, 1, 44)

        old_debug = tempo.logger.debug
        tempo.logger.debug = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            target_date, extended = await runtime.resolve_injection_target(now)
            target_date_again, extended_again = await runtime.resolve_injection_target(
                datetime.datetime(2026, 7, 5, 1, 45)
            )
        finally:
            tempo.logger.debug = old_debug

        self.assertEqual(target_date, "2026-07-05")
        self.assertFalse(extended)
        self.assertEqual(target_date_again, "2026-07-05")
        self.assertFalse(extended_again)
        self.assertEqual(len(messages), 1)
        self.assertIn("未找到可延续的昨日记录", messages[-1])
        self.assertIn("改用当前日期记录: 2026-07-05", messages[-1])
        self.assertNotIn("准备生成今日数据", messages[-1])
    async def test_daily_refresh_generates_resolved_dashboard_target_date(self):
        from core.models import LifeDecisionRecord

        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date="2026-07-05",
                timeline=[TimelineItem(time="22:30", activity="准备休息")],
            )
        )
        calls = []
        reviews = []

        class Composer:
            async def compose_daily_review(self, date):
                reviews.append(date)

            async def generate_daily(self, date=None, force=False, **kwargs):
                calls.append((date, force))
                date_str = date.strftime("%Y-%m-%d")
                await archive.save_life_decision(
                    LifeDecisionRecord(
                        date=date_str,
                        kind="daily_plan",
                        subject=date_str,
                        decision="定时刷新后的生活安排",
                        reason="沿用面板目标日期生成",
                        evidence="定时刷新目标日期",
                        outcome="写入同一天的生活观察",
                    )
                )
                return DayRecord(date=date_str)

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"rhythm_config": {"schedule_time": "07:30"}})
        runtime.archive = archive
        runtime.archive.cleanup_by_storage_policy = lambda storage: async_return([])
        runtime.composer = Composer()
        runtime.generation_lock = asyncio.Lock()
        runtime.mark_page_status_changed = lambda reason="": async_return(1)
        runtime.maintain_sight_cache = lambda: async_return(None)
        runtime.maintain_emoji_assets = lambda: async_return(None)
        runtime.maintain_plugin_file_cache = lambda: async_return(None)

        now = datetime.datetime(2026, 7, 6, 1, 20)
        with patch("core.runtime.spine.pulse.life_now", return_value=now):
            await runtime.run_daily_refresh()

        self.assertEqual(reviews, ["2026-07-05"])
        self.assertEqual(len(calls), 1)
        target_dt, force = calls[0]
        self.assertTrue(force)
        self.assertEqual(target_dt, datetime.datetime(2026, 7, 5, 1, 20))
        decisions = await archive.get_life_decisions(limit=5, kind="daily_plan", date="2026-07-05")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, "定时刷新后的生活安排")
    async def test_injection_reuses_short_lived_snapshot_without_hiding_current_message(self):
        provider = Provider([])

        class CountingArchive(DataManager):
            def __init__(self):
                super().__init__()
                self.relationship_calls = 0

            async def get_recent_relationships(self, limit=8):
                self.relationship_calls += 1
                return await super().get_recent_relationships(limit)

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {},
                "state_config": {"enabled": False},
            }
        )
        runtime.archive = CountingArchive()
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            note="喜欢看展",
            date_str="2026-05-24",
        )
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅蓝外套",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler(normal_limit=4, chat_limit=1)
        runtime._injection_snapshot_cache = {}
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = type("Composer", (), {})()
        runtime.resolve_injection_target = lambda now: async_return(("2026-05-24", False))
        runtime.maybe_collect_emoji_assets_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_commitment_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_chat_memory_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        event1 = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event1.message_str = "周末去看展吧"
        event2 = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event2.message_str = "下午场也可以"
        req1 = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session_1"})()
        req2 = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session_2"})()

        await runtime.inject_life_context(req1, event1)
        await runtime.inject_life_context(req2, event2)

        self.assertEqual(runtime.archive.relationship_calls, 1)
        self.assertIn("喜欢看展", req2.system_prompt)
        self.assertIn("<daily_life>", req2.system_prompt)
        await asyncio.gather(*list(runtime._background_scheduler.tasks))
    async def test_injection_missing_day_generates_in_background_with_anti_fabrication_context(self):
        generation_started = asyncio.Event()
        allow_generation = asyncio.Event()

        class Composer:
            def __init__(self):
                self.calls = 0

            async def generate_daily(self, date=None, force=False, target_hour=None, extra=None):
                self.calls += 1
                generation_started.set()
                await allow_generation.wait()
                day = DayRecord(
                    date=date.strftime("%Y-%m-%d"),
                    outfit="浅蓝外套",
                    timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
                )
                await runtime.archive.save_day(day)
                return day

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = Composer()
        runtime.mark_page_status_changed = lambda reason: async_return(1)
        runtime.resolve_injection_target = lambda now: async_return((now.strftime("%Y-%m-%d"), False))
        req = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session"})()

        await runtime.inject_life_context(req)
        await asyncio.wait_for(generation_started.wait(), timeout=1)

        self.assertEqual(runtime.composer.calls, 1)
        self.assertIn("<daily_life>", req.system_prompt)
        self.assertIn("[HiddenScheduleUnavailable]", req.system_prompt)
        self.assertIn("禁止编造今天正在做什么", req.system_prompt)
        self.assertNotIn("[HiddenScheduleMemory]", req.system_prompt)
        self.assertEqual(runtime.archive.days, {})

        allow_generation.set()
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

        self.assertEqual(len(runtime.archive.days), 1)
    async def test_injection_no_longer_runs_rule_based_period_update(self):
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="白天居家裙",
                time_period="afternoon",
                meta={"life_mode": "late_night", "sleep_mode": "late_night"},
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.calls = []

            async def update_outfit(self, date_str, period, current_time=None):
                self.calls.append((date_str, period, current_time))
                day = await self.archive.get_day(date_str)
                day.time_period = period
                day.outfit = "LLM 自主判断后的夜间状态"
                await self.archive.save_day(day)
                return day

        composer = Composer(archive)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._get_curr_period = lambda: "night"

        data = await runtime.maybe_update_injection_outfit(
            "2026-05-24",
            await archive.get_day("2026-05-24"),
            using_extended_night=False,
        )

        self.assertEqual(composer.calls, [])
        self.assertEqual(data.time_period, "afternoon")
        self.assertEqual(data.outfit, "白天居家裙")
        self.assertEqual(data.meta["life_mode"], "late_night")
    async def test_auto_life_update_asks_llm_on_refresh_interval(self):
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date=datetime.datetime.now().strftime("%Y-%m-%d"),
                outfit="白天居家裙",
                time_period="afternoon",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料")],
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.calls = []

            async def update_outfit(self, date_str, period, current_time=None):
                self.calls.append((date_str, period, current_time))
                day = await self.archive.get_day(date_str)
                day.time_period = period
                day.outfit = "LLM 自主检查后的状态"
                await self.archive.save_day(day)
                return day

        composer = Composer(archive)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True, "refresh_minutes": 30, "quiet_hours": ""},
            }
        )
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.try_update_weather = lambda today_str: async_return(None)
        runtime.resolve_injection_target = lambda now: async_return((datetime.datetime.now().strftime("%Y-%m-%d"), False))
        runtime._get_curr_period = lambda now=None: "afternoon"

        async def refresh_state_for_day(date_str, data, now, source="", detail="", force=False, notify_page=True):
            runtime.refresh_call = (date_str, source, detail, force, notify_page)
            return data

        runtime.refresh_state_for_day = refresh_state_for_day

        await runtime.check_autonomous_life_update()

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        stored = await archive.get_day(today)
        self.assertEqual(runtime.refresh_call[1], "auto")
        self.assertFalse(runtime.refresh_call[4])
        self.assertEqual(len(composer.calls), 1)
        self.assertEqual(composer.calls[0][:2], (today, "afternoon"))
        self.assertIsInstance(composer.calls[0][2], datetime.datetime)
        self.assertEqual(stored.outfit, "LLM 自主检查后的状态")
        self.assertIn("auto_life_last_checked_at", stored.meta)

        composer.calls.clear()
        await runtime.check_autonomous_life_update()
        self.assertEqual(composer.calls, [])
    async def test_auto_life_update_skips_quiet_hours(self):
        archive = DataManager()
        today = "2026-06-24"
        await archive.save_day(
            DayRecord(
                date=today,
                outfit="白天居家裙",
                time_period="afternoon",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料")],
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.calls = []

            async def update_outfit(self, date_str, period, current_time=None):
                self.calls.append((date_str, period, current_time))
                return await self.archive.get_day(date_str)

        composer = Composer(archive)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True, "refresh_minutes": 30, "quiet_hours": "00:00-06:30"},
            }
        )
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.try_update_weather = lambda today_str: async_return(None)
        runtime.resolve_injection_target = lambda now: async_return((today, False))
        runtime._get_curr_period = lambda now=None: "afternoon"
        runtime.refresh_state_for_day = lambda *args, **kwargs: async_return(None)

        quiet_now = datetime.datetime(2026, 6, 24, 1, 20)
        runtime._runtime_now = lambda: quiet_now
        await runtime.check_autonomous_life_update()

        stored = await archive.get_day(today)
        self.assertEqual(composer.calls, [])
        self.assertNotIn("auto_life_last_checked_at", stored.meta)
    async def test_chat_state_refresh_runs_during_quiet_hours(self):
        archive = DataManager()
        today = "2026-06-24"
        await archive.save_day(
            DayRecord(
                date=today,
                outfit="白天居家裙",
                time_period="afternoon",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料")],
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.calls = []

            async def update_outfit(self, date_str, period, current_time=None):
                self.calls.append((date_str, period, current_time))
                return await self.archive.get_day(date_str)

        composer = Composer(archive)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True, "refresh_minutes": 30, "quiet_hours": "00:00-06:30"},
            }
        )
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.resolve_injection_target = lambda now: async_return((today, False))
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: async_return({})
        scheduled_tasks = []

        def schedule_background_task(coro, *args, **kwargs):
            if kwargs.get("key") == f"chat_state:{today}":
                scheduled_tasks.append((coro, kwargs))
            else:
                coro.close()
            return True

        runtime._schedule_background_task = schedule_background_task
        page_reasons = []
        runtime.mark_page_status_changed = lambda reason="": page_reasons.append(reason) or async_return(len(page_reasons))
        state_refresh_kwargs = []

        async def refresh_state_for_day(*args, **kwargs):
            state_refresh_kwargs.append(kwargs)
            return await archive.get_day(today)

        runtime.refresh_state_for_day = refresh_state_for_day
        runtime.try_update_weather = lambda *args, **kwargs: async_return(None)
        runtime._get_curr_period = lambda now=None: "night"

        class Event:
            message_str = "凌晨聊两句"

        quiet_now = datetime.datetime(2026, 6, 24, 1, 20)
        with patch("core.runtime.inject.life_now", return_value=quiet_now):
            await runtime.inject_life_context(ProviderRequest(session_id="user_session"), Event())

        self.assertEqual(len(composer.calls), 0)
        chat_state_tasks = [
            coro for coro, kwargs in scheduled_tasks if kwargs.get("key") == f"chat_state:{today}"
        ]
        self.assertEqual(len(chat_state_tasks), 1)
        await chat_state_tasks[0]
        self.assertEqual(len(composer.calls), 1)
        self.assertEqual(page_reasons, ["chat_state_refresh"])
        self.assertEqual(len(state_refresh_kwargs), 1)
        self.assertFalse(state_refresh_kwargs[0].get("notify_page"))
    async def test_chat_state_refresh_stops_before_save_when_source_recalled(self):
        provider_started = asyncio.Event()
        allow_provider = asyncio.Event()
        provider = Provider(['{"energy":22,"summary":"撤回后不应保存","mood":"闷热"}'])
        archive = DataManager()
        today = "2026-06-27"
        await archive.save_day(
            DayRecord(
                date=today,
                outfit="白天居家裙",
                time_period="afternoon",
                timeline=[TimelineItem(time="13:00", activity="在家休息")],
                state=LifeState(energy=60, summary="原状态", updated_at="2026-06-27 13:00"),
            )
        )
        saved_days = []
        original_save_day = archive.save_day

        async def save_day(day):
            saved_days.append((day.outfit, day.state.summary if day.state else "", dict(day.meta)))
            await original_save_day(day)

        archive.save_day = save_day

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, **kwargs):
                provider_started.set()
                await allow_provider.wait()
                return (await provider_arg.text_chat(prompt, session_id)).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            @staticmethod
            def _compute_sleep_continuity(previous, day):
                return (0.0, 0.0, float(day.state.energy or 0))

            async def learn_preferences_from_payload(self, *args, **kwargs):
                raise AssertionError("撤回后不应保存偏好")

            async def persist_life_events_from_payload(self, *args, **kwargs):
                raise AssertionError("撤回后不应保存生活事件")

            async def update_outfit(self, date_str, period, current_time=None, should_abort=None):
                raise AssertionError("撤回后不应继续判断穿搭")

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True, "refresh_minutes": 5, "quiet_hours": ""},
            }
        )
        runtime.archive = archive
        runtime.composer = Composer()
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.mark_page_status_changed = lambda *args, **kwargs: async_return(0)
        runtime._get_curr_period = lambda now=None: "afternoon"
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="m-hot")
        event.message_str = "这种天，好热"

        task = asyncio.create_task(
            runtime._refresh_state_for_chat_background(
                today,
                datetime.datetime(2026, 6, 27, 13, 54),
                source_event=event,
            )
        )
        await asyncio.wait_for(provider_started.wait(), timeout=1)
        recall_event = Event(unified_msg_origin=event.unified_msg_origin)
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "message_id": "m-hot",
            "user_id": "10001",
        }
        runtime.note_recalled_message(recall_event)
        allow_provider.set()
        await task

        stored = await archive.get_day(today)
        self.assertEqual(stored.state.summary, "原状态")
        self.assertEqual(stored.outfit, "白天居家裙")
        self.assertEqual(saved_days, [])
    async def test_accept_invite_schedules_outfit_update_after_timeline_change(self):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date=today,
                outfit="奶茶色居家裙",
                timeline=[TimelineItem(time="12:00", activity="在家看综艺", status="放松")],
            )
        )

        class Composer:
            def __init__(self, archive):
                self.archive = archive
                self.outfit_calls = []

            async def handle_invite(self, *args, **kwargs):
                return (
                    "想顺势出门透透气",
                    [
                        TimelineItem(time="12:00", activity="在家看综艺", status="放松"),
                        TimelineItem(time="15:00", activity="和阿林去书店闲逛", status="期待"),
                    ],
                    {"decision": "accept", "accept": True},
                )

            async def learn_preferences_from_payload(self, *args, **kwargs):
                return None

            async def persist_life_events_from_payload(self, *args, **kwargs):
                return None

            async def update_outfit(self, date_str, period, current_time=None):
                day = await self.archive.get_day(date_str)
                self.outfit_calls.append((date_str, period, current_time, day.timeline[-1].activity))
                day.outfit = "适合外出的轻便穿搭"
                day.meta["outfit_decision"] = "outdoor"
                await self.archive.save_day(day)
                return day

        scheduled = []
        page_reasons = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.memos = runtime._create_memos_service()
        runtime.archive = archive
        runtime.composer = Composer(archive)
        runtime.generation_lock = asyncio.Lock()
        runtime.contact_resolver = types.SimpleNamespace(resolve_event_sender=lambda event: async_return("阿林"))
        runtime.remember_interaction = lambda *args, **kwargs: async_return(None)
        runtime.refresh_state_for_day = lambda date_str, data, now, **kwargs: async_return(data)
        runtime._get_curr_period = lambda now=None: "afternoon"
        runtime.mark_page_status_changed = lambda reason="": page_reasons.append(reason) or async_return(1)
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

        event = Event()
        event.message_str = "下午一起出门闲逛"
        reply = await runtime.accept_user_invite(event, "下午一起出门闲逛")

        self.assertIn("已把与【阿林】的邀约加入接下来的安排", reply)
        outfit_tasks = [item for item in scheduled if item[0] == "邀约穿搭判断"]
        self.assertEqual(len(outfit_tasks), 1)
        self.assertEqual(outfit_tasks[0][:2], ("邀约穿搭判断", ""))
        stored = await archive.get_day(today)
        self.assertEqual(stored.timeline[-1].activity, "和阿林去书店闲逛")

        await outfit_tasks[0][2]

        stored = await archive.get_day(today)
        self.assertEqual(stored.outfit, "适合外出的轻便穿搭")
        self.assertEqual(len(runtime.composer.outfit_calls), 1)
        self.assertEqual(runtime.composer.outfit_calls[0][0], today)
        self.assertEqual(runtime.composer.outfit_calls[0][1], "afternoon")
        self.assertIsInstance(runtime.composer.outfit_calls[0][2], datetime.datetime)
        self.assertEqual(runtime.composer.outfit_calls[0][3], "和阿林去书店闲逛")
        self.assertEqual(page_reasons, ["invite_outfit_update"])
    async def test_apply_config_rebuilds_runtime_and_saves_config(self):
        class Config(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.saved = 0

            def save_config(self):
                self.saved += 1

        class WeatherClient:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        async def daily_task():
            return None

        async def week_refresh_task():
            return None

        async def auto_task():
            return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.raw_config = Config({"rhythm_config": {"schedule_time": "07:00"}})
        runtime.generation_lock = asyncio.Lock()
        runtime.config = LifeSettings.from_dict(runtime.raw_config)
        runtime.archive = DataManager()
        runtime.weather_client = WeatherClient()
        runtime.composer = object()
        runtime.rhythm = type(
            "Rhythm",
            (),
            {
                "stopped": False,
                "stop": lambda self: setattr(self, "stopped", True),
            },
        )()
        runtime.run_daily_refresh = daily_task
        runtime.run_weekly_refresh = week_refresh_task
        runtime.check_autonomous_life_update = auto_task

        await runtime.apply_config(
            {
                "rhythm_config": {
                    "schedule_time": "08:25",
                },
                "weather_awareness": {"default_city": "上海"},
                "state_config": {"enabled": False, "refresh_minutes": 45},
            }
        )

        self.assertEqual(runtime.raw_config.saved, 1)
        self.assertEqual(runtime.raw_config["rhythm_config"]["schedule_time"], "08:25")
        self.assertEqual(runtime.config.schedule_time, "08:25")
        self.assertEqual(runtime.config.weather.default_city, "上海")
        self.assertFalse(runtime.config.state.enabled)
        self.assertTrue(runtime.weather_client is not None)
        self.assertTrue(runtime.rhythm.scheduler.running)
    async def test_refresh_state_uses_selected_state_provider(self):
        async def cleanup(session_id):
            return None
        async def learn_preferences(payload, *, date_str, source):
            return []
        async def persist_events(payload, *, date_str, source):
            return []
        async def get_provider(provider_id=""):
            if provider_id == "selected":
                return selected_provider
            return default_provider
        async def call_llm_text(provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
            resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
            return resp.get("content", "") if isinstance(resp, dict) else getattr(resp, "completion_text", "")

        default_provider = Provider([])
        selected_provider = AltProvider(
            [
                '{"energy":28,"mood":"困倦但还算平静","busyness":75,"social":18,'
                '"sleep":{"quality":35,"summary":"睡眠不足还在影响精神"},'
                '"summary":"今天更想低负担地待着"}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(default_provider, selected_provider)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"provider": "selected", "refresh_minutes": 5},
                "rhythm_config": {"llm_provider": ""},
            }
        )
        runtime.archive = DataManager()
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_call_llm_text": staticmethod(call_llm_text),
                "_cleanup_conversation": staticmethod(cleanup),
                "_compute_sleep_continuity": staticmethod(lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))),
                "learn_preferences_from_payload": staticmethod(learn_preferences),
                "persist_life_events_from_payload": staticmethod(persist_events),
            },
        )()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                weather="北京 晴 20°C",
                timeline=[TimelineItem(time="12:00", activity="在家休息", status="慢")],
                state=LifeState(updated_at="2026-05-24 11:00"),
            )
        )

        data = await runtime.refresh_state_for_day(
            "2026-05-24",
            now=datetime.datetime(2026, 5, 24, 12, 0),
            source="chat",
            force=True,
        )

        self.assertEqual(len(default_provider.prompts), 0)
        self.assertEqual(len(selected_provider.prompts), 1)
        self.assertIn("当前状态", selected_provider.prompts[0])
        self.assertIn("【通用自主原则】", selected_provider.prompts[0])
        self.assertIn("【通用状态行为原则】", selected_provider.prompts[0])
        self.assertIn("缺少明确依据时使用空字符串", selected_provider.prompts[0])
        self.assertLess(selected_provider.prompts[0].index("【通用自主原则】"), selected_provider.prompts[0].index("【眼前内容】"))
        self.assertGreater(selected_provider.prompts[0].index("当前状态"), selected_provider.prompts[0].index("【眼前内容】"))
        self.assertLess(selected_provider.prompts[0].index("生活日程日期：2026-05-24"), selected_provider.prompts[0].index("当前时间：2026-05-24 12:00"))
        self.assertLess(selected_provider.prompts[0].index("当前状态"), selected_provider.prompts[0].index("触发来源：chat"))
        self.assertEqual(data.state.energy, 28)
        self.assertEqual(data.state.source, "chat")
        self.assertEqual(data.state.updated_at, "2026-05-24 12:00")
        self.assertTrue(data.state_log)
    async def test_refresh_state_persists_and_recalls_emotion_arc(self):
        async def cleanup(session_id):
            return None
        async def learn_preferences(payload, *, date_str, source):
            return []
        async def persist_events(payload, *, date_str, source):
            return []
        async def get_provider(provider_id=""):
            return provider
        async def call_llm_text(provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
            resp = await provider_arg.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
            return getattr(resp, "completion_text", "")

        provider = Provider(
            [
                json.dumps(
                    {
                        "energy": 32,
                        "mood": "困倦但放松",
                        "mood_score": 68,
                        "sleepiness": 82,
                        "interaction_capacity": 38,
                        "sleep": {"quality": 48, "summary": "熬夜后还没完全缓过来"},
                        "summary": "更适合低强度地接话",
                        "emotion_arc": {
                            "label": "困倦但放松",
                            "valence": 30,
                            "arousal": 25,
                            "intensity": 72,
                            "stability": 66,
                            "trigger": "睡前聊天",
                            "evidence": "体力低但语气放松",
                            "influence": "更适合短句和低强度安排",
                            "expires_at": "2099-01-01 00:00:00",
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "energy": 36,
                        "mood": "慢慢回神",
                        "sleep": {"quality": 55, "summary": "仍有一点困"},
                        "summary": "状态稍微恢复",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({"state_config": {"enabled": True, "refresh_minutes": 5}})
        runtime.archive = DataManager()
        runtime.mark_page_status_changed = lambda _kind: async_return(None)
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_call_llm_text": staticmethod(call_llm_text),
                "_cleanup_conversation": staticmethod(cleanup),
                "_compute_sleep_continuity": staticmethod(lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))),
                "learn_preferences_from_payload": staticmethod(learn_preferences),
                "persist_life_events_from_payload": staticmethod(persist_events),
            },
        )()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                timeline=[TimelineItem(time="22:30", activity="准备睡觉", status="困")],
                state=LifeState(updated_at="2026-05-24 21:30"),
            )
        )

        await runtime.refresh_state_for_day(
            "2026-05-24",
            now=datetime.datetime(2026, 5, 24, 22, 30),
            source="chat",
            detail="还没睡吗",
            force=True,
        )
        arcs = await runtime.archive.get_emotion_arcs(limit=10)

        self.assertEqual(arcs[0].label, "困倦但放松")
        self.assertEqual(arcs[0].valence, 30)
        self.assertIn("低强度", arcs[0].influence)

        await runtime.refresh_state_for_day(
            "2026-05-24",
            now=datetime.datetime(2026, 5, 24, 22, 45),
            source="idle",
            force=True,
        )

        self.assertIn("近期情绪脉络", provider.prompts[1])
        self.assertIn("困倦但放松", provider.prompts[1])
        self.assertIn("更适合短句和低强度安排", provider.prompts[1])
    async def test_refresh_state_unset_provider_uses_current_default_provider(self):
        async def cleanup(session_id):
            return None
        async def learn_preferences(payload, *, date_str, source):
            return []
        async def persist_events(payload, *, date_str, source):
            return []
        async def get_provider(provider_id=""):
            if provider_id == "generation-model":
                return generation_provider
            return default_provider
        async def call_llm_text(provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
            resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
            return resp.get("content", "") if isinstance(resp, dict) else getattr(resp, "completion_text", "")

        default_provider = AltProvider(
            [
                '{"energy":36,"mood":"慢慢回神","busyness":20,"social":40,'
                '"sleep":{"quality":60,"summary":"略有困意"},'
                '"summary":"当前默认模型刷新状态"}'
            ],
            provider_id="default-model",
        )
        generation_provider = AltProvider([], provider_id="generation-model")
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(default_provider, providers={"generation-model": generation_provider})
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"provider": "", "refresh_minutes": 5},
                "rhythm_config": {"llm_provider": "generation-model"},
            }
        )
        runtime.archive = DataManager()
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_call_llm_text": staticmethod(call_llm_text),
                "_cleanup_conversation": staticmethod(cleanup),
                "_compute_sleep_continuity": staticmethod(lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))),
                "learn_preferences_from_payload": staticmethod(learn_preferences),
                "persist_life_events_from_payload": staticmethod(persist_events),
            },
        )()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                timeline=[TimelineItem(time="12:00", activity="在家休息", status="慢")],
                state=LifeState(updated_at="2026-05-24 11:00"),
            )
        )

        data = await runtime.refresh_state_for_day(
            "2026-05-24",
            now=datetime.datetime(2026, 5, 24, 12, 0),
            source="chat",
            force=True,
        )

        self.assertEqual(data.state.summary, "当前默认模型刷新状态")
        self.assertEqual(len(default_provider.prompts), 1)
        self.assertEqual(len(generation_provider.prompts), 0)
    async def test_refresh_state_keeps_daily_plan_meta_separate(self):
        provider = Provider(
            [
                (
                    '{"energy":45,"summary":"状态稳定","life_mode":"late_night",'
                    '"sleep_mode":"late_night",'
                    '"meta":{"life_mode":"sleeping","sleep_mode":"asleep"}}'
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": True},
            }
        )
        runtime.archive = DataManager()
        async def mark_page_status_changed(_kind):
            return None

        runtime.mark_page_status_changed = mark_page_status_changed
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                timeline=[TimelineItem(time="12:00", activity="在家整理资料")],
                meta={"life_mode": "resting", "sleep_mode": "normal"},
            )
        )

        async def get_provider(provider_id=""):
            return provider

        async def call_llm_text(provider_arg, prompt, session_id, **kwargs):
            return (await provider_arg.text_chat(prompt, session_id)).completion_text

        async def cleanup(session_id):
            return None

        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_call_llm_text": staticmethod(call_llm_text),
                "_cleanup_conversation": staticmethod(cleanup),
                "_compute_sleep_continuity": staticmethod(lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))),
                "learn_preferences_from_payload": staticmethod(lambda *args, **kwargs: []),
                "persist_life_events_from_payload": staticmethod(lambda *args, **kwargs: []),
            },
        )()

        data = await runtime.refresh_state_for_day(
            "2026-05-24",
            now=datetime.datetime(2026, 5, 24, 12, 0),
            force=True,
        )

        self.assertEqual(data.meta["life_mode"], "resting")
        self.assertEqual(data.meta["sleep_mode"], "normal")
        self.assertEqual(data.meta["sleep_debt"], "0")
        self.assertEqual(data.meta["energy_carryover"], "45")
