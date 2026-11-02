"""用真实 SQLite 和消息队列检验关系变化的来源、去重、纠错与调用链。"""

import asyncio
import datetime
import tempfile
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from support import LifeSettings
from core.archive import LifeArchive
from core.relationship import normalize_assessment, relationship_summary, response_bias
from core.runtime.bonding import batch_relationship_context, settle_batch_relationships, relationship_context, contextual_relationship_memories, capture_bili_relationship, relationship_only_batch
from core.runtime.capture.batch import ChatMemoryBatchComponent
from core.models.thought import AffectiveStateRecord
from core.runtime.contact import contact_rhythm
from core.life.evolution import LifeEvolutionService


class RelationshipLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "life.db"
        self.db = LifeArchive(self.path)
        self.scope = "qq:FriendMessage:alice"
        await self.db.touch_relationship("alice", name="小夏", date_str="2026-11-02",
                                         platform="qq", user_id="alice", contact_type="friend", target_scope=self.scope)
        await self.db.touch_relationship("bob", name="小夏", date_str="2026-11-02")

    async def asyncTearDown(self):
        self.db.close()
        self.temp.cleanup()

    async def row(self, number="1", *, role="user", person="alice", scope=None, day="2026-11-02", text="这次你记得我们的约定，我很安心。"):
        scope = scope or self.scope
        data = {"session_id": scope, "event_key": scope + ":" + number, "message_id": number,
                "role": role, "sender_profile_id": person, "sender_name": person,
                "platform": "qq", "user_id": person, "is_group": False, "is_directed": True,
                "is_quoted": False, "message_text": text, "occurred_at": day + " 12:00:00"}
        identity, _ = await self.db.enqueue_chat_memory_message(data)
        return {**data, "id": identity}

    async def apply(self, row=None, **changes):
        row = row or await self.row()
        raw = {"profile_id": "alice", "source_message_ids": [row["message_id"]],
               "trust_delta": .04, "confidence": .9, "reason": "兑现约定带来了可靠感", **changes}
        return await self.db.apply_relationship_assessment(raw, session_id=row["session_id"], rows=[row])

    async def state(self):
        return (await self.db.get_relationship("alice")).relationship_state

    async def test_independent_dimensions_and_unknown_baseline(self):
        self.assertEqual((await self.state())["observed_dimensions"], [])
        self.assertIn("尚无足够证据", relationship_summary(await self.state()))
        result = await self.apply(familiarity_delta=.02, affinity_delta=-.03)
        self.assertEqual(result["status"], "applied")
        state = await self.state()
        self.assertAlmostEqual(state["familiarity"], .02)
        self.assertAlmostEqual(state["trust"], .54)
        self.assertAlmostEqual(state["affinity"], .47)

    async def test_retry_concurrency_and_restart_do_not_rescore(self):
        row = await self.row()
        results = await asyncio.gather(*(self.apply(row) for _ in range(6)))
        self.assertEqual([r["status"] for r in results].count("applied"), 1)
        self.db.close()
        self.db = LifeArchive(self.path)
        self.assertEqual((await self.apply(row))["status"], "duplicate")
        self.assertAlmostEqual((await self.state())["trust"], .54)

    async def test_overlapping_evidence_cannot_reinforce(self):
        first, second = await self.row("1"), await self.row("2")
        await self.apply(first)
        result = await self.db.apply_relationship_assessment(
            {"profile_id": "alice", "source_message_ids": ["1", "2"], "trust_delta": .04,
             "confidence": .9, "reason": "同一经历再次提及"}, session_id=self.scope, rows=[first, second])
        self.assertEqual(result["status"], "duplicate")

    async def test_restatement_links_to_old_event(self):
        first = await self.apply()
        again = await self.apply(await self.row("2"), change_id=first["change_id"])
        self.assertEqual(again["status"], "duplicate")
        self.assertEqual(len(await self.db.get_relationship_changes("alice")), 1)
        self.assertEqual((await self.db.get_relationship_changes("alice"))[0]["source_message_ids"], ["1", "2"])

    async def test_retraction_cannot_apply_an_extra_inverse_delta(self):
        first = await self.apply(trust_delta=.04)
        await self.apply(await self.row("correct", day="2026-11-03"), trust_delta=-.04,
                         supersedes_change_id=first["change_id"], correction_mode="retract")
        self.assertAlmostEqual((await self.state())["trust"], .5)

    async def test_replacement_uses_complete_new_interpretation(self):
        first = await self.apply(trust_delta=.04)
        await self.apply(await self.row("correct", day="2026-11-03"), trust_delta=-.02,
                         supersedes_change_id=first["change_id"], correction_mode="replace")
        self.assertAlmostEqual((await self.state())["trust"], .48)

    async def test_no_change_does_not_consume_evidence(self):
        row = await self.row()
        self.assertEqual((await self.apply(row, trust_delta=0))["status"], "unchanged")
        self.assertEqual((await self.apply(row))["status"], "applied")

    async def test_disabled_memory_does_not_enqueue_bili_streams(self):
        config = LifeSettings.from_dict({})
        config.memory.enabled = False
        runtime = SimpleNamespace(archive=self.db, config=config)
        self.assertFalse(await capture_bili_relationship(runtime, {
            "scope": self.scope, "uid": "42", "id": "incoming", "kind": "dm_in", "status": "new",
            "text": "内容", "at": "2026-11-02T12:00:00"}, {"uid": "42"}, account="bot"))
        self.assertEqual(await self.db.list_chat_memory_sessions(), [])

    async def test_daily_budget_limits_repeated_events(self):
        for number in range(8):
            await self.apply(await self.row(str(number)), trust_delta=.08)
        self.assertAlmostEqual((await self.state())["trust"], .58)
        await self.apply(await self.row("tomorrow", day="2026-11-03"))
        self.assertAlmostEqual((await self.state())["trust"], .62)

    async def test_uncertain_evidence_does_not_set_long_term_values(self):
        result = await self.apply(confidence=.3)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual((await self.state())["observed_dimensions"], [])
        self.assertEqual((await self.state())["trust"], .5)

    async def test_correction_reverses_old_change_and_is_idempotent(self):
        first = await self.apply(trust_delta=.06)
        row = await self.row("correct", day="2026-11-03", text="刚才说的是另一个人，你误会了。")
        corrected = await self.apply(row, trust_delta=0, supersedes_change_id=first["change_id"], reason="纠正对象归属")
        self.assertEqual(corrected["status"], "applied")
        self.assertAlmostEqual((await self.state())["trust"], .5)
        self.assertEqual((await self.apply(row))["status"], "duplicate")
        self.assertEqual((await self.db.get_relationship_changes("alice"))[-1]["status"], "superseded")

    async def test_wrong_person_forged_row_and_assistant_only_are_rejected(self):
        for row in (await self.row("bob", person="bob"), await self.row("bot", role="assistant")):
            forged = {**row, "sender_profile_id": "alice", "role": "user"}
            self.assertEqual((await self.apply(forged))["status"], "invalid_evidence")
        self.assertEqual((await self.apply(source_message_ids=["missing"]))["status"], "invalid_evidence")

    async def test_cross_scope_and_cross_person_correction_rejected(self):
        first = await self.apply()
        row = await self.row("elsewhere", scope="qq:FriendMessage:elsewhere")
        self.assertEqual((await self.apply(row, supersedes_change_id=first["change_id"]))["status"], "invalid_correction")
        self.assertEqual(await self.db.get_relationship_changes("bob"), [])

    async def test_nonfinite_numbers_and_missing_reason_do_not_create_scores(self):
        raw = {"profile_id": "alice", "reason": "证据", "source_message_ids": ["1"], "trust_delta": float("nan"), "affinity_delta": float("inf")}
        self.assertEqual(normalize_assessment(raw)["deltas"], dict(familiarity=0, trust=0, affinity=0))
        self.assertEqual((await self.apply(reason=""))["status"], "invalid")

    async def test_batch_context_and_settlement_feed_actual_runtime(self):
        row = await self.row()
        batch = {"id": 1, "session_id": self.scope, "messages": [row]}
        output = await settle_batch_relationships(self.db, {"relationship_updates": [{
            "profile_id": "alice", "source_message_ids": ["1"], "confidence": .9,
            "affinity_delta": .06, "reason": "有共同感兴趣的事情"}]}, batch)
        self.assertEqual(output[0]["status"], "applied")
        context = await batch_relationship_context(self.db, batch)
        self.assertEqual(context[0]["recent_changes"][0]["source_message_ids"], ["1"])
        self.assertGreater(response_bias(context[0]["state"]), 0)
        runtime = SimpleNamespace(archive=self.db, config=LifeSettings.from_dict({}))
        rhythm = await contact_rhythm(runtime, self.scope, datetime.datetime(2026,11,2,13), shared=False)
        self.assertAlmostEqual(rhythm["relationship_context"][0]["state"]["affinity"], .56)

    async def test_no_reply_and_elapsed_time_do_not_reduce_relationship(self):
        await self.apply()
        before = await self.state()
        runtime = SimpleNamespace(archive=self.db, config=LifeSettings.from_dict({}))
        for day in (3, 20):
            await contact_rhythm(runtime, self.scope, datetime.datetime(2026,11,day,13), shared=False)
        self.assertEqual(before, await self.state())

    async def test_public_projection_contains_no_private_state(self):
        await self.apply()
        self.assertEqual(await relationship_context(self.db, self.scope, public=True), [])
        self.assertEqual(await relationship_context(self.db, "qq:GroupMessage:one"), [])
        self.assertEqual(await relationship_context(self.db, "global"), [])

    async def test_semantic_recall_is_scoped_and_has_no_keyword_fallback(self):
        reader = AsyncMock(return_value=[SimpleNamespace(id=1, scope=self.scope, content="共同经历"),
                                        SimpleNamespace(id=2, scope="other", content="别人的秘密")])
        runtime = SimpleNamespace(archive=SimpleNamespace(list_recent_long_term_memories=reader))
        self.assertEqual(await contextual_relationship_memories(runtime, scope=self.scope, query="今天的生活"), "")
        async def rank(query, groups, limits, **kwargs):
            self.assertEqual(len(groups["long_term_memory"]), 1)
            self.assertEqual(kwargs["optional_groups"], {"long_term_memory"})
            self.assertTrue(kwargs["cached_only"])
            return {"long_term_memory": [groups["long_term_memory"][0][2]]}
        runtime.rank_semantic_groups = rank
        recalled = await contextual_relationship_memories(runtime, scope=self.scope, query="今天的生活")
        self.assertIn("共同经历", recalled)
        self.assertNotIn("秘密", recalled)

    async def test_daily_review_cannot_reapply_relationship_delta(self):
        await self.apply()
        before = await self.state()
        await LifeEvolutionService(self.db).settle_review(
            {"relationship_updates": [{"profile_id": "alice", "trust_delta": .08, "evidence_ids": ["event:1"], "reason": "旧提示词返回"}]},
            date="2026-11-02", events=[SimpleNamespace(id=1)], decisions=[], feedback=[], reply_effects=[])
        self.assertEqual(before, await self.state())

    async def test_transaction_failure_rolls_back_evidence_and_all_dimensions(self):
        await self.db._run_db(lambda: self.db._conn.execute("CREATE TRIGGER fail_relation BEFORE INSERT ON relationship_states BEGIN SELECT RAISE(ABORT,'test failure'); END"))
        with self.assertRaises(sqlite3.IntegrityError):
            await self.apply()
        self.assertEqual(await self.db.get_relationship_changes("alice"), [])
        self.assertEqual((await self.state())["revision"], 0)
        await self.db._run_db(lambda: self.db._conn.execute("DROP TRIGGER fail_relation"))
        self.assertEqual((await self.apply())["status"], "applied")

    async def test_schema_upgrade_preserves_history_without_inventing_scores(self):
        await self.db.save_affective_state(AffectiveStateRecord(scope="relationship:alice", layer="relationship", label="关系亲近度", intensity=.9))
        def old_schema():
            with self.db._conn:
                for table in ("relationship_evidence_claims", "relationship_states", "relationship_changes"):
                    self.db._conn.execute("DROP TABLE " + table)
                self.db._conn.execute("UPDATE meta SET value='32' WHERE key='schema_version'")
        await self.db._run_db(old_schema)
        self.db.close()
        self.db = LifeArchive(self.path)
        self.assertEqual((await self.state())["observed_dimensions"], [])
        self.assertEqual((await self.db.get_relationship("alice")).name, "小夏")
        self.assertEqual(len(await self.db.get_affective_states(scope="relationship:alice")), 1)

    async def test_correction_does_not_refill_daily_budget(self):
        first = await self.apply(trust_delta=.08)
        await self.apply(await self.row("correction"), supersedes_change_id=first["change_id"], trust_delta=0)
        await self.apply(await self.row("another"), trust_delta=.08)
        self.assertAlmostEqual((await self.state())["trust"], .5)

    async def test_later_negative_event_is_kept_when_older_event_is_corrected(self):
        first = await self.apply(trust_delta=.04)
        await self.apply(await self.row("later", day="2026-11-03"), trust_delta=-.02)
        await self.apply(await self.row("correct", day="2026-11-04"), trust_delta=0, supersedes_change_id=first["change_id"])
        self.assertAlmostEqual((await self.state())["trust"], .48)

    async def test_bili_real_receipts_are_isolated_and_unknown_delivery_is_excluded(self):
        runtime = SimpleNamespace(archive=self.db, _chat_memory_wakeup=asyncio.Event())
        record = {"scope": self.scope, "uid": "42", "id": "receipt-1", "kind": "dm", "status": "uncertain",
                  "text": "这个视频让我想到你", "at": "2026-11-02T12:00:00"}
        self.assertFalse(await capture_bili_relationship(runtime, record, {"uid": "42"}, account="bot"))
        record["status"] = "confirmed"
        self.assertFalse(await capture_bili_relationship(runtime, record, {"uid": "99"}, account="bot"))
        self.assertTrue(await capture_bili_relationship(runtime, record, {"uid": "42"}, account="bot"))
        self.assertFalse(await capture_bili_relationship(runtime, record, {"uid": "42"}, account="bot"))
        sessions = await self.db.list_chat_memory_sessions()
        self.assertTrue(relationship_only_batch(sessions[0]))
        self.assertNotEqual(sessions[0]["session_id"], self.scope)
        self.assertEqual((await self.state())["observed_dimensions"], [])

    async def test_bili_worker_learns_feedback_without_extracting_chat_commitments(self):
        runtime = SimpleNamespace(archive=self.db, config=LifeSettings.from_dict({}),
                                  _get_memory_provider=AsyncMock(return_value=object()),
                                  close_text_session=AsyncMock(),
                                  _validate_chat_memory_payload=ChatMemoryBatchComponent._validate_chat_memory_payload)
        record = {"scope": self.scope, "uid": "42", "id": "incoming-1", "kind": "dm_in", "status": "new",
                  "text": "你还记得我喜欢的内容，这个推荐很合适。", "at": "2026-11-02T12:00:00"}
        await capture_bili_relationship(runtime, record, {"uid": "42"}, account="bot")
        stream = (await self.db.list_chat_memory_sessions())[0]["session_id"]
        batch = await self.db.begin_chat_memory_batch(stream, max_messages=10, max_chars=2000)
        payload = {"relationship_updates": [{"profile_id": "alice", "source_message_ids": ["incoming-1"],
                   "confidence": .9, "affinity_delta": .03, "reason": "分享兴趣得到明确回应"}],
                   "commitments": [{"description": "不应进入普通约定提取"}]}
        with patch("core.runtime.capture.batch.call_pure_json", AsyncMock(return_value=payload)) as model:
            self.assertTrue(await ChatMemoryBatchComponent(runtime)._process_chat_memory_batch(batch))
            self.assertIn("incoming-1", model.call_args.args[2])
        self.assertAlmostEqual((await self.state())["affinity"], .53)
        self.assertEqual(await self.db.get_commitments(), [])
        self.assertEqual(await self.db.list_chat_memory_sessions(), [])

    async def test_cancelled_worker_retries_and_does_not_lose_batch(self):
        row = await self.row(scope="relationship-bilibili:test")
        batch = await self.db.begin_chat_memory_batch(row["session_id"], max_messages=10, max_chars=2000)
        runtime = SimpleNamespace(archive=self.db, config=LifeSettings.from_dict({}),
                                  _get_memory_provider=AsyncMock(return_value=object()), close_text_session=AsyncMock(),
                                  _validate_chat_memory_payload=ChatMemoryBatchComponent._validate_chat_memory_payload)
        with patch("core.runtime.capture.batch.call_pure_json", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await ChatMemoryBatchComponent(runtime)._process_chat_memory_batch(batch)
        retry = await self.db.begin_chat_memory_batch(row["session_id"], max_messages=10, max_chars=2000)
        self.assertIsNotNone(retry)
        self.assertEqual(retry["messages"][0]["id"], row["id"])


if __name__ == "__main__":
    unittest.main()
