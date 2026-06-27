import asyncio
import datetime
import types
import unittest

from support import Event, LifeSettings, DailyLifeCommandCenter, DataManager, async_return
from core.models import DayRecord, EventRecord, LifeState, PlaceRecord, RelationshipNote, RelationshipRecord, TimelineItem, WeekTemplateRecord


class LifeCommandsTest(unittest.IsolatedAsyncioTestCase):
    async def test_config_command_displays_vision_provider(self):
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict(
                {
                    "rhythm_config": {"llm_provider": "generation-model"},
                    "memory_config": {"provider": "memory-model"},
                    "vision_config": {"provider": "vision-model"},
                }
            ),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        event.message_str = "/生活 配置"

        result = await center.dispatch(event).__anext__()

        self.assertIn("视觉 vision-model", result)

    async def test_commitment_commands_manage_future_promises(self):
        archive = DataManager()
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=archive,
            contact_resolver=types.SimpleNamespace(resolve_event_sender=lambda event: async_return("阿林")),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()

        event.message_str = "/生活 承诺 添加 周末一起看电影"
        added = await center.dispatch(event).__anext__()
        self.assertIn("已记录承诺 #1", added)

        event.message_str = "/生活 承诺"
        listed = await center.dispatch(event).__anext__()
        self.assertIn("周末一起看电影", listed)

        event.message_str = "/生活 承诺 延期 1 2026-06-01"
        delayed = await center.dispatch(event).__anext__()
        self.assertIn("已延期", delayed)
        self.assertEqual((await archive.get_commitment(1)).trigger_date, "2026-06-01")

        event.message_str = "/生活 承诺 完成 1"
        done = await center.dispatch(event).__anext__()
        self.assertIn("已更新", done)
        self.assertEqual((await archive.get_commitment(1)).status, "done")

    async def test_template_create_uses_natural_language_and_sqlite_archive(self):
        class CommandArchive:
            def __init__(self):
                self.saved = None

            async def save_custom_week_template(self, template):
                self.saved = template
                return template

        class CommandComposer:
            async def compose_week_template_from_text(self, instruction):
                self.instruction = instruction
                return WeekTemplateRecord(
                    template_id="light_recovery",
                    name="轻恢复周",
                    description="减少外出，早睡和轻社交",
                    emoji="🌿",
                    goals=["早睡", "低强度运动"],
                )

        archive = CommandArchive()
        composer = CommandComposer()
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=archive,
            composer=composer,
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        event.message_str = "/生活 模板 新建 轻恢复周：这周偏累，减少外出"

        stream = center.dispatch(event)
        first = await stream.__anext__()
        second = await stream.__anext__()

        self.assertIn("正在整理", first)
        self.assertIn("light_recovery", second)
        self.assertEqual(archive.saved.name, "轻恢复周")
        self.assertIn("这周偏累", composer.instruction)

    async def test_template_management_commands_update_custom_template(self):
        class CommandArchive:
            def __init__(self):
                self.calls = []

            async def set_custom_week_template_weight(self, template_id, weight):
                self.calls.append(("weight", template_id, weight))
                return True

            async def set_custom_week_template_enabled(self, template_id, enabled):
                self.calls.append(("enabled", template_id, enabled))
                return True

            async def delete_custom_week_template(self, template_id):
                self.calls.append(("delete", template_id))
                return True

        archive = CommandArchive()
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=archive,
            composer=types.SimpleNamespace(_get_week_templates=lambda include_disabled=False: async_return({})),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)

        event = Event()
        event.message_str = "/生活 模板 权重 light_recovery 0.4"
        result = await center.dispatch(event).__anext__()
        self.assertIn("已更新", result)

        event.message_str = "/生活 模板 禁用 light_recovery"
        result = await center.dispatch(event).__anext__()
        self.assertIn("已更新", result)

        event.message_str = "/生活 模板 删除 light_recovery"
        result = await center.dispatch(event).__anext__()
        self.assertIn("已删除", result)

        self.assertEqual(
            archive.calls,
            [
                ("weight", "light_recovery", 0.4),
                ("enabled", "light_recovery", False),
                ("delete", "light_recovery"),
            ],
        )

    async def test_template_management_command_updates_builtin_template_state(self):
        class CommandArchive:
            def __init__(self):
                self.calls = []

            async def set_builtin_item_enabled(self, kind, item_id, enabled, scope=""):
                self.calls.append((kind, scope, item_id, enabled))
                return True

        archive = CommandArchive()
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=archive,
            composer=types.SimpleNamespace(_get_week_templates=lambda include_disabled=False: async_return({})),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        event.message_str = "/生活 模板 禁用 regular"

        result = await center.dispatch(event).__anext__()

        self.assertIn("已更新", result)
        self.assertEqual(archive.calls, [("template", "", "regular", False)])

    async def test_command_reset_yields_progress_before_generation(self):
        class CommandArchive:
            async def get_day(self, date_str):
                return DayRecord(date=date_str, timeline=[TimelineItem(time="10:00", activity="写手帐")])

        class CommandComposer:
            def __init__(self):
                self.calls = []

            async def _get_week_templates(self):
                return {}

            async def update_outfit(self, date_str, period, current_time=None):
                self.calls.append(("update_outfit", date_str, period, current_time))
                return DayRecord(date=date_str, outfit="新穿搭")

        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            generation_lock=asyncio.Lock(),
            archive=CommandArchive(),
            composer=CommandComposer(),
            week_templates={},
            failed_dates={},
            _get_curr_period=lambda: "afternoon",
            _target_datetime_for_command=lambda date_str, now: datetime.datetime(2026, 5, 24, now.hour, now.minute),
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        event.message_str = "/生活 重置 保持"

        stream = center.dispatch(event)
        first = await stream.__anext__()
        self.assertIn("重新生成", first)
        self.assertEqual(runtime.composer.calls, [])

        second = await stream.__anext__()
        self.assertIn("生成成功", second)
        self.assertEqual(
            [item[:3] for item in runtime.composer.calls],
            [("update_outfit", "2026-05-24", "afternoon")],
        )
        self.assertIsInstance(runtime.composer.calls[0][3], datetime.datetime)

    async def test_sleep_and_stay_awake_commands_are_removed(self):
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=DataManager(),
            _get_curr_period=lambda: "night",
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()

        event.message_str = "/生活 睡觉"
        sleep_result = await center.dispatch(event).__anext__()
        event.message_str = "/生活 熬夜"
        stay_awake_result = await center.dispatch(event).__anext__()

        self.assertIn("未知指令", sleep_result)
        self.assertIn("未知指令", stay_awake_result)

    async def test_world_command_displays_relationship_places_and_events(self):
        class CommandArchive:
            async def get_recent_relationships(self, limit=8):
                return [
                    RelationshipRecord(
                        id="u1",
                        name="阿林",
                        interactions=3,
                        notes=[RelationshipNote(date="2026-05-24", content="约过周末看展")],
                    )
                ]

            async def get_recent_places(self, limit=10):
                return [PlaceRecord(name="常去咖啡店", type="cafe", visits=2, hint="写手帐")]

            async def get_recent_events(self, limit=10):
                return [EventRecord(date="2026-05-24", summary="在常去咖啡店完成手帐", place="常去咖啡店")]

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
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        event.message_str = "/生活 世界"

        result = await center.dispatch(event).__anext__()

        self.assertIn("日常生活世界", result)
        self.assertIn("阿林", result)
        self.assertIn("常去咖啡店", result)
        self.assertIn("完成手帐", result)

    async def test_display_world_alias_is_removed(self):
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=DataManager(),
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        event.message_str = "/生活 显示 世界"

        result = await center.dispatch(event).__anext__()

        self.assertIn("未知显示项", result)

    async def test_storage_command_lists_and_clears_category(self):
        archive = DataManager()
        await archive.save_day(
            DayRecord(date="2026-05-24", timeline=[TimelineItem(time="10:00", activity="整理手帐")])
        )
        await archive.touch_relationship("u1", name="阿林", note="聊到周末看展", date_str="2026-05-24")
        runtime = types.SimpleNamespace(
            config=LifeSettings.from_dict({}),
            archive=archive,
            _get_curr_period=lambda: "afternoon",
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
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

    async def test_status_command_displays_current_state(self):
        class CommandArchive:
            async def get_day(self, date_str):
                return DayRecord(
                    date=date_str,
                    timeline=[TimelineItem(time="10:00", activity="在家整理手帐", status="慢慢来")],
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
            _resolve_command_target_date=lambda now: async_return(("2026-05-24", False)),
        )
        center = DailyLifeCommandCenter(runtime)
        event = Event()
        event.message_str = "/生活 状态"

        result = await center.dispatch(event).__anext__()

        self.assertIn("在家整理手帐", result)
        self.assertIn("体力：32/100", result)
        self.assertIn("今天不太想出门", result)

    async def test_refresh_state_command_forces_runtime_update(self):
        class CommandArchive:
            async def get_day(self, date_str):
                return DayRecord(
                    date=date_str,
                    timeline=[TimelineItem(time="10:00", activity="在家整理手帐", status="慢慢来")],
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

            async def refresh_state_for_day(self, date_str, data, now, source="", detail="", force=False):
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
        event.message_str = "/生活 刷新状态 刚刚聊了很久"

        result = await center.dispatch(event).__anext__()

        self.assertEqual(runtime.calls, [("2026-05-24", "manual", "刚刚聊了很久", True)])
        self.assertIn("体力：22/100", result)
        self.assertIn("今天只想低负担互动", result)
