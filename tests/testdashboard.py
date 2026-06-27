import asyncio
import base64
import datetime
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from support import (
    BehaviorFeedbackRecord,
    CatalogItemRecord,
    DailyLifeDashboardMixin,
    DataManager,
    FocusTargetRecord,
    GroupEnvironmentRecord,
    HairStyleRecord,
    LifeEpisodeRecord,
    LifeSettings,
    LifeTermRecord,
    MemoryBoundaryRecord,
    MemoryEvidenceRecord,
    DayRecord,
    EventRecord,
    LifeState,
    PlaceRecord,
    RelationshipNote,
    RelationshipRecord,
    TimelineItem,
    WeekPlanRecord,
    WeekTemplateRecord,
)
from core.presets import DEFAULT_CATALOG_POOLS, DEFAULT_STYLE_TO_HAIR_MAP
from core.archive import builtin_entry_id
from core.models import ActionDecisionRecord, MessageVisibilityRecord


class PageContext:
    def __init__(self):
        self.routes = []

    def register_web_api(self, path, handler, methods, desc):
        self.routes.append((path, handler, methods, desc))


class PageComposer:
    def __init__(self, archive):
        self.archive = archive
        self.daily_calls = []
        self.week_calls = []
        self.web_inspiration = types.SimpleNamespace(search=self.web_search)
        self.web_calls = []

    async def web_search(self, keyword, prompt_template, **kwargs):
        self.web_calls.append((keyword, prompt_template, kwargs))
        return f"联网参考：{keyword}"

    async def _get_persona(self):
        return "测试人格"

    async def _get_week_plan(self):
        return WeekPlanRecord(
            week_id="2026-W23",
            theme="慢生活周",
            goals=["恢复体力", "少安排高强度外出"],
            daily_hints={"thursday": "今天节奏轻一点"},
            suggested_activities={"weekday": ["整理书桌", "散步"]},
            template_id="light_recovery",
            generated=True,
        )

    async def _get_week_templates(self, include_disabled=False):
        custom = await self.archive.get_custom_week_templates(include_disabled=include_disabled)
        states = await self.archive.get_builtin_item_states("template")
        regular_enabled = states.get("regular", True)
        templates = {
            "regular": {
                "emoji": "R",
                "name": "常规周",
                "description": "保持节奏",
                "goals": ["按日常节奏"],
                "daily_hints": {},
                "suggested_activities": {},
                "weight": 0.2,
                "enabled": regular_enabled,
            }
        }
        if not include_disabled and not regular_enabled:
            templates.pop("regular")
        templates.update({template_id: item.as_template_dict() for template_id, item in custom.items()})
        return templates

    async def generate_daily(self, date=None, force=False, target_hour=None, extra=None, web_inspiration=""):
        self.daily_calls.append((date, force, target_hour, extra, web_inspiration))
        day = DayRecord(
            date=date.strftime("%Y-%m-%d"),
            outfit="浅蓝外套",
            timeline=[TimelineItem(time="10:00", activity="在窗边写手帐", status="平静")],
        )
        await self.archive.save_day(day)
        return day

    async def generate_week_plan(self, template_id=None, goals="", web_inspiration=""):
        self.week_calls.append((template_id, goals, web_inspiration))
        return WeekPlanRecord(
            week_id="2026-W23",
            theme="新周计划",
            goals=[goals or "按日常节奏"],
            template_id=template_id or "random",
            generated=True,
        )

    async def compose_week_template_from_text(self, description, use_web=False):
        return WeekTemplateRecord(
            template_id="quiet_week",
            name="安静恢复周",
            description=description,
            emoji="Q",
            weight=0.4,
            goals=["早睡"],
        )

    async def compose_catalog_item_from_text(self, category, description, use_web=False):
        return CatalogItemRecord(category=category, text=f"智能素材：{description}", enabled=True)

    async def compose_hair_style_from_text(self, description, use_web=False):
        return HairStyleRecord(
            name=f"智能发型组：{description}",
            hairstyles=[f"{description}低挽长发", f"{description}半扎长发"],
            enabled=True,
        )

class PageRuntime:
    def __init__(self):
        self.raw_config = {
            "rhythm_config": {"schedule_time": "07:00"},
            "state_config": {"enabled": True, "refresh_minutes": 30},
        }
        self.config = LifeSettings.from_dict({})
        self.archive = DataManager()
        self.composer = PageComposer(self.archive)
        self.generation_lock = asyncio.Lock()
        self.refresh_calls = []
        self.apply_calls = []
        self.data_dir = Path(tempfile.mkdtemp(prefix="daily_life_page_"))
        self.data_path = self.data_dir / "daily_life.db"

    async def resolve_injection_target(self, now):
        return "2026-06-11", False

    @staticmethod
    def _target_datetime_for_command(date_str, now):
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=now.hour,
            minute=now.minute,
        )

    async def refresh_state_for_day(self, date_str, now=None, source="", detail="", force=False):
        self.refresh_calls.append((date_str, source, detail, force))
        data = await self.archive.get_day(date_str)
        if data:
            data.state = LifeState.from_value(
                {
                    "energy": 44,
                    "mood": "平静",
                    "busyness": 50,
                    "social": 30,
                    "sleep": {"quality": 60, "summary": "睡得一般"},
                    "summary": "今天适合慢一点",
                    "source": source,
                }
            )
            await self.archive.save_day(data)
        return data

    async def apply_config(self, config):
        self.apply_calls.append(config)
        self.raw_config.clear()
        self.raw_config.update(config)
        self.config = LifeSettings.from_dict(config)
        return self.config


class PagePlugin(DailyLifeDashboardMixin):
    def __init__(self):
        self.context = PageContext()
        self.runtime = PageRuntime()
        self.body = {}
        self.method = "GET"

    async def _page_json_body(self):
        return dict(self.body)

    def _page_request_method(self):
        return self.method


class DailyLifeDashboardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = PagePlugin()
        await self.plugin.runtime.archive.save_day(
            DayRecord(
                date="2026-06-11",
                outfit="浅蓝外套和白裙子",
                weather="北京 晴 24C",
                timeline=[
                    TimelineItem(time="09:20", activity="整理早餐和手帐", status="慢慢来"),
                    TimelineItem(time="14:10", activity="去常去咖啡店写稿", status="专注"),
                ],
                places=[PlaceRecord(name="常去咖啡店", type="cafe", hint="适合写稿")],
                new_events=[EventRecord(date="2026-06-11", summary="完成一页手帐", place="家里")],
                state=LifeState(
                    energy=52,
                    mood="轻松",
                    mood_score=74,
                    busyness=40,
                    social=35,
                    stress=28,
                    focus=68,
                    sleepiness=24,
                    outgoing=46,
                    emotional_stability=72,
                    interaction_capacity=58,
                ),
                meta={
                    "theme": "安静整理日",
                    "mood": "薄荷绿·治愈",
                    "schedule_type": "宅家充电的慵懒一日",
                    "style": "清爽休闲风",
                    "sleep_debt": "1.5",
                    "energy_carryover": "62",
                    "life_mode": "resting",
                    "sleep_mode": "normal",
                    "schedule_intent": "rest",
                    "plan_outfit_decision": "keep",
                    "outfit_decision": "keep",
                    "outfit_reason": "今天没有出门需求，保持当前穿搭更自然。",
                },
                outfit_history={"afternoon": "浅蓝外套和白裙子"},
                memo="下午确认明天安排",
                state_log=["09:20 起床后慢慢恢复", "14:10 去咖啡店前确认穿搭适合外出"],
            )
        )
        await self.plugin.runtime.archive.save_custom_week_template(
            WeekTemplateRecord(
                template_id="light_recovery",
                name="轻恢复周",
                description="少外出，多休息",
                emoji="L",
                weight=0.5,
                goals=["早睡"],
                enabled=True,
            )
        )
        await self.plugin.runtime.archive.touch_relationship(
            "u1",
            name="阿林",
            note="约过周末看展",
            date_str="2026-06-10",
            source="chat",
        )
        await self.plugin.runtime.archive.touch_places(
            "2026-06-10",
            [PlaceRecord(name="常去咖啡店", type="cafe", hint="适合写稿")],
        )
        await self.plugin.runtime.archive.add_events(
            "2026-06-10",
            [EventRecord(date="2026-06-10", summary="和阿林聊到看展", people=["阿林"], place="线上")],
        )
        episode = await self.plugin.runtime.archive.save_life_episode(
            LifeEpisodeRecord(
                date="2026-06-11",
                title="轻量恢复日",
                summary="今天偏慢节奏，减少外出。",
                kind="daily_plan",
                source="daily",
            )
        )
        await self.plugin.runtime.archive.save_memory_evidence(
            MemoryEvidenceRecord(
                target_type="life_episode",
                target_id=str(episode.id),
                evidence_type="daily_generation",
                date="2026-06-11",
                summary="来自今日生成",
            )
        )
        await self.plugin.runtime.archive.save_memory_evidence(
            MemoryEvidenceRecord(
                target_type="relationship",
                target_id="u1",
                evidence_type="observation",
                date="2026-06-11",
                summary="测试用户在私聊中主动邀约一起吃饭",
            )
        )
        await self.plugin.runtime.archive.add_behavior_feedback(
            BehaviorFeedbackRecord(
                date="2026-06-11",
                scene="日程生成",
                action="reduce_outing",
                feedback="低体力时减少外出更自然。",
                result="positive",
                score=1.0,
            )
        )
        await self.plugin.runtime.archive.add_behavior_feedback(
            BehaviorFeedbackRecord(
                date="2026-06-11",
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后会话继续有新回应",
                result="positive",
                score=1.0,
                reason="后续消息：继续聊",
                source="proactive_reply",
            )
        )
        await self.plugin.runtime.archive.add_behavior_feedback(
            BehaviorFeedbackRecord(
                date="2026-06-11",
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后会话继续有新回应",
                result="positive",
                score=1.0,
                reason="后续消息：继续聊",
                source="proactive_reply",
            )
        )
        await self.plugin.runtime.archive.upsert_focus_target(
            FocusTargetRecord(
                target_type="topic",
                target_id="早睡",
                label="早睡恢复",
                priority=70,
                reason="睡眠债偏高",
            )
        )
        await self.plugin.runtime.archive.upsert_life_term(
            LifeTermRecord(term="蹲后续", meaning="暂时围观同一话题后续", last_seen="2026-06-11")
        )
        await self.plugin.runtime.archive.set_memory_boundary(
            MemoryBoundaryRecord(source_scope="group:1", target_scope="private:1", policy="ask", reason="跨域谨慎引用")
        )

    async def test_registers_page_routes(self):
        self.plugin._register_page_web_apis()
        paths = [item[0] for item in self.plugin.context.routes]

        self.assertIn("/astrbot_plugin_daily_life/page/status", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/template/create", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/template/save", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/catalog/create", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/hair/create", paths)
        self.assertNotIn("/astrbot_plugin_daily_life/page/workshop/expand", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/timeline/save", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/action/generate-week", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/config", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/config/character-reference", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/config/character-reference/preview", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/config/character-reference/delete", paths)
        self.assertNotIn("/astrbot_plugin_daily_life/page/storage/cleanup", paths)
        self.assertNotIn("/astrbot_plugin_daily_life/page/storage/clear", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/experience/episode/correct", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/experience/focus", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/experience/boundary", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/experience/feedback", paths)

    async def test_page_config_get_returns_schema_and_current_config(self):
        result = await self.plugin.page_config()

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertIn("rhythm_config", data["schema"])
        self.assertEqual(data["config"]["rhythm_config"]["schedule_time"], "07:00")
        self.assertFalse(data["saved"])

    async def test_page_config_post_applies_runtime_config(self):
        self.plugin.method = "POST"
        self.plugin.body = {
                "config": {
                    "rhythm_config": {
                        "schedule_time": "08:15",
                    },
                    "state_config": {"enabled": False, "refresh_minutes": 45},
                }
            }

        result = await self.plugin.page_config()

        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["saved"])
        self.assertEqual(self.plugin.runtime.config.schedule_time, "08:15")
        self.assertFalse(self.plugin.runtime.config.state.enabled)
        self.assertEqual(self.plugin.runtime.raw_config["rhythm_config"]["schedule_time"], "08:15")

    async def test_character_reference_upload_saves_image_in_plugin_data_dir(self):
        payload = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nlife").decode("ascii")
        self.plugin.body = {"image": payload, "filename": "正面参考.png"}

        result = await self.plugin.page_character_reference_upload()

        self.assertTrue(result["ok"])
        item = result["data"]["item"]
        saved_path = Path(item["path"])
        self.assertTrue(saved_path.name.startswith("character_reference_"))
        self.assertEqual(saved_path.parent, self.plugin.runtime.data_path.parent / "media_references")
        self.assertEqual(item["name"], "正面参考.png")
        self.assertEqual(item["mime"], "image/png")
        self.assertEqual(item["size"], len(b"\x89PNG\r\n\x1a\nlife"))
        self.assertEqual(saved_path.read_bytes(), b"\x89PNG\r\n\x1a\nlife")

        self.plugin.body = {"path": str(saved_path)}
        preview = await self.plugin.page_character_reference_preview()
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["data"]["data_url"].startswith("data:image/png;base64,"))

        self.plugin.body = {"path": str(saved_path)}
        deleted = await self.plugin.page_character_reference_delete()
        self.assertTrue(deleted["ok"])
        self.assertFalse(saved_path.exists())

        self.plugin.body = {"path": str(self.plugin.runtime.data_path)}
        blocked = await self.plugin.page_character_reference_delete()
        self.assertFalse(blocked["ok"])

    async def test_build_page_status_returns_current_life_world_and_templates(self):
        status = await self.plugin._build_page_status()

        self.assertEqual(status["target_date"], "2026-06-11")
        self.assertEqual(status["day"]["outfit"], "浅蓝外套和白裙子")
        self.assertEqual(status["day"]["state"]["energy"], 52)
        self.assertEqual(status["day"]["state"]["mood_score"], 74)
        self.assertEqual(status["day"]["state"]["stress"], 28)
        self.assertEqual(status["day"]["state"]["interaction_capacity"], 58)
        self.assertEqual(status["day"]["state_log"][0], "09:20 起床后慢慢恢复")
        self.assertEqual(status["day"]["meta"]["sleep_debt"], "1.5")
        self.assertEqual(status["day"]["meta"]["energy_carryover"], "62")
        self.assertEqual(status["day"]["meta"]["mood"], "薄荷绿·治愈")
        self.assertEqual(status["day"]["meta"]["schedule_type"], "宅家充电的慵懒一日")
        self.assertEqual(status["day"]["meta"]["schedule_intent"], "rest")
        self.assertEqual(status["week_plan"]["theme"], "慢生活周")
        self.assertEqual(status["world"]["relationships"][0]["name"], "阿林")
        relationship_evidence = [
            item for item in status["experience"]["evidence"]
            if item["target_type"] == "relationship"
        ][0]
        self.assertEqual(relationship_evidence["target_label"], "阿林")
        self.assertEqual(status["experience"]["episodes"][0]["title"], "轻量恢复日")
        proactive_feedback = [
            item for item in status["experience"]["feedback"]
            if item["scene"] == "闲时回复读空气" and item["action"] == "闲时续话"
        ]
        self.assertEqual(len(proactive_feedback), 1)
        self.assertEqual(status["experience"]["focus_targets"][0]["label"], "早睡恢复")
        self.assertEqual(status["experience"]["terms"][0]["term"], "蹲后续")
        self.assertGreater(status["experience"]["health"]["score"], 0)
        self.assertNotIn("storage", status)
        self.assertTrue(any(item["template_id"] == "light_recovery" and item["editable"] for item in status["templates"]))

    async def test_page_day_hides_yesterday_current_during_extended_night(self):
        day = await self.plugin.runtime.archive.get_day("2026-06-11")
        page_day = self.plugin._page_day(
            day,
            datetime.datetime(2026, 6, 12, 1, 30),
            extended_night=True,
        )

        self.assertIsNone(page_day["current"])
        self.assertIsNone(page_day["next"])
        self.assertTrue(page_day["extended_night"])

    async def test_page_status_filters_duplicate_feedback_rows(self):
        duplicate = [
            BehaviorFeedbackRecord(
                id=1,
                date="2026-06-11",
                target_type="proactive_session",
                target_id="aiocqhttp:GroupMessage:100",
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后会话继续有新回应",
                result="positive",
                score=1.0,
                reason="后续消息：继续聊",
                source="proactive_reply",
            ),
            BehaviorFeedbackRecord(
                id=2,
                date="2026-06-12",
                target_type="proactive_session",
                target_id="aiocqhttp:GroupMessage:200",
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后会话继续有新回应",
                result="positive",
                score=1.0,
                reason="后续消息：换了另一句回复",
                source="proactive_reply",
            ),
            BehaviorFeedbackRecord(
                id=3,
                date="2026-06-12",
                target_type="proactive_session",
                target_id="aiocqhttp:GroupMessage:100",
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后一段时间内没有新的可见回应",
                result="ignored",
                score=-1.0,
                reason="沉默超时",
                source="proactive_reply",
            ),
        ]

        unique = self.plugin._page_feedback_records(duplicate)

        self.assertEqual(len(unique), 2)
        self.assertEqual(unique[0].id, 1)
        self.assertEqual(unique[1].id, 3)

    async def test_page_status_feedback_dedupe_ignores_score_and_fallback_text(self):
        records = [
            BehaviorFeedbackRecord(
                id=1,
                date="2026-06-11",
                target_type="proactive_session",
                target_id="session-a",
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后会话继续有新回应",
                result="positive",
                score=1.0,
                reason="后续消息：继续聊",
                source="proactive_reply",
            ),
            BehaviorFeedbackRecord(
                id=2,
                date="2026-06-12",
                target_type="proactive_session",
                target_id="session-b",
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后会话继续有新回应",
                result="positive",
                score=2.0,
                reason="另一条后续说明",
                source="chat_memory",
            ),
        ]

        unique = self.plugin._page_feedback_records(records)

        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0].id, 1)

    async def test_page_status_shows_attention_and_decision_records(self):
        await self.plugin.runtime.archive.save_message_visibility(
            MessageVisibilityRecord(
                session_id="aiocqhttp:GroupMessage:100",
                sender_profile_id="u1",
                sender_name="小林",
                group_id="100",
                visibility="seen_but_ignored",
                reason="扫到了但当时不想接普通闲聊",
            )
        )
        await self.plugin.runtime.archive.save_action_decision(
            ActionDecisionRecord(
                session_id="aiocqhttp:GroupMessage:100",
                sender_profile_id="u1",
                sender_name="小林",
                group_id="100",
                action="observe",
                reason="先观察，不急着接话",
            )
        )
        await self.plugin.runtime.archive.save_action_decision(
            ActionDecisionRecord(
                session_id="aiocqhttp:GroupMessage:100",
                sender_profile_id="u1",
                sender_name="小林",
                group_id="100",
                action="reply",
                reason="后续有自然接话点",
            )
        )
        await self.plugin.runtime.archive.save_action_decision(
            ActionDecisionRecord(
                session_id="aiocqhttp:GroupMessage:100",
                sender_profile_id="u1",
                sender_name="小林",
                group_id="100",
                action="observe",
                reason="",
                scene_type="普通闲聊",
            )
        )

        status = await self.plugin._build_page_status()

        self.assertEqual(len(status["world"]["message_visibility"]), 1)
        self.assertEqual(status["world"]["message_visibility"][0]["reason"], "扫到了但当时不想接普通闲聊")
        self.assertEqual(len(status["world"]["action_decisions"]), 2)
        self.assertEqual(status["world"]["action_decisions"][0]["reason"], "后续有自然接话点")
        self.assertEqual(status["world"]["action_decisions"][1]["reason"], "先观察，不急着接话")

    async def test_page_status_keeps_group_environment_history(self):
        await self.plugin.runtime.archive.save_group_environment(
            GroupEnvironmentRecord(
                session_id="aiocqhttp:GroupMessage:100",
                group_id="100",
                group_name="测试群",
                atmosphere="平稳",
                topic="旧话题",
                summary="第一轮群聊氛围",
            )
        )
        await self.plugin.runtime.archive.save_group_environment(
            GroupEnvironmentRecord(
                session_id="aiocqhttp:GroupMessage:100",
                group_id="100",
                group_name="测试群",
                atmosphere="活跃",
                topic="新话题",
                summary="第二轮群聊氛围",
            )
        )

        status = await self.plugin._build_page_status()

        environments = status["world"]["group_environments"]
        self.assertGreaterEqual(len(environments), 2)
        self.assertEqual(environments[0]["topic"], "新话题")
        self.assertEqual(environments[1]["topic"], "旧话题")

    async def test_template_actions_use_structured_archive(self):
        self.plugin.body = {"template_id": "light_recovery", "weight": "0.8"}
        result = await self.plugin.page_template_weight()
        self.assertTrue(result["ok"])

        templates = await self.plugin.runtime.archive.get_custom_week_templates(include_disabled=True)
        self.assertEqual(templates["light_recovery"].weight, 0.8)

        self.plugin.body = {"template_id": "light_recovery", "enabled": False}
        result = await self.plugin.page_template_enabled()
        self.assertTrue(result["ok"])
        templates = await self.plugin.runtime.archive.get_custom_week_templates(include_disabled=True)
        self.assertFalse(templates["light_recovery"].enabled)

        self.plugin.body = {"description": "安静恢复周：减少外出，早睡"}
        result = await self.plugin.page_template_create()
        self.assertTrue(result["ok"])
        self.assertIn("status", result["data"])
        self.assertTrue(any(item["template_id"] == "regular" for item in result["data"]["status"]["templates"]))
        templates = await self.plugin.runtime.archive.get_custom_week_templates(include_disabled=True)
        self.assertIn("quiet_week", templates)

        self.plugin.body = {"template_id": "quiet_week"}
        result = await self.plugin.page_template_delete()
        self.assertTrue(result["ok"])
        self.assertIn("status", result["data"])
        self.assertFalse(any(item["template_id"] == "quiet_week" for item in result["data"]["status"]["templates"]))
        self.assertTrue(any(item["template_id"] == "regular" for item in result["data"]["status"]["templates"]))
        templates = await self.plugin.runtime.archive.get_custom_week_templates(include_disabled=True)
        self.assertNotIn("quiet_week", templates)

    async def test_template_save_persists_structured_template(self):
        self.plugin.body = {
            "template": {
                "template_id": "spark_week",
                "name": "闪光日常周",
                "emoji": "S",
                "description": "认真生活，积攒可爱",
                "weight": 0.3,
                "enabled": True,
                "cooldown_weeks": 2,
                "goals": ["保持节奏", "记录小确幸"],
                "daily_hints": {"monday": "扎起头发", "sunday": "整理心情"},
                "suggested_activities": {"weekday": ["认真搬砖"], "weekend": ["公园野餐"]},
                "tags": ["日常"],
            }
        }

        result = await self.plugin.page_template_save()

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["template"]["template_id"], "spark_week")
        self.assertIn("status", result["data"])
        self.assertTrue(any(
            item["template_id"] == "spark_week"
            for item in result["data"]["status"]["templates"]
        ))
        templates = await self.plugin.runtime.archive.get_custom_week_templates(include_disabled=True)
        template = templates["spark_week"]
        self.assertEqual(template.name, "闪光日常周")
        self.assertEqual(template.weight, 0.3)
        self.assertEqual(template.cooldown_weeks, 2)
        self.assertEqual(template.goals, ["保持节奏", "记录小确幸"])
        self.assertEqual(template.daily_hints["sunday"], "整理心情")
        self.assertEqual(template.suggested_activities["weekend"], ["公园野餐"])
        self.assertEqual(template.tags, ["日常"])

    async def test_template_save_rejects_invalid_template_id(self):
        self.plugin.body = {"template": {"template_id": "Bad ID", "name": "坏 ID"}}

        result = await self.plugin.page_template_save()

        self.assertFalse(result["ok"])
        self.assertIn("模板标识", result["error"]["message"])

    async def test_template_save_rejects_builtin_template_id(self):
        self.plugin.body = {"template": {"template_id": "regular", "name": "覆盖默认"}}

        result = await self.plugin.page_template_save()

        self.assertFalse(result["ok"])
        self.assertIn("内置周模板", result["error"]["message"])
        templates = await self.plugin.runtime.archive.get_custom_week_templates(include_disabled=True)
        self.assertNotIn("regular", templates)

    async def test_builtin_template_can_be_disabled_without_becoming_custom(self):
        self.plugin.body = {"template_id": "regular", "enabled": False}

        result = await self.plugin.page_template_enabled()
        status = await self.plugin._build_page_status()

        self.assertTrue(result["ok"])
        states = await self.plugin.runtime.archive.get_builtin_item_states("template")
        self.assertFalse(states["regular"])
        templates = await self.plugin.runtime.archive.get_custom_week_templates(include_disabled=True)
        self.assertNotIn("regular", templates)
        self.assertTrue(any(
            item["template_id"] == "regular" and item["enabled"] is False and item["editable"] is False
            for item in status["templates"]
        ))

    async def test_catalog_save_rejects_builtin_item_id(self):
        self.plugin.body = {
            "item": {
                "category": "daily_themes",
                "item_id": "builtin_0",
                "text": "覆盖默认素材",
            }
        }

        result = await self.plugin.page_catalog_save()

        self.assertFalse(result["ok"])
        self.assertIn("内置素材", result["error"]["message"])

    async def test_catalog_save_returns_updated_status(self):
        self.plugin.body = {
            "item": {
                "category": "daily_themes",
                "text": "复制出的柔软素材",
                "enabled": True,
            }
        }

        result = await self.plugin.page_catalog_save()

        self.assertTrue(result["ok"])
        item = result["data"]["item"]
        self.assertIn("status", result["data"])
        pool = next(
            pool for pool in result["data"]["status"]["catalog"]["pools"]
            if pool["category"] == "daily_themes"
        )
        self.assertTrue(any(saved["item_id"] == item["item_id"] for saved in pool["items"]))

    async def test_catalog_create_saves_generated_item(self):
        self.plugin.body = {"category": "daily_themes", "description": "轻快探索日"}

        result = await self.plugin.page_catalog_create()

        self.assertTrue(result["ok"])
        item = result["data"]["item"]
        self.assertEqual(item["category"], "daily_themes")
        self.assertTrue(item["item_id"])
        self.assertIn("轻快探索日", item["text"])
        self.assertIn("status", result["data"])
        pool = next(
            pool for pool in result["data"]["status"]["catalog"]["pools"]
            if pool["category"] == "daily_themes"
        )
        self.assertTrue(any(saved["item_id"] == item["item_id"] for saved in pool["items"]))
        items = await self.plugin.runtime.archive.get_custom_catalog_items(include_disabled=True)
        self.assertTrue(any(saved.item_id == item["item_id"] for saved in items["daily_themes"]))

        self.plugin.body = {"category": "daily_themes", "item_id": item["item_id"]}
        result = await self.plugin.page_catalog_delete()
        self.assertTrue(result["ok"])
        self.assertIn("status", result["data"])
        pool = next(pool for pool in result["data"]["status"]["catalog"]["pools"] if pool["category"] == "daily_themes")
        self.assertFalse(any(saved["item_id"] == item["item_id"] for saved in pool["items"]))

    async def test_builtin_catalog_item_can_be_disabled_without_becoming_custom(self):
        category = "daily_themes"
        item_id = builtin_entry_id(DEFAULT_CATALOG_POOLS[category][0])
        self.plugin.body = {"category": category, "item_id": item_id, "enabled": False}

        result = await self.plugin.page_catalog_enabled()
        status = await self.plugin._build_page_status()

        self.assertTrue(result["ok"])
        states = await self.plugin.runtime.archive.get_builtin_item_states("catalog", category)
        self.assertFalse(states[item_id])
        pool = next(item for item in status["catalog"]["pools"] if item["category"] == category)
        item = next(item for item in pool["items"] if item["item_id"] == item_id)
        self.assertFalse(item["enabled"])
        self.assertFalse(item["editable"])
        self.assertNotIn((category, item_id), self.plugin.runtime.archive.catalog_items)

    async def test_hair_save_rejects_builtin_style_id(self):
        self.plugin.body = {
            "style": {
                "style_id": "builtin_0",
                "name": "覆盖默认发型组",
                "hairstyles": ["低马尾"],
            }
        }

        result = await self.plugin.page_hair_save()

        self.assertFalse(result["ok"])
        self.assertIn("内置发型组", result["error"]["message"])

    async def test_hair_save_returns_updated_status(self):
        self.plugin.body = {
            "style": {
                "name": "复制出的柔软发型组",
                "hairstyles": ["松松散下的长发", "低低挽起的长发"],
                "enabled": True,
            }
        }

        result = await self.plugin.page_hair_save()

        self.assertTrue(result["ok"])
        style = result["data"]["style"]
        self.assertIn("status", result["data"])
        self.assertTrue(any(
            item["style_id"] == style["style_id"]
            for item in result["data"]["status"]["catalog"]["hair_styles"]
        ))

    async def test_hair_create_saves_generated_style(self):
        self.plugin.body = {"description": "雨天温柔风"}

        result = await self.plugin.page_hair_create()

        self.assertTrue(result["ok"])
        style = result["data"]["style"]
        self.assertTrue(style["style_id"])
        self.assertIn("雨天温柔风", style["name"])
        self.assertGreaterEqual(len(style["hairstyles"]), 2)
        self.assertIn("status", result["data"])
        self.assertTrue(any(
            item["style_id"] == style["style_id"]
            for item in result["data"]["status"]["catalog"]["hair_styles"]
        ))
        styles = await self.plugin.runtime.archive.get_custom_hair_styles(include_disabled=True)
        self.assertIn(style["style_id"], styles)

        self.plugin.body = {"style_id": style["style_id"]}
        result = await self.plugin.page_hair_delete()
        self.assertTrue(result["ok"])
        self.assertIn("status", result["data"])
        self.assertFalse(any(item["style_id"] == style["style_id"] for item in result["data"]["status"]["catalog"]["hair_styles"]))

    async def test_timeline_save_replaces_day_timeline(self):
        self.plugin.body = {
            "date": "2026-06-11",
            "timeline": [
                {"time": "15:30", "activity": "整理新的时间轴", "status": "专注"},
                {"time": "09:05", "activity": "慢慢吃早餐", "status": "放松"},
            ],
        }

        result = await self.plugin.page_timeline_save()
        day = await self.plugin.runtime.archive.get_day("2026-06-11")

        self.assertTrue(result["ok"])
        self.assertEqual([item.time for item in day.timeline], ["09:05", "15:30"])
        self.assertEqual(day.timeline[0].activity, "慢慢吃早餐")

    async def test_builtin_hair_style_can_be_disabled_without_becoming_custom(self):
        style_name = next(iter(DEFAULT_STYLE_TO_HAIR_MAP))
        style_id = builtin_entry_id(style_name)
        self.plugin.body = {"style_id": style_id, "enabled": False}

        result = await self.plugin.page_hair_enabled()
        status = await self.plugin._build_page_status()

        self.assertTrue(result["ok"])
        states = await self.plugin.runtime.archive.get_builtin_item_states("hair")
        self.assertFalse(states[style_id])
        style = next(item for item in status["catalog"]["hair_styles"] if item["style_id"] == style_id)
        self.assertFalse(style["enabled"])
        self.assertFalse(style["editable"])
        self.assertNotIn(style_id, self.plugin.runtime.archive.hair_styles)

    async def test_template_weight_rejects_non_number(self):
        self.plugin.body = {"template_id": "light_recovery", "weight": "heavy"}

        result = await self.plugin.page_template_weight()

        self.assertFalse(result["ok"])
        self.assertIn("权重必须是数字", result["error"]["message"])

    async def test_reset_day_uses_current_business_date(self):
        self.plugin.body = {"extra": "今天多安排室内活动"}

        result = await self.plugin.page_reset_day()

        self.assertTrue(result["ok"])
        call = self.plugin.runtime.composer.daily_calls[0]
        self.assertEqual(call[0].strftime("%Y-%m-%d"), "2026-06-11")
        self.assertTrue(call[1])
        self.assertEqual(call[3], "今天多安排室内活动")
        self.assertEqual(call[4], "")
        self.assertEqual(result["data"]["day"]["outfit"], "浅蓝外套")
        self.assertEqual(result["data"]["status"]["day"]["outfit"], "浅蓝外套")

    async def test_refresh_state_action_returns_full_page_status(self):
        result = await self.plugin.page_refresh_state()

        self.assertTrue(result["ok"])
        self.assertEqual(self.plugin.runtime.refresh_calls[-1], ("2026-06-11", "dashboard", "面板手动刷新", True))
        self.assertIn("status", result["data"])
        self.assertEqual(result["data"]["status"]["day"]["state"]["source"], "dashboard")
        self.assertEqual(result["data"]["status"]["target_date"], "2026-06-11")

    async def test_reset_day_can_use_web_inspiration(self):
        self.plugin.body = {"extra": "少女穿搭出门", "use_web": True}

        result = await self.plugin.page_reset_day()

        self.assertTrue(result["ok"])
        call = self.plugin.runtime.composer.daily_calls[0]
        self.assertEqual(call[3], "少女穿搭出门")
        self.assertIn("联网参考：少女穿搭出门", call[4])
        self.assertEqual(self.plugin.runtime.composer.web_calls[0][0], "少女穿搭出门")

    async def test_reset_day_uses_enabled_web_inspiration_by_default(self):
        self.plugin.runtime.config.web_inspiration.enabled = True
        self.plugin.body = {"extra": "雨天宅家"}

        result = await self.plugin.page_reset_day()

        self.assertTrue(result["ok"])
        self.assertIn("联网参考：雨天宅家", self.plugin.runtime.composer.daily_calls[0][4])
        self.assertEqual(self.plugin.runtime.composer.web_calls[0][0], "雨天宅家")

    async def test_reset_day_can_explicitly_skip_enabled_web_inspiration(self):
        self.plugin.runtime.config.web_inspiration.enabled = True
        self.plugin.body = {"extra": "雨天宅家", "use_web": False}

        result = await self.plugin.page_reset_day()

        self.assertTrue(result["ok"])
        self.assertEqual(self.plugin.runtime.composer.daily_calls[0][4], "")
        self.assertEqual(self.plugin.runtime.composer.web_calls, [])

    async def test_generate_week_can_use_web_inspiration(self):
        self.plugin.body = {"template_id": "regular", "goals": "轻松恢复", "use_web": True}

        result = await self.plugin.page_generate_week()

        self.assertTrue(result["ok"])
        self.assertEqual(self.plugin.runtime.composer.week_calls[0][0], "regular")
        self.assertEqual(self.plugin.runtime.composer.week_calls[0][1], "轻松恢复")
        self.assertIn("联网参考：轻松恢复", self.plugin.runtime.composer.week_calls[0][2])
        self.assertEqual(self.plugin.runtime.composer.web_calls[0][0], "轻松恢复")
        self.assertEqual(self.plugin.runtime.composer.web_calls[0][2]["category"], "周计划")
        self.assertEqual(self.plugin.runtime.composer.daily_calls, [])


