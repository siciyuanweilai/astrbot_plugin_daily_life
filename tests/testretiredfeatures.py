"""普通面板不读诊断数据；旧版记录保留只读，退役写入不再假成功。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import LifeArchive
from core.models import FocusTargetRecord
from legacy_support import seed_legacy_record
from testdashboard import PagePlugin


DIAGNOSTIC_READS = {
    "get_reflections", "get_persona_assertions", "get_decision_traces",
    "get_life_action_outcomes", "get_grounded_diary_entries", "get_durable_tasks",
    "get_emotion_arcs", "get_physiological_rhythm_logs", "get_life_terms",
    "get_memory_episode_clusters", "get_memory_entities", "get_memory_conflicts",
    "get_behavior_patterns", "get_session_mid_summaries",
    "get_temporary_expression_states", "get_focus_slots", "get_focus_targets",
}


class RetiredFeaturesTest(unittest.IsolatedAsyncioTestCase):
    async def test_normal_page_skips_optional_queries_but_keeps_rhythm_summary(self):
        plugin = PagePlugin()
        with patch.object(plugin, "_page_archive_records", wraps=plugin._page_archive_records) as reads:
            status = await plugin._build_page_status()
        self.assertFalse(status["config"]["diagnostics_enabled"])
        self.assertFalse(DIAGNOSTIC_READS & {call.args[0] for call in reads.call_args_list})
        self.assertNotIn("legacy_records", status["experience"])
        self.assertIn("physiological_rhythm_trend", status["experience"])

    async def test_diagnostics_are_explicit_and_do_not_persist_into_normal_request(self):
        plugin = PagePlugin()
        with patch.object(plugin, "_page_archive_records", wraps=plugin._page_archive_records) as reads:
            diagnostic = await plugin._build_page_status(diagnostics=True)
        self.assertTrue(diagnostic["config"]["diagnostics_enabled"])
        self.assertTrue(DIAGNOSTIC_READS <= {call.args[0] for call in reads.call_args_list})
        legacy = diagnostic["experience"]["legacy_records"]
        self.assertTrue(legacy["read_only"])
        self.assertFalse(legacy["participates_in_decisions"])
        normal = await plugin._build_page_status()
        self.assertFalse(normal["config"]["diagnostics_enabled"])
        self.assertNotIn("legacy_records", normal["experience"])

    async def test_status_and_long_poll_forward_explicit_diagnostics(self):
        plugin = PagePlugin()
        for method in (plugin.page_status, plugin.page_status_wait):
            for enabled in (False, True):
                for version in (0, 2):
                    with self.subTest(method=method.__name__, enabled=enabled, version=version), patch.object(
                        plugin, "_page_query_args", return_value={"diagnostics": "1" if enabled else "0", "since": "1"}
                    ), patch.object(plugin.runtime, "wait_page_status_changed", AsyncMock(return_value=version), create=True), patch.object(
                        plugin, "_build_page_status", AsyncMock(return_value={"status_version": version})
                    ) as build:
                        result = await method()
                        self.assertTrue(result["ok"])
                        build.assert_awaited_once_with(diagnostics=enabled)

    async def test_retired_focus_returns_gone_without_reading_or_writing_request(self):
        from quart import Quart
        plugin = PagePlugin()
        app = Quart(__name__)
        with patch.object(plugin, "_page_json_body", AsyncMock()) as body, patch.object(
            plugin.runtime.archive, "upsert_focus_target", AsyncMock()
        ) as write:
            async with app.test_request_context("/page/experience/focus", method="POST"):
                response = await plugin.page_experience_focus()
                self.assertEqual(response.status_code, 410)
                payload = await response.get_json()
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "feature_retired")
            body.assert_not_awaited()
            write.assert_not_awaited()

    async def test_disabled_expired_legacy_records_survive_reopen_and_diagnostic_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.db"
            archive = LifeArchive(path)
            await seed_legacy_record(archive, FocusTargetRecord(
                target_id="old", label="旧版关注记录", enabled=False, expires_at="2000-01-01",
            ))
            archive.close()
            reopened = LifeArchive(path)
            try:
                plugin = PagePlugin()
                plugin.runtime.archive = reopened
                before = await reopened.get_focus_targets(enabled_only=False, include_expired=True)
                diagnostic = await plugin._page_experience_snapshot(
                    "2026-11-02", relationship_records=[], summaries=[], diagnostics=True,
                )
                records = diagnostic["experience"]["legacy_records"]["focus_targets"]
                self.assertEqual([item["label"] for item in records], ["旧版关注记录"])
                self.assertFalse(records[0]["enabled"])
                after = await reopened.get_focus_targets(enabled_only=False, include_expired=True)
                self.assertEqual([r.as_dict() for r in before], [r.as_dict() for r in after])
                self.assertEqual(await reopened.get_focus_targets(), [])
            finally:
                reopened.close()
