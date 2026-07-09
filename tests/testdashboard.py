import asyncio
import base64
import datetime
import hashlib
import json
import subprocess
import tempfile
import types
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from support import (
    BehaviorFeedbackRecord,
    DailyLifeDashboardMixin,
    DataManager,
    EmotionArcRecord,
    FocusSlotRecord,
    FocusTargetRecord,
    GroupEnvironmentRecord,
    LifeDecisionRecord,
    LifeEpisodeRecord,
    LifeSettings,
    LifeTermRecord,
    LongTermMemoryRecord,
    MemoryBoundaryRecord,
    MemoryCorrectionRecord,
    MemoryEvidenceRecord,
    DayRecord,
    EventRecord,
    LifeState,
    PhysiologicalRhythmLogRecord,
    PlaceRecord,
    RelationshipNote,
    RelationshipRecord,
    TimelineItem,
    WeekPlanRecord,
)
from core.models import ActionDecisionRecord, EmojiAssetRecord, MessageVisibilityRecord


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
            generated=True,
        )

    async def generate_daily(self, date=None, force=False, target_hour=None, extra=None, web_inspiration=""):
        self.daily_calls.append((date, force, target_hour, extra, web_inspiration))
        day = DayRecord(
            date=date.strftime("%Y-%m-%d"),
            outfit="浅蓝外套",
            timeline=[TimelineItem(time="10:00", activity="在窗边写手帐", status="平静")],
        )
        await self.archive.save_day(day)
        return day

    async def generate_week_plan(self, goals="", web_inspiration=""):
        self.week_calls.append((goals, web_inspiration))
        return WeekPlanRecord(
            week_id="2026-W23",
            theme="新周计划",
            goals=[goals or "按日常节奏"],
            generated=True,
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

    async def cleanup_emoji_asset_cache(self):
        return 0

    def _emoji_max_bytes(self):
        max_mb = max(1.0, min(float(self.config.emoji.max_size_mb), 20.0))
        return int(max_mb * 1024 * 1024)

    def _emoji_asset_cache_dir(self, *, create=True):
        cache_dir = self.data_path.parent / "emoji"
        if create:
            cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    @staticmethod
    def _emoji_asset_is_remote(path_text):
        return str(path_text or "").startswith(("http://", "https://"))

    def _resolve_cached_emoji_path(self, path_text, cache_dir):
        if not path_text or self._emoji_asset_is_remote(path_text):
            return None
        try:
            resolved = Path(path_text).expanduser().resolve()
            resolved.relative_to(cache_dir.resolve())
        except (OSError, RuntimeError, ValueError):
            return None
        return resolved

    async def _cache_emoji_asset_path(self, payload, fingerprint):
        source = str((payload or {}).get("image") or "").strip()
        if not source.startswith("data:image/") or ";base64," not in source:
            return None
        header, encoded = source.split(",", 1)
        mime = header[5:].split(";", 1)[0].strip().lower()
        suffix = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
        }.get(mime, ".png")
        data = base64.b64decode(encoded, validate=True)
        target = self._emoji_asset_cache_dir() / f"{fingerprint}{suffix}"
        target.write_bytes(data)
        return target


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
                    physiological_rhythm=LifeState.from_value(
                        {
                            "physiological_rhythm": {
                                "energy_curve": "上午平稳，下午慢慢回落",
                                "body_condition": {"label": "轻微疲惫", "intensity": 28, "source": "每日生成"},
                                "recovery_actions": ["少量散步", "早点收尾"],
                                "social_battery": 42,
                                "attention_state": "低刺激更舒服",
                                "summary": "适合低强度恢复",
                            }
                        }
                    ).physiological_rhythm,
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
        await self.plugin.runtime.archive.save_day(
            DayRecord(
                date="2026-06-10",
                outfit="米白针织衫和浅灰长裙",
                timeline=[
                    TimelineItem(time="09:40", activity="在家慢慢整理桌面", status="安静"),
                    TimelineItem(time="21:30", activity="洗漱后提前收尾休息", status="放松"),
                ],
                places=[PlaceRecord(name="家里", type="home", hint="低强度恢复")],
                meta={
                    "theme": "低强度整理",
                    "schedule_type": "宅家充电的慵懒一日",
                    "schedule_intent": "rest",
                    "life_mode": "resting",
                    "outfit_style_pool": "outfit_styles",
                    "mood": "薄荷绿·安稳",
                },
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
        await self.plugin.runtime.archive.save_emotion_arc(
            EmotionArcRecord(
                date="2026-06-11",
                label="轻松但想慢一点",
                valence=48,
                arousal=35,
                intensity=62,
                stability=76,
                trigger="睡眠债偏高但状态不差",
                evidence="当前心情轻松，体力只恢复到中等",
                influence="安排上保留低强度和少量外出",
                expires_at="2099-01-01 00:00:00",
                source="state",
            )
        )
        await self.plugin.runtime.archive.save_physiological_rhythm_log(
            PhysiologicalRhythmLogRecord(
                date="2026-06-11",
                source="state",
                energy_curve="上午平稳，下午慢慢回落",
                body_label="轻微疲惫",
                body_intensity=28,
                recovery_actions=["少量散步", "早点收尾"],
                social_battery=42,
                attention_state="低刺激更舒服",
                summary="适合低强度恢复",
                lifecycle_kind="short_term",
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
        await self.plugin.runtime.archive.upsert_focus_slot(
            FocusSlotRecord(
                scope="",
                focus_key="early_sleep",
                label="早睡恢复",
                priority=80,
                reason="这几天让她多休息",
            )
        )
        await self.plugin.runtime.archive.save_memory_correction(
            MemoryCorrectionRecord(
                target_type="life_episode",
                target_id="outing_loop",
                correction="最近别总写出门，优先保留恢复节奏。",
                evidence="用户纠偏",
                confidence=0.9,
            )
        )
        await self.plugin.runtime.archive.upsert_life_term(
            LifeTermRecord(term="蹲后续", meaning="暂时围观同一话题后续", last_seen="2026-06-11")
        )
        await self.plugin.runtime.archive.upsert_long_term_memory(
            LongTermMemoryRecord(
                scope="",
                category="preference:life",
                title="安静恢复",
                content="她偏好安静恢复，不适合连续安排高强度外出。",
                source_table="preferences",
                source_id="1",
                date="2026-06-10",
            )
        )
        await self.plugin.runtime.archive.upsert_long_term_memory(
            LongTermMemoryRecord(
                scope="",
                category="short_term",
                title="早睡恢复",
                content="这几天让她多休息，减少重复外出。",
                source_table="focus_slots",
                source_id="early_sleep",
                date="2026-06-11",
            )
        )
        await self.plugin.runtime.archive.set_memory_boundary(
            MemoryBoundaryRecord(source_scope="group:1", target_scope="private:1", policy="ask", reason="跨域谨慎引用")
        )
        saved_decision = await self.plugin.runtime.archive.save_life_decision(
            LifeDecisionRecord(
                date="2026-06-11",
                kind="daily_plan",
                subject="2026-06-11",
                decision="低体力恢复日",
                reason="睡眠债偏高，延续慢节奏但减少外出重复。",
                evidence="短期目标：早睡恢复；近期生活惯性：低强度整理。",
                outcome="宅家整理、少量咖啡店写稿。",
            )
        )
        await self.plugin.runtime.archive.save_memory_evidence(
            MemoryEvidenceRecord(
                target_type="life_decision",
                target_id=str(saved_decision.id),
                evidence_type="decision",
                source_table="life_decisions",
                source_id=str(saved_decision.id),
                date="2026-06-11",
                summary="短期目标早睡恢复和低强度惯性共同影响今日决策。",
            )
        )
        await self.plugin.runtime.archive.save_memory_evidence(
            MemoryEvidenceRecord(
                target_type="focus",
                target_id="early_sleep",
                evidence_type="decision",
                source_table="life_decisions",
                source_id=str(saved_decision.id),
                date="2026-06-11",
                summary="早睡恢复已参与今日生活决策。",
            )
        )

    async def test_registers_page_routes(self):
        self.plugin._register_page_web_apis()
        paths = [item[0] for item in self.plugin.context.routes]

        self.assertIn("/astrbot_plugin_daily_life/page/status", paths)
        self.assertNotIn("/astrbot_plugin_daily_life/page/template/create", paths)
        self.assertNotIn("/astrbot_plugin_daily_life/page/template/save", paths)
        self.assertNotIn("/astrbot_plugin_daily_life/page/catalog/create", paths)
        self.assertNotIn("/astrbot_plugin_daily_life/page/hair/create", paths)
        self.assertNotIn("/astrbot_plugin_daily_life/page/workshop/expand", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/timeline/save", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/action/generate-week", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/config", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/config/character-reference", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/config/character-reference/preview", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/config/character-reference/delete", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/emoji/list", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/emoji/import", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/emoji/preview", paths)
        self.assertNotIn("/astrbot_plugin_daily_life/page/emoji/maintain", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/emoji/delete", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/emoji/sendable", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/emoji/backup", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/emoji/restore", paths)
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
        self.assertEqual(saved_path.parent, self.plugin.runtime.data_path.parent / "references")
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

    async def test_emoji_management_lists_previews_toggles_and_deletes_assets(self):
        cache_dir = self.plugin.runtime._emoji_asset_cache_dir()
        emoji_path = cache_dir / "emoji-one.png"
        emoji_path.write_bytes(b"\x89PNG\r\n\x1a\nemoji")
        asset = await self.plugin.runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="emoji-one",
                file_path=str(emoji_path),
                label="探头",
                description="适合轻轻围观",
                emotions=["好奇", "围观"],
                source_kind="review",
                asset_type="sticker",
                confidence=0.92,
                sendable=True,
                status="ready",
            )
        )

        listed = await self.plugin.page_emoji_list()
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["data"]["stats"]["total"], 1)
        self.assertEqual(listed["data"]["stats"]["review"], 1)
        self.assertTrue(listed["data"]["items"][0]["is_cached"])
        self.assertTrue(listed["data"]["items"][0]["preview_available"])

        self.plugin.body = {"id": asset.id}
        preview = await self.plugin.page_emoji_preview()
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["data"]["data_url"].startswith("data:image/png;base64,"))

        self.plugin.body = {"id": asset.id, "sendable": False}
        toggled = await self.plugin.page_emoji_sendable()
        self.assertTrue(toggled["ok"])
        self.assertFalse(toggled["data"]["item"]["sendable"])

        self.plugin.body = {"id": asset.id}
        deleted = await self.plugin.page_emoji_delete()
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["data"]["deleted_records"], 1)
        self.assertEqual(deleted["data"]["deleted_files"], 1)
        self.assertFalse(emoji_path.exists())
        self.assertEqual(deleted["data"]["stats"]["total"], 0)

    async def test_emoji_import_upload_caches_asset_and_lists_manual_source(self):
        image_bytes = b"\x89PNG\r\n\x1a\nmanual-emoji"
        payload = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
        digest = hashlib.sha256(image_bytes).hexdigest()
        self.plugin.body = {"image": payload, "filename": "手动导入.png"}

        result = await self.plugin.page_emoji_import()

        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["imported"])
        item = result["data"]["item"]
        saved_path = Path(item["file_path"])
        self.assertEqual(saved_path.parent, self.plugin.runtime.data_path.parent / "emoji")
        self.assertEqual(saved_path.name, f"{digest}.png")
        self.assertEqual(saved_path.read_bytes(), image_bytes)
        self.assertEqual(item["source_kind"], "manual")
        self.assertEqual(item["label"], "手动导入")
        self.assertEqual(item["status"], "pending")
        self.assertFalse(item["sendable"])
        self.assertEqual(result["data"]["stats"]["manual"], 1)
        self.assertTrue(result["data"]["items"][0]["preview_available"])

        self.plugin.body = {"id": item["id"]}
        preview = await self.plugin.page_emoji_preview()
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["data"]["data_url"].startswith("data:image/png;base64,"))

    async def test_emoji_import_accepts_large_gif_when_limit_is_raised(self):
        self.plugin.runtime.config = LifeSettings.from_dict({"emoji_config": {"max_size_mb": 20}})
        image_bytes = b"GIF89a" + b"\x00" * (5 * 1024 * 1024 + 16)
        payload = "data:image/gif;base64," + base64.b64encode(image_bytes).decode("ascii")
        digest = hashlib.sha256(image_bytes).hexdigest()
        self.plugin.body = {"image": payload, "filename": "大一点的动图.gif"}

        result = await self.plugin.page_emoji_import()

        self.assertTrue(result["ok"])
        item = result["data"]["item"]
        self.assertTrue(item["is_animated"])
        saved_path = Path(item["file_path"])
        self.assertEqual(saved_path.name, f"{digest}.gif")
        self.assertEqual(saved_path.read_bytes(), image_bytes)

        self.plugin.body = {"id": item["id"]}
        preview = await self.plugin.page_emoji_preview()
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["data"]["data_url"].startswith("data:image/gif;base64,"))

        self.plugin.body = {"id": item["id"], "still": True}
        still_preview = await self.plugin.page_emoji_preview()
        self.assertTrue(still_preview["ok"])
        self.assertTrue(still_preview["data"]["still"])
        self.assertFalse(still_preview["data"]["data_url"].startswith("data:image/gif;base64,"))

    async def test_emoji_import_rejects_large_gif_by_default(self):
        image_bytes = b"GIF89a" + b"\x00" * (5 * 1024 * 1024 + 16)
        payload = "data:image/gif;base64," + base64.b64encode(image_bytes).decode("ascii")
        self.plugin.body = {"image": payload, "filename": "默认超限动图.gif"}

        result = await self.plugin.page_emoji_import()

        self.assertFalse(result["ok"])
        self.assertIn("图片不能超过 5 MB", result["error"]["message"])

    async def test_emoji_import_rejects_url_payload(self):
        self.plugin.body = {"url": "https://example.com/emoji.png"}

        result = await self.plugin.page_emoji_import()

        self.assertFalse(result["ok"])
        self.assertIn("请选择图片文件", result["error"]["message"])

    async def test_emoji_preview_rejects_external_local_path(self):
        external = self.plugin.runtime.data_path.parent / "outside.png"
        external.write_bytes(b"outside")
        asset = await self.plugin.runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="outside",
                file_path=str(external),
                label="外部图片",
                source_kind="review",
                status="ready",
            )
        )

        self.plugin.body = {"id": asset.id}
        result = await self.plugin.page_emoji_preview()

        self.assertFalse(result["ok"])

    async def test_emoji_management_deletes_selected_assets_in_batch(self):
        cache_dir = self.plugin.runtime._emoji_asset_cache_dir()
        first_path = cache_dir / "emoji-batch-one.png"
        second_path = cache_dir / "emoji-batch-two.png"
        first_path.write_bytes(b"\x89PNG\r\n\x1a\none")
        second_path.write_bytes(b"\x89PNG\r\n\x1a\ntwo")
        first = await self.plugin.runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="emoji-batch-one",
                file_path=str(first_path),
                label="批量一",
                source_kind="review",
                status="ready",
            )
        )
        second = await self.plugin.runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="emoji-batch-two",
                file_path=str(second_path),
                label="批量二",
                source_kind="review",
                status="ready",
            )
        )

        self.plugin.body = {"ids": [first.id, second.id]}
        deleted = await self.plugin.page_emoji_delete()

        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["data"]["deleted_records"], 2)
        self.assertEqual(deleted["data"]["deleted_files"], 2)
        self.assertFalse(first_path.exists())
        self.assertFalse(second_path.exists())
        self.assertEqual(deleted["data"]["stats"]["total"], 0)

    async def test_emoji_management_toggles_selected_assets_in_batch(self):
        cache_dir = self.plugin.runtime._emoji_asset_cache_dir()
        first_path = cache_dir / "emoji-batch-enable-one.png"
        second_path = cache_dir / "emoji-batch-enable-two.png"
        first_path.write_bytes(b"\x89PNG\r\n\x1a\none")
        second_path.write_bytes(b"\x89PNG\r\n\x1a\ntwo")
        first = await self.plugin.runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="emoji-batch-enable-one",
                file_path=str(first_path),
                label="批量启停一",
                source_kind="review",
                sendable=False,
                status="ready",
            )
        )
        second = await self.plugin.runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="emoji-batch-enable-two",
                file_path=str(second_path),
                label="批量启停二",
                source_kind="review",
                sendable=False,
                status="ready",
            )
        )

        self.plugin.body = {"ids": [first.id, second.id], "sendable": True}
        enabled = await self.plugin.page_emoji_sendable()

        self.assertTrue(enabled["ok"])
        self.assertEqual(enabled["data"]["updated_records"], 2)
        self.assertIsNone(enabled["data"]["item"])
        self.assertEqual(enabled["data"]["stats"]["sendable"], 2)
        self.assertTrue(all(item["sendable"] for item in enabled["data"]["items"]))

        self.plugin.body = {"ids": [first.id, second.id], "sendable": False}
        disabled = await self.plugin.page_emoji_sendable()

        self.assertTrue(disabled["ok"])
        self.assertEqual(disabled["data"]["updated_records"], 2)
        self.assertEqual(disabled["data"]["stats"]["sendable"], 0)
        self.assertFalse(any(item["sendable"] for item in disabled["data"]["items"]))

    async def test_emoji_backup_exports_zip_and_restore_merges_assets(self):
        cache_dir = self.plugin.runtime._emoji_asset_cache_dir()
        image_bytes = b"\x89PNG\r\n\x1a\nbackup-emoji"
        digest = hashlib.sha256(image_bytes).hexdigest()
        emoji_path = cache_dir / f"{digest}.png"
        emoji_path.write_bytes(image_bytes)
        emoji_path.with_name(f"{emoji_path.stem}.still.png").write_bytes(b"preview-cache")
        await self.plugin.runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash=digest,
                file_path=str(emoji_path),
                label="备份测试",
                description="用于测试表情备份还原",
                emotions=["测试", "还原"],
                source_kind="manual",
                asset_type="emoji",
                confidence=0.88,
                sendable=True,
                status="ready",
            )
        )

        backup = await self.plugin.page_emoji_backup()

        self.assertTrue(backup["ok"])
        self.assertEqual(backup["data"]["count"], 1)
        self.assertEqual(backup["data"]["files"], 1)
        self.assertTrue(backup["data"]["filename"].endswith(".zip"))
        encoded = backup["data"]["data_url"].split(",", 1)[1]
        archive_bytes = base64.b64decode(encoded)
        with zipfile.ZipFile(BytesIO(archive_bytes)) as package:
            names = package.namelist()
            self.assertIn("manifest.json", names)
            self.assertFalse(any(name.endswith(".still.png") for name in names))
            manifest = json.loads(package.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["format"], "daily_life_emoji_backup")
            item = manifest["items"][0]
            self.assertTrue(item["backup_asset"].startswith("assets/"))
            self.assertEqual(package.read(item["backup_asset"]), image_bytes)

        restored_plugin = PagePlugin()
        restored_plugin.body = {
            "archive": backup["data"]["data_url"],
            "filename": backup["data"]["filename"],
        }
        restored = await restored_plugin.page_emoji_restore()

        self.assertTrue(restored["ok"])
        self.assertEqual(restored["data"]["restored"], 1)
        self.assertEqual(restored["data"]["stats"]["total"], 1)
        item = restored["data"]["items"][0]
        restored_path = Path(item["file_path"])
        self.assertEqual(restored_path.parent, restored_plugin.runtime.data_path.parent / "emoji")
        self.assertEqual(restored_path.read_bytes(), image_bytes)
        self.assertEqual(item["label"], "备份测试")
        self.assertEqual(item["emotions"], ["测试", "还原"])
        self.assertTrue(item["sendable"])
        self.assertEqual(item["status"], "ready")

        merged_plugin = PagePlugin()
        await merged_plugin.runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash=digest,
                label="已有识图",
                description="本地已有更完整说明",
                emotions=["已有"],
                source_kind="review",
                confidence=0.97,
                sendable=False,
                status="ready",
            )
        )
        merged_plugin.body = {
            "archive": backup["data"]["data_url"],
            "filename": backup["data"]["filename"],
        }
        merged = await merged_plugin.page_emoji_restore()

        self.assertTrue(merged["ok"])
        item = merged["data"]["items"][0]
        self.assertEqual(item["label"], "已有识图")
        self.assertEqual(item["description"], "本地已有更完整说明")
        self.assertEqual(item["emotions"], ["已有"])
        self.assertFalse(item["sendable"])
        self.assertEqual(Path(item["file_path"]).read_bytes(), image_bytes)

    async def test_build_page_status_returns_current_life_world_without_workshop_data(self):
        today = datetime.datetime.now().date()
        await self.plugin.runtime.archive.upsert_focus_target(
            FocusTargetRecord(
                target_type="topic",
                target_id="过期高优先级目标",
                label="过期高优先级目标",
                priority=100,
                reason="历史目标不应挤进当前体验层",
                expires_at=(today - datetime.timedelta(days=1)).isoformat(),
            )
        )
        await self.plugin.runtime.archive.upsert_focus_target(
            FocusTargetRecord(
                target_type="topic",
                target_id="已停用目标",
                label="已停用目标",
                priority=95,
                reason="停用目标不应挤进当前体验层",
                enabled=False,
            )
        )
        status = await self.plugin._build_page_status()

        self.assertEqual(status["target_date"], "2026-06-11")
        self.assertEqual(status["day"]["outfit"], "浅蓝外套和白裙子")
        self.assertEqual(status["day"]["state"]["energy"], 52)
        self.assertEqual(status["day"]["state"]["mood_score"], 74)
        self.assertEqual(status["day"]["state"]["stress"], 28)
        self.assertEqual(status["day"]["state"]["interaction_capacity"], 58)
        rhythm = status["day"]["state"]["physiological_rhythm"]
        self.assertEqual(rhythm["energy_curve"], "上午平稳，下午慢慢回落")
        self.assertEqual(rhythm["body_condition"]["label"], "轻微疲惫")
        self.assertEqual(rhythm["social_battery"], 42)
        self.assertEqual(rhythm["attention_state"], "低刺激更舒服")
        self.assertEqual(status["day"]["state_log"][0], "09:20 起床后慢慢恢复")
        self.assertEqual(status["day"]["meta"]["sleep_debt"], "1.5")
        self.assertEqual(status["day"]["meta"]["energy_carryover"], "62")
        self.assertEqual(status["day"]["meta"]["mood"], "薄荷绿·治愈")
        self.assertEqual(status["day"]["meta"]["schedule_type"], "宅家充电的慵懒一日")
        self.assertEqual(status["day"]["meta"]["schedule_intent"], "rest")
        self.assertNotIn("memo", status["day"])
        self.assertEqual(status["memo"]["date"], "2026-06-11")
        self.assertEqual(status["memo"]["scope"], "target")
        self.assertEqual(status["memo"]["text"], "下午确认明天安排")
        self.assertEqual(status["memo"]["display_text"], "下午确认明天安排")
        self.assertEqual(status["memo"]["total"], 1)
        self.assertEqual(status["memo"]["items"], [
            {
                "date": "2026-06-11",
                "scope": "target",
                "text": "下午确认明天安排",
                "display_text": "下午确认明天安排",
            }
        ])
        self.assertNotIn("target", status["memo"])
        self.assertNotIn("tomorrow", status["memo"])
        self.assertNotIn("display_label", status["memo"])
        self.assertEqual(status["week_plan"]["theme"], "慢生活周")
        self.assertEqual(status["world"]["relationships"][0]["name"], "阿林")
        self.assertNotIn("life_decisions", status["world"])
        self.assertNotIn("life_decisions", status["lifecycle"])
        self.assertEqual(status["observatory"]["today_decision"]["decision"], "低体力恢复日")
        self.assertEqual(status["observatory"]["today_decision"]["source"], "autonomous_life")
        self.assertIn("早睡恢复已参与今日生活决策", " ".join(status["observatory"]["today_decision"]["influence_sources"]))
        self.assertNotIn("current_snapshot", status["observatory"])
        self.assertNotIn("proactive_state", status["observatory"])
        self.assertNotIn("execution_review", status["observatory"])
        self.assertNotIn("correction_lifecycle", status["observatory"])
        self.assertNotIn("repeat_guard", status["observatory"])
        self.assertNotIn("world_drivers", status["observatory"])
        self.assertNotIn("memory_influence", status["observatory"])
        self.assertNotIn("decision_influence_chain", status["observatory"])
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
        self.assertEqual(status["experience"]["emotion_arcs"][0]["label"], "轻松但想慢一点")
        self.assertIn("低强度", status["experience"]["emotion_arcs"][0]["influence"])
        self.assertEqual(status["experience"]["physiological_rhythm_logs"][0]["body_label"], "轻微疲惫")
        self.assertIn("平均身体负荷", status["experience"]["physiological_rhythm_trend"]["summary"])
        self.assertEqual(status["experience"]["focus_targets"][0]["label"], "早睡恢复")
        self.assertNotIn(
            "过期高优先级目标",
            [item["label"] for item in status["experience"]["focus_targets"]],
        )
        self.assertNotIn(
            "已停用目标",
            [item["label"] for item in status["experience"]["focus_targets"]],
        )
        self.assertEqual(status["experience"]["terms"][0]["term"], "蹲后续")
        self.assertTrue(status["experience"]["long_term_memories"])
        self.assertTrue(status["experience"]["memory_clusters"])
        self.assertTrue(status["experience"]["memory_entities"])
        self.assertTrue(status["experience"]["memory_conflicts"])
        self.assertTrue(status["observatory"]["today_decision"]["memory_sources"])
        self.assertGreater(status["experience"]["health"]["score"], 0)

    async def test_page_status_embeds_compact_today_decision_sources(self):
        long_reason = (
            "全天宅家，阴天闷热，睡裙穿得正舒服，没必要换。当前：10:30 把吃完的碗碟冲洗了，"
            "回房间找出那个半途而废的打卡本，翻开看了几页，又忍不住想今天的热搜。"
        )
        current = (await self.plugin.runtime.archive.get_life_decisions(limit=1, kind="daily_plan"))[0]
        for index, summary in enumerate([long_reason, long_reason, f"用户纠偏影响今日安排 {long_reason}"]):
            await self.plugin.runtime.archive.save_memory_evidence(
                MemoryEvidenceRecord(
                    target_type="focus",
                    target_id=f"today-source-{index}",
                    evidence_type="daily_generation",
                    source_table="life_decisions",
                    source_id=str(current.id),
                    date="2026-06-11",
                    summary=summary,
                )
            )

        status = await self.plugin._build_page_status()

        sources = status["observatory"]["today_decision"]["influence_sources"]
        self.assertLessEqual(len(sources), 2)
        self.assertEqual(len(sources), len(set(sources)))
        self.assertTrue(all(len(item) <= 80 for item in sources))
        self.assertNotIn("decision_influence_chain", status["observatory"])

    async def test_page_status_keeps_today_decision_when_other_decisions_are_newer(self):
        for index in range(25):
            await self.plugin.runtime.archive.save_life_decision(
                LifeDecisionRecord(
                    date="2026-06-11",
                    kind="outfit",
                    subject=f"穿搭判断 {index}",
                    decision=f"保持当前穿搭 {index}",
                    reason="聊天触发的穿搭巡检",
                )
            )

        status = await self.plugin._build_page_status()

        self.assertEqual(status["observatory"]["today_decision"]["kind"], "daily_plan")
        self.assertEqual(status["observatory"]["today_decision"]["decision"], "低体力恢复日")

    async def test_page_status_hides_same_day_prefix_in_today_decision(self):
        await self.plugin.runtime.archive.save_life_decision(
            LifeDecisionRecord(
                date="2026-06-11",
                kind="daily_plan",
                subject="2026-06-11",
                decision="宅家恢复日",
                reason="2026-06-11 天气闷热，所以白天少出门。",
                evidence="2026-06-11 | 今天体力偏低；2026-06-10 | 昨天晚睡；2026-06-11，晚上补一点轻活动。",
                outcome="2026-06-11：傍晚再短暂出门透气。",
            )
        )

        status = await self.plugin._build_page_status()
        decision = status["observatory"]["today_decision"]

        self.assertEqual(decision["reason"], "天气闷热，所以白天少出门。")
        self.assertEqual(decision["evidence"], "今天体力偏低；2026-06-10 | 昨天晚睡；晚上补一点轻活动。")
        self.assertEqual(decision["outcome"], "傍晚再短暂出门透气。")

    async def test_page_status_returns_future_memo_carousel_when_current_day_has_none(self):
        day = await self.plugin.runtime.archive.get_day("2026-06-11")
        day.memo = ""
        await self.plugin.runtime.archive.save_day(day)
        await self.plugin.runtime.archive.save_day(DayRecord(date="2026-06-15", memo="- 下周看展"))
        await self.plugin.runtime.archive.save_day(DayRecord(date="2026-06-13", memo="- 周六取快递\n- 晚上确认车票"))

        status = await self.plugin._build_page_status()

        self.assertEqual(status["memo"]["date"], "2026-06-13")
        self.assertEqual(status["memo"]["scope"], "future")
        self.assertEqual(status["memo"]["text"], "- 周六取快递")
        self.assertEqual(status["memo"]["display_text"], "2026-06-13 - 周六取快递")
        self.assertEqual(status["memo"]["total"], 3)
        self.assertEqual(
            [item["display_text"] for item in status["memo"]["items"]],
            [
                "2026-06-13 - 周六取快递",
                "2026-06-13 - 晚上确认车票",
                "2026-06-15 - 下周看展",
            ],
        )
        self.assertNotIn("target", status["memo"])
        self.assertNotIn("tomorrow", status["memo"])
        self.assertNotIn("display_label", status["memo"])

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
        self.plugin.body = {"goals": "轻松恢复", "use_web": True}

        result = await self.plugin.page_generate_week()

        self.assertTrue(result["ok"])
        self.assertEqual(self.plugin.runtime.composer.week_calls[0][0], "轻松恢复")
        self.assertIn("联网参考：轻松恢复", self.plugin.runtime.composer.week_calls[0][1])
        self.assertEqual(self.plugin.runtime.composer.web_calls[0][0], "轻松恢复")
        self.assertEqual(self.plugin.runtime.composer.web_calls[0][2]["category"], "周计划")
        self.assertEqual(self.plugin.runtime.composer.daily_calls, [])


