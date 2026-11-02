"""记忆时效、原话证据、多路召回和后台整理的集成回归。"""

import asyncio
import json
import sqlite3
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock

from support import ChatSummaryRecord, Event, LifeArchive, LongTermMemoryRecord
from core.archive.ddl import iter_schema_sql
from core.archive.migrations import SCHEMA_VERSION, schema_fingerprint, CURRENT_SCHEMA_FINGERPRINT
from core.archive.schema import validate_schema
from core.clock import now as life_now
from core.runtime.recallrank import fuse_memory_routes
from core.runtime.remember import LifeMemoryService


class RecollectionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "life.db"
        self.archive = LifeArchive(self.path)
        await self.archive.initialize()

    async def asyncTearDown(self):
        self.archive.close()
        self.directory.cleanup()

    async def summary(self, text="之前喜欢红裙", scope="friend:1", message_id="m1"):
        saved = await self.archive.save_chat_summary(ChatSummaryRecord(
            session_id=scope, date="2026-01-01", brief=text, long_summary=text,
        ))
        memory = await self.archive.upsert_long_term_memory(LongTermMemoryRecord(
            scope=scope, session_id=scope, message_id=message_id, category="chat_summary",
            title="挑衣服", content=text, date="2026-01-01",
            source_table="chat_summaries", source_id=str(saved.id),
        ))
        return saved, memory

    async def fact(self, operation="ADD", **changes):
        value = dict(scope="friend:1", subject="用户", predicate="喜欢的裙子", object_value="红裙",
                     valid_from="2026-01-01 12:00:00", source_type="chat_message", source_id="m1",
                     source="chat_batch", provenance={"epistemic_status": "reported"})
        value.update(changes)
        return await self.archive.write_temporal_fact(operation, value)

    def message(self, text="我喜欢红裙", **changes):
        row = dict(session_id="friend:1", event_key="e1", message_id="m1", role="user",
                   sender_name="朋友", occurred_at="2026-01-01 12:00:00", message_text=text)
        row.update(changes)
        return row

    async def test_old_summary_keeps_history_and_uses_new_current_fact(self):
        saved, memory = await self.summary()
        old = await self.fact()
        await self.archive.save_memory_sources(saved.id, [self.message()])
        await self.fact("UPDATE", object_value="蓝裙", valid_from="2026-01-02 12:00:00", source_id="m2")
        notes = await self.archive.get_memory_recall_context([memory.id], scopes=["friend:1"], as_of="2026-01-03T00:00:00+08:00")
        self.assertIn("红裙", notes[memory.id])
        self.assertIn("蓝裙", notes[memory.id])
        self.assertIn("历史事实", notes[memory.id])
        self.assertEqual(await self.archive.get_memory_recall_context(
            [memory.id], scopes=["friend:1"], as_of="2026-01-01 15:00:00"), {})
        self.assertEqual(await self.archive.get_memory_recall_context([memory.id], scopes=["friend:2"]), {})
        # 清理认知记录后，摘要所依赖的历史事实仍然可核验。
        await self.archive._run_db(self.archive._cleanup_cognition_unlocked, 1)
        self.assertIsNotNone(self.archive._conn.execute("SELECT id FROM temporal_facts WHERE id = ?", (old.id,)).fetchone())

    async def test_repeated_statement_links_existing_version_and_invalidation_is_not_current(self):
        old = await self.fact()
        saved, memory = await self.summary(message_id="repeat")
        await self.archive.save_memory_sources(saved.id, [self.message(message_id="repeat")], fact_ids=[old.id])
        await self.fact("INVALIDATE", valid_from="2026-01-02 12:00:00", source_id="m2")
        notes = await self.archive.get_memory_recall_context([memory.id], scopes=["friend:1"], as_of="2026-01-03 00:00:00")
        self.assertIn("当前未确认有效", notes[memory.id])

    async def test_evidence_survives_queue_cleanup_paginates_and_checks_scope(self):
        saved, memory = await self.summary()
        text = "原话内容" * 1800
        await self.archive.save_memory_sources(saved.id, [self.message(text)])
        await self.archive.save_memory_sources(saved.id, [self.message("不能覆盖原话")])
        await self.archive._run_db(lambda: self.archive._conn.execute("DELETE FROM chat_memory_messages"))
        first = await self.archive.read_memory_evidence(memory.id, scopes=["friend:1"])
        self.assertEqual(len(first["text"]), 4000)
        pieces, page = [], first
        while True:
            pieces.append(page["text"])
            if page["next_offset"] is None:
                break
            page = await self.archive.read_memory_evidence(memory.id, scopes=["friend:1"], offset=page["next_offset"])
        self.assertIn(text, "".join(pieces))
        self.assertNotIn("不能覆盖原话", "".join(pieces))
        self.assertEqual((await self.archive.read_memory_evidence(memory.id, scopes=["friend:2"]))["status"], "unavailable")
        self.assertEqual((await self.archive.read_memory_evidence(memory.id, scopes=[]))["status"], "unavailable")
        self.archive.close()
        self.archive = LifeArchive(self.path)
        self.assertEqual(await self.archive.read_memory_evidence(memory.id, scopes=["friend:1"]), first)

    async def test_missing_original_is_explicit_and_tool_can_read_saved_source(self):
        saved, memory = await self.summary()
        missing = await self.archive.read_memory_evidence(memory.id, scopes=["friend:1"])
        self.assertEqual(missing["status"], "source_unavailable")
        await self.archive.save_memory_sources(saved.id, [self.message()])
        service = self.service()
        result = json.loads(await service.search_text(Event(unified_msg_origin="friend:1"), mode="evidence", memory_id=memory.id))
        self.assertEqual(result["source"], "chat_message")
        self.assertIn("我喜欢红裙", result["text"])

    def service(self, **changes):
        kwargs = dict(archive=lambda: self.archive, group_meta=lambda event: ("", ""),
                      session_id=lambda event: event.unified_msg_origin, message_text=lambda event: "红裙")
        kwargs.update(changes)
        return LifeMemoryService(**kwargs)

    async def test_chat_injection_marks_stale_summary_without_overwriting_it(self):
        saved, memory = await self.summary()
        await self.fact()
        await self.archive.save_memory_sources(saved.id, [self.message()])
        await self.fact("UPDATE", object_value="蓝裙", valid_from="2026-01-02 12:00:00", source_id="m2")
        body = await self.service().build_context(Event(unified_msg_origin="friend:1"))
        self.assertIn("【历史摘要】", body)
        self.assertIn("蓝裙", body)
        stored = self.archive._conn.execute("SELECT content FROM long_term_memories WHERE id = ?", (memory.id,)).fetchone()[0]
        self.assertEqual(stored, "之前喜欢红裙")

    async def test_related_episode_expands_scoped_candidates_and_respects_category(self):
        first = await self.archive.upsert_long_term_memory(LongTermMemoryRecord(
            scope="friend:1", category="episode", content="一起挑电影", source_table="life_episodes", source_id="film"))
        second = await self.archive.upsert_long_term_memory(LongTermMemoryRecord(
            scope="friend:1", category="episode", content="看完讨论结局", source_table="life_episodes", source_id="film"))
        foreign = await self.archive.upsert_long_term_memory(LongTermMemoryRecord(
            scope="friend:2", category="episode", content="别人的结局", source_table="life_episodes", source_id="film"))
        # 即使坏数据把其他会话的记录连进同一簇，读取仍须隔离。
        cluster = self.archive._conn.execute("SELECT cluster_id FROM memory_episode_cluster_items WHERE memory_id = ?", (first.id,)).fetchone()[0]
        await self.archive._run_db(lambda: self.archive._conn.execute(
            "INSERT OR IGNORE INTO memory_episode_cluster_items(cluster_id, memory_id, sort_order) VALUES (?, ?, 9)", (cluster, foreign.id)))
        related = await self.archive.get_related_long_term_memories([first.id], scopes=["friend:1"], categories=[], limit=5)
        self.assertEqual([row.id for row in related], [second.id])
        recalled = await self.service().search("挑电影", event=Event(unified_msg_origin="friend:1"))
        self.assertEqual([row.id for row in recalled], [first.id, second.id])
        self.assertEqual(await self.archive.get_related_long_term_memories([first.id], scopes=["friend:1"], categories=["preference"]), [])
        self.assertEqual(await self.archive.get_related_long_term_memories([foreign.id], scopes=["friend:1"], categories=[]), [])

    async def test_background_compaction_preserves_protected_and_open_memories(self):
        values = dict(scope="friend:1", category="short_term", date="2026-01-01", expires_at="2026-01-07")
        ordinary = await self.archive.upsert_long_term_memory(LongTermMemoryRecord(**values, content="临时小事"))
        important = await self.archive.upsert_long_term_memory(LongTermMemoryRecord(**values, content="重要小事", weight=4))
        await self.archive._run_db(lambda: self.archive._conn.execute(
            "INSERT INTO commitments(content, status, source_session, source_message_id) VALUES ('一起看电影', 'active', 'friend:1', 'promise')"))
        promise = await self.archive.upsert_long_term_memory(LongTermMemoryRecord(**values, content="等你看电影", session_id="friend:1", message_id="promise"))
        correction = await self.archive.upsert_long_term_memory(LongTermMemoryRecord(scope="friend:1", category="correction", content="上次记错了", date="2026-01-01"))
        for text in ("挑好电影", "看完电影"):
            await self.archive.upsert_long_term_memory(LongTermMemoryRecord(
                scope="friend:1", category="episode", content=text, date="2026-01-01", source_table="life_episodes", source_id="shared"))
        result = await self.archive.consolidate_living_memories(today="2026-10-21")
        self.assertEqual(result["dormant"], 1)
        self.assertGreater(result["consolidated"], 0)
        self.assertEqual((await self.archive.consolidate_living_memories(today="2026-10-21"))["consolidated"], 0)
        await self.archive.cleanup_long_term_memories("2026-10-01")
        ids = {row[0] for row in self.archive._conn.execute("SELECT id FROM long_term_memories")}
        self.assertNotIn(ordinary.id, ids)
        self.assertTrue({important.id, promise.id, correction.id} <= ids)

    async def test_v26_schema_migration_retains_data_and_is_idempotent(self):
        await self.summary()
        await self.archive.save()
        self.archive.close()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("DROP TABLE memory_fact_links")
            conn.execute("DROP TABLE memory_source_messages")
            conn.execute("UPDATE meta SET value = '26' WHERE key = 'schema_version'")
        self.archive = LifeArchive(self.path)
        await self.archive.initialize()
        self.assertEqual(self.archive._conn.execute("SELECT COUNT(*) FROM chat_summaries").fetchone()[0], 1)
        self.assertEqual(self.archive._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], str(SCHEMA_VERSION))
        validate_schema(self.archive._conn)
        self.assertEqual(schema_fingerprint(self.archive._conn), CURRENT_SCHEMA_FINGERPRINT)

    async def test_slow_fact_check_does_not_inject_unchecked_summary(self):
        async def wait(*args, **kwargs):
            await asyncio.Event().wait()
        archive = types.SimpleNamespace(search_long_term_memories=AsyncMock(return_value=[LongTermMemoryRecord(id=1, content="旧偏好")]),
                                        get_memory_recall_context=wait)
        service = self.service(archive=lambda: archive)
        result = await asyncio.wait_for(service.search("偏好", event=Event(unified_msg_origin="friend:1")), 0.6)
        self.assertEqual(result, [])


