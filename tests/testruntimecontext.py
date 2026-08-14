import datetime
import unittest
from types import SimpleNamespace

from core.runtime.context import (
    ContextSnapshotRepository,
    InteractionContextMixin,
    interaction_fact_is_current,
)


class InteractionRuntime(InteractionContextMixin):
    def __init__(self, facts):
        self.writes = []
        self.archive = SimpleNamespace(
            get_temporal_facts=lambda **kwargs: self._facts(facts, kwargs),
            get_current_temporal_fact=lambda *args: self._current_fact(facts, args),
            write_temporal_fact=self._write_fact,
        )

    @staticmethod
    async def _facts(facts, kwargs):
        return list(facts)

    @staticmethod
    async def _current_fact(facts, args):
        return facts[0] if facts else None

    async def _write_fact(self, operation, payload):
        self.writes.append((operation, payload))
        return SimpleNamespace(**payload)

    @staticmethod
    def _event_session_id(event):
        return str(getattr(event, "unified_msg_origin", "") or "")

    @staticmethod
    def _event_profile_id(event):
        return str(getattr(event, "sender_id", "") or "")

    @staticmethod
    def _event_is_group_message(event):
        return ":GroupMessage:" in str(getattr(event, "unified_msg_origin", "") or "")


class ContextSnapshotRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_repository_delegates_to_archive_contract(self):
        class Archive:
            def __init__(self):
                self.calls = []

            async def get_context_snapshot(self, **kwargs):
                self.calls.append(kwargs)
                return {"relationships": []}

        archive = Archive()
        repository = ContextSnapshotRepository(archive)

        result = await repository.read(
            max_summaries=6,
            experience_scope="test-scope",
            session_id="test-session",
        )

        self.assertEqual(result, {"relationships": []})
        self.assertEqual(
            archive.calls,
            [
                {
                    "max_summaries": 6,
                    "experience_scope": "test-scope",
                    "session_id": "test-session",
                }
            ],
        )

    async def test_repository_rejects_archive_without_snapshot_contract(self):
        with self.assertRaisesRegex(TypeError, "缺少上下文快照读取能力"):
            ContextSnapshotRepository(object())

    async def test_interaction_context_separates_transport_from_real_world_mode(self):
        fact = SimpleNamespace(
            predicate="interaction_mode",
            subject="u1",
            object_value={"mode": "co_present"},
            confidence=0.92,
            observed_at="2026-08-13 12:00:00",
        )
        runtime = InteractionRuntime([fact])
        event = SimpleNamespace(
            unified_msg_origin="aiocqhttp:FriendMessage:u1",
            sender_id="u1",
        )

        context = await runtime.resolve_interaction_context(
            event=event,
            now=datetime.datetime(2026, 8, 13, 12, 30),
        )

        self.assertEqual(context.transport, "私聊")
        self.assertEqual(context.mode, "co_present")
        prompt = context.format_for_generation()
        self.assertIn("只描述平台承载方式，不代表双方现实距离", prompt)
        self.assertIn("按面对面交流回应", prompt)

    async def test_stale_interaction_fact_falls_back_to_unknown(self):
        fact = SimpleNamespace(
            predicate="interaction_mode",
            subject="u1",
            object_value={"mode": "co_present"},
            confidence=0.95,
            observed_at="2026-08-13 08:00:00",
        )
        runtime = InteractionRuntime([fact])

        context = await runtime.resolve_interaction_context(
            target_scope="aiocqhttp:FriendMessage:u1",
            now=datetime.datetime(2026, 8, 13, 12, 1),
        )

        self.assertEqual(context.mode, "unknown")
        self.assertFalse(
            interaction_fact_is_current(
                fact,
                now=datetime.datetime(2026, 8, 13, 12, 1),
            )
        )

    async def test_current_turn_does_not_treat_previous_remote_fact_as_current(self):
        fact = SimpleNamespace(
            predicate="interaction_mode",
            subject="u1",
            object_value={"mode": "remote"},
            confidence=0.9,
            observed_at="2026-08-13 12:00:00",
        )
        runtime = InteractionRuntime([fact])
        event = SimpleNamespace(
            unified_msg_origin="aiocqhttp:FriendMessage:u1",
            sender_id="u1",
            message_str="测试对象补充了当前场景",
        )

        context = await runtime.resolve_interaction_context(
            event=event,
            now=datetime.datetime(2026, 8, 13, 12, 30),
        )

        self.assertEqual(context.mode, "unknown")
        self.assertEqual(context.previous_mode, "remote")
        self.assertTrue(context.pending_current)
        prompt = context.format_for_generation()
        self.assertIn("这不是当前话轮的结论", prompt)
        self.assertIn("待根据当前完整话轮的语义确认", prompt)
        self.assertIn("历史 assistant 回复只是已经说过的话", prompt)
        self.assertIn("会话摘要和关系记忆只用于回忆话题与关系", prompt)

    async def test_current_semantic_decision_overrides_and_persists_remote_fact(self):
        fact = SimpleNamespace(
            predicate="interaction_mode",
            subject="u1",
            object_value={"mode": "remote"},
            confidence=0.9,
            observed_at="2026-08-13 12:00:00",
        )
        runtime = InteractionRuntime([fact])
        event = SimpleNamespace(
            unified_msg_origin="aiocqhttp:FriendMessage:u1",
            sender_id="u1",
            message_id="synthetic-message",
            message_str="测试对象明确说明双方已经在同一现场",
        )

        decision = await runtime.apply_interaction_turn_decision(
            event,
            {
                "decision": "set",
                "mode": "co_present",
                "confidence": 0.94,
                "reason": "当前完整话轮明确说明双方同处现场",
            },
            now=datetime.datetime(2026, 8, 13, 12, 31),
        )
        context = await runtime.resolve_interaction_context(
            event=event,
            now=datetime.datetime(2026, 8, 13, 12, 31),
        )

        self.assertEqual(decision["mode"], "co_present")
        self.assertEqual(context.mode, "co_present")
        self.assertIn("按面对面交流回应", context.format_for_generation())
        self.assertEqual(runtime.writes[0][0], "UPDATE")
        self.assertEqual(runtime.writes[0][1]["object_value"], {"mode": "co_present"})
        self.assertEqual(runtime.writes[0][1]["source"], "chat_turn_semantic")


if __name__ == "__main__":
    unittest.main()
