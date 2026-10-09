"""验证前缀稳定、背景不入历史、真实缓存计量和资源释放。"""

import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from runtimehelpers import DailyLifeRuntime, LifeSettings, ProviderRequest
from astrbot.core.agent.message import TextPart
from core.facts import PersonFactContext, build_person_fact_audit_prompt
from core.life.contract import DailyContractMixin
from core.life.locator import DailyLocationGenerationMixin
from core.runtime.mirror.pack import SnapshotPackMixin
from core.telemetry import ModelCacheMetrics, cache_usage
from core.sight import sample


class PrefixTest(unittest.TestCase):
    def setUp(self):
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.config = LifeSettings.from_dict({})

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
            self.assertEqual(req.extra_user_content_parts[-1].text, facts)
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
        self.assertEqual([part.text for part in req.extra_user_content_parts], [other.text, "新生活背景"])
        self.assertEqual(req.system_prompt.count("<daily_life_policy>"), 1)
        self.assertIn("其他插件新增规则", req.system_prompt)

    def test_minimal_request_keeps_current_fact_without_accumulating_old_fact(self):
        req = types.SimpleNamespace(system_prompt="原规则")
        self.runtime._apply_life_request_context(req, "旧背景")
        self.runtime._apply_life_request_context(req, "新背景")
        self.assertIn("新背景", req.system_prompt)
        self.assertNotIn("旧背景", req.system_prompt)

    def test_scene_dependent_internal_rules_have_shared_prefix(self):
        def prefix(prompt, title):
            return prompt.split(f"【{title}】", 1)[0]

        a = build_person_fact_audit_prompt(PersonFactContext(), {}, [], subject="日程")
        b = build_person_fact_audit_prompt(PersonFactContext(), {}, [], subject="穿搭")
        self.assertEqual(prefix(a, "人物事实审计资料"), prefix(b, "人物事实审计资料"))
        contract = DailyContractMixin()
        a = contract._build_repair_prompt("{}", "时间覆盖不足", expected_coverage="full_day", issue_code="timeline_density")
        b = contract._build_repair_prompt("{}", "重复穿搭", issue_code="outfit_repeat")
        self.assertEqual(prefix(a, "日程修复"), prefix(b, "日程修复"))
        self.assertIn("重写 timeline", a)
        self.assertIn("只重写 life_decision.outfit", b)
        a = DailyLocationGenerationMixin._daily_location_request_prompt({"expected_coverage": "full_day"})
        b = DailyLocationGenerationMixin._daily_location_request_prompt({"expected_coverage": "partial"})
        self.assertEqual(prefix(a, "日程地点预选资料"), prefix(b, "日程地点预选资料"))
        self.assertNotEqual(a, b)


class UsageTest(unittest.TestCase):
    def test_vendor_usage_formats(self):
        cases = [
            ({"usage": {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 768}}, 1000, 768),
            ({"usage": {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 0}}}, 1000, 0),
            ({"usage": {"input_tokens": 30, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 100}}, 430, 300),
            ({"usage": {"prompt_tokens": 430, "cache_read_input_tokens": 300}}, 430, 300),
            ({"usage_metadata": {"prompt_token_count": 1000, "cached_content_token_count": 500}}, 1000, 500),
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
        runtime = SnapshotPackMixin()
        runtime._INJECTION_SNAPSHOT_CACHE_LIMIT = 2
        cache = {}
        with patch("core.runtime.mirror.pack.time.monotonic", return_value=10.0):
            for key in ("a", "b", "c"):
                runtime._store_injection_snapshot(cache, key, {"outfit": key})
        self.assertEqual(list(cache), ["b", "c"])
        with patch("core.runtime.mirror.pack.time.monotonic", return_value=19.0):
            runtime._store_injection_snapshot(cache, "new", {"outfit": "睡衣"})
            self.assertEqual(list(cache), ["new"])
            self.assertEqual(runtime._cached_injection_snapshot(cache, "new"), {"outfit": "睡衣"})
