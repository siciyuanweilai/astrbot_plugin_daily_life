"""验证前缀稳定、背景不入历史、真实缓存计量和资源释放。"""

import asyncio
import json
import types
import unittest
from unittest.mock import AsyncMock, patch

from runtimehelpers import DailyLifeRuntime, LifeSettings, ProviderRequest
from astrbot.core.agent.message import TextPart
from core.facts import PersonFactContext, build_person_fact_audit_prompt
from core.contextbudget import budget_chat_context
from componentfixtures import DailyContractComponent
from componentfixtures import DailyLocationGenerationComponent
from componentfixtures import SnapshotPackComponent
from core.telemetry import ModelCacheMetrics, cache_prefix_version, cache_usage
from core.life.planner import LifeBackgroundComposer
from core.life.covenant import build_daily_plan_contract
from core.life.reliability import ProviderCircuit
from core.sight import sample
from core.runtime.decisioncontext import decision_prompt_context


class PrefixTest(unittest.TestCase):
    def setUp(self):
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.config = LifeSettings.from_dict({})

    def test_decision_prefix_survives_live_updates_without_reusing_old_facts(self):
        stable = {"persona": "喜欢摄影", "preferences": [{"text": "散步", "id": 1}],
                  "focus": [], "self_model": {"interests": []}}
        first = {"body": {"energy": 76}, "now": "21:30", **stable}
        second = {**stable, "now": "23:05", "body": {"energy": 40}}
        prompts = [decision_prompt_context(value) for value in (first, second)]
        self.assertEqual(prompts[0].split(',"body":', 1)[0],
                         prompts[1].split(',"body":', 1)[0])
        self.assertEqual([json.loads(text) for text in prompts], [first, second])
        self.assertEqual(list(json.loads(prompts[0]))[:4],
                         ["persona", "preferences", "focus", "self_model"])
        self.assertEqual(decision_prompt_context(dict(reversed(list(first.items())))), prompts[0])
        changed = decision_prompt_context({**first, "persona": "开始喜欢绘画"})
        self.assertNotEqual(changed.split(',"body":', 1)[0],
                            prompts[0].split(',"body":', 1)[0])

    def test_current_outfit_and_time_change_without_changing_system_or_history(self):
        history = [{"role": "user", "content": "之前聊过的电影"}]
        requests = []
        for facts in ("当前时间：21:30，穿着蓝裙", "当前时间：23:05，穿着居家睡衣"):
            req = ProviderRequest(prompt="现在穿什么？", system_prompt="角色原有设定")
            req.contexts = history.copy()
            self.runtime._apply_life_request_context(req, facts)
            requests.append(req)
            self.assertEqual(req.contexts, history)
            self.assertEqual(req.prompt, "现在穿什么？")
            self.assertEqual(req.extra_user_content_parts[-1].text, budget_chat_context(facts))
            self.assertTrue(req.extra_user_content_parts[-1].model_dump_for_context()["_no_save"])
        self.assertEqual(requests[0].system_prompt, requests[1].system_prompt)
        self.assertNotIn("蓝裙", requests[0].system_prompt)
        self.assertNotIn("睡衣", requests[1].system_prompt)

    def test_repeat_hook_replaces_only_own_part_and_rules(self):
        other = TextPart(text="其他插件的内容")
        req = ProviderRequest(system_prompt="人设")
        req.extra_user_content_parts.append(other)
        self.runtime._apply_life_request_context(req, "旧生活背景")
        req.system_prompt += "\n其他插件新增规则"
        self.runtime._apply_life_request_context(req, "新生活背景")
        self.assertEqual([part.text for part in req.extra_user_content_parts], [other.text, budget_chat_context("新生活背景")])
        self.assertEqual(req.system_prompt.count("<daily_life_policy>"), 1)
        self.assertIn("其他插件新增规则", req.system_prompt)

    def test_minimal_request_uses_temporary_fact_without_accumulating_old_fact(self):
        req = types.SimpleNamespace(system_prompt="原规则")
        self.runtime._apply_life_request_context(req, "旧背景")
        self.runtime._apply_life_request_context(req, "新背景")
        self.assertEqual([part.text for part in req.extra_user_content_parts], [budget_chat_context("新背景")])
        self.assertTrue(req.extra_user_content_parts[0].model_dump_for_context()["_no_save"])
        self.assertNotIn("新背景", req.system_prompt)
        self.assertNotIn("旧背景", req.system_prompt)

    def test_scene_dependent_internal_rules_have_shared_prefix(self):
        def prefix(prompt, title):
            return prompt.split(f"【{title}】", 1)[0]

        a = build_person_fact_audit_prompt(PersonFactContext(), {}, [], subject="日程")
        b = build_person_fact_audit_prompt(PersonFactContext(), {}, [], subject="穿搭")
        self.assertEqual(prefix(a, "人物事实审计资料"), prefix(b, "人物事实审计资料"))
        contract = DailyContractComponent()
        a = contract._build_repair_prompt("{}", "时间覆盖不足", expected_coverage="full_day", issue_code="timeline_density")
        b = contract._build_repair_prompt("{}", "重复穿搭", issue_code="outfit_repeat")
        self.assertEqual(prefix(a, "日程修复"), prefix(b, "日程修复"))
        self.assertIn("重写 timeline", a)
        self.assertIn("只重写 life_decision.outfit", b)
        a = DailyLocationGenerationComponent._daily_location_request_prompt({"expected_coverage": "full_day"})
        b = DailyLocationGenerationComponent._daily_location_request_prompt({"expected_coverage": "partial"})
        self.assertEqual(prefix(a, "日程地点预选资料"), prefix(b, "日程地点预选资料"))
        self.assertNotEqual(a, b)

    def test_daily_contract_cache_reuses_prefix_and_respects_setting_changes(self):
        composer = LifeBackgroundComposer.__new__(LifeBackgroundComposer)
        composer.config = LifeSettings.from_dict({})
        build_daily_plan_contract.cache_clear()
        draft = composer._part_daily_draft_component
        first = draft._timeline_prompt_fixed_contract("full_day")
        self.assertIs(first, draft._timeline_prompt_fixed_contract("full_day"))
        self.assertEqual(build_daily_plan_contract.cache_info().hits, 1)
        composer.config.state_prompt = "独立配置的身体状态要求"
        changed = draft._timeline_prompt_fixed_contract("full_day")
        self.assertNotEqual(first, changed)
        self.assertIn(composer.config.state_prompt, changed)
        self.assertNotEqual(changed, draft._timeline_prompt_fixed_contract("target_period"))

    def test_daily_contract_preserves_narrative_continuity_with_custom_rules(self):
        prompt = build_daily_plan_contract(
            "契约",
            "示例",
            "状态规则",
            "自定义时间轴规则",
            "地点规则",
        )

        self.assertIn("自定义时间轴规则", prompt)
        self.assertIn("按事情真实发生的顺序连贯呈现主要动作", prompt)
        self.assertIn("不得为氛围补造事实", prompt)
        self.assertIn("status 记录状态，不代替过程描写", prompt)


