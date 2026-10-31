"""优化后的语义路由、迁移与单一事实来源回归。"""

import json
import sqlite3
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import support  # noqa: F401 -- 先安装 AstrBot 测试桩

from core.archive.store import LifeArchive
from core.config.options.generate import DEFAULT_RTC_MODEL_NAME, RealtimeVoiceCallSettings
from core.life.tools import get_weather_activity_constraint
from core.runtime.capture.consolidation import memory_batch_rules
from core.runtime.lifecontract import build_life_decision_contract
from core.runtime.voicecall.calltranscript import VoiceCallTranscriptTurn
from core.runtime.voicecall.manager import VoiceCallInvite
from core.sight.bridge import SightComponent
import testbilibrowsing as bili_fixture


class WardrobeMigrationBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_preserve_legacy_facts_and_version_migration_repairs_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "life.db"
            archive = LifeArchive(path)
            try:
                item = await archive.upsert_style_catalog_item({
                    "kind": "outfit", "source_image_hash": "b" * 64,
                    "description": "浅蓝上衣", "confidence": 0.95,
                })
                def reset():
                    archive._conn.execute("UPDATE wardrobe_units SET ownership='candidate',"
                                          "acquired_at='',condition='dirty',revision=7,payload_json='{}'")
                    archive._conn.commit()
                    return archive._conn.total_changes
                changes = await archive._run_db(reset)
                for _ in range(2):
                    self.assertEqual((await archive.get_wardrobe_snapshot())["items"][str(item.id)]["ownership"], "candidate")
                    await archive.get_style_catalog_items()
                self.assertEqual(await archive._run_db(lambda: archive._conn.total_changes), changes)
            finally:
                archive.close()
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute("UPDATE meta SET value='29' WHERE key='schema_version'")
            upgraded = LifeArchive(path)
            try:
                state = (await upgraded.get_wardrobe_snapshot())["items"][str(item.id)]
                self.assertEqual(state["ownership"], "owned")
                self.assertEqual(state["condition"], "dirty")
                before = await upgraded._run_db(lambda: upgraded._conn.total_changes)
                await upgraded.get_wardrobe_snapshot()
                self.assertEqual(await upgraded._run_db(lambda: upgraded._conn.total_changes), before)
            finally:
                upgraded.close()


class NaturalBiliRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_negation_and_video_topic_do_not_suppress_normal_conversation(self):
        case = bili_fixture.BrowsingTest()
        await case.asyncSetUp()
        try:
            def schedule(coroutine, **kwargs):
                coroutine.close()
                return True
            case.runtime._schedule_background_task = schedule
            case.runtime.event_was_recalled = lambda event, **kwargs: False
            case.runtime._suppress_default_llm = Mock()
            case.service._model = AsyncMock(return_value={"mode": "conversation"})
            for text in ("不用总结，陪我看看就好", "这是读书笔记的视频"):
                event = types.SimpleNamespace(scope="private:a", message_str=f"{text} {bili_fixture.BV}")
                await case.service.incoming(event)
                self.assertTrue(event._daily_life_bili_natural)
                self.assertFalse(SightComponent(case.runtime).schedule_bili_summary_from_event(event))
            case.runtime._suppress_default_llm.assert_not_called()
        finally:
            await case.asyncTearDown()


class DecisionContractTest(unittest.TestCase):
    @staticmethod
    def schema(context):
        return json.loads(build_life_decision_contract(context).split("输出结构：", 1)[1])

    def test_optional_capabilities_require_current_evidence(self):
        minimal = self.schema({})
        for key in ("new_goals", "self_model_updates", "action_reflections", "interest_updates", "timeline_updates"):
            self.assertNotIn(key, minimal)
        self.assertIn("action_reflections", self.schema({"unreflected_outcomes": [{"action_id": "done"}]}))
        reviewed = self.schema({"goal_review_due": True})
        self.assertIn("new_goals", reviewed)
        self.assertIn("self_model_updates", reviewed)
        active = self.schema({"world": {"goals": [{"id": "existing"}]}})
        self.assertIn("goal_updates", active)
        self.assertNotIn("new_goals", active)

    def test_weather_safety_uses_structured_risk_and_actual_exposure(self):
        severe = get_weather_activity_constraint({"condition": "局地预警", "is_severe": True})
        self.assertIn("核实预警", severe)
        rainy = get_weather_activity_constraint({"condition": "阵雨", "is_rainy": True})
        self.assertIn("场地遮蔽", rainy)
        self.assertNotIn("取消户外", rainy)
        self.assertEqual(get_weather_activity_constraint({"is_severe": True}, enabled=False), "")

    def test_memory_revision_and_group_rules_require_relevant_inputs(self):
        private = memory_batch_rules({"scope": {"type": "private"}})
        self.assertNotIn("episode_link", private)
        self.assertNotIn("group_environment", private)
        with_data = memory_batch_rules({"scope": {"type": "group"}, "correctable_memories": [{"id": 1}]})
        self.assertIn("episode_link", with_data)
        self.assertIn("group_environment", with_data)

    def test_transcript_views_follow_turns_without_mirrored_storage(self):
        invite = VoiceCallInvite("id", "scope", "user", "name", "context", "", 0, 1)
        invite.transcript_turns = [VoiceCallTranscriptTurn("user", "第一句", finalized=True)]
        self.assertEqual(invite.user_transcript, "第一句")
        self.assertTrue(invite.user_transcript_finalized)
        invite.transcript_turns.append(VoiceCallTranscriptTurn("user", "第二句"))
        self.assertEqual(invite.user_transcript, "第一句\n第二句")
        self.assertFalse(invite.user_transcript_finalized)
        invite.transcript_turns.clear()
        self.assertEqual(invite.user_transcript, "")

    def test_rtc_model_is_normalized_at_config_boundary(self):
        for model in ("1.2.6.1", "doubao-seed-1.6", ""):
            self.assertEqual(RealtimeVoiceCallSettings.from_dict({"rtc_model_name": model}).rtc_model_name, DEFAULT_RTC_MODEL_NAME)
        self.assertEqual(RealtimeVoiceCallSettings.from_dict({"rtc_model_name": "custom-model"}).rtc_model_name, "custom-model")
