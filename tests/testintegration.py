import asyncio
import json
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


class _ShareArchive:
    def __init__(self):
        self.calls = []

    async def get_life_episodes(self, limit=16):
        return [
            {
                "date": "2026-08-12",
                "title": "公共日程片段",
                "summary": "午后在窗边整理照片",
                "source": "daily",
                "related_people": [],
            },
            {
                "date": "2026-08-11",
                "title": "甲的私聊片段",
                "summary": "和联系人甲聊到周末看展",
                "source": "chat_memory",
                "related_people": ["联系人甲"],
            },
            {
                "date": "2026-08-10",
                "title": "乙的私聊片段",
                "summary": "和联系人乙约好吃饭",
                "source": "chat_memory",
                "related_people": ["联系人乙"],
            },
            {
                "date": "2026-08-09",
                "title": "无归属私聊片段",
                "summary": "不应作为公共生活片段",
                "source": "chat_memory",
                "related_people": [],
            },
        ]

    async def get_physiological_rhythm_trend(self, days=7, *, limit=8):
        return {"summary": "最近几天午后精力偏低"}

    async def get_focus_targets(self, limit=6, *, scope=""):
        self.calls.append(("focus_targets", scope))
        return [
            {"scope": scope, "label": "当前目标", "reason": "本轮仍在关注"},
            {"scope": "", "label": "未归属目标", "reason": "不得透传"},
        ]

    async def get_focus_slots(self, limit=6, *, scope=""):
        self.calls.append(("focus_slots", scope))
        return []

    async def get_expression_profiles(self, limit=4, *, scope="", profile_id=""):
        if profile_id:
            self.calls.append(("person_profiles", profile_id))
            return [
                {
                    "scope": "other-scope",
                    "profile_id": profile_id,
                    "label": "联系人甲",
                    "tone": "自然熟悉",
                    "habits": ["简短回应"],
                    "evidence": "原始表达证据不得透传",
                }
            ]
        self.calls.append(("expression_profiles", scope))
        return [
            {
                "scope": scope,
                "profile_id": "",
                "label": "当前会话",
                "tone": "轻松",
            },
            {"scope": "", "label": "未归属画像", "tone": "不得透传"},
        ]

    async def get_temporary_expression_states(self, limit=3, *, scope=""):
        self.calls.append(("temporary_states", scope))
        return []

    async def get_behavior_patterns(self, limit=4, *, scope=""):
        self.calls.append(("behavior_patterns", scope))
        return [
            {
                "scope": scope,
                "scene": "日常分享",
                "pattern": "先说结论",
                "evidence": "内部模式证据不得透传",
            },
            {"scope": "", "scene": "未归属模式", "pattern": "不得透传"},
        ]

    async def get_behavior_scenes(self, limit=4, *, scope=""):
        self.calls.append(("behavior_scenes", scope))
        return []

    async def get_reply_effects(self, limit=8, *, scope=""):
        self.calls.append(("reply_effects", scope))
        return [
            {
                "scope": scope,
                "outcome": "positive",
                "reply_text": "原始回复不得透传",
            }
        ]

    async def get_behavior_feedback(self, limit=8, *, target_id=""):
        self.calls.append(("behavior_feedback", target_id))
        return [{"target_id": target_id, "result": "neutral"}]

    async def get_life_terms(self, limit=4, *, scope=""):
        self.calls.append(("life_terms", scope))
        return [
            {"scope": scope, "term": "约饭", "meaning": "约好一起吃饭"},
            {"scope": "", "term": "未归属用语", "meaning": "不得透传"},
        ]


