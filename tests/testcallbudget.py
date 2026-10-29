"""省调用不能牺牲当前事实、人物边界和可验证的请求计量。"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.contextbudget import (
    EvidenceCache,
    compact_json,
)
from core.telemetry import (
    ModelCacheMetrics,
    model_task,
    model_task_identity,
    output_usage,
)
from support import DailyLifeRuntime, LifeSettings


class ContextBudgetTest(unittest.TestCase):


    def test_cache_does_not_allow_mutation_or_unbounded_growth(self):
        cache = EvidenceCache(2)
        cache.put("one", {"items": [1]})
        value = cache.get("one")
        value["items"].append(2)
        self.assertEqual(cache.get("one"), {"items": [1]})
        cache.put("two", {})
        cache.put("three", {})
        self.assertIsNone(cache.get("one"))
        self.assertEqual(compact_json({"b": 2, "a": "中文"}), compact_json({"a": "中文", "b": 2}))


class MemoryAuditReuseTest(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime._get_memory_provider = AsyncMock(return_value=object())
        runtime.call_text_model = AsyncMock(return_value=json.dumps({
            "needs_revision": False, "interaction_checked": True,
            "checked_target_ids": ["friend"],
        }))
        runtime.close_text_session = AsyncMock()
        return runtime

    async def test_batch_checked_target_reuses_only_same_persona_and_message_evidence(self):
        runtime = self.runtime()
        target = {"profile_id": "friend", "name": "小林", "persona_hint": "男生，摄影同好", "points": ["一起看展"]}
        payload = {"worth_saving": True, "memory_targets": [target]}
        meta = {"sender_profile_id": "friend", "interaction_messages_json": '[{"id":"one","content":"一起看展"}]'}
        result = await runtime._calibrate_chat_memory_payload(payload, meta, "当前角色是女生")
        await runtime._calibrate_memory_target_payload(result["memory_targets"][0], {**meta, "sender_profile_id": "friend", "message_id": "different-delivery", "sender_name": "小林"}, target["persona_hint"])
        self.assertEqual(runtime.call_text_model.await_count, 1)
        await runtime._calibrate_chat_memory_payload(payload, meta, "当前角色是女生")
        self.assertEqual(runtime.call_text_model.await_count, 1)
        await runtime._calibrate_memory_target_payload(target, {**meta, "interaction_messages_json": "新证据"}, target["persona_hint"])
        await runtime._calibrate_memory_target_payload(target, meta, "更正：是同事")
        runtime.config.memory.provider = "另一个模型"
        await runtime._calibrate_memory_target_payload(target, meta, target["persona_hint"])
        self.assertEqual(runtime.call_text_model.await_count, 4)

    async def test_failed_or_unchecked_review_never_grants_audit_reuse(self):
        runtime = self.runtime()
        runtime.call_text_model.return_value = "{}"
        target = {"profile_id": "friend", "persona_hint": "朋友", "points": ["一起看展"]}
        for _ in range(2):
            await runtime._calibrate_memory_target_payload(target, {}, "朋友")
        self.assertEqual(runtime.call_text_model.await_count, 2)
        runtime.call_text_model.return_value = '{"needs_revision":false}'
        await runtime._calibrate_chat_memory_payload({"memory_targets": [target]}, {}, "我")
        await runtime._calibrate_memory_target_payload(target, {}, "朋友")
        self.assertEqual(runtime.call_text_model.await_count, 4)


class TaskUsageTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_task_labels_and_nested_json_repair_are_isolated(self):
        async def call(purpose):
            with model_task(purpose):
                await asyncio.sleep(0)
                base = model_task_identity("daily_life_private-user-id")
                with model_task(stage="json_repair"):
                    repaired = model_task_identity("")
                return base, repaired, model_task_identity("")
        values = await asyncio.gather(call("voice_expression"), call("semantic_segment"))
        for purpose, value in zip(("voice_expression", "semantic_segment"), values):
            self.assertEqual(value, ((purpose, "request"), (purpose, "json_repair"), (purpose, "request")))
        self.assertEqual(model_task_identity("private-user-id"), ("other_internal", "request"))

    def test_actual_output_usage_and_retry_repair_requests(self):
        meter = ModelCacheMetrics()
        for attempt, stage in ((0, "request"), (1, "request"), (0, "json_repair")):
            meter.record({"usage": {"prompt_tokens": 100, "completion_tokens": 12, "prompt_cache_hit_tokens": 60}},
                         kind="internal", model="测试模型", purpose="wardrobe", attempt=attempt, stage=stage)
        row = meter.snapshot()["tasks"][0]
        self.assertEqual((row["requests"], row["retry_requests"], row["repair_requests"]), (3, 1, 1))
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["cached_tokens"]), (300, 36, 180))
        self.assertEqual(output_usage({"usageMetadata": {"candidatesTokenCount": 7}}), 7)
        self.assertIsNone(output_usage(SimpleNamespace()))

    def test_missing_usage_remains_unknown_and_reported_zero_is_distinct(self):
        meter = ModelCacheMetrics()
        meter.record({}, kind="internal", purpose="wardrobe")
        row = meter.snapshot()["tasks"][0]
        for name in ("input", "cached", "output"):
            self.assertIsNone(row[f"{name}_tokens"])
            self.assertEqual(row[f"{name}_reported_requests"], 0)
        meter.record({"usage": {"prompt_tokens": 20, "completion_tokens": 0,
                                "prompt_cache_hit_tokens": 0}},
                     kind="internal", purpose="wardrobe")
        row = meter.snapshot()["tasks"][0]
        self.assertEqual((row["input_tokens"], row["cached_tokens"], row["output_tokens"]), (20, 0, 0))
        self.assertEqual(row["cached_reported_requests"], 1)
