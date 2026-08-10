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

from core.models import ActionDecisionRecord, EmojiAssetRecord, MessageVisibilityRecord
from core.runtime.generation import DailyGenerationMixin
from support import (
    BehaviorFeedbackRecord,
    ChatSummaryRecord,
    DailyLifeDashboardMixin,
    DataManager,
    DayRecord,
    EmotionArcRecord,
    EventRecord,
    FocusSlotRecord,
    FocusTargetRecord,
    GroupEnvironmentRecord,
    LifeDecisionRecord,
    LifeEpisodeRecord,
    LifeSettings,
    LifeState,
    LifeTermRecord,
    LongTermMemoryRecord,
    MemoryBoundaryRecord,
    MemoryCorrectionRecord,
    MemoryEvidenceRecord,
    PhysiologicalRhythmLogRecord,
    PlaceRecord,
    TimelineItem,
    WeekPlanRecord,
)


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
        self.search = types.SimpleNamespace(inspiration=self.web_search)
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

    async def generate_daily(
        self,
        date=None,
        force=False,
        target_hour=None,
        extra=None,
        web_inspiration="",
        regenerate_existing=False,
    ):
        self.daily_calls.append(
            (
                date,
                force,
                target_hour,
                extra,
                web_inspiration,
                regenerate_existing,
            )
        )
        day = DayRecord(
            date=date.strftime("%Y-%m-%d"),
            outfit="浅蓝外套",
            timeline=[
                TimelineItem(time="10:00", activity="在窗边写手帐", status="平静")
            ],
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


class PageRuntime(DailyGenerationMixin):
    def __init__(self):
        self.raw_config = {
            "rhythm_config": {"schedule_time": "07:00"},
            "state_config": {"enabled": True, "refresh_minutes": 30},
        }
        self.config = LifeSettings.from_dict({})
        self.archive = DataManager()
        self.composer = PageComposer(self.archive)
        self.generation_lock = asyncio.Lock()
        self._init_daily_generation_state()
        self.refresh_calls = []
        self.weather_refresh_calls = []
        self.refresh_order = []
        self.apply_calls = []
        self.data_dir = Path(tempfile.mkdtemp(prefix="daily_life_page_"))
        self.data_path = self.data_dir / "daily_life.db"

    async def get_persona_text(self, scope=""):
        return await self.composer._get_persona()

    async def resolve_injection_target(self, now):
        return "2026-06-11", False

    @staticmethod
    def _target_datetime_for_command(date_str, now):
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=now.hour,
            minute=now.minute,
        )

    async def refresh_state_for_day(
        self, date_str, now=None, source="", detail="", force=False
    ):
        self.refresh_order.append("state")
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

    async def try_update_weather(self, date_str, *, force=False):
        self.refresh_order.append("weather")
        self.weather_refresh_calls.append((date_str, force))
        data = await self.archive.get_day(date_str)
        if data:
            data.weather = "测试市 晴 28°C"
            data.weather_info.temp = 28
            data.weather_info.condition = "晴"
            await self.archive.save_day(data)
        return True

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
        self.upload_path = None

    async def _page_json_body(self):
        return dict(self.body)

    def _page_request_method(self):
        return self.method

    async def _page_receive_upload(self, target, *, max_bytes):
        source = Path(self.upload_path) if self.upload_path else None
        if not source or not source.is_file():
            raise ValueError("没有收到上传文件")
        data = source.read_bytes()
        if len(data) > max_bytes:
            raise ValueError("上传文件超过大小限制")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".zip": "application/zip",
        }.get(source.suffix.lower(), "application/octet-stream")
        return {
            "path": target,
            "filename": source.name,
            "mime": mime,
            "size": len(data),
        }

    async def _page_send_download(self, path, *, mime, filename):
        return {
            "ok": True,
            "data": {
                "path": str(path),
                "mime": mime,
                "filename": filename,
                "size": path.stat().st_size,
            },
        }


class DailyLifeDashboardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = PagePlugin()
        await self.plugin.runtime.archive.save_day(
            DayRecord(
                date="2026-06-11",
                outfit="浅蓝外套和白裙子",
                weather="测试市 晴 24C",
                timeline=[
                    TimelineItem(
                        time="09:20", activity="整理早餐和手帐", status="慢慢来"
                    ),
                    TimelineItem(
                        time="14:10", activity="去常去咖啡店写稿", status="专注"
                    ),
                ],
                places=[PlaceRecord(name="常去咖啡店", type="cafe", hint="适合写稿")],
                new_events=[
                    EventRecord(date="2026-06-11", summary="完成一页手帐", place="家里")
                ],
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
                                "body_condition": {
                                    "label": "轻微疲惫",
                                    "intensity": 28,
                                    "source": "每日生成",
                                },
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
                    TimelineItem(
                        time="09:40", activity="在家慢慢整理桌面", status="安静"
                    ),
                    TimelineItem(
                        time="21:30", activity="洗漱后提前收尾休息", status="放松"
                    ),
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
            [
                EventRecord(
                    date="2026-06-10",
                    summary="和阿林聊到看展",
                    people=["阿林"],
                    place="线上",
                )
            ],
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
            LifeTermRecord(
                term="蹲后续", meaning="暂时围观同一话题后续", last_seen="2026-06-11"
            )
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
            MemoryBoundaryRecord(
                source_scope="group:1",
                target_scope="private:1",
                policy="ask",
                reason="跨域谨慎引用",
            )
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
        self.assertIn(
            "/astrbot_plugin_daily_life/page/config/character-reference", paths
        )
        self.assertIn(
            "/astrbot_plugin_daily_life/page/config/character-reference/preview", paths
        )
        self.assertIn(
            "/astrbot_plugin_daily_life/page/config/character-reference/delete", paths
        )
        friend_upload = paths.index(
            "/astrbot_plugin_daily_life/page/config/friend-reference/<path:profile_id>"
        )
        friend_preview = paths.index(
            "/astrbot_plugin_daily_life/page/config/friend-reference/preview/<path:profile_id>"
        )
        friend_delete = paths.index(
            "/astrbot_plugin_daily_life/page/config/friend-reference/delete/<path:profile_id>"
        )
        self.assertLess(friend_preview, friend_upload)
        self.assertLess(friend_delete, friend_upload)
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
        self.assertIn(
            "/astrbot_plugin_daily_life/page/experience/episode/correct", paths
        )
        self.assertIn(
            "/astrbot_plugin_daily_life/page/experience/episode/protect", paths
        )
        self.assertIn("/astrbot_plugin_daily_life/page/experience/focus", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/experience/boundary", paths)
        self.assertIn("/astrbot_plugin_daily_life/page/experience/feedback", paths)

    async def test_programmatic_management_routes_complete_storage_roundtrip(self):
        episode = await self.plugin.runtime.archive.save_life_episode(
            LifeEpisodeRecord(
                date="2026-06-11",
                title="测试生活片段",
                summary="用于验证程序化接口闭环。",
            )
        )
        self.plugin.body = {
            "episode_id": episode.id,
            "correction": "修正后的测试片段",
            "protected": True,
        }
        corrected = await self.plugin.page_experience_episode_correct()

        self.plugin.body = {"episode_id": episode.id, "protected": False}
        protected = await self.plugin.page_experience_episode_protect()

        self.plugin.body = {
            "focus": {
                "target_id": "test-focus",
                "label": "测试关注目标",
                "scope": "test:scope",
            }
        }
        focused = await self.plugin.page_experience_focus()

        self.plugin.body = {
            "boundary": {
                "source_scope": "test:source",
                "target_scope": "test:target",
                "policy": "ask",
            }
        }
        bounded = await self.plugin.page_experience_boundary()

        self.plugin.body = {
            "feedback": {
                "target_id": "test-action",
                "feedback": "测试反馈已确认",
                "score": 1,
            }
        }
        feedback = await self.plugin.page_experience_feedback()

        for result in (corrected, protected, focused, bounded, feedback):
            self.assertTrue(result["ok"])
        saved_episode = (await self.plugin.runtime.archive.get_life_episodes(1))[0]
        self.assertEqual(saved_episode.correction, "修正后的测试片段")
        self.assertFalse(saved_episode.protected)
        self.assertEqual(focused["data"]["focus"]["target_id"], "test-focus")
        self.assertEqual(bounded["data"]["boundary"]["target_scope"], "test:target")
        self.assertEqual(feedback["data"]["feedback"]["target_id"], "test-action")

    def test_page_error_messages_hide_internal_english_exceptions(self):
        self.assertEqual(
            self.plugin._page_public_error(ValueError("时间轴格式不正确")),
            "时间轴格式不正确",
        )
        self.assertEqual(
            self.plugin._page_public_error(RuntimeError("database is locked")),
            "操作失败，请查看后台日志",
        )

    async def test_page_config_get_returns_schema_and_current_config(self):
        await self.plugin.runtime.archive.touch_relationship(
            "profile:friend", name="示例好友", date_str="2026-06-11"
        )
        result = await self.plugin.page_config()

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertIn("rhythm_config", data["schema"])
        self.assertEqual(data["config"]["rhythm_config"]["schedule_time"], "07:00")
        self.assertIn(
            {"profile_id": "profile:friend", "display_name": "示例好友"},
            data["relationships"],
        )
        self.assertFalse(data["saved"])

    async def test_page_config_separates_chat_and_embedding_providers(self):
        class PageProvider:
            def __init__(self, provider_id, model, provider_type):
                self.provider_id = provider_id
                self.model = model
                self.provider_type = provider_type

            def meta(self):
                return types.SimpleNamespace(
                    id=self.provider_id,
                    model=self.model,
                    type=self.provider_type,
                )

        self.plugin.context.get_all_providers = lambda: [
            PageProvider("chat-one", "chat-model", "chat_completion")
        ]
        self.plugin.context.get_all_embedding_providers = lambda: [
            PageProvider("embedding-one", "embedding-model", "embedding")
        ]

        result = await self.plugin.page_config()

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["data"]["providers"],
            [
                {
                    "id": "chat-one",
                    "label": "chat-one · chat-model · chat_completion",
                    "kind": "chat",
                },
                {
                    "id": "embedding-one",
                    "label": "embedding-one · embedding-model · embedding",
                    "kind": "embedding",
                },
            ],
        )

    async def test_page_config_returns_and_saves_interface_keys_directly(self):
        self.plugin.runtime.raw_config.update(
            {
                "weather_awareness": {"api_key": "weather-secret"},
                "image_generation_config": {
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_key": "image-secret",
                            "model": "image-model",
                        }
                    ]
                },
                "video_generation_config": {"api_keys": ["video-one", "video-two"]},
            }
        )

        loaded = await self.plugin.page_config()
        config = loaded["data"]["config"]

        self.assertEqual(config["weather_awareness"]["api_key"], "weather-secret")
        self.assertEqual(
            config["image_generation_config"]["text_channels"][0]["api_key"],
            "image-secret",
        )
        self.assertEqual(
            config["video_generation_config"]["api_keys"],
            ["video-one", "video-two"],
        )

        config["rhythm_config"]["schedule_time"] = "08:20"
        self.plugin.method = "POST"
        self.plugin.body = {"config": config}
        saved = await self.plugin.page_config()

        self.assertTrue(saved["ok"])
        self.assertEqual(
            self.plugin.runtime.raw_config["weather_awareness"]["api_key"],
            "weather-secret",
        )
        self.assertEqual(
            self.plugin.runtime.raw_config["image_generation_config"]["text_channels"][
                0
            ]["api_key"],
            "image-secret",
        )
        self.assertEqual(
            self.plugin.runtime.raw_config["video_generation_config"]["api_keys"],
            ["video-one", "video-two"],
        )

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
        self.assertEqual(
            self.plugin.runtime.raw_config["rhythm_config"]["schedule_time"], "08:15"
        )

    async def test_page_config_post_keeps_disabled_group_response_gate(self):
        self.plugin.method = "POST"
        self.plugin.body = {
            "config": {
                "response_gate_config": {
                    "group_enabled": False,
                    "private_enabled": True,
                }
            }
        }

        result = await self.plugin.page_config()

        self.assertTrue(result["ok"])
        self.assertFalse(self.plugin.runtime.config.response_gate.group_enabled)
        self.assertFalse(
            self.plugin.runtime.raw_config["response_gate_config"]["group_enabled"]
        )
        self.assertFalse(
            result["data"]["config"]["response_gate_config"]["group_enabled"]
        )

    async def test_page_config_post_keeps_disabled_group_idle_reply(self):
        self.plugin.method = "POST"
        self.plugin.body = {
            "config": {
                "proactive_config": {
                    "group_enabled": False,
                    "private_enabled": True,
                }
            }
        }

        result = await self.plugin.page_config()

        self.assertTrue(result["ok"])
        self.assertFalse(self.plugin.runtime.config.proactive.group_enabled)
        self.assertFalse(
            self.plugin.runtime.raw_config["proactive_config"]["group_enabled"]
        )
        self.assertFalse(result["data"]["config"]["proactive_config"]["group_enabled"])

    async def test_character_reference_upload_saves_image_in_plugin_data_dir(self):
        source = self.plugin.runtime.data_path.parent / "正面参考.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nlife")
        self.plugin.upload_path = source

        result = await self.plugin.page_character_reference_upload()

        self.assertTrue(result["ok"])
        item = result["data"]["item"]
        saved_path = Path(item["path"])
        self.assertTrue(saved_path.name.startswith("character_reference_"))
        self.assertEqual(
            saved_path.parent, self.plugin.runtime.data_path.parent / "references"
        )
        self.assertEqual(item["name"], "正面参考.png")
        self.assertEqual(item["mime"], "image/png")
        self.assertEqual(item["size"], len(b"\x89PNG\r\n\x1a\nlife"))
        self.assertEqual(saved_path.read_bytes(), b"\x89PNG\r\n\x1a\nlife")
        self.assertFalse((saved_path.parent / "transfer").exists())
        self.assertEqual(list(saved_path.parent.glob("*.upload")), [])

        duplicate = await self.plugin.page_character_reference_upload()
        self.assertTrue(duplicate["ok"])
        self.assertEqual(duplicate["data"]["item"]["path"], str(saved_path))
        self.assertEqual(list(saved_path.parent.glob("*.upload")), [])

        self.plugin.body = {"path": str(saved_path)}
        preview = await self.plugin.page_character_reference_preview()
        self.assertTrue(preview["ok"])
        self.assertTrue(
            preview["data"]["data_url"].startswith("data:image/png;base64,")
        )

        self.plugin.body = {"path": str(saved_path)}
        deleted = await self.plugin.page_character_reference_delete()
        self.assertTrue(deleted["ok"])
        self.assertFalse(saved_path.exists())

        self.plugin.body = {"path": str(self.plugin.runtime.data_path)}
        blocked = await self.plugin.page_character_reference_delete()
        self.assertFalse(blocked["ok"])

    async def test_friend_reference_upload_is_scoped_to_relationship_profile(self):
        source = self.plugin.runtime.data_path.parent / "好友参考.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nfriend")
        self.plugin.upload_path = source

        result = await self.plugin.page_friend_reference_upload("profile:friend")

        self.assertTrue(result["ok"])
        item = result["data"]["item"]
        saved_path = Path(item["path"])
        expected_key = hashlib.sha256(b"profile:friend").hexdigest()[:20]
        self.assertEqual(saved_path.parent.name, expected_key)
        self.assertEqual(saved_path.parent.parent.name, "friends")
        self.assertFalse(
            (self.plugin.runtime.data_path.parent / "references" / "transfer").exists()
        )
        self.assertEqual(list(saved_path.parent.glob("*.upload")), [])

        self.plugin.body = {"path": str(saved_path)}
        blocked = await self.plugin.page_friend_reference_preview("profile:other")
        self.assertFalse(blocked["ok"])

        self.plugin.body = {"path": str(saved_path)}
        preview = await self.plugin.page_friend_reference_preview("profile:friend")
        self.assertTrue(preview["ok"])
        deleted = await self.plugin.page_friend_reference_delete("profile:friend")
        self.assertTrue(deleted["ok"])
        self.assertFalse(saved_path.exists())

    async def test_reference_upload_cleans_target_directory_temporary_file(self):
        source = self.plugin.runtime.data_path.parent / "错误格式.txt"
        source.write_bytes(b"not-an-image")
        self.plugin.upload_path = source

        result = await self.plugin.page_character_reference_upload()

        self.assertFalse(result["ok"])
        self.assertIn("仅支持 PNG", result["error"]["message"])
        reference_dir = self.plugin.runtime.data_path.parent / "references"
        self.assertEqual(list(reference_dir.glob("*.upload")), [])
        self.assertFalse((reference_dir / "transfer").exists())

    async def test_reference_upload_reports_clear_size_limit(self):
        async def reject_oversize_upload(*args, **kwargs):
            raise ValueError("上传文件超过大小限制")

        self.plugin._page_receive_upload = reject_oversize_upload

        result = await self.plugin.page_character_reference_upload()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["message"], "参考图不能超过 12 MB")

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
        self.assertTrue(
            preview["data"]["data_url"].startswith("data:image/png;base64,")
        )

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
        digest = hashlib.sha256(image_bytes).hexdigest()
        source = self.plugin.runtime.data_path.parent / "手动导入.png"
        source.write_bytes(image_bytes)
        self.plugin.upload_path = source

        result = await self.plugin.page_emoji_import()

        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["imported"])
        item = result["data"]["item"]
        saved_path = Path(item["file_path"])
        self.assertEqual(
            saved_path.parent, self.plugin.runtime.data_path.parent / "emoji"
        )
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
        self.assertTrue(
            preview["data"]["data_url"].startswith("data:image/png;base64,")
        )

    async def test_emoji_import_accepts_large_gif_when_limit_is_raised(self):
        self.plugin.runtime.config = LifeSettings.from_dict(
            {"emoji_config": {"max_size_mb": 20}}
        )
        image_bytes = b"GIF89a" + b"\x00" * (5 * 1024 * 1024 + 16)
        digest = hashlib.sha256(image_bytes).hexdigest()
        source = self.plugin.runtime.data_path.parent / "大一点的动图.gif"
        source.write_bytes(image_bytes)
        self.plugin.upload_path = source

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
        self.assertTrue(
            preview["data"]["data_url"].startswith("data:image/gif;base64,")
        )

        self.plugin.body = {"id": item["id"], "still": True}
        still_preview = await self.plugin.page_emoji_preview()
        self.assertTrue(still_preview["ok"])
        self.assertTrue(still_preview["data"]["still"])
        self.assertFalse(
            still_preview["data"]["data_url"].startswith("data:image/gif;base64,")
        )

    async def test_emoji_import_rejects_large_gif_by_default(self):
        image_bytes = b"GIF89a" + b"\x00" * (5 * 1024 * 1024 + 16)
        source = self.plugin.runtime.data_path.parent / "默认超限动图.gif"
        source.write_bytes(image_bytes)
        self.plugin.upload_path = source

        result = await self.plugin.page_emoji_import()

        self.assertFalse(result["ok"])
        self.assertIn("上传文件超过大小限制", result["error"]["message"])

    async def test_emoji_import_rejects_url_payload(self):
        self.plugin.body = {"url": "https://example.com/emoji.png"}

        result = await self.plugin.page_emoji_import()

        self.assertFalse(result["ok"])
        self.assertIn("没有收到上传文件", result["error"]["message"])

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
        emoji_path.with_name(f"{emoji_path.stem}.still.png").write_bytes(
            b"preview-cache"
        )
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
        self.assertTrue(backup["data"]["filename"].endswith(".zip"))
        archive_path = Path(backup["data"]["path"])
        archive_bytes = archive_path.read_bytes()
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
        restored_plugin.upload_path = archive_path
        restored = await restored_plugin.page_emoji_restore()

        self.assertTrue(restored["ok"])
        self.assertEqual(restored["data"]["restored"], 1)
        self.assertEqual(restored["data"]["stats"]["total"], 1)
        item = restored["data"]["items"][0]
        restored_path = Path(item["file_path"])
        self.assertEqual(
            restored_path.parent, restored_plugin.runtime.data_path.parent / "emoji"
        )
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
        merged_plugin.upload_path = archive_path
        merged = await merged_plugin.page_emoji_restore()

        self.assertTrue(merged["ok"])
        item = merged["data"]["items"][0]
        self.assertEqual(item["label"], "已有识图")
        self.assertEqual(item["description"], "本地已有更完整说明")
        self.assertEqual(item["emotions"], ["已有"])
        self.assertFalse(item["sendable"])
        self.assertEqual(Path(item["file_path"]).read_bytes(), image_bytes)

    async def test_build_page_status_returns_current_life_world_without_workshop_data(
        self,
    ):
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
        self.assertEqual(
            status["memo"]["items"],
            [
                {
                    "date": "2026-06-11",
                    "scope": "target",
                    "text": "下午确认明天安排",
                    "display_text": "下午确认明天安排",
                }
            ],
        )
        self.assertNotIn("target", status["memo"])
        self.assertNotIn("tomorrow", status["memo"])
        self.assertNotIn("display_label", status["memo"])
        self.assertEqual(status["week_plan"]["theme"], "慢生活周")
        self.assertEqual(status["world"]["relationships"][0]["name"], "阿林")
        self.assertNotIn("life_decisions", status["world"])
        self.assertNotIn("life_decisions", status["lifecycle"])
        self.assertEqual(
            status["observatory"]["today_decision"]["decision"], "低体力恢复日"
        )
        self.assertEqual(
            status["observatory"]["today_decision"]["source"], "autonomous_life"
        )
        self.assertIn(
            "早睡恢复已参与今日生活决策",
            " ".join(status["observatory"]["today_decision"]["influence_sources"]),
        )
        self.assertNotIn("current_snapshot", status["observatory"])
        self.assertNotIn("proactive_state", status["observatory"])
        self.assertNotIn("execution_review", status["observatory"])
        self.assertNotIn("correction_lifecycle", status["observatory"])
        self.assertNotIn("repeat_guard", status["observatory"])
        self.assertNotIn("world_drivers", status["observatory"])
        self.assertNotIn("memory_influence", status["observatory"])
        self.assertNotIn("decision_influence_chain", status["observatory"])
        relationship_evidence = [
            item
            for item in status["experience"]["evidence"]
            if item["target_type"] == "relationship"
        ][0]
        self.assertEqual(relationship_evidence["target_label"], "阿林")
        self.assertEqual(status["experience"]["episodes"][0]["title"], "轻量恢复日")
        proactive_feedback = [
            item
            for item in status["experience"]["feedback"]
            if item["scene"] == "闲时回复读空气" and item["action"] == "闲时续话"
        ]
        self.assertEqual(len(proactive_feedback), 1)
        self.assertEqual(
            status["experience"]["emotion_arcs"][0]["label"], "轻松但想慢一点"
        )
        self.assertIn("低强度", status["experience"]["emotion_arcs"][0]["influence"])
        self.assertEqual(
            status["experience"]["physiological_rhythm_logs"][0]["body_label"],
            "轻微疲惫",
        )
        self.assertIn(
            "平均身体负荷",
            status["experience"]["physiological_rhythm_trend"]["summary"],
        )
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
        health_checks = status["experience"]["health"]["checks"]
        health_labels = [item["label"] for item in health_checks]
        self.assertEqual(len(health_labels), len(set(health_labels)))

    async def test_page_domain_actions_use_contact_and_group_display_names(self):
        class Resolver:
            @staticmethod
            def get_relationship_alias(target):
                return "测试备注" if target.endswith(":10001") else ""

            @staticmethod
            async def get_onebot_nickname(_target):
                return "测试昵称"

            @staticmethod
            async def resolve_group_name(_group_id, *, target_umo=""):
                return "测试群聊" if target_umo else ""

        self.plugin.runtime.contact_resolver = Resolver()
        snapshot = await self.plugin._page_domain_snapshot(
            {
                "conversation_actions": [
                    {
                        "title": "确认测试安排",
                        "source_session": "test-adapter:FriendMessage:10001",
                    },
                    {
                        "title": "确认昵称解析",
                        "source_session": "test-adapter:FriendMessage:10002",
                    },
                    {
                        "title": "查看群聊安排",
                        "source_session": "test-adapter:GroupMessage:20001",
                    },
                    {
                        "title": "检查未知来源",
                        "source_session": "custom-session",
                    },
                ]
            }
        )

        actions = snapshot["conversation_actions"]
        self.assertEqual(actions[0]["source_session_label"], "测试备注")
        self.assertEqual(actions[1]["source_session_label"], "测试昵称")
        self.assertEqual(actions[2]["source_session_label"], "测试群聊")
        self.assertEqual(actions[3]["source_session_label"], "会话")
        self.assertEqual(
            actions[0]["source_session"], "test-adapter:FriendMessage:10001"
        )

    async def test_page_domain_snapshot_only_exposes_current_day_activity(self):
        snapshot = await self.plugin._page_domain_snapshot(
            {
                "activity_sessions": [
                    {"date": "2026-06-10", "title": "昨天的活动"},
                    {"date": "2026-06-11", "title": "今天的活动"},
                ],
                "pantry": [{"name": "测试库存"}],
                "recipes": [{"name": "测试食谱"}],
                "meals": [
                    {"date": "2026-06-10", "name": "昨天的餐食"},
                    {"date": "2026-06-11", "name": "今天的餐食"},
                ],
                "chores": [{"name": "测试家务轮换", "enabled": True}],
                "chore_records": [
                    {
                        "occurred_at": "2026-06-10 09:00:00",
                        "name": "昨天执行的家务",
                    },
                    {
                        "occurred_at": "2026-06-11 09:00:00",
                        "name": "今天执行的家务",
                    },
                ],
                "fitness": [
                    {"date": "2026-06-10", "activity": "昨天的运动"},
                    {"date": "2026-06-11", "activity": "今天的运动"},
                ],
                "conversation_actions": [
                    {
                        "title": "仍待完成的行动项",
                        "status": "open",
                        "created_at": "2026-06-10 08:00:00",
                    },
                    {
                        "title": "昨天已完成的行动项",
                        "status": "completed",
                        "updated_at": "2026-06-10 18:00:00",
                    },
                    {
                        "title": "今天已完成的行动项",
                        "status": "completed",
                        "updated_at": "2026-06-11 18:00:00",
                    },
                ],
                "timeline": [
                    {
                        "title": "昨天的总览记录",
                        "occurred_at": "2026-06-10 12:00:00",
                    },
                    {
                        "title": "今天的总览记录",
                        "occurred_at": "2026-06-11 12:00:00",
                    },
                ],
            },
            "2026-06-11",
        )

        self.assertEqual(
            [item["title"] for item in snapshot["activity_sessions"]],
            ["今天的活动"],
        )
        self.assertEqual([item["name"] for item in snapshot["meals"]], ["今天的餐食"])
        self.assertEqual(
            [item["name"] for item in snapshot["chore_records"]],
            ["今天执行的家务"],
        )
        self.assertEqual(
            [item["activity"] for item in snapshot["fitness"]],
            ["今天的运动"],
        )
        self.assertEqual(
            [item["title"] for item in snapshot["timeline"]],
            ["今天的总览记录"],
        )
        self.assertEqual(
            [item["title"] for item in snapshot["conversation_actions"]],
            ["仍待完成的行动项", "今天已完成的行动项"],
        )
        self.assertEqual(snapshot["pantry"][0]["name"], "测试库存")
        self.assertEqual(snapshot["recipes"][0]["name"], "测试食谱")
        self.assertEqual(snapshot["chores"][0]["name"], "测试家务轮换")

    async def test_page_status_embeds_compact_today_decision_sources(self):
        long_reason = (
            "全天宅家，阴天闷热，睡裙穿得正舒服，没必要换。当前：10:30 把吃完的碗碟冲洗了，"
            "回房间找出那个半途而废的打卡本，翻开看了几页，又忍不住想今天的热搜。"
        )
        current = (
            await self.plugin.runtime.archive.get_life_decisions(
                limit=1, kind="daily_plan"
            )
        )[0]
        for index, summary in enumerate(
            [long_reason, long_reason, f"用户纠偏影响今日安排 {long_reason}"]
        ):
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

    async def test_page_status_keeps_today_decision_when_other_decisions_are_newer(
        self,
    ):
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
        self.assertEqual(
            status["observatory"]["today_decision"]["decision"], "低体力恢复日"
        )

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
        self.assertEqual(
            decision["evidence"],
            "今天体力偏低；2026-06-10 | 昨天晚睡；晚上补一点轻活动。",
        )
        self.assertEqual(decision["outcome"], "傍晚再短暂出门透气。")

    async def test_page_status_returns_future_memo_carousel_when_current_day_has_none(
        self,
    ):
        day = await self.plugin.runtime.archive.get_day("2026-06-11")
        day.memo = ""
        await self.plugin.runtime.archive.save_day(day)
        await self.plugin.runtime.archive.save_day(
            DayRecord(date="2026-06-15", memo="- 下周看展")
        )
        await self.plugin.runtime.archive.save_day(
            DayRecord(date="2026-06-13", memo="- 周六取快递\n- 晚上确认车票")
        )

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
                feedback="用户明确拒绝继续这一轮闲时续话",
                result="negative",
                score=-1.0,
                reason="后续消息明确拒绝",
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
                date="2026-06-11",
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
                date="2026-06-11",
                action="observe",
                reason="深夜02:40高度困倦，先观察，不急着接话",
            )
        )
        await self.plugin.runtime.archive.save_action_decision(
            ActionDecisionRecord(
                session_id="aiocqhttp:GroupMessage:100",
                sender_profile_id="u1",
                sender_name="小林",
                group_id="100",
                date="2026-06-11",
                action="reply",
                reason="约好16:40碰头，后续有自然接话点",
            )
        )
        await self.plugin.runtime.archive.save_action_decision(
            ActionDecisionRecord(
                session_id="aiocqhttp:GroupMessage:100",
                sender_profile_id="u1",
                sender_name="小林",
                group_id="100",
                date="2026-06-11",
                action="observe",
                reason="",
                scene_type="普通闲聊",
            )
        )

        status = await self.plugin._build_page_status()

        self.assertEqual(len(status["world"]["message_visibility"]), 1)
        self.assertEqual(
            status["world"]["message_visibility"][0]["reason"],
            "扫到了但当时不想接普通闲聊",
        )
        self.assertEqual(len(status["world"]["action_decisions"]), 2)
        self.assertEqual(
            status["world"]["action_decisions"][0]["reason"],
            "约好碰头，后续有自然接话点",
        )
        self.assertEqual(
            status["world"]["action_decisions"][1]["reason"],
            "深夜高度困倦，先观察，不急着接话",
        )
        self.assertEqual(status["world"]["action_decisions"][0]["sender_name"], "小林")
        self.assertNotIn("decision_category", status["world"]["action_decisions"][0])
        self.assertNotIn("decision_outcome", status["world"]["action_decisions"][0])

    async def test_page_status_keeps_group_environment_history(self):
        await self.plugin.runtime.archive.save_group_environment(
            GroupEnvironmentRecord(
                session_id="aiocqhttp:GroupMessage:100",
                group_id="100",
                group_name="测试群",
                date="2026-06-11",
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
                date="2026-06-11",
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

    async def test_page_status_only_exposes_current_day_process_records(self):
        await self.plugin.runtime.archive.save_chat_summary(
            ChatSummaryRecord(
                session_id="test-adapter:FriendMessage:10001",
                date="2026-06-10",
                brief="昨天的会话",
                long_summary="昨天留下的会话摘要",
            )
        )
        await self.plugin.runtime.archive.save_chat_summary(
            ChatSummaryRecord(
                session_id="test-adapter:FriendMessage:10001",
                date="2026-06-11",
                brief="今天的会话",
                long_summary="今天形成的会话摘要",
            )
        )
        await self.plugin.runtime.archive.add_events(
            "2026-06-11",
            [
                EventRecord(
                    date="2026-06-11",
                    summary="今天完成测试安排",
                    place="测试地点",
                )
            ],
        )
        await self.plugin.runtime.archive.save_message_visibility(
            MessageVisibilityRecord(
                session_id="test-adapter:FriendMessage:10001",
                sender_profile_id="u1",
                date="2026-06-10",
                visibility="seen",
                reason="昨天的消息留意记录",
            )
        )
        await self.plugin.runtime.archive.save_action_decision(
            ActionDecisionRecord(
                session_id="test-adapter:FriendMessage:10001",
                sender_profile_id="u1",
                date="2026-06-10",
                action="observe",
                reason="昨天的裁定记录",
            )
        )
        await self.plugin.runtime.archive.save_life_episode(
            LifeEpisodeRecord(
                date="2026-06-10",
                title="昨天的生活经历",
                summary="不应继续显示在今天的体验层。",
            )
        )

        status = await self.plugin._build_page_status()

        self.assertEqual(
            [item["brief"] for item in status["world"]["summaries"]],
            ["今天的会话"],
        )
        self.assertEqual(
            [item["summary"] for item in status["world"]["events"]],
            ["今天完成测试安排"],
        )
        self.assertFalse(status["world"]["message_visibility"])
        self.assertFalse(status["world"]["action_decisions"])
        self.assertNotIn(
            "昨天的生活经历",
            [item["title"] for item in status["experience"]["episodes"]],
        )
        self.assertTrue(status["world"]["relationships"])
        self.assertTrue(status["world"]["places"])
        self.assertTrue(status["experience"]["long_term_memories"])

    def test_page_date_filter_prefers_explicit_business_date(self):
        records = [
            {
                "date": "2026-06-10",
                "created_at": "2026-06-11 08:00:00",
                "summary": "昨天的业务记录",
            },
            {
                "date": "2026-06-11",
                "created_at": "2026-06-10 23:59:59",
                "summary": "今天的业务记录",
            },
        ]

        filtered = self.plugin._page_records_for_date(
            records, "2026-06-11", "date", "created_at"
        )

        self.assertEqual([item["summary"] for item in filtered], ["今天的业务记录"])

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

    async def test_timeline_save_preserves_existing_travel_context(self):
        day = await self.plugin.runtime.archive.get_day("2026-06-11")
        day.timeline[1].place = "测试咖啡店"
        day.timeline[1].place_kind = "poi"
        day.timeline[1].travel_mode = "walking"
        day.timeline[1].travel_origin = "家"
        day.timeline[1].travel_provider = "amap"
        day.timeline[1].travel_detail = "地铁"
        day.timeline[1].travel_minutes = 16
        day.timeline[1].travel_distance_meters = 1300
        await self.plugin.runtime.archive.save_day(day)
        self.plugin.body = {
            "date": "2026-06-11",
            "timeline": [
                {
                    "time": item.time,
                    "activity": item.activity,
                    "status": item.status,
                }
                for item in day.timeline
            ],
        }

        result = await self.plugin.page_timeline_save()
        saved = await self.plugin.runtime.archive.get_day("2026-06-11")

        self.assertTrue(result["ok"])
        self.assertEqual(saved.timeline[1].travel_origin, "家")
        self.assertEqual(saved.timeline[1].travel_provider, "amap")
        self.assertEqual(saved.timeline[1].travel_detail, "地铁")
        self.assertEqual(saved.timeline[1].travel_minutes, 16)
        self.assertEqual(saved.timeline[1].travel_distance_meters, 1300)

    async def test_reset_day_uses_current_business_date(self):
        self.plugin.body = {"extra": "今天多安排室内活动"}

        result = await self.plugin.page_reset_day()

        self.assertTrue(result["ok"])
        call = self.plugin.runtime.composer.daily_calls[0]
        self.assertEqual(call[0].strftime("%Y-%m-%d"), "2026-06-11")
        self.assertTrue(call[1])
        self.assertEqual(call[3], "今天多安排室内活动")
        self.assertEqual(call[4], "")
        self.assertTrue(call[5])
        self.assertEqual(result["data"]["day"]["outfit"], "浅蓝外套")
        self.assertEqual(result["data"]["status"]["day"]["outfit"], "浅蓝外套")

    async def test_refresh_state_action_returns_full_page_status(self):
        result = await self.plugin.page_refresh_state()

        self.assertTrue(result["ok"])
        self.assertEqual(
            self.plugin.runtime.refresh_calls[-1],
            (
                "2026-06-11",
                "dashboard",
                "面板手动刷新天气和实时状态",
                True,
            ),
        )
        self.assertEqual(
            self.plugin.runtime.weather_refresh_calls[-1],
            ("2026-06-11", True),
        )
        self.assertEqual(self.plugin.runtime.refresh_order[-2:], ["weather", "state"])
        self.assertTrue(result["data"]["weather_refreshed"])
        self.assertIn("status", result["data"])
        self.assertEqual(result["data"]["status"]["day"]["weather"], "测试市 晴 28°C")
        self.assertEqual(
            result["data"]["status"]["day"]["state"]["source"], "dashboard"
        )
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
        self.plugin.runtime.config.search.inspiration_enabled = True
        self.plugin.body = {"extra": "雨天宅家"}

        result = await self.plugin.page_reset_day()

        self.assertTrue(result["ok"])
        self.assertIn(
            "联网参考：雨天宅家", self.plugin.runtime.composer.daily_calls[0][4]
        )
        self.assertEqual(self.plugin.runtime.composer.web_calls[0][0], "雨天宅家")

    async def test_reset_day_can_explicitly_skip_enabled_web_inspiration(self):
        self.plugin.runtime.config.search.inspiration_enabled = True
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
        self.assertIn(
            "联网参考：轻松恢复", self.plugin.runtime.composer.week_calls[0][1]
        )
        self.assertEqual(self.plugin.runtime.composer.web_calls[0][0], "轻松恢复")
        self.assertEqual(
            self.plugin.runtime.composer.web_calls[0][2]["category"], "周计划"
        )
        self.assertEqual(self.plugin.runtime.composer.daily_calls, [])

    async def test_generate_week_uses_enabled_web_inspiration_by_default(self):
        self.plugin.runtime.config.search.inspiration_enabled = True
        self.plugin.body = {"goals": "周末慢下来"}

        result = await self.plugin.page_generate_week()

        self.assertTrue(result["ok"])
        self.assertIn(
            "联网参考：周末慢下来", self.plugin.runtime.composer.week_calls[0][1]
        )
        self.assertEqual(
            self.plugin.runtime.composer.web_calls[0][2]["category"], "周计划"
        )

    async def test_generate_week_serializes_search_and_generation(self):
        active_searches = 0
        max_active_searches = 0

        async def controlled_search(keyword, prompt_template, **kwargs):
            nonlocal active_searches, max_active_searches
            active_searches += 1
            max_active_searches = max(max_active_searches, active_searches)
            await asyncio.sleep(0.01)
            active_searches -= 1
            return f"联网参考：{keyword}"

        self.plugin.runtime.composer.search.inspiration = controlled_search
        self.plugin.body = {"goals": "轻松恢复", "use_web": True}

        first, second = await asyncio.gather(
            self.plugin.page_generate_week(),
            self.plugin.page_generate_week(),
        )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(max_active_searches, 1)

    async def test_generate_week_can_explicitly_skip_enabled_web_inspiration(self):
        self.plugin.runtime.config.search.inspiration_enabled = True
        self.plugin.body = {"goals": "周末慢下来", "use_web": False}

        result = await self.plugin.page_generate_week()

        self.assertTrue(result["ok"])
        self.assertEqual(self.plugin.runtime.composer.week_calls[0][1], "")
        self.assertEqual(self.plugin.runtime.composer.web_calls, [])


class DailyLifeDashboardStaticTest(unittest.TestCase):
    def test_domain_actions_only_render_resolved_session_names(self):
        from pathlib import Path

        app = (
            Path(__file__).resolve().parents[1] / "pages" / "dashboard" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("item.source_session_label", app)
        self.assertNotIn("clean(item.source_session)", app)

    def test_page_evidence_hides_internal_references(self):
        self.assertEqual(
            PagePlugin._page_readable_evidence("251880291"),
            "来自 1 条聊天消息",
        )
        self.assertEqual(
            PagePlugin._page_readable_evidence("说明；251880291,929722496"),
            "说明",
        )
        self.assertEqual(
            PagePlugin._page_readable_evidence("群号 12345678"),
            "群号 12345678",
        )

    @staticmethod
    def _dashboard_style(root):
        import re

        dashboard = (
            root if (root / "style.css").exists() else root / "pages" / "dashboard"
        )
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
        self.assertTrue((root / "shared" / "terms.js").exists())
        self.assertTrue((root / "shared" / "format.js").exists())
        for name in (
            "foundation.css",
            "selects.css",
            "effects.css",
            "shell.css",
            "dashboard.css",
            "emoji.css",
            "settings.css",
            "responsive.css",
            "dark.css",
        ):
            self.assertTrue((root / "styles" / name).exists())
        self.assertTrue((root / "ui" / "effects.js").exists())
        self.assertTrue((root / "ui" / "selects.js").exists())

    def test_dashboard_visible_page_copy_uses_chinese(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        formatter = (root / "shared" / "format.js").read_text(encoding="utf-8")

        visible_source = f"{html}\n{app}"
        for english_heading in ("Daily Life ·", "Emoji Pocket ·", "Soft Settings ·"):
            self.assertNotIn(english_heading, visible_source)
        for chinese_heading in ("日常生活 ·", "表情口袋 ·", "运行设置 ·"):
            self.assertIn(chinese_heading, visible_source)
        self.assertIn(
            'return /[A-Za-z]/.test(readable) ? "其他" : readable;',
            formatter,
        )

    def test_dashboard_timeline_displays_and_preserves_travel_details(self):
        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        app = (root / "app.js").read_text(encoding="utf-8")
        formatter = (root / "shared" / "format.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn("timelineTravelText,", app)
        self.assertIn("function timelineTravelText", formatter)
        self.assertIn('node("div", "timeline-travel", travel)', app)
        self.assertIn("...source", app)
        self.assertIn(".timeline-travel", style)

    def test_dashboard_timeline_uses_internal_vertical_scroll(self):
        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn('id="timelineList"', html)
        self.assertIn('tabindex="0"', html)
        self.assertIn('aria-label="今日日程时间轴，可滚动查看"', html)
        self.assertIn("max-height: min(78vh, 840px);", style)
        self.assertIn("max-height: min(72vh, 640px);", style)
        self.assertIn("overflow-y: auto;", style)
        self.assertIn("scrollbar-gutter: stable;", style)
        self.assertIn("touch-action: pan-y;", style)
        self.assertIn("el.timelineList.scrollTop = el.timelineList.scrollHeight;", app)

    def test_dashboard_local_assets_do_not_use_version_queries(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        css = (root / "style.css").read_text(encoding="utf-8")

        self.assertIn('href="./style.css"', html)
        self.assertIn('src="./app.js"', html)
        for asset in (
            "foundation.css",
            "selects.css",
            "effects.css",
            "shell.css",
            "dashboard.css",
            "emoji.css",
            "settings.css",
            "responsive.css",
            "dark.css",
        ):
            self.assertIn(f'url("./styles/{asset}")', css)
        for module in ("settings.js", "dialog.js", "effects.js", "selects.js"):
            self.assertIn(f'./ui/{module}"', app)
        display = (root / "shared" / "format.js").read_text(encoding="utf-8")
        version_query = "?" + "v="
        self.assertNotIn(version_query, "\n".join((html, css, app, display)))

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
        self.assertIn("border-start-start-radius: inherit", style)
        self.assertIn("border-start-end-radius: inherit", style)
        self.assertIn(".config-field.has-open-select::before", style)
        self.assertIn("height: var(--diary-radius-lg)", style)
        self.assertIn("top / 100% 6px no-repeat", style)
        self.assertIn(".life-native-select", style)
        self.assertIn("createLifeSelectControls", selects)
        self.assertNotIn("MutationObserver", selects)
        self.assertNotIn("documentObserver", selects)
        self.assertIn("refresh(scope = root)", selects)
        self.assertIn("syncExisting(scope = null)", selects)
        self.assertIn("scopeContains(scope, select)", selects)
        self.assertIn("item.tabIndex = -1", selects)
        self.assertIn("syncSelectControls", app)
        self.assertIn("configLoading: false", app)
        self.assertIn("deferConfigLoadForSettings", app)
        self.assertIn("loadConfig({ quiet: true, busy: false })", app)
        self.assertIn(
            "syncSelectControls",
            (root / "ui" / "settings.js").read_text(encoding="utf-8"),
        )
        self.assertIn("lifeSelectControls.init()", app)

    def test_dashboard_shows_interface_keys_directly(self):
        root = Path(__file__).resolve().parents[1]
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )
        style = self._dashboard_style(root / "pages" / "dashboard")
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))

        self.assertNotIn("wrapSecretControl", config)
        self.assertNotIn('type = "password"', config)
        self.assertNotIn(".secret-control", style)
        self.assertNotIn("secret", schema["weather_awareness"]["items"]["api_key"])
        self.assertNotIn(
            "secret", schema["video_generation_config"]["items"]["api_keys"]
        )

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
            for name in re.findall(
                r"(?<![A-Za-z0-9_$])el\.([A-Za-z_$][A-Za-z0-9_$]*)", app
            )
            if name not in {"append", "htmlFor", "textContent"}
        }
        mapped_refs = set(re.findall(r"\n\s*([A-Za-z_$][A-Za-z0-9_$]*):", el_block))
        mapped_ids = set(re.findall(r'byId\("([^"]+)"\)', el_block))

        self.assertIn("const byId = (id) => document.getElementById(id);", app)
        self.assertIn(
            "const all = (selector) => Array.from(document.querySelectorAll(selector));",
            app,
        )
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
        self.assertIn(
            'window.addEventListener("pointermove", handleCursorMove', effects
        )
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
        self.assertIn('aria-label="刷新今日天气与实时状态"', html)
        self.assertIn('title="刷新今日天气与实时状态"', html)
        self.assertLess(
            html.index('id="refreshStateButton"'), html.index('id="targetDate"')
        )
        self.assertIn('refreshStateButton: byId("refreshStateButton")', app)
        self.assertIn('apiPost(\n      "page/action/refresh-state"', app)
        self.assertIn('"天气与实时状态已刷新"', app)
        self.assertIn('"实时状态已刷新，天气保持当前数据"', app)
        self.assertIn('"实时状态已刷新，天气暂不可用"', app)
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
        self.assertIn(
            'class="view-button bento-ribbon-button bento-ribbon-toggle dashboard-toggle active"',
            html,
        )
        self.assertIn(
            'class="view-button bento-ribbon-button bento-ribbon-toggle emoji-toggle"',
            html,
        )
        self.assertIn(
            'class="view-button bento-ribbon-button bento-ribbon-toggle settings-toggle"',
            html,
        )
        self.assertIn('class="page-stickers"', html)
        self.assertIn('class="life-layout"', html)
        self.assertIn('class="panel today-panel"', html)
        self.assertIn('class="panel current-panel"', html)
        self.assertIn('class="panel timeline-panel"', html)
        self.assertIn('class="panel state-log-panel"', html)
        self.assertIn('class="life-column life-column-side"', html)
        self.assertIn('class="panel memory-panel"', html)
        self.assertIn('class="panel domain-panel"', html)
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
        side_start = html.index('class="life-column life-column-side"')
        side_end = html.index("</aside>", side_start)
        side_markup = html[side_start:side_end]
        self.assertLess(
            side_markup.index('class="panel memory-panel"'),
            side_markup.index('class="panel domain-panel"'),
        )
        self.assertNotIn('id="contextDrawerToggle"', html)
        self.assertNotIn('id="contextDrawerScrim"', html)
        self.assertNotIn('id="contextDrawer"', html)
        self.assertNotIn('id="contextDrawerClose"', html)
        self.assertNotIn("data-context-tab=", html)
        self.assertNotIn("data-context-panel=", html)
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
        self.assertIn('role="tab" aria-selected="true"', html)
        self.assertIn('role="tabpanel" aria-labelledby="memoryTabWorld"', html)
        self.assertIn('aria-controls="worldList" tabindex="0"', html)
        self.assertIn('aria-controls="experienceList" tabindex="-1"', html)
        self.assertIn('worldTab: "life_decisions"', app)
        self.assertIn('experienceTab: "relationships"', app)
        self.assertIn('memoryTab: "world"', app)
        self.assertNotIn("contextDrawerOpen", app)
        self.assertNotIn('contextTab: "world"', app)
        self.assertIn("function renderWorld", app)
        self.assertIn("function renderMemoryPanel", app)
        self.assertIn("function syncTabSelection", app)
        self.assertIn("function bindRovingTabs", app)
        self.assertIn('event.key === "ArrowRight"', app)
        self.assertIn('event.key === "ArrowLeft"', app)
        self.assertIn('event.key === "Home"', app)
        self.assertIn('event.key === "End"', app)
        self.assertIn("tab.tabIndex = active ? 0 : -1;", app)
        self.assertIn('state.memoryTab = tab.dataset.memoryTab || "world";', app)
        self.assertIn("renderMemoryPanel();", app)
        self.assertNotIn("function contextSummaryRecord", app)
        self.assertNotIn("function renderContextRecordList", app)
        self.assertNotIn("function openContextDetail", app)
        self.assertNotIn("function closeContextDetail", app)
        self.assertNotIn("function renderContextDrawer", app)
        self.assertNotIn("function setContextDrawer", app)
        self.assertNotIn("function setContextTab", app)
        self.assertLess(
            html.index('class="panel today-panel"'),
            html.index('class="panel current-panel"'),
        )
        self.assertLess(
            html.index('class="panel current-panel"'),
            html.index('class="panel timeline-panel"'),
        )
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
        self.assertIn(".domain-head", style)
        self.assertIn("flex-wrap: wrap;", style)
        self.assertNotIn("font-size: clamp(18px, 1.8vw", style)
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
        self.assertIn("emojiItems: []", app)
        self.assertIn("EMOJI_PAGE_SIZE = 30", app)
        self.assertNotIn("EMOJI_DEFAULT_PAGE_SIZE", app)
        self.assertIn("function emojiPageWindow", app)
        self.assertIn("function renderEmojiPager", app)
        self.assertIn("state.emojiPage = 1", app)
        self.assertIn("place-items: center;", emoji_style)
        self.assertIn("text-align: center;", emoji_style)
        self.assertIn(
            "grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));", emoji_style
        )
        self.assertNotIn(
            "grid-template-columns: repeat(auto-fill, minmax(146px, 1fr));", emoji_style
        )
        self.assertIn('apiGet("page/emoji/list"', app)
        self.assertIn('apiUpload("page/emoji/import"', app)
        self.assertIn('apiPost("page/emoji/preview"', app)
        self.assertIn('apiDownload("page/emoji/backup"', app)
        self.assertIn('apiUpload("page/emoji/restore"', app)
        self.assertIn("loadEmojiPreview(preview, item.id, { still: true })", app)
        self.assertIn("loadEmojiPreview(preview, item.id, { still: false })", app)
        self.assertIn("function observeEmojiAnimatedPreview", app)
        self.assertIn("function scheduleEmojiAnimatedPreview", app)
        self.assertIn("IntersectionObserver", app)
        self.assertIn("loadEmojiPreview(img, id, { still: true })", app)
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
        self.assertIn(
            'return item.status === "ready" && item.sendable ? "✔️" : "❌";', app
        )
        self.assertIn("function renderEmojiDetailDialog", app)
        self.assertIn("function openEmojiDetail", app)
        self.assertIn("function openEmojiImport", app)
        self.assertIn("function importEmojiFiles", app)
        self.assertNotIn("function importEmojiUrl", app)
        self.assertNotIn("emojiImportUrl", app)
        self.assertIn("const EMOJI_IMPORT_MAX_MB = 20;", app)
        self.assertIn(
            "const EMOJI_IMPORT_MAX_BYTES = EMOJI_IMPORT_MAX_MB * 1024 * 1024;", app
        )
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
        self.assertIn(".emoji-thumb:focus-visible", style)
        self.assertIn("outline: 3px solid rgba(148, 218, 242, 0.64);", style)
        self.assertNotIn(
            ".emoji-thumb:focus-visible {\n  border-color: rgba(223, 95, 149, 0.58);\n  outline: none;",
            style,
        )
        self.assertIn(".emoji-layout", style)
        self.assertIn(".emoji-panel", style)
        self.assertIn(".emoji-head", style)
        self.assertIn(".emoji-tools", style)
        self.assertIn(".emoji-action-tools", style)
        self.assertIn(".emoji-filter-tools", style)
        self.assertIn("grid-template-columns: minmax(0, 1fr) max-content;", style)
        self.assertIn(
            "grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));", style
        )
        self.assertIn("justify-content: stretch;", style)
        self.assertIn(
            ".emoji-tools {\n    grid-template-columns: minmax(0, 1fr);", style
        )
        self.assertIn(".emoji-action-tools {\n    display: grid;", style)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr));", style)
        self.assertIn(".emoji-filter-tools {\n    display: grid;", style)
        self.assertIn(
            "grid-template-columns: minmax(118px, 0.82fr) minmax(0, 1.18fr);", style
        )
        self.assertIn(
            ".emoji-filter-tools .life-select-filter {\n    min-width: 0;", style
        )
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
        self.assertIn(
            ".emoji-import-card {\n  width: min(360px, calc(100vw - 28px));\n}", style
        )
        self.assertNotIn(".emoji-import-url", style)
        self.assertIn(
            "grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));", style
        )
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
        self.assertIn("<span data-action-label>重生</span>", html)
        self.assertIn('id="timelineCancelButton" type="button" hidden', html)
        self.assertIn(
            'id="timelineSaveButton" type="button" class="primary-button" hidden', html
        )
        self.assertIn(
            "el.timelineEditButton.hidden = !hasDay || state.timelineEditing;", app
        )
        self.assertIn(
            "el.timelineAddButton.hidden = !hasDay || !state.timelineEditing;", app
        )
        self.assertIn(
            "el.timelineCancelButton.hidden = !hasDay || !state.timelineEditing;", app
        )
        self.assertIn(
            "el.timelineSaveButton.hidden = !hasDay || !state.timelineEditing;", app
        )
        self.assertIn(
            'pendingMessage: useWeb\n        ? "正在联网重新安排今天的时间轴和生活状态，请稍等"',
            app,
        )
        self.assertIn('busyLabel: "重生中…"', app)
        self.assertIn("generationRunningIds: new Set(),", app)
        self.assertIn("function syncDailyGenerationButton(status = {})", app)
        self.assertIn('button.dataset.lockDisabled = "true";', app)
        self.assertIn("syncDailyGenerationButton(status);", app)
        self.assertIn("state.generationRunningIds.has(nextId)", app)
        self.assertIn('setNotice(pendingMessage, "info")', app)
        self.assertIn("const NOTICE_HIDE_MS = 4200;", app)
        self.assertIn(
            'id="notice" class="notice" role="status" aria-live="polite"', html
        )
        self.assertNotIn(".notice.persistent {", style)
        self.assertNotIn("persistent ?", app)
        self.assertIn(
            "grid-template-columns: minmax(126px, 0.24fr) minmax(0, 1fr);", style
        )
        self.assertIn("font-variant-numeric: tabular-nums;", style)
        self.assertIn("inset: 0 1px auto;", style)
        self.assertIn(".timeline-edit-row::before {\n  content: none;\n}", style)
        self.assertNotIn("grid-template-columns: 96px minmax(0, 1fr);", style)

    def test_dashboard_dark_mode_keeps_text_readable(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        style = self._dashboard_style(root)

        self.assertIn('[data-theme="dark"] .emoji-detail-card', style)
        self.assertIn(
            '[data-theme="dark"] .emoji-detail-card .record-line-value', style
        )
        self.assertIn('[data-theme="dark"] .timeline-item', style)
        self.assertIn('[data-theme="dark"] .memory-tabs', style)
        self.assertIn('[data-theme="dark"] .emoji-pager', style)
        self.assertIn('[data-theme="dark"] .empty', style)
        self.assertIn("background: rgba(255, 246, 251, 0.08);", style)
        self.assertIn('[data-theme="dark"] #targetDate', style)
        self.assertIn("color: var(--diary-strawberry);", style)
        self.assertIn("--life-dark-text: #fff6fb;", style)
        self.assertIn("--life-dark-card: rgba(255, 246, 251, 0.105);", style)

    def test_dashboard_groups_model_provider_settings(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        dashboard = root / "pages" / "dashboard"
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (dashboard / "ui" / "settings.js").read_text(encoding="utf-8")
        style = self._dashboard_style(dashboard)

        self.assertIn('const MODEL_SECTION_KEY = "__model_provider_settings"', config)
        self.assertIn('description: "大语言模型"', config)
        self.assertIn("图片轻量润色", config)
        self.assertIn("function collectProviderConfigFields()", config)
        self.assertIn('spec._special === "select_provider"', config)
        self.assertIn('spec._special === "select_provider_embedding"', config)
        self.assertIn("function providerConfigKind", config)
        self.assertIn('provider.kind || "chat"', config)
        self.assertIn("function configSectionVisibleFields", config)
        self.assertIn("schemaViewCache", config)
        self.assertIn("buildConfigSchemaView", config)
        self.assertIn("renderModelConfigField", config)
        self.assertIn("config-source-label", config)
        self.assertIn(".config-source-label", style)
        self.assertIn(
            "const modelLabel = configLabel(field.fieldKey, field.spec);", config
        )
        self.assertNotIn("const sectionLabel = configLabel(field.sectionKey", config)
        self.assertNotIn("field-type", config)
        self.assertNotIn(".field-type", style)
        self.assertNotIn("configTypeLabel", config)
        self.assertEqual(
            schema["image_generation_config"]["items"]["prompt_rewrite_provider"][
                "_special"
            ],
            "select_provider",
        )
        self.assertEqual(
            schema["memory_config"]["items"]["embedding_provider"]["_special"],
            "select_provider_embedding",
        )

    def test_dashboard_config_renders_template_list_fields(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        config = (root / "ui" / "settings.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn('spec.type === "template_list"', config)
        self.assertIn("renderConfigTemplateList", config)
        self.assertIn("templateEntries", config)
        self.assertIn("normalizeTemplateListItem", config)
        self.assertIn("reorderItem", config)
        self.assertIn("templateListDropIndex", config)
        self.assertIn("dragState.itemCenters", config)
        self.assertIn("moveEvent.pointerId !== dragState?.pointerId", config)
        self.assertIn("pointerdown", config)
        self.assertIn("pointermove", config)
        self.assertIn("animateListFrom", config)
        self.assertIn("IMAGE_CHANNEL_LIST_PATHS", config)
        self.assertIn('node("div", "template-list-item-inline")', config)
        self.assertIn("row.append(dragButton, body, removeButton)", config)
        self.assertIn('templateField.dataset.templateField = "__template_key"', config)
        self.assertIn("label.dataset.templateField = fieldKey", config)
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
        self.assertIn("touch-action: none;", style)
        self.assertIn(".template-list-item.is-drop-target", style)
        self.assertIn(".template-list-item.is-shifting", style)
        self.assertIn("will-change: transform", style)
        self.assertIn(".template-list-item-grid", style)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", style)
        self.assertIn(".template-list-control-compact .template-list-items", style)
        self.assertIn(
            ".template-list-control-compact .template-list-items {\n"
            "  min-width: 0;\n"
            "  max-width: 100%;\n"
            "}",
            style,
        )
        self.assertIn(".template-list-item-inline", style)
        self.assertIn("minmax(0, 1.45fr)", style)
        self.assertIn("minmax(54px, 72px)", style)
        self.assertIn("minmax(58px, 76px)", style)
        self.assertIn("minmax(68px, 80px)", style)
        self.assertIn(".template-list-item-compact :is(input, select, textarea)", style)
        self.assertIn(".template-list-item-compact .life-select-trigger", style)
        self.assertIn("overflow-x: hidden", style)
        self.assertIn("@media (min-width: 681px)", style)
        self.assertIn("width: 120px;\n    min-width: 100%;", style)
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr))", style)
        self.assertIn("grid-column: 1 / -1;\n    grid-row: 2;", style)
        self.assertIn('[data-template-field="api_url"]', style)
        self.assertIn('[data-template-field="resolution"]', style)
        self.assertNotIn("min-width: 1420px", style)

    def test_dashboard_template_list_drop_index_uses_stable_item_centers(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = """
globalThis.window = { AstrBotPluginPage: null };
const { templateListDropIndex } = await import("./pages/dashboard/ui/settings.js");
const centers = [100, 200, 300];
const cases = [
  [50, 1, 0],
  [150, 1, 1],
  [350, 1, 2],
  [150, 2, 1],
  [250, 0, 1],
  [350, 0, 2],
];
for (const [pointerY, fromIndex, expected] of cases) {
  const actual = templateListDropIndex(pointerY, centers, fromIndex);
  if (actual !== expected) {
    throw new Error(`落点计算错误：${pointerY}/${fromIndex} -> ${actual}，预期 ${expected}`);
  }
}
"""
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_mobile_sliders_do_not_capture_vertical_scroll(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        config = (root / "ui" / "settings.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn(
            'const pointerGuard = node("span", "range-pointer-guard");',
            config,
        )
        self.assertIn(
            'pointerGesture.intent = deltaX > deltaY * 1.25 ? "adjust" : "scroll";',
            config,
        )
        self.assertIn('if (pointerGesture.intent !== "adjust") return;', config)
        self.assertIn('pointerGuard.addEventListener("pointercancel"', config)
        self.assertIn("wrap.append(slider, pointerGuard, number);", config)
        self.assertNotIn('slider.addEventListener("pointerdown"', config)
        self.assertIn('.number-line.has-range > input[type="range"]', style)
        self.assertIn("pointer-events: none;", style)
        self.assertIn(".range-pointer-guard", style)
        self.assertIn("touch-action: pan-y;", style)

    def test_dashboard_slider_changes_only_after_horizontal_touch_intent(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = (
            self._dashboard_dom_mock_script()
            + """
const { createConfigPanel } = await import("./pages/dashboard/ui/settings.js");
const makeNode = (tag, className = "", content = "") => {
  const item = new MockElement(tag);
  item.className = className;
  item.textContent = content === null || content === undefined ? "" : String(content);
  return item;
};
const state = {
  configSectionKey: "test_config",
  configSchema: {
    test_config: {
      description: "测试设置",
      type: "object",
      items: {
        level: {
          description: "测试数值",
          type: "int",
          default: 50,
          slider: { min: 0, max: 100, step: 1 },
        },
      },
    },
  },
  config: { test_config: { level: 50 } },
  providers: [],
  relationships: [],
  configLoaded: false,
  configDirty: false,
  configDirtySince: 0,
  configVersion: 0,
  configChangeSeq: 0,
  configSaveTimer: 0,
};
const el = {
  configNav: new MockElement("nav"),
  configSectionTitle: new MockElement("h2"),
  configSectionHint: new MockElement("p"),
  configFieldList: new MockElement("div"),
};
const panel = createConfigPanel({
  state,
  el,
  node: makeNode,
  empty: (message) => makeNode("div", "empty", message),
  setBusy() {},
  setNotice() {},
  async loadStatus() {},
});
panel.renderConfig();

const descendants = [];
const visit = (item) => {
  descendants.push(item);
  for (const child of item.children || []) visit(child);
};
visit(el.configFieldList);
const slider = descendants.find((item) => item.tagName === "INPUT" && item.type === "range");
const number = descendants.find((item) => item.tagName === "INPUT" && item.type === "number");
const guard = descendants.find((item) => item.className === "range-pointer-guard");
if (!slider || !number || !guard) throw new Error("滑块控件没有完整渲染");

guard.dispatch("pointerdown", { pointerId: 1, pointerType: "touch", clientX: 80, clientY: 10 });
guard.dispatch("pointermove", { pointerId: 1, pointerType: "touch", clientX: 82, clientY: 42 });
guard.dispatch("pointerup", { pointerId: 1, pointerType: "touch", clientX: 82, clientY: 42 });
if (slider.value !== "50" || number.value !== "50" || state.config.test_config.level !== 50) {
  throw new Error("纵向滚动误改了滑块数值");
}

guard.dispatch("pointerdown", { pointerId: 2, pointerType: "touch", clientX: 50, clientY: 10 });
guard.dispatch("pointermove", { pointerId: 2, pointerType: "touch", clientX: 80, clientY: 12 });
if (slider.value !== "80" || number.value !== "80" || state.config.test_config.level !== 50) {
  throw new Error("横向拖动预览或延迟提交不正确");
}
guard.dispatch("pointerup", { pointerId: 2, pointerType: "touch", clientX: 80, clientY: 12 });
if (state.config.test_config.level !== 80) throw new Error("横向拖动没有提交数值");

guard.dispatch("pointerdown", { pointerId: 3, pointerType: "touch", clientX: 80, clientY: 10 });
guard.dispatch("pointermove", { pointerId: 3, pointerType: "touch", clientX: 95, clientY: 11 });
guard.dispatch("pointercancel", { pointerId: 3, pointerType: "touch", clientX: 95, clientY: 11 });
if (slider.value !== "80" || number.value !== "80" || state.config.test_config.level !== 80) {
  throw new Error("取消的手势没有恢复原值");
}

slider.value = "60";
slider.dispatch("input");
if (number.value !== "60" || state.config.test_config.level !== 60) {
  throw new Error("键盘调整滑块失效");
}
"""
        )
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_settings_use_explicit_section_order(self):
        from pathlib import Path

        config = (
            Path(__file__).resolve().parents[1]
            / "pages"
            / "dashboard"
            / "ui"
            / "settings.js"
        ).read_text(encoding="utf-8")

        self.assertIn("const CONFIG_SECTION_ORDER = [", config)
        self.assertIn("const CONFIG_SECTION_ORDER_INDEX = new Map", config)
        self.assertIn("function sortConfigSectionEntries(entries = [])", config)
        self.assertIn("function visibleConfigSections()", config)
        self.assertIn("const visibleSchemaEntries = visibleConfigSections();", config)

        expected_order = [
            '"rhythm_config"',
            '"life_domain_config"',
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
            '"search_config"',
            '"storage_config"',
            '"story_engine_config"',
        ]
        positions = [config.index(token) for token in expected_order]
        self.assertEqual(positions, sorted(positions))

    def test_dashboard_merges_sparse_settings_sections(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )

        self.assertEqual(schema["rhythm_config"]["description"], "生活背景")
        self.assertEqual(schema["state_config"]["description"], "生活感知")
        self.assertEqual(schema["memory_config"]["description"], "关系与记忆")
        self.assertEqual(schema["chat_style_config"]["description"], "聊天表达")
        self.assertEqual(schema["video_generation_config"]["description"], "视频")
        self.assertNotIn("default_city", schema["weather_awareness"]["items"])
        self.assertIn("home_address", schema["life_domain_config"]["items"])
        self.assertIn("天气环境", schema["rhythm_config"]["hint"])
        self.assertIn("实时状态", schema["rhythm_config"]["hint"])
        self.assertIn("天气环境", schema["state_config"]["hint"])
        self.assertIn("随心回复", schema["chat_style_config"]["hint"])
        self.assertIn("闲时回复", schema["chat_style_config"]["hint"])
        self.assertIn("视频理解", schema["video_generation_config"]["hint"])
        self.assertIn("const CONFIG_SECTION_DISPLAY_SECTIONS = new Map", config)
        self.assertIn('["weather_awareness", "rhythm_config"]', config)
        self.assertIn('["state_config", "rhythm_config"]', config)
        self.assertIn('["lifecycle_config", "rhythm_config"]', config)
        self.assertNotIn('["search_config", "rhythm_config"]', config)
        self.assertIn('["relationship_aliases", "memory_config"]', config)
        self.assertIn('["bot_identity_aliases", "memory_config"]', config)
        self.assertIn('["commitment_config", "memory_config"]', config)
        self.assertIn('["memos_config", "memory_config"]', config)
        self.assertIn('["response_gate_config", "chat_style_config"]', config)
        self.assertIn('["proactive_config", "chat_style_config"]', config)
        self.assertIn('["sight_config", "video_generation_config"]', config)
        self.assertIn('["relationship_aliases", "identity_aliases"]', config)
        self.assertIn('["bot_identity_aliases", "identity_aliases"]', config)
        self.assertIn(
            'const CONFIG_GROUPED_DISPLAY_SECTIONS = new Set(["rhythm_config", "memory_config", "chat_style_config", "video_generation_config"]);',
            config,
        )
        self.assertIn('description: "基础生成"', config)
        self.assertIn('description: "天气环境"', config)
        self.assertIn('description: "实时状态"', config)
        self.assertIn('description: "生活演化"', config)
        self.assertIn('description: "称呼与身份"', config)
        self.assertIn('description: "记忆沉淀"', config)
        self.assertIn('description: "MemOS 外部记忆"', config)
        self.assertIn('description: "聊天表达"', config)
        self.assertNotIn("structured_reply_config", config)
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
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )

        self.assertEqual(schema["storage_config"]["description"], "数据管理")
        for key in (
            "video_cache_ttl_hours",
            "video_cache_max_items",
            "sight_cache_keep_days",
        ):
            self.assertIn(key, schema["sight_config"]["items"])
            self.assertNotIn(key, schema["storage_config"]["items"])
        self.assertIn(f'["sight_config.{key}", "storage_config"]', config)
        self.assertIn("CONFIG_FIELD_DISPLAY_SECTIONS", config)
        self.assertIn(
            "addConfigViewField(fieldsBySection, displaySection, field)", config
        )
        self.assertIn("displaySection === sectionKey", config)
        self.assertIn("explicitPath", config)

    def test_dashboard_merges_people_reference_into_image_generation_group(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )
        image_groups = config.split('["image_generation_config", [', 1)[1].split(
            '["storage_config", [', 1
        )[0]

        self.assertIn('label: "图片生成"', image_groups)
        self.assertNotIn('label: "图片能力"', image_groups)
        self.assertNotIn('label: "人物参考"', image_groups)
        generation_group = image_groups.split('key: "general"', 1)[1].split(
            'key: "channels"', 1
        )[0]
        for field in (
            "image_generation_config.enabled",
            "image_generation_config.prompt_rewrite_provider",
            "image_generation_config.character_reference_policy",
            "image_generation_config.character_reference_images",
            "image_generation_config.photo_suite_planning_timeout_seconds",
            "image_generation_config.friend_reference_profiles",
        ):
            self.assertIn(field, generation_group)
        self.assertLess(
            generation_group.index(
                "image_generation_config.character_reference_images"
            ),
            generation_group.index(
                "image_generation_config.photo_suite_planning_timeout_seconds"
            ),
        )
        self.assertLess(
            generation_group.index(
                "image_generation_config.photo_suite_planning_timeout_seconds"
            ),
            generation_group.index("image_generation_config.friend_reference_profiles"),
        )
        self.assertNotIn(
            "image_generation_config.reference_max_count", generation_group
        )
        self.assertIn("const MAX_CHARACTER_REFERENCE_IMAGES = 6;", config)
        self.assertIn("return MAX_CHARACTER_REFERENCE_IMAGES;", config)

    def test_dashboard_groups_data_management_by_lifecycle(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        dashboard = root / "pages" / "dashboard"
        config = (dashboard / "ui" / "settings.js").read_text(encoding="utf-8")
        style = self._dashboard_style(dashboard)

        self.assertIn("const CONFIG_SECTION_FIELD_GROUPS = new Map", config)
        self.assertIn('["storage_config", [', config)
        group_source = config[
            config.index("const CONFIG_SECTION_FIELD_GROUPS") : config.index(
                "const CONFIG_GROUPED_DISPLAY_SECTIONS"
            )
        ]
        expected_groups = ["生活记录", "关系与记忆", "对话与表达", "媒体与缓存"]
        group_positions = [
            group_source.index(f'label: "{label}"') for label in expected_groups
        ]
        self.assertEqual(group_positions, sorted(group_positions))

        expected_fields = [
            "storage_config.daily_keep_days",
            "storage_config.review_keep_days",
            "storage_config.planning_keep_days",
            "storage_config.relationships_keep_days",
            "storage_config.world_keep_days",
            "storage_config.longterm_keep_days",
            "storage_config.conversation_keep_days",
            "storage_config.experience_keep_days",
            "storage_config.expression_keep_days",
            "storage_config.media_keep_days",
            "sight_config.video_cache_ttl_hours",
            "sight_config.video_cache_max_items",
            "sight_config.sight_cache_keep_days",
            "storage_config.generated_media_keep_days",
            "storage_config.reverse_cache_keep_days",
        ]
        field_positions = [group_source.index(f'"{path}"') for path in expected_fields]
        self.assertEqual(field_positions, sorted(field_positions))
        self.assertIn("function applyConfigFieldGroups(fieldsBySection)", config)
        self.assertIn("applyConfigFieldGroups(fieldsBySection);", config)
        self.assertIn('const group = node("div", "config-field-group");', config)
        self.assertNotIn("config-field-subsection", config)
        self.assertNotIn(".config-field-subsection", style)

    def test_dashboard_groups_life_domain_settings_by_workflow(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )
        life_groups = config.split('["life_domain_config", [', 1)[1].split(
            '["chat_style_config", [', 1
        )[0]

        expected_groups = [
            "基础与结算",
            "地点与出行",
            "生活记录",
            "行动项与上下文",
        ]
        group_positions = [
            life_groups.index(f'label: "{label}"') for label in expected_groups
        ]
        self.assertEqual(group_positions, sorted(group_positions))

        expected_fields = [
            f"life_domain_config.{field_key}"
            for field_key in schema["life_domain_config"]["items"]
        ]
        field_positions = [life_groups.index(f'"{path}"') for path in expected_fields]
        self.assertEqual(field_positions, sorted(field_positions))

        for path in expected_fields:
            self.assertEqual(life_groups.count(f'"{path}"'), 1)

    def test_dashboard_groups_reply_settings_by_workflow(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )
        chat_groups = config.split('["chat_style_config", [', 1)[1].split(
            '["image_generation_config", [', 1
        )[0]

        expected_groups = [
            "基础节奏",
            "连续话轮",
            "语义与发送",
            "标点处理",
            "随心回复",
            "闲时回复",
            "私聊回访",
        ]
        group_positions = [
            chat_groups.index(f'label: "{label}"') for label in expected_groups
        ]
        self.assertEqual(group_positions, sorted(group_positions))

        for section_key in (
            "chat_style_config",
            "response_gate_config",
            "proactive_config",
        ):
            for field_key, field_spec in schema[section_key]["items"].items():
                if field_spec.get("_special") == "select_provider":
                    continue
                if (section_key, field_key) == (
                    "chat_style_config",
                    "casual_short_prompt",
                ):
                    continue
                self.assertIn(f'"{section_key}.{field_key}"', chat_groups)
        self.assertNotIn('"response_gate_config.enabled"', chat_groups)
        self.assertNotIn('"proactive_config.enabled"', chat_groups)

    def test_dashboard_moves_chat_style_prompts_to_prompt_settings(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )

        self.assertEqual(schema["story_engine_config"]["description"], "提示词")
        self.assertIn("casual_short_prompt", schema["chat_style_config"]["items"])
        self.assertNotIn(
            "fact_check_query_prompt", schema["chat_style_config"]["items"]
        )
        self.assertNotIn("casual_short_prompt", schema["story_engine_config"]["items"])
        self.assertNotIn(
            "fact_check_query_prompt", schema["story_engine_config"]["items"]
        )
        self.assertIn(
            '["chat_style_config.casual_short_prompt", "story_engine_config"]', config
        )
        self.assertNotIn(
            '["chat_style_config.fact_check_query_prompt", "story_engine_config"]',
            config,
        )

    def test_dashboard_moves_daily_inspiration_template_to_prompt_settings(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("today_prompt", schema["search_config"]["items"])
        self.assertNotIn("today_prompt", schema["story_engine_config"]["items"])
        self.assertIn("联网灵感查询", schema["story_engine_config"]["hint"])
        prompt_spec = schema["search_config"]["items"]["today_prompt"]
        for variable in ("keyword", "category", "date", "persona", "today"):
            self.assertIn(f"{{{variable}}}", prompt_spec["default"])
            self.assertIn(f"{{{variable}}}", prompt_spec["hint"])
        self.assertIn('["search_config.today_prompt", "story_engine_config"]', config)
        order_start = config.index(
            '["story_engine_config", [',
            config.index("const CONFIG_SECTION_FIELD_ORDER"),
        )
        order_end = config.index("]],", order_start)
        order_source = config[order_start:order_end]
        self.assertIn('"search_config.today_prompt"', order_source)

    def test_dashboard_moves_outfit_prompt_settings_to_prompt_settings(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )

        self.assertEqual(schema["outfit_config"]["description"], "服装")
        self.assertEqual(schema["story_engine_config"]["description"], "提示词")
        self.assertIn("穿搭审美", schema["story_engine_config"]["hint"])
        self.assertIn("发型审美", schema["story_engine_config"]["hint"])
        self.assertIn("default_style_preference", schema["outfit_config"]["items"])
        self.assertIn("default_hair_preference", schema["outfit_config"]["items"])
        self.assertNotIn(
            "default_style_preference", schema["story_engine_config"]["items"]
        )
        self.assertNotIn(
            "default_hair_preference", schema["story_engine_config"]["items"]
        )
        self.assertIn(
            '["outfit_config.default_style_preference", "story_engine_config"]', config
        )
        self.assertIn(
            '["outfit_config.default_hair_preference", "story_engine_config"]', config
        )
        self.assertIn(
            '["outfit_config.default_preference_weight", "rhythm_config"]', config
        )
        self.assertNotIn('["outfit_config", "story_engine_config"]', config)

        self.assertIn("const CONFIG_SECTION_FIELD_ORDER = new Map", config)
        self.assertIn("function applyConfigFieldOrder(fieldsBySection)", config)
        self.assertIn("applyConfigFieldOrder(fieldsBySection);", config)
        order_start = config.index(
            '["story_engine_config", [',
            config.index("const CONFIG_SECTION_FIELD_ORDER"),
        )
        order_end = config.index("]],", order_start)
        order_source = config[order_start:order_end]
        expected_order = [
            "story_engine_config.state_rules",
            "story_engine_config.timeline_rules",
            "story_engine_config.world_rules",
            "story_engine_config.chat_rules",
            "outfit_config.default_style_preference",
            "outfit_config.default_hair_preference",
            "chat_style_config.casual_short_prompt",
        ]
        positions = [order_source.index(f'"{path}"') for path in expected_order]
        self.assertEqual(positions, sorted(positions))

    def test_dashboard_no_longer_exposes_segment_pattern_setting(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )

        self.assertNotIn(
            "natural_segment_pattern", schema["chat_style_config"]["items"]
        )
        self.assertNotIn("natural_segment_pattern", config)
        self.assertIn("spec.multiline === false", config)

    def test_dashboard_no_longer_exposes_weekly_theme_config(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "_conf_schema.json").read_text(encoding="utf-8-sig")
        )
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("weekly_theme_config", schema)
        self.assertNotIn('"weekly_theme_config"', config)

    def test_dashboard_settings_auto_save_config(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        config = (root / "ui" / "settings.js").read_text(encoding="utf-8")
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

    def test_dashboard_stops_visual_timers_when_hidden_or_unloaded(self):
        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        app = (root / "app.js").read_text(encoding="utf-8")

        self.assertIn("function stopClock()", app)
        self.assertIn('document.hidden || state.view !== "dashboard"', app)
        self.assertIn('window.addEventListener("pagehide"', app)
        self.assertIn("stopClock();", app)
        self.assertIn("stopMemoCarousel();", app)

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
        config = (root / "ui" / "settings.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertIn('classes.push("text-field")', config)
        self.assertIn("isPromptTextField", config)
        self.assertIn('classes.push("prompt-field")', config)
        self.assertIn("promptText ? 8 : 5", config)
        self.assertIn(
            "grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));", style
        )
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
        for programmatic_endpoint in (
            "page/experience/episode/correct",
            "page/experience/episode/protect",
            "page/experience/focus",
            "page/experience/boundary",
            "page/experience/feedback",
        ):
            self.assertNotIn(f'"{programmatic_endpoint}"', app)
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

        html = (
            Path(__file__).resolve().parents[1] / "pages" / "dashboard" / "index.html"
        ).read_text(encoding="utf-8")

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
            / "settings.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function configOptionLabel(option, spec = {})", config)
        self.assertIn("const labels = spec.option_labels || {};", config)
        self.assertIn("configOptionLabel(option, spec)", config)

    def test_dashboard_boolean_fields_only_toggle_from_checkbox(self):
        from pathlib import Path

        config = (
            Path(__file__).resolve().parents[1]
            / "pages"
            / "dashboard"
            / "ui"
            / "settings.js"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'node(spec.type === "bool" ? "div" : "label", "field-title")', config
        )
        self.assertIn(
            'node(field.spec.type === "bool" ? "div" : "label", "field-title")',
            config,
        )
        self.assertIn(
            'node(fieldSpec.type === "bool" ? "div" : "label", "template-list-subfield")',
            config,
        )
        self.assertIn('if (spec.type !== "bool") label.htmlFor', config)
        self.assertIn('if (field.spec.type !== "bool") title.htmlFor', config)
        self.assertIn('if (fieldSpec.type !== "bool") label.htmlFor', config)
        self.assertIn('input.setAttribute("aria-label"', config)

    def test_dashboard_config_supports_character_reference_upload(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )
        style = self._dashboard_style(root)
        schema = (root / "_conf_schema.json").read_text(encoding="utf-8")

        self.assertIn('"_special": "character_reference_gallery"', schema)
        self.assertIn('apiUpload("page/config/character-reference"', config)
        self.assertIn('apiPost("page/config/character-reference/preview"', config)
        self.assertIn('apiPost("page/config/character-reference/delete"', config)
        self.assertIn("function renderCharacterReferenceGallery(path, value)", config)
        self.assertIn("function renderImageGallery(spec, path, value)", config)
        self.assertIn(
            'fileInput.accept = "image/png,image/jpeg,image/webp,image/gif"', config
        )
        self.assertIn("fileInput.multiple = true;", config)
        self.assertIn("referenceItemsForConfig(items)", config)
        self.assertIn("reference-gallery-preview", config)
        self.assertIn("reference-gallery-thumb", config)
        self.assertIn("const referencePreviewCache = new Map();", config)
        self.assertIn("function cachedReferencePreview(path)", config)
        self.assertIn("setReferencePreviewCache(item.path, result.data_url);", config)
        show_preview_body = config.split("function showReferencePreview", 1)[1].split(
            "async function loadReferencePreview", 1
        )[0]
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
        self.assertIn('<svg viewBox="0 0 24 24"', config)
        self.assertNotIn('reference-gallery-remove", "×"', config)
        self.assertIn("const CHARACTER_REFERENCE_MAX_MB = 12;", config)
        self.assertIn("超过 ${CHARACTER_REFERENCE_MAX_MB} MB 上限，未上传", config)
        self.assertIn("成功上传 ${uploadedCount} 张", config)
        self.assertNotIn('setNotice(`${label}已更新`, "success");', config)
        self.assertIn(".reference-gallery-preview", style)

    def test_dashboard_config_supports_friend_reference_profiles(self):
        root = Path(__file__).resolve().parents[1]
        config = (root / "pages" / "dashboard" / "ui" / "settings.js").read_text(
            encoding="utf-8"
        )
        style = self._dashboard_style(root)
        schema = (root / "_conf_schema.json").read_text(encoding="utf-8")

        self.assertIn('"_special": "friend_reference_profiles"', schema)
        self.assertNotIn('"character_appearance_profile"', schema)
        self.assertIn(
            "function renderFriendReferenceProfiles(path, value, onProfileStateChange = null)",
            config,
        )
        self.assertIn("page/config/friend-reference/${profileId}", config)
        self.assertIn(
            'gallery_label: `${profile.display_name || "好友"}参考图`', config
        )
        self.assertIn("MAX_FRIEND_REFERENCE_IMAGES = 3", config)
        self.assertNotIn("appearance_profile", config)
        self.assertNotIn('"稳定体貌"', config)
        self.assertNotIn("friend-reference-appearance", config)
        self.assertIn(".friend-reference-profile", style)
        self.assertNotIn(".friend-reference-appearance", style)
        self.assertIn(
            "grid-template-columns: repeat(auto-fill, minmax(min(100%, 280px), 1fr));",
            style,
        )
        self.assertIn(".friend-reference-list {\n  grid-template-columns:", style)
        self.assertIn("align-items: start;", style)
        self.assertIn(
            ".friend-reference-add {\n"
            "  grid-template-columns: minmax(0, 280px) auto;\n"
            "  justify-content: start;",
            style,
        )
        self.assertNotIn("外貌备注", schema)
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
        self.assertIn(
            ".config-field.reference-profile-field {\n  grid-column: auto;",
            style,
        )
        self.assertIn(
            ".config-field.reference-profile-field.has-reference-profiles {\n"
            "  grid-column: 1 / -1;",
            style,
        )
        self.assertIn(
            'field.classList.toggle("has-reference-profiles", hasProfiles)', config
        )
        self.assertIn("onProfileStateChange(profiles.length > 0)", config)
        self.assertNotIn(":has(.friend-reference-profile)", style)
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
        self.assertIn(
            "const total = reviews.length + preferences.length + events.length;", app
        )
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

    def test_dashboard_action_decisions_use_v109_compact_display(self):
        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        style = self._dashboard_style(root)

        self.assertNotIn("data-decision-filter", html)
        self.assertNotIn("decisionFilter", app)
        self.assertNotIn("decisionMatchesFilter", app)
        self.assertNotIn("function decisionRecord", app)
        self.assertNotIn("item.decision_category", app)
        self.assertNotIn("item.decision_source", app)
        self.assertNotIn("item.decision_stage", app)
        self.assertNotIn("item.decision_outcome", app)
        self.assertIn('enumLabel(item.action, ACTION_LABELS) || "未定"', app)
        self.assertIn("enumLabel(item.scene_type, SCENE_TYPE_LABELS)", app)
        self.assertIn("enumLabel(item.understanding, UNDERSTANDING_LABELS)", app)
        self.assertIn('relationshipText(item.reason) || "无裁定说明"', app)
        self.assertNotIn("decision-filter-tabs", style)
        self.assertNotIn(".decision-record", style)

    def test_dashboard_experience_tabs_group_long_records(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        relationships = (root / "shared" / "relationships.js").read_text(
            encoding="utf-8"
        )
        style = self._dashboard_style(root)

        tabs = re.findall(r'data-experience-tab="([^"]+)"', html)

        self.assertEqual(
            tabs,
            ["relationships", "behavior", "language", "evidence", "feedback"],
        )
        self.assertNotIn('data-experience-tab="decision"', html)
        self.assertIn("state.experienceTab = tab.dataset.experienceTab;", app)
        self.assertIn("renderExperience(state.status || {});", app)
        self.assertIn('experienceTab: "relationships"', app)
        self.assertIn("function experienceGroups(status)", app)
        self.assertIn(
            "const longTermMemories = objectItems(experience.long_term_memories);", app
        )
        self.assertIn('sourceTable === "chat_summaries"', app)
        self.assertIn('category === "chat_summary"', app)
        self.assertIn('fromChatSummary ? "来源：会话摘要" : ""', app)
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
        self.assertIn("暂无独立关系记忆；会话摘要会保留来源标注", app)
        self.assertIn("function relationshipNameIndex(status = {})", relationships)
        self.assertIn(
            "function relationshipScopeLabel(value, relationshipNames = new Map())",
            relationships,
        )
        self.assertIn("function relationshipTextResolver(status = {})", relationships)
        self.assertNotIn("firstPersonRelationshipText", relationships)
        self.assertIn("function addGroupScopeName(index, key, label)", relationships)
        self.assertIn(
            "function relationshipRecordLines(items, relationshipText)", relationships
        )
        self.assertIn('"group_profile",', relationships)
        self.assertIn(
            'item.scope ? ["范围", relationshipScopeLabel(item.scope, relationshipNames)] : ""',
            app,
        )
        self.assertIn(
            'item.evidence ? ["证据", relationshipText(evidenceText(item.evidence))] : ""',
            app,
        )
        self.assertIn(
            "recordLines([relationshipText(evidenceText(item.summary))])", app
        )
        self.assertIn("recordLines([clean(item.meaning)", app)
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
        self.assertNotIn('observationList: byId("observationList")', app)
        self.assertNotIn("function renderObservatory", app)
        self.assertIn("function lifeObservationRecords", app)
        self.assertIn('if (activeTab === "life_decisions")', app)
        self.assertIn("const relationship = relationshipTextResolver(status);", app)
        self.assertIn(
            'readableReferenceLabel(sender, "未知发送者")',
            app,
        )
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
        self.assertIn(
            '["依据", relationshipText(evidenceText(decision.evidence))]', app
        )
        self.assertIn('["来源", influenceSources]', app)
        self.assertNotIn("sourceParts", app)
        self.assertNotIn(
            "const sourceLabel = enumLabel(decision.source, SOURCE_LABELS)", app
        )
        self.assertIn('["安排", relationshipText(decision.outcome)]', app)
        self.assertNotIn('["影响来源", influenceSources]', app)
        self.assertNotIn('["具体安排", clean(decision.outcome, "")]', app)
        self.assertNotIn('["落地", clean(decision.outcome, "")]', app)
        self.assertNotIn('["结果", clean(decision.outcome, "")]', app)
        self.assertIn("Array.isArray(line)", app)
        self.assertNotIn(
            "[clean(decision.decision), clean(decision.reason), clean(decision.evidence), clean(decision.outcome)]",
            app,
        )
        self.assertNotIn("周目标执行", app)
        self.assertNotIn("记忆影响", app)
        self.assertIn("decision.influence_sources", app)
        self.assertNotIn("决策影响链路", app)
        self.assertNotIn("decision_influence_chain", app)
        self.assertNotIn('item.decision ? `决策：${clean(item.decision)}` : ""', app)
        self.assertIn('join(" · ")', app)
        self.assertNotIn('join(" -> ")', app)
        self.assertIn(
            "grid-template-columns: minmax(76px, 112px) minmax(0, 1fr)", style
        )
        self.assertIn("text-overflow: ellipsis", style)
        self.assertNotIn(".observation-panel", style)

    def test_dashboard_keeps_selected_world_tab_on_status_refresh(self):
        from pathlib import Path

        app = (
            Path(__file__).resolve().parents[1] / "pages" / "dashboard" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertNotIn("function worldTabHasRecords", app)
        self.assertNotIn("function selectAvailableWorldTab", app)
        self.assertNotIn("selectAvailableWorldTab(nextStatus)", app)
        self.assertNotIn("tabs.find((tab) => worldTabHasRecords", app)
        self.assertIn("bindRovingTabs(el.worldTabs, (tab) => {", app)
        self.assertIn("state.worldTab = tab.dataset.worldTab;", app)
        self.assertIn("renderWorld(state.status || {});", app)

    def test_today_week_plan_labels_hint_and_suggestions(self):
        from pathlib import Path

        app = (
            Path(__file__).resolve().parents[1] / "pages" / "dashboard" / "app.js"
        ).read_text(encoding="utf-8")

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
        labels = (root / "shared" / "terms.js").read_text(encoding="utf-8")
        display = (root / "shared" / "format.js").read_text(encoding="utf-8")
        scripts = "\n".join([app, labels, display])
        style = self._dashboard_style(root)

        self.assertNotIn("const SCHEDULE_TONE_LABELS = {", app)
        self.assertIn('from "./shared/terms.js"', app)
        self.assertIn('from "./shared/format.js"', app)
        self.assertIn("export const SCHEDULE_TONE_LABELS = {", labels)
        self.assertIn('awake: "正常活动"', labels)
        self.assertIn("export const CURRENT_SLEEP_LABELS = {", labels)
        self.assertIn('awake: "未入睡"', labels)
        self.assertIn('residence: "居住地"', labels)
        self.assertIn('anchor: "主要地点"', labels)
        self.assertIn('bookstore: "书店"', labels)
        self.assertIn('street: "街道"', labels)
        self.assertIn('gallery: "展馆"', labels)
        self.assertIn("export const EMOJI_SOURCE_LABELS = {", labels)
        self.assertIn('verified: "识图确认"', labels)
        self.assertIn("export const EMOJI_STATUS_LABELS = {", labels)
        self.assertIn("export const EMOJI_TYPE_LABELS = {", labels)
        self.assertIn('mface: "商城表情"', labels)
        self.assertIn('custom_emoji: "自定义表情"', labels)
        self.assertIn('emoji_image: "表情图片"', labels)
        self.assertNotIn("const EMOJI_SOURCE_LABELS = {", app)
        self.assertNotIn("const EMOJI_STATUS_LABELS = {", app)
        self.assertNotIn("const EMOJI_TYPE_LABELS = {", app)
        self.assertIn(
            'enumLabelOrReadableText(item.type, PLACE_TYPE_LABELS, "其他地点")',
            app,
        )
        self.assertIn(
            'enumLabelStrict(item.source_kind, EMOJI_SOURCE_LABELS, "未知来源")',
            app,
        )
        self.assertIn('enumLabelStrict(item.status, EMOJI_STATUS_LABELS, "未知")', app)
        self.assertIn(
            'enumLabelStrict(item.asset_type, EMOJI_TYPE_LABELS, "未分类")', app
        )
        self.assertIn('["life_mode", SCHEDULE_TONE_LABELS, "日程基调"]', display)
        self.assertIn('["sleep_mode", SLEEP_MODE_LABELS, "睡眠倾向"]', display)
        self.assertIn(
            '["schedule_intent", SCHEDULE_INTENT_LABELS, "活动倾向"]', display
        )
        self.assertIn('watchstate: "观看状态"', display)
        self.assertIn('targettype: "目标类型"', display)
        self.assertIn('evidence: "证据"', display)
        self.assertIn('evidencetype: "证据类型"', display)
        self.assertIn('chat_state_refresh: "聊天触发状态巡检"', labels)
        self.assertIn('auto_check: "自动检查"', labels)
        self.assertIn('chat_batch: "会话批次"', labels)
        self.assertIn('private_revisit: "私聊回访"', labels)
        self.assertIn('regular_reply: "普通回复"', labels)
        self.assertIn('previous_reply: "上一轮回复"', labels)
        self.assertIn('superseded: "已被替代"', labels)
        self.assertIn('["生活模式", SCHEDULE_TONE_LABELS, "日程基调"]', display)
        self.assertIn('["日程倾向", SCHEDULE_INTENT_LABELS, "活动倾向"]', display)
        self.assertIn("export const PLAN_OUTFIT_DECISION_LABELS = {", labels)
        self.assertIn('outdoor: "预计外出"', labels)
        self.assertIn("export const OUTFIT_STYLE_POOL_LABELS = {", labels)
        self.assertIn('sleep_styles: "居家/睡眠风格"', labels)
        self.assertIn('outfit_styles: "日常/外出风格"', labels)
        self.assertIn("export const OUTFIT_SCENE_CATEGORY_LABELS = {", labels)
        self.assertIn(
            '["plan_outfit_decision", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"]', display
        )
        self.assertIn('["style_pool", OUTFIT_STYLE_POOL_LABELS, "风格池"]', display)
        self.assertIn(
            '["outfit_style_pool", OUTFIT_STYLE_POOL_LABELS, "穿搭风格池"]', display
        )
        self.assertIn(
            '["scene_category", OUTFIT_SCENE_CATEGORY_LABELS, "场景"]', display
        )
        self.assertIn('["风格池", OUTFIT_STYLE_POOL_LABELS, "风格池"]', display)
        self.assertIn('["换装", OUTFIT_DECISION_LABELS, "穿搭"]', display)
        self.assertIn('["穿搭", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"]', display)
        self.assertIn(
            "EVENT_STATUS_LABELS,", display.split('} from "./terms.js";', 1)[0]
        )
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
        self.assertIn(".facts-column-fill {\n  align-self: start;", style)
        self.assertIn("block-size: auto;", style)
        self.assertIn("grid-template-rows: none;", style)
        self.assertIn('.facts-column-fill > [data-fact-card="memo"]', style)
        self.assertIn(".facts-column > div {\n  min-width: 0;", style)
        self.assertIn('.facts-column > [data-fact-card="schedule-tone"]', style)
        self.assertIn('.facts-column > [data-fact-card="schedule-intent"]', style)

        self.assertIn("padding-block: 5px;", style)
        self.assertIn("const FACT_CARD_ORDER = [", app)
        self.assertIn("function layoutTodayFacts()", app)
        self.assertIn("function scheduleTodayFactsLayout()", app)
        self.assertIn(
            'window.addEventListener("resize", scheduleTodayFactsLayout)', app
        )
        self.assertIn("const leftSize = Math.ceil(FACT_CARD_ORDER.length / 2);", app)
        self.assertNotIn("compact ? FACT_CARD_ORDER.length", app)
        self.assertNotIn("window.innerWidth || 0) <= 680", app)
        self.assertIn(
            ".facts {\n    grid-template-columns: repeat(2, minmax(0, 1fr));", style
        )
        self.assertIn(".facts-column-fill {\n    align-self: start;", style)
        self.assertNotIn(
            "grid-template-rows: max-content max-content repeat(3, minmax(max-content, 1fr));",
            style,
        )
        self.assertIn("function renderTodayWeekPlan", app)
        self.assertIn("function stripLeadingEmoji", app)
        self.assertIn("function todayWeekRow", app)
        self.assertIn('clean(stripLeadingEmoji(week.theme), "")', app)
        self.assertIn('clean(stripLeadingEmoji(week.today_hint), "")', app)
        self.assertIn('clean(stripLeadingEmoji(week.today_suggested), "")', app)
        self.assertNotIn("today-week-title", app)
        self.assertIn(
            "renderFactPair(el.currentOutfitText, currentOutfitDisplayText(day, meta), TODAY_FACT_EMPTY_TEXT.currentOutfitText)",
            app,
        )
        self.assertIn(
            "renderFactPair(el.outfitDecisionText, outfitDecisionText(meta), TODAY_FACT_EMPTY_TEXT.outfitDecisionText)",
            app,
        )
        self.assertIn(
            'function renderFactPair(target, value, emptyText = "暂无内容")', app
        )
        self.assertIn('node("div", "today-week-appearance-hair", "")', app)
        self.assertIn('const hairStyle = clean(meta.hair_style, "")', display)
        self.assertIn("function stripCoveredAppearanceDetail", display)
        self.assertIn(
            "stripCoveredAppearanceDetail(day.outfit, hairStyle, hair)",
            display,
        )
        self.assertIn("return { style, outfit, hairStyle, hair }", display)
        self.assertIn('hair_style: "发型名称"', display)
        self.assertIn('hair: "发型细节"', display)
        self.assertIn('data.hairStyle ? `发型：${data.hairStyle}` : "发型："', app)
        self.assertIn("document.createTextNode(data.hair)", app)
        self.assertNotIn('data.hairStyle ? "，"', app)
        self.assertNotIn("`发型：${data.hair}`", app)
        self.assertNotIn(".today-week-appearance-hair-detail {", style)
        self.assertIn('todayWeekRow("提示", hint)', app)
        self.assertIn('todayWeekRow("建议", suggested, "muted")', app)
        self.assertNotIn('todayWeekRow("进度", hint)', app)
        self.assertNotIn('todayWeekRow("目标", suggested', app)
        self.assertIn("card.replaceChildren(...lines)", app)
        self.assertNotIn(
            'node("div", "today-week-card", ...(title ? [title] : []), ...lines)', app
        )
        self.assertNotIn("今日提醒", app)
        self.assertNotIn("今日建议", app)
        self.assertIn("<dt>👗 当前穿搭</dt>", html)
        self.assertIn("<dt>🪞 穿搭判断</dt>", html)
        self.assertIn("function moodColorText(value)", display)
        self.assertIn('body.includes("·")', display)
        self.assertIn(
            "el.moodColorText.textContent = clean(moodColorText(meta.mood), TODAY_FACT_EMPTY_TEXT.moodColorText)",
            app,
        )
        self.assertIn("function scheduleTypeText(value)", display)
        self.assertIn(
            "el.scheduleTypeText.textContent = clean(scheduleTypeText(meta.schedule_type), TODAY_FACT_EMPTY_TEXT.scheduleTypeText)",
            app,
        )
        self.assertNotIn("meta.schedule_type || meta.style", app)
        self.assertIn(
            "el.themeText.textContent = clean(meta.theme, TODAY_FACT_EMPTY_TEXT.themeText)",
            app,
        )
        self.assertIn(
            "el.scheduleToneText.textContent = clean(enumLabel(meta.life_mode, SCHEDULE_TONE_LABELS), TODAY_FACT_EMPTY_TEXT.scheduleToneText)",
            app,
        )
        self.assertIn("function currentOutfitDisplayText(day = {}, meta = {})", display)
        self.assertIn("`风格：${data.style}`", app)
        self.assertIn("function outfitDecisionText(meta = {})", display)
        self.assertIn("return { decision, reason }", display)
        self.assertIn(
            "renderFactPair(el.currentOutfitText, currentOutfitDisplayText(day, meta), TODAY_FACT_EMPTY_TEXT.currentOutfitText)",
            app,
        )
        self.assertIn(
            "renderFactPair(el.outfitDecisionText, outfitDecisionText(meta), TODAY_FACT_EMPTY_TEXT.outfitDecisionText)",
            app,
        )
        self.assertIn(
            'function renderFactPair(target, value, emptyText = "暂无内容")', app
        )
        self.assertIn('node("span", "today-week-label", `风格：${data.style}`)', app)
        self.assertIn('node("span", "today-week-label", data.decision)', app)
        self.assertNotIn("outfit-panel", html)
        self.assertNotIn('id="periodText"', html)
        self.assertNotIn('id="outfitText"', html)
        self.assertNotIn("periodText:", app)
        self.assertNotIn("outfitText:", app)
        self.assertNotIn(".outfit-panel", style)
        self.assertIn(
            "function currentScheduleIntentText(day = {}, clock = currentClockDate())",
            app,
        )
        self.assertIn(
            "function renderRealtimeDayFacts(clock = currentClockDate())", app
        )
        self.assertIn("renderRealtimeDayFacts(clock)", app)
        self.assertIn('const CURRENT_ACTIVITY_EMPTY_TEXT = "暂无当前活动"', app)
        self.assertIn('const METER_EMPTY_TEXT = "暂无数据"', app)
        self.assertIn('const TIMELINE_TIME_EMPTY_TEXT = "未定"', app)
        self.assertIn("const TODAY_FACT_EMPTY_TEXT = {", app)
        self.assertIn("function renderEmptyTodayFacts()", app)
        self.assertIn("percent === null ? METER_EMPTY_TEXT", app)
        self.assertIn("clean(item.time, TIMELINE_TIME_EMPTY_TEXT)", app)
        self.assertIn(": CURRENT_ACTIVITY_EMPTY_TEXT", app)
        self.assertIn(
            "el.todayWeekPlan.textContent = TODAY_FACT_EMPTY_TEXT.todayWeekPlan", app
        )
        self.assertIn("target.textContent = emptyText", app)
        self.assertIn('<span id="targetDate" class="pill">加载中</span>', html)
        self.assertIn('<dd id="weatherText">暂无天气</dd>', html)
        self.assertIn('<dd id="themeText">暂无主题</dd>', html)
        self.assertIn('<dd id="todayWeekPlan">暂无周计划</dd>', html)
        self.assertIn('<dd id="moodColorText">暂无心情色彩</dd>', html)
        self.assertIn('<dd id="scheduleTypeText">暂无日程类型</dd>', html)
        self.assertIn('<dd id="scheduleToneText">暂无日程基调</dd>', html)
        self.assertIn("<dt>🚪 活动状态</dt>", html)
        self.assertIn('<dd id="scheduleIntentText">暂无活动状态</dd>', html)
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
        self.assertIn("const HEALTH_CHECK_KEYS = [", display)
        self.assertIn("function healthCheckRows(checks = [])", display)
        self.assertNotIn("const orderedKeys = [...HEALTH_CHECK_KEYS]", scripts)
        self.assertIn("health-check-list", app)
        self.assertNotIn("health-check-grid", app)
        self.assertNotIn("health-check-group", app)
        self.assertNotIn('"is-ok"', app)
        self.assertNotIn('"is-pending"', app)
        self.assertIn('function node(tag, className = "", content = "")', display)
        self.assertIn("function visibleLifeEpisodes(episodes)", display)
        self.assertIn('text(item.kind).trim().toLowerCase() !== "daily_plan"', display)
        self.assertIn("visibleEpisodes.slice(0, 4).forEach", app)
        self.assertIn("function lifeEpisodeLines(item)", display)
        self.assertIn('new Set(["时间轴", "地点"])', display)
        self.assertIn(
            "recordLines(relationshipRecordLines([...lifeEpisodeLines(item), people].filter(Boolean), relationshipText))",
            app,
        )
        self.assertNotIn("function visibleMemoryBoundaries(boundaries)", scripts)
        self.assertNotIn("boundaries.slice(0, 3).forEach", scripts)
        self.assertNotIn("experience.boundaries", scripts)
        self.assertNotIn('join(" / ")', scripts)
        self.assertNotIn("` / ${", scripts)
        self.assertNotIn("} / ${", scripts)
        self.assertIn('join(" · ")', scripts)
        self.assertIn(
            "权重 ${Number(item.weight || 0).toFixed(1)} · ${relationshipText(evidence)}",
            app,
        )
        self.assertIn(".record-lines", style)
        self.assertIn(
            "function evidenceTargetTitle(item, displayIndex = null)", display
        )
        self.assertIn("clean(item.target_label", display)
        self.assertIn("function stateLogText(value)", display)
        self.assertIn("PAGE_STATUS_REASON_LABELS", display)
        self.assertIn("export const LIFE_DECISION_KIND_LABELS = {", labels)
        self.assertIn('daily_plan: "日程规划"', labels)
        self.assertIn('weekly_plan: "周计划"', labels)
        self.assertIn('outfit: "穿搭判断"', labels)
        self.assertIn('invite: "邀约判断"', labels)
        self.assertIn("export const WEEK_PROGRESS_STATUS_LABELS = {", labels)
        self.assertIn('missing: "暂无记录"', labels)
        self.assertIn("LIFE_DECISION_KIND_LABELS", display)
        self.assertIn("WEEK_PROGRESS_STATUS_LABELS", display)
        self.assertIn("typedLabel(decision.kind, LIFE_DECISION_KIND_LABELS)", app)
        self.assertIn("return enumLabel(raw, labels);", app)
        self.assertIn(
            'enumLabelOrReadableText(item.label, HEALTH_CHECK_LABELS, "其他检查")',
            display,
        )
        self.assertIn("longTermMemoryCategoryLabel(item.category)", app)
        self.assertNotIn("clean(item.kind)", app)
        self.assertNotIn('clean(item.target_type || "memory")', app)
        self.assertIn("stateLogText(entry)", app)
        self.assertNotIn('["社交电量", rhythm.social_battery]', app)
        self.assertIn(
            "rhythm.social_battery !== undefined ? `社交电量：${Number(rhythm.social_battery || 0)}/100`",
            app,
        )
        self.assertIn('appendInfoBox(\n    "生理节律"', app)
        self.assertIn("const rhythm = lifeState.physiological_rhythm || {}", app)
        self.assertIn('physiological_rhythm: "生理节律"', display)
        self.assertIn('social_battery: "社交电量"', display)
        self.assertIn('autonomous_life_update: "自主生活状态与穿搭更新"', labels)
        self.assertIn('planned: "已计划"', labels)
        self.assertIn('node("div", "status", clean(item.status))', app)
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

    def test_dashboard_shared_enum_labels_match_backend(self):
        from core.labels import (
            EMOJI_EMOTION_CATEGORY_LABELS,
            LONG_TERM_MEMORY_CATEGORY_LABELS,
            MEMORY_CONFLICT_TYPE_LABELS,
            MEMORY_ENTITY_TYPE_LABELS,
            PAGE_STATUS_REASON_LABELS,
            PREFERENCE_CATEGORY_LABELS,
        )

        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        labels = (root / "shared" / "terms.js").read_text(encoding="utf-8")
        mappings = [
            PAGE_STATUS_REASON_LABELS,
            PREFERENCE_CATEGORY_LABELS,
            MEMORY_ENTITY_TYPE_LABELS,
            MEMORY_CONFLICT_TYPE_LABELS,
            LONG_TERM_MEMORY_CATEGORY_LABELS,
            EMOJI_EMOTION_CATEGORY_LABELS,
        ]
        for mapping in mappings:
            for key, label in mapping.items():
                self.assertIn(
                    f"{key}: {json.dumps(label, ensure_ascii=False)}",
                    labels,
                    key,
                )

    def test_dashboard_user_facing_fallbacks_are_chinese(self):
        root = Path(__file__).resolve().parents[1]
        dashboard = root / "pages" / "dashboard"
        app = (dashboard / "app.js").read_text(encoding="utf-8")
        config = (dashboard / "ui" / "settings.js").read_text(encoding="utf-8")
        selects = (dashboard / "ui" / "selects.js").read_text(encoding="utf-8")
        api = (dashboard / "api" / "transport.js").read_text(encoding="utf-8")
        entry = (root / "core" / "interface" / "portal" / "entry.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('"Select"', selects)
        self.assertIn('|| "请选择"', selects)
        self.assertIn('|| "选择项"', selects)
        self.assertNotIn("error.message ||", app)
        self.assertNotIn("error.message ||", config)
        self.assertIn("userErrorMessage(error", app)
        self.assertIn("userErrorMessage(error", config)
        self.assertIn("result.error?.public === true", api)
        self.assertIn('"public": True', entry)

    def test_dashboard_api_only_displays_public_error_messages(self):
        root = Path(__file__).resolve().parents[1]
        script = """
globalThis.window = {
  AstrBotPluginPage: {
    async apiGet(endpoint) {
      if (endpoint === "public") {
        return { ok: false, error: { public: true, message: "参数格式不正确" } };
      }
      return { ok: false, error: { message: "database is locked" } };
    },
  },
  setTimeout,
  clearTimeout,
};
const mod = await import("./pages/dashboard/api/transport.js");
let publicMessage = "";
try {
  await mod.apiGet("public");
} catch (error) {
  publicMessage = mod.userErrorMessage(error, "请求失败");
}
if (publicMessage !== "参数格式不正确") {
  throw new Error(`公开错误没有正确展示：${publicMessage}`);
}
let privateMessage = "";
try {
  await mod.apiGet("private");
} catch (error) {
  privateMessage = mod.userErrorMessage(error, "请求失败");
}
if (privateMessage !== "请求失败，请查看后台日志" || privateMessage.includes("database")) {
  throw new Error(`内部错误发生泄漏：${privateMessage}`);
}
if (mod.userErrorMessage(new Error("Failed to fetch"), "状态加载失败") !== "状态加载失败") {
  throw new Error("原生英文异常没有使用中文回退");
}
"""
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

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
const mod = await import("./pages/dashboard/shared/format.js");
const labels = await import("./pages/dashboard/shared/terms.js");
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
if (
  mod.memoryEntityTypeLabel("constraint") !== "约束"
  || mod.memoryConflictTypeLabel("tension") !== "记忆张力"
  || mod.memoryConflictTypeLabel("override") !== "覆盖修正"
  || mod.memoryConflictTypeLabel("temporary_tension") !== "临时冲突"
  || mod.longTermMemoryCategoryLabel("life") !== "生活记忆"
  || mod.longTermMemoryCategoryLabel("expression") !== "表达记忆"
  || mod.longTermMemoryCategoryLabel("preference:life") !== "生活偏好"
  || mod.longTermMemoryCategoryLabel("unknown_value") !== "未分类"
  || mod.enumLabelStrict("unknown_value", {}, "未分类") !== "未分类"
) {
  throw new Error("体验层枚举没有正确中文化");
}
if (
  mod.enumLabelOrReadableText("bookstore", labels.PLACE_TYPE_LABELS, "其他地点") !== "书店"
  || mod.enumLabelOrReadableText("gallery", labels.PLACE_TYPE_LABELS, "其他地点") !== "展馆"
  || mod.enumLabelOrReadableText("街角", labels.PLACE_TYPE_LABELS, "其他地点") !== "街角"
  || mod.enumLabelOrReadableText("unknown_place", labels.PLACE_TYPE_LABELS, "其他地点") !== "其他地点"
  || mod.enumLabelStrict("verified", labels.EMOJI_SOURCE_LABELS, "未知来源") !== "识图确认"
  || mod.enumLabelStrict("mface", labels.EMOJI_TYPE_LABELS, "未分类") !== "商城表情"
  || mod.enumLabelStrict("unknown_type", labels.EMOJI_TYPE_LABELS, "未分类") !== "未分类"
) {
  throw new Error("地点或表情枚举没有正确中文化");
}
const emojiLabels = mod.emojiEmotionLabels(["category:happy", "happy", "难为情", "unknown_tag"]);
if (emojiLabels.join("、") !== "开心、难为情、其他情绪") {
  throw new Error(`表情情绪标签没有正确中文化和去重：${emojiLabels.join("、")}`);
}
if (
  mod.clean("planned") !== "已计划"
  || mod.clean("pending") !== "待进行"
  || mod.clean("in_progress") !== "进行中"
  || mod.clean("completed") !== "已完成"
  || mod.clean("skipped") !== "已跳过"
  || mod.clean("canceled") !== "已取消"
) {
  throw new Error("时间轴状态枚举没有正确中文化");
}
if (
  mod.timelineTravelText({
    travel_mode: "transit",
    travel_detail: "公交 + 地铁",
    travel_minutes: 55,
    travel_distance_meters: 5700,
    travel_provider: "amap",
    travel_origin: "家",
    place: "测试广场",
  }) !== "从家前往测试广场 · 公交 + 地铁约 55 分钟 · 5.7 公里 · 高德地图"
) {
  throw new Error("公共交通细分没有正确展示");
}
if (
  mod.timelineTravelText({ travel_mode: "transit", travel_minutes: 20 })
  !== "公共交通约 20 分钟"
) {
  throw new Error("公共交通回退被地点枚举错误翻译");
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
if (mod.evidenceTargetTitle({ target_type: "focus", target_id: "251880291" }) !== "关注目标") {
  throw new Error("evidenceTargetTitle 不应展示内部数字 ID");
}
if (mod.readableReferenceLabel("251880291", "关注目标") !== "关注目标") {
  throw new Error("readableReferenceLabel 没有隐藏内部数字 ID");
}
if (
  mod.cognitionSubjectText("self") !== "自己"
  || mod.cognitionSubjectText("bot") !== "角色"
  || mod.cognitionSubjectText("unknown_subject") !== "unknown_subject"
  || mod.clean("self") !== "自己"
  || mod.cognitionPredicateText("current_place") !== "当前地点"
  || mod.cognitionPredicateText("favorite_food") !== "喜欢的食物"
  || mod.cognitionValueText({ dish: "窝蛋牛肉煲仔饭", place: "家里" }) !== "菜品：窝蛋牛肉煲仔饭；地点：家里"
  || mod.humanizeToken("meal_preference") !== "饮食偏好"
) {
  throw new Error("认知事实主体没有正确中文化");
}
if (mod.evidenceText("251880291,929722496,416704502") !== "来自 3 条聊天消息") {
  throw new Error("evidenceText 没有把纯 ID 证据转换为可读来源");
}
if (mod.evidenceText("251880291") !== "来自 1 条聊天消息") {
  throw new Error("evidenceText 没有隐藏单个内部 ID");
}
if (mod.evidenceText("用户明确使用这个称呼；251880291,929722496") !== "用户明确使用这个称呼") {
  throw new Error("evidenceText 没有从可读说明中移除内部 ID");
}
if (mod.evidenceText("用户明确使用这个称呼；251880291") !== "用户明确使用这个称呼") {
  throw new Error("evidenceText 没有从说明中移除单个内部 ID");
}
if (mod.evidenceText("群号 12345678") !== "群号12345678") {
  throw new Error("evidenceText 不应隐藏带有可读语义的群号");
}
const ordinaryEvidence = mod.evidenceText("发生于 2026-07-18 22:32，金额 100 元");
if (!ordinaryEvidence.includes("2026") || !ordinaryEvidence.includes("22:32") || !ordinaryEvidence.includes("100")) {
  throw new Error(`evidenceText 误清理了日期、时间或金额：${ordinaryEvidence}`);
}
const body = mod.recordLines(["状态：open", ["来源", "chat_memory"]]);
if (body.tagName !== "DIV" || !body.className.includes("record-lines") || body.children.length !== 2) {
  throw new Error("recordLines 没有生成预期节点");
}
const timedBody = mod.recordLines(["示例好友在22:32至22:40间连续发送四条消息。"]);
const timedLine = timedBody.children[0];
if (
  !timedLine
  || !timedLine.className.includes("full")
  || timedLine.textContent !== "示例好友在22:32至22:40间连续发送四条消息。"
  || timedLine.children.length !== 0
) {
  throw new Error("recordLines 不应把时间中的半角冒号识别为字段分隔符");
}
const asciiFieldBody = mod.recordLines(["状态:open"]);
if (asciiFieldBody.children[0]?.children.length !== 2) {
  throw new Error("recordLines 仍应支持半角冒号分隔的结构化字段");
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
const healthRows = mod.healthCheckRows([
  { key: "behavior_feedback", label: "行为反馈", count: 2 },
  { key: "memory_evidence", label: "证据链", count: 3 },
  { key: "focus_targets", label: "关注目标", count: 1 },
  { key: "evidence", label: "证据链", count: 0 },
]);
if (
  healthRows.length !== 3
  || healthRows.map((item) => item.key).join(",") !== "memory_evidence,focus_targets,behavior_feedback"
  || healthRows.find((item) => item.label === "证据链").count !== 3
  || new Set(healthRows.map((item) => item.label)).size !== healthRows.length
) {
  throw new Error("healthCheckRows 应只展示接口返回的数据且标签不能重复");
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
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
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
    this.listeners = {};
    this.attributes = {};
    this.capturedPointers = new Set();
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
  addEventListener(type, listener) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }
  dispatch(type, event = {}) {
    const payload = {
      pointerId: 0,
      pointerType: "mouse",
      button: 0,
      clientX: 0,
      clientY: 0,
      preventDefault() {},
      ...event,
    };
    for (const listener of this.listeners[type] || []) listener(payload);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getBoundingClientRect() { return { left: 0, width: 100 }; }
  focus() {}
  setPointerCapture(pointerId) { this.capturedPointers.add(pointerId); }
  hasPointerCapture(pointerId) { return this.capturedPointers.has(pointerId); }
  releasePointerCapture(pointerId) { this.capturedPointers.delete(pointerId); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
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
        script = (
            self._dashboard_dom_mock_script()
            + """
await import("./pages/dashboard/app.js");
"""
        )
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_relationship_scope_uses_profile_display_name(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = (
            self._dashboard_dom_mock_script()
            + """
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
        )
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_realtime_schedule_intent_prefers_extended_night_home(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = (
            self._dashboard_dom_mock_script()
            + """
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
        )
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_realtime_schedule_intent_prefers_home_before_first_item_at_dawn(
        self,
    ):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = (
            self._dashboard_dom_mock_script()
            + """
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
        )
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_realtime_activity_prefers_current_place_over_outgoing_intent(
        self,
    ):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = (
            self._dashboard_dom_mock_script()
            + """
const mod = await import("./pages/dashboard/app.js");
const day = {
  date: "2026-06-26",
  timeline: [
    {
      time: "15:00",
      activity: "在测试公园散步",
      place: "测试公园",
      place_kind: "poi",
      execution_state: "active",
    },
    { time: "17:00", activity: "回家", place: "家", place_kind: "home" },
  ],
  state: {
    energy: 55,
    outgoing: 20,
    social: 20,
    busyness: 20,
    focus: 20,
    interaction_capacity: 20,
    sleepiness: 20,
    sleep: { depth: "awake" },
  },
};
const value = mod.currentScheduleIntentText(day, new Date(2026, 5, 26, 15, 30));
if (value !== "外出中") {
  throw new Error(`当前地点事实应优先于外出意愿：${value}`);
}
"""
        )
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_current_timeline_prefers_active_and_ignores_terminal_items(
        self,
    ):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = (
            self._dashboard_dom_mock_script()
            + """
const mod = await import("./pages/dashboard/app.js");
const day = {
  date: "2026-06-26",
  timeline: [
    { time: "14:00", activity: "已取消安排", execution_state: "cancelled" },
    { time: "14:30", activity: "已经完成的安排", execution_state: "completed" },
    { time: "15:00", activity: "正在进行的安排", execution_state: "active" },
    { time: "16:30", activity: "下一项安排", execution_state: "planned" },
  ],
};
const pair = mod.currentTimelinePair(day, new Date(2026, 5, 26, 15, 20));
if (pair.current?.activity !== "正在进行的安排") {
  throw new Error(`没有优先显示实际进行中的节点：${pair.current?.activity || "空"}`);
}
if (pair.next?.activity !== "下一项安排") {
  throw new Error(`没有跳过终态节点寻找下一项：${pair.next?.activity || "空"}`);
}
"""
        )
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dashboard_current_panel_does_not_show_yesterday_last_item_at_dawn(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = (
            self._dashboard_dom_mock_script()
            + """
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
        )
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
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
        self.assertNotIn(" / 权重", combined)
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
        display = (root / "shared" / "format.js").read_text(encoding="utf-8")

        self.assertIn("function visibleExperienceEvidence(evidence, episodes)", display)
        self.assertIn('targetType === "life_episode"', display)
        self.assertIn('evidenceType === "daily_generation"', display)
        self.assertIn("episodeIds.has(targetId)", display)
        self.assertNotIn("currentDecisionId", display)
        self.assertIn("visibleExperienceEvidence(evidence, episodes)", app)
        self.assertIn("visibleEvidence.slice(0, 3).forEach", app)

    def test_life_domain_enums_are_rendered_with_chinese_labels(self):
        root = Path(__file__).resolve().parents[1] / "pages" / "dashboard"
        app = (root / "app.js").read_text(encoding="utf-8")
        terms = (root / "shared" / "terms.js").read_text(encoding="utf-8")

        for expected in (
            'lunch: "午餐"',
            'snack: "加餐"',
            'breakfast: "早餐"',
            'dinner: "晚餐"',
            'self: "当前角色"',
            'shared: "共同"',
            'serving: "份"',
            'failed: "执行失败"',
            'skipped: "已跳过"',
        ):
            self.assertIn(expected, terms)
        self.assertIn(
            'enumLabelOrReadableText(item.meal_type, MEAL_TYPE_LABELS, "饮食")',
            app,
        )
        self.assertIn(
            'enumLabelOrReadableText(item.owner, ACTION_OWNER_LABELS, "未定")',
            app,
        )
        self.assertNotIn('clean(item.meal_type, "饮食")', app)
        self.assertNotIn('`负责人：${clean(item.owner, "未定")}`', app)
        self.assertIn("Array.isArray(domains.recipes)", app)
        self.assertIn('ingredients.length ? `食材：${ingredients.join("、")}`', app)

    def test_current_outfit_display_separates_clothing_and_hair(self):
        root = Path(__file__).resolve().parents[1]
        script = """
const mod = await import("./pages/dashboard/shared/format.js");
const repeated = mod.currentOutfitDisplayText(
  {
    outfit: "浅绿色短袖衬衫搭配白色直筒裤，脚穿帆布鞋；头发扎成蓬松高马尾，用浅色发圈固定，额前留有轻薄刘海。",
  },
  {
    style: "清爽日常风",
    hair_style: "蓬松高马尾",
    hair: "蓬松高马尾用浅色发圈固定，额前留有轻薄刘海，发尾自然微卷。",
  },
);
if (
  repeated.outfit !== "浅绿色短袖衬衫搭配白色直筒裤，脚穿帆布鞋"
  || !repeated.hair.includes("浅色发圈")
  || repeated.hairStyle !== "蓬松高马尾"
) {
  throw new Error(`穿搭与发型没有正确分离：${JSON.stringify(repeated)}`);
}
const distinct = mod.currentOutfitDisplayText(
  { outfit: "浅绿色短袖衬衫搭配白色直筒裤，脚穿帆布鞋。" },
  {
    hair_style: "蓬松高马尾",
    hair: "蓬松高马尾用浅色发圈固定，额前留有轻薄刘海。",
  },
);
if (
  distinct.outfit !== "浅绿色短袖衬衫搭配白色直筒裤，脚穿帆布鞋"
  || !distinct.hair.includes("浅色发圈")
) {
  throw new Error(`独立穿搭或发型被错误修改：${JSON.stringify(distinct)}`);
}
"""
        result = subprocess.run(
            ["node", "--input-type=module"],
            cwd=root,
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
