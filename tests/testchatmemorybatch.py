import asyncio
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from support import *  # noqa: F401,F403

from core.archive import LifeArchive
from core.config.options import LifeSettings
from core.models import DayRecord
from core.runtime.live import DailyLifeRuntime


class ChatMemoryArchiveTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.temp.name) / "life.db")

    async def asyncTearDown(self):
        self.archive.close()
        self.temp.cleanup()

    @staticmethod
    def snapshot(
        session_id="private:1",
        message_id="1",
        text="hello",
        occurred_at="2026-07-10T12:00:00",
        is_group=False,
    ):
        return {
            "event_key": f"{session_id}:{message_id}",
            "session_id": session_id,
            "message_id": message_id,
            "sender_profile_id": "user-1",
            "sender_name": "Alice",
            "platform": "test",
            "user_id": "user-1",
            "group_id": "group-1" if is_group else "",
            "group_name": "Group" if is_group else "",
            "is_group": is_group,
            "is_directed": False,
            "is_quoted": False,
            "message_text": text,
            "message_facts": text,
            "quote_context": "",
            "structured_context": "",
            "occurred_at": occurred_at,
        }

    async def test_duplicate_event_key_is_idempotent(self):
        first_id, first_inserted = await self.archive.enqueue_chat_memory_message(
            self.snapshot()
        )
        second_id, second_inserted = await self.archive.enqueue_chat_memory_message(
            self.snapshot()
        )
        self.assertTrue(first_inserted)
        self.assertFalse(second_inserted)
        self.assertEqual(first_id, second_id)
        sessions = await self.archive.list_chat_memory_sessions()
        self.assertEqual(sessions[0]["pending_count"], 1)

    async def test_completed_batch_advances_persistent_cursor_and_next_batch_is_non_overlapping(
        self,
    ):
        for index in range(1, 5):
            await self.archive.enqueue_chat_memory_message(
                self.snapshot(message_id=str(index), text=f"message-{index}")
            )
        first = await self.archive.begin_chat_memory_batch(
            "private:1", max_messages=2, max_chars=1000
        )
        self.assertEqual([row["message_id"] for row in first["messages"]], ["1", "2"])
        await self.archive.complete_chat_memory_batch(first["id"], summary_id=12)
        second = await self.archive.begin_chat_memory_batch(
            "private:1", max_messages=10, max_chars=1000
        )
        self.assertEqual([row["message_id"] for row in second["messages"]], ["3", "4"])

    async def test_failed_batch_does_not_advance_cursor_and_reuses_batch_key(self):
        await self.archive.enqueue_chat_memory_message(self.snapshot())
        first = await self.archive.begin_chat_memory_batch(
            "private:1", max_messages=10, max_chars=1000
        )
        await self.archive.fail_chat_memory_batch(first["id"], "temporary")
        second = await self.archive.begin_chat_memory_batch(
            "private:1", max_messages=10, max_chars=1000
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["batch_key"], second["batch_key"])


class ChatMemoryBatchTriggerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.archive = LifeArchive(Path(self.temp.name) / "life.db")
        self.runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {
                    "private_message_threshold": 3,
                    "group_message_threshold": 5,
                    "idle_flush_seconds": 90,
                    "idle_flush_min_messages": 2,
                    "max_batch_messages": 10,
                    "max_batch_chars": 1000,
                    "worker_poll_seconds": 30,
                }
            }
        )
        self.runtime._init_chat_memory_batcher()
        self.processed = []

        async def process(batch):
            self.processed.append(batch)
            await self.runtime.archive.complete_chat_memory_batch(batch["id"])
            return True

        self.runtime._process_chat_memory_batch = process

    async def asyncTearDown(self):
        await self.runtime._shutdown_chat_memory_batcher()
        self.runtime.archive.close()
        self.temp.cleanup()

    async def enqueue(
        self, count, *, session_id="private:1", is_group=False, at="2026-07-10T12:00:00"
    ):
        for index in range(1, count + 1):
            snapshot = ChatMemoryArchiveTest.snapshot(
                session_id, str(index), f"message-{index}", at, is_group
            )
            await self.runtime.archive.enqueue_chat_memory_message(snapshot)

    async def test_private_threshold_calls_one_batch(self):
        await self.enqueue(2)
        self.assertEqual(
            await self.runtime.process_due_chat_memory_batches(
                datetime.datetime(2026, 7, 10, 12, 0, 10)
            ),
            0,
        )
        await self.runtime.archive.enqueue_chat_memory_message(
            ChatMemoryArchiveTest.snapshot(message_id="3", text="message-3")
        )
        self.assertEqual(
            await self.runtime.process_due_chat_memory_batches(
                datetime.datetime(2026, 7, 10, 12, 0, 10)
            ),
            1,
        )
        self.assertEqual(len(self.processed[0]["messages"]), 3)

    async def test_group_uses_group_threshold(self):
        await self.enqueue(4, session_id="group:1", is_group=True)
        self.assertEqual(
            await self.runtime.process_due_chat_memory_batches(
                datetime.datetime(2026, 7, 10, 12, 0, 10)
            ),
            0,
        )
        await self.runtime.archive.enqueue_chat_memory_message(
            ChatMemoryArchiveTest.snapshot("group:1", "5", "message-5", is_group=True)
        )
        self.assertEqual(
            await self.runtime.process_due_chat_memory_batches(
                datetime.datetime(2026, 7, 10, 12, 0, 10)
            ),
            1,
        )

    async def test_idle_flush_requires_minimum_messages(self):
        await self.enqueue(1)
        self.assertEqual(
            await self.runtime.process_due_chat_memory_batches(
                datetime.datetime(2026, 7, 10, 12, 2, 0)
            ),
            0,
        )
        await self.runtime.archive.enqueue_chat_memory_message(
            ChatMemoryArchiveTest.snapshot(message_id="2", text="message-2")
        )
        self.assertEqual(
            await self.runtime.process_due_chat_memory_batches(
                datetime.datetime(2026, 7, 10, 12, 2, 0)
            ),
            1,
        )

    async def test_max_batch_chars_creates_non_overlapping_followup(self):
        await self.enqueue(3)
        self.runtime.config.memory.max_batch_chars = 18
        await self.runtime.process_due_chat_memory_batches(
            datetime.datetime(2026, 7, 10, 12, 0, 10)
        )
        self.assertEqual(
            [row["message_id"] for row in self.processed[0]["messages"]], ["1"]
        )
        states = await self.runtime.archive.list_chat_memory_sessions()
        self.assertEqual(states[0]["pending_count"], 2)

    async def test_worker_immediately_recovers_pending_session_on_startup(self):
        await self.enqueue(3)
        self.runtime._start_chat_memory_batcher()
        for _ in range(50):
            if self.processed:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(len(self.processed), 1)
        self.assertEqual(
            [row["message_id"] for row in self.processed[0]["messages"]],
            ["1", "2", "3"],
        )

    async def test_backlog_processes_consecutive_non_overlapping_batches(self):
        await self.enqueue(5)
        self.runtime.config.memory.max_batch_messages = 2
        count = await self.runtime.process_due_chat_memory_batches(
            datetime.datetime(2026, 7, 10, 12, 0, 10)
        )
        self.assertEqual(count, 2)
        self.assertEqual(
            [
                [row["message_id"] for row in batch["messages"]]
                for batch in self.processed
            ],
            [["1", "2"], ["3", "4"]],
        )
        states = await self.runtime.archive.list_chat_memory_sessions()
        self.assertEqual(states[0]["pending_count"], 1)

    async def test_group_memory_targets_use_each_profiles_own_metadata(self):
        calls = []

        async def save_targets(payload, meta):
            calls.append((payload, meta))
            return [{"profile_id": meta["sender_profile_id"]}]

        self.runtime._save_memory_targets = save_targets
        batch = {
            "session_id": "group:1",
            "messages": [
                {
                    "id": 1,
                    "message_id": "m1",
                    "sender_profile_id": "u1",
                    "sender_name": "Alice",
                    "platform": "onebot",
                    "user_id": "1001",
                },
                {
                    "id": 2,
                    "message_id": "m2",
                    "sender_profile_id": "u2",
                    "sender_name": "Bob",
                    "platform": "telegram",
                    "user_id": "2002",
                },
            ],
        }
        payload = {
            "memory_targets": [
                {"profile_id": "u1", "name": "Alice", "relationship_note": "note-a"},
                {"profile_id": "u2", "name": "Bob", "relationship_note": "note-b"},
                {
                    "profile_id": "unknown",
                    "name": "Unknown",
                    "relationship_note": "skip",
                },
            ]
        }
        saved = await self.runtime._save_batch_memory_targets(
            payload, batch, {"sender_profile_id": "u2", "sender_name": "Bob"}
        )
        self.assertEqual([item["profile_id"] for item in saved], ["u1", "u2"])
        self.assertEqual(
            [
                (
                    meta["sender_profile_id"],
                    meta["sender_name"],
                    meta["platform"],
                    meta["user_id"],
                    meta["message_id"],
                )
                for _, meta in calls
            ],
            [
                ("u1", "Alice", "onebot", "1001", "m1"),
                ("u2", "Bob", "telegram", "2002", "m2"),
            ],
        )

    def test_batch_prompt_exposes_all_dashboard_memory_categories(self):
        message = ChatMemoryArchiveTest.snapshot(
            session_id="group:1",
            message_id="m1",
            text="神兽这个称呼就是群里开玩笑",
            is_group=True,
        )
        message["id"] = 1
        message["is_directed"] = True
        prompt = self.runtime._build_chat_memory_batch_prompt(
            {"session_id": "group:1", "messages": [message]}
        )
        schema_text = prompt.split("输出结构：", 1)[1].split("\n输入批次：", 1)[0]
        source_text = prompt.split("\n输入批次：", 1)[1]
        schema = json.loads(schema_text)
        source = json.loads(source_text)

        self.assertTrue(
            {
                "visibility",
                "group_environment",
                "action_decision",
                "behavior_feedback",
                "life_terms",
            }.issubset(schema)
        )
        self.assertEqual(source["scope"]["type"], "group")
        self.assertEqual(source["scope"]["group_id"], "group-1")
        self.assertTrue(source["messages"][0]["is_directed"])
        self.assertIn("自然语言字段必须使用简体中文", prompt)
        self.assertIn("当前角色参与的动作、感受、关系和互动使用第一人称", prompt)
        self.assertIn("叙述视角必须依据主体身份确定", prompt)
        self.assertIn("不能通过词面替换推断主体", prompt)
        self.assertIn("没有内容时字段留空", prompt)
        self.assertIn("不得复制 row_id、message_id、target_id", prompt)

    def test_batch_payload_hides_internal_ids_only_from_readable_fields(self):
        batch = {
            "messages": [
                {"id": 11, "message_id": "251880291"},
                {"id": 12, "message_id": "929722496"},
            ]
        }
        payload = {
            "worth_saving": True,
            "brief": "称呼习惯",
            "long_summary": "用户习惯使用这个称呼",
            "life_terms": [
                {
                    "term": "老婆",
                    "meaning": "亲密称呼",
                    "evidence": "251880291,929722496",
                },
                {
                    "term": "示例昵称",
                    "meaning": "角色称呼",
                    "evidence": "用户明确使用这个称呼；251880291,929722496",
                },
            ],
            "commitments": [
                {
                    "content": "明天提醒喝水",
                    "source_message_ids": ["251880291", "929722496"],
                }
            ],
        }

        normalized = self.runtime._normalize_chat_memory_batch_payload(payload, batch)

        self.assertEqual(normalized["life_terms"][0]["evidence"], "来自 2 条聊天消息")
        self.assertEqual(
            normalized["life_terms"][1]["evidence"], "用户明确使用这个称呼"
        )
        self.assertEqual(
            normalized["commitments"][0]["source_message_ids"],
            ["251880291", "929722496"],
        )

    async def test_empty_english_explanations_are_not_saved_as_memory(self):
        message = ChatMemoryArchiveTest.snapshot(
            session_id="group:1",
            message_id="m1",
            text="群里随手聊了两句",
            is_group=True,
        )
        message["id"] = 1
        await self.runtime.archive.save_day(DayRecord(date="2026-07-10"))

        saved = await self.runtime._save_chat_memory_batch_payload(
            {
                "worth_saving": False,
                "brief": "No stable long-term facts found.",
                "long_summary": (
                    "No stable long-term facts, preferences, or commitments "
                    "found in this batch."
                ),
                "visibility": {
                    "level": "ignored",
                    "attention_level": 0,
                    "priority": "low",
                    "is_directed_at_bot": False,
                    "freshness": "stale",
                    "psychological_freshness": 0,
                    "reason": "No messages in this batch are directed at the bot.",
                },
                "action_decision": {
                    "action": "skip_memory",
                    "reason": (
                        "No stable long-term facts, preferences, or commitments "
                        "found in this batch."
                    ),
                    "understanding": "understood",
                },
            },
            {"session_id": "group:1", "messages": [message]},
        )

        self.assertIsNone(saved)
        self.assertEqual(await self.runtime.archive.get_recent_chat_summaries(10), [])
        self.assertEqual(
            await self.runtime.archive.get_message_visibility_records(10), []
        )
        self.assertEqual(await self.runtime.archive.get_action_decision_records(10), [])
        self.assertEqual(
            (await self.runtime.archive.get_day("2026-07-10")).state_log, []
        )

    async def test_commitment_survives_when_long_term_summary_is_empty(self):
        message = ChatMemoryArchiveTest.snapshot(
            session_id="private:1",
            message_id="m1",
            text="周六一起去看展",
        )
        message["id"] = 1
        saved = await self.runtime._save_chat_memory_batch_payload(
            {
                "worth_saving": False,
                "brief": "No long-term summary.",
                "commitments": [
                    {
                        "content": "周六一起去看展",
                        "kind": "plan",
                        "trigger_date": "2026-07-11",
                        "people": ["Alice"],
                        "confidence": 0.95,
                        "source_message_ids": ["m1"],
                    }
                ],
            },
            {"session_id": "private:1", "messages": [message]},
        )

        self.assertIsNone(saved)
        commitments = await self.runtime.archive.get_commitments(limit=10)
        self.assertEqual(len(commitments), 1)
        self.assertEqual(commitments[0].content, "周六一起去看展")

    async def test_batch_payload_saves_awareness_without_long_term_summary(self):
        message = ChatMemoryArchiveTest.snapshot(
            session_id="group:1",
            message_id="m1",
            text="群里正在接着聊刚才的话题",
            is_group=True,
        )
        message["id"] = 1
        saved = await self.runtime._save_chat_memory_batch_payload(
            {
                "worth_saving": False,
                "visibility": {
                    "level": "focused",
                    "attention_level": 82,
                    "reason": "群聊正在直接讨论我",
                },
                "group_environment": {
                    "atmosphere": "玩梗",
                    "topic": "延续刚才的称呼梗",
                    "is_discussing_bot": True,
                    "summary": "群里在轻松接梗",
                },
                "action_decision": {
                    "action": "observe",
                    "reason": "先看群友怎么把梗接下去",
                },
            },
            {"session_id": "group:1", "messages": [message]},
        )

        self.assertIsNone(saved)
        self.assertEqual(
            (await self.runtime.archive.get_message_visibility_records(10))[0].reason,
            "群聊正在直接讨论我",
        )
        self.assertEqual(
            (await self.runtime.archive.get_recent_group_environments(10))[0].topic,
            "延续刚才的称呼梗",
        )
        self.assertEqual(
            (await self.runtime.archive.get_action_decision_records(10))[0].reason,
            "先看群友怎么把梗接下去",
        )

    async def test_batch_payload_saves_feedback_and_language_records(self):
        async def learn_preferences_from_payload(payload, *, date_str, source):
            del payload, date_str, source
            return []

        self.runtime.composer = SimpleNamespace(
            learn_preferences_from_payload=learn_preferences_from_payload
        )
        self.runtime._schedule_chat_memory_memos = lambda *args, **kwargs: None
        message = ChatMemoryArchiveTest.snapshot(
            session_id="group:1",
            message_id="m1",
            text="刚才那样接梗挺自然，神兽就是群里对你的调侃称呼",
            is_group=True,
        )
        message["id"] = 1
        saved = await self.runtime._save_chat_memory_batch_payload(
            {
                "worth_saving": True,
                "brief": "群聊反馈和称呼梗",
                "long_summary": "用户认可刚才的接梗方式，并解释神兽这个群聊称呼。",
                "behavior_feedback": [
                    {
                        "scene": "群聊接梗",
                        "action": "轻松接话",
                        "feedback": "刚才那样接梗挺自然",
                        "result": "positive",
                        "score": 1.0,
                    }
                ],
                "life_terms": [
                    {
                        "term": "神兽",
                        "meaning": "群里用来调侃机器人的称呼",
                        "scope": "group-1",
                        "scene": "群聊玩梗",
                        "examples": ["神兽来了"],
                        "familiarity": 60,
                        "confidence": 0.9,
                        "evidence": "1,m1",
                    }
                ],
            },
            {"session_id": "group:1", "messages": [message]},
        )

        self.assertIsNotNone(saved)
        feedback = await self.runtime.archive.get_behavior_feedback(10)
        terms = await self.runtime.archive.get_life_terms(10)
        self.assertEqual(feedback[0].feedback, "刚才那样接梗挺自然")
        self.assertEqual(terms[0].term, "神兽")
        self.assertEqual(terms[0].meaning, "群里用来调侃机器人的称呼")
        self.assertEqual(terms[0].evidence, "来自 2 条聊天消息")


if __name__ == "__main__":
    unittest.main()