class UsageTest(unittest.TestCase):
    def test_chat_error_counts_as_failure(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        error = types.SimpleNamespace(role="err", is_error=True, raw_completion=None)
        runtime.note_chat_model_usage(error)
        row = runtime.model_cache_status()["models"][0]
        self.assertEqual(row["failed_requests"], 1)
        self.assertEqual(row["unreported_requests"], 0)

    def test_vendor_usage_formats(self):
        cases = [
            ({"usage": {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 768}}, 1000, 768),
            ({"usage": {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 0}}}, 1000, 0),
            ({"usage": {"input_tokens": 30, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 100}}, 430, 300),
            ({"usage": {"prompt_tokens": 430, "cache_read_input_tokens": 300}}, 430, 300),
            ({"usage_metadata": {"prompt_token_count": 1000, "cached_content_token_count": 500}}, 1000, 500),
            ({"usageMetadata": {"promptTokenCount": 1000, "cachedContentTokenCount": 500}}, 1000, 500),
            ({"usage": {"prompt_tokens": 1000}}, 1000, None),
            ({"usage": {"prompt_tokens": True, "prompt_cache_hit_tokens": -1}}, None, None),
        ]
        for raw, total, cached in cases:
            with self.subTest(raw=raw):
                self.assertEqual(cache_usage(types.SimpleNamespace(raw_completion=raw)), {"input_tokens": total, "cached_tokens": cached})

    def test_unknown_zero_normalization_is_not_false_zero_hit_rate(self):
        response = types.SimpleNamespace(usage=types.SimpleNamespace(input_other=1000, input_cached=0))
        meter = ModelCacheMetrics()
        meter.record(response, kind="chat")
        row = meter.snapshot()["models"][0]
        self.assertIsNone(row["cached_token_ratio"])
        self.assertEqual(row["unreported_requests"], 1)

    def test_weighted_ratio_and_bounded_storage(self):
        meter = ModelCacheMetrics(limit=2)
        for total, cached in ((1000, 900), (100, 0)):
            meter.record({"usage": {"prompt_tokens": total, "prompt_cache_hit_tokens": cached}}, kind="chat", model="same")
        row = meter.snapshot()["models"][0]
        self.assertAlmostEqual(row["cached_token_ratio"], 900 / 1100, places=4)
        self.assertEqual(row["requests_with_hits"], 1)
        for model in ("two", "three"):
            meter.record({}, kind="internal", model=model)
        self.assertEqual(len(meter.entries), 2)
        self.assertNotIn(("chat", "same"), meter.entries)

    def test_cache_ratio_is_separated_by_fixed_prefix_version(self):
        meter = ModelCacheMetrics()
        fixed_a = cache_prefix_version("固定规则A\n\n【资料】\n变化一", has_dynamic_section=True)
        fixed_a_again = cache_prefix_version("固定规则A\n\n【资料】\n变化二", has_dynamic_section=True)
        fixed_b = cache_prefix_version("固定规则B\n\n【资料】\n变化一", has_dynamic_section=True)
        self.assertEqual(fixed_a, fixed_a_again)
        self.assertNotEqual(fixed_a, fixed_b)
        for version, cached in ((fixed_a, 80), (fixed_a_again, 20), (fixed_b, 0)):
            meter.record(
                {"usage": {"prompt_tokens": 100, "prompt_cache_hit_tokens": cached}},
                kind="internal", model="model", prefix_version=version,
            )
        rows = {row["prefix_version"]: row for row in meter.snapshot()["models"]}
        self.assertEqual(rows[fixed_a]["requests"], 2)
        self.assertEqual(rows[fixed_a]["cached_token_ratio"], 0.5)
        self.assertEqual(rows[fixed_b]["cached_token_ratio"], 0.0)
        self.assertNotIn("固定规则", str(rows))


