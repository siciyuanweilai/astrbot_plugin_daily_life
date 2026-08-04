import asyncio
import types
import unittest

from core.runtime.locks import operation_lock
from core.runtime.scopes import RuntimeScopeState
from support import DailyLifeRuntime


class RuntimeScopeStateTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _runtime():
        runtime = types.SimpleNamespace()
        runtime._event_session_id = lambda event: event.scope
        runtime._event_group_meta = lambda event: (event.group_id, "")
        runtime._safe_event_call = lambda event, name: (
            event.sender_id if name == "get_sender_id" else ""
        )
        runtime._response_gate_last_seen_at = {}
        runtime._structured_messages = {}
        runtime._semantic_segment_revisions = {}
        runtime._chat_pacing_state = {}
        runtime._proactive_idle_candidates = {}
        runtime._proactive_idle_tasks = {}
        runtime._injection_snapshot_cache = {}
        return runtime

    @staticmethod
    def _event(scope: str, sender_id: str, group_id: str = ""):
        return types.SimpleNamespace(
            scope=scope,
            sender_id=sender_id,
            group_id=group_id,
        )

    async def test_scope_limit_evicts_all_aliases_and_idle_task(self):
        runtime = self._runtime()
        state = RuntimeScopeState(runtime, max_scopes=1)
        first = self._event("platform:GroupMessage:one", "user-one", "group-one")
        second = self._event("platform:GroupMessage:two", "user-two", "group-two")

        state.note_event(first)
        runtime._response_gate_last_seen_at["group-one"] = object()
        runtime._structured_messages[first.scope] = ["message"]
        runtime._semantic_segment_revisions[first.scope] = 1
        runtime._chat_pacing_state[first.scope] = {"effect": 0.5}
        runtime._proactive_idle_candidates["group-one"] = {"state": "pending"}
        idle_task = asyncio.create_task(asyncio.sleep(60))
        runtime._proactive_idle_tasks["group-one"] = idle_task

        state.note_event(second)
        await asyncio.sleep(0)

        self.assertNotIn("group-one", runtime._response_gate_last_seen_at)
        self.assertNotIn(first.scope, runtime._structured_messages)
        self.assertNotIn(first.scope, runtime._semantic_segment_revisions)
        self.assertNotIn(first.scope, runtime._chat_pacing_state)
        self.assertNotIn("group-one", runtime._proactive_idle_candidates)
        self.assertNotIn("group-one", runtime._proactive_idle_tasks)
        self.assertTrue(idle_task.cancelled())
        self.assertEqual(state.snapshot()["scopes"], 1)

    async def test_shared_platform_alias_is_not_removed_early(self):
        runtime = self._runtime()
        state = RuntimeScopeState(runtime, max_scopes=2)
        first = self._event("platform-a:GroupMessage:1", "user-a", "1")
        second = self._event("platform-b:GroupMessage:1", "user-b", "1")
        state.note_event(first)
        state.note_event(second)
        runtime._response_gate_last_seen_at["1"] = object()

        state.max_scopes = 1
        state.prune(force=True, protected={second.scope})

        self.assertIn("1", runtime._response_gate_last_seen_at)

    async def test_expired_snapshot_entries_are_pruned(self):
        runtime = self._runtime()
        runtime._injection_snapshot_cache = {
            "3:old": {"ts": 1.0, "data": {}},
        }
        state = RuntimeScopeState(runtime)

        state.prune(force=True)

        self.assertEqual(runtime._injection_snapshot_cache, {})


class MessageDedupStateTest(unittest.TestCase):
    def test_observed_history_keys_are_bounded(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._init_response_gate_state()
        runtime._MESSAGE_DEDUP_LIMIT = 2

        self.assertTrue(runtime._remember_observed_history_key("one"))
        self.assertTrue(runtime._remember_observed_history_key("two"))
        self.assertTrue(runtime._remember_observed_history_key("three"))

        self.assertEqual(runtime._observed_user_history_keys, {"two", "three"})
        self.assertTrue(runtime._remember_observed_history_key("one"))

    def test_recalled_skip_log_is_bounded(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._init_response_gate_state()
        runtime._MESSAGE_DEDUP_LIMIT = 2

        runtime._remember_recalled_skip_log("one")
        runtime._remember_recalled_skip_log("two")
        runtime._remember_recalled_skip_log("three")

        self.assertEqual(runtime._recalled_skip_logged, {"two", "three"})


class OperationLockTest(unittest.IsolatedAsyncioTestCase):
    async def test_lock_serializes_callers_and_releases_pool_entry(self):
        owner = types.SimpleNamespace(_operation_locks={})
        entered = asyncio.Event()
        release = asyncio.Event()
        order = []

        async def first():
            async with operation_lock(owner, "same"):
                order.append("first")
                entered.set()
                await release.wait()

        async def second():
            await entered.wait()
            async with operation_lock(owner, "same"):
                order.append("second")

        first_task = asyncio.create_task(first())
        second_task = asyncio.create_task(second())
        await entered.wait()
        await asyncio.sleep(0)
        self.assertEqual(order, ["first"])
        release.set()
        await asyncio.gather(first_task, second_task)

        self.assertEqual(order, ["first", "second"])
        self.assertEqual(owner._operation_locks, {})

    async def test_cancelled_waiter_releases_reference(self):
        owner = types.SimpleNamespace(_operation_locks={})
        release = asyncio.Event()

        async def holder():
            async with operation_lock(owner, "same"):
                await release.wait()

        async def waiter():
            async with operation_lock(owner, "same"):
                pass

        holder_task = asyncio.create_task(holder())
        await asyncio.sleep(0)
        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        waiter_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter_task
        release.set()
        await holder_task

        self.assertEqual(owner._operation_locks, {})