class DailyLifeDashboardStaticTest(unittest.TestCase):
    def test_package_exports_plugin_entrypoint(self):
        from pathlib import Path

        init_file = Path(__file__).resolve().parents[1] / "__init__.py"
        self.assertTrue(init_file.exists())
        self.assertIn("DailyLifePlugin", init_file.read_text(encoding="utf-8"))

    def test_page_static_files_exist(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        self.assertTrue((root / "index.html").exists())
        self.assertTrue((root / "style.css").exists())
        self.assertTrue((root / "app.js").exists())
        self.assertTrue((root / "shared" / "labels.js").exists())
        self.assertTrue((root / "shared" / "display.js").exists())

    def test_dashboard_reference_preview_assets_use_cache_buster(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")

        self.assertIn("style.css?v=20260621-reference-preview-cache2", html)
        self.assertIn("app.js?v=20260621-reference-preview-cache2", html)
        self.assertIn('./ui/config.js?v=20260621-reference-preview-cache2', app)

    def test_dashboard_dom_entrypoints_exist(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        workshop = "\n".join(
            (root / "ui" / name).read_text(encoding="utf-8")
            for name in ("workshop.js", "template.js", "catalog.js", "hair.js")
        )

        ids = set(re.findall(r'id="([^"]+)"', html))
        el_block_match = re.search(r"const el = \{(?P<body>.*?)\n\};", app, re.S)
        self.assertIsNotNone(el_block_match)
        el_block = el_block_match.group("body")
        direct_refs = {
            name
            for name in re.findall(r"(?<![A-Za-z0-9_$])el\.([A-Za-z_$][A-Za-z0-9_$]*)", app)
            if name not in {"append", "htmlFor", "textContent"}
        }
        mapped_refs = set(re.findall(r"\n\s*([A-Za-z_$][A-Za-z0-9_$]*):", el_block))
        mapped_ids = set(re.findall(r'byId\("([^"]+)"\)', el_block))

        self.assertIn("const byId = (id) => document.getElementById(id);", app)
        self.assertIn("const all = (selector) => Array.from(document.querySelectorAll(selector));", app)
        self.assertEqual(direct_refs - mapped_refs, set())
        self.assertEqual(mapped_ids - ids, set())
        self.assertIn('viewButtons: all(".view-button")', app)
        self.assertIn('worldTabs: all("[data-world-tab]")', app)
        self.assertIn('actionGroups: all("[data-action-view]")', app)

    def test_dashboard_has_today_refresh_state_button(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="refreshStateButton"', html)
        self.assertIn('aria-label="刷新实时状态"', html)
        self.assertLess(html.index('id="refreshStateButton"'), html.index('id="targetDate"'))
        self.assertIn('refreshStateButton: byId("refreshStateButton")', app)
        self.assertIn('apiPost(\n      "page/action/refresh-state"', app)
        self.assertIn("page/status/wait", app)
        self.assertIn(".today-head-actions", style)
        self.assertIn(".refresh-state-button", style)
        self.assertIn("position: fixed;", style)
        self.assertIn("animation: notice-toast 4.2s ease forwards;", style)
        self.assertIn("void el.notice.offsetWidth;", app)

    def test_dashboard_groups_model_provider_settings(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        app = (root / "app.js").read_text(encoding="utf-8")
        config = (root / "ui" / "config.js").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")

        self.assertIn('const MODEL_SECTION_KEY = "__model_provider_settings"', config)
        self.assertIn('description: "大语言模型"', config)
        self.assertIn("function collectProviderConfigFields()", config)
        self.assertIn('spec._special === "select_provider"', config)
        self.assertIn("function configSectionVisibleFields", config)
        self.assertIn("!isProviderConfigField(itemSpec)", config)
        self.assertIn("renderModelConfigField", config)
        self.assertIn("config-source-label", config)
        self.assertIn(".config-source-label", style)
        self.assertIn("const modelLabel = configLabel(field.fieldKey, field.spec);", config)
        self.assertNotIn("const sectionLabel = configLabel(field.sectionKey", config)

    def test_dashboard_config_renders_template_list_fields(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        config = (root / "ui" / "config.js").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")

        self.assertIn('spec.type === "template_list"', config)
        self.assertIn("renderConfigTemplateList", config)
        self.assertIn("templateEntries", config)
        self.assertIn("normalizeTemplateListItem", config)
        self.assertIn("reorderItem", config)
        self.assertIn("pointerdown", config)
        self.assertIn("pointermove", config)
        self.assertIn("animateListFrom", config)
        self.assertIn("拖动排序", config)
        self.assertNotIn("上移", config)
        self.assertNotIn("下移", config)
        self.assertIn("添加", config)
        self.assertNotIn("text(item.name).trim()", config)
        self.assertIn(".config-field.template-list-field", style)
        self.assertIn(".template-list-item-actions", style)
        self.assertIn(".template-list-drag", style)
        self.assertIn(".template-list-item.is-drop-target", style)
        self.assertIn(".template-list-item.is-shifting", style)
        self.assertIn("will-change: transform", style)
        self.assertIn(".template-list-item-grid", style)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", style)

    def test_dashboard_settings_use_explicit_section_order(self):
        from pathlib import Path

        config = (Path(__file__).resolve().parents[1] / "pages" / "dashboard" / "ui" / "config.js").read_text(encoding="utf-8")

        self.assertIn("const CONFIG_SECTION_ORDER = [", config)
        self.assertIn("const CONFIG_SECTION_ORDER_INDEX = new Map", config)
        self.assertIn("function sortConfigSectionEntries(entries = [])", config)
        self.assertIn("function visibleConfigSections()", config)
        self.assertIn("const visibleSchemaEntries = visibleConfigSections();", config)

        expected_order = [
            '"rhythm_config"',
            '"weekly_theme_config"',
            '"weather_awareness"',
            '"state_config"',
            '"relationship_aliases"',
            '"commitment_config"',
            '"memory_config"',
            '"memos_config"',
            '"proactive_config"',
            '"lifecycle_config"',
            '"image_generation_config"',
            '"video_generation_config"',
            '"voice_generation_config"',
            '"web_inspiration_config"',
            '"storage_config"',
            '"story_engine_config"',
        ]
        positions = [config.index(token) for token in expected_order]
        self.assertEqual(positions, sorted(positions))

    def test_dashboard_weekly_theme_config_keeps_visible_fields(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8-sig"))
        weekly = schema["weekly_theme_config"]["items"]

        self.assertNotIn("enabled", weekly)
        self.assertIn("generation_day", weekly)
        self.assertIn("generation_time", weekly)
        self.assertIn("default_template", weekly)
        self.assertNotEqual(
            [key for key, spec in weekly.items() if spec.get("_special") != "select_provider"],
            [],
        )

    def test_dashboard_settings_auto_save_config(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        config = (root / "ui" / "config.js").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")

        self.assertNotIn("configSaveButton", html)
        self.assertNotIn("configSaveButton", app)
        self.assertNotIn("configReloadButton", html)
        self.assertNotIn("configReloadButton", app)
        self.assertNotIn("configSectionCount", html)
        self.assertNotIn("configSectionCount", app)
        self.assertNotIn("个分区", html)
        self.assertNotIn("个分区", app)
        self.assertIn("AUTOSAVE_DELAY_MS", config)
        self.assertIn("scheduleConfigAutosave", config)
        self.assertIn("等待自动保存", config)
        self.assertIn("已自动保存", config)
        self.assertIn("configVersion", app + config)
        self.assertIn(".toolbar-pill.saved", style)

    def test_dashboard_settings_hides_config_field_counts(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")

        self.assertNotIn("configSectionBadge", html)
        self.assertNotIn("configSectionBadge", app)
        self.assertNotIn("configFieldCount", html)
        self.assertNotIn("configFieldCount", app)
        self.assertNotIn("config-tab-count", app)
        self.assertNotIn(".config-tab-count", style)
        self.assertNotIn("项设置", html)
        self.assertNotIn("项设置", app)

    def test_dashboard_settings_text_prompts_use_horizontal_grid(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        app = (root / "app.js").read_text(encoding="utf-8")
        config = (root / "ui" / "config.js").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")

        self.assertIn('classes.push("text-field")', config)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", style)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", style)
        self.assertNotIn("repeat(auto-fit, minmax(min(100%, 380px), 1fr))", style)
        self.assertIn(".config-grid > .config-field", style)
        self.assertIn("grid-column: auto;", style)
        self.assertIn(".config-field.text-field textarea", style)
        self.assertNotIn('classes.push("wide")', config)
        self.assertNotIn('classes.push("extra-wide")', config)
        self.assertNotIn(".config-field.extra-wide", style)

    def test_dashboard_hides_storage_panel(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")

        for text in (
            "存储分类",
            "storage-panel",
            "storageTotal",
            "storageList",
            "storageCleanupAllButton",
            "按策略清理",
            "page/storage/cleanup",
            "page/storage/clear",
            "renderStorage",
        ):
            self.assertNotIn(text, html)
            self.assertNotIn(text, app)
            self.assertNotIn(text, style)

    def test_dashboard_workshop_has_web_inspiration_actions(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        workshop = "\n".join(
            (root / "ui" / name).read_text(encoding="utf-8")
            for name in ("workshop.js", "template.js", "catalog.js", "hair.js")
        )

        for element_id in (
            "weekWebButton",
            "templateWebButton",
            "catalogWebButton",
            "hairWebButton",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertNotIn('id="resetForm"', html)
        self.assertNotIn('id="resetExtraInput"', html)
        self.assertNotIn('id="resetWebButton"', html)
        self.assertNotIn("resetForm", app)
        self.assertNotIn("resetExtraInput", app)
        self.assertNotIn("resetWebButton", app)
        self.assertIn("const payload = { extra };", app)
        self.assertIn("payload.use_web = true;", app)
        self.assertNotIn("{ extra, use_web: useWeb }", app)
        self.assertNotIn('id="materialPackWebButton"', html)
        self.assertNotIn('id="materialPackForm"', html)
        self.assertNotIn("materialPackWebButton", app + workshop)
        self.assertNotIn("materialPackForm", app + workshop)
        self.assertNotIn("page/workshop/expand", app + workshop)
        self.assertNotIn("智能扩展", html)
        self.assertNotIn("联网扩展", html)
        self.assertIn(f'{element_id}: byId("{element_id}")', app)
        self.assertIn("use_web: useWeb", workshop)
        self.assertIn("generateWeek(el.weekGoalsInput.value.trim(), true)", app)
        self.assertIn('"page/action/generate-week"', app)
        self.assertIn("formTextValue", workshop)
        self.assertIn("fillTemplateEditor(findTemplate(templateId) || result.template)", workshop)
        self.assertIn("applyActionStatus(result)", app)
        self.assertIn("function findCatalogItem(category, itemId)", workshop)
        self.assertIn("function findHairStyle(styleId)", workshop)
        self.assertIn("renderCatalog(state.status || {});", workshop)
        self.assertIn("fillCatalogEditor(findCatalogItem(itemCategory, result.item.item_id) || result.item)", workshop)
        self.assertIn("fillHairEditor(findHairStyle(result.style.style_id) || result.style)", workshop)
        self.assertIn("renderTemplates(state.status || {});", workshop)
        self.assertIn("fillTemplateEditor(findTemplate(templateId) || result.template)", workshop)
        self.assertIn("state.templateDraftId = \"\";", workshop)
        self.assertIn('apiPost("page/template/delete"', workshop)
        self.assertIn("const next = templateItems()[0] || null", workshop)
        self.assertIn("联网填充", html)
        self.assertIn("联网新建", html)

    def test_dashboard_workshop_layout_keeps_library_wide(self):
        from pathlib import Path

        style = (
            Path(__file__).resolve().parents[1]
            / "pages"
            / "dashboard"
            / "style.css"
        ).read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: minmax(360px, 520px) minmax(0, 1fr);", style)
        self.assertIn("grid-template-columns: minmax(320px, 520px) minmax(0, 1fr);", style)
        self.assertIn(".template-editor .template-grid", style)
        self.assertIn(".catalog-editor .template-grid", style)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", style)
        self.assertNotIn("grid-template-columns: minmax(0, 1.4fr) minmax(300px, 0.8fr);", style)
        self.assertNotIn("grid-template-columns: minmax(0, 0.92fr) minmax(320px, 1.08fr);", style)

    def test_dashboard_generation_inputs_use_short_placeholder(self):
        from pathlib import Path

        html = (Path(__file__).resolve().parents[1] / "pages" / "dashboard" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="templateText" rows="3" placeholder="输入想要的日程基调"', html)
        self.assertIn('id="catalogText" rows="3" placeholder="输入想添入的灵感"', html)
        self.assertIn('id="hairText" rows="3" placeholder="输入想要的发型氛围"', html)
        self.assertNotIn("效果提示词", html)
        self.assertNotIn('id="templateText" rows="3" placeholder="轻恢复周：', html)
        self.assertNotIn('id="catalogText" rows="3" placeholder="给当前分类', html)
        self.assertNotIn("materialPackText", html)
        self.assertNotIn('id="hairText" rows="3" placeholder="雨天温柔风：', html)

    def test_dashboard_config_select_uses_schema_option_labels(self):
        from pathlib import Path

        config = (
            Path(__file__).resolve().parents[1]
            / "pages"
            / "dashboard"
            / "ui"
            / "config.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function configOptionLabel(option, spec = {})", config)
        self.assertIn("const labels = spec.option_labels || {};", config)
        self.assertIn("configOptionLabel(option, spec)", config)

    def test_dashboard_config_supports_character_reference_upload(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        config = (root / "pages" / "dashboard" / "ui" / "config.js").read_text(encoding="utf-8")
        style = (root / "pages" / "dashboard" / "style.css").read_text(encoding="utf-8")
        schema = (root / "_conf_schema.json").read_text(encoding="utf-8")

        self.assertIn('"_special": "character_reference_gallery"', schema)
        self.assertIn('apiPost("page/config/character-reference"', config)
        self.assertIn('apiPost("page/config/character-reference/preview"', config)
        self.assertIn('apiPost("page/config/character-reference/delete"', config)
        self.assertIn("function renderCharacterReferenceGallery(path, value)", config)
        self.assertIn('fileInput.accept = "image/png,image/jpeg,image/webp,image/gif"', config)
        self.assertIn("fileInput.multiple = true;", config)
        self.assertIn("referenceItemsForConfig(items)", config)
        self.assertIn("reference-gallery-preview", config)
        self.assertIn("reference-gallery-thumb", config)
        self.assertIn("const referencePreviewCache = new Map();", config)
        self.assertIn("function cachedReferencePreview(path)", config)
        self.assertIn("setReferencePreviewCache(result.item.path, image);", config)
        show_preview_body = config.split("function showReferencePreview", 1)[1].split("async function loadReferencePreview", 1)[0]
        self.assertNotIn("isConnected", show_preview_body)
        self.assertIn('thumb?.classList.add("is-loading")', config)
        self.assertIn('thumb?.classList.add("is-error")', config)
        self.assertIn("function createReferencePreviewImage(src, altText)", config)
        self.assertIn("thumb.prepend(preview);", config)
        self.assertIn('preview.classList.add("is-pending")', config)
        self.assertIn("window.setTimeout(finishWithError, 8000)", config)
        self.assertNotIn("function loadImageElement(src, altText)", config)
        self.assertNotIn("preview.hidden = true;", config)
        self.assertIn("reference-gallery-remove", config)
        self.assertIn("<svg viewBox=\"0 0 24 24\"", config)
        self.assertNotIn('reference-gallery-remove", "×"', config)
        self.assertIn(".reference-gallery-preview", style)
        self.assertIn(".reference-gallery-thumb", style)
        self.assertIn(".reference-gallery-thumb.is-loading::before", style)
        self.assertIn(".reference-gallery-thumb.is-error::after", style)
        self.assertIn(".reference-gallery-preview.is-pending", style)
        self.assertIn('content: ""', style)
        self.assertNotIn('content: "加载中"', style)
        self.assertIn('content: "预览失败"', style)
        self.assertIn(".reference-gallery-remove", style)
        self.assertIn(".reference-gallery-remove svg", style)
        self.assertIn(".reference-gallery-item", style)
        self.assertIn(".reference-gallery-actions", style)
        self.assertIn("overflow-x: auto", style)
        self.assertIn("flex: 0 0 82px", style)
        self.assertNotIn(".reference-upload", style)

    def test_dashboard_uses_world_attention_without_interrupt_panel(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")

        self.assertIn('data-world-tab="message_visibility"', html)
        self.assertNotIn("打断记录", html)
        self.assertNotIn("interruptCount", html)
        self.assertNotIn("interruptList", html)
        self.assertNotIn("interruptCount", app)
        self.assertNotIn("interruptList", app)
        self.assertNotIn("renderInterruptRecords", app)
        self.assertNotIn(".interrupt-panel", style)
        self.assertNotIn("决策审计", html)
        self.assertNotIn("auditCount", html)
        self.assertNotIn("auditList", html)
        self.assertNotIn("auditCount", app)
        self.assertNotIn("auditList", app)
        self.assertNotIn("renderAudit", app)
        self.assertNotIn(".audit-panel", style)

    def test_dashboard_translates_structured_life_enum_text(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        labels = (root / "shared" / "labels.js").read_text(encoding="utf-8")
        display = (root / "shared" / "display.js").read_text(encoding="utf-8")
        scripts = "\n".join([app, labels, display])
        style = (root / "style.css").read_text(encoding="utf-8")

        self.assertNotIn('const SCHEDULE_TONE_LABELS = {', app)
        self.assertIn('from "./shared/labels.js"', app)
        self.assertIn('from "./shared/display.js"', app)
        self.assertIn('export const SCHEDULE_TONE_LABELS = {', labels)
        self.assertIn('awake: "正常活动"', labels)
        self.assertIn('export const CURRENT_SLEEP_LABELS = {', labels)
        self.assertIn('awake: "未入睡"', labels)
        self.assertIn('["life_mode", SCHEDULE_TONE_LABELS, "日程基调"]', display)
        self.assertIn('["sleep_mode", SLEEP_MODE_LABELS, "睡眠倾向"]', display)
        self.assertIn('["schedule_intent", SCHEDULE_INTENT_LABELS, "活动倾向"]', display)
        self.assertIn('["生活模式", SCHEDULE_TONE_LABELS, "日程基调"]', display)
        self.assertIn('["日程倾向", SCHEDULE_INTENT_LABELS, "活动倾向"]', display)
        self.assertIn('export const PLAN_OUTFIT_DECISION_LABELS = {', labels)
        self.assertIn('outdoor: "预计外出"', labels)
        self.assertIn('["plan_outfit_decision", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"]', display)
        self.assertIn('["换装", OUTFIT_DECISION_LABELS, "穿搭"]', display)
        self.assertIn('["穿搭", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"]', display)
        self.assertIn("EVENT_STATUS_LABELS,", display.split('} from "./labels.js";', 1)[0])
        self.assertIn('outdoor: "外出"', labels)
        self.assertIn('appendInfoBox("当前睡眠"', app)
        self.assertIn('id="moodColorText"', html)
        self.assertIn('id="scheduleTypeText"', html)
        self.assertIn('id="scheduleToneText"', html)
        self.assertIn('id="scheduleIntentText"', html)
        self.assertIn('id="currentOutfitText"', html)
        self.assertIn('id="outfitDecisionText"', html)
        self.assertIn('id="todayWeekPlan"', html)
        self.assertIn("function renderTodayWeekPlan", app)
        self.assertIn("function stripLeadingEmoji", app)
        self.assertIn("function todayWeekRow", app)
        self.assertIn("clean(stripLeadingEmoji(week.theme), \"\")", app)
        self.assertIn("clean(stripLeadingEmoji(week.today_hint), \"\")", app)
        self.assertIn("clean(stripLeadingEmoji(week.today_suggested), \"\")", app)
        self.assertNotIn("today-week-title", app)
        self.assertIn('renderFactPair(el.currentOutfitText, currentOutfitDisplayText(day, meta))', app)
        self.assertIn('renderFactPair(el.outfitDecisionText, outfitDecisionText(meta))', app)
        self.assertIn("function renderFactPair(target, value)", app)
        self.assertIn('todayWeekRow("进度", hint)', app)
        self.assertIn('todayWeekRow("目标", suggested, "muted")', app)
        self.assertIn('card.replaceChildren(...lines)', app)
        self.assertNotIn('node("div", "today-week-card", ...(title ? [title] : []), ...lines)', app)
        self.assertNotIn("今日提醒", app)
        self.assertNotIn("今日建议", app)
        self.assertIn("<dt>👗 当前穿搭</dt>", html)
        self.assertIn("<dt>🪞 穿搭判断</dt>", html)
        self.assertIn("function moodColorText(value)", display)
        self.assertIn('body.includes("·")', display)
        self.assertIn("el.moodColorText.textContent = clean(moodColorText(meta.mood))", app)
        self.assertIn("function scheduleTypeText(value)", display)
        self.assertIn("el.scheduleTypeText.textContent = clean(scheduleTypeText(meta.schedule_type))", app)
        self.assertNotIn("meta.schedule_type || meta.style", app)
        self.assertIn("el.themeText.textContent = clean(meta.theme)", app)
        self.assertIn("el.scheduleToneText.textContent = clean(enumLabel(meta.life_mode, SCHEDULE_TONE_LABELS))", app)
        self.assertIn("function currentOutfitDisplayText(day = {}, meta = {})", display)
        self.assertIn("return { style, outfit }", display)
        self.assertIn("function outfitDecisionText(meta = {})", display)
        self.assertIn("return { decision, reason }", display)
        self.assertIn('renderFactPair(el.currentOutfitText, currentOutfitDisplayText(day, meta))', app)
        self.assertIn('renderFactPair(el.outfitDecisionText, outfitDecisionText(meta))', app)
        self.assertIn("function renderFactPair(target, value)", app)
        self.assertIn('node("span", "today-week-label", data.style)', app)
        self.assertIn('node("span", "today-week-label", data.decision)', app)
        self.assertNotIn("outfit-panel", html)
        self.assertNotIn("id=\"periodText\"", html)
        self.assertNotIn("id=\"outfitText\"", html)
        self.assertNotIn("periodText:", app)
        self.assertNotIn("outfitText:", app)
        self.assertNotIn(".outfit-panel", style)
        self.assertIn("function currentScheduleIntentText(day = {}, clock = currentClockDate())", app)
        self.assertIn("function renderRealtimeDayFacts(clock = currentClockDate())", app)
        self.assertIn("renderRealtimeDayFacts(clock)", app)
        self.assertNotIn("meta.style || enumLabel(day.time_period", app)
        self.assertNotIn('appendInfoBox("状态摘要"', app)
        self.assertIn('const mood = clean(lifeState.mood, "")', app)
        self.assertNotIn("lifeState.mood || lifeState.summary", app)
        self.assertNotIn('appendInfoBox("状态来源"', app)
        self.assertNotIn('["当前注意力"', app)
        self.assertNotIn('["状态摘要"', app)
        self.assertIn("enumLabel(sleep.depth, CURRENT_SLEEP_LABELS)", app)
        self.assertIn("recordLines([clean(health.summary), ...checks])", app)
        self.assertIn("function node(tag, className = \"\", content = \"\")", display)
        self.assertIn("function visibleLifeEpisodes(episodes)", display)
        self.assertIn('text(item.kind).trim().toLowerCase() !== "daily_plan"', display)
        self.assertIn("visibleEpisodes.slice(0, 4).forEach", app)
        self.assertIn("function lifeEpisodeLines(item)", display)
        self.assertIn('new Set(["时间轴", "地点"])', display)
        self.assertIn("recordLines([...lifeEpisodeLines(item), people]", app)
        self.assertNotIn("function visibleMemoryBoundaries(boundaries)", scripts)
        self.assertNotIn("boundaries.slice(0, 3).forEach", scripts)
        self.assertNotIn("experience.boundaries", scripts)
        self.assertNotIn('join(" / ")', scripts)
        self.assertNotIn("` / ${", scripts)
        self.assertNotIn("} / ${", scripts)
        self.assertIn('join(" · ")', scripts)
        self.assertIn("权重 ${Number(item.weight || 0).toFixed(1)} · ${clean(evidence)}", app)
        self.assertIn(".record-lines", style)
        self.assertIn("function evidenceTargetTitle(item)", display)
        self.assertIn("clean(item.target_label", display)
        self.assertIn("function stateLogText(value)", display)
        self.assertIn("PAGE_STATUS_REASON_LABELS", display)
        self.assertIn("stateLogText(entry)", app)
        self.assertIn('autonomous_life_update: "自主生活状态与穿搭更新"', labels)
        self.assertIn("width: min(1500px, 100%)", style)
        self.assertNotIn("width: min(1680px, 100%)", style)
        self.assertIn("grid-template-columns: minmax(250px, 0.84fr) minmax(350px, 1.2fr) minmax(380px, 1.08fr)", style)
        self.assertIn(".world-panel .panel-head h2", style)
        self.assertIn(".record-title > .muted", style)
        self.assertNotIn('"策略："', app)
        self.assertNotIn('"旁白："', app)
        self.assertNotIn("item.reply_strategy", app)
        self.assertNotIn("item.inner_monologue", app)

    def test_display_text_record_lines_runs_as_module(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = """
globalThis.document = {
  createElement(tag) {
    return {
      tagName: String(tag).toUpperCase(),
      className: "",
      textContent: "",
      children: [],
      classList: { add(name) { this.owner.className = this.owner.className ? `${this.owner.className} ${name}` : name; } },
      append(...items) { this.children.push(...items); },
    };
  }
};
const originalCreateElement = globalThis.document.createElement;
globalThis.document.createElement = (tag) => {
  const element = originalCreateElement(tag);
  element.classList.owner = element;
  return element;
};
const mod = await import("./pages/dashboard/shared/display.js");
if (!mod.humanizeToken("monday") || !mod.humanizeToken("random") || !mod.humanizeToken("daily")) {
  throw new Error("humanizeToken 没有正确转换配置选项标签");
}
const body = mod.recordLines(["状态：open", ["来源", "chat_memory"]]);
if (body.tagName !== "DIV" || !body.className.includes("record-lines") || body.children.length !== 2) {
  throw new Error("recordLines 没有生成预期节点");
}
const log = mod.stateLogText("12:30 群聊观察；留意=seen_but_ignored；裁定=save_memory；watch_state=peek；interrupt_level=high；reason=autonomous_life_update");
if (!log.includes("留意：看见但略过") || !log.includes("裁定：保存记忆") || !log.includes("观看状态：偶尔看一眼") || !log.includes("打断等级：强信号才打断") || !log.includes("原因：自主生活状态与穿搭更新")) {
  throw new Error(`stateLogText 没有翻译状态变化枚举：${log}`);
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def _dashboard_dom_mock_script(self):
        return """
class MockElement {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.className = "";
    this.textContent = "";
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this.disabled = false;
    this.classList = {
      add: (...names) => { this.className = [this.className, ...names].filter(Boolean).join(" "); },
      remove: (...names) => {
        const remove = new Set(names);
        this.className = this.className.split(/\\s+/).filter((name) => name && !remove.has(name)).join(" ");
      },
      toggle: (name, force) => {
        const names = new Set(this.className.split(/\\s+/).filter(Boolean));
        const enabled = force === undefined ? !names.has(name) : Boolean(force);
        if (enabled) names.add(name); else names.delete(name);
        this.className = Array.from(names).join(" ");
      },
    };
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener() {}
  querySelector() { return null; }
}
globalThis.Option = class Option {
  constructor(text, value) { this.text = text; this.textContent = text; this.value = value; }
};
globalThis.document = {
  getElementById: () => new MockElement(),
  querySelectorAll: () => [],
  createElement: (tag) => new MockElement(tag),
  addEventListener() {},
};
globalThis.window = {
  AstrBotPluginPage: null,
  addEventListener() {},
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
};
"""

    def test_dashboard_app_runs_as_module(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = self._dashboard_dom_mock_script() + """
await import("./pages/dashboard/app.js");
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_realtime_schedule_intent_prefers_extended_night_home(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = self._dashboard_dom_mock_script() + """
const mod = await import("./pages/dashboard/app.js");
const day = {
  date: "2026-06-25",
  extended_night: true,
  timeline: [
    { time: "18:20", activity: "回到家换居家服", status: "放松" },
    { time: "20:50", activity: "洗完澡准备睡前放松", status: "困倦" },
  ],
  state: {
    energy: 70,
    outgoing: 90,
    social: 40,
    busyness: 10,
    focus: 20,
    interaction_capacity: 30,
    sleepiness: 45,
    sleep: { depth: "light_rest" },
  },
};
const value = mod.currentScheduleIntentText(day, new Date(2026, 5, 26, 1, 30));
if (value !== "居家") {
  throw new Error(`凌晨延续昨日记录不应显示外出：${value}`);
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_realtime_schedule_intent_prefers_home_before_first_item_at_dawn(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = self._dashboard_dom_mock_script() + """
const mod = await import("./pages/dashboard/app.js");
const day = {
  date: "2026-06-26",
  extended_night: false,
  timeline: [
    { time: "09:20", activity: "开始今天的安排", status: "清醒" },
    { time: "15:10", activity: "下午活动", status: "活跃" },
  ],
  state: {
    energy: 80,
    outgoing: 95,
    social: 30,
    busyness: 10,
    focus: 20,
    interaction_capacity: 30,
    sleepiness: 20,
    sleep: { depth: "awake" },
  },
};
const value = mod.currentScheduleIntentText(day, new Date(2026, 5, 26, 1, 30));
if (value !== "居家") {
  throw new Error(`凌晨当天日程未开始不应显示外出：${value}`);
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_current_panel_does_not_show_yesterday_last_item_at_dawn(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = self._dashboard_dom_mock_script() + """
const mod = await import("./pages/dashboard/app.js");
const day = {
  date: "2026-06-25",
  extended_night: true,
  timeline: [
    { time: "18:20", activity: "回到家换居家服", status: "放松" },
    { time: "20:50", activity: "洗完澡准备睡前放松", status: "困倦" },
  ],
};
const displayPair = mod.currentTimelinePair(day, new Date(2026, 5, 26, 1, 30), { carryExtendedNight: false });
if (displayPair.current !== null) {
  throw new Error(`当前面板不应显示昨天最后一项：${displayPair.current.activity}`);
}
const intentPair = mod.currentTimelinePair(day, new Date(2026, 5, 26, 1, 30));
if (!intentPair.current || intentPair.current.time !== "20:50") {
  throw new Error("活动倾向仍需要能读取延续昨日的时间轴位置");
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_display_text_uses_middle_dot_separator(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        files = [
            root / "core" / "life" / "surroundings.py",
            root / "core" / "runtime" / "inject.py",
            root / "core" / "life" / "outfit.py",
            root / "core" / "interface" / "preferences.py",
            root / "core" / "interface" / "display.py",
            root / "core" / "interface" / "view.py",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files)

        self.assertNotIn('" / ".join', combined)
        self.assertNotIn("' / '.join", combined)
        self.assertNotIn('f" / ', combined)
        self.assertNotIn(' / 权重', combined)
        self.assertIn('" · ".join', combined)

    def test_runtime_logs_translate_internal_enum_values(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        service = (root / "core" / "runtime" / "live.py").read_text(encoding="utf-8")
        outfit = (root / "core" / "life" / "outfit.py").read_text(encoding="utf-8")

        self.assertIn("page_status_reason_label(reason)", service)
        self.assertIn("时间标签「{get_time_period_cn(target_period)}」", outfit)
        self.assertNotIn("原因={reason}", service)
        self.assertNotIn("时间标签={target_period}", outfit)

    def test_dashboard_hides_duplicate_life_episode_generation_evidence(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        app = (root / "app.js").read_text(encoding="utf-8")
        display = (root / "shared" / "display.js").read_text(encoding="utf-8")

        self.assertIn("function visibleExperienceEvidence(evidence, episodes)", display)
        self.assertIn('targetType === "life_episode"', display)
        self.assertIn('evidenceType === "daily_generation"', display)
        self.assertIn("episodeIds.has(targetId)", display)
        self.assertIn("visibleEvidence.slice(0, 3).forEach", app)