class InternalUsageTest(unittest.IsolatedAsyncioTestCase):
    async def test_chat_identity_keeps_error_and_returned_alias_in_same_group(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        provider = types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(id="聊天分组", model="configured-model"),
        )
        runtime.context = types.SimpleNamespace(
            get_provider_by_id=lambda provider_id: provider,
            get_using_provider_async=AsyncMock(return_value=provider),
        )
        event = types.SimpleNamespace(
            get_extra=lambda key: "聊天分组", unified_msg_origin="test:FriendMessage:1",
        )
        identity = await runtime.capture_chat_model_identity(event)
        runtime.context.get_using_provider_async.assert_not_awaited()
        runtime.note_chat_model_usage(types.SimpleNamespace(
            raw_completion={"model": "returned-alias", "usage": {"prompt_tokens": 10}},
        ), model_identity=identity)
        runtime.note_chat_model_usage(types.SimpleNamespace(
            role="err", is_error=True, raw_completion=None,
        ), model_identity=identity)
        row, = runtime.model_cache_status()["models"]
        self.assertEqual(row["model"], "聊天分组 / configured-model")
        self.assertEqual(row["requests"], 2)
        self.assertEqual(row["failed_requests"], 1)

    async def test_identity_lookup_does_not_hold_foreground_indefinitely(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        cancelled = asyncio.Event()

        async def slow_provider(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        runtime.context = types.SimpleNamespace(get_using_provider_async=slow_provider)
        event = types.SimpleNamespace(unified_msg_origin="test:FriendMessage:1")
        identity = await asyncio.wait_for(runtime.capture_chat_model_identity(event), 0.3)
        self.assertEqual(identity, "")
        self.assertTrue(cancelled.is_set())

    async def test_empty_internal_response_is_a_failed_request(self):
        provider = types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(id="configured-provider"),
            text_chat=AsyncMock(return_value=types.SimpleNamespace(completion_text="")),
        )
        composer = LifeBackgroundComposer.__new__(LifeBackgroundComposer)
        composer.config = LifeSettings.from_dict({})
        composer._provider_circuit = ProviderCircuit()
        composer.model_cache_metrics = ModelCacheMetrics()
        self.assertEqual(await composer._call_llm_text(
            provider, "审查", "test:empty", empty_retries=0,
        ), "")
        row, = composer.model_cache_metrics.snapshot()["models"]
        self.assertEqual(row["failed_requests"], 1)
        self.assertEqual(row["unreported_requests"], 0)

    async def test_success_and_failure_share_provider_group(self):
        provider = types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(id="configured-provider"),
            text_chat=AsyncMock(),
        )
        composer = LifeBackgroundComposer.__new__(LifeBackgroundComposer)
        composer.config = LifeSettings.from_dict({})
        composer._provider_circuit = ProviderCircuit()
        composer.model_cache_metrics = ModelCacheMetrics()
        provider.text_chat.return_value = types.SimpleNamespace(
            completion_text="完成",
            raw_completion={
                "model": "returned-model",
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 50},
                },
            },
        )
        await composer._call_llm_text(provider, "审查", "test:success", empty_retries=0)
        provider.text_chat.side_effect = RuntimeError("模拟失败")
        await composer._call_llm_text(provider, "审查", "test:failure", empty_retries=0)
        rows = composer.model_cache_metrics.snapshot()["models"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["requests"], 2)
        self.assertEqual(rows[0]["failed_requests"], 1)
        self.assertEqual(rows[0]["model"], "configured-provider")


class ResourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_video_download_waiters_share_lock_without_retaining_idle_keys(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def download(*args, **kwargs):
            entered.set()
            await release.wait()
            return None, "测试"

        key = sample.source_fingerprint("https://example.com/test-cache-lock.mp4")
        with patch.object(sample, "_download_remote_video_with_reason_once", new=AsyncMock(side_effect=download)) as worker:
            first = asyncio.create_task(sample._download_remote_video_with_reason("https://example.com/test-cache-lock.mp4", None))
            await entered.wait()
            second = asyncio.create_task(sample._download_remote_video_with_reason("https://example.com/test-cache-lock.mp4", None))
            await asyncio.sleep(0)
            self.assertEqual(worker.await_count, 1)
            self.assertIn(key, sample._REMOTE_DOWNLOAD_LOCKS)
            release.set()
            await asyncio.gather(first, second)
            self.assertEqual(worker.await_count, 2)
        self.assertNotIn(key, sample._REMOTE_DOWNLOAD_LOCKS)

    async def test_director_closes_session_on_success_failure_and_cancel(self):
        for outcome in (" 成功 ", ValueError("失败"), asyncio.CancelledError()):
            with self.subTest(outcome=type(outcome).__name__):
                runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
                runtime.get_text_provider = AsyncMock(return_value=object())
                runtime.call_text_model = AsyncMock(
                    return_value=outcome if isinstance(outcome, str) else None,
                    side_effect=outcome if isinstance(outcome, BaseException) else None,
                )
                runtime.close_text_session = AsyncMock()
                if isinstance(outcome, BaseException):
                    with self.assertRaises(type(outcome)):
                        await runtime._media_director_text_call("画面说明")
                else:
                    self.assertEqual(await runtime._media_director_text_call("画面说明"), "成功")
                session = runtime.call_text_model.call_args.args[2]
                runtime.close_text_session.assert_awaited_once_with(session)

    def test_snapshot_capacity_and_expiry_do_not_freeze_new_data(self):
        runtime = SnapshotPackComponent()
        runtime._INJECTION_SNAPSHOT_CACHE_LIMIT = 2
        cache = {}
        with patch("core.runtime.mirror.pack.time.monotonic", return_value=10.0):
            for key in ("a", "b", "c"):
                runtime._part_snapshot_pack_component._store_injection_snapshot(cache, key, {"outfit": key})
        self.assertEqual(list(cache), ["b", "c"])
        with patch("core.runtime.mirror.pack.time.monotonic", return_value=19.0):
            runtime._part_snapshot_pack_component._store_injection_snapshot(cache, "new", {"outfit": "睡衣"})
            self.assertEqual(list(cache), ["new"])
            self.assertEqual(runtime._cached_injection_snapshot(cache, "new"), {"outfit": "睡衣"})
