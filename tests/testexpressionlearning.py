import copy
import datetime
import tempfile
import unittest
from pathlib import Path

from support import DailyLifeRuntime, Event, LifeSettings
from core.archive import LifeArchive
from core.models import ExpressionProfileRecord, ExpressionReviewRecord, ReplyEffectRecord


class ExpressionLearningTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.config = LifeSettings.from_dict({})
        self.runtime.archive = LifeArchive(Path(self.temp.name) / "life.db")
        self.batch = {
            "id": 1,
            "session_id": "test:FriendMessage:u1",
            "messages": [
                self.row(1, "assistant", "今晚的电影我还想聊两句，你呢？"),
                self.row(2, "user", "闲聊就短一点，接着刚才的事说就好，别总反问我"),
                self.row(3, "assistant", "嗯，刚才那个结尾我也没想到"),
            ],
        }

    async def asyncTearDown(self):
        self.runtime.archive.close()
        self.temp.cleanup()

    @staticmethod
    def row(index, role, text, profile_id="u1"):
        return {
            "id": index,
            "message_id": f"m{index}",
            "role": role,
            "message_text": text,
            "sender_profile_id": profile_id if role == "user" else "",
            "sender_name": "小林" if role == "user" else "角色",
            "occurred_at": "2026-10-08T12:00:00",
            "is_group": False,
        }

    def profile(self, **changes):
        return {
            "profile_id": "u1",
            "label": "日常闲聊",
            "tone": "轻松简短",
            "habits": ["顺着当前话题接一句"],
            "avoid": ["每轮结尾都追问"],
            "evidence": "用户明确希望闲聊简短并减少反问",
            "confidence": 0.9,
            "basis": "explicit",
            "source_message_ids": ["m2"],
            **changes,
        }

    def normalize(self, profile, batch=None):
        return self.runtime._normalize_chat_memory_batch_payload(
            {"worth_saving": False, "expression_profiles": [profile]},
            batch or self.batch,
        )

    def test_explicit_feedback_has_authoritative_scope_and_owner(self):
        payload = self.normalize(self.profile(scope="foreign", source="foreign"))
        record = payload["expression_profiles"][0]
        self.assertEqual(record["scope"], self.batch["session_id"])
        self.assertEqual(record["source"], "chat_expression")
        self.assertEqual(record["profile_id"], "u1")

    def test_unreliable_or_misattributed_preferences_are_rejected(self):
        cases = [
            {"source_message_ids": ["missing"]},
            {"source_message_ids": ["m1"]},
            {"profile_id": "u2"},
            {"confidence": 0.5},
            {"confidence": "high"},
            {"confidence": True},
            {"confidence": float("nan")},
            {"confidence": float("inf")},
            {"basis": "guess"},
            {"evidence": ""},
            {"tone": "", "habits": [], "avoid": []},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(
                    self.normalize(self.profile(**changes))["expression_profiles"], []
                )

    def test_repeated_style_needs_three_independent_turns(self):
        batch = copy.deepcopy(self.batch)
        batch["messages"] = [
            self.row(index, "assistant" if index % 2 else "user", "真实回应或反馈")
            for index in range(1, 7)
        ]
        profile = self.profile(
            basis="repeated",
            source_message_ids=["m2", "m4", "m6"],
            reply_message_ids=["m1", "m3", "m5"],
        )
        self.assertEqual(len(self.normalize(profile, batch)["expression_profiles"]), 1)
        profile["reply_message_ids"] = ["m1"]
        self.assertEqual(self.normalize(profile, batch)["expression_profiles"], [])

    async def test_saved_reply_effects_are_scoped_and_support_repeated_learning(self):
        for index in range(3):
            await self.runtime.archive.save_reply_effect(
                ReplyEffectRecord(
                    scope=self.batch["session_id"],
                    target_message_id=f"request-{index}",
                    reply_text="短句接着聊",
                    outcome="positive",
                    evidence="自然接住当前话题",
                )
            )
        await self.runtime.archive.save_reply_effect(
            ReplyEffectRecord(
                scope="other",
                reply_text="外部回复",
                outcome="positive",
                evidence="外部反馈",
            )
        )
        batch = {
            **self.batch,
            **await self.runtime._batch_expression_context(self.batch),
        }
        self.assertEqual(len(batch["recent_reply_effects"]), 3)
        profile = self.profile(
            basis="repeated",
            reply_effect_ids=[
                str(item["id"]) for item in batch["recent_reply_effects"]
            ],
        )
        self.assertEqual(len(self.normalize(profile, batch)["expression_profiles"]), 1)
        profile["reply_effect_ids"].append("999")
        self.assertEqual(self.normalize(profile, batch)["expression_profiles"], [])

    def test_group_effects_cannot_learn_another_users_preference(self):
        batch = copy.deepcopy(self.batch)
        batch["messages"][-1].update(is_group=True, group_id="group")
        batch["recent_reply_effects"] = [
            {
                "id": i,
                "scope": batch["session_id"],
                "outcome": "positive",
                "evidence": "他人认可",
                "target_message_id": f"other-{i}",
            }
            for i in range(1, 4)
        ]
        profile = self.profile(basis="repeated", reply_effect_ids=["1", "2", "3"])
        self.assertEqual(self.normalize(profile, batch)["expression_profiles"], [])

    async def test_learning_survives_skipped_summary_and_records_source(self):
        async def unchanged(payload, *_args):
            return payload

        self.runtime._calibrate_chat_memory_payload = unchanged
        summary = await self.runtime._save_chat_memory_batch_payload(
            {"worth_saving": False, "expression_profiles": [self.profile()]}, self.batch
        )
        self.assertIsNone(summary)
        profiles = await self.runtime.archive.get_expression_profiles(
            scope=self.batch["session_id"]
        )
        self.assertEqual(len(profiles), 1)
        evidence = await self.runtime.archive.get_memory_evidence(
            target_type="expression_profile", target_id=str(profiles[0].id)
        )
        self.assertEqual(
            [(item.source_table, item.source_id) for item in evidence],
            [("chat_memory_messages", "2")],
        )
        context = self.runtime._hidden_expression_profile_line(profiles[0])
        self.assertIn("每轮结尾都追问", context)

    async def test_explicit_correction_clears_old_avoid_and_updates_confidence(self):
        await self.runtime.archive.upsert_expression_profile(
            ExpressionProfileRecord(
                scope=self.batch["session_id"],
                profile_id="u1",
                label="日常闲聊",
                tone="原语气",
                habits=["旧习惯"],
                avoid=["旧避免项"],
                confidence=0.98,
            )
        )
        payload = self.normalize(self.profile(avoid=[], confidence=0.85))
        await self.runtime._save_batch_expression_learning(
            payload,
            self.batch,
            {"session_id": self.batch["session_id"], "date": "2026-10-08"},
        )
        profiles = await self.runtime.archive.get_expression_profiles(
            scope=self.batch["session_id"]
        )
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].avoid, [])
        self.assertEqual(profiles[0].confidence, 0.85)

    def test_review_requires_later_feedback_and_uses_actual_reply(self):
        review = {
            "profile_id": "u1",
            "reply_message_id": "m1",
            "source_message_ids": ["m2"],
            "passed": False,
            "reply_text": "伪造内容",
            "reason": "用户明确要求减少反问",
        }
        payload = self.runtime._normalize_chat_memory_batch_payload(
            {"expression_reviews": [review]}, self.batch
        )
        self.assertEqual(
            payload["expression_reviews"][0]["reply_text"],
            self.batch["messages"][0]["message_text"],
        )
        review["reply_message_id"] = "m3"
        payload = self.runtime._normalize_chat_memory_batch_payload(
            {"expression_reviews": [review]}, self.batch
        )
        self.assertEqual(payload["expression_reviews"], [])

    async def test_chat_snapshot_and_revisit_do_not_load_foreign_profiles(self):
        await self.runtime.archive.upsert_expression_profile(
            ExpressionProfileRecord(
                scope="other",
                profile_id="u1",
                label="其他会话",
                tone="外部语气",
            )
        )
        snapshot = await self.runtime.archive.get_context_snapshot(
            max_summaries=2, experience_scope=self.batch["session_id"]
        )
        self.assertEqual(snapshot["expression_profiles"], [])
        context = await self.runtime._private_revisit_expression_context(
            self.batch["session_id"], None, datetime.datetime(2026, 10, 8, 12)
        )
        self.assertEqual(context["expression_profiles"], [])

    async def test_group_snapshot_uses_profile_scope_and_feedback_session(self):
        session = "test:GroupMessage:group"
        await self.runtime.archive.upsert_expression_profile(ExpressionProfileRecord(
            scope="group", profile_id="u1", label="群聊闲聊", tone="轻松短句"
        ))
        await self.runtime.archive.save_expression_review(ExpressionReviewRecord(
            scope=session, reply_text="重复问候", passed=False, risk="重复关心"
        ))
        await self.runtime.archive.save_reply_effect(ReplyEffectRecord(
            scope=session, reply_text="重复问候", outcome="negative", evidence="明确纠正"
        ))
        snapshot = await self.runtime.archive.get_context_snapshot(
            max_summaries=2, experience_scope="group", session_id=session
        )
        self.assertEqual(len(snapshot["expression_profiles"]), 1)
        self.assertEqual(len(snapshot["expression_reviews"]), 1)
        self.assertEqual(len(snapshot["reply_effects"]), 1)

    def test_proactive_missing_or_failed_review_does_not_pass(self):
        for review in (None, {}, {"passed": "true"}, {"passed": False}):
            with self.subTest(review=review):
                self.assertFalse(
                    self.runtime._expression_review_passed(
                        {"expression_review": review}
                    )
                )
        self.assertTrue(
            self.runtime._expression_review_passed(
                {"expression_review": {"passed": True}}
            )
        )

    async def test_rejected_revisit_keeps_candidate_for_review_and_forces_scope(self):
        payload = {
            "should_reply": True,
            "confidence": 0.95,
            "benefit": 90,
            "timeliness": 90,
            "continuity": 90,
            "disruption": 0,
            "uncertainty": 0,
            "reply_text": "又问一次有没有喝水",
            "expression_review": {
                "passed": False,
                "risk": "重复关心",
                "reason": "刚刚已经问过",
                "scope": "foreign",
                "reply_text": "伪造候选",
            },
        }
        normalized = self.runtime._private_revisit_normalize_payload(
            payload,
            target_scope=self.batch["session_id"],
            revisit_evidence={"can_revisit": True},
        )
        self.assertFalse(normalized["should_reply"])
        self.assertEqual(normalized["reply_text"], "")
        event = Event(unified_msg_origin=self.batch["session_id"])
        await self.runtime._save_proactive_expression_records(
            event, normalized, "", source="private_revisit"
        )
        reviews = await self.runtime.archive.get_expression_reviews(
            scope=self.batch["session_id"]
        )
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].reply_text, "又问一次有没有喝水")
        self.assertFalse(reviews[0].passed)