class _ShareSnapshot(SnapshotExportMixin):
    def __init__(self):
        self.archive = _ShareArchive()

    async def _settle_stale_reply_effects(self):
        return 0


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

    async def test_private_share_guidance_is_target_scoped_and_sanitized(self):
        snapshot = _ShareSnapshot()
        target = "bot-test:FriendMessage:user-test-a"

        result = await snapshot._share_guidance(
            target,
            [{"id": "user-test-a", "name": "联系人甲"}],
        )

        self.assertEqual(
            [item["title"] for item in result["episodes"]],
            ["公共日程片段", "甲的私聊片段"],
        )
        self.assertEqual(result["focus"][0]["label"], "当前目标")
        self.assertEqual(result["behavior"][0]["preferred"], "先说结论")
        self.assertEqual(result["interaction"]["positive"], 1)
        self.assertEqual(result["interaction"]["neutral"], 1)
        self.assertIn(("reply_effects", target), snapshot.archive.calls)
        self.assertIn(("behavior_feedback", target), snapshot.archive.calls)

        serialized = json.dumps(result, ensure_ascii=False)
        for forbidden in (
            "乙的私聊片段",
            "无归属私聊片段",
            "未归属",
            "原始回复",
            "原始表达证据",
            "内部模式证据",
        ):
            self.assertNotIn(forbidden, serialized)
        for internal_field in ("reply_text", "evidence", "id", "target_id"):
            self.assertNotIn(f'"{internal_field}"', serialized)

    async def test_group_share_guidance_uses_group_scope_without_private_episodes(self):
        snapshot = _ShareSnapshot()
        target = "bot-test:GroupMessage:group-test-a"

        result = await snapshot._share_guidance(target, [])

        self.assertEqual(
            [item["title"] for item in result["episodes"]], ["公共日程片段"]
        )
        self.assertIn(("focus_targets", "group-test-a"), snapshot.archive.calls)
        self.assertIn(("reply_effects", target), snapshot.archive.calls)
        self.assertIn(("behavior_feedback", "group-test-a"), snapshot.archive.calls)
        self.assertIn(("behavior_feedback", target), snapshot.archive.calls)
        self.assertNotIn(
            "person_profiles", [item[0] for item in snapshot.archive.calls]
        )

    async def test_public_share_guidance_contains_only_public_daily_episode(self):
        snapshot = _ShareSnapshot()

        result = await snapshot._share_guidance("qzone_broadcast", [])

        self.assertEqual(
            [item["title"] for item in result["episodes"]], ["公共日程片段"]
        )


class ExternalLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_share_context_contract_delegates_to_runtime(self):
        plugin = DailyLifePlugin(types.SimpleNamespace(), {})

        class Runtime:
            async def get_share_context(self, target_umo=""):
                return {"target": target_umo, "share_guidance": {"version": 1}}

        plugin.runtime = Runtime()
        plugin.commands = object()

        target = "bot-test:FriendMessage:user-test-a"
        self.assertEqual(
            await plugin.get_share_context(target),
            {"target": target, "share_guidance": {"version": 1}},
        )
        self.assertFalse(hasattr(plugin, "get_life_context"))

    async def test_share_image_contract_passes_separate_and_legacy_models(self):
        plugin = DailyLifePlugin(types.SimpleNamespace(), {})
        calls = []

        class Runtime:
            async def generate_life_image_asset(self, event, prompt, *args, **kwargs):
                calls.append((event, prompt, kwargs))
                return types.SimpleNamespace(path="generated.png")

        plugin.runtime = Runtime()
        plugin.commands = object()

        self.assertEqual(
            await plugin.generate_share_image(
                None,
                "分别指定模型",
                text_model="gpt-image-text",
                edit_model="gpt-image-edit",
                contains_character=True,
            ),
            "generated.png",
        )
        self.assertEqual(
            await plugin.generate_share_image(
                None,
                "兼容单一模型",
                model="gpt-image-legacy",
            ),
            "generated.png",
        )
        self.assertEqual(
            calls[0][2]["text_model"],
            "gpt-image-text",
        )
        self.assertEqual(calls[0][2]["edit_model"], "gpt-image-edit")
        self.assertEqual(calls[1][2]["text_model"], "gpt-image-legacy")
        self.assertEqual(calls[1][2]["edit_model"], "gpt-image-legacy")

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

    async def test_terminate_waits_for_external_share_context_call(self):
        plugin = DailyLifePlugin(types.SimpleNamespace(), {})
        entered = asyncio.Event()
        release = asyncio.Event()
        terminated = asyncio.Event()

        class Runtime:
            async def get_share_context(self, target_umo=""):
                entered.set()
                await release.wait()
                return {"target": target_umo}

            async def terminate(self):
                terminated.set()

        plugin.runtime = Runtime()
        plugin.commands = object()
        context_task = asyncio.create_task(
            plugin.get_share_context("bot-test:FriendMessage:user-test-a")
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

    async def test_terminate_cancels_share_context_call_after_grace_period(self):
        plugin = DailyLifePlugin(types.SimpleNamespace(), {})
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        terminated = asyncio.Event()

        class Runtime:
            async def get_share_context(self, target_umo=""):
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
            plugin.get_share_context("bot-test:FriendMessage:user-test-a")
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
