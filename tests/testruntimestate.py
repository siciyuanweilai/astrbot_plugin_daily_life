# ruff: noqa: I001

import unittest

from runtimehelpers import (
    AltProvider,
    BackgroundTaskScheduler,
    Context,
    CORE_INTERNAL_SYSTEM_PROMPT,
    CommitmentRecord,
    DailyLifeRuntime,
    DataManager,
    DayRecord,
    Event,
    LifeSettings,
    LifeState,
    PhysiologicalRhythmLogRecord,
    Provider,
    ProviderRequest,
    RuntimeAsyncHelperMixin,
    TimelineItem,
    WeatherInfo,
    async_return,
    asyncio,
    datetime,
    json,
    patch,
    types,
)


class RuntimeStateTest(unittest.TestCase):
    def test_hidden_context_uses_daily_life_tag(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="测试市 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            timeline=[
                TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")
            ],
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
        tool_context = types.SimpleNamespace(
            context=types.SimpleNamespace(event=inner_event)
        )

        self.assertEqual(runtime._event_profile_id(tool_context), "10000001")
        self.assertEqual(
            runtime._event_platform_user(tool_context), ("aiocqhttp", "10000001")
        )
        self.assertEqual(runtime._event_group_meta(tool_context), ("10001", "测试群"))
        self.assertEqual(runtime._event_message_id(tool_context), "abc123")

    def test_hidden_context_can_include_group_awareness(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="测试市 晴 20°C",
            timeline=[
                TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")
            ],
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
            weather="测试市 晴 20°C",
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
            timeline=[
                TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")
            ],
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

    def test_residence_refresh_hides_stale_current_life_facts(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-08-04",
            weather="旧城市 晴 30°C",
            weather_info=WeatherInfo(temp=30, temp_desc="炎热"),
            outfit="旧城市出门穿搭",
            timeline=[
                TimelineItem(
                    time="18:00",
                    activity="去旧城市公园散步",
                    status="轻松",
                )
            ],
            meta={
                "theme": "旧城市生活",
                "mood": "橙色·轻快",
                "residence_context_stale": "true",
            },
            state=LifeState.from_value({"summary": "正在旧城市公园"}),
            memo="晚上回复朋友的消息",
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 8, 4, 18, 10),
            using_extended_night=False,
        )

        self.assertIn("[HiddenResidenceRefresh]", text)
        self.assertIn("晚上回复朋友的消息", text)
        self.assertNotIn("旧城市出门穿搭", text)
        self.assertNotIn("去旧城市公园散步", text)
        self.assertNotIn("旧城市 晴 30°C", text)
        self.assertNotIn("正在旧城市公园", text)
        self.assertNotIn("旧城市生活", text)

    def test_hidden_context_keeps_fast_changing_parts_after_stable_daily_parts(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {},
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 45,
                },
            }
        )
        data = DayRecord(
            date="2026-05-24",
            weather="测试市 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            memo="晚上记得取快递",
            meta={
                "theme": "雨后散步",
                "mood": "松弛",
                "style": "清爽日常风",
                "hair_style": "松散低马尾",
                "hair": "黑色中长直发，低马尾，碎发自然垂落",
            },
            state=LifeState.from_value(
                {
                    "energy": 30,
                    "mood": "有点累",
                    "summary": "今天不太想出门",
                }
            ),
            timeline=[
                TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")
            ],
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

        self.assertLess(
            text.index("[HiddenContextRules]"), text.index("[HiddenAppearanceHint]")
        )
        self.assertLess(
            text.index("[HiddenChatStyle]"), text.index("[HiddenAppearanceHint]")
        )
        self.assertLess(
            text.index("[HiddenAppearanceHint]"), text.index("[HiddenMoodHint]")
        )
        self.assertIn("当前穿搭：浅蓝外套和白裙子", text)
        self.assertIn("当前穿搭风格：清爽日常风", text)
        self.assertIn("当前发型名称：松散低马尾", text)
        self.assertIn("当前发型细节：黑色中长直发，低马尾，碎发自然垂落", text)
        self.assertLess(
            text.index("[HiddenMoodHint]"), text.index("[HiddenScheduleWindow]")
        )
        self.assertLess(
            text.index("[HiddenScheduleWindow]"), text.index("[HiddenWeather]")
        )
        self.assertLess(text.index("[HiddenWeather]"), text.index("[HiddenMemoHint]"))
        self.assertLess(
            text.index("[HiddenMemoHint]"), text.index("[HiddenWorldMemory]")
        )
        self.assertLess(
            text.index("[HiddenWorldMemory]"), text.index("[HiddenLifeExperience]")
        )
        self.assertLess(
            text.index("[HiddenLifeExperience]"), text.index("[HiddenExternalMemory]")
        )
        self.assertLess(
            text.index("[HiddenExternalMemory]"), text.index("[HiddenStatusHint]")
        )
        self.assertLess(
            text.index("[HiddenStatusHint]"), text.index("[HiddenActivityHint]")
        )
        self.assertLess(text.index("[HiddenActivityHint]"), text.index("[HiddenTime]"))
        self.assertLess(text.index("[HiddenTime]"), text.index("[HiddenState]"))
        self.assertLess(
            text.index("[HiddenState]"), text.index("[HiddenGroupChatAwareness]")
        )
        self.assertLess(
            text.index("[HiddenGroupChatAwareness]"),
            text.index("[HiddenRecentVideoUnderstanding]"),
        )
        self.assertLess(
            text.index("[HiddenRecentVideoUnderstanding]"),
            text.index("[HiddenStructuredConversation]"),
        )
        self.assertLess(
            text.index("[HiddenStructuredConversation]"), text.index("</daily_life>")
        )
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
        self.assertLess(
            text.index("全天索引"), text.index("当前: 12:00 在厨房煮清汤面 [温和]")
        )
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
                ),
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
            timeline=[
                TimelineItem(time="01:30", activity="还在窗边写设定", status="清醒")
            ],
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
    async def test_life_memory_context_uses_one_semantic_ranking_call(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        calls = []

        async def rank_once(query, groups, limits):
            calls.append((query, groups, limits))
            return {key: [] for key in groups}

        runtime.rank_semantic_groups = rank_once
        world_context, experience_context = await runtime._select_life_memory_contexts(
            {},
            DayRecord(
                date="2026-08-04",
                memo="晚上整理照片",
                meta={"theme": "慢节奏生活"},
            ),
            "今天打算做什么？",
        )

        self.assertEqual(len(calls), 1)
        query, groups, limits = calls[0]
        self.assertIn("今天打算做什么", query)
        self.assertIn("relationships", groups)
        self.assertIn("episodes", groups)
        self.assertIn("relationships", limits)
        self.assertIn("episodes", limits)
        self.assertEqual(world_context, "")
        self.assertEqual(experience_context, "")

    async def test_life_memory_context_excludes_pre_residence_places_and_events(self):
        class Domains:
            async def residence_boundary_date(self):
                return "2026-08-04"

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.domains = Domains()
        calls = []

        async def rank_once(query, groups, limits):
            calls.append((query, groups, limits))
            return {key: [] for key in groups}

        runtime.rank_semantic_groups = rank_once
        await runtime._select_life_memory_contexts(
            {
                "places": [
                    {"name": "旧城市公园", "last_seen": "2026-08-03"},
                    {"name": "新城市书店", "last_seen": "2026-08-04"},
                ],
                "events": [
                    {"summary": "在旧城市公园散步", "date": "2026-08-03"},
                    {"summary": "整理新居书架", "date": "2026-08-04"},
                ],
            },
            DayRecord(date="2026-08-04"),
            "附近有什么地方",
        )

        self.assertEqual(len(calls), 1)
        groups = calls[0][1]
        self.assertNotIn("旧城市公园", str(groups))
        self.assertIn("新城市书店", str(groups))
        self.assertNotIn("在旧城市公园散步", str(groups))
        self.assertIn("整理新居书架", str(groups))

    async def test_manual_weather_refresh_bypasses_hour_cache_and_debounces(self):
        class WeatherClient:
            def __init__(self):
                self.calls = []

            async def get_weather(self, city):
                self.calls.append(city)
                return {
                    "code": 200,
                    "data": {
                        "location": {"city": city},
                        "weather": {"condition": "晴", "temperature": 29},
                        "life_indices": [],
                        "air_quality": {"aqi": 22, "quality": "优"},
                    },
                }

        class Domains:
            async def resolve_weather_city(self):
                return "测试市"

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"weather_awareness": {"api_key": "weather-key"}}
        )
        runtime.archive = DataManager()
        runtime.weather_client = WeatherClient()
        runtime.domains = Domains()
        changed_reasons = []
        runtime.mark_page_status_changed = lambda reason="": (
            changed_reasons.append(reason) or async_return(1)
        )
        await runtime.archive.save_day(
            DayRecord(
                date="2026-08-03",
                weather="测试市 多云 27°C",
                weather_info=WeatherInfo(condition="多云", temp=27),
                weather_last_update=1000,
            )
        )

        with patch("core.runtime.spine.sky.life_timestamp", return_value=1100):
            auto_updated = await runtime.try_update_weather("2026-08-03")
            manual_updated = await runtime.try_update_weather(
                "2026-08-03",
                force=True,
            )
        with patch("core.runtime.spine.sky.life_timestamp", return_value=1110):
            repeated_manual_update = await runtime.try_update_weather(
                "2026-08-03",
                force=True,
            )

        self.assertFalse(auto_updated)
        self.assertTrue(manual_updated)
        self.assertFalse(repeated_manual_update)
        self.assertEqual(runtime.weather_client.calls, ["测试市"])
        self.assertEqual(changed_reasons, ["weather"])
        day = await runtime.archive.get_day("2026-08-03")
        self.assertEqual(day.weather, "测试市 晴 29°C (AQI: 22 优)")
        self.assertEqual(day.weather_last_update, 1100)

    async def test_resolve_injection_target_uses_today_log_when_extended_night_has_no_yesterday(
        self,
    ):
        from core.runtime.mirror import tempo

        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"schedule_time": "07:00"})
        runtime.archive = DataManager()
        now = datetime.datetime(2026, 7, 5, 1, 44)

        old_debug = tempo.logger.debug
        tempo.logger.debug = lambda message, *args, **kwargs: messages.append(
            str(message)
        )
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
        runtime.config = LifeSettings.from_dict(
            {"rhythm_config": {"schedule_time": "07:30"}}
        )
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
        decisions = await archive.get_life_decisions(
            limit=5, kind="daily_plan", date="2026-07-05"
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, "定时刷新后的生活安排")

    async def test_injection_reuses_short_lived_snapshot_without_hiding_current_message(
        self,
    ):
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
                timeline=[
                    TimelineItem(time="12:00", activity="在家整理资料", status="平静")
                ],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler(
            normal_limit=4, chat_limit=1
        )
        runtime._injection_snapshot_cache = {}
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = type("Composer", (), {})()
        runtime.resolve_injection_target = lambda now: async_return(
            ("2026-05-24", False)
        )
        runtime.maybe_collect_emoji_assets_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime.maybe_capture_commitment_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime.maybe_capture_chat_memory_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {
                "resolve_event_sender": staticmethod(
                    lambda event: async_return(event.get_sender_name())
                )
            },
        )()

        event1 = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event1.message_str = "周末去看展吧"
        event2 = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event2.message_str = "下午场也可以"
        req1 = type(
            "Request",
            (),
            {"prompt": "你好", "system_prompt": "", "session_id": "chat_session_1"},
        )()
        req2 = type(
            "Request",
            (),
            {"prompt": "你好", "system_prompt": "", "session_id": "chat_session_2"},
        )()

        await runtime.inject_life_context(req1, event1)
        await runtime.inject_life_context(req2, event2)

        self.assertEqual(runtime.archive.relationship_calls, 1)
        self.assertIn("喜欢看展", req2.system_prompt)
        self.assertIn("<daily_life>", req2.system_prompt)
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

    async def test_injection_missing_day_generates_in_background_with_anti_fabrication_context(
        self,
    ):
        generation_started = asyncio.Event()
        allow_generation = asyncio.Event()

        class Composer:
            def __init__(self):
                self.calls = 0

            async def generate_daily(
                self,
                date=None,
                force=False,
                target_hour=None,
                extra=None,
                web_inspiration="",
            ):
                self.calls += 1
                generation_started.set()
                await allow_generation.wait()
                day = DayRecord(
                    date=date.strftime("%Y-%m-%d"),
                    outfit="浅蓝外套",
                    timeline=[
                        TimelineItem(
                            time="12:00", activity="在家整理资料", status="平静"
                        )
                    ],
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
        runtime.resolve_injection_target = lambda now: async_return(
            (now.strftime("%Y-%m-%d"), False)
        )
        req = type(
            "Request",
            (),
            {"prompt": "你好", "system_prompt": "", "session_id": "chat_session"},
        )()

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

    async def test_startup_missing_day_bootstraps_current_life_day(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"schedule_time": "07:30"})
        runtime.archive = DataManager()
        runtime.failed_dates = {}
        calls = []

        async def generate_daily(**kwargs):
            calls.append(kwargs)
            day = DayRecord(
                date=kwargs["date"].strftime("%Y-%m-%d"),
                timeline=[TimelineItem(time="09:00", activity="慢慢醒来")],
            )
            await runtime.archive.save_day(day)
            return types.SimpleNamespace(day=day)

        runtime.run_daily_generation = generate_daily
        runtime.resolve_injection_target = lambda now: async_return(
            (now.strftime("%Y-%m-%d"), False)
        )
        now = datetime.datetime(2026, 7, 31, 14, 20)

        await runtime.ensure_startup_day_data(now)
        await runtime.ensure_startup_day_data(now)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["source"], "startup_seed")
        stored = await runtime.archive.get_day("2026-07-31")
        self.assertEqual(stored.date, "2026-07-31")

    async def test_startup_consumes_offline_residence_change_and_rebuilds_day(self):
        class Domains:
            async def resolve_home_location(self):
                return {"city": "新城市"}

            def consume_detected_residence_change(self):
                return "2026-08-04 09:00:00"

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"schedule_time": "07:30"})
        runtime.archive = DataManager()
        runtime.domains = Domains()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-08-04",
                weather="旧城市 晴 30°C",
                timeline=[
                    TimelineItem(time="10:00", activity="在旧城市公园散步")
                ],
            )
        )
        runtime.resolve_injection_target = lambda now: async_return(
            ("2026-08-04", False)
        )
        calls = []

        async def prepare(target):
            calls.append(("prepare", target))

        async def refresh(target):
            calls.append(("refresh", target))

        runtime._prepare_residence_change = prepare
        runtime._refresh_after_residence_change = refresh

        await runtime.ensure_startup_day_data(
            datetime.datetime(2026, 8, 4, 10, 0)
        )

        self.assertEqual([item[0] for item in calls], ["prepare", "refresh"])
        self.assertEqual(calls[0][1], datetime.datetime(2026, 8, 4, 10, 0))

    async def test_startup_generation_waits_for_onebot_connection(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"schedule_time": "07:30"})
        runtime.archive = DataManager()
        runtime.failed_dates = {}
        calls = []
        client = types.SimpleNamespace(
            _wsr_event_clients=set(),
            _wsr_api_clients={},
        )
        instance = types.SimpleNamespace(
            config={"type": "aiocqhttp"},
            bot=client,
            status=types.SimpleNamespace(value="running"),
        )
        instances = []
        runtime.context = types.SimpleNamespace(
            platform_manager=types.SimpleNamespace(
                get_insts=lambda: instances,
                platforms_config=[{"type": "aiocqhttp", "enable": True}],
            )
        )

        async def generate_daily(**kwargs):
            calls.append(kwargs)
            day = DayRecord(
                date=kwargs["date"].strftime("%Y-%m-%d"),
                timeline=[TimelineItem(time="09:00", activity="慢慢醒来")],
            )
            await runtime.archive.save_day(day)
            return types.SimpleNamespace(day=day)

        runtime.run_daily_generation = generate_daily
        runtime.resolve_injection_target = lambda now: async_return(
            (now.strftime("%Y-%m-%d"), False)
        )
        now = datetime.datetime(2026, 7, 31, 14, 20)

        with patch(
            "core.runtime.spine.boot._PLATFORM_READY_POLL_SECONDS", 0.01
        ):
            task = asyncio.create_task(runtime.ensure_startup_day_data(now))
            await asyncio.sleep(0.03)
            self.assertEqual(calls, [])

            instances.append(instance)
            await asyncio.sleep(0.03)
            self.assertEqual(calls, [])

            client._wsr_event_clients.add(object())
            await asyncio.wait_for(task, timeout=1)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["source"], "startup_seed")

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
                "state_config": {
                    "enabled": True,
                    "refresh_minutes": 30,
                    "quiet_hours": "",
                },
            }
        )
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.try_update_weather = lambda today_str: async_return(None)
        runtime.resolve_injection_target = lambda now: async_return(
            (datetime.datetime.now().strftime("%Y-%m-%d"), False)
        )
        runtime._get_curr_period = lambda now=None: "afternoon"

        async def refresh_state_for_day(
            date_str, data, now, source="", detail="", force=False, notify_page=True
        ):
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
                "state_config": {
                    "enabled": True,
                    "refresh_minutes": 30,
                    "quiet_hours": "00:00-06:30",
                },
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
                "state_config": {
                    "enabled": True,
                    "refresh_minutes": 30,
                    "quiet_hours": "00:00-06:30",
                },
            }
        )
        runtime.archive = archive
        runtime.composer = composer
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.resolve_injection_target = lambda now: async_return((today, False))
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: (
            async_return({})
        )
        scheduled_tasks = []

        def schedule_background_task(coro, *args, **kwargs):
            if kwargs.get("key") == f"chat_state:{today}":
                scheduled_tasks.append((coro, kwargs))
            else:
                coro.close()
            return True

        runtime._schedule_background_task = schedule_background_task
        page_reasons = []
        runtime.mark_page_status_changed = lambda reason="": (
            page_reasons.append(reason) or async_return(len(page_reasons))
        )
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
        event = Event()
        with patch("core.runtime.inject.life_now", return_value=quiet_now):
            await runtime.inject_life_context(
                ProviderRequest(session_id="user_session"), event
            )

        self.assertEqual(len(composer.calls), 0)
        self.assertEqual(scheduled_tasks, [])
        self.assertTrue(runtime.schedule_pending_chat_state_refresh(event))
        self.assertFalse(runtime.schedule_pending_chat_state_refresh(event))
        chat_state_tasks = [
            coro
            for coro, kwargs in scheduled_tasks
            if kwargs.get("key") == f"chat_state:{today}"
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
                state=LifeState(
                    energy=60, summary="原状态", updated_at="2026-06-27 13:00"
                ),
            )
        )
        saved_days = []
        original_save_day = archive.save_day

        async def save_day(day):
            saved_days.append(
                (day.outfit, day.state.summary if day.state else "", dict(day.meta))
            )
            await original_save_day(day)

        archive.save_day = save_day

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, **kwargs):
                provider_started.set()
                await allow_provider.wait()
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            @staticmethod
            def _compute_sleep_continuity(previous, day):
                return (0.0, 0.0, float(day.state.energy or 0))

            async def learn_preferences_from_payload(self, *args, **kwargs):
                raise AssertionError("撤回后不应保存偏好")

            async def persist_life_events_from_payload(self, *args, **kwargs):
                raise AssertionError("撤回后不应保存生活事件")

            async def update_outfit(
                self, date_str, period, current_time=None, should_abort=None
            ):
                raise AssertionError("撤回后不应继续判断穿搭")

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "state_config": {
                    "enabled": True,
                    "refresh_minutes": 5,
                    "quiet_hours": "",
                },
            }
        )
        runtime.archive = archive
        runtime.composer = Composer()
        runtime.generation_lock = asyncio.Lock()
        runtime._page_status_version = 0
        runtime._page_status_changed = asyncio.Condition()
        runtime.mark_page_status_changed = lambda *args, **kwargs: async_return(0)
        runtime._get_curr_period = lambda now=None: "afternoon"
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="m-hot"
        )
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
                timeline=[
                    TimelineItem(time="12:00", activity="在家看综艺", status="放松")
                ],
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
                        TimelineItem(
                            time="12:00", activity="在家看综艺", status="放松"
                        ),
                        TimelineItem(
                            time="15:00", activity="和阿林去书店闲逛", status="期待"
                        ),
                    ],
                    {"decision": "accept", "accept": True},
                )

            async def learn_preferences_from_payload(self, *args, **kwargs):
                return None

            async def persist_life_events_from_payload(self, *args, **kwargs):
                return None

            async def update_outfit(self, date_str, period, current_time=None):
                day = await self.archive.get_day(date_str)
                self.outfit_calls.append(
                    (date_str, period, current_time, day.timeline[-1].activity)
                )
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
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return("阿林")
        )
        runtime.remember_interaction = lambda *args, **kwargs: async_return(None)
        runtime.refresh_state_for_day = lambda date_str, data, now, **kwargs: (
            async_return(data)
        )
        runtime._get_curr_period = lambda now=None: "afternoon"
        runtime.mark_page_status_changed = lambda reason="": (
            page_reasons.append(reason) or async_return(1)
        )
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )

        event = Event()
        event.message_str = "下午一起出门闲逛"
        reply = await runtime.accept_user_invite(event, "下午一起出门闲逛")

        reply_payload = json.loads(reply)
        self.assertEqual(reply_payload["decision"], "accept")
        self.assertEqual(reply_payload["person"], "阿林")
        self.assertEqual(reply_payload["activity"], "下午一起出门闲逛")
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

    async def test_same_day_commitment_updates_schedule_and_defers_outfit(self):
        today = "2026-08-06"
        now = datetime.datetime(2026, 8, 6, 14, 0)
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date=today,
                outfit="下午居家穿搭",
                timeline=[
                    TimelineItem(time="13:00", activity="在家休息", status="放松"),
                    TimelineItem(time="17:30", activity="独自散步", status="平静"),
                ],
            )
        )
        commitment = await archive.save_commitment(
            CommitmentRecord(
                content="傍晚一起去老街，换上适合同行的外出穿搭",
                trigger_date=today,
                people=["测试对象"],
            )
        )

        class Composer:
            async def reconcile_commitment_with_timeline(self, *args, **kwargs):
                return (
                    [
                        TimelineItem(time="13:00", activity="在家休息", status="放松"),
                        TimelineItem(
                            time="17:10", activity="换好衣服准备出门", status="准备"
                        ),
                        TimelineItem(
                            time="17:40",
                            activity="和测试对象一起去老街散步",
                            status="期待",
                        ),
                    ],
                    {
                        "should_apply": True,
                        "reason": "双方已经确认同行",
                        "outfit_instruction": "适合傍晚同行的外出穿搭",
                        "outfit_effective_time": "17:10",
                    },
                )

        scheduled = []
        page_reasons = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = archive
        runtime.composer = Composer()
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((coro, label, key)) or True
        )
        runtime.mark_page_status_changed = lambda reason="": (
            page_reasons.append(reason) or async_return(1)
        )

        applied = await runtime.apply_commitment_to_current_day(
            commitment, now=now, owner_hint="共同"
        )

        self.assertTrue(applied)
        stored = await archive.get_day(today)
        self.assertEqual(stored.timeline[-1].activity, "和测试对象一起去老街散步")
        pending = json.loads(stored.meta["pending_commitment_outfit"])
        self.assertEqual(pending["effective_time"], "17:10")
        self.assertEqual(
            (await archive.get_commitment(commitment.id)).status, "scheduled"
        )
        self.assertEqual(scheduled, [])
        self.assertIn("commitment_schedule_update", page_reasons)

    async def test_confirmation_reuses_pending_invite_alternative(self):
        now = datetime.datetime(2026, 8, 6, 14, 0)
        today = now.strftime("%Y-%m-%d")
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date=today,
                timeline=[
                    TimelineItem(time="13:00", activity="在家休息", status="放松"),
                    TimelineItem(time="18:00", activity="独自散步", status="平静"),
                ],
            )
        )

        class Composer:
            def __init__(self):
                self.invite_texts = []

            async def handle_invite(self, *args, **kwargs):
                self.invite_texts.append(args[2])
                if len(self.invite_texts) == 1:
                    return (
                        "下午想先休息，傍晚更合适",
                        None,
                        {
                            "decision": "propose_alternative",
                            "accept": False,
                            "alternative_time": "傍晚五点半一起出门",
                        },
                    )
                return (
                    "改约方案已经得到确认",
                    [
                        TimelineItem(time="13:00", activity="在家休息", status="放松"),
                        TimelineItem(
                            time="17:30", activity="和测试对象一起出门", status="期待"
                        ),
                    ],
                    {"decision": "accept", "accept": True},
                )

            async def learn_preferences_from_payload(self, *args, **kwargs):
                return None

            async def persist_life_events_from_payload(self, *args, **kwargs):
                return None

        scheduled = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"state_config": {"enabled": False}})
        runtime.memos = runtime._create_memos_service()
        runtime.archive = archive
        runtime.composer = Composer()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return("测试对象")
        )
        runtime.remember_interaction = lambda *args, **kwargs: async_return(None)
        runtime.mark_page_status_changed = lambda reason="": async_return(1)
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append(coro) or True
        )

        first = Event()
        first.message_str = "现在一起出去吗"
        second = Event()
        second.message_str = "好，安排上"
        with patch("core.runtime.spine.rsvp.life_now", return_value=now):
            await runtime.accept_user_invite(first, "现在一起出去")
            stored = await archive.get_day(today)
            self.assertIn("pending_invite_alternative", stored.meta)
            reply = await runtime.accept_user_invite(second, "确认刚才的改约方案")

        self.assertIn("傍晚五点半一起出门", runtime.composer.invite_texts[-1])
        self.assertEqual(json.loads(reply)["decision"], "accept")
        stored = await archive.get_day(today)
        self.assertNotIn("pending_invite_alternative", stored.meta)
        self.assertEqual(stored.timeline[-1].activity, "和测试对象一起出门")
        for coro in scheduled:
            coro.close()

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
        runtime.raw_config = Config(
            {
                "rhythm_config": {"schedule_time": "07:00"},
                "life_domain_config": {
                    "home_address": "测试省旧城市测试区旧路1号"
                },
            }
        )
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
        prepared = []
        scheduled = []

        async def prepare_residence_change(target):
            prepared.append(target)

        def schedule_background(coroutine, *, label="", key=""):
            coroutine.close()
            scheduled.append((label, key))
            return True

        runtime._prepare_residence_change = prepare_residence_change
        runtime._schedule_background_task = schedule_background

        await runtime.apply_config(
            {
                "rhythm_config": {
                    "schedule_time": "08:25",
                },
                "life_domain_config": {
                    "home_address": "测试省测试市测试区测试路1号"
                },
                "state_config": {"enabled": False, "refresh_minutes": 45},
            }
        )

        self.assertEqual(runtime.raw_config.saved, 1)
        self.assertEqual(runtime.raw_config["rhythm_config"]["schedule_time"], "08:25")
        self.assertEqual(runtime.config.schedule_time, "08:25")
        self.assertEqual(
            runtime.config.domains.home_address,
            "测试省测试市测试区测试路1号",
        )
        self.assertFalse(hasattr(runtime.config.weather, "default_city"))
        self.assertFalse(runtime.config.state.enabled)
        self.assertTrue(runtime.weather_client is not None)
        self.assertTrue(runtime.rhythm.scheduler.running)
        self.assertEqual(len(prepared), 1)
        self.assertEqual(scheduled, [("居住地变化刷新", "residence_change_refresh")])

    async def test_prepare_residence_change_invalidates_current_location_context(self):
        from core.life.tools import get_week_id
        from core.models import PlaceRecord, WeekPlanRecord

        class Domains:
            def __init__(self):
                self.boundary = ""

            def set_residence_boundary(self, changed_at):
                self.boundary = changed_at

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.domains = Domains()
        runtime._injection_snapshot_cache = {"old": object()}
        changed_reasons = []
        runtime.mark_page_status_changed = lambda reason="": (
            changed_reasons.append(reason) or async_return(1)
        )
        target = datetime.datetime(2026, 8, 4, 18, 0)
        await runtime.archive.save_day(
            DayRecord(
                date="2026-08-04",
                weather="旧城市 晴 30°C",
                weather_info=WeatherInfo(condition="晴", temp=30),
                weather_last_update=123,
                places=[PlaceRecord(name="旧城市公园")],
                timeline=[
                    TimelineItem(time="18:00", activity="去旧城市公园散步")
                ],
            )
        )
        await runtime.archive.save_week_plan(
            WeekPlanRecord(week_id=get_week_id(target), theme="旧城市生活")
        )
        await runtime.archive.touch_places(
            "2026-08-03", [PlaceRecord(name="旧城市公园")]
        )

        with patch(
            "core.runtime.spine.adapt.life_now",
            return_value=datetime.datetime(2026, 8, 4, 18, 1),
        ):
            await runtime._prepare_residence_change(target)

        day = await runtime.archive.get_day("2026-08-04")
        self.assertEqual(day.weather, "")
        self.assertIsNone(day.weather_info.temp)
        self.assertEqual(day.weather_last_update, 0)
        self.assertEqual(day.places, [])
        self.assertEqual(day.meta["residence_context_stale"], "true")
        self.assertEqual(day.timeline[0].activity, "去旧城市公园散步")
        self.assertEqual(runtime.archive.places, {})
        self.assertEqual(runtime.archive.week_plans, {})
        self.assertEqual(
            runtime.archive.residence_context_changed_at,
            "2026-08-04 18:01:00",
        )
        self.assertEqual(runtime.domains.boundary, "2026-08-04 18:01:00")
        self.assertEqual(runtime._injection_snapshot_cache, {})
        self.assertEqual(changed_reasons, ["residence_changed"])

    async def test_runtime_service_swap_waits_for_active_lease(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def use_service():
            async with runtime.runtime_service_lease():
                entered.set()
                await release.wait()

        user = asyncio.create_task(use_service())
        await entered.wait()
        swap = asyncio.create_task(runtime._begin_runtime_service_swap())
        await asyncio.sleep(0)

        self.assertFalse(swap.done())

        release.set()
        await user
        await swap
        self.assertTrue(runtime._service_swap_pending)
        await runtime._end_runtime_service_swap()
        self.assertFalse(runtime._service_swap_pending)

    async def test_apply_config_restores_old_runtime_when_commit_fails(self):
        class Config(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.saved = 0

            def save_config(self):
                self.saved += 1

        class Rhythm:
            def __init__(self, running):
                self.scheduler = types.SimpleNamespace(running=running)
                self.start_calls = 0
                self.stop_calls = 0

            def start(self):
                self.start_calls += 1
                self.scheduler.running = True

            def stop(self):
                self.stop_calls += 1
                self.scheduler.running = False

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.raw_config = Config({"rhythm_config": {"schedule_time": "07:00"}})
        runtime.generation_lock = asyncio.Lock()
        runtime.config = LifeSettings.from_dict(runtime.raw_config)
        runtime.rhythm = Rhythm(True)
        runtime.media = types.SimpleNamespace(name="old-media")
        runtime.memos = types.SimpleNamespace(name="old-memos")
        runtime.contact_resolver = types.SimpleNamespace(name="old-contact")
        runtime.weather_client = types.SimpleNamespace(name="old-weather")
        runtime.search = types.SimpleNamespace(name="old-search")
        runtime.composer = types.SimpleNamespace(name="old-composer")
        runtime.model_gateway = types.SimpleNamespace(name="old-gateway")
        candidate = types.SimpleNamespace(
            config=LifeSettings.from_dict(
                {"rhythm_config": {"schedule_time": "09:30"}}
            ),
            rhythm=Rhythm(False),
            media=types.SimpleNamespace(name="new-media"),
            memos=types.SimpleNamespace(name="new-memos"),
            contact_resolver=types.SimpleNamespace(name="new-contact"),
            weather_client=types.SimpleNamespace(name="new-weather"),
            search=types.SimpleNamespace(name="new-search"),
            composer=types.SimpleNamespace(name="new-composer"),
            model_gateway=types.SimpleNamespace(name="new-gateway"),
        )
        closed = []

        runtime._build_runtime_services = lambda *_args: candidate
        runtime._prune_disabled_proactive_candidates = lambda: (_ for _ in ()).throw(
            RuntimeError("提交失败")
        )

        async def close_services(services):
            closed.append(services)

        runtime._close_runtime_services = close_services

        with self.assertRaisesRegex(RuntimeError, "提交失败"):
            await runtime.apply_config({"rhythm_config": {"schedule_time": "09:30"}})

        self.assertEqual(runtime.config.schedule_time, "07:00")
        self.assertEqual(runtime.raw_config["rhythm_config"]["schedule_time"], "07:00")
        self.assertTrue(runtime.rhythm.scheduler.running)
        self.assertEqual(runtime.raw_config.saved, 2)
        self.assertEqual(closed, [candidate])

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

        async def call_llm_text(
            provider, prompt, session_id, empty_retries=0, primary_provider_id=""
        ):
            resp = await provider.text_chat(
                prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
            )
            return (
                resp.get("content", "")
                if isinstance(resp, dict)
                else getattr(resp, "completion_text", "")
            )

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
                "_compute_sleep_continuity": staticmethod(
                    lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))
                ),
                "learn_preferences_from_payload": staticmethod(learn_preferences),
                "persist_life_events_from_payload": staticmethod(persist_events),
            },
        )()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                weather="测试市 晴 20°C",
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
        self.assertLess(
            selected_provider.prompts[0].index("【通用自主原则】"),
            selected_provider.prompts[0].index("【眼前内容】"),
        )
        self.assertGreater(
            selected_provider.prompts[0].index("当前状态"),
            selected_provider.prompts[0].index("【眼前内容】"),
        )
        self.assertLess(
            selected_provider.prompts[0].index("生活日程日期：2026-05-24"),
            selected_provider.prompts[0].index("当前时间：2026-05-24 12:00"),
        )
        self.assertLess(
            selected_provider.prompts[0].index("当前状态"),
            selected_provider.prompts[0].index("触发来源：chat"),
        )
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

        async def call_llm_text(
            provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""
        ):
            resp = await provider_arg.text_chat(
                prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
            )
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
        runtime.config = LifeSettings.from_dict(
            {"state_config": {"enabled": True, "refresh_minutes": 5}}
        )
        runtime.archive = DataManager()
        runtime.mark_page_status_changed = lambda _kind: async_return(None)
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_call_llm_text": staticmethod(call_llm_text),
                "_cleanup_conversation": staticmethod(cleanup),
                "_compute_sleep_continuity": staticmethod(
                    lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))
                ),
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

        async def call_llm_text(
            provider, prompt, session_id, empty_retries=0, primary_provider_id=""
        ):
            resp = await provider.text_chat(
                prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
            )
            return (
                resp.get("content", "")
                if isinstance(resp, dict)
                else getattr(resp, "completion_text", "")
            )

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
        runtime.context = Context(
            default_provider, providers={"generation-model": generation_provider}
        )
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
                "_compute_sleep_continuity": staticmethod(
                    lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))
                ),
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
                "_compute_sleep_continuity": staticmethod(
                    lambda previous, day: (0.0, 0.0, float(day.state.energy or 0))
                ),
                "learn_preferences_from_payload": staticmethod(
                    lambda *args, **kwargs: []
                ),
                "persist_life_events_from_payload": staticmethod(
                    lambda *args, **kwargs: []
                ),
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
