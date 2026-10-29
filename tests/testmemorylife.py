"""六项记忆能力的来源、隐私、并发和真实进度回归。"""

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from support import DayRecord, LifeArchive, LongTermMemoryRecord
from core.archive.migrations import (
    CURRENT_SCHEMA_FINGERPRINT,
    SCHEMA_VERSION,
    schema_fingerprint,
)
from core.archive.schema import validate_schema
from core.runtime.memorylife import MemoryLifeService
from core.interface.portal.memo import PortalMemoryComponent


class MemoryLifeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "life.db"
        self.archive = LifeArchive(self.path)
        self.life = self.archive.memory_life

    async def asyncTearDown(self):
        self.archive.close()
        self.temp.cleanup()

    async def memory(
        self, text="和小未来一起到江边散步", scope="private:1", date="2026-10-20"
    ):
        return await self.archive.upsert_long_term_memory(
            LongTermMemoryRecord(
                scope=scope,
                category="general",
                title="江边的傍晚",
                content=text,
                date=date,
            )
        )

    async def enrich(self, diary=False):
        offered = await self.life.pending()
        payload = {
            "entities": [
                {"key": "a", "name": "小未来", "kind": "person"},
                {"key": "b", "name": "江边", "kind": "place"},
            ],
            "relations": [
                {
                    "from": "a",
                    "to": "b",
                    "memory_id": r["id"],
                    "relation": "一起散步",
                    "quote": r["content"],
                }
                for r in offered
            ],
        }
        if diary:
            payload["diary"] = {
                "write": True,
                "date": offered[0]["date"],
                "title": "江边的风",
                "content": "一起走过江边，我想记下这一刻。",
                "memory_ids": [r["id"] for r in offered],
            }
        await self.life.save_enrichment(offered, payload)
        return offered, payload

    def runtime(self):
        return types.SimpleNamespace(
            archive=self.archive,
            config=types.SimpleNamespace(
                memory=types.SimpleNamespace(enabled=True, provider=""),
                llm_timeout_seconds=60,
            ),
            mark_page_status_changed=AsyncMock(),
            get_persona_text=AsyncMock(return_value="角色本人"),
        )

    async def test_associations_evidence_and_private_boundaries(self):
        m = await self.memory()
        await self.enrich()
        graph = await self.life.graph(["private:1"])
        self.assertEqual(graph["edges"][0]["memory_id"], m.id)
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(
            (await self.life.graph(["private:2"], memory_ids=[m.id]))["edges"], []
        )
        self.assertEqual((await self.life.graph([]))["edges"], [])

    async def test_unsupported_relationship_is_not_saved(self):
        await self.memory()
        offered = await self.life.pending()
        await self.life.save_enrichment(
            offered,
            {
                "entities": [
                    {"key": "a", "name": "甲", "kind": "person"},
                    {"key": "b", "name": "乙", "kind": "person"},
                ],
                "relations": [
                    {
                        "from": "a",
                        "to": "b",
                        "memory_id": offered[0]["id"],
                        "relation": "夫妻",
                        "quote": "根本不存在的证据",
                    }
                ],
            },
        )
        self.assertEqual((await self.life.graph(["private:1"]))["edges"], [])

    async def test_correction_invalidates_graph_chapters_diary_in_all_read_paths(self):
        m = await self.memory()
        await self.memory("在江边买了一杯水")
        await self.enrich(diary=True)
        candidate = await self.life.chapter_candidate()
        await self.life.save_chapter(
            candidate, "一起走过的一周", "那一周散步时买了水。"
        )
        self.assertTrue(await self.life.chapters(["private:1"]))
        self.assertTrue(
            await self.archive.get_grounded_diary_entries(scope="private:1")
        )
        await self.archive.correct_life_memory(
            "private:1",
            m.id,
            "那天没有去江边",
            evidence_id="test",
            evidence="我明确纠正那天的经历",
        )
        self.assertFalse(await self.life.chapters(["private:1"]))
        self.assertFalse(await self.life.diaries(["private:1"]))
        self.assertFalse(
            await self.archive.get_grounded_diary_entries(scope="private:1")
        )
        self.assertNotIn(
            m.id,
            [e["memory_id"] for e in (await self.life.graph(["private:1"]))["edges"]],
        )

    async def test_background_result_cannot_revive_concurrently_corrected_memory(self):
        m = await self.memory()
        offered = await self.life.pending()
        await self.archive.correct_life_memory(
            "private:1", m.id, "纠正内容", evidence_id="e", evidence="真实纠正"
        )
        self.assertEqual(
            await self.life.save_enrichment(offered, {"entities": [], "relations": []}),
            0,
        )
        self.assertIsNone(
            self.archive._conn.execute(
                "SELECT * FROM memory_enrichments WHERE memory_id=?", (m.id,)
            ).fetchone()
        )

    async def test_chapters_build_hierarchy_and_stop_when_unchanged(self):
        await self.memory()
        await self.memory("散步后一起回家")
        levels = []
        for _ in range(4):
            c = await self.life.chapter_candidate()
            levels.append(c["level"])
            if len(levels) > 1:
                self.assertTrue(c["children"])
            self.assertTrue(
                await self.life.save_chapter(c, "阶段回忆", "有证据的共同经历")
            )
        self.assertEqual(levels, ["week", "month", "quarter", "year"])
        self.assertIsNone(await self.life.chapter_candidate())
        self.assertFalse(await self.life.chapters(["private:2"]))

    async def test_diary_can_decline_and_replay_does_not_duplicate(self):
        await self.memory()
        offered, payload = await self.enrich()
        self.assertFalse(await self.life.diaries(["private:1"]))
        await self.life.save_enrichment(offered, payload)
        self.assertEqual(len((await self.life.graph(["private:1"]))["edges"]), 1)
        self.assertFalse(await self.life.pending())

    async def test_identity_merge_split_and_cross_scope_rejection(self):
        m = await self.memory()
        await self.enrich()
        nodes = (await self.life.graph(["private:1"]))["nodes"]
        person = next(n for n in nodes if n["entity_type"] == "person")
        split = await self.life.manage_identity(
            operation="split",
            entity_id=person["id"],
            name="另一位同名朋友",
            memory_ids=[m.id],
        )
        self.assertIn(
            split, [n["id"] for n in (await self.life.graph(["private:1"]))["nodes"]]
        )
        await self.life.manage_identity(
            operation="merge", entity_id=split, target_id=person["id"]
        )
        self.assertIn(
            person["id"],
            [n["id"] for n in (await self.life.graph(["private:1"]))["nodes"]],
        )
        await self.memory("另一个会话的江边散步", scope="private:2")
        await self.enrich()
        other = next(
            n
            for n in (await self.life.graph(["private:2"]))["nodes"]
            if n["entity_type"] == "person"
        )
        with self.assertRaises(ValueError):
            await self.life.manage_identity(
                operation="merge", entity_id=person["id"], target_id=other["id"]
            )

    async def test_originals_survive_queue_deletion_and_restart(self):
        row = dict(
            session_id="private:1",
            event_key="user:1",
            role="user",
            sender_name="未来",
            occurred_at="2026-10-20 12:00:00",
            message_text="原话与摘要不同",
        )
        await self.archive.enqueue_chat_memory_message(row)
        await self.archive.enqueue_chat_memory_message(row)
        self.archive._conn.execute("DELETE FROM chat_memory_messages")
        self.archive.close()
        self.archive = LifeArchive(self.path)
        self.life = self.archive.memory_life
        result = await self.life.originals(
            ["private:1"], start="2026-10-20", end="2026-10-20 23:59:59"
        )
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["message_text"], row["message_text"])
        self.assertFalse((await self.life.originals(["private:2"]))["messages"])

    async def test_library_duplicate_and_real_progress_and_quote_validation(self):
        source = await self.life.import_source(
            scope="global",
            title="测试资料",
            content="第一段需要水。\n" + "第二段内容。" * 600,
        )
        same = await self.life.import_source(
            scope="global",
            title="重复导入",
            content="第一段需要水。\n" + "第二段内容。" * 600,
        )
        self.assertTrue(same["duplicate"])
        self.assertEqual(source["id"], same["id"])
        p = await self.life.passage(source["id"], ["global"])
        self.assertFalse(
            await self.life.save_reading(
                p, {"understood": True, "quote": "编造引用", "summary": "读过了"}
            )
        )
        self.assertFalse(
            await self.life.save_reading(
                p, {"understood": False, "quote": "第一段需要水。", "summary": "不确定"}
            )
        )
        payload = {
            "understood": True,
            "quote": "第一段需要水。",
            "summary": "这段提到了水。",
            "reflection": "还没想清其他情形",
            "question": "要多少水？",
        }
        self.assertTrue(await self.life.save_reading(p, payload))
        self.assertFalse(await self.life.save_reading(p, payload))
        self.assertEqual((await self.life.library(["global"]))[0]["read_count"], 1)
        p2 = await self.life.passage(source["id"], ["global"])
        self.assertNotEqual(p2["id"], p["id"])
        self.assertEqual(p2["previous"]["summary"], payload["summary"])
        self.assertIsNone(await self.life.passage(source["id"], ["private:2"]))
        await self.life.set_source_status(source["id"], "paused")
        self.assertIsNone(await self.life.passage(source["id"], ["global"]))

    async def test_autonomous_reading_only_for_selected_running_study(self):
        source = await self.life.import_source(
            scope="global", title="水的资料", content="这段原文提到水。"
        )
        runtime = self.runtime()
        runtime.archive.get_continuous_life = AsyncMock(
            return_value={
                "run": {
                    "status": "paused",
                    "action": {
                        "action_type": "study",
                        "action_id": "a",
                        "payload": {"knowledge_source_id": source["id"]},
                    },
                }
            }
        )
        service = MemoryLifeService(runtime)
        service.model = AsyncMock(
            return_value={
                "understood": True,
                "quote": "原文提到水",
                "summary": "资料提到了水",
            }
        )
        self.assertFalse(await service.read_current_activity())
        service.model.assert_not_awaited()
        runtime.archive.get_continuous_life.return_value["run"]["status"] = "running"
        self.assertTrue(await service.read_current_activity())
        self.assertEqual((await self.life.library(["global"]))[0]["read_count"], 1)

    async def reading_runtime(self, payload):
        runtime = self.runtime()
        date = "2026-10-29"
        action = {"action_id": "reading-new", "action_type": "study", "payload": payload}
        await self.archive.save_day(DayRecord(
            date=date, meta={"planned_life_actions": json.dumps([action])},
        ))
        await self.archive.mutate_continuous_life(date, lambda day, world: world.update(
            run={"status": "running", "date": date, "action": action},
        ))
        runtime.composer = types.SimpleNamespace(search=types.SimpleNamespace(
            search=AsyncMock(return_value=types.SimpleNamespace(
                status="ok", sources=[types.SimpleNamespace(as_dict=lambda: {
                    "title": "水的流动", "url": "https://example.org/water",
                })],
            )),
            fetch=AsyncMock(return_value={
                "status": "ok", "mode": "page_extract", "url": "https://example.org/water",
                "content": "水会沿着地势流向低处。观察水流也能帮助理解地形。",
            }),
        ))
        service = MemoryLifeService(runtime)
        service.model = AsyncMock(side_effect=[
            {"index": 0},
            {"understood": True, "quote": "水会沿着地势流向低处", "summary": "地势影响水流方向"},
        ])
        return runtime, service

    async def test_autonomous_search_fetch_read_binds_real_source_to_action(self):
        runtime, service = await self.reading_runtime({"knowledge_query": "水流方向"})
        self.assertTrue(await service.read_current_activity())
        runtime.composer.search.search.assert_awaited_once_with("水流方向", source_scope="web")
        runtime.composer.search.fetch.assert_awaited_once_with("https://example.org/water")
        sources = await self.life.library(["self"])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["read_count"], 1)
        self.assertEqual(await self.life.library(["private:someone"]), [])
        world = await self.archive.get_continuous_life()
        sid = world["run"]["action"]["payload"]["knowledge_source_id"]
        day = await self.archive.get_day("2026-10-29")
        self.assertEqual(json.loads(day.meta["planned_life_actions"])[0]["payload"]["knowledge_source_id"], sid)
        self.assertEqual(len(await self.life.reading_receipts("reading-new", sid)), 1)
        self.assertFalse(await service.read_current_activity())
        self.assertEqual(service.model.await_count, 2)

    async def test_search_excerpt_does_not_count_as_page_or_reading(self):
        runtime, service = await self.reading_runtime({"knowledge_url": "https://example.org/water"})
        runtime.composer.search.fetch.return_value["mode"] = "search_fallback"
        self.assertFalse(await service.read_current_activity())
        self.assertEqual(await self.life.library(["self"]), [])
        service.model.assert_not_awaited()

    async def test_interrupted_page_fetch_does_not_import_or_credit_reading(self):
        runtime, service = await self.reading_runtime({"knowledge_url": "https://example.org/water"})
        page = runtime.composer.search.fetch.return_value

        async def fetch(url):
            await self.archive.mutate_continuous_life(
                "2026-10-29", lambda day, world: world["run"].update(status="paused"),
            )
            return page

        runtime.composer.search.fetch.side_effect = fetch
        self.assertFalse(await service.read_current_activity())
        self.assertEqual(await self.life.library(["self"]), [])
        service.model.assert_not_awaited()

    async def test_knowledge_note_completes_matching_running_study(self):
        source = await self.life.import_source(
            scope="global", title="水的资料", content="原文提到水会流动。"
        )
        passage = await self.life.passage(source["id"], ["global"])
        runtime = self.runtime()
        runtime.life_memory = types.SimpleNamespace(
            _event_memory_scopes=lambda event: ["global"]
        )
        runtime.archive.get_continuous_life = AsyncMock(
            return_value={
                "run": {
                    "status": "running",
                    "action": {
                        "action_type": "study",
                        "action_id": "study-note-1",
                        "payload": {
                            "knowledge_source_id": source["id"],
                            "knowledge_passage_id": passage["id"],
                        },
                    },
                }
            }
        )
        service = MemoryLifeService(runtime)
        result = await service.knowledge_tool(
            None,
            action="note",
            source_id=source["id"],
            passage_id=passage["id"],
            quote="原文提到水",
            summary="水会流动",
        )
        self.assertIn('"saved": true', result)
        receipts = await self.life.reading_receipts("study-note-1", source["id"])
        self.assertEqual(len(receipts), 1)

    async def test_reading_interruption_and_failure_do_not_advance(self):
        source = await self.life.import_source(
            scope="global", title="资料", content="可核对的原文"
        )
        runtime = self.runtime()
        run = {
            "run": {
                "status": "running",
                "action": {
                    "action_type": "study",
                    "action_id": "a",
                    "payload": {"knowledge_source_id": source["id"]},
                },
            }
        }
        runtime.archive.get_continuous_life = AsyncMock(
            side_effect=[run, {"run": {"status": "paused"}}]
        )
        service = MemoryLifeService(runtime)
        service.model = AsyncMock(
            return_value={"understood": True, "quote": "原文", "summary": "理解"}
        )
        self.assertFalse(await service.read_current_activity())
        self.assertEqual((await self.life.library(["global"]))[0]["read_count"], 0)

    async def test_background_operations_fail_independently(self):
        service = MemoryLifeService(self.runtime())
        service.read_current_activity = AsyncMock(return_value=False)
        service.enrich = AsyncMock(side_effect=RuntimeError("provider offline"))
        service.summarize = AsyncMock(return_value=False)
        await service.tick()
        service.summarize.assert_awaited_once()
        service.read_current_activity.assert_awaited_once()

    async def test_dashboard_data_and_import_wiring(self):
        m = await self.memory()
        args = {"section": "memories", "scope": "private:1"}

        async def wrapped(fn):
            return await fn()

        ctx = types.SimpleNamespace(
            runtime=self.runtime(),
            _page_query_args=lambda: args,
            _page_json=wrapped,
            _page_json_body=AsyncMock(
                return_value={
                    "operation": "import",
                    "title": "给角色的资料",
                    "content": "完整来源文字",
                }
            ),
        )
        portal = PortalMemoryComponent(ctx)
        result = await portal.page_memory_center()
        self.assertEqual(result["data"][0]["id"], m.id)
        await portal.page_memory_manage()
        args.update(section="library", scope="global")
        self.assertEqual(
            (await portal.page_memory_center())["data"]["sources"][0]["title"],
            "给角色的资料",
        )

    async def test_timeline_combines_real_notes_without_repeating_diary_sources(self):
        memory = await self.memory()
        await self.enrich(diary=True)
        later = await self.memory("后来独自看了一场雨", date="2026-10-21")
        await self.memory("别的关系里的经历", scope="private:2")
        source = await self.life.import_source(
            scope="global", title="水的资料", content="原文里写到水会流动。"
        )
        passage = await self.life.passage(source["id"], ["global"])
        await self.life.save_reading(
            passage,
            {
                "understood": True,
                "quote": "水会流动",
                "summary": "水会流动。",
                "reflection": "想再看看水如何流过石缝。",
                "question": "流速有什么影响？",
            },
        )
        result = await self.life.timeline(["private:1", "global"])
        items = result["items"]
        self.assertEqual([r["kind"] for r in items], ["reading", "memory", "diary"])
        self.assertEqual(items[1]["memory_ids"], [later.id])
        self.assertEqual(items[2]["memory_ids"], [memory.id])
        self.assertIn("想再看看", items[0]["content"])
        self.assertEqual(items[0]["passage_id"], passage["id"])
        self.assertEqual(len({r["id"] for r in items}), 3)
        self.assertIsNone(result["next_offset"])
        self.assertFalse((await self.life.timeline([]))["items"])
        self.assertEqual(len((await self.life.timeline(["private:2"]))["items"]), 1)

    async def test_timeline_drops_invalid_diary_and_shows_corrected_source(self):
        memory = await self.memory()
        await self.enrich(diary=True)
        await self.archive.correct_life_memory(
            "private:1",
            memory.id,
            "那天其实留在了家里",
            evidence_id="test",
            evidence="对方在聊天中主动纠正了经历",
        )
        items = (await self.life.timeline(["private:1"]))["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "memory")
        self.assertEqual(items[0]["content"], "那天其实留在了家里")
        self.assertNotIn(memory.id, items[0]["memory_ids"])
        self.assertEqual(len(items[0]["memory_ids"]), 1)

    async def test_timeline_default_page_is_read_only_and_paginates(self):
        for day in range(1, 15):
            await self.memory(f"十月{day}日的经历", date=f"2026-10-{day:02}")

        async def wrapped(fn):
            return await fn()

        args = {"scope": "private:1"}
        context = types.SimpleNamespace(
            runtime=self.runtime(),
            _page_query_args=lambda: args,
            _page_json=wrapped,
        )
        portal = PortalMemoryComponent(context)
        changes = self.archive._conn.total_changes
        result = await portal.page_memory_center()
        self.assertEqual(len(result["data"]["items"]), 12)
        self.assertEqual(result["data"]["items"][0]["date"], "2026-10-14")
        self.assertEqual(result["data"]["next_offset"], 12)
        args["offset"] = 12
        earlier = (await portal.page_memory_center())["data"]
        self.assertEqual(len(earlier["items"]), 2)
        self.assertIsNone(earlier["next_offset"])
        self.assertFalse(
            {r["id"] for r in earlier["items"]}
            & {r["id"] for r in result["data"]["items"]}
        )
        self.assertEqual(self.archive._conn.total_changes, changes)

    async def test_v31_migration_carries_original_messages_and_reopens(self):
        await self.archive.enqueue_chat_memory_message(
            dict(
                session_id="p",
                event_key="x",
                role="user",
                occurred_at="2026-10-20",
                message_text="升级前原话",
            )
        )
        for table in (
            "knowledge_readings",
            "knowledge_passages",
            "knowledge_sources",
            "memory_identity_redirects",
            "memory_associations",
            "memory_enrichments",
            "memory_chapters",
            "conversation_originals",
        ):
            self.archive._conn.execute(f"DROP TABLE {table}")
        self.archive._conn.execute(
            "UPDATE meta SET value='31' WHERE key='schema_version'"
        )
        self.archive.close()
        self.archive = LifeArchive(self.path)
        self.life = self.archive.memory_life
        validate_schema(self.archive._conn)
        self.assertEqual(
            schema_fingerprint(self.archive._conn), CURRENT_SCHEMA_FINGERPRINT
        )
        self.assertEqual(
            self.archive._conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0],
            str(SCHEMA_VERSION),
        )
        self.assertEqual(
            (await self.life.originals(["p"]))["messages"][0]["message_text"],
            "升级前原话",
        )

    async def test_read_search_uses_full_passage_and_validates_quotes(self):
        source = await self.life.import_source(
            scope="global", title="无关标题", content="文章深处解释了毛细作用。"
        )
        await self.life.import_source(
            scope="private:other", title="秘密", content="不应被读取"
        )
        passage = await self.life.passage(source["id"], ["global"])
        service = MemoryLifeService(self.runtime())
        service.model = AsyncMock(
            return_value={
                "matches": [
                    {
                        "passage_id": passage["id"],
                        "quote": "毛细作用",
                        "reason": "回答问题",
                    },
                    {"passage_id": passage["id"], "quote": "不存在的引用"},
                ]
            }
        )
        found = await service.search_sources("水为什么上升", ["global"])
        self.assertEqual(len(found["matches"]), 1)
        self.assertEqual(found["matches"][0]["source_id"], source["id"])
        sent = service.model.call_args.args[1]["passages"]
        self.assertEqual(len(sent), 1)
        self.assertIn("毛细作用", sent[0]["content"])
        self.assertEqual((await self.life.library(["global"]))[0]["read_count"], 0)

    async def test_merged_identity_is_not_revived_by_future_memory(self):
        m = await self.memory()
        await self.enrich()
        person = next(
            n
            for n in (await self.life.graph(["private:1"]))["nodes"]
            if n["entity_type"] == "person"
        )
        new = await self.life.manage_identity(
            operation="split",
            entity_id=person["id"],
            name="确认的名字",
            memory_ids=[m.id],
        )
        await self.life.manage_identity(
            operation="merge", entity_id=person["id"], target_id=new
        )
        await self.archive.upsert_long_term_memory(
            LongTermMemoryRecord(
                scope="private:1",
                category="relationship",
                title=person["name"],
                content="以旧称呼记录的一条新事实",
                date="2026-10-21",
            )
        )
        row = self.archive._conn.execute(
            "SELECT status FROM memory_entities WHERE id=?", (person["id"],)
        ).fetchone()
        self.assertEqual(row["status"], "merged")
        self.assertIn(
            person["name"],
            self.archive._conn.execute(
                "SELECT aliases FROM memory_entities WHERE id=?", (new,)
            ).fetchone()[0],
        )

    async def test_reading_evidence_survives_restart_and_reread_keeps_history(self):
        source = await self.life.import_source(
            scope="global", title="资料", content="一份原文的依据"
        )
        offered = await self.life.passage(source["id"], ["global"])
        payload = {
            "understood": True,
            "quote": "原文的依据",
            "summary": "第一次理解",
            "reflection": "先保留想法",
        }
        self.assertTrue(
            await self.life.save_reading(offered, payload, action_id="study-1")
        )
        self.archive.close()
        self.archive = LifeArchive(self.path)
        self.life = self.archive.memory_life
        self.assertEqual(
            len(await self.life.reading_receipts("study-1", source["id"])), 1
        )
        again = await self.life.passage(
            source["id"], ["global"], passage_id=offered["id"]
        )
        payload.update(summary="重新理解", reflection="这次有不同看法")
        self.assertTrue(
            await self.life.save_reading(again, payload, action_id="study-2")
        )
        notes = await self.life.reading_notes(["global"])
        self.assertEqual(notes[0]["previous_reading_id"], notes[1]["id"])
        self.assertEqual((await self.life.library(["global"]))[0]["read_count"], 1)

    async def test_source_withdrawal_during_read_does_not_create_receipt(self):
        source = await self.life.import_source(
            scope="global", title="资料", content="原文内容"
        )
        offered = await self.life.passage(source["id"], ["global"])
        await self.life.set_source_status(source["id"], "removed")
        self.assertFalse(
            await self.life.save_reading(
                offered,
                {"understood": True, "quote": "原文", "summary": "已理解"},
                action_id="study",
            )
        )
        self.assertFalse(await self.life.reading_receipts("study", source["id"]))

    async def test_longterm_clear_removes_derived_tables_without_fk_errors(self):
        await self.memory()
        await self.enrich()
        await self.life.import_source(scope="global", title="资料", content="内容")
        await self.archive.clear_storage_category("longterm")
        self.assertFalse(await self.life.library(["global"]))
        self.assertFalse((await self.life.graph(["private:1"]))["edges"])
        self.assertFalse(
            self.archive._conn.execute("PRAGMA foreign_key_check").fetchall()
        )
