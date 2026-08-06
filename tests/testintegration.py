import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from support import Event  # noqa: F401

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from astrbot_plugin_daily_life.core.archive import LifeArchive  # noqa: E402
from astrbot_plugin_daily_life.core.models import ChatSummaryRecord  # noqa: E402
from astrbot_plugin_daily_life.core.runtime.mirror.export import (  # noqa: E402
    SnapshotExportMixin,
)
from astrbot_plugin_daily_life.main import DailyLifePlugin  # noqa: E402


def _person(name, profile_id):
    return types.SimpleNamespace(
        id=profile_id,
        name=name,
        alias="",
        subjective_name="",
        user_id=profile_id,
    )


class _TargetArchive:
    def __init__(self):
        self.relationships = {
            "bot-test:FriendMessage:user-test-a": [_person("联系人甲", "user-test-a")],
            "bot-test:FriendMessage:user-test-b": [_person("联系人乙", "user-test-b")],
        }

    async def get_relationships_for_target(self, scope, limit=1):
        return list(self.relationships.get(scope, []))[:limit]

    async def get_relationship(self, profile_id):
        return None

    async def get_chat_summaries_for_session(self, scope, limit=5):
        return [types.SimpleNamespace(session_id=scope, brief=f"摘要:{scope}")]

    async def get_recent_places(self, limit):
        return []

    async def get_recent_events(self, limit):
        return [
            types.SimpleNamespace(summary="公共事件", people=[]),
            types.SimpleNamespace(summary="甲的事件", people=["联系人甲"]),
            types.SimpleNamespace(summary="乙的事件", people=["联系人乙"]),
        ]

    async def get_commitments(self, status="active", limit=20):
        return [
            types.SimpleNamespace(
                content="甲的约定",
                people=["联系人甲"],
                source_session="bot-test:FriendMessage:user-test-a",
            ),
            types.SimpleNamespace(
                content="乙的约定",
                people=["联系人乙"],
                source_session="bot-test:FriendMessage:user-test-b",
            ),
        ]


class _Snapshot(SnapshotExportMixin):
    def __init__(self):
        self.archive = _TargetArchive()


class TargetLifeContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_archive_queries_relationship_and_summary_by_exact_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = LifeArchive(Path(temp_dir) / "daily_life.db")
            try:
                target_a = "bot-test:FriendMessage:user-test-a"
                target_b = "bot-test:FriendMessage:user-test-b"
                await archive.touch_relationship(
                    "user-test-a",
                    name="联系人甲",
                    contact_type="friend",
                    target_scope=target_a,
                )
                await archive.touch_relationship(
                    "user-test-b",
                    name="联系人乙",
                    contact_type="friend",
                    target_scope=target_b,
                )
                await archive.save_chat_summary(
                    ChatSummaryRecord(session_id=target_a, brief="甲的摘要")
                )
                await archive.save_chat_summary(
                    ChatSummaryRecord(session_id=target_b, brief="乙的摘要")
                )

                relationships = await archive.get_relationships_for_target(target_a)
                summaries = await archive.get_chat_summaries_for_session(target_a)

                self.assertEqual([item.name for item in relationships], ["联系人甲"])
                self.assertEqual([item.brief for item in summaries], ["甲的摘要"])
            finally:
                archive.close()

    async def test_target_snapshot_does_not_mix_private_relationships(self):
        snapshot = _Snapshot()

        target_a = await snapshot._life_context_target_archive_snapshot(
            "bot-test:FriendMessage:user-test-a"
        )
        target_b = await snapshot._life_context_target_archive_snapshot(
            "bot-test:FriendMessage:user-test-b"
        )

        self.assertEqual(
            [item.name for item in target_a["relationships"]], ["联系人甲"]
        )
        self.assertEqual(
            [item.name for item in target_b["relationships"]], ["联系人乙"]
        )
        self.assertEqual(
            [item.summary for item in target_a["events"]],
            ["公共事件", "甲的事件"],
        )
        self.assertEqual(
            [item.content for item in target_a["commitments"]],
            ["甲的约定"],
        )

    async def test_group_snapshot_contains_no_private_relationship(self):
        snapshot = _Snapshot()

        result = await snapshot._life_context_target_archive_snapshot(
            "bot-test:GroupMessage:group-test-a"
        )

        self.assertEqual(result["relationships"], [])
        self.assertEqual([item.summary for item in result["events"]], ["公共事件"])


class ExternalLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_share_search_contract_delegates_to_runtime_search(self):
        plugin = DailyLifePlugin(types.SimpleNamespace(), {})
        calls = []

        class Search:
            async def search_external_evidence(self, query, **kwargs):
                calls.append((query, kwargs))
                return {
                    "status": "ok",
                    "query": query,
                    "content": "可验证的搜索证据",
                    "sources": [],
                    "category": kwargs["category"],
                }

        plugin.runtime = types.SimpleNamespace(search=Search())
        plugin.commands = object()

        result = await plugin.search_share_evidence(
            "测试主题",
            category="news",
            target_umo="bot-test:GroupMessage:group-test-a",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["category"], "news")
        self.assertEqual(
            calls,
            [
                (
                    "测试主题",
                    {
                        "category": "news",
                        "umo": "bot-test:GroupMessage:group-test-a",
                    },
                )
            ],
        )

    async def test_terminate_waits_for_external_public_call(self):
        plugin = DailyLifePlugin(types.SimpleNamespace(), {})
        entered = asyncio.Event()
        release = asyncio.Event()
        terminated = asyncio.Event()

        class Runtime:
            async def get_life_context(self, target_umo=""):
                entered.set()
                await release.wait()
                return {"target": target_umo}

            async def terminate(self):
                terminated.set()

        plugin.runtime = Runtime()
        plugin.commands = object()
        context_task = asyncio.create_task(
            plugin.get_life_context("bot-test:FriendMessage:user-test-a")
        )
        await entered.wait()
        terminate_task = asyncio.create_task(plugin.terminate())
        await asyncio.sleep(0)

        self.assertFalse(terminated.is_set())
        release.set()
        self.assertEqual(
            await context_task,
            {"target": "bot-test:FriendMessage:user-test-a"},
        )
        await terminate_task
        self.assertTrue(terminated.is_set())
        self.assertIsNone(plugin.runtime)

    async def test_terminate_cancels_external_call_after_grace_period(self):
        plugin = DailyLifePlugin(types.SimpleNamespace(), {})
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        terminated = asyncio.Event()

        class Runtime:
            async def get_life_context(self, target_umo=""):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            async def terminate(self):
                terminated.set()

        plugin.runtime = Runtime()
        plugin.commands = object()
        context_task = asyncio.create_task(
            plugin.get_life_context("bot-test:FriendMessage:user-test-a")
        )
        await entered.wait()

        with patch(
            "astrbot_plugin_daily_life.main.EXTERNAL_LEASE_SHUTDOWN_TIMEOUT_SECONDS",
            0.01,
        ):
            await plugin.terminate()

        self.assertTrue(cancelled.is_set())
        self.assertTrue(terminated.is_set())
        self.assertIsNone(plugin.runtime)
        with self.assertRaises(asyncio.CancelledError):
            await context_task

    async def test_terminate_cancels_task_still_holding_reentrant_external_lease(self):
        plugin = DailyLifePlugin(types.SimpleNamespace(), {})
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        terminated = asyncio.Event()

        class Runtime:
            async def terminate(self):
                terminated.set()

        plugin.runtime = Runtime()
        plugin.commands = object()

        async def hold_outer_lease_after_inner_exits():
            try:
                async with plugin._external_runtime_lease():
                    async with plugin._external_runtime_lease():
                        entered.set()
                    await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        call_task = asyncio.create_task(hold_outer_lease_after_inner_exits())
        await entered.wait()
        try:
            with patch(
                "astrbot_plugin_daily_life.main.EXTERNAL_LEASE_SHUTDOWN_TIMEOUT_SECONDS",
                0.01,
            ):
                await plugin.terminate()

            self.assertTrue(cancelled.is_set())
            self.assertTrue(terminated.is_set())
            self.assertIsNone(plugin.runtime)
            with self.assertRaises(asyncio.CancelledError):
                await call_task
        finally:
            if not call_task.done():
                call_task.cancel()
                await asyncio.gather(call_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
