import asyncio
import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from support import *  # noqa: F401,F403

from core.archive import LifeArchive
from core.config.options import LifeSettings
from core.runtime.live import DailyLifeRuntime


class ChatMemoryArchiveTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.temp.name) / "life.db")

    async def asyncTearDown(self):
        self.archive.close()
        self.temp.cleanup()

    @staticmethod
    def snapshot(session_id="private:1", message_id="1", text="hello", occurred_at="2026-07-10T12:00:00", is_group=False):
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
        first_id, first_inserted = await self.archive.enqueue_chat_memory_message(self.snapshot())
        second_id, second_inserted = await self.archive.enqueue_chat_memory_message(self.snapshot())
        self.assertTrue(first_inserted)
        self.assertFalse(second_inserted)
        self.assertEqual(first_id, second_id)
        sessions = await self.archive.list_chat_memory_sessions()
        self.assertEqual(sessions[0]["pending_count"], 1)

    async def test_completed_batch_advances_persistent_cursor_and_next_batch_is_non_overlapping(self):
        for index in range(1, 5):
            await self.archive.enqueue_chat_memory_message(self.snapshot(message_id=str(index), text=f"message-{index}"))
        first = await self.archive.begin_chat_memory_batch("private:1", max_messages=2, max_chars=1000)
        self.assertEqual([row["message_id"] for row in first["messages"]], ["1", "2"])
        await self.archive.complete_chat_memory_batch(first["id"], summary_id=12)
        second = await self.archive.begin_chat_memory_batch("private:1", max_messages=10, max_chars=1000)
        self.assertEqual([row["message_id"] for row in second["messages"]], ["3", "4"])

    async def test_failed_batch_does_not_advance_cursor_and_reuses_batch_key(self):
        await self.archive.enqueue_chat_memory_message(self.snapshot())
        first = await self.archive.begin_chat_memory_batch("private:1", max_messages=10, max_chars=1000)
        await self.archive.fail_chat_memory_batch(first["id"], "temporary")
        second = await self.archive.begin_chat_memory_batch("private:1", max_messages=10, max_chars=1000)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["batch_key"], second["batch_key"])


class ChatMemoryBatchTriggerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.archive = LifeArchive(Path(self.temp.name) / "life.db")
        self.runtime.config = LifeSettings.from_dict({
            "memory_config": {
                "private_message_threshold": 3,
                "group_message_threshold": 5,
                "idle_flush_seconds": 90,
                "idle_flush_min_messages": 2,
                "max_batch_messages": 10,
                "max_batch_chars": 1000,
                "worker_poll_seconds": 30,
            }
        })
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

    async def enqueue(self, count, *, session_id="private:1", is_group=False, at="2026-07-10T12:00:00"):
        for index in range(1, count + 1):
            snapshot = ChatMemoryArchiveTest.snapshot(session_id, str(index), f"message-{index}", at, is_group)
            await self.runtime.archive.enqueue_chat_memory_message(snapshot)

    async def test_private_threshold_calls_one_batch(self):
        await self.enqueue(2)
        self.assertEqual(await self.runtime.process_due_chat_memory_batches(datetime.datetime(2026, 7, 10, 12, 0, 10)), 0)
        await self.runtime.archive.enqueue_chat_memory_message(ChatMemoryArchiveTest.snapshot(message_id="3", text="message-3"))
        self.assertEqual(await self.runtime.process_due_chat_memory_batches(datetime.datetime(2026, 7, 10, 12, 0, 10)), 1)
        self.assertEqual(len(self.processed[0]["messages"]), 3)

    async def test_group_uses_group_threshold(self):
        await self.enqueue(4, session_id="group:1", is_group=True)
        self.assertEqual(await self.runtime.process_due_chat_memory_batches(datetime.datetime(2026, 7, 10, 12, 0, 10)), 0)
        await self.runtime.archive.enqueue_chat_memory_message(ChatMemoryArchiveTest.snapshot("group:1", "5", "message-5", is_group=True))
        self.assertEqual(await self.runtime.process_due_chat_memory_batches(datetime.datetime(2026, 7, 10, 12, 0, 10)), 1)

    async def test_idle_flush_requires_minimum_messages(self):
        await self.enqueue(1)
        self.assertEqual(await self.runtime.process_due_chat_memory_batches(datetime.datetime(2026, 7, 10, 12, 2, 0)), 0)
        await self.runtime.archive.enqueue_chat_memory_message(ChatMemoryArchiveTest.snapshot(message_id="2", text="message-2"))
        self.assertEqual(await self.runtime.process_due_chat_memory_batches(datetime.datetime(2026, 7, 10, 12, 2, 0)), 1)

    async def test_max_batch_chars_creates_non_overlapping_followup(self):
        await self.enqueue(3)
        self.runtime.config.memory.max_batch_chars = 18
        await self.runtime.process_due_chat_memory_batches(datetime.datetime(2026, 7, 10, 12, 0, 10))
        self.assertEqual([row["message_id"] for row in self.processed[0]["messages"]], ["1"])
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
        self.assertEqual([row["message_id"] for row in self.processed[0]["messages"]], ["1", "2", "3"])

    async def test_backlog_processes_consecutive_non_overlapping_batches(self):
        await self.enqueue(5)
        self.runtime.config.memory.max_batch_messages = 2
        count = await self.runtime.process_due_chat_memory_batches(
            datetime.datetime(2026, 7, 10, 12, 0, 10)
        )
        self.assertEqual(count, 2)
        self.assertEqual(
            [[row["message_id"] for row in batch["messages"]] for batch in self.processed],
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
                {"profile_id": "unknown", "name": "Unknown", "relationship_note": "skip"},
            ]
        }
        saved = await self.runtime._save_batch_memory_targets(
            payload, batch, {"sender_profile_id": "u2", "sender_name": "Bob"}
        )
        self.assertEqual([item["profile_id"] for item in saved], ["u1", "u2"])
        self.assertEqual(
            [
                (meta["sender_profile_id"], meta["sender_name"], meta["platform"], meta["user_id"], meta["message_id"])
                for _, meta in calls
            ],
            [
                ("u1", "Alice", "onebot", "1001", "m1"),
                ("u2", "Bob", "telegram", "2002", "m2"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
