import json
import unittest
from types import SimpleNamespace

from core.runtime.capture.batch import ChatMemoryBatchMixin


class Archive:
    def __init__(self):
        self.writes = []
        self.signals = []

    async def write_temporal_fact(self, operation, payload):
        self.writes.append((operation, payload))
        return SimpleNamespace(id=len(self.writes), **payload)

    async def add_fact_evidence_signal(self, payload):
        self.signals.append(payload)
        return payload


class Runtime(ChatMemoryBatchMixin):
    def __init__(self):
        self.archive = Archive()

    @staticmethod
    def _chinese_text_payload(value):
        return str(value or "").strip()


class TemporalMemoryIntegrationTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def batch():
        return {
            "id": 3,
            "session_id": "private:u1",
            "messages": [
                {
                    "id": 11,
                    "message_id": "m11",
                    "occurred_at": "2026-08-01 10:30:00",
                    "role": "user",
                    "sender_profile_id": "u1",
                    "sender_name": "阿林",
                    "message_text": "",
                }
            ],
            "current_temporal_facts": [
                {
                    "scope": "private:u1",
                    "subject": "u1",
                    "predicate": "favorite_food",
                    "object_value": "煲仔饭",
                }
            ],
        }

    def test_prompt_exposes_current_facts_and_explicit_operations(self):
        runtime = Runtime()
        prompt = runtime._build_chat_memory_batch_prompt(self.batch())
        self.assertIn("ADD|UPDATE|INVALIDATE|NONE", prompt)
        self.assertIn("current_temporal_facts", prompt)
        self.assertIn("favorite_food", prompt)
        self.assertIn("不得省略历史变化而直接覆盖", prompt)

    async def test_temporal_fact_save_locks_scope_and_source_message(self):
        runtime = Runtime()
        saved = await runtime._save_batch_temporal_facts(
            {
                "temporal_facts": [
                    {
                        "operation": "UPDATE",
                        "subject": "u1",
                        "predicate": "favorite_food",
                        "object_value": "窝蛋牛肉煲仔饭",
                        "confidence": 0.9,
                        "source_message_id": "11",
                        "evidence_signal": "reinforce",
                        "evidence_summary": "对方明确补充了口味。",
                    },
                    {
                        "operation": "ADD",
                        "subject": "u1",
                        "predicate": "unknown",
                        "object_value": True,
                        "source_message_id": "not-in-batch",
                    },
                ]
            },
            self.batch(),
        )
        self.assertEqual(len(saved), 1)
        operation, fact = runtime.archive.writes[0]
        self.assertEqual(operation, "UPDATE")
        self.assertEqual(fact["scope"], "private:u1")
        self.assertEqual(fact["source_id"], "m11")
        self.assertEqual(fact["valid_from"], "2026-08-01 10:30:00")
        self.assertEqual(len(runtime.archive.signals), 1)
        self.assertEqual(runtime.archive.signals[0]["signal"], "reinforce")
        json.dumps(fact["provenance"])

    async def test_stale_batch_fact_does_not_attach_signal_to_current_fact(self):
        runtime = Runtime()

        async def keep_current_fact(operation, payload):
            runtime.archive.writes.append((operation, payload))
            return SimpleNamespace(
                id=9,
                source="life_action_receipt",
                source_id="receipt-1",
            )

        runtime.archive.write_temporal_fact = keep_current_fact
        saved = await runtime._save_batch_temporal_facts(
            {
                "temporal_facts": [
                    {
                        "operation": "UPDATE",
                        "subject": "self",
                        "predicate": "current_place",
                        "object_value": "旧测试地点",
                        "confidence": 0.9,
                        "source_message_id": "11",
                        "evidence_signal": "dispute",
                        "evidence_summary": "较早消息中的地点描述。",
                    }
                ]
            },
            self.batch(),
        )

        self.assertEqual(len(saved), 1)
        self.assertEqual(runtime.archive.signals, [])


if __name__ == "__main__":
    unittest.main()
