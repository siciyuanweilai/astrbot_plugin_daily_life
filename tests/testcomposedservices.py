"""组合服务的并发、缓存隔离、撤回与状态提交回归。"""

import asyncio
import datetime
import json
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from support import Context, DailyLifeRuntime, DataManager, DayRecord, Event, LifeSettings, LongTermMemoryRecord, Provider
from core.runtime.background import BackgroundTaskScheduler
from core.runtime.meaning import MeaningService
from core.runtime.bonding import contextual_relationship_memories
from core.runtime.reaction import ToolReactionService
from core.runtime.status import StateRefreshService, _StateRefreshSpec


class MeaningServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.archive = DataManager()
        self.config = types.SimpleNamespace(
            embedding_provider="embedding", semantic_ranking_model_enabled=False,
            provider="memory", ranking_provider="ranking",
        )
        self.model = "model-a"
        self.provider = types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(id="embedding", model=self.model),
            get_embedding=AsyncMock(return_value=[1.0, 0.0]),
            get_embeddings=AsyncMock(side_effect=lambda values: [[1.0, 0.0] for _ in values]),
        )
        self.scheduler = BackgroundTaskScheduler(normal_limit=1)
        self.call_model = AsyncMock()
        self.text_provider = AsyncMock(return_value=object())
        self.close_session = AsyncMock()
        self.service = self.make_service()
        self.service._MEMORY_INDEX_REQUEST_INTERVAL_SECONDS = 0

    def make_service(self):
        return MeaningService(
            embedding_providers=lambda: [self.provider],
            memory_config=lambda: self.config,
            archive=lambda: self.archive,
            text_provider=self.text_provider,
            call_text_model=self.call_model,
            close_text_session=self.close_session,
            schedule_background=self.scheduler.schedule,
        )

    async def asyncTearDown(self):
        await self.service.close()
        await self.scheduler.cancel_all()

    async def drain(self):
        if self.scheduler.tasks:
            await asyncio.gather(*list(self.scheduler.tasks))

    async def query(self, text="电影"):
        return await self.service._meaning_query_vector(self.provider, self.model, text)

    async def test_concurrent_normalized_queries_share_request_and_results_are_copies(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def embed(query):
            started.set()
            await release.wait()
            return [1.0, 0.0]

        self.provider.get_embedding.side_effect = embed
        first = asyncio.create_task(self.query("Ａ  B"))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(self.query("A B"))
        await asyncio.sleep(0)
        release.set()
        left, right = await asyncio.gather(first, second)
        left[0] = 99
        self.assertEqual(right, [1.0, 0.0])
        self.assertEqual(await self.query("A B"), [1.0, 0.0])
        self.provider.get_embedding.assert_awaited_once()

    async def test_semantic_search_recalls_paraphrase_without_lexical_overlap(self):
        self.archive.long_term_memories[1] = LongTermMemoryRecord(
            id=1, content="我不吃香菜", weight=1.0,
        )
        self.archive.long_term_memories[2] = LongTermMemoryRecord(
            id=2, content="我喜欢晴天散步", weight=1.0,
        )
        self.provider.get_embedding.side_effect = lambda text: (
            [1.0, 0.0] if "香菜" in text or "芫荽" in text else [0.0, 1.0]
        )
        self.service.start_memory_index()
        await self.drain()
        found = await self.service.search_long_term_memories(
            "我排斥芫荽", lexical=[], scopes=[], categories=[], limit=2,
        )
        self.assertEqual([item.id for item in found], [1])

    async def test_old_memory_is_recalled_beyond_recent_candidate_window(self):
        for identifier in range(1, 131):
            self.archive.long_term_memories[identifier] = LongTermMemoryRecord(
                id=identifier, scope="friend:1", content="我不吃香菜" if identifier == 1 else f"散步记录 {identifier}",
            )
        self.archive.long_term_memories[131] = LongTermMemoryRecord(
            id=131, scope="friend:2", content="别人的饮食秘密",
        )
        self.provider.get_embedding.side_effect = lambda text: (
            [1.0, 0.0] if "香菜" in text or "芫荽" in text or "秘密" in text else [0.0, 1.0]
        )
        self.service.start_memory_index()
        await self.drain()
        found = await self.service.search_long_term_memories(
            "我排斥芫荽", lexical=[], scopes=["friend:1"], categories=[], limit=2,
        )
        self.assertEqual([item.id for item in found], [1])
        self.provider.get_embeddings.assert_not_awaited()
        count = self.provider.get_embedding.await_count
        await self.service.search_long_term_memories(
            "我排斥芫荽", lexical=[], scopes=["friend:1"], categories=[], limit=2,
        )
        self.assertEqual(self.provider.get_embedding.await_count, count)

    async def test_cold_recall_does_not_wait_for_slow_index_and_cancel_does_not_lose_it(self):
        self.archive.long_term_memories[1] = LongTermMemoryRecord(id=1, content="我不吃香菜")
        entered, release = asyncio.Event(), asyncio.Event()

        async def embed(text):
            entered.set()
            await release.wait()
            return [1.0, 0.0]

        self.provider.get_embedding.side_effect = embed
        found = await asyncio.wait_for(self.service.search_long_term_memories(
            "我排斥芫荽", lexical=[], scopes=[], categories=[], limit=2,
        ), 0.2)
        self.assertEqual(found, [])
        await asyncio.wait_for(entered.wait(), 1)
        await self.service.search_long_term_memories("另一句话", lexical=[], scopes=[], categories=[], limit=2)
        self.assertEqual(len(self.scheduler.keys), 1)
        release.set()
        await self.drain()
        self.assertEqual(set(await self.archive.get_memory_vectors("long_term_memory", ["1"])), {"1"})
        self.provider.get_embedding.assert_awaited_once()

    async def test_history_index_waits_within_background_budget(self):
        self.archive.long_term_memories[1] = LongTermMemoryRecord(id=1, content="较慢的旧日经历")
        self.service._MEMORY_INDEX_TIMEOUT_SECONDS = 0.2

        async def embed(text):
            await asyncio.sleep(0.03)
            return [1.0, 0.0]

        self.provider.get_embedding.side_effect = embed
        self.service.start_memory_index()
        await self.drain()
        self.assertEqual(len(self.archive.memory_vectors), 1)
        self.provider.get_embedding.assert_awaited_once()

    async def test_recall_deduplicates_source_revisions_and_uses_latest_evidence(self):
        for identifier, content in [(1, "原先想去散步"), (2, "后来改为在家休息")]:
            self.archive.long_term_memories[identifier] = LongTermMemoryRecord(
                id=identifier, content=content, category="autobiography", scope="global",
                source_table="continuous_life_entries", source_id="same-action",
            )
        self.service.start_memory_index()
        await self.drain()
        found = await self.service.search_long_term_memories(
            "后来怎样", lexical=[self.archive.long_term_memories[1]], scopes=["friend:1"], categories=[], limit=4,
        )
        self.assertEqual([item.id for item in found], [2])

    async def test_close_cancels_running_memory_index(self):
        self.archive.long_term_memories[1] = LongTermMemoryRecord(id=1, content="待索引的旧事")
        entered = asyncio.Event()

        async def embed(values):
            entered.set()
            await asyncio.Event().wait()

        self.provider.get_embedding.side_effect = embed
        self.service.start_memory_index()
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(self.service.close(), 1)
        self.assertEqual(self.archive.memory_vectors, {})
        self.assertFalse(self.service.start_memory_index())

    async def test_failed_index_keeps_completed_records_and_resumes_missing_only(self):
        for identifier in range(1, 10):
            self.archive.long_term_memories[identifier] = LongTermMemoryRecord(id=identifier, content=f"经历 {identifier}")
        self.service._MEMORY_INDEX_MAX_PASSES = 1
        self.provider.get_embedding.side_effect = [[1.0, 0.0] for _ in range(8)] + [RuntimeError("接口失败")] * 2
        self.service.start_memory_index()
        await self.drain()
        self.assertEqual(len(self.archive.memory_vectors), 8)
        self.provider.get_embedding.side_effect = lambda text: [1.0, 0.0]
        self.service.start_memory_index()
        await self.drain()
        self.assertEqual(len(self.archive.memory_vectors), 9)
        self.assertIsInstance(self.provider.get_embedding.await_args.args[0], str)

    async def test_one_failed_memory_does_not_block_later_history(self):
        for identifier in range(1, 5):
            self.archive.long_term_memories[identifier] = LongTermMemoryRecord(id=identifier, content=f"经历 {identifier}")
        self.provider.get_embedding.side_effect = [
            RuntimeError("单条失败"), [1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [1.0, 0.0],
        ]
        self.service.start_memory_index()
        await self.drain()
        self.assertEqual(len(self.archive.memory_vectors), 4)
        self.assertEqual(self.provider.get_embedding.await_count, 5)

    async def test_provider_outage_stops_index_after_consecutive_failures(self):
        for identifier in range(1, 6):
            self.archive.long_term_memories[identifier] = LongTermMemoryRecord(id=identifier, content=f"经历 {identifier}")
        self.provider.get_embedding.side_effect = RuntimeError("接口不可用")
        self.service.start_memory_index()
        await self.drain()
        self.assertEqual(self.archive.memory_vectors, {})
        self.assertEqual(self.provider.get_embedding.await_count, 9)

    async def test_index_resumes_after_consecutive_failures_without_new_chat(self):
        for identifier in range(1, 5):
            self.archive.long_term_memories[identifier] = LongTermMemoryRecord(id=identifier, content=f"经历 {identifier}")
        self.provider.get_embedding.side_effect = [RuntimeError("接口短暂失败")] * 3 + [[1.0, 0.0]] * 4
        self.service.start_memory_index()
        await self.drain()
        self.assertEqual(len(self.archive.memory_vectors), 4)
        self.assertEqual(self.provider.get_embedding.await_count, 7)

    async def test_index_continues_while_each_pass_makes_progress(self):
        self.service._MEMORY_INDEX_MAX_PASSES = 1
        for identifier in range(1, 4):
            self.archive.long_term_memories[identifier] = LongTermMemoryRecord(id=identifier, content=f"经历 {identifier}")
        self.provider.get_embedding.side_effect = [
            [1.0, 0.0], RuntimeError("短暂失败"), RuntimeError("短暂失败"),
            [1.0, 0.0], RuntimeError("短暂失败"), [1.0, 0.0],
        ]
        self.service.start_memory_index()
        await self.drain()
        self.assertEqual(len(self.archive.memory_vectors), 3)
        self.assertEqual(self.provider.get_embedding.await_count, 6)

    async def test_failed_head_records_do_not_starve_unattempted_history(self):
        for identifier in range(1, 7):
            self.archive.long_term_memories[identifier] = LongTermMemoryRecord(id=identifier, content=f"经历 {identifier}")
        failed_texts = set()
        calls = []

        async def embed(text):
            calls.append(text)
            if len(failed_texts) < 3:
                failed_texts.add(text)
            if text in failed_texts:
                raise RuntimeError("单条记录失败")
            return [1.0, 0.0]

        self.provider.get_embedding.side_effect = embed
        self.service.start_memory_index()
        await self.drain()
        self.assertEqual(len(set(calls[:6])), 6)
        self.assertEqual(len(self.archive.memory_vectors), 3)

    async def test_index_restarts_after_cooldown_without_new_chat(self):
        self.service._MEMORY_INDEX_MAX_PASSES = 1
        self.archive.long_term_memories[1] = LongTermMemoryRecord(id=1, content="旧日经历")
        self.provider.get_embedding.side_effect = RuntimeError("暂时不可用")
        loop = asyncio.get_running_loop()
        with patch.object(loop, "call_later", wraps=loop.call_later) as timers:
            self.service.start_memory_index()
            await self.drain()
        self.assertEqual(self.archive.memory_vectors, {})
        self.assertIsNotNone(self.service._index_retry_handle)
        self.provider.get_embedding.side_effect = None
        # 主动推进冷却回调，避免 Windows 的计时粒度让 10ms 定时器抢先触发。
        callback = next(call.args[1] for call in timers.call_args_list
                        if call.args[0] == self.service._MEMORY_INDEX_RETRY_SECONDS)
        self.service._index_retry_handle.cancel()
        callback()
        await self.drain()
        self.assertEqual(len(self.archive.memory_vectors), 1)
        self.assertIsNone(self.service._index_retry_handle)

    async def test_close_cancels_pending_index_resume(self):
        self.service._MEMORY_INDEX_MAX_PASSES = 1
        self.archive.long_term_memories[1] = LongTermMemoryRecord(id=1, content="旧日经历")
        self.provider.get_embedding.side_effect = RuntimeError("暂时不可用")
        self.service.start_memory_index()
        await self.drain()
        calls_before = self.provider.get_embedding.await_count
        self.assertIsNotNone(self.service._index_retry_handle)
        pending = self.service._index_retry_handle
        await self.service.close()
        self.assertTrue(pending.cancelled())
        self.assertIsNone(self.service._index_retry_handle)
        self.assertEqual(self.provider.get_embedding.await_count, calls_before)

    async def test_new_index_job_remembers_failed_records_and_cancels_cooldown(self):
        self.service._MEMORY_INDEX_MAX_PASSES = 1
        for identifier in range(1, 7):
            self.archive.long_term_memories[identifier] = LongTermMemoryRecord(id=identifier, content=f"经历 {identifier}")
        self.provider.get_embedding.side_effect = RuntimeError("短暂不可用")
        self.service.start_memory_index()
        await self.drain()
        self.assertIsNotNone(self.service._index_retry_handle)
        self.provider.get_embedding.side_effect = None
        self.service.start_memory_index()
        self.assertIsNone(self.service._index_retry_handle)
        await self.drain()
        calls = [call.args[0] for call in self.provider.get_embedding.await_args_list]
        self.assertEqual(len(set(calls[:6])), 6)
        self.assertEqual(len(self.archive.memory_vectors), 6)
        self.assertEqual(self.service._index_failure_counts, {})

    async def test_cached_vectors_reject_zero_nonfinite_and_mismatched_dimensions(self):
        cached = {
            "content_hash": self.service._meaning_hash("经历"),
            "provider_id": "embedding:model-a", "dimensions": 2,
        }
        for raw in ("[0,0]", "[1]", "[1,NaN]", "[1,Infinity]"):
            with self.subTest(vector=raw):
                self.assertEqual(self.service._cached_vector({**cached, "vector_json": raw}, "经历", "embedding:model-a"), [])
        self.assertEqual(self.service._cached_vector({**cached, "vector_json": "[1,0]"}, "经历", "embedding:model-a"), [1.0, 0.0])

    async def test_full_background_queue_keeps_index_resume_pending(self):
        self.service._MEMORY_INDEX_RETRY_SECONDS = 0.01
        self.service._schedule_background = lambda coro, **kwargs: (coro.close(), False)[1]
        self.service._schedule_memory_index_retry()
        first_handle = self.service._index_retry_handle
        await asyncio.sleep(0.025)
        self.assertIsNotNone(self.service._index_retry_handle)
        self.assertIsNot(self.service._index_retry_handle, first_handle)
        self.config.enabled = False
        await asyncio.sleep(0.025)
        self.assertIsNone(self.service._index_retry_handle)

    async def test_semantic_search_without_embedding_preserves_lexical_result(self):
        self.service._embedding_providers = lambda: []
        lexical = [LongTermMemoryRecord(id=1, content="已有记忆")]
        self.assertIsNone(await self.service.search_long_term_memories(
            "已有", lexical=lexical, scopes=[], categories=[], limit=2,
        ))

    async def test_cancelling_one_waiter_preserves_other_waiter(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def embed(query):
            started.set()
            await release.wait()
            return [1.0, 0.0]

        self.provider.get_embedding.side_effect = embed
        first = asyncio.create_task(self.query())
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(self.query())
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertEqual(await asyncio.wait_for(second, 1), [1.0, 0.0])
        self.provider.get_embedding.assert_awaited_once()

    async def test_separate_instances_do_not_share_query_cache(self):
        other = self.make_service()
        try:
            await self.query()
            await other._meaning_query_vector(self.provider, self.model, "电影")
            self.assertEqual(self.provider.get_embedding.await_count, 2)
        finally:
            await other.close()

    async def test_model_change_invalidates_query_and_candidate_vectors(self):
        groups = {"memory": [("1", "一起看的电影", "记录")]}
        await self.service.rank_embedding_groups("电影", groups, {"memory": 1})
        await self.drain()
        self.model = "model-b"
        await self.service.rank_embedding_groups("电影", groups, {"memory": 1})
        await self.drain()
        self.assertEqual(self.provider.get_embedding.await_count, 2)
        self.assertEqual(self.provider.get_embeddings.await_count, 2)
        row = (await self.archive.get_memory_vectors("memory", ["1"]))["1"]
        self.assertEqual(row["provider_id"], "embedding:model-b")

    async def test_embedding_model_attribute_is_used_when_meta_model_is_empty(self):
        self.provider.meta = lambda: types.SimpleNamespace(id="embedding", model="")
        self.provider.model = "qwen-a"
        self.assertEqual(self.service._meaning_provider_cache_id(self.provider), "embedding:qwen-a")
        self.provider.model = "qwen-b"
        self.assertEqual(self.service._meaning_provider_cache_id(self.provider), "embedding:qwen-b")

    async def test_same_query_different_candidate_batches_both_persist(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.archive.upsert_memory_vectors

        async def store(rows):
            entered.set()
            await release.wait()
            await original(rows)

        self.archive.upsert_memory_vectors = store
        try:
            for identifier in ("1", "2"):
                await self.service.rank_embedding_groups(
                    "电影", {"memory": [(identifier, identifier, identifier)]}, {"memory": 1},
                )
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(len(self.scheduler.keys), 2)
        finally:
            release.set()
            await self.drain()
        self.assertEqual(set(await self.archive.get_memory_vectors("memory", ["1", "2"])), {"1", "2"})

    async def test_close_cancels_embedding_and_rejects_new_work(self):
        entered = asyncio.Event()

        async def embed(query):
            entered.set()
            await asyncio.Event().wait()

        self.provider.get_embedding.side_effect = embed
        task = asyncio.create_task(self.service.rank_embedding_groups(
            "电影", {"memory": [("1", "记录", "记录")]}, {"memory": 1},
        ))
        await asyncio.wait_for(entered.wait(), 1)
        await self.service.close()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaisesRegex(RuntimeError, "已关闭"):
            await self.service.rank_embedding_groups("电影", {}, {})
        with self.assertRaisesRegex(RuntimeError, "已关闭"):
            await self.service.rank_semantic_groups("电影", {}, {})
        self.assertEqual(self.service._query_vector_cache._items, {})
        self.assertEqual(self.service._query_vector_flight._tasks, {})
        self.assertEqual(self.scheduler.tasks, set())

    async def test_failed_embedding_only_uses_model_when_enabled_and_always_closes_session(self):
        self.provider.get_embedding.side_effect = RuntimeError("模型不可用")
        groups = {"memory": [("1", "记录一", "第一项"), ("2", "记录二", "第二项")]}
        self.assertEqual(await self.service.rank_semantic_groups("电影", groups, {"memory": 1}), {"memory": ["第一项"]})
        self.call_model.assert_not_awaited()
        self.config.semantic_ranking_model_enabled = True
        self.call_model.return_value = json.dumps({"selected": {"memory": [1]}})
        self.assertEqual(await self.service.rank_semantic_groups("电影", groups, {"memory": 1}), {"memory": ["第二项"]})
        self.close_session.assert_awaited_once()
        self.call_model.side_effect = RuntimeError("排序失败")
        self.assertEqual(await self.service.rank_semantic_groups("电影", groups, {"memory": 1}), {"memory": ["第一项"]})
        self.assertEqual(self.close_session.await_count, 2)

    async def prepare_revisit(self):
        self.archive.long_term_memories[1] = LongTermMemoryRecord(
            id=1, scope="friend:1", title="共同出游", content="一起看过海边的日落",
        )
        self.archive.long_term_memories[2] = LongTermMemoryRecord(
            id=2, scope="friend:2", title="另一位朋友", content="另一会话的私人经历",
        )
        self.service.start_memory_index()
        await self.drain()
        self.provider.get_embedding.reset_mock()
        self.provider.get_embeddings.reset_mock()
        return types.SimpleNamespace(archive=self.archive, rank_semantic_groups=self.service.rank_semantic_groups)

    async def test_revisit_accepts_query_over_two_seconds_and_reuses_titled_index(self):
        runtime = await self.prepare_revisit()

        async def embed(query):
            await asyncio.sleep(2.1)
            return [1.0, 0.0]

        self.provider.get_embedding.side_effect = embed
        recalled = await contextual_relationship_memories(runtime, scope="friend:1", query="今天又看见落日")
        self.assertIn("一起看过海边的日落", recalled)
        self.assertNotIn("另一会话", recalled)
        self.provider.get_embedding.assert_awaited_once_with("今天又看见落日")
        self.provider.get_embeddings.assert_not_awaited()

    async def test_revisit_late_query_is_cached_without_replaying_timed_out_call(self):
        runtime = await self.prepare_revisit()
        self.service._SEMANTIC_RANK_TIMEOUT_SECONDS = 0.1
        release = asyncio.Event()

        async def embed(query):
            await release.wait()
            return [1.0, 0.0]

        self.provider.get_embedding.side_effect = embed
        first = await contextual_relationship_memories(runtime, scope="friend:1", query="落日")
        self.assertEqual(first, "")
        pending = list(self.service._query_vector_flight._tasks.values())
        self.assertEqual(len(pending), 1)
        release.set()
        await asyncio.wait_for(asyncio.gather(*pending), 1)
        self.assertEqual(first, "")
        self.service._SEMANTIC_RANK_TIMEOUT_SECONDS = MeaningService._SEMANTIC_RANK_TIMEOUT_SECONDS
        second = await contextual_relationship_memories(runtime, scope="friend:1", query="落日")
        self.assertIn("[memory:1]", second)
        self.provider.get_embedding.assert_awaited_once()
        self.provider.get_embeddings.assert_not_awaited()

    async def test_shared_query_has_finite_lifetime_and_can_retry_after_timeout(self):
        self.service._MEANING_QUERY_REQUEST_TIMEOUT_SECONDS = 0.03
        cancelled = asyncio.Event()

        async def embed(query):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.provider.get_embedding.side_effect = embed
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.query(), 1)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.service._query_vector_flight._tasks, {})
        self.assertEqual(self.service._query_vector_cache._items, {})
        self.provider.get_embedding.side_effect = None
        self.assertEqual(await self.query(), [1.0, 0.0])
        self.assertEqual(self.provider.get_embedding.await_count, 2)

    async def test_semantic_budget_includes_index_read(self):
        self.service._SEMANTIC_RANK_TIMEOUT_SECONDS = 0.03
        cancelled = asyncio.Event()

        async def read(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.archive.get_memory_vectors = read
        result = await asyncio.wait_for(self.service.rank_semantic_groups(
            "落日", {"long_term_memory": [("1", "经历", "候选")]},
            {"long_term_memory": 1}, optional_groups={"long_term_memory"}, cached_only=True,
        ), 1)
        self.assertEqual(result, {"long_term_memory": []})
        self.assertTrue(cancelled.is_set())
        self.provider.get_embedding.assert_not_awaited()

    async def test_model_budget_includes_provider_lookup(self):
        self.service._SEMANTIC_RANK_TIMEOUT_SECONDS = 0.03
        self.config.semantic_ranking_model_enabled = True
        self.service._embedding_providers = lambda: []
        async def lookup(*args):
            await asyncio.Event().wait()

        self.text_provider.side_effect = lookup
        result = await asyncio.wait_for(self.service.rank_semantic_groups(
            "落日", {"memory": [("1", "经历", "候选")]}, {"memory": 1}, optional_groups={"memory"},
        ), 1)
        self.assertEqual(result, {"memory": []})
        self.call_model.assert_not_awaited()

    async def test_model_timeout_and_session_cleanup_are_bounded(self):
        self.service._SEMANTIC_RANK_TIMEOUT_SECONDS = 0.03
        self.service._SEMANTIC_SESSION_CLOSE_TIMEOUT_SECONDS = 0.02
        self.config.semantic_ranking_model_enabled = True
        self.service._embedding_providers = lambda: []

        async def hang(*args, **kwargs):
            await asyncio.Event().wait()

        self.call_model.side_effect = hang
        self.close_session.side_effect = hang
        result = await asyncio.wait_for(self.service.rank_semantic_groups(
            "落日", {"memory": [("1", "经历", "候选")]}, {"memory": 1}, optional_groups={"memory"},
        ), 1)
        self.assertEqual(result, {"memory": []})
        self.close_session.assert_awaited_once()

    async def test_cancelling_semantic_model_does_not_turn_into_fallback(self):
        self.config.semantic_ranking_model_enabled = True
        self.service._embedding_providers = lambda: []
        entered = asyncio.Event()

        async def generate(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        self.call_model.side_effect = generate
        task = asyncio.create_task(self.service.rank_semantic_groups(
            "落日", {"memory": [("1", "经历", "候选")]}, {"memory": 1}, optional_groups={"memory"},
        ))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.close_session.assert_awaited_once()

    async def test_cold_revisit_defers_indexing_and_next_recall_uses_index(self):
        self.archive.long_term_memories[1] = LongTermMemoryRecord(
            id=1, scope="friend:1", title="共同经历", content="一起看日落",
        )
        runtime = types.SimpleNamespace(archive=self.archive, rank_semantic_groups=self.service.rank_semantic_groups)
        first = await contextual_relationship_memories(runtime, scope="friend:1", query="落日")
        self.assertEqual(first, "")
        await self.drain()
        recalled = await contextual_relationship_memories(runtime, scope="friend:1", query="落日")
        self.assertIn("一起看日落", recalled)
        self.provider.get_embeddings.assert_not_awaited()
        self.assertEqual(self.provider.get_embedding.await_count, 2)

    async def test_unrelated_revisit_memory_does_not_become_evidence(self):
        runtime = await self.prepare_revisit()
        self.provider.get_embedding.return_value = [0.0, 1.0]
        self.assertEqual(await contextual_relationship_memories(runtime, scope="friend:1", query="另一个主题"), "")
        self.call_model.assert_not_awaited()

    async def test_runtime_forwards_cached_only_to_semantic_service(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.__dict__["meaning"] = types.SimpleNamespace(rank_semantic_groups=AsyncMock(return_value={"memory": []}))
        await runtime.rank_semantic_groups("落日", {}, {}, optional_groups={"memory"}, cached_only=True)
        runtime.meaning.rank_semantic_groups.assert_awaited_once_with(
            "落日", {}, {}, optional_groups={"memory"}, cached_only=True,
        )


class ScopedServiceTest(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider())
        runtime.config = LifeSettings.from_dict({})
        return runtime

    async def test_addressing_log_dedup_is_scoped_and_eviction_is_local(self):
        first, second = self.runtime(), self.runtime()
        scopes = [f"test:GroupMessage:{index}" for index in (1, 2)]

        def decorate(runtime, scope):
            return runtime.decorate_group_addressing_chain(
                ["回复"], target_scope=scope, source_message_id="42", source="proactive",
            )

        with patch("core.runtime.addressing.logger.debug") as log:
            for scope in scopes:
                self.assertTrue(decorate(first, scope))
                self.assertTrue(decorate(first, scope))
            self.assertEqual(log.call_count, 2)
            self.assertTrue(decorate(second, scopes[0]))
            self.assertEqual(log.call_count, 3)
            first._evict_scope_services({scopes[0]})
            self.assertTrue(decorate(first, scopes[1]))
            self.assertTrue(decorate(first, scopes[0]))
            self.assertEqual(log.call_count, 4)
            first.addressing.clear()
            self.assertTrue(decorate(first, scopes[1]))
            self.assertEqual(log.call_count, 5)

    async def test_reaction_state_eviction_preserves_other_scope_same_message_id(self):
        bot = types.SimpleNamespace(set_msg_emoji_like=AsyncMock())
        service = ToolReactionService(
            message_id=lambda event: event.message_id,
            session_id=lambda event: event.unified_msg_origin,
            event_sources=lambda event: [event], was_recalled=lambda event: False,
            background_request=lambda event, name: None,
        )
        for scope in ("scope-a", "scope-b"):
            await service.note_tool_reaction_start(
                Event(bot=bot, message_id="42", unified_msg_origin=scope), "life_video_generate",
            )
        self.assertEqual(set(service._states), {"scope-a\n42", "scope-b\n42"})
        service.evict_scopes({"scope-a"})
        self.assertEqual(set(service._states), {"scope-b\n42"})
        self.assertEqual(set(service._sent), {"scope-b\n42"})
        service.clear()
        self.assertEqual(service._states, {})
        self.assertEqual(service._sent, {})


class StateRefreshServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.day = DayRecord(date="2026-10-12")
        self.now = datetime.datetime(2026, 10, 12, 15, 0)
        self.archive = types.SimpleNamespace(
            get_day=AsyncMock(return_value=None), save_day=AsyncMock(),
            mutate_day=AsyncMock(), save_decision_trace=AsyncMock(),
            save_physiological_rhythm_log=AsyncMock(), save_emotion_arc=AsyncMock(),
        )
        self.recalled = False
        self.replan = Mock(return_value=types.SimpleNamespace(status="applied", reason="状态变化"))
        self.sync_kernel = AsyncMock()
        self.sync_world = AsyncMock()
        self.notify = AsyncMock()
        self.service = StateRefreshService(
            config=lambda: types.SimpleNamespace(enabled=True, refresh_minutes=5, provider="state"),
            archive=lambda: self.archive, compute_sleep=lambda previous, day: (0, 0, day.state.energy),
            replan=self.replan, sync_world_facts=self.sync_world,
            text_provider=AsyncMock(), call_text_model=AsyncMock(), close_text_session=AsyncMock(),
            event_was_recalled=lambda event, **kwargs: self.recalled,
            sync_kernel=self.sync_kernel, notify_status=self.notify,
        )
        self.spec = _StateRefreshSpec(self.day.date, self.now, "chat", "天气变化", object(), True)

    async def test_cancelled_transaction_does_not_write_side_records_or_notify(self):
        self.day.meta["continuous_body"] = "{}"

        async def mutate(date, change):
            self.recalled = True
            self.assertFalse(change(self.day))
            return self.day

        self.archive.mutate_day.side_effect = mutate
        original = self.day.persistence_snapshot()
        await self.service._commit_state_refresh(self.day, {"energy": 10}, self.spec)
        self.assertEqual(self.day.persistence_snapshot(), original)
        self.archive.save_physiological_rhythm_log.assert_not_awaited()
        self.archive.save_emotion_arc.assert_not_awaited()
        self.sync_kernel.assert_not_awaited()
        self.sync_world.assert_not_awaited()
        self.notify.assert_not_awaited()

    async def test_replan_runs_once_against_latest_transaction_record(self):
        self.day.meta["continuous_body"] = "{}"
        latest = DayRecord(date=self.day.date, outfit="刚换上的睡衣", meta={"continuous_body": "{}"})

        async def mutate(date, change):
            change(latest)
            return latest

        self.archive.mutate_day.side_effect = mutate
        await self.service._commit_state_refresh(self.day, {
            "state": {"energy": 20},
            "schedule_replan": {"should_replan": True, "replacements": [], "reason": "天气变化"},
        }, self.spec)
        self.replan.assert_called_once()
        self.assertIs(self.replan.call_args.args[0], latest)
        self.assertEqual(self.day.outfit, "刚换上的睡衣")
        self.archive.save_decision_trace.assert_awaited_once()
        self.sync_kernel.assert_awaited_once()
        self.notify.assert_awaited_once_with("state")

    async def test_kernel_sync_error_is_visible_to_caller(self):
        self.sync_kernel.side_effect = RuntimeError("内核提交失败")
        with self.assertRaisesRegex(RuntimeError, "内核提交失败"):
            await self.service._commit_state_refresh(self.day, {"energy": 10}, self.spec)
        self.archive.save_day.assert_awaited_once()
        self.notify.assert_not_awaited()
