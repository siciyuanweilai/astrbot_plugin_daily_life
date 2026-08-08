import asyncio
import datetime
import types
import unittest

from core.models import (
    DayRecord,
    EventRecord,
    LifeState,
    PlaceRecord,
    RelationshipNote,
    RelationshipRecord,
    TimelineItem,
)
from support import (
    DailyLifeCommandCenter,
    DataManager,
    Event,
    LifeSettings,
    async_return,
)


class LifeCommandsTest(unittest.IsolatedAsyncioTestCase):
    async def test_life_query_displays_config_vision_provider(self):
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict(
                {
                    "rhythm_config": {"llm_provider": "generation-model"},
                    "memory_config": {"provider": "memory-model"},
                    "vision_config": {"provider": "vision-model"},
                }
            ),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        result = await center.query_life(event, target="config")

        self.assertIn("视觉 vision-model", result)

    async def test_life_tools_unclear_actions_return_user_facing_help(self):
        center = DailyLifeCommandCenter(types.SimpleNamespace())
        event = Event()

        query = await center.query_life(event, target="unknown")
        self.assertIn("未能确认要查看的生活信息类型", query)
        self.assertNotIn("status/today", query)
        self.assertNotIn("可以", query)

        adjust = await center.adjust_life(event, action="unknown")
        self.assertIn("未能确认要执行的生活调整动作", adjust)
        self.assertNotIn("refresh_state", adjust)
        self.assertNotIn("可以", adjust)

        commitment = await center.manage_commitment(event, action="unknown")
        self.assertIn("未能确认要执行的承诺处理动作", commitment)
        self.assertNotIn("memo_tomorrow", commitment)
        self.assertNotIn("可以", commitment)

    async def test_life_commitment_tool_manages_future_promises(self):
        archive = DataManager()
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=archive,
            contact_resolver=types.SimpleNamespace(
                resolve_event_sender=lambda event: async_return("阿林")
            ),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()

        added = await center.manage_commitment(
            event, action="add", content="周末一起看电影"
        )
        self.assertIn("已记录承诺 #1", added)

        listed = await center.manage_commitment(event, action="list")
        self.assertIn("周末一起看电影", listed)

        delayed = await center.manage_commitment(
            event, action="reschedule", commitment_id=1, target_date="2026-06-01"
        )
        self.assertIn("已延期", delayed)
        self.assertEqual((await archive.get_commitment(1)).trigger_date, "2026-06-01")

        done = await center.manage_commitment(event, action="done", commitment_id=1)
        self.assertIn("已更新", done)
        self.assertEqual((await archive.get_commitment(1)).status, "done")

    async def test_command_invite_saves_authoritative_commitment_before_day(self):
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                timeline=[TimelineItem(time="15:00", activity="整理测试资料")],
            )
        )

        class Composer:
            async def handle_invite(self, *args, **kwargs):
                return (
                    "可以，一起去走走。",
                    [
                        TimelineItem(time="15:00", activity="整理测试资料"),
                        TimelineItem(time="18:00", activity="去测试公园散步"),
                    ],
                    {"decision": "accept", "accept": True},
                )

            async def learn_preferences_from_payload(self, *args, **kwargs):
                return None

            async def persist_life_events_from_payload(self, *args, **kwargs):
                return None

        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({"state_config": {"enabled": False}}),
            archive=archive,
            composer=Composer(),
            contact_resolver=types.SimpleNamespace(
                resolve_event_sender=lambda event: async_return("测试对象")
            ),
            remember_interaction=lambda *args, **kwargs: async_return(None),
            schedule_invite_outfit_sync=lambda *args, **kwargs: None,
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event(message_id="command-invite-1")
        event.message_str = "/生活 邀约 傍晚一起去测试公园"
        req = await center._make_request(
            ["/生活", "邀约", "傍晚一起去测试公园"],
            target_date="2026-05-24",
        )

        results = [item async for item in center._invite(event, req)]

        self.assertIn("稍等", results[0])
        commitments = await archive.get_commitments(status="scheduled", limit=10)
        self.assertEqual(len(commitments), 1)
        self.assertEqual(commitments[0].source, "invite")
        self.assertEqual(commitments[0].source_message_id, "command-invite-1")
        self.assertEqual(commitments[0].content, "傍晚一起去测试公园")
        day = await archive.get_day("2026-05-24")
        self.assertEqual(day.timeline[-1].activity, "去测试公园散步")

    async def test_template_commands_are_removed(self):
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=DataManager(),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        event.message_str = "/生活 模板"

        result = await center.dispatch(event).__anext__()

        self.assertIn("未知指令", result)

    async def test_life_adjust_update_outfit_keeps_schedule(self):
        class CommandArchive:
            async def get_day(self, date_str):
                return DayRecord(
                    date=date_str,
                    timeline=[TimelineItem(time="10:00", activity="写手帐")],
                )

        class CommandComposer:
            def __init__(self):
                self.calls = []

            async def update_outfit(
                self,
                date_str,
                period,
                current_time=None,
                instruction="",
            ):
                self.calls.append(
                    ("update_outfit", date_str, period, current_time, instruction)
                )
                return DayRecord(date=date_str, outfit="新穿搭")

        async def fail_daily_generation(**kwargs):
            raise AssertionError("已有日程时不应调用完整日程生成")

        status_reasons = []

        async def mark_page_status_changed(reason):
            status_reasons.append(reason)

        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            generation_lock=asyncio.Lock(),
            archive=CommandArchive(),
            composer=CommandComposer(),
            failed_dates={},
            _get_curr_period=lambda: "afternoon",
            _target_datetime_for_command=lambda date_str, now: datetime.datetime(
                2026, 5, 24, now.hour, now.minute
            ),
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
            run_daily_generation=fail_daily_generation,
            mark_page_status_changed=mark_page_status_changed,
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        result = await center.adjust_life(
            event,
            action="update_outfit",
            detail="换成浅粉色荷叶边睡裙",
            period="傍晚",
        )

        self.assertNotIn("重新生成", result)
        self.assertNotIn("时间轴", result)
        self.assertIn("穿搭已更新：新穿搭", result)
        self.assertIn("日程保持不变", result)
        self.assertEqual(
            [item[:3] for item in runtime.composer.calls],
            [("update_outfit", "2026-05-24", "evening")],
        )
        self.assertIsInstance(runtime.composer.calls[0][3], datetime.datetime)
        self.assertEqual(runtime.composer.calls[0][4], "换成浅粉色荷叶边睡裙")
        self.assertEqual(status_reasons, ["outfit_update"])

    async def test_life_adjust_update_outfit_generates_only_when_day_is_missing(self):
        class EmptyArchive:
            async def get_day(self, date_str):
                return None

        generation_calls = []

        async def run_daily_generation(**kwargs):
            generation_calls.append(kwargs)
            return types.SimpleNamespace(
                day=DayRecord(date="2026-05-24", outfit="浅粉色睡裙")
            )

        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            generation_lock=asyncio.Lock(),
            archive=EmptyArchive(),
            composer=types.SimpleNamespace(),
            failed_dates={},
            _get_curr_period=lambda: "afternoon",
            _target_datetime_for_command=lambda date_str, now: datetime.datetime(
                2026, 5, 24, now.hour, now.minute
            ),
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
            run_daily_generation=run_daily_generation,
        )
        center = DailyLifeCommandCenter(runtime)

        result = await center.adjust_life(
            Event(),
            action="update_outfit",
            detail="换成浅粉色睡裙",
            period="傍晚",
        )

        self.assertIn("当天尚无生活安排", result)
        self.assertEqual(len(generation_calls), 1)
        call = generation_calls[0]
        self.assertEqual(call["source"], "outfit_seed")
        self.assertEqual(call["extra"], "换成浅粉色睡裙")
        self.assertEqual(call["target_hour"], 17)
        self.assertFalse(call["delete_existing"])

    async def test_sleep_and_stay_awake_commands_are_removed(self):
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=DataManager(),
            _get_curr_period=lambda: "night",
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()

        event.message_str = "/生活 睡觉"
        sleep_result = await center.dispatch(event).__anext__()
        event.message_str = "/生活 熬夜"
        stay_awake_result = await center.dispatch(event).__anext__()

        self.assertIn("未知指令", sleep_result)
        self.assertIn("未知指令", stay_awake_result)

    async def test_life_query_world_displays_relationship_places_and_events(self):
        class CommandArchive:
            async def get_recent_relationships(self, limit=8):
                return [
                    RelationshipRecord(
                        id="u1",
                        name="阿林",
                        interactions=3,
                        notes=[
                            RelationshipNote(date="2026-05-24", content="约过周末看展")
                        ],
                    )
                ]

            async def get_recent_places(self, limit=10):
                return [
                    PlaceRecord(name="常去咖啡店", type="cafe", visits=2, hint="写手帐")
                ]

            async def get_recent_events(self, limit=10):
                return [
                    EventRecord(
                        date="2026-05-24",
                        summary="在常去咖啡店完成手帐",
                        place="常去咖啡店",
                    )
                ]

            async def get_recent_chat_summaries(self, limit=8):
                return []

            async def get_recent_group_environments(self, limit=5):
                return []

            async def get_recent_action_decisions(self, limit=5):
                return []

            async def get_recent_message_visibility(self, limit=5):
                return []

        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=CommandArchive(),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        result = await center.query_life(event, target="world")

        self.assertIn("日常生活世界", result)
        self.assertIn("阿林", result)
        self.assertIn("常去咖啡店", result)
        self.assertIn("完成手帐", result)

    async def test_display_world_alias_is_removed(self):
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=DataManager(),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        event.message_str = "/生活 显示 世界"

        result = await center.dispatch(event).__anext__()

        self.assertIn("未知指令", result)

    async def test_storage_command_lists_and_clears_category(self):
        archive = DataManager()
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                timeline=[TimelineItem(time="10:00", activity="整理手帐")],
            )
        )
        await archive.touch_relationship(
            "u1", name="阿林", note="聊到周末看展", date_str="2026-05-24"
        )
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=archive,
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()

        event.message_str = "/生活 存储"
        listed = await center.dispatch(event).__anext__()
        self.assertIn("日常记录", listed)
        self.assertIn("关系档案", listed)

        event.message_str = "/生活 存储 清空 日常记录"
        cleared = await center.dispatch(event).__anext__()
        self.assertIn("已清空", cleared)
        self.assertIsNone(await archive.get_day("2026-05-24"))

    async def test_life_query_status_displays_current_state(self):
        class CommandArchive:
            async def get_day(self, date_str):
                return DayRecord(
                    date=date_str,
                    timeline=[
                        TimelineItem(
                            time="10:00", activity="在家整理手帐", status="慢慢来"
                        )
                    ],
                    state=LifeState.from_value(
                        {
                            "energy": 32,
                            "mood": "有点累",
                            "busyness": 68,
                            "social": 24,
                            "sleep": {"quality": 40, "summary": "昨晚睡得浅"},
                            "summary": "今天不太想出门",
                            "updated_at": "2026-05-24 10:00",
                            "source": "daily",
                        }
                    ),
                )

        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=CommandArchive(),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(
                ("2026-05-24", False)
            ),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        result = await center.query_life(event, target="status")

        self.assertIn("在家整理手帐", result)
        self.assertIn("体力：32/100", result)
        self.assertIn("今天不太想出门", result)

    async def test_life_adjust_refresh_state_forces_runtime_update(self):
        class CommandArchive:
            async def get_day(self, date_str):
                return DayRecord(
                    date=date_str,
                    timeline=[
                        TimelineItem(
                            time="10:00", activity="在家整理手帐", status="慢慢来"
                        )
                    ],
                    state=LifeState(energy=60, updated_at="2026-05-24 09:00"),
                )

        class CommandRuntime:
            def __init__(self):
                self.config = LifeSettings.from_dict({})
                self.archive = CommandArchive()
                self.calls = []

            def _get_curr_period(self):
                return "afternoon"

            async def _resolve_command_target_date(self, now):
                return "2026-05-24", False

            async def refresh_state_for_day(
                self, date_str, data, now, source="", detail="", force=False
            ):
                self.calls.append((date_str, source, detail, force))
                data.state = LifeState.from_value(
                    {
                        "energy": 22,
                        "mood": "需要休息",
                        "busyness": 50,
                        "social": 18,
                        "sleep": {"quality": 35, "summary": "睡眠不足"},
                        "summary": "今天只想低负担互动",
                        "updated_at": "2026-05-24 10:00",
                        "source": source,
                    }
                )
                return data

        runtime = CommandRuntime()
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        result = await center.adjust_life(
            event, action="refresh_state", detail="刚刚聊了很久"
        )

        self.assertEqual(
            runtime.calls, [("2026-05-24", "manual", "刚刚聊了很久", True)]
        )
        self.assertIn("体力：22/100", result)
        self.assertIn("今天只想低负担互动", result)
