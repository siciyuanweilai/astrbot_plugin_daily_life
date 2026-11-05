"""Remote stalls must not discard reusable work or mix current memory records."""
import asyncio
import types
import unittest
from unittest.mock import AsyncMock

from support import DataManager
from core.runtime.background import BackgroundTaskScheduler
from core.runtime.layer.chain import LayerChainComponent
from core.runtime.meaning import MeaningService
from core.runtime.vectorcache import CandidateVectorCache


class CandidateVectorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cache = CandidateVectorCache()
        self.provider = types.SimpleNamespace(get_embeddings=AsyncMock(
            side_effect=lambda texts: [[float(int(text)), 1.0] for text in texts],
        ))

    async def asyncTearDown(self):
        await self.cache.close()

    async def test_overlapping_batches_share_each_text_and_keep_order(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def embed(texts):
            started.set()
            await release.wait()
            return [[float(int(t)), 1.0] for t in texts]
        self.provider.get_embeddings.side_effect = embed
        first = asyncio.create_task(self.cache.get_many(self.provider, 'model', ['1', '2']))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(self.cache.get_many(self.provider, 'model', ['2', '3', '2']))
        await asyncio.sleep(0)
        release.set()
        a, b = await asyncio.gather(first, second)
        self.assertEqual(a, [[1, 1], [2, 1]])
        self.assertEqual(b, [[2, 1], [3, 1], [2, 1]])
        sent = [text for call in self.provider.get_embeddings.await_args_list for text in call.args[0]]
        self.assertCountEqual(sent, ['1', '2', '3'])
        a[0][0] = 99
        self.assertEqual(await self.cache.get_many(self.provider, 'model', ['1']), [[1, 1]])

    async def test_late_vectors_survive_cancel_and_next_turn_reuses_them(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def embed(texts):
            started.set()
            await release.wait()
            return [[1.0, 0.0] for _ in texts]
        self.provider.get_embeddings.side_effect = embed
        waiter = asyncio.create_task(self.cache.get_many(self.provider, 'model', ['1']))
        await asyncio.wait_for(started.wait(), 1)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        pending = list(self.cache._tasks)
        self.assertEqual(len(pending), 1)
        release.set()
        await asyncio.wait_for(asyncio.gather(*pending), 1)
        self.assertEqual(await self.cache.get_many(self.provider, 'model', ['1']), [[1, 0]])
        self.provider.get_embeddings.assert_awaited_once()

    async def test_batch_size_and_concurrency_are_bounded(self):
        self.cache.batch_size = 3
        active = peak = 0
        async def embed(texts):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return [[float(int(t)), 1.0] for t in texts]
        self.provider.get_embeddings.side_effect = embed
        result = await self.cache.get_many(self.provider, 'model', [str(n) for n in range(10)])
        self.assertEqual(result, [[float(n), 1] for n in range(10)])
        self.assertLessEqual(peak, 2)
        self.assertTrue(all(len(call.args[0]) <= 3 for call in self.provider.get_embeddings.await_args_list))

    async def test_invalid_batch_is_not_cached_and_can_retry(self):
        for raw in ([[1, 0]], [[1, 0], [float('nan'), 0]], [[1, 0], [1, 0, 0]]):
            with self.subTest(raw=raw):
                self.provider.get_embeddings.side_effect = None
                self.provider.get_embeddings.return_value = raw
                with self.assertRaises(ValueError):
                    await self.cache.get_many(self.provider, 'model', ['1', '2'])
                self.assertEqual(self.cache.cache._items, {})
        self.provider.get_embeddings.return_value = [[1, 0], [0, 1]]
        self.assertEqual(await self.cache.get_many(self.provider, 'model', ['1', '2']), [[1, 0], [0, 1]])

    async def test_content_and_provider_changes_do_not_reuse_old_vectors(self):
        await self.cache.get_many(self.provider, 'model-a', ['1'])
        await self.cache.get_many(self.provider, 'model-b', ['1'])
        await self.cache.get_many(self.provider, 'model-b', ['2'])
        self.assertEqual(self.provider.get_embeddings.await_count, 3)

    async def test_hanging_request_expires_and_close_cancels_producers(self):
        cancelled = asyncio.Event()
        async def hang(texts):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.provider.get_embeddings.side_effect = hang
        self.cache.timeout_seconds = 0.03
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.cache.get_many(self.provider, 'model', ['1']), 1)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(self.cache.busy)
        self.cache.timeout_seconds = 10
        task = asyncio.create_task(self.cache.get_many(self.provider, 'model', ['2']))
        await asyncio.sleep(0)
        await self.cache.close()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.cache.busy)
        with self.assertRaises(RuntimeError):
            await self.cache.get_many(self.provider, 'model', ['3'])

    async def test_queue_overflow_does_not_start_partial_batch_work(self):
        self.cache.batch_size = 1
        self.cache.max_pending_batches = 1
        with self.assertRaises(RuntimeError):
            await self.cache.get_many(self.provider, 'model', ['1', '2'])
        self.provider.get_embeddings.assert_not_awaited()
        self.assertFalse(self.cache.busy)


class SemanticResponsivenessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.archive = DataManager()
        self.provider = types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(id='embedding', model='test'),
            get_embedding=AsyncMock(return_value=[1.0, 0.0]),
            get_embeddings=AsyncMock(side_effect=lambda texts: [[1.0, 0.0] for _ in texts]),
        )
        self.scheduler = BackgroundTaskScheduler()
        self.service = MeaningService(
            embedding_providers=lambda: [self.provider],
            memory_config=lambda: types.SimpleNamespace(embedding_provider='', semantic_ranking_model_enabled=False),
            archive=lambda: self.archive, text_provider=AsyncMock(), call_text_model=AsyncMock(),
            close_text_session=AsyncMock(), schedule_background=self.scheduler.schedule,
        )

    async def asyncTearDown(self):
        await self.service.close()
        await self.scheduler.cancel_all()

    async def test_query_and_candidates_start_without_waiting_for_each_other(self):
        query_started, candidates_started = asyncio.Event(), asyncio.Event()
        async def query(text):
            query_started.set()
            await candidates_started.wait()
            return [1.0, 0.0]
        async def batch(texts):
            candidates_started.set()
            await query_started.wait()
            return [[1.0, 0.0] for _ in texts]
        self.provider.get_embedding.side_effect = query
        self.provider.get_embeddings.side_effect = batch
        result = await asyncio.wait_for(self.service.rank_embedding_groups(
            '当前交流', {'memory': [('1', '已发生的共同经历', '经历')]}, {'memory': 1},
        ), 1)
        self.assertEqual(result, {'memory': ['经历']})

    async def test_timeout_only_warms_vectors_and_next_turn_uses_current_records(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def batch(texts):
            started.set()
            await release.wait()
            return [[1.0, 0.0] for _ in texts]
        self.provider.get_embeddings.side_effect = batch
        first = asyncio.create_task(self.service.rank_semantic_groups(
            '当前交流', {'memory': [('old-id', '共同经历', '旧对象')]}, {'memory': 1},
        ))
        await asyncio.wait_for(started.wait(), 1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        await asyncio.gather(*list(self.service._candidate_vectors._tasks))
        self.assertEqual(self.archive.memory_vectors, {})
        result = await self.service.rank_semantic_groups(
            '当前交流', {'memory': [('new-id', '共同经历', '本轮已核对对象')]}, {'memory': 1},
        )
        self.assertEqual(result, {'memory': ['本轮已核对对象']})
        self.provider.get_embeddings.assert_awaited_once()
        self.provider.get_embedding.assert_awaited_once()

    async def test_dimension_mismatch_cannot_be_persisted_or_used_as_semantic_evidence(self):
        self.provider.get_embeddings.side_effect = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
        result = await self.service.rank_semantic_groups(
            '当前交流', {'memory': [('1', '经历', '候选')]}, {'memory': 1}, optional_groups={'memory'},
        )
        self.assertEqual(result, {'memory': []})
        self.assertEqual(self.archive.memory_vectors, {})

    async def test_history_yields_while_current_query_is_inflight(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def query(text):
            started.set()
            await release.wait()
            return [1.0, 0.0]
        self.provider.get_embedding.side_effect = query
        foreground = asyncio.create_task(self.service._meaning_query_vector(self.provider, 'test', '交流'))
        await asyncio.wait_for(started.wait(), 1)
        index_turn = asyncio.create_task(self.service._wait_memory_index_turn())
        await asyncio.sleep(0)
        self.assertFalse(index_turn.done())
        release.set()
        await asyncio.wait_for(asyncio.gather(foreground, index_turn), 1)


class ContextPreparationTest(unittest.IsolatedAsyncioTestCase):
    async def test_long_term_recall_and_snapshot_selection_progress_together(self):
        recall_started, ranking_started = asyncio.Event(), asyncio.Event()
        async def recall(*args):
            recall_started.set()
            await ranking_started.wait()
            return '长期回忆'
        async def rank(*args):
            ranking_started.set()
            await recall_started.wait()
            return '关系背景', '经历背景'
        context = types.SimpleNamespace(
            _event_message_text=lambda event: '自然交流',
            _gather_life_context_snapshot=AsyncMock(return_value={}),
            format_recent_sight_context=AsyncMock(return_value=''),
            build_heuristic_memory_context=recall,
            domains=types.SimpleNamespace(format_context=AsyncMock(return_value='')),
            _build_person_fact_injection_context=AsyncMock(return_value=''),
            _format_cognition_context=lambda snapshot: '',
            resolve_interaction_context=AsyncMock(return_value=types.SimpleNamespace(format_for_generation=lambda: '')),
            _format_hidden_experience_context=lambda **kwargs: '',
            format_structured_message_context=lambda *args, **kwargs: '',
            build_hidden_life_context=lambda *args, **kwargs: '生活背景',
        )
        component = LayerChainComponent(context)
        component._build_injection_memos_context = AsyncMock(return_value='')
        component._select_life_memory_contexts = rank
        component._scope_snapshot_items = lambda *args: ([], [], [])
        result = await asyncio.wait_for(component._build_available_life_context(
            object(), None, False, types.SimpleNamespace(),
        ), 1)
        self.assertIn('长期回忆', result)
        self.assertIn('生活背景', result)
