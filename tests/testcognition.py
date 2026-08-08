import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.archive.schema import SCHEMA_VERSION
from core.archive.tables import (
    AWARENESS_SQL,
    COMMITMENT_SQL,
    CONVERSATION_SQL,
    CORE_SQL,
    DAILY_SQL,
    EXPERIENCE_SQL,
    INDEX_SQL,
    REVIEW_SQL,
    WEEKLY_SQL,
    WORLD_SQL,
)
from support import LifeArchive


class CognitionArchiveTest(unittest.IsolatedAsyncioTestCase):
    def test_version_two_database_migrates_cognition_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "daily_life.db"
            conn = sqlite3.connect(path)
            for script in (
                CORE_SQL,
                DAILY_SQL,
                WEEKLY_SQL,
                COMMITMENT_SQL,
                WORLD_SQL,
                AWARENESS_SQL,
                REVIEW_SQL,
                EXPERIENCE_SQL,
                CONVERSATION_SQL,
                INDEX_SQL,
            ):
                conn.executescript(script)
            for column in (
                "last_decay_at",
                "half_life_minutes",
                "baseline",
                "layer",
            ):
                conn.execute(f"ALTER TABLE emotion_arcs DROP COLUMN {column}")
            conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '2')")
            conn.commit()
            conn.close()

            archive = LifeArchive(path)
            try:
                version = archive._conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                tables = {
                    str(row[0])
                    for row in archive._conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                emotion_columns = {
                    str(row[1])
                    for row in archive._conn.execute(
                        "PRAGMA table_info(emotion_arcs)"
                    ).fetchall()
                }
                self.assertEqual(version, str(SCHEMA_VERSION))
                self.assertIn("temporal_facts", tables)
                self.assertIn("durable_tasks", tables)
                self.assertIn("grounded_diary_entries", tables)
                self.assertTrue(
                    {
                        "layer",
                        "baseline",
                        "half_life_minutes",
                        "last_decay_at",
                    }.issubset(emotion_columns)
                )
            finally:
                archive.close()

    async def test_temporal_fact_lifecycle_and_evidence_confidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(Path(tmpdir) / "daily_life.db")
            try:
                first = await archive.write_temporal_fact(
                    "ADD",
                    {
                        "scope": "private:1",
                        "subject": "friend:1",
                        "predicate": "meal_preference",
                        "object_value": {"dish": "煲仔饭"},
                        "valid_from": "2026-08-01 08:00:00",
                        "confidence": 0.6,
                    },
                )
                unchanged = await archive.write_temporal_fact(
                    "NONE",
                    {
                        "scope": "private:1",
                        "subject": "friend:1",
                        "predicate": "meal_preference",
                    },
                )
                second = await archive.write_temporal_fact(
                    "UPDATE",
                    {
                        "scope": "private:1",
                        "subject": "friend:1",
                        "predicate": "meal_preference",
                        "object_value": {"dish": "窝蛋牛肉煲仔饭"},
                        "valid_from": "2026-08-01 12:00:00",
                        "confidence": 0.7,
                    },
                )
                self.assertEqual(unchanged.id, first.id)
                self.assertEqual(second.supersedes_id, first.id)
                morning = await archive.get_temporal_facts(
                    scope="private:1", as_of="2026-08-01 10:00:00"
                )
                self.assertEqual(morning[0].object_value, {"dish": "煲仔饭"})

                await archive.add_fact_evidence_signal(
                    {
                        "fact_id": second.id,
                        "signal": "reinforce",
                        "weight": 1.0,
                        "confidence": 1.0,
                    }
                )
                reinforced = await archive.get_temporal_fact_confidence(second.id)
                await archive.add_fact_evidence_signal(
                    {
                        "fact_id": second.id,
                        "signal": "dispute",
                        "weight": 2.0,
                        "confidence": 1.0,
                    }
                )
                disputed = await archive.get_temporal_fact_confidence(second.id)
                self.assertGreater(reinforced, second.confidence)
                self.assertLess(disputed, reinforced)

                invalidated = await archive.write_temporal_fact(
                    "INVALIDATE",
                    {
                        "scope": "private:1",
                        "subject": "friend:1",
                        "predicate": "meal_preference",
                        "valid_from": "2026-08-01 18:00:00",
                    },
                )
                self.assertEqual(invalidated.status, "invalidated")
                self.assertIsNone(
                    await archive.get_current_temporal_fact(
                        "private:1", "friend:1", "meal_preference"
                    )
                )
            finally:
                archive.close()

    async def test_older_temporal_fact_cannot_replace_newer_current_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(Path(tmpdir) / "daily_life.db")
            try:
                current = await archive.write_temporal_fact(
                    "ADD",
                    {
                        "scope": "global",
                        "subject": "self",
                        "predicate": "current_place",
                        "object_value": "测试公园",
                        "valid_from": "2026-08-08 18:30:00",
                        "source": "life_action_receipt",
                    },
                )
                delayed = await archive.write_temporal_fact(
                    "UPDATE",
                    {
                        "scope": "global",
                        "subject": "self",
                        "predicate": "current_place",
                        "object_value": "旧测试地点",
                        "valid_from": "2026-08-08 18:00:00",
                        "source": "chat_batch",
                    },
                )

                self.assertEqual(delayed.id, current.id)
                self.assertEqual(delayed.object_value, "测试公园")
                facts = await archive.get_temporal_facts(
                    scope="global", subject="self", predicate="current_place"
                )
                self.assertEqual(len(facts), 1)
            finally:
                archive.close()

    async def test_lower_priority_fact_cannot_replace_same_time_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(Path(tmpdir) / "daily_life.db")
            try:
                current = await archive.write_temporal_fact(
                    "ADD",
                    {
                        "scope": "global",
                        "subject": "self",
                        "predicate": "current_outfit",
                        "object_value": "测试外出装",
                        "valid_from": "2026-08-08T19:00:00",
                        "source": "life_action_receipt",
                    },
                )
                delayed = await archive.write_temporal_fact(
                    "UPDATE",
                    {
                        "scope": "global",
                        "subject": "self",
                        "predicate": "current_outfit",
                        "object_value": "旧测试居家装",
                        "valid_from": "2026-08-08 19:00:00",
                        "source": "chat_batch",
                    },
                )

                self.assertEqual(delayed.id, current.id)
                self.assertEqual(delayed.object_value, "测试外出装")
            finally:
                archive.close()

    async def test_reflection_promotes_only_with_threshold_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(Path(tmpdir) / "daily_life.db")
            try:
                await archive.save_reflection(
                    {
                        "scope": "private:1",
                        "summary": "交流时偏好轻松直接的语气",
                        "importance": 0.85,
                        "evidence_ids": ["message:1", "message:2"],
                        "assertion_subject": "self",
                        "assertion_predicate": "conversation_style",
                        "assertion_object": {"tone": "轻松直接"},
                        "confidence": 0.9,
                    }
                )
                await archive.save_reflection(
                    {
                        "scope": "private:1",
                        "summary": "证据不足的候选归纳",
                        "importance": 0.9,
                        "evidence_ids": ["message:3"],
                        "assertion_subject": "self",
                        "assertion_predicate": "uncertain_style",
                        "assertion_object": True,
                    }
                )
                promoted = await archive.promote_reflections(
                    min_importance=0.8, min_evidence=2
                )
                self.assertEqual(len(promoted), 1)
                self.assertEqual(promoted[0].predicate, "conversation_style")
                assertions = await archive.get_persona_assertions(scope="private:1")
                self.assertEqual(len(assertions), 1)
            finally:
                archive.close()

    async def test_durable_task_recovers_leases_and_honors_attempt_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(Path(tmpdir) / "daily_life.db")
            try:
                task = await archive.enqueue_durable_task(
                    "revisit:1",
                    "private_revisit",
                    {"scope": "private:1"},
                    available_at="2026-08-01 10:00:00",
                    max_attempts=2,
                )
                duplicate = await archive.enqueue_durable_task(
                    "revisit:1", "private_revisit", {"scope": "changed"}
                )
                self.assertEqual(task.id, duplicate.id)
                self.assertEqual(duplicate.payload, {"scope": "private:1"})

                first_lease = await archive.lease_durable_tasks(
                    "worker-a", now="2026-08-01 10:00:00", lease_seconds=30
                )
                self.assertEqual(first_lease[0].attempts, 1)
                recovered = await archive.recover_expired_durable_tasks(
                    now="2026-08-01 10:01:00"
                )
                self.assertEqual(recovered, 1)
                second_lease = await archive.lease_durable_tasks(
                    "worker-b", now="2026-08-01 10:01:00", lease_seconds=30
                )
                self.assertEqual(second_lease[0].attempts, 2)
                self.assertTrue(
                    await archive.fail_durable_task(
                        task.id, "发送失败", owner="worker-b"
                    )
                )
                third_lease = await archive.lease_durable_tasks(
                    "worker-c", now="2026-08-01 10:02:00"
                )
                self.assertEqual(third_lease, [])
            finally:
                archive.close()

    async def test_pending_durable_media_task_can_be_finalized_after_delivery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(Path(tmpdir) / "daily_life.db")
            try:
                task = await archive.enqueue_durable_task(
                    "media_delivery:test-1",
                    "media_delivery",
                    {
                        "scope": "private:test",
                        "media_kind": "image",
                        "artifacts": ["/tmp/test.png"],
                    },
                )
                self.assertTrue(
                    await archive.finalize_durable_task(
                        task.id,
                        {"delivery": "sent", "detail": "测试投递完成"},
                    )
                )
                rows = await archive.get_durable_tasks(
                    kind="media_delivery", status="completed"
                )
                self.assertEqual(rows[0].result["delivery"], "sent")
            finally:
                archive.close()

    async def test_external_durable_task_failure_keeps_failed_terminal_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(Path(tmpdir) / "daily_life.db")
            try:
                await archive.enqueue_durable_task(
                    "web_research:failed-1",
                    "web_research",
                    {"task_id": "failed-1", "request_id": "remote-1"},
                )
                self.assertTrue(
                    await archive.fail_durable_task_by_key(
                        "web_research:failed-1",
                        "研究任务超过等待时间",
                        {"status": "timeout"},
                    )
                )
                task = (await archive.get_durable_tasks(kind="web_research"))[0]
                self.assertEqual(task.status, "failed")
                self.assertEqual(task.last_error, "研究任务超过等待时间")
                self.assertEqual(task.result, {"status": "timeout"})
            finally:
                archive.close()

    async def test_trace_action_affect_and_grounded_diary_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(Path(tmpdir) / "daily_life.db")
            try:
                trace = await archive.save_decision_trace(
                    {
                        "scope": "private:1",
                        "stage": "candidate",
                        "reason_code": "continuity_high",
                        "scores": {"benefit": 0.8, "disruption": 0.2},
                    }
                )
                advanced = await archive.save_decision_trace(
                    {
                        "trace_id": trace.trace_id,
                        "scope": "private:1",
                        "stage": "delivered",
                        "reason_code": "send_succeeded",
                        "outcome": "committed",
                    }
                )
                self.assertNotEqual(advanced.id, trace.id)
                self.assertEqual(advanced.stage, "delivered")
                traces = await archive.get_decision_traces(trace_id=trace.trace_id)
                self.assertEqual(
                    {item.stage for item in traces}, {"candidate", "delivered"}
                )

                action = await archive.save_life_action_outcome(
                    {
                        "action_id": "action:rest:1",
                        "date": "2026-08-01",
                        "action_type": "rest",
                        "preconditions": {"energy_below": 40},
                        "effects": {"energy_delta": 15},
                        "status": "committed",
                        "evidence": [trace.trace_id],
                    }
                )
                affect = await archive.save_affective_state(
                    {
                        "scope": "private:1",
                        "layer": "transient",
                        "label": "轻松",
                        "intensity": 0.9,
                        "baseline": 0.3,
                        "decay_half_life_minutes": 60,
                        "valid_from": "2026-08-01 10:00:00",
                        "evidence": [f"action:{action.id}"],
                    }
                )
                decayed = await archive.get_affective_states(
                    scope="private:1", at="2026-08-01 11:00:00"
                )
                self.assertAlmostEqual(decayed[0].intensity, 0.6)
                self.assertEqual(affect.layer, "transient")

                diary = await archive.save_grounded_diary_entry(
                    {
                        "date": "2026-08-01",
                        "scope": "private:1",
                        "summary": "我休息了一会儿，状态慢慢放松下来。",
                        "evidence_ids": [f"action:{action.id}", f"affect:{affect.id}"],
                    }
                )
                self.assertEqual(len(diary.evidence_ids), 2)
                with self.assertRaises(ValueError):
                    await archive.save_grounded_diary_entry(
                        {
                            "date": "2026-08-02",
                            "scope": "private:1",
                            "summary": "没有证据的日记",
                            "evidence_ids": [],
                        }
                    )
            finally:
                archive.close()


if __name__ == "__main__":
    unittest.main()
