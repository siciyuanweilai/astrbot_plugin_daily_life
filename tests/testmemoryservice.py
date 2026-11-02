"""长期记忆服务的归档契约与事件范围回归。"""

import types
import unittest
from unittest.mock import AsyncMock

from support import DailyLifeRuntime, Event, LongTermMemoryRecord
from core.runtime.remember import LifeMemoryService


class LifeMemoryServiceTest(unittest.IsolatedAsyncioTestCase):
    def make_runtime(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = types.SimpleNamespace(
            search_long_term_memories=AsyncMock(return_value=[]),
            list_recent_long_term_memories=AsyncMock(return_value=[]),
        )
        return runtime

    async def test_search_uses_group_and_session_scope_and_bounds_results(self):
        runtime = self.make_runtime()
        event = Event(group_id="42", unified_msg_origin="platform:GroupMessage:42")
        hits = [LongTermMemoryRecord(content="一起看过电影")]
        runtime.archive.search_long_term_memories.return_value = hits
        self.assertIs(await runtime.life_memory.search(
            "  电影  ", event=event, category="生活，约定", limit=100,
        ), hits)
        runtime.archive.search_long_term_memories.assert_awaited_once_with(
            "电影", scopes=["42", "platform:GroupMessage:42"],
            categories=["生活", "约定"], limit=12,
        )

    async def test_empty_query_requires_explicit_recent_mode(self):
        runtime = self.make_runtime()
        self.assertEqual(await runtime.life_memory.search(""), [])
        runtime.archive.search_long_term_memories.assert_not_awaited()
        runtime.archive.list_recent_long_term_memories.assert_not_awaited()
        event = Event(unified_msg_origin="platform:FriendMessage:42")
        await runtime.life_memory.search("", event=event, mode="recent")
        runtime.archive.list_recent_long_term_memories.assert_awaited_once_with(
            scopes=[event.unified_msg_origin], categories=[], limit=5,
        )

    async def test_semantic_recall_used_when_lexical_search_misses(self):
        runtime = self.make_runtime()
        memory = LongTermMemoryRecord(id=7, content="我不吃香菜")
        semantic = AsyncMock(return_value=[memory])
        service = LifeMemoryService(
            archive=lambda: runtime.archive,
            group_meta=runtime._event_group_meta,
            session_id=runtime._event_session_id,
            message_text=runtime._event_message_text,
            semantic_search=semantic,
        )
        self.assertEqual(await service.search("我排斥芫荽"), [memory])
        semantic.assert_awaited_once_with(
            "我排斥芫荽", lexical=[], scopes=[], categories=[], limit=5,
        )

    async def test_cached_service_reads_latest_archive_and_keeps_instances_separate(self):
        runtime = self.make_runtime()
        first = runtime.archive
        service = runtime.life_memory
        runtime.archive = types.SimpleNamespace(
            search_long_term_memories=AsyncMock(return_value=[LongTermMemoryRecord(content="新记忆")]),
        )
        other = self.make_runtime()
        self.assertIn("新记忆", await runtime.life_memory_search(Event(), "电影"))
        self.assertEqual(await other.life_memory.search("电影"), [])
        self.assertIs(runtime.life_memory, service)
        first.search_long_term_memories.assert_not_awaited()
        self.assertEqual(runtime.archive.search_long_term_memories.await_count, 1)

    async def test_context_combines_turn_text_and_retains_source_attribution(self):
        runtime = self.make_runtime()
        event = Event(unified_msg_origin="platform:FriendMessage:42")
        event.message_str = "电影"
        runtime.archive.search_long_term_memories.return_value = [LongTermMemoryRecord(
            id=3, title="约定", content="周末一起看电影", source_table="commitments",
            source_id="7", date="2026-10-12",
        )]
        text = await runtime.build_heuristic_memory_context(event, "周末", 4)
        runtime.archive.search_long_term_memories.assert_awaited_once_with(
            "周末 电影", scopes=[event.unified_msg_origin], categories=[], limit=4,
        )
        self.assertIn("[HiddenLongTermMemory]", text)
        self.assertIn("记忆编号 3", text)
        self.assertIn("commitments / 7", text)
        runtime.archive.search_long_term_memories.return_value = []
        self.assertEqual(await runtime.build_heuristic_memory_context(event), "")

    async def test_short_followup_uses_recent_conversation_only_for_semantic_query(self):
        runtime = self.make_runtime()
        recent = [{"role": "user", "content": "上次一起挑的那条裙子"}, {"role": "assistant", "content": "那条蓝色的？"}]
        semantic = AsyncMock(return_value=[LongTermMemoryRecord(id=7, content="一起选过蓝色裙子")])
        scopes = []

        async def history(scope):
            scopes.append(scope)
            return recent

        service = LifeMemoryService(
            archive=lambda: runtime.archive, group_meta=runtime._event_group_meta,
            session_id=runtime._event_session_id, message_text=runtime._event_message_text,
            semantic_search=semantic, recent_messages=history,
        )
        event = Event(unified_msg_origin="platform:FriendMessage:42")
        event.message_str = "对 就那个"
        text = await service.build_context(event)
        self.assertIn("一起选过蓝色裙子", text)
        self.assertEqual(scopes, [event.unified_msg_origin])
        self.assertIn("上次一起挑的那条裙子", semantic.await_args.args[0])
        runtime.archive.search_long_term_memories.assert_awaited_once_with(
            "对 就那个", scopes=[event.unified_msg_origin], categories=[], limit=4,
        )

    async def test_disabled_or_zero_injection_does_not_access_memory(self):
        runtime = self.make_runtime()
        runtime.config = types.SimpleNamespace(memory=types.SimpleNamespace(enabled=True, max_injection_items=0))
        event = Event()
        event.message_str = "电影"
        self.assertEqual(await runtime.build_heuristic_memory_context(event), "")
        runtime.config.memory.enabled = False
        runtime.config.memory.max_injection_items = 5
        self.assertEqual(await runtime.build_heuristic_memory_context(event), "")
        runtime.archive.search_long_term_memories.assert_not_awaited()

    async def test_slow_recent_history_does_not_hold_recall(self):
        import asyncio

        runtime = self.make_runtime()
        cancelled = asyncio.Event()

        async def history(scope):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        semantic = AsyncMock(return_value=[])
        service = LifeMemoryService(
            archive=lambda: runtime.archive, group_meta=runtime._event_group_meta,
            session_id=runtime._event_session_id, message_text=runtime._event_message_text,
            semantic_search=semantic, recent_messages=history,
        )
        event = Event()
        event.message_str = "电影"
        self.assertEqual(await asyncio.wait_for(service.build_context(event), 0.4), "")
        self.assertTrue(cancelled.is_set())
        self.assertEqual(semantic.await_args.args[0], "电影")