class RecallRankingTest(unittest.TestCase):
    def test_overlap_beats_single_route_and_related_cannot_displace_direct_match(self):
        a = LongTermMemoryRecord(id=1, content="第一路")
        b = LongTermMemoryRecord(id=2, content="两路都相关")
        c = LongTermMemoryRecord(id=3, content="另一语义结果")
        d = LongTermMemoryRecord(id=4, content="关联经历")
        result = fuse_memory_routes([(1.0, [a, b]), (1.0, [c, b]), (0.35, [d])], 4)
        self.assertEqual([row.id for row in result], [2, 1, 3, 4])

    def test_exact_duplicate_keeps_one_slot_and_old_important_memory_is_not_decayed(self):
        a = LongTermMemoryRecord(id=1, content="一样的内容", scope="friend:1")
        b = LongTermMemoryRecord(id=2, content="一样的内容", scope="friend:1")
        self.assertEqual(len(fuse_memory_routes([(1.0, [a, b])], 5)), 1)
        old = LongTermMemoryRecord(id=3, content="重要纠正", category="correction", date="2020-01-01")
        recent = LongTermMemoryRecord(id=4, content="今天闲聊", category="chat_summary", date=life_now().date().isoformat())
        self.assertEqual(fuse_memory_routes([(1.0, [old, recent])], 2)[0].id, 3)

    def test_fresh_schema_matches_migrated_schema(self):
        with sqlite3.connect(":memory:") as conn:
            for script in iter_schema_sql():
                conn.executescript(script)
            self.assertEqual(schema_fingerprint(conn), CURRENT_SCHEMA_FINGERPRINT)