class DailyLifeDashboardStaticTest(unittest.TestCase):
    @staticmethod
    def _dashboard_style(root):
        import re

        dashboard = root if (root / "style.css").exists() else root / "pages" / "dashboard"
        entry = dashboard / "style.css"
        body = entry.read_text(encoding="utf-8")
        pieces = [body]
        for import_path in re.findall(r'@import url\("\./([^"?]+)', body):
            pieces.append((dashboard / import_path).read_text(encoding="utf-8"))
        return "\n".join(pieces)

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
        for name in ("foundation.css", "selects.css", "effects.css", "shell.css", "dashboard.css", "emoji.css", "settings.css", "responsive.css", "dark.css"):
            self.assertTrue((root / "styles" / name).exists())
        self.assertTrue((root / "ui" / "effects.js").exists())
        self.assertTrue((root / "ui" / "selects.js").exists())

    def test_dashboard_reference_preview_assets_use_cache_buster(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        css = (root / "style.css").read_text(encoding="utf-8")

        self.assertIn("style.css?v=20260709-life-sensing", html)
        self.assertIn("foundation.css?v=20260709-no-field-type", css)
        self.assertIn("shell.css?v=20260709-system-chillround", css)
        self.assertIn("dashboard.css?v=20260709-title-dot", css)
        self.assertIn("settings.css?v=20260709-life-sensing", css)
        self.assertIn("responsive.css?v=20260709-mobile-facts-fill", css)
        self.assertIn("dark.css?v=20260709-dark-readable", css)
        self.assertIn("app.js?v=20260709-life-generation", html)
        self.assertIn('./ui/config.js?v=20260709-life-generation', app)
        self.assertIn('./ui/effects.js?v=20260709-life-effects', app)
        self.assertIn('./ui/selects.js?v=20260709-life-settings-fast', app)

    def test_dashboard_prefers_installed_chill_round_font(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn('--life-ui-font: "ChillRoundM", var(--life-system-font);', style)
        self.assertIn("font: 14px/1.62 var(--life-ui-font);", style)
        self.assertIn("--diary-font-body: var(--life-ui-font);", style)
        self.assertIn("--diary-font-display: var(--life-ui-font);", style)
        self.assertNotIn("@font-face", style)
        self.assertNotIn("ChillRoundM.ttf", style)
        self.assertNotIn('rel="preload"', html)

    def test_dashboard_selects_use_life_dropdown_controls(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        style = self._dashboard_style(root)
        selects = (root / "ui" / "selects.js").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")

        self.assertIn(".life-select-trigger", style)
        self.assertIn(".life-select-menu", style)
        self.assertIn(".life-select-option.is-selected", style)
        self.assertIn(".life-native-select", style)
        self.assertIn("createLifeSelectControls", selects)
        self.assertNotIn("MutationObserver", selects)
        self.assertNotIn("documentObserver", selects)
        self.assertIn("refresh(scope = root)", selects)
        self.assertIn("syncExisting(scope = null)", selects)
        self.assertIn("scopeContains(scope, select)", selects)
        self.assertIn("syncSelectControls", app)
        self.assertIn("configLoading: false", app)
        self.assertIn("deferConfigLoadForSettings", app)
        self.assertIn("loadConfig({ quiet: true, busy: false })", app)
        self.assertIn("syncSelectControls", (root / "ui" / "config.js").read_text(encoding="utf-8"))
        self.assertIn("lifeSelectControls.init()", app)

    def test_page_status_world_sections_use_twenty_item_limit(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        view = (root / "core" / "interface" / "view.py").read_text(encoding="utf-8")

        self.assertIn("PAGE_WORLD_RECORD_LIMIT = 20", view)
        self.assertIn("get_recent_relationships(PAGE_WORLD_RECORD_LIMIT)", view)
        self.assertIn("get_recent_places(PAGE_WORLD_RECORD_LIMIT)", view)
        self.assertIn("get_recent_events(PAGE_WORLD_RECORD_LIMIT)", view)
        self.assertIn("get_recent_chat_summaries(PAGE_WORLD_RECORD_LIMIT)", view)
        self.assertIn("get_recent_group_environments(PAGE_WORLD_RECORD_LIMIT)", view)
        self.assertIn("get_message_visibility_records(PAGE_WORLD_RECORD_LIMIT)", view)
        self.assertIn("limit=PAGE_WORLD_RECORD_LIMIT", view)
        self.assertNotIn("get_recent_relationships(8)", view)
        self.assertNotIn("get_recent_places(12)", view)
        self.assertNotIn("get_recent_events(12)", view)
        self.assertNotIn("get_recent_chat_summaries(8)", view)
        self.assertNotIn("get_recent_group_environments(8)", view)

    def test_dashboard_dom_entrypoints_exist(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")

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
        self.assertIn('memoryTabs: all("[data-memory-tab]")', app)
        self.assertIn('memoryPanels: all("[data-memory-panel]")', app)
        self.assertIn('worldTabs: all("[data-world-tab]")', app)
        self.assertIn('experienceTabs: all("[data-experience-tab]")', app)
        self.assertIn('actionGroups: all("[data-action-view]")', app)
        self.assertNotIn("memoryDrawer", app)
        for name in ("workshop.js", "template.js", "catalog.js", "hair.js"):
            self.assertFalse((root / "ui" / name).exists())

    def test_dashboard_has_daily_life_effects(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        effects = (root / "ui" / "effects.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn('id="lifeDriftLayer"', html)
        self.assertIn('id="cursorTrailLayer"', html)
        self.assertIn('lifeDriftLayer: byId("lifeDriftLayer")', app)
        self.assertIn('cursorTrailLayer: byId("cursorTrailLayer")', app)
        self.assertIn("const dashboardEffects = createDashboardEffects({", app)
        self.assertIn("dashboardEffects.initLifeDrift();", app)
        self.assertIn("dashboardEffects.initCursorTrail();", app)
        self.assertIn("driftDesktopPieces = 32", effects)
        self.assertIn("driftKinds = [", effects)
        self.assertIn("cursorKinds = [", effects)
        self.assertIn('mediaMatches("(prefers-reduced-motion: reduce)")', effects)
        self.assertIn('mediaMatches("(pointer: fine)")', effects)
        self.assertIn('window.addEventListener("pointermove", handleCursorMove', effects)
        self.assertIn(".life-drift-layer", style)
        self.assertIn(".life-drift-piece", style)
        self.assertIn(".cursor-trail-layer", style)
        self.assertIn(".cursor-note", style)
        self.assertIn("--cursor-default:", style)
        self.assertIn("M5.2 26.8 8.1 18.9", style)
        self.assertIn("M4.9 27.1 8.3 18.6", style)
        self.assertIn("@media (pointer: fine)", style)
        self.assertIn("@media (prefers-reduced-motion: reduce)", style)
        self.assertIn("cursor: var(--cursor-default);", style)

    def test_dashboard_has_today_refresh_state_button(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

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

    def test_dashboard_uses_studio_layout(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn('class="studio"', html)
        self.assertIn('class="ribbonbar topbar dashboard-hero bento-hero"', html)
        self.assertIn('class="brand topbar-title hero-copy bento-hero-copy"', html)
        self.assertIn('id="heroEyebrow"', html)
        self.assertIn('id="heroTitle"', html)
        self.assertIn('id="heroSubtitle"', html)
        self.assertIn('class="bento-subtitle"', html)
        self.assertIn("const HERO_COPY = {", app)
        self.assertIn("emoji: {", app)
        self.assertIn('title: "表情管理"', app)
        self.assertIn("settings: {", app)
        self.assertIn('title: "运行规则"', app)
        self.assertIn("配置导航", html)
        self.assertIn("function renderHeroCopy(view = state.view)", app)
        self.assertIn("renderHeroCopy(state.view)", app)
        self.assertIn('class="view-switch hero-badges bento-ribbon"', html)
        self.assertIn('class="view-button bento-ribbon-button bento-ribbon-toggle dashboard-toggle active"', html)
        self.assertIn('class="view-button bento-ribbon-button bento-ribbon-toggle emoji-toggle"', html)
        self.assertIn('class="view-button bento-ribbon-button bento-ribbon-toggle settings-toggle"', html)
        self.assertIn('class="page-stickers"', html)
        self.assertIn('class="life-layout"', html)
        self.assertIn('class="panel today-panel"', html)
        self.assertIn('class="panel current-panel"', html)
        self.assertIn('class="panel timeline-panel"', html)
        self.assertIn('class="panel state-log-panel"', html)
        self.assertIn('class="life-column life-column-side"', html)
        self.assertIn('class="panel memory-panel"', html)
        self.assertIn('class="tabs memory-tabs"', html)
        self.assertIn('class="memory-page world-panel"', html)
        self.assertIn('class="memory-page experience-panel"', html)
        self.assertIn('class="memory-page lifecycle-panel"', html)
        self.assertNotIn('class="panel world-panel"', html)
        self.assertNotIn('class="panel experience-panel"', html)
        self.assertNotIn('class="panel lifecycle-panel"', html)
        self.assertNotIn('id="contextDetailDialog"', html)
        self.assertNotIn('id="contextDetailBody"', html)
        self.assertNotIn('id="contextDetailClose"', html)
        self.assertLess(
            html.index('class="panel timeline-panel"'),
            html.index('class="life-column life-column-side"'),
        )
        self.assertNotIn('id="contextDrawerToggle"', html)
        self.assertNotIn('id="contextDrawerScrim"', html)
        self.assertNotIn('id="contextDrawer"', html)
        self.assertNotIn('id="contextDrawerClose"', html)
        self.assertNotIn('data-context-tab=', html)
        self.assertNotIn('data-context-panel=', html)
        self.assertIn('data-memory-tab="world"', html)
        self.assertIn('data-memory-tab="experience"', html)
        self.assertIn('data-memory-tab="lifecycle"', html)
        self.assertIn('data-memory-panel="world"', html)
        self.assertIn('data-memory-panel="experience"', html)
        self.assertIn('data-memory-panel="lifecycle"', html)
        self.assertIn('data-world-tab="life_decisions"', html)
        self.assertIn('data-world-tab="relationships"', html)
        self.assertIn('data-experience-tab="relationships"', html)
        self.assertIn('data-experience-tab="language"', html)
        self.assertIn('worldTab: "life_decisions"', app)
        self.assertIn('experienceTab: "relationships"', app)
        self.assertIn('memoryTab: "world"', app)
        self.assertNotIn('contextDrawerOpen', app)
        self.assertNotIn('contextTab: "world"', app)
        self.assertIn("function renderWorld", app)
        self.assertIn("function renderMemoryPanel", app)
        self.assertIn("state.memoryTab = tab.dataset.memoryTab || \"world\";", app)
        self.assertIn("renderMemoryPanel();", app)
        self.assertNotIn("function contextSummaryRecord", app)
        self.assertNotIn("function renderContextRecordList", app)
        self.assertNotIn("function openContextDetail", app)
        self.assertNotIn("function closeContextDetail", app)
        self.assertNotIn("function renderContextDrawer", app)
        self.assertNotIn("function setContextDrawer", app)
        self.assertNotIn("function setContextTab", app)
        self.assertLess(html.index('class="panel today-panel"'), html.index('class="panel current-panel"'))
        self.assertLess(html.index('class="panel current-panel"'), html.index('class="panel timeline-panel"'))
        self.assertIn('class="life-column life-column-left"', html)
        self.assertIn('class="life-column life-column-main"', html)
        self.assertIn('class="life-column life-column-side"', html)
        self.assertIn(".life-column {\n  display: grid;", style)
        self.assertIn(".life-column-side", style)
        self.assertIn("minmax(280px, 0.86fr)", style)
        self.assertIn("minmax(460px, 1.25fr)", style)
        self.assertIn("minmax(300px, 0.92fr)", style)
        self.assertIn("function renderLifecycle", app)
        self.assertIn("function renderExperience", app)
        self.assertIn("state.worldTab = tab.dataset.worldTab;", app)
        self.assertIn("state.experienceTab = tab.dataset.experienceTab;", app)
        self.assertNotIn("setContextDrawer", app)
        self.assertNotIn("setContextTab", app)
        self.assertIn(".studio", style)
        self.assertIn(".ribbonbar", style)
        self.assertIn(".ribbonbar {\n  position: relative;", style)
        self.assertNotIn(".ribbonbar {\n  position: sticky;", style)
        self.assertIn(".bento-subtitle", style)
        self.assertIn(".view-switch.bento-ribbon", style)
        self.assertIn(".bento-ribbon .view-button", style)
        self.assertIn(".bento-ribbon .dashboard-toggle", style)
        self.assertIn(".bento-ribbon .emoji-toggle", style)
        self.assertIn(".bento-ribbon .settings-toggle", style)
        self.assertIn(".life-layout", style)
        self.assertIn(".memory-panel", style)
        self.assertIn(".memory-tabs", style)
        self.assertIn(".memory-page", style)
        self.assertIn(".memory-subtabs", style)
        self.assertIn(".world-panel", style)
        self.assertIn(".lifecycle-panel", style)
        self.assertIn(".experience-panel", style)
        self.assertNotIn(".context-layer", style)
        self.assertNotIn(".context-layer-grid", style)
        self.assertNotIn(".context-card", style)
        self.assertNotIn(".context-summary-record", style)
        self.assertNotIn(".context-summary-text", style)
        self.assertNotIn(".context-detail-card", style)
        self.assertNotIn(".context-detail-body", style)
        self.assertIn(".memory-page .record-body", style)
        self.assertIn("max-height: none;", style)
        self.assertIn("overflow: visible;", style)
        self.assertIn("grid-column: 1 / -1;", style)
        self.assertNotIn(".context-drawer-handle", style)
        self.assertNotIn(".context-drawer-scrim", style)
        self.assertNotIn(".context-drawer", style)
        self.assertNotIn(".context-drawer-panel", style)
        self.assertIn("scrollbar-gutter: stable", style)
        self.assertIn("border-color:", style)
        self.assertNotIn("desk-shell", html + style)
        self.assertNotIn("desk-rail", html + style)
        self.assertNotIn("desk-workspace", html + style)
        self.assertNotIn("daily-board", html + style)
        self.assertNotIn("status-column", html + style)
        self.assertNotIn("timeline-column", html + style)
        self.assertNotIn("memory-wall", html + style)
        self.assertNotIn("life-desk", html + style)
        self.assertNotIn("glance-stack", html + style)
        self.assertNotIn("main-stage", html + style)
        self.assertNotIn("memory-book", html + style)
        self.assertNotIn("life-card", html + style)
        self.assertNotIn("candy-house", html + style)
        self.assertNotIn("memoryDrawer", app)

    def test_dashboard_has_emoji_management_view(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)
        emoji_style = (root / "styles" / "emoji.css").read_text(encoding="utf-8")

        self.assertIn('data-view="emoji"', html)
        self.assertIn('id="emojiView"', html)
        self.assertNotIn('id="emojiRefreshButton"', html)
        self.assertNotIn("emojiRefreshButton", app)
        self.assertNotIn('id="emojiMaintainButton"', html)
        self.assertNotIn("emojiMaintainButton", app)
        self.assertIn('id="emojiDetailDialog"', html)
        self.assertIn('id="emojiDetailBody"', html)
        self.assertIn('id="emojiImportButton"', html)
        self.assertIn('id="emojiImportDialog"', html)
        self.assertIn('id="emojiImportFile"', html)
        self.assertIn('id="emojiBackupButton"', html)
        self.assertIn('id="emojiRestoreButton"', html)
        self.assertIn('id="emojiRestoreFile"', html)
        self.assertIn('id="emojiPager"', html)
        self.assertIn('id="emojiPrevPage"', html)
        self.assertIn('id="emojiPageInfo"', html)
        self.assertIn('id="emojiNextPage"', html)
        self.assertNotIn('id="emojiPageSize"', html)
        self.assertNotIn("表情每页数量", html)
        self.assertLess(html.index('id="emojiFilter"'), html.index('id="emojiPager"'))
        self.assertLess(html.index('id="emojiPager"'), html.index('id="emojiStats"'))
        self.assertNotIn('id="emojiImportUrl"', html)
        self.assertNotIn('id="emojiImportUrlButton"', html)
        self.assertIn('id="emojiManageButton"', html)
        self.assertIn('id="emojiCancelManageButton"', html)
        self.assertIn('id="emojiBulkEnableButton"', html)
        self.assertIn('id="emojiBulkDisableButton"', html)
        self.assertIn('id="emojiBulkDeleteButton"', html)
        self.assertNotIn('id="emojiSelectVisibleButton"', html)
        self.assertNotIn('id="emojiClearSelectionButton"', html)
        self.assertIn('class="emoji-layout"', html)
        self.assertIn('class="panel emoji-panel"', html)
        self.assertIn('class="panel-head emoji-head"', html)
        self.assertIn('class="emoji-tools"', html)
        self.assertIn('class="emoji-action-tools"', html)
        self.assertIn('class="emoji-filter-tools"', html)
        self.assertNotIn("sticker-vault", html)
        self.assertNotIn("vault-topbar", html)
        self.assertNotIn("vault-actions", html)
        self.assertNotIn("vault-filterbar", html)
        self.assertNotIn("vault-library", html)
        self.assertNotIn("emoji-studio", html)
        self.assertNotIn("emoji-command", html)
        self.assertNotIn("emoji-library", html)
        self.assertIn("表情管理", html)
        self.assertIn("导入表情", html)
        self.assertIn("手动导入", html)
        self.assertIn("已选 0 条", html)
        self.assertIn("启用选中", html)
        self.assertIn("停用选中", html)
        self.assertIn("删除选中", html)
        self.assertIn("取消", html)
        self.assertIn('emojiItems: []', app)
        self.assertIn("EMOJI_PAGE_SIZE = 30", app)
        self.assertNotIn("EMOJI_DEFAULT_PAGE_SIZE", app)
        self.assertIn("function emojiPageWindow", app)
        self.assertIn("function renderEmojiPager", app)
        self.assertIn("state.emojiPage = 1", app)
        self.assertIn("place-items: center;", emoji_style)
        self.assertIn("text-align: center;", emoji_style)
        self.assertIn("grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));", emoji_style)
        self.assertNotIn("grid-template-columns: repeat(auto-fill, minmax(146px, 1fr));", emoji_style)
        self.assertIn('apiGet("page/emoji/list"', app)
        self.assertIn('apiPost("page/emoji/import"', app)
        self.assertIn('apiPost("page/emoji/preview"', app)
        self.assertIn('apiPost("page/emoji/backup"', app)
        self.assertIn('apiPost("page/emoji/restore"', app)
        self.assertIn('loadEmojiPreview(preview, item.id, { still: true })', app)
        self.assertIn('loadEmojiPreview(preview, item.id, { still: false })', app)
        self.assertIn("function observeEmojiAnimatedPreview", app)
        self.assertIn("function scheduleEmojiAnimatedPreview", app)
        self.assertIn("IntersectionObserver", app)
        self.assertIn('loadEmojiPreview(img, id, { still: true })', app)
        self.assertIn('apiPost("page/emoji/preview", { id, still })', app)
        self.assertIn('apiPost("page/emoji/sendable"', app)
        self.assertIn('apiPost("page/emoji/delete"', app)
        self.assertIn("function confirmEmojiDelete", app)
        self.assertNotIn("window.confirm", app)
        self.assertIn("EMOJI_AUTO_REFRESH_MS", app)
        self.assertIn("function scheduleEmojiAutoRefresh", app)
        self.assertIn("function stopEmojiAutoRefresh", app)
        self.assertIn("loadEmojiAssets({ quiet: true })", app)
        self.assertIn("function emojiCompactMeta", app)
        self.assertIn("function emojiStatusMark", app)
        self.assertIn('return item.status === "ready" && item.sendable ? "✔️" : "❌";', app)
        self.assertIn("function renderEmojiDetailDialog", app)
        self.assertIn("function openEmojiDetail", app)
        self.assertIn("function openEmojiImport", app)
        self.assertIn("function importEmojiFiles", app)
        self.assertNotIn("function importEmojiUrl", app)
        self.assertNotIn("emojiImportUrl", app)
        self.assertIn("const EMOJI_IMPORT_MAX_MB = 20;", app)
        self.assertIn("const EMOJI_IMPORT_MAX_BYTES = EMOJI_IMPORT_MAX_MB * 1024 * 1024;", app)
        self.assertIn("const EMOJI_BACKUP_MAX_MB = 200;", app)
        self.assertIn("function backupEmojiAssets", app)
        self.assertIn("function restoreEmojiBackupFile", app)
        self.assertIn("个超过 ${EMOJI_IMPORT_MAX_MB} MB", app)
        self.assertIn("function beginEmojiManage", app)
        self.assertIn("function cancelEmojiManage", app)
        self.assertIn("function resetEmojiManageState", app)
        self.assertIn("function toggleEmojiSelected", app)
        self.assertIn("function setSelectedEmojiSendable", app)
        self.assertIn("function setEmojiBulkButton", app)
        self.assertIn("if (state.emojiManageMode)", app)
        self.assertIn("toggleEmojiSelected(emojiId)", app)
        self.assertIn("openEmojiDetail(emojiId)", app)
        self.assertNotIn('node("button", "", "详情")', app)
        self.assertNotIn("function selectVisibleEmoji", app)
        self.assertNotIn("function clearEmojiSelection", app)
        self.assertIn("function confirmEmojiBulkDelete", app)
        self.assertIn("emojiBulkEnableButton", app)
        self.assertIn("emojiBulkDisableButton", app)
        self.assertIn('apiPost("page/emoji/delete", { ids: targets })', app)
        self.assertIn(".emoji-record", style)
        self.assertIn(".emoji-thumb", style)
        self.assertIn(".emoji-record-title", style)
        self.assertIn("width: 28px;", style)
        self.assertIn("font-size: 11px;", style)
        self.assertIn(".emoji-select", style)
        self.assertIn(".emoji-record.is-selected", style)
        self.assertIn(".emoji-pager", style)
        self.assertNotIn(".emoji-page-size", style)
        self.assertIn(".emoji-manage-button.is-active", style)
        self.assertIn("[hidden] {\n  display: none !important;\n}", style)
        self.assertNotIn(".emoji-tools [hidden]", style)
        self.assertIn(".emoji-thumb:hover", style)
        self.assertIn(".emoji-layout", style)
        self.assertIn(".emoji-panel", style)
        self.assertIn(".emoji-head", style)
        self.assertIn(".emoji-tools", style)
        self.assertIn(".emoji-action-tools", style)
        self.assertIn(".emoji-filter-tools", style)
        self.assertIn("grid-template-columns: minmax(0, 1fr) max-content;", style)
        self.assertIn("grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));", style)
        self.assertIn("justify-content: stretch;", style)
        self.assertIn(".emoji-tools {\n    grid-template-columns: minmax(0, 1fr);", style)
        self.assertIn(".emoji-action-tools {\n    display: grid;", style)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr));", style)
        self.assertIn(".emoji-filter-tools {\n    display: grid;", style)
        self.assertIn("grid-template-columns: minmax(118px, 0.82fr) minmax(0, 1.18fr);", style)
        self.assertIn(".emoji-filter-tools .life-select-filter {\n    min-width: 0;", style)
        self.assertIn(".emoji-pager {\n    width: 100%;", style)
        self.assertIn("@media (max-width: 420px)", style)
        self.assertNotIn("repeat(auto-fit, minmax(78px, 96px))", style)
        self.assertIn("min-height: 50px;", style)
        self.assertIn("font-size: 12px;", style)
        self.assertIn("font-size: 21px;", style)
        self.assertNotIn(".sticker-vault", style)
        self.assertNotIn(".vault-topbar", style)
        self.assertNotIn(".vault-library", style)
        self.assertNotIn(".emoji-studio", style)
        self.assertNotIn(".emoji-command", style)
        self.assertNotIn(".emoji-library", style)
        self.assertIn(".emoji-detail-dialog", style)
        self.assertIn(".emoji-detail-card", style)
        self.assertIn(".emoji-import-card {\n  width: min(360px, calc(100vw - 28px));\n}", style)
        self.assertNotIn(".emoji-import-url", style)
        self.assertIn("grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));", style)
        self.assertIn("aspect-ratio: 1;", style)
        self.assertNotIn(".maintenance-field", style)
        self.assertIn(".danger.is-confirming", style)

    def test_dashboard_reset_today_button_stays_with_timeline_tools(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        timeline_tools_start = html.index('<div class="timeline-tools">')
        reset_button = html.index('id="resetDayButton"')
        add_button = html.index('id="timelineAddButton"')
        edit_button = html.index('id="timelineEditButton"')
        timeline_card_start = html.index('class="panel timeline-panel"')

        self.assertGreater(reset_button, timeline_tools_start)
        self.assertLess(reset_button, add_button)
        self.assertLess(add_button, edit_button)
        self.assertGreater(reset_button, timeline_card_start)
        self.assertIn('id="timelineAddButton" type="button" hidden', html)
        self.assertIn('id="timelineCancelButton" type="button" hidden', html)
        self.assertIn('id="timelineSaveButton" type="button" class="primary-button" hidden', html)
        self.assertIn("el.timelineEditButton.hidden = !hasDay || state.timelineEditing;", app)
        self.assertIn("el.timelineAddButton.hidden = !hasDay || !state.timelineEditing;", app)
        self.assertIn("el.timelineCancelButton.hidden = !hasDay || !state.timelineEditing;", app)
        self.assertIn("el.timelineSaveButton.hidden = !hasDay || !state.timelineEditing;", app)
        self.assertIn("grid-template-columns: minmax(126px, 0.24fr) minmax(0, 1fr);", style)
        self.assertIn("font-variant-numeric: tabular-nums;", style)
        self.assertIn(".timeline-edit-row::before {\n  content: none;\n}", style)
        self.assertNotIn("grid-template-columns: 96px minmax(0, 1fr);", style)

    def test_dashboard_dark_mode_keeps_text_readable(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        style = self._dashboard_style(root)

        self.assertIn('[data-theme="dark"] .emoji-detail-card', style)
        self.assertIn('[data-theme="dark"] .emoji-detail-card .record-line-value', style)
        self.assertIn('[data-theme="dark"] .timeline-item', style)
        self.assertIn('[data-theme="dark"] .memory-tabs', style)
        self.assertIn('[data-theme="dark"] .emoji-pager', style)
        self.assertIn("--life-dark-text: #fff6fb;", style)
        self.assertIn("--life-dark-card: rgba(255, 246, 251, 0.105);", style)

    def test_dashboard_groups_model_provider_settings(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        dashboard = root / "pages" / "dashboard"
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8-sig"))
        app = (dashboard / "app.js").read_text(encoding="utf-8")
        config = (dashboard / "ui" / "config.js").read_text(encoding="utf-8")
        style = self._dashboard_style(dashboard)

        self.assertIn('const MODEL_SECTION_KEY = "__model_provider_settings"', config)
        self.assertIn('description: "大语言模型"', config)
        self.assertIn("图片轻量润色", config)
        self.assertIn("function collectProviderConfigFields()", config)
        self.assertIn('spec._special === "select_provider"', config)
        self.assertIn("function configSectionVisibleFields", config)
        self.assertIn("schemaViewCache", config)
        self.assertIn("buildConfigSchemaView", config)
        self.assertIn("renderModelConfigField", config)
        self.assertIn("config-source-label", config)
        self.assertIn(".config-source-label", style)
        self.assertIn("const modelLabel = configLabel(field.fieldKey, field.spec);", config)
        self.assertNotIn("const sectionLabel = configLabel(field.sectionKey", config)
        self.assertNotIn("field-type", config)
        self.assertNotIn(".field-type", style)
        self.assertNotIn("configTypeLabel", config)
        self.assertEqual(
            schema["image_generation_config"]["items"]["prompt_rewrite_provider"]["_special"],
            "select_provider",
        )

    def test_dashboard_config_renders_template_list_fields(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        config = (root / "ui" / "config.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn('spec.type === "template_list"', config)
        self.assertIn("renderConfigTemplateList", config)
        self.assertIn("templateEntries", config)
        self.assertIn("normalizeTemplateListItem", config)
        self.assertIn("reorderItem", config)
        self.assertIn("pointerdown", config)
        self.assertIn("pointermove", config)
        self.assertIn("animateListFrom", config)
        self.assertIn("IMAGE_CHANNEL_LIST_PATHS", config)
        self.assertIn('"image_generation_config.text_channels"', config)
        self.assertIn('"image_generation_config.edit_channels"', config)
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
            '"weather_awareness"',
            '"state_config"',
            '"memory_config"',
            '"memos_config"',
            '"chat_style_config"',
            '"response_gate_config"',
            '"proactive_config"',
            '"voice_generation_config"',
            '"image_generation_config"',
            '"video_generation_config"',
            '"sight_config"',
            '"web_inspiration_config"',
            '"storage_config"',
            '"story_engine_config"',
        ]
        positions = [config.index(token) for token in expected_order]
        self.assertEqual(positions, sorted(positions))

    def test_dashboard_merges_sparse_settings_sections(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8-sig"))
        config = (root / "pages" / "dashboard" / "ui" / "config.js").read_text(encoding="utf-8")

        self.assertEqual(schema["rhythm_config"]["description"], "生活背景")
        self.assertEqual(schema["state_config"]["description"], "生活感知")
        self.assertEqual(schema["memory_config"]["description"], "记忆与关系")
        self.assertEqual(schema["chat_style_config"]["description"], "聊天表达")
        self.assertEqual(schema["video_generation_config"]["description"], "视频")
        self.assertIn("天气环境", schema["rhythm_config"]["hint"])
        self.assertIn("实时状态", schema["rhythm_config"]["hint"])
        self.assertIn("天气环境", schema["state_config"]["hint"])
        self.assertIn("用户称呼", schema["memory_config"]["hint"])
        self.assertIn("MemOS 外部记忆", schema["memory_config"]["hint"])
        self.assertIn("随心回复", schema["chat_style_config"]["hint"])
        self.assertIn("闲时回复", schema["chat_style_config"]["hint"])
        self.assertIn("视频理解", schema["video_generation_config"]["hint"])
        self.assertIn("const CONFIG_SECTION_DISPLAY_SECTIONS = new Map", config)
        self.assertIn('["weather_awareness", "rhythm_config"]', config)
        self.assertIn('["state_config", "rhythm_config"]', config)
        self.assertIn('["lifecycle_config", "rhythm_config"]', config)
        self.assertIn('["web_inspiration_config", "rhythm_config"]', config)
        self.assertIn('["relationship_aliases", "memory_config"]', config)
        self.assertIn('["bot_identity_aliases", "memory_config"]', config)
        self.assertIn('["commitment_config", "memory_config"]', config)
        self.assertIn('["memos_config", "memory_config"]', config)
        self.assertIn('["response_gate_config", "chat_style_config"]', config)
        self.assertIn('["proactive_config", "chat_style_config"]', config)
        self.assertIn('["sight_config", "video_generation_config"]', config)
        self.assertIn('["relationship_aliases", "identity_aliases"]', config)
        self.assertIn('["bot_identity_aliases", "identity_aliases"]', config)
        self.assertIn('const CONFIG_GROUPED_DISPLAY_SECTIONS = new Set(["rhythm_config", "memory_config", "chat_style_config", "video_generation_config"]);', config)
        self.assertIn('description: "基础生成"', config)
        self.assertIn('description: "天气环境"', config)
        self.assertIn('description: "实时状态"', config)
        self.assertIn('description: "生活演化"', config)
        self.assertIn('description: "称呼与身份"', config)
        self.assertIn('description: "MemOS 外部记忆"', config)
        self.assertIn('description: "短句节奏"', config)
        self.assertIn('description: "随心回复"', config)
        self.assertIn('description: "闲时回复"', config)
        self.assertIn('description: "视频生成"', config)
        self.assertIn('description: "视频理解"', config)
        self.assertIn("function renderConfigGroup(field)", config)
        self.assertIn("configSectionDisplaySection(sectionKey)", config)
        self.assertIn("isProviderConfigField(fieldSpec)", config)

    def test_dashboard_moves_sight_cache_settings_to_data_management(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8-sig"))
        config = (root / "pages" / "dashboard" / "ui" / "config.js").read_text(encoding="utf-8")

        self.assertEqual(schema["storage_config"]["description"], "数据管理")
        for key in ("video_cache_ttl_hours", "video_cache_max_items", "sight_cache_keep_days"):
            self.assertIn(key, schema["sight_config"]["items"])
            self.assertNotIn(key, schema["storage_config"]["items"])
        self.assertIn(f'["sight_config.{key}", "storage_config"]', config)
        self.assertIn("CONFIG_FIELD_DISPLAY_SECTIONS", config)
        self.assertIn("addConfigViewField(fieldsBySection, displaySection, field)", config)
        self.assertIn("displaySection === sectionKey", config)
        self.assertIn("explicitPath", config)

    def test_dashboard_moves_chat_style_prompts_to_prompt_settings(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8-sig"))
        config = (root / "pages" / "dashboard" / "ui" / "config.js").read_text(encoding="utf-8")

        self.assertEqual(schema["story_engine_config"]["description"], "提示词")
        self.assertIn("casual_short_prompt", schema["chat_style_config"]["items"])
        self.assertNotIn("fact_check_query_prompt", schema["chat_style_config"]["items"])
        self.assertNotIn("casual_short_prompt", schema["story_engine_config"]["items"])
        self.assertNotIn("fact_check_query_prompt", schema["story_engine_config"]["items"])
        self.assertIn('["chat_style_config.casual_short_prompt", "story_engine_config"]', config)
        self.assertNotIn('["chat_style_config.fact_check_query_prompt", "story_engine_config"]', config)

    def test_dashboard_no_longer_exposes_segment_pattern_setting(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8-sig"))
        config = (root / "pages" / "dashboard" / "ui" / "config.js").read_text(encoding="utf-8")

        self.assertNotIn("natural_segment_pattern", schema["chat_style_config"]["items"])
        self.assertNotIn("natural_segment_pattern", config)
        self.assertIn("spec.multiline === false", config)

    def test_dashboard_no_longer_exposes_weekly_theme_config(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8-sig"))
        config = (root / "pages" / "dashboard" / "ui" / "config.js").read_text(encoding="utf-8")

        self.assertNotIn("weekly_theme_config", schema)
        self.assertNotIn('"weekly_theme_config"', config)

    def test_dashboard_settings_auto_save_config(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        config = (root / "ui" / "config.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertNotIn("configSaveButton", html)
        self.assertNotIn("configSaveButton", app)
        self.assertNotIn("configReloadButton", html)
        self.assertNotIn("configReloadButton", app)
        self.assertNotIn("configSectionCount", html)
        self.assertNotIn("configSectionCount", app)
        self.assertNotIn("个分区", html)
        self.assertNotIn("个分区", app)
        self.assertNotIn("AUTOSAVE_DELAY_MS", config)
        self.assertIn("AUTOSAVE_FAST_DELAY_MS", config)
        self.assertIn("AUTOSAVE_TEXT_DELAY_MS", config)
        self.assertIn("AUTOSAVE_RETRY_DELAY_MS", config)
        self.assertIn("AUTOSAVE_MAX_WAIT_MS", config)
        self.assertIn("scheduleConfigAutosave", config)
        self.assertIn("sameConfigValue", config)
        self.assertIn("state.configChangeSeq", app + config)
        self.assertIn("state.configDirtySince", app + config)
        self.assertIn("flushConfigAutosave", app + config)
        self.assertIn("focusout", app)
        self.assertIn("visibilitychange", app)
        self.assertIn("saveConfig({ auto: true, changeSeq", config)
        self.assertIn("saveDelayMs: AUTOSAVE_TEXT_DELAY_MS", config)
        self.assertNotIn("configDirtyBadge", html + app + config)
        self.assertNotIn("settings-toolbar", html + style)
        self.assertNotIn("等待自动保存", html + config)
        self.assertNotIn("保存中", html + config)
        self.assertNotIn('textContent = "保存失败"', config)
        self.assertNotIn("已自动保存", html + config)
        self.assertIn("configVersion", app + config)
        self.assertNotIn("toolbar-pill", html + style)

    def test_dashboard_settings_uses_settings_layout(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn('class="settings-layout"', html)
        self.assertIn('class="settings-rail"', html)
        self.assertIn('class="rail-title"', html)
        self.assertIn('class="config-rail"', html)
        self.assertIn('class="config-desk"', html)
        self.assertIn('class="config-cover"', html)
        self.assertIn('class="config-grid"', html)
        self.assertIn('id="configNav"', html)
        self.assertIn('id="configSectionTitle"', html)
        self.assertIn('id="configSectionHint"', html)
        self.assertIn('id="configFieldList"', html)
        self.assertNotIn("config-atelier", html)
        self.assertNotIn("atelier-index", html)
        self.assertNotIn("atelier-tabs", html)
        self.assertNotIn("atelier-workbench", html)
        self.assertNotIn("atelier-cover", html)
        self.assertNotIn("settings-studio", html)
        self.assertIn(".settings-layout", style)
        self.assertIn(".settings-rail", style)
        self.assertIn(".rail-title", style)
        self.assertIn(".config-rail", style)
        self.assertIn(".config-desk", style)
        self.assertIn(".config-cover", style)
        self.assertIn(".config-grid", style)
        self.assertIn(".config-tab", style)
        self.assertNotIn(".config-atelier", style)
        self.assertNotIn(".atelier-index", style)
        self.assertNotIn(".atelier-tabs", style)
        self.assertNotIn(".atelier-workbench", style)
        self.assertNotIn(".atelier-cover", style)
        self.assertNotIn(".settings-studio", style)
        self.assertNotIn(".settings-index", style)
        self.assertNotIn(".settings-tabs", style)
        self.assertNotIn(".settings-workbench", style)
        self.assertNotIn(".settings-cover", style)

    def test_dashboard_settings_hides_config_field_counts(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

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
        style = self._dashboard_style(root)

        self.assertIn('classes.push("text-field")', config)
        self.assertIn("isPromptTextField", config)
        self.assertIn('classes.push("prompt-field")', config)
        self.assertIn("promptText ? 8 : 5", config)
        self.assertIn("grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));", style)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", style)
        self.assertNotIn("repeat(auto-fit, minmax(min(100%, 380px), 1fr))", style)
        self.assertIn(".config-field.template-list-field", style)
        self.assertIn(".config-field.text-field textarea", style)
        self.assertIn(".config-field.prompt-field textarea", style)
        self.assertNotIn('classes.push("wide")', config)
        self.assertNotIn('classes.push("extra-wide")', config)
        self.assertNotIn(".config-field.extra-wide", style)

    def test_dashboard_hides_storage_panel(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

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

    def test_dashboard_no_longer_exposes_workshop_actions(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")

        for element_id in (
            "weekWebButton",
            "templateWebButton",
            "catalogWebButton",
            "hairWebButton",
        ):
            self.assertNotIn(f'id="{element_id}"', html)
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
        self.assertNotIn("materialPackWebButton", app)
        self.assertNotIn("materialPackForm", app)
        self.assertNotIn("page/workshop/expand", app)
        self.assertNotIn("createWorkshopPanel", app)
        self.assertNotIn("智能扩展", html)
        self.assertNotIn("联网扩展", html)
        self.assertNotIn("generateWeek(", app)
        self.assertNotIn('"page/action/generate-week"', app)
        self.assertNotIn("templateDraft", app)
        self.assertNotIn("catalogDraft", app)
        self.assertNotIn("hairDraft", app)
        self.assertNotIn("renderTemplates", app)
        self.assertNotIn("renderCatalog", app)
        self.assertNotIn("renderHair", app)
        self.assertNotIn("fillTemplateEditor", app)
        self.assertNotIn("fillCatalogEditor", app)
        self.assertNotIn("fillHairEditor", app)
        self.assertIn("applyActionStatus(result)", app)
        self.assertNotIn("联网填充", html)
        self.assertNotIn("联网新建", html)

    def test_dashboard_no_longer_keeps_workshop_layout_styles(self):
        from pathlib import Path

        style = self._dashboard_style(Path(__file__).resolve().parents[1])

        self.assertNotIn(".settings-workshops", style)
        self.assertNotIn(".template-workspace", style)
        self.assertNotIn(".catalog-workspace", style)
        self.assertNotIn(".hair-workspace", style)
        self.assertNotIn(".template-editor", style)
        self.assertNotIn(".catalog-editor", style)

    def test_dashboard_no_longer_exposes_workshop_generation_inputs(self):
        from pathlib import Path

        html = (Path(__file__).resolve().parents[1] / "pages" / "dashboard" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn('id="templateText"', html)
        self.assertNotIn('id="catalogText"', html)
        self.assertNotIn('id="hairText"', html)
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
        style = self._dashboard_style(root)
        schema = (root / "_conf_schema.json").read_text(encoding="utf-8")

        self.assertIn('"_special": "character_reference_gallery"', schema)
        self.assertIn('apiPost("page/config/character-reference"', config)
        self.assertIn('apiPost("page/config/character-reference/preview"', config)
        self.assertIn('apiPost("page/config/character-reference/delete"', config)
        self.assertIn("function renderCharacterReferenceGallery(path, value)", config)
        self.assertIn("function renderImageGallery(spec, path, value)", config)
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
        style = self._dashboard_style(root)

        self.assertIn('data-world-tab="message_visibility"', html)
        self.assertIn('data-world-tab="life_decisions"', html)
        self.assertIn('life_decisions: "暂无生活观察记录"', app)
        self.assertIn('activeTab === "life_decisions"', app)
        self.assertNotIn("const decisions = objectItems(lifecycle.life_decisions)", app)
        self.assertIn('const total = reviews.length + preferences.length + events.length;', app)
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

    def test_dashboard_world_tabs_follow_debug_order(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")

        tabs = re.findall(r'data-world-tab="([^"]+)"', html)

        self.assertEqual(
            tabs,
            [
                "life_decisions",
                "action_decisions",
                "message_visibility",
                "group_environments",
                "summaries",
                "relationships",
                "places",
                "events",
            ],
        )
        self.assertIn('worldTab: "life_decisions"', app)

    def test_dashboard_experience_tabs_group_long_records(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        tabs = re.findall(r'data-experience-tab="([^"]+)"', html)

        self.assertEqual(
            tabs,
            ["relationships", "behavior", "language", "evidence", "feedback"],
        )
        self.assertNotIn('data-experience-tab="decision"', html)
        self.assertIn('state.experienceTab = tab.dataset.experienceTab;', app)
        self.assertIn("renderExperience(state.status || {});", app)
        self.assertIn('experienceTab: "relationships"', app)
        self.assertIn("function experienceGroups(status)", app)
        self.assertIn("relationships: []", app)
        self.assertIn("behavior: []", app)
        self.assertIn("language: []", app)
        self.assertIn("evidence: []", app)
        self.assertIn("feedback: []", app)
        self.assertNotIn("decision: lifeObservationRecords(status)", app)
        self.assertIn("groups.relationships.push(record)", app)
        self.assertIn("groups.behavior.push(record)", app)
        self.assertIn("groups.language.push(record)", app)
        self.assertIn("groups.evidence.push(record)", app)
        self.assertIn("groups.feedback.push(record)", app)
        self.assertIn("experienceEmptyText(activeTab)", app)
        self.assertIn("function relationshipNameIndex(status = {})", app)
        self.assertIn("function relationshipScopeLabel(value, relationshipNames = new Map())", app)
        self.assertIn("function relationshipTextResolver(status = {})", app)
        self.assertIn("function addGroupScopeName(index, key, label)", app)
        self.assertIn("function relationshipRecordLines(items, relationshipText)", app)
        self.assertIn('["profile", "relationship", "group_profile", "群友档案", "关系"]', app)
        self.assertIn('item.scope ? ["范围", relationshipScopeLabel(item.scope, relationshipNames)] : ""', app)
        self.assertIn('item.evidence ? ["证据", relationshipText(item.evidence)] : ""', app)
        self.assertIn("recordLines([relationshipText(item.summary)])", app)
        self.assertIn(".experience-panel .tabs", style)
        self.assertIn(".world-panel .tabs", style)
        self.assertIn("overflow-x: auto", style)

    def test_dashboard_renders_life_observation_in_world_life_tab(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn('data-world-tab="life_decisions"', html)
        self.assertNotIn("生活观察</h2>", html)
        self.assertNotIn('id="observationList"', html)
        self.assertNotIn("observationList: byId(\"observationList\")", app)
        self.assertNotIn("function renderObservatory", app)
        self.assertIn("function lifeObservationRecords", app)
        self.assertIn('if (activeTab === "life_decisions")', app)
        self.assertIn("const relationship = relationshipTextResolver(status);", app)
        self.assertIn("relationship.scope(item.sender_name || item.sender_profile_id) || \"未知\"", app)
        self.assertIn('relationshipText(item.reason) || "无裁定说明"', app)
        self.assertIn("今日决策摘要", app)
        self.assertIn("memory_clusters", app)
        self.assertIn("memory_entities", app)
        self.assertIn("memory_conflicts", app)
        self.assertIn("long_term_memories", app)
        self.assertIn("emotion_arcs", app)
        self.assertIn("情绪脉络", app)
        self.assertIn("physiological_rhythm_logs", app)
        self.assertIn("生理节律趋势", app)
        self.assertIn("经历聚合", app)
        self.assertIn("记忆张力", app)
        self.assertIn("有效期：", app)
        self.assertNotIn("有效到：", app)
        self.assertNotIn("最近生活惯性", app)
        self.assertNotIn("当前状态", app)
        self.assertNotIn("主动行为", app)
        self.assertNotIn("执行检查", app)
        self.assertNotIn("用户纠偏", app)
        self.assertNotIn("重复控制", app)
        self.assertNotIn("记忆驱动", app)
        self.assertIn('["决策", relationshipText(decision.decision)]', app)
        self.assertIn('["原因", relationshipText(decision.reason)]', app)
        self.assertIn('["依据", relationshipText(decision.evidence)]', app)
        self.assertIn('["来源", influenceSources]', app)
        self.assertNotIn("sourceParts", app)
        self.assertNotIn("const sourceLabel = enumLabel(decision.source, SOURCE_LABELS)", app)
        self.assertIn('["安排", relationshipText(decision.outcome)]', app)
        self.assertNotIn('["影响来源", influenceSources]', app)
        self.assertNotIn('["具体安排", clean(decision.outcome, "")]', app)
        self.assertNotIn('["落地", clean(decision.outcome, "")]', app)
        self.assertNotIn('["结果", clean(decision.outcome, "")]', app)
        self.assertIn("Array.isArray(line)", app)
        self.assertNotIn("[clean(decision.decision), clean(decision.reason), clean(decision.evidence), clean(decision.outcome)]", app)
        self.assertNotIn("周目标执行", app)
        self.assertNotIn("记忆影响", app)
        self.assertIn("decision.influence_sources", app)
        self.assertNotIn("决策影响链路", app)
        self.assertNotIn("decision_influence_chain", app)
        self.assertNotIn('item.decision ? `决策：${clean(item.decision)}` : ""', app)
        self.assertIn('join(" · ")', app)
        self.assertNotIn('join(" -> ")', app)
        self.assertIn("grid-template-columns: minmax(76px, 112px) minmax(0, 1fr)", style)
        self.assertIn("text-overflow: ellipsis", style)
        self.assertNotIn(".observation-panel", style)

    def test_dashboard_keeps_selected_world_tab_on_status_refresh(self):
        from pathlib import Path

        app = (Path(__file__).resolve().parents[1] / "pages" / "dashboard" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("function worldTabHasRecords", app)
        self.assertNotIn("function selectAvailableWorldTab", app)
        self.assertNotIn("selectAvailableWorldTab(nextStatus)", app)
        self.assertNotIn("tabs.find((tab) => worldTabHasRecords", app)
        self.assertIn('tab.addEventListener("click", () => {', app)
        self.assertIn('state.worldTab = tab.dataset.worldTab;', app)
        self.assertIn("renderWorld(state.status || {});", app)

    def test_today_week_plan_labels_hint_and_suggestions(self):
        from pathlib import Path

        app = (Path(__file__).resolve().parents[1] / "pages" / "dashboard" / "app.js").read_text(encoding="utf-8")

        self.assertIn('todayWeekRow("主题", theme)', app)
        self.assertIn('todayWeekRow("提示", hint)', app)
        self.assertIn('todayWeekRow("建议", suggested, "muted")', app)
        self.assertNotIn('todayWeekRow("进度", hint)', app)
        self.assertNotIn('todayWeekRow("目标", suggested', app)
        self.assertNotIn('todayWeekRow("今日提示", hint)', app)
        self.assertNotIn('todayWeekRow("建议活动", suggested', app)

    def test_dashboard_translates_structured_life_enum_text(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        labels = (root / "shared" / "labels.js").read_text(encoding="utf-8")
        display = (root / "shared" / "display.js").read_text(encoding="utf-8")
        scripts = "\n".join([app, labels, display])
        style = self._dashboard_style(root)

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
        self.assertIn('watchstate: "观看状态"', display)
        self.assertIn('targettype: "目标类型"', display)
        self.assertIn('evidence: "证据"', display)
        self.assertIn('evidencetype: "证据类型"', display)
        self.assertIn('chat_state_refresh: "聊天触发状态巡检"', labels)
        self.assertIn('["生活模式", SCHEDULE_TONE_LABELS, "日程基调"]', display)
        self.assertIn('["日程倾向", SCHEDULE_INTENT_LABELS, "活动倾向"]', display)
        self.assertIn('export const PLAN_OUTFIT_DECISION_LABELS = {', labels)
        self.assertIn('outdoor: "预计外出"', labels)
        self.assertIn('export const OUTFIT_STYLE_POOL_LABELS = {', labels)
        self.assertIn('sleep_styles: "居家/睡眠风格"', labels)
        self.assertIn('outfit_styles: "日常/外出风格"', labels)
        self.assertIn('export const OUTFIT_SCENE_CATEGORY_LABELS = {', labels)
        self.assertIn('["plan_outfit_decision", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"]', display)
        self.assertIn('["style_pool", OUTFIT_STYLE_POOL_LABELS, "风格池"]', display)
        self.assertIn('["outfit_style_pool", OUTFIT_STYLE_POOL_LABELS, "穿搭风格池"]', display)
        self.assertIn('["scene_category", OUTFIT_SCENE_CATEGORY_LABELS, "场景"]', display)
        self.assertIn('["风格池", OUTFIT_STYLE_POOL_LABELS, "风格池"]', display)
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
        self.assertIn('id="todayFacts"', html)
        self.assertEqual(html.count('class="facts-column"'), 1)
        self.assertIn('class="facts-column facts-column-fill"', html)
        self.assertEqual(html.count("data-fact-card="), 10)
        self.assertIn(".facts-column {\n  display: grid;", style)
        self.assertIn("grid-auto-rows: max-content;", style)
        self.assertIn("align-content: start;", style)
        self.assertIn(".facts-column-fill {\n  align-self: stretch;", style)
        self.assertIn("block-size: 100%;", style)
        self.assertIn("grid-template-rows: max-content max-content repeat(3, minmax(max-content, 1fr));", style)
        self.assertIn('.facts-column-fill > [data-fact-card="memo"]', style)
        self.assertIn(".facts-column > div {\n  min-width: 0;", style)
        self.assertIn('.facts-column > [data-fact-card="schedule-tone"]', style)
        self.assertIn('.facts-column > [data-fact-card="schedule-intent"]', style)
        self.assertIn("padding-block: 5px;", style)
        self.assertIn("const FACT_CARD_ORDER = [", app)
        self.assertIn("function layoutTodayFacts()", app)
        self.assertIn("function scheduleTodayFactsLayout()", app)
        self.assertIn('window.addEventListener("resize", scheduleTodayFactsLayout)', app)
        self.assertIn("const leftSize = Math.ceil(FACT_CARD_ORDER.length / 2);", app)
        self.assertNotIn("compact ? FACT_CARD_ORDER.length", app)
        self.assertNotIn("window.innerWidth || 0) <= 680", app)
        self.assertIn(".facts {\n    grid-template-columns: repeat(2, minmax(0, 1fr));", style)
        self.assertIn(".facts-column-fill {\n    align-self: stretch;", style)
        self.assertIn("grid-template-rows: max-content max-content repeat(3, minmax(max-content, 1fr));", style)
        self.assertNotIn("grid-template-rows: none;", style)
        self.assertIn("function renderTodayWeekPlan", app)
        self.assertIn("function stripLeadingEmoji", app)
        self.assertIn("function todayWeekRow", app)
        self.assertIn("clean(stripLeadingEmoji(week.theme), \"\")", app)
        self.assertIn("clean(stripLeadingEmoji(week.today_hint), \"\")", app)
        self.assertIn("clean(stripLeadingEmoji(week.today_suggested), \"\")", app)
        self.assertNotIn("today-week-title", app)
        self.assertIn('renderFactPair(el.currentOutfitText, currentOutfitDisplayText(day, meta), TODAY_FACT_EMPTY_TEXT.currentOutfitText)', app)
        self.assertIn('renderFactPair(el.outfitDecisionText, outfitDecisionText(meta), TODAY_FACT_EMPTY_TEXT.outfitDecisionText)', app)
        self.assertIn('function renderFactPair(target, value, emptyText = "暂无内容")', app)
        self.assertIn('todayWeekRow("提示", hint)', app)
        self.assertIn('todayWeekRow("建议", suggested, "muted")', app)
        self.assertNotIn('todayWeekRow("进度", hint)', app)
        self.assertNotIn('todayWeekRow("目标", suggested', app)
        self.assertIn('card.replaceChildren(...lines)', app)
        self.assertNotIn('node("div", "today-week-card", ...(title ? [title] : []), ...lines)', app)
        self.assertNotIn("今日提醒", app)
        self.assertNotIn("今日建议", app)
        self.assertIn("<dt>👗 当前穿搭</dt>", html)
        self.assertIn("<dt>🪞 穿搭判断</dt>", html)
        self.assertIn("function moodColorText(value)", display)
        self.assertIn('body.includes("·")', display)
        self.assertIn("el.moodColorText.textContent = clean(moodColorText(meta.mood), TODAY_FACT_EMPTY_TEXT.moodColorText)", app)
        self.assertIn("function scheduleTypeText(value)", display)
        self.assertIn("el.scheduleTypeText.textContent = clean(scheduleTypeText(meta.schedule_type), TODAY_FACT_EMPTY_TEXT.scheduleTypeText)", app)
        self.assertNotIn("meta.schedule_type || meta.style", app)
        self.assertIn("el.themeText.textContent = clean(meta.theme, TODAY_FACT_EMPTY_TEXT.themeText)", app)
        self.assertIn("el.scheduleToneText.textContent = clean(enumLabel(meta.life_mode, SCHEDULE_TONE_LABELS), TODAY_FACT_EMPTY_TEXT.scheduleToneText)", app)
        self.assertIn("function currentOutfitDisplayText(day = {}, meta = {})", display)
        self.assertIn("return { style, outfit }", display)
        self.assertIn("function outfitDecisionText(meta = {})", display)
        self.assertIn("return { decision, reason }", display)
        self.assertIn('renderFactPair(el.currentOutfitText, currentOutfitDisplayText(day, meta), TODAY_FACT_EMPTY_TEXT.currentOutfitText)', app)
        self.assertIn('renderFactPair(el.outfitDecisionText, outfitDecisionText(meta), TODAY_FACT_EMPTY_TEXT.outfitDecisionText)', app)
        self.assertIn('function renderFactPair(target, value, emptyText = "暂无内容")', app)
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
        self.assertIn('const CURRENT_ACTIVITY_EMPTY_TEXT = "暂无当前活动"', app)
        self.assertIn('const METER_EMPTY_TEXT = "暂无数据"', app)
        self.assertIn('const TIMELINE_TIME_EMPTY_TEXT = "未定"', app)
        self.assertIn("const TODAY_FACT_EMPTY_TEXT = {", app)
        self.assertIn("function renderEmptyTodayFacts()", app)
        self.assertIn("percent === null ? METER_EMPTY_TEXT", app)
        self.assertIn("clean(item.time, TIMELINE_TIME_EMPTY_TEXT)", app)
        self.assertIn(": CURRENT_ACTIVITY_EMPTY_TEXT", app)
        self.assertIn("el.todayWeekPlan.textContent = TODAY_FACT_EMPTY_TEXT.todayWeekPlan", app)
        self.assertIn("target.textContent = emptyText", app)
        self.assertIn('<span id="targetDate" class="pill">加载中</span>', html)
        self.assertIn('<dd id="weatherText">暂无天气</dd>', html)
        self.assertIn('<dd id="themeText">暂无主题</dd>', html)
        self.assertIn('<dd id="todayWeekPlan">暂无周计划</dd>', html)
        self.assertIn('<dd id="moodColorText">暂无心情色彩</dd>', html)
        self.assertIn('<dd id="scheduleTypeText">暂无日程类型</dd>', html)
        self.assertIn('<dd id="scheduleToneText">暂无日程基调</dd>', html)
        self.assertIn('<dd id="scheduleIntentText">暂无活动倾向</dd>', html)
        self.assertIn('<dd id="currentOutfitText">暂无穿搭</dd>', html)
        self.assertIn('<dd id="outfitDecisionText">暂无判断</dd>', html)
        self.assertIn("function memoDisplayText(status = {})", app)
        self.assertIn("function memoCarouselItems(status = {})", app)
        self.assertIn("function syncMemoCarousel(status = {})", app)
        self.assertIn("window.setInterval(() => {", app)
        self.assertIn("MEMO_CAROUSEL_MS", app)
        self.assertIn('const MEMO_EMPTY_TEXT = "暂无备忘录"', app)
        self.assertIn("if (!items.length) return MEMO_EMPTY_TEXT", app)
        self.assertIn("return clean(items[index].display_text, MEMO_EMPTY_TEXT)", app)
        self.assertIn('<dd id="memoText">暂无备忘录</dd>', html)
        self.assertNotIn("clean(day.memo", app)
        self.assertNotIn("memo.display || memo.target || memo.tomorrow", app)
        self.assertNotIn("display_label", app)
        self.assertNotIn("meta.style || enumLabel(day.time_period", app)
        self.assertNotIn('appendInfoBox("状态摘要"', app)
        self.assertIn('const mood = clean(lifeState.mood, "")', app)
        self.assertNotIn("lifeState.mood || lifeState.summary", app)
        self.assertNotIn('appendInfoBox("状态来源"', app)
        self.assertNotIn('["当前注意力"', app)
        self.assertNotIn('["状态摘要"', app)
        self.assertIn("enumLabel(sleep.depth, CURRENT_SLEEP_LABELS)", app)
        self.assertIn("const HEALTH_CHECK_KEYS = [", app)
        self.assertIn("function healthCheckRows(checks = [])", app)
        self.assertIn("health-check-list", app)
        self.assertNotIn("health-check-grid", app)
        self.assertNotIn("health-check-group", app)
        self.assertNotIn('"is-ok"', app)
        self.assertNotIn('"is-pending"', app)
        self.assertIn("function node(tag, className = \"\", content = \"\")", display)
        self.assertIn("function visibleLifeEpisodes(episodes)", display)
        self.assertIn('text(item.kind).trim().toLowerCase() !== "daily_plan"', display)
        self.assertIn("visibleEpisodes.slice(0, 4).forEach", app)
        self.assertIn("function lifeEpisodeLines(item)", display)
        self.assertIn('new Set(["时间轴", "地点"])', display)
        self.assertIn("recordLines(relationshipRecordLines([...lifeEpisodeLines(item), people].filter(Boolean), relationshipText))", app)
        self.assertNotIn("function visibleMemoryBoundaries(boundaries)", scripts)
        self.assertNotIn("boundaries.slice(0, 3).forEach", scripts)
        self.assertNotIn("experience.boundaries", scripts)
        self.assertNotIn('join(" / ")', scripts)
        self.assertNotIn("` / ${", scripts)
        self.assertNotIn("} / ${", scripts)
        self.assertIn('join(" · ")', scripts)
        self.assertIn("权重 ${Number(item.weight || 0).toFixed(1)} · ${relationshipText(evidence)}", app)
        self.assertIn(".record-lines", style)
        self.assertIn("function evidenceTargetTitle(item, displayIndex = null)", display)
        self.assertIn("clean(item.target_label", display)
        self.assertIn("function stateLogText(value)", display)
        self.assertIn("PAGE_STATUS_REASON_LABELS", display)
        self.assertIn('export const LIFE_DECISION_KIND_LABELS = {', labels)
        self.assertIn('daily_plan: "日程规划"', labels)
        self.assertIn('weekly_plan: "周计划"', labels)
        self.assertIn('outfit: "穿搭判断"', labels)
        self.assertIn('invite: "邀约判断"', labels)
        self.assertIn('export const WEEK_PROGRESS_STATUS_LABELS = {', labels)
        self.assertIn('missing: "暂无记录"', labels)
        self.assertIn("LIFE_DECISION_KIND_LABELS", display)
        self.assertIn("WEEK_PROGRESS_STATUS_LABELS", display)
        self.assertIn("typedLabel(decision.kind, LIFE_DECISION_KIND_LABELS)", app)
        self.assertNotIn("clean(item.kind)", app)
        self.assertNotIn("clean(item.target_type || \"memory\")", app)
        self.assertIn("stateLogText(entry)", app)
        self.assertNotIn('["社交电量", rhythm.social_battery]', app)
        self.assertIn('rhythm.social_battery !== undefined ? `社交电量：${Number(rhythm.social_battery || 0)}/100`', app)
        self.assertIn('appendInfoBox(\n    "生理节律"', app)
        self.assertIn("const rhythm = lifeState.physiological_rhythm || {}", app)
        self.assertIn('physiological_rhythm: "生理节律"', display)
        self.assertIn('social_battery: "社交电量"', display)
        self.assertIn('autonomous_life_update: "自主生活状态与穿搭更新"', labels)
        self.assertIn("width: min(1500px, 100%)", style)
        self.assertIn("minmax(280px, 0.86fr)", style)
        self.assertIn("minmax(460px, 1.25fr)", style)
        self.assertIn("minmax(300px, 0.92fr)", style)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", style)
        self.assertIn(".facts-column > div {\n  min-width: 0;", style)
        self.assertNotIn("width: min(1680px, 100%)", style)
        self.assertNotIn("width: min(1740px, 100%)", style)
        self.assertNotIn('"hero hero memory"', style)
        self.assertNotIn("grid-template-areas:", style)
        self.assertIn("border: 1px solid var(--line)", style)
        self.assertIn(".world-panel .tabs", style)
        self.assertIn(".experience-panel .tabs", style)
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
if (
  !mod.humanizeToken("monday")
  || !mod.humanizeToken("random")
  || mod.humanizeToken("daily_plan") !== "日程规划"
  || mod.humanizeToken("outfit") !== "穿搭判断"
  || mod.humanizeToken("life_decision") !== "生活决策"
  || mod.humanizeToken("action_decision") !== "动作裁定"
  || mod.humanizeToken("autonomous_life") !== "自主生活"
  || mod.humanizeToken("short_term") !== "短期"
  || mod.humanizeToken("interactioncapacity") !== "互动意愿"
  || mod.humanizeToken("interaction_capacity") !== "互动意愿"
  || mod.humanizeToken("sleepmode") !== "睡眠倾向"
  || mod.humanizeToken("lifemode") !== "日程基调"
  || mod.humanizeToken("watchstate") !== "观看状态"
  || mod.humanizeToken("interruptlevel") !== "打断等级"
  || mod.humanizeToken("physiologicalrhythm") !== "生理节律"
  || mod.humanizeToken("socialbattery") !== "社交电量"
  || mod.humanizeToken("bodycondition") !== "身体状态"
  || mod.humanizeToken("targettype") !== "目标类型"
  || mod.humanizeToken("evidencetype") !== "证据类型"
) {
  throw new Error("humanizeToken 没有正确转换配置选项标签");
}
if (mod.evidenceTargetTitle({ target_type: "life_decision", target_id: "4" }, 2) !== "生活决策") {
  throw new Error("evidenceTargetTitle 不应给生活决策证据展示临时序号");
}
if (mod.evidenceTargetTitle({ target_type: "life_decision", target_id: "4" }) !== "生活决策") {
  throw new Error("evidenceTargetTitle 不应把生活决策原始 ID 当成列表序号");
}
if (mod.evidenceTargetTitle({ target_type: "focus", target_id: "early_sleep" }) !== "关注目标 早睡") {
  throw new Error("evidenceTargetTitle 没有正确转换普通证据目标");
}
const body = mod.recordLines(["状态：open", ["来源", "chat_memory"]]);
if (body.tagName !== "DIV" || !body.className.includes("record-lines") || body.children.length !== 2) {
  throw new Error("recordLines 没有生成预期节点");
}
const evidence = mod.visibleExperienceEvidence(
  [
    { target_type: "life_decision", target_id: "1", evidence_type: "decision", summary: "当前决策" },
    { target_type: "life_decision", target_id: "2", evidence_type: "decision", summary: "历史决策" },
    { target_type: "life_decision", target_id: "3", evidence_type: "decision", summary: "更旧决策" },
    { target_type: "focus", target_id: "early_sleep", evidence_type: "decision", summary: "关注目标证据" },
  ],
  []
);
if (
  evidence.length !== 4
  || !evidence.some((item) => item.summary === "当前决策")
  || !evidence.some((item) => item.summary === "历史决策")
  || !evidence.some((item) => item.summary === "更旧决策")
  || !evidence.some((item) => item.summary === "关注目标证据")
) {
  throw new Error("visibleExperienceEvidence 不应额外隐藏生活决策证据");
}
const metrics = mod.clean("social14, interactioncapacity40; social_battery35; stress=20; emotional_stability:72; sleepiness 35; outgoing55; focus20");
if (
  metrics.includes("interactioncapacity")
  || metrics.includes("social_battery")
  || metrics.includes("emotional_stability")
  || !metrics.includes("社交意愿14")
  || !metrics.includes("互动意愿40")
  || !metrics.includes("社交电量35")
  || !metrics.includes("压力感：20")
  || !metrics.includes("情绪稳定：72")
  || !metrics.includes("困倦度35")
  || !metrics.includes("外出意愿55")
  || !metrics.includes("专注度20")
) {
  throw new Error(`状态数值字段没有正确中文化：${metrics}`);
}
const embedded = mod.clean("keep | 浅蓝色睡裙；状态：open；动作=save_memory；场景：casual_chat；来源：chat_memory");
if (
  embedded.includes("keep")
  || embedded.includes("open")
  || embedded.includes("save_memory")
  || embedded.includes("casual_chat")
  || embedded.includes("chat_memory")
  || !embedded.includes("保持当前穿搭")
  || !embedded.includes("进行中")
  || !embedded.includes("保存记忆")
  || !embedded.includes("普通闲聊")
  || !embedded.includes("聊天记忆")
) {
  throw new Error(`嵌入式枚举字段没有正确中文化：${embedded}`);
}
const evolution = mod.clean("根据 0.9 · 连续设定 sleepmode 为 early_sleep，且31号流程已预设早睡");
if (
  evolution.includes("sleepmode")
  || evolution.includes("early_sleep")
  || !evolution.includes("睡眠倾向")
  || !evolution.includes("早睡")
) {
  throw new Error(`生活演化摘要没有正确中文化：${evolution}`);
}
const proof = mod.clean("targettype 为 life_episode；evidencetype 为 daily_generation；sourcescope 为 group；targetscope 为 private");
if (
  proof.includes("targettype")
  || proof.includes("evidencetype")
  || proof.includes("sourcescope")
  || proof.includes("targetscope")
  || proof.includes("life_episode")
  || proof.includes("daily_generation")
  || !proof.includes("目标类型")
  || !proof.includes("生活片段")
  || !proof.includes("证据类型")
  || !proof.includes("每日生成依据")
  || !proof.includes("来源范围")
  || !proof.includes("目标范围")
) {
  throw new Error(`证据字段没有正确中文化：${proof}`);
}
const plainEvidence = mod.clean("evidence:群友档案");
if (plainEvidence.includes("evidence") || !plainEvidence.includes("证据：群友档案")) {
  throw new Error(`裸 evidence 字段没有正确中文化：${plainEvidence}`);
}
const log = mod.stateLogText("12:30 群聊观察；留意=seen_but_ignored；裁定=save_memory；watch_state=peek；interrupt_level=high；reason=autonomous_life_update");
if (!log.includes("留意：看见但略过") || !log.includes("裁定：保存记忆") || !log.includes("观看状态：偶尔看一眼") || !log.includes("打断等级：强信号才打断") || !log.includes("原因：自主生活状态与穿搭更新")) {
  throw new Error(`stateLogText 没有翻译状态变化枚举：${log}`);
}
const compactLog = mod.stateLogText("watchstate=peek；interruptlevel=high；sleepdepth=light_sleep；timeperiod=late_night；statusreason=chat_state_refresh");
if (
  compactLog.includes("watchstate")
  || compactLog.includes("interruptlevel")
  || compactLog.includes("sleepdepth")
  || compactLog.includes("timeperiod")
  || compactLog.includes("statusreason")
  || compactLog.includes("chat_state_refresh")
  || !compactLog.includes("观看状态：偶尔看一眼")
  || !compactLog.includes("打断等级：强信号才打断")
  || !compactLog.includes("睡眠层级：浅睡眠")
  || !compactLog.includes("时段：深夜")
  || !compactLog.includes("更新原因：聊天触发状态巡检")
) {
  throw new Error(`压扁状态字段没有正确中文化：${compactLog}`);
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            text=True,
            encoding="utf-8",
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
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_relationship_scope_uses_profile_display_name(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = self._dashboard_dom_mock_script() + """
const mod = await import("./pages/dashboard/app.js");
const names = mod.relationshipNameIndex({
  world: {
    relationships: [
      {
        id: "10000000",
        name: "10000000",
        display_name: "阿林",
        contacts: [
          {
            profile_id: "10000000",
            user_id: "10000000",
            target_scope: "aiocqhttp:FriendMessage:10000000",
          },
        ],
      },
    ],
    group_environments: [
      {
        group_id: "group-test-001",
        group_name: "测试",
        session_id: "aiocqhttp:GroupMessage:group-test-001",
      },
    ],
  },
});
for (const key of [
  "10000000",
  "profile:10000000",
  "relationship:10000000",
  "group_profile:10000000",
  "群友档案:10000000",
  "关系:10000000",
  "aiocqhttp:FriendMessage:10000000",
]) {
  if (mod.relationshipScopeLabel(key, names) !== "阿林") {
    throw new Error(`关系范围没有显示昵称：${key} -> ${mod.relationshipScopeLabel(key, names)}`);
  }
}
const evidence = mod.relationshipReferenceText("证据：关系:10000000", names);
if (evidence !== "证据：阿林") {
  throw new Error(`证据正文没有显示昵称：${evidence}`);
}
const reason = mod.relationshipReferenceText("原因：关系:10000000 有新互动", names);
if (reason !== "原因：阿林 有新互动") {
  throw new Error(`普通正文没有显示昵称：${reason}`);
}
for (const key of [
  "group-test-001",
  "aiocqhttp:GroupMessage:group-test-001",
]) {
  if (mod.relationshipScopeLabel(key, names) !== "测试") {
    throw new Error(`群聊范围没有显示群名：${key} -> ${mod.relationshipScopeLabel(key, names)}`);
  }
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            text=True,
            encoding="utf-8",
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
            encoding="utf-8",
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
            encoding="utf-8",
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
            encoding="utf-8",
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
        self.assertNotIn("currentDecisionId", display)
        self.assertIn("visibleExperienceEvidence(evidence, episodes)", app)
        self.assertIn("visibleEvidence.slice(0, 3).forEach", app)
