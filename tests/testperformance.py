"""性能修复的并发、缓存时效、取消清理与历史完整性回归；不调用外部服务。"""

import asyncio
import json
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import DailyLifeRuntime, LifeArchive, LifeSettings
from core.archive.lifetraces import revision
from core.interface.portal.entry import PortalBaseComponent
from core.runtime.memorylife import MemoryLifeService
from core.runtime.scopes import RuntimeScopeState
from core.search.cache import SingleFlight


class SnapshotConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.config = LifeSettings.from_dict({})
        self.runtime._injection_snapshot_cache = {}
        self.runtime._page_status_version = 0
        self.runtime._page_status_changed = asyncio.Condition()
        self.runtime._settle_stale_reply_effects = AsyncMock()
        self.runtime.archive = types.SimpleNamespace(living_revision=0)

    async def asyncTearDown(self):
        await self.runtime._close_injection_snapshot_flight()

    async def test_invalidation_does_not_join_old_inflight_snapshot(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def read(**kwargs):
            version = self.runtime._page_status_version
            calls.append(version)
            if version == 0:
                started.set()
                await release.wait()
            return {"version": version}

        self.runtime.context_snapshot = types.SimpleNamespace(read=read)
        first = asyncio.create_task(self.runtime._gather_life_context_snapshot())
        await asyncio.wait_for(started.wait(), 1)
        await self.runtime.mark_page_status_changed()
        try:
            new = await asyncio.wait_for(self.runtime._gather_life_context_snapshot(), 1)
            self.assertEqual(new["version"], 1)
        finally:
            release.set()
            await first
        self.assertEqual(calls, [0, 1])
        self.assertEqual((await self.runtime._gather_life_context_snapshot())["version"], 1)

    async def test_cache_replacement_does_not_join_previous_configuration(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def read(**kwargs):
            nonlocal calls
            calls += 1
            value = calls
            if value == 1:
                started.set()
                await release.wait()
            return {"value": value}

        self.runtime.context_snapshot = types.SimpleNamespace(read=read)
        first = asyncio.create_task(self.runtime._gather_life_context_snapshot())
        await asyncio.wait_for(started.wait(), 1)
        self.runtime._injection_snapshot_cache = {}
        try:
            new = await asyncio.wait_for(self.runtime._gather_life_context_snapshot(), 1)
            self.assertEqual(new["value"], 2)
        finally:
            release.set()
            await first

    async def test_shared_and_cached_top_level_projection_is_isolated(self):
        async def read(**kwargs):
            await asyncio.sleep(0)
            return {"facts": [{"outfit": "原穿搭"}]}
        self.runtime.context_snapshot = types.SimpleNamespace(read=AsyncMock(side_effect=read))
        first, second = await asyncio.gather(*(self.runtime._gather_life_context_snapshot() for _ in range(2)))
        first["facts"] = [{"outfit": "不应污染缓存"}]
        self.assertEqual(second["facts"][0]["outfit"], "原穿搭")
        cached = await self.runtime._gather_life_context_snapshot()
        self.assertEqual(cached["facts"][0]["outfit"], "原穿搭")
        self.assertEqual(self.runtime.context_snapshot.read.await_count, 1)

    async def test_sender_reads_coalesce_but_remain_fresh_and_private(self):
        self.runtime.context_snapshot = types.SimpleNamespace(read=AsyncMock(return_value={}))
        self.runtime._event_session_id = lambda e: "platform:group:1"
        self.runtime._event_group_meta = lambda e: ("1", "")
        async def expression(**kwargs):
            await asyncio.sleep(.01)
            return [{"sender": kwargs["profile_id"]}]
        self.runtime.archive.get_expression_context = AsyncMock(side_effect=expression)
        event = lambda sender: types.SimpleNamespace(get_sender_id=lambda: sender)
        results = await asyncio.gather(*(self.runtime._gather_life_context_snapshot(event(s)) for s in ("a", "a", "b")))
        self.assertEqual([r["expression_profiles"][0]["sender"] for r in results], ["a", "a", "b"])
        self.assertEqual(self.runtime.archive.get_expression_context.await_count, 2)
        results[0]["expression_profiles"][0]["sender"] = "changed"
        self.assertEqual(results[1]["expression_profiles"][0]["sender"], "a")
        await self.runtime._gather_life_context_snapshot(event("a"))
        self.assertEqual(self.runtime.archive.get_expression_context.await_count, 3)

    async def test_scope_eviction_handles_old_and_versioned_keys(self):
        keys = [json.dumps([3, "group", "session"]), json.dumps([3, "group", "session", 8])]
        keep = json.dumps([3, "other", "other-session", 8])
        self.runtime._injection_snapshot_cache = {key: {} for key in [*keys, keep]}
        RuntimeScopeState(self.runtime)._remove_snapshot_aliases({"group", "session"})
        self.assertEqual(list(self.runtime._injection_snapshot_cache), [keep])

    async def test_cache_uses_lru_without_extending_ttl(self):
        part = self.runtime._part_snapshot_pack_component
        self.runtime._INJECTION_SNAPSHOT_CACHE_LIMIT = 2
        cache = self.runtime._injection_snapshot_cache
        with patch("core.runtime.mirror.pack.time.monotonic", return_value=100):
            part._store_injection_snapshot(cache, "a", {"a": 1})
            part._store_injection_snapshot(cache, "b", {"b": 1})
        with patch("core.runtime.mirror.pack.time.monotonic", return_value=107):
            self.runtime._cached_injection_snapshot(cache, "a")
            part._store_injection_snapshot(cache, "c", {"c": 1})
            self.assertIn("a", cache)
            self.assertNotIn("b", cache)
        with patch("core.runtime.mirror.pack.time.monotonic", return_value=109):
            self.assertIsNone(self.runtime._cached_injection_snapshot(cache, "a"))

    async def test_closed_shared_service_rejects_new_work(self):
        flight = SingleFlight()
        factory = AsyncMock(return_value=1)
        await flight.close()
        with self.assertRaises(RuntimeError):
            await flight.run("key", factory)
        factory.assert_not_awaited()


class BoundedArchiveTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.temp.name) / "test.db")
        self.life = self.archive.memory_life

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.temp.cleanup()

    def seed(self, count=50):
        self.archive._conn.executemany(
            "INSERT INTO long_term_memories(scope,content,date) VALUES(?,?,?)",
            [("private:a" if i % 2 == 0 else "private:b", f"证据 {i}", "2026-10-20") for i in range(count)],
        )

    async def test_pending_preserves_scope_order_budget_and_revisions(self):
        self.seed()
        result = await self.life.pending(limit=8)
        self.assertEqual([r["id"] for r in result], list(range(1, 16, 2)))
        self.assertTrue(all(r["revision"] == revision(r) for r in result))
        for r in result:
            self.archive._conn.execute("INSERT INTO memory_enrichments(memory_id,source_revision,processed_at) VALUES(?,?,?)", (r["id"],r["revision"],"now"))
        self.assertEqual([r["id"] for r in await self.life.pending(limit=2)], [2, 4])
        self.archive._conn.execute("UPDATE long_term_memories SET content='纠正证据' WHERE id=1")
        self.assertEqual((await self.life.pending(limit=1))[0]["id"], 1)

    async def test_source_validation_handles_large_batches_corrections_and_scope(self):
        self.seed(1002)
        rows = self.life._memories(["private:a"])
        refs = {str(r["id"]): revision(r) for r in rows}
        self.assertTrue(self.life._valid_sources(refs, "private:a"))
        self.assertFalse(self.life._valid_sources(refs, "private:b"))
        self.archive._conn.execute("UPDATE long_term_memories SET status='removed' WHERE id=1")
        self.assertFalse(self.life._valid_sources(refs, "private:a"))

    async def test_chapters_skip_invalid_latest_without_hiding_valid_older(self):
        self.seed(4)
        row = self.life._memories(["private:a"])[0]
        for scope, period, refs in (("private:a","2026-W41",{str(row["id"]):revision(row)}),
                                    ("private:a","2026-W42",{"999":"invalid"}),
                                    ("private:b","2026-W43",{str(row["id"]):revision(row)})):
            self.archive._conn.execute("INSERT INTO memory_chapters(scope,level,period,title,content,sources_json,updated_at) VALUES(?,?,?,?,?,?,?)", (scope,"week",period,"title","body",json.dumps(refs),"now"))
        self.assertEqual([r["period"] for r in await self.life.chapters(["private:a"],1)], ["2026-W41"])
        self.assertEqual(await self.life.chapters([]), [])
        self.assertEqual(len(await self.life.chapters(["private:a"],0)),1)

    async def test_reading_notes_sql_scope_order_and_limits(self):
        for i, scope in enumerate(("global","private:a"), 1):
            self.archive._conn.execute("INSERT INTO knowledge_sources(id,scope,title,content_hash,created_at) VALUES(?,?,?,?,?)",(i,scope,"资料",str(i),"now"))
            self.archive._conn.execute("INSERT INTO knowledge_passages(id,source_id,ordinal,content) VALUES(?,?,1,'内容')",(i,i))
        self.archive._conn.executemany("INSERT INTO knowledge_readings(passage_id,summary,evidence_quote,created_at) VALUES(?,'摘要','内容','now')",[(1+i%2,) for i in range(220)])
        self.assertEqual([r["id"] for r in await self.life.reading_notes(["global"],3)], [219,217,215])
        self.assertEqual(await self.life.reading_notes([]), [])
        self.assertEqual(len(await self.life.reading_notes(["global"],999)),100)
        self.assertEqual(len(await self.life.reading_notes(["global"],0)),1)


class UploadResponsivenessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.target = Path(self.temp.name) / "upload.bin"
        self.base = PortalBaseComponent(None)
        self.base.context = self.base

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_async_upload_open_and_close_run_off_event_loop(self):
        main = threading.get_ident()
        threads = []
        real_open = Path.open
        def slow_open(path, *args, **kwargs):
            threads.append(threading.get_ident())
            time.sleep(.04)
            return real_open(path, *args, **kwargs)
        upload = types.SimpleNamespace(read=AsyncMock(side_effect=[b"123", b""]))
        heartbeats = 0
        async def heartbeat():
            nonlocal heartbeats
            for _ in range(8):
                await asyncio.sleep(.005)
                heartbeats += 1
        with patch.object(Path,"open",slow_open):
            size, _ = await asyncio.gather(self.base._page_copy_upload(upload,self.target,max_bytes=3), heartbeat())
        self.assertEqual(size,3)
        self.assertEqual(self.target.read_bytes(),b"123")
        self.assertTrue(all(t != main for t in threads))
        self.assertEqual(heartbeats,8)

    async def test_cancel_during_async_open_closes_and_removes_file(self):
        entered, release = threading.Event(), threading.Event()
        real_open = Path.open
        def slow_open(path,*args,**kwargs):
            entered.set()
            release.wait(2)
            return real_open(path,*args,**kwargs)
        upload=types.SimpleNamespace(read=AsyncMock(return_value=b""))
        with patch.object(Path,"open",slow_open):
            task=asyncio.create_task(self.base._page_copy_upload(upload,self.target,max_bytes=10))
            await asyncio.wait_for(asyncio.to_thread(entered.wait),1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task,2)
        self.assertFalse(self.target.exists())

    async def test_cancel_sync_upload_waits_for_worker_before_removal(self):
        entered, release = threading.Event(), threading.Event()
        calls=0
        def read(size):
            nonlocal calls
            calls+=1
            if calls==1:
                entered.set()
                release.wait(2)
                return b"data"
            return b""
        task=asyncio.create_task(self.base._page_copy_upload(types.SimpleNamespace(read=read),self.target,max_bytes=10))
        await asyncio.wait_for(asyncio.to_thread(entered.wait),1)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task,2)
        self.assertFalse(self.target.exists())

    async def test_async_oversize_and_read_failure_remove_partial_file(self):
        for chunks, error in (([b"1234"],ValueError),([b"1",RuntimeError("read failed")],RuntimeError)):
            with self.assertRaises(error):
                await self.base._page_copy_upload(types.SimpleNamespace(read=AsyncMock(side_effect=chunks)),self.target,max_bytes=3)
            self.assertFalse(self.target.exists())


class StableMemoryPromptTest(unittest.IsolatedAsyncioTestCase):
    async def test_persona_prefix_is_stable_while_new_memories_remain_current(self):
        runtime=types.SimpleNamespace(config=types.SimpleNamespace(memory=types.SimpleNamespace(provider="test"),llm_timeout_seconds=60),
            get_text_provider=AsyncMock(return_value=object()),get_persona_text=AsyncMock(return_value="稳定人设"),
            call_text_model=AsyncMock(return_value="{}"),close_text_session=AsyncMock())
        service=MemoryLifeService(runtime)
        for content in ("旧经历","新经历"):
            await service.model("规则",{"memories":[content]},persona=True)
        prompts=[call.args[1] for call in runtime.call_text_model.await_args_list]
        self.assertEqual(prompts[0].split(',"memories":')[0],prompts[1].split(',"memories":')[0])
        self.assertEqual([json.loads(p)["memories"] for p in prompts],[["旧经历"],["新经历"]])
        self.assertEqual(runtime.close_text_session.await_count,2)
