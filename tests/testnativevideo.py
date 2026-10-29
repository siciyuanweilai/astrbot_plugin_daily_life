"""完整视频协议、资源边界及后台集成；模拟 HTTP 传输核对提交字节。"""

import asyncio
import base64
import copy
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import DailyLifeRuntime, Event, LifeArchive, LifeSettings
from core.sight.clip import SightClip
from core.sight.native import (
    INLINE_VIDEO_BYTES,
    NATIVE_VIDEO_PROMPT,
    NativeVideoClient,
    NativeVideoError,
    NativeVideoRoute,
    parse_video_response,
    video_identity,
)
from core.sight.sample import (
    _download_remote_video_with_reason,
    prepare_sample_video_source,
    source_fingerprint,
)


VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 500


def analysis(**changes):
    return {
        "video_received": True,
        "summary": "先出现红色圆，再出现绿色三角。",
        "answer": "先圆后是三角。",
        "timeline": [
            {"time": "00:00", "visual": "红底白圆", "audio": "说了一句测试语音"},
            {"time": "00:03", "visual": "绿底白三角", "audio": "无声"},
        ],
        "transcript": "测试语音",
        "details": ["可见测试验证码"],
        "limitations": [],
        **changes,
    }


def envelope(result=None, *, finish="STOP", thought=False, wrap=False):
    payload = {
        "candidates": [
            {
                "finishReason": finish,
                "content": {
                    "parts": [
                        {"text": "不应使用的内部思考", "thought": True},
                        {
                            "text": json.dumps(
                                result if result is not None else analysis(),
                                ensure_ascii=False,
                            ),
                            "thought": thought,
                        },
                    ]
                },
            }
        ],
        "usageMetadata": {"promptTokenCount": 123, "cachedContentTokenCount": 0},
    }
    return {"response": payload} if wrap else payload


def provider(
    base="https://video.example/v1", key="secret-test", model="gemini-3.8-flash"
):
    return types.SimpleNamespace(
        provider_config={"api_base": base, "model": model},
        get_current_key=lambda: key,
        get_model=lambda: model,
    )


class NativeVideoFormatTest(unittest.TestCase):
    def test_route_uses_native_endpoint_for_openai_config_and_hides_secret(self):
        for base in (
            "https://video.example",
            "https://video.example/v1",
            "https://video.example/v1beta",
            "https://video.example/v1beta/models",
        ):
            with self.subTest(base=base):
                route = NativeVideoRoute.from_provider(provider(base))
                self.assertEqual(
                    route.url,
                    "https://video.example/v1beta/models/gemini-3.8-flash:generateContent",
                )
                self.assertNotIn("secret-test", repr(route))
        self.assertIsNone(NativeVideoRoute.from_provider(provider(model="other-model")))
        for base, key in (
            ("", "key"),
            ("https://video.example?key=private", "key"),
            ("https://video.example", ""),
        ):
            with self.assertRaises(NativeVideoError):
                NativeVideoRoute.from_provider(provider(base, key))

    def test_thinking_budget_applies_only_to_verified_model(self):
        self.assertEqual(
            NativeVideoRoute.from_provider(
                provider(model="gemini-2.5-flash")
            ).generation_config()["thinkingConfig"],
            {"thinkingBudget": 0},
        )
        self.assertNotIn(
            "thinkingConfig",
            NativeVideoRoute.from_provider(provider()).generation_config(),
        )

    def test_response_wrapper_ignores_thought_and_checks_semantic_receipt(self):
        result, usage = parse_video_response(envelope(wrap=True))
        self.assertEqual(result, analysis())
        self.assertEqual(usage["promptTokenCount"], 123)
        for payload in (
            envelope(thought=True),
            envelope(finish="MAX_TOKENS"),
            envelope(finish="SAFETY"),
            envelope(analysis(video_received=False)),
            envelope(analysis(video_received="true")),
            envelope(analysis(summary="")),
            envelope(analysis(timeline=[])),
            envelope(analysis(details=None)),
            {"error": {"message": "secret"}},
            {"candidates": []},
        ):
            with (
                self.subTest(payload=payload),
                self.assertRaises(NativeVideoError) as error,
            ):
                parse_video_response(payload)
            self.assertNotIn("secret", str(error.exception))


class NativeVideoHttpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "video.mp4"
        self.path.write_bytes(VIDEO)
        self.requests = []
        self.status = 200
        self.response = envelope()
        self.waiter = None
        self.entered = asyncio.Event()
        self.active = 0
        self.peak = 0
        outer = self

        class Response:
            async def __aenter__(self):
                self.status = outer.status
                self.content = self
                outer.active += 1
                outer.peak = max(outer.peak, outer.active)
                outer.entered.set()
                return self

            async def __aexit__(self, *args):
                outer.active -= 1

            async def iter_chunked(self, size):
                if outer.waiter:
                    await asyncio.wait_for(outer.waiter.wait(), self.timeout)
                raw = (
                    outer.response
                    if isinstance(outer.response, str)
                    else json.dumps(outer.response)
                )
                yield raw.encode()

        class Session:
            closed = False

            def post(self, url, **kwargs):
                headers = kwargs["headers"]
                outer.requests.append((headers, json.loads(kwargs["data"])))
                outer.last_url = url
                result = Response()
                result.timeout = kwargs["timeout"].kwargs["total"]
                return result

            async def close(self):
                self.closed = True

        self.session_patch = patch("core.sight.native.aiohttp.ClientSession", Session)
        self.session_patch.start()
        self.route = NativeVideoRoute.from_provider(
            provider("https://video.example/v1")
        )
        self.client = NativeVideoClient()
        self.fingerprint = video_identity(self.path)[0]

    async def asyncTearDown(self):
        if self.waiter:
            self.waiter.set()
        await self.client.close()
        self.session_patch.stop()
        self.temp.cleanup()

    async def call(self, *, key="", timeout=5):
        return await self.client.analyze(
            self.path,
            self.route,
            NATIVE_VIDEO_PROMPT,
            self.fingerprint,
            timeout=timeout,
            cache_key=key,
        )

    async def test_actual_http_body_contains_whole_video_and_single_auth_header(self):
        # 满额文件连同编码膨胀及长问题应能实际进入 HTTP 提交。
        with self.path.open("r+b") as handle:
            handle.truncate(15 * 1024 * 1024)
        self.fingerprint = video_identity(self.path)[0]
        prompt = NATIVE_VIDEO_PROMPT + "\n用户问题：" + "视频" * 4000
        result, _ = await self.client.analyze(
            self.path, self.route, prompt, self.fingerprint, timeout=5
        )
        self.assertEqual(result["summary"], analysis()["summary"])
        headers, body = self.requests[0]
        self.assertEqual(headers["x-goog-api-key"], "secret-test")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(len(headers["X-Request-ID"]), 32)
        parts = body["contents"][0]["parts"]
        self.assertEqual(parts[0]["inlineData"]["mimeType"], "video/mp4")
        self.assertEqual(
            base64.b64decode(parts[0]["inlineData"]["data"]), self.path.read_bytes()
        )
        self.assertEqual(parts[1]["text"], prompt)
        self.assertNotIn("messages", body)
        self.assertNotIn("responseModalities", body["generationConfig"])
        self.assertTrue(self.path.exists())

    async def test_webm_mime_comes_from_bytes_instead_of_download_suffix(self):
        self.path.write_bytes(b"\x1aE\xdf\xa3\x42\x82\x84webm" + b"\x00" * 50)
        self.fingerprint, mime, _ = video_identity(self.path)
        self.assertEqual(mime, "video/webm")
        await self.call()
        self.assertEqual(
            self.requests[0][1]["contents"][0]["parts"][0]["inlineData"]["mimeType"],
            "video/webm",
        )

    async def test_oversized_invalid_or_changed_file_is_rejected_before_network(self):
        for content in (b"<html>bad file</html>", b"", VIDEO + b"changed"):
            self.path.write_bytes(content)
            with self.assertRaises(NativeVideoError):
                await self.call()
        with self.path.open("wb") as handle:
            handle.truncate(INLINE_VIDEO_BYTES + 1)
        with self.assertRaisesRegex(NativeVideoError, "15 MiB"):
            await self.call()
        self.assertEqual(self.requests, [])

    async def test_errors_redact_payload_and_never_retry(self):
        for status, response in (
            (400, {"error": {"message": "secret-test BASE64 PRIVATE"}}),
            (502, "secret-test"),
            (200, "not-json-secret-test"),
            (200, envelope(finish="MAX_TOKENS")),
        ):
            self.status, self.response = status, response
            before = len(self.requests)
            with self.assertRaises(NativeVideoError) as error:
                await self.call()
            self.assertNotIn("secret-test", str(error.exception))
            self.assertEqual(len(self.requests), before + 1)

    async def test_single_flight_single_concurrency_and_responsive_event_loop(self):
        self.waiter = asyncio.Event()
        first = asyncio.create_task(self.call(key="scope-one"))
        await self.entered.wait()
        duplicate = asyncio.create_task(self.call(key="scope-one"))
        other = asyncio.create_task(self.call(key="scope-two"))
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(first.done())
        self.waiter.set()
        await asyncio.gather(first, duplicate, other)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.peak, 1)

    async def test_timeout_and_close_release_session_without_retry(self):
        self.waiter = asyncio.Event()
        with self.assertRaisesRegex(NativeVideoError, "超时"):
            await self.call(timeout=0.03)
        self.assertEqual(len(self.requests), 1)
        task = asyncio.create_task(self.call(key="pending"))
        await asyncio.sleep(0.02)
        await self.client.close()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.client._session.closed)


class NativeVideoRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "clip.mp4"
        self.path.write_bytes(VIDEO)
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.data_path = Path(self.temp.name) / "life.db"
        self.runtime.archive = LifeArchive(self.runtime.data_path)
        self.runtime.config = LifeSettings.from_dict(
            {"sight_config": {"auto_video_understanding": True}}
        )
        self.runtime.composer = types.SimpleNamespace(
            _get_provider=AsyncMock(return_value=provider())
        )
        self.runtime._native_video_client = types.SimpleNamespace(
            analyze=AsyncMock(return_value=(analysis(), {}))
        )
        self.event = Event(
            unified_msg_origin="test:FriendMessage:1", message_id="video-1"
        )
        self.event.message_str = "先后出现什么图形？"
        self.event.message_items = [{"type": "video", "file": str(self.path)}]
        self.event.message_obj.message = self.event.message_items
        self.clip = SightClip(
            scope=self.event.unified_msg_origin,
            message_id="video-1",
            source=str(self.path),
            text=self.event.message_str,
        )

    async def asyncTearDown(self):
        flight = getattr(self.runtime, "_sight_flight", None)
        if flight:
            await flight.close()
        await self.runtime.archive.aclose()
        self.temp.cleanup()

    async def test_native_path_skips_frame_and_asr_then_reuses_content_cache(self):
        with (
            patch(
                "core.sight.bridge.extract_video_frames",
                side_effect=AssertionError("不应抽帧"),
            ),
            patch(
                "core.sight.cinematics.prepare_sample_video_source",
                wraps=prepare_sample_video_source,
            ) as prepare,
        ):
            self.runtime._prepare_sight_clip_material = AsyncMock(
                side_effect=AssertionError("不应走 ASR")
            )
            result = await self.runtime._understand_sight_clip(self.event, self.clip)
            again = await self.runtime._understand_sight_clip(self.event, self.clip)
        prepare.assert_awaited_once()
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.metadata["video_input_mode"], "gemini_native")
        self.assertTrue(again.metadata["sight_metrics"]["cache_hit"])
        self.assertEqual(result.transcript, "测试语音")
        self.runtime._native_video_client.analyze.assert_awaited_once()
        self.assertNotIn(
            "secret-test", json.dumps(result.as_dict(), ensure_ascii=False)
        )
        self.assertIn(
            self.event.message_str,
            self.runtime._native_video_client.analyze.call_args.args[2],
        )

    async def test_completed_cache_binds_current_message_without_preparing_video_again(self):
        later = copy.deepcopy(self.clip)
        later.message_id = "video-later"
        with patch(
            "core.sight.cinematics.prepare_sample_video_source",
            wraps=prepare_sample_video_source,
        ) as prepare:
            first = await self.runtime._understand_sight_clip(self.event, self.clip)
            again = await self.runtime._understand_sight_clip(self.event, later)
        prepare.assert_awaited_once()
        self.runtime._native_video_client.analyze.assert_awaited_once()
        self.assertTrue(again.metadata["sight_metrics"]["cache_hit"])
        self.assertEqual(again.clip.message_id, "video-later")
        self.assertEqual(first.clip.message_id, "video-1")

    async def test_force_reanalyzes_even_with_completed_cache(self):
        await self.runtime._understand_sight_clip(self.event, self.clip)
        result = await self.runtime._understand_sight_clip(
            self.event, self.clip, force=True
        )
        self.assertEqual(result.status, "ready")
        self.assertFalse(result.metadata["sight_metrics"].get("cache_hit", False))
        self.assertEqual(self.runtime._native_video_client.analyze.await_count, 2)

    async def test_completed_index_cannot_reuse_removed_result(self):
        await self.runtime._understand_sight_clip(self.event, self.clip)
        removed = await self.runtime._sight_vault_for_runtime().remove_message(
            self.clip.scope, self.clip.message_id
        )
        self.assertEqual(removed, 1)
        result = await self.runtime._understand_sight_clip(self.event, self.clip)
        self.assertEqual(result.status, "ready")
        self.assertEqual(self.runtime._native_video_client.analyze.await_count, 2)

    async def test_completed_index_cannot_extend_result_ttl(self):
        await self.runtime._understand_sight_clip(self.event, self.clip)
        expired_at = time.time() + self.runtime._sight_cache_ttl_seconds() + 1
        with patch("core.sight.vault.time.time", return_value=expired_at):
            result = await self.runtime._understand_sight_clip(self.event, self.clip)
        self.assertEqual(result.status, "ready")
        self.assertEqual(self.runtime._native_video_client.analyze.await_count, 2)

    async def test_recalled_event_cannot_reuse_completed_result(self):
        await self.runtime._understand_sight_clip(self.event, self.clip)
        self.runtime.event_was_recalled = lambda *args, **kwargs: True
        with patch(
            "core.sight.cinematics.prepare_sample_video_source",
            side_effect=AssertionError("撤回后不应准备视频"),
        ):
            result = await self.runtime._understand_sight_clip(self.event, self.clip)
        self.assertEqual(result.status, "failed")
        self.assertIn("撤回", result.error)
        self.runtime._native_video_client.analyze.assert_awaited_once()

    async def test_concurrent_requests_share_analysis_and_keep_own_message_id(self):
        later = copy.deepcopy(self.clip)
        later.message_id = "video-later"

        async def delayed(*args, **kwargs):
            await asyncio.sleep(0.02)
            return analysis(), {}

        self.runtime._native_video_client.analyze.side_effect = delayed
        first, second = await asyncio.gather(
            self.runtime._understand_sight_clip(self.event, self.clip),
            self.runtime._understand_sight_clip(self.event, later),
        )
        self.runtime._native_video_client.analyze.assert_awaited_once()
        self.assertEqual(first.clip.message_id, "video-1")
        self.assertEqual(second.clip.message_id, "video-later")

    async def test_share_wait_timeout_keeps_video_task_and_persists_result_for_followup(
        self,
    ):
        from core.sight.sharemedia import describe_share_video

        release = asyncio.Event()

        async def delayed(*args, **kwargs):
            await release.wait()
            return analysis(), {}

        self.runtime._native_video_client.analyze.side_effect = delayed
        source = "https://example.com/shared.mp4"
        with (
            patch(
                "core.sight.sharemedia.is_http_url_allowed_async",
                AsyncMock(return_value=True),
            ),
            patch(
                "core.sight.cinematics.prepare_sample_video_source",
                AsyncMock(return_value=self.path),
            ),
        ):
            with patch("core.sight.sharemedia.SHARE_VIDEO_WAIT_SECONDS", 0.01):
                first = await describe_share_video(self.runtime, source)
                second = await describe_share_video(self.runtime, source)
            self.assertEqual(first["status"], "pending")
            self.assertEqual(second["status"], "pending")
            self.runtime._native_video_client.analyze.assert_awaited_once()
            release.set()
            ready = await describe_share_video(self.runtime, source)
            again = await describe_share_video(self.runtime, source)
        self.assertEqual(ready["status"], "ok")
        self.assertEqual(again, ready)
        self.assertIn("红底白圆", ready["details"])
        self.assertIn("测试语音", ready["details"])
        self.runtime._native_video_client.analyze.assert_awaited_once()
        stored = await self.runtime._sight_vault_for_runtime().recent("qzone_broadcast")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].status, "ready")

    async def test_share_request_failure_is_persisted_without_repeated_submit(self):
        from core.sight.sharemedia import describe_share_video

        self.runtime._native_video_client.analyze.side_effect = NativeVideoError(
            "请求超时"
        )
        with (
            patch(
                "core.sight.sharemedia.is_http_url_allowed_async",
                AsyncMock(return_value=True),
            ),
            patch(
                "core.sight.cinematics.prepare_sample_video_source",
                AsyncMock(return_value=self.path),
            ),
        ):
            first = await describe_share_video(
                self.runtime, "https://example.com/shared.mp4"
            )
            again = await describe_share_video(
                self.runtime, "https://example.com/shared.mp4"
            )
        self.assertEqual(first["status"], "unavailable")
        self.assertEqual(again, first)
        self.runtime._native_video_client.analyze.assert_awaited_once()

    async def test_same_video_referenced_another_way_reuses_native_result(self):
        await self.runtime._understand_sight_clip(self.event, self.clip)
        quote = copy.deepcopy(self.clip)
        quote.origin = "quote"
        quote.message_id = "quoted-later"
        again = await self.runtime._understand_sight_clip(self.event, quote)
        self.assertTrue(again.metadata["sight_metrics"]["cache_hit"])
        self.assertEqual(again.clip.message_id, "quoted-later")
        self.runtime._native_video_client.analyze.assert_awaited_once()

    async def test_cache_isolates_question_key_model_scope_and_file_content(self):
        await self.runtime._understand_sight_clip(self.event, self.clip)
        changed = copy.deepcopy(self.clip)
        changed.text = "声音说了什么？"
        await self.runtime._understand_sight_clip(self.event, changed)
        changed.scope = "test:FriendMessage:2"
        await self.runtime._understand_sight_clip(self.event, changed)
        for p in (
            provider(key="changed-key"),
            provider(model="gemini-3.1-pro-preview"),
        ):
            self.runtime.composer._get_provider.return_value = p
            await self.runtime._understand_sight_clip(self.event, changed)
        self.path.write_bytes(VIDEO + b"other-content")
        await self.runtime._understand_sight_clip(self.event, changed)
        self.assertEqual(self.runtime._native_video_client.analyze.await_count, 6)

    async def test_same_event_failure_is_not_repeatedly_submitted(self):
        self.runtime._native_video_client.analyze.side_effect = NativeVideoError(
            "接口失败"
        )
        result = await self.runtime._understand_sight_clip(self.event, self.clip)
        again = await self.runtime._understand_sight_clip(self.event, self.clip)
        self.assertEqual(result.status, "failed")
        self.assertEqual(again.status, "failed")
        self.runtime._native_video_client.analyze.assert_awaited_once()

    async def test_bili_source_keeps_existing_long_video_pipeline(self):
        clip = SightClip(source="https://www.bilibili.com/video/test")
        self.assertIsNone(await self.runtime._native_sight_route(clip))
        self.runtime.composer._get_provider.assert_not_called()

    async def test_tool_returns_pending_and_locks_original_video_before_background_work(
        self,
    ):
        scheduled = []

        def submit(snapshot, original, work, **kwargs):
            scheduled.append((snapshot, work, kwargs))
            return json.dumps({"status": "pending"})

        self.runtime._submit_background_tool = submit
        result = await self.runtime.life_video_understand(self.event)
        self.assertEqual(json.loads(result)["status"], "pending")
        self.runtime._native_video_client.analyze.assert_not_called()
        self.event.message_str = "另一条消息"
        self.path.unlink()
        answer = await scheduled[0][1]()
        self.assertEqual(answer.status, "ok")
        prompt = self.runtime._native_video_client.analyze.call_args.args[2]
        self.assertIn("先后出现什么图形", prompt)
        self.assertNotIn("另一条消息", prompt)
        self.assertEqual(scheduled[0][2]["category"], "video")
        self.assertNotIn("后续回答规则", answer)

    async def test_recent_video_is_fixed_before_background_job_starts(self):
        await self.runtime._understand_sight_clip(self.event, self.clip)
        self.event.message_items = []
        self.event.message_obj.message = []
        self.event.message_str = "刚才那段视频声音是什么？"
        scheduled = []
        self.runtime._submit_background_tool = (
            lambda snapshot, original, work, **kwargs: (
                scheduled.append(work) or "pending"
            )
        )
        await self.runtime.life_video_understand(self.event)
        self.runtime._sight_recent_for_event = AsyncMock(
            side_effect=AssertionError("后台不能改取后续视频")
        )
        answer = await scheduled[0]()
        self.assertEqual(answer.status, "ok")
        self.assertIn(
            "刚才那段视频声音是什么",
            self.runtime._native_video_client.analyze.call_args.args[2],
        )

    async def test_remote_video_uses_bounded_downloader_not_platform_converter(self):
        component = types.SimpleNamespace(
            type="video",
            file="https://video.example/file.mp4",
            convert_to_file_path=AsyncMock(),
        )
        clip = await self.runtime._sight_video_clip_from_component(
            component, scope=self.clip.scope, message_id="remote", origin="current"
        )
        self.assertEqual(clip.source, component.file)
        component.convert_to_file_path.assert_not_called()

    async def test_download_cache_cannot_bypass_smaller_size_limit(self):
        url = "https://video.example/large.mp4"
        cache = Path(self.temp.name) / "cache"
        (cache / "media").mkdir(parents=True)
        target = cache / "media" / f"{source_fingerprint(url)}.mp4"
        target.write_bytes(VIDEO)
        path, reason = await _download_remote_video_with_reason(
            url, cache, max_bytes=100
        )
        self.assertIsNone(path)
        self.assertIn("超过大小上限", reason)
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
