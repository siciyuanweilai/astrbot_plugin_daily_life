import datetime
import json
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import support  # noqa: F401
from core.archive import LifeArchive
from core.media.picture.polling import ImageTaskError, ImageTaskFailed
from core.media.video.errors import VideoTaskError, VideoTaskFailed
from core.models import DayRecord
from core.runtime.integration import ExternalIntegrationMixin
from core.runtime.mirror.export import SnapshotExportMixin
from core.runtime.receipt import RuntimeActionReceiptMixin


class _AssetRuntime(RuntimeActionReceiptMixin):
    def __init__(self, archive):
        self.archive = archive
        self.listener = None
        self.calls = 0
        self.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                track_async_tasks=self.track,
                resume_async_image=AsyncMock(
                    return_value=types.SimpleNamespace(path="/tmp/original.png")
                ),
            ),
            video=types.SimpleNamespace(
                track_async_tasks=self.track,
                resume_async_video=AsyncMock(
                    return_value=types.SimpleNamespace(
                        url="https://cdn.example/original.mp4"
                    )
                ),
            ),
        )
        self.stage_durable_media_delivery = AsyncMock()

    @contextmanager
    def track(self, listener):
        self.listener = listener
        try:
            yield
        finally:
            self.listener = None

    async def generate_life_image_asset(self, *args, **kwargs):
        self.calls += 1
        await self.listener(
            "image-original",
            types.SimpleNamespace(
                api_url="https://image.example/v1",
                model="image-test",
                protocol="openai",
                api_key="never-persist-this",
            ),
        )
        raise ImageTaskError("already accepted, poll timed out")

    async def generate_life_video_asset(self, *args, **kwargs):
        self.calls += 1
        await self.listener(
            "video-original",
            {
                "endpoint": "https://video.example/v1/videos",
                "model": "video-test",
                "api_key": "never-persist-this",
            },
        )
        raise VideoTaskError("already accepted, poll timed out")


class ShareLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_assets_resume_original_and_never_send_to_chat(self):
        for kind in ("image", "video"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                archive = LifeArchive(Path(folder) / "life.db")
                try:
                    first = _AssetRuntime(archive)
                    generate = getattr(first, f"generate_share_{kind}_task")
                    result = await generate(
                        None, "preserved scene", task_key="share-owner-a"
                    )
                    self.assertEqual(result["status"], "pending")
                    repeated = await generate(
                        None,
                        "different prompt must not resubmit",
                        task_key="share-owner-a",
                    )
                    self.assertEqual(repeated["status"], "pending")
                    self.assertEqual(first.calls, 1)
                    tasks = await archive.get_durable_tasks(
                        kind=f"share_{kind}_generation"
                    )
                    self.assertEqual(len(tasks), 1)
                    self.assertNotIn(
                        "never-persist-this", json.dumps(tasks[0].as_dict())
                    )
                    fresh = _AssetRuntime(archive)
                    restored = await getattr(fresh, f"resume_share_{kind}_generation")(
                        tasks[0]
                    )
                    self.assertEqual(fresh.calls, 0)
                    self.assertTrue(
                        restored.get("artifact_path") or restored.get("artifact_url")
                    )
                    resume = (
                        fresh.media.image.resume_async_image
                        if kind == "image"
                        else fresh.media.video.resume_async_video
                    )
                    self.assertEqual(resume.await_args.args[0], f"{kind}-original")
                    fresh.stage_durable_media_delivery.assert_not_awaited()
                finally:
                    archive.close()

    async def test_public_activity_is_idempotent_and_not_a_private_exchange(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = LifeArchive(Path(folder) / "life.db")
            try:
                date = datetime.date.today().isoformat()
                await archive.save_day(DayRecord(date=date))

                class Runtime(ExternalIntegrationMixin):
                    async def resolve_injection_target(self, now):
                        return date, False

                runtime = Runtime()
                runtime.archive = archive
                receipt = {
                    "scene": "qzone_post",
                    "event_id": "post-1",
                    "post_id": "owner:post-1",
                    "content": "刚喝完一杯水",
                    "occurred_at": date + "T12:00:00",
                }
                self.assertTrue(await runtime.record_public_activity(receipt))
                self.assertTrue(await runtime.record_public_activity(receipt))
                receipts = await archive.get_durable_tasks(
                    kind="public_activity_receipt"
                )
                self.assertEqual(len(receipts), 1)
                world = await archive.get_continuous_life()
                events = [
                    item
                    for item in world["kernel"]["events"]
                    if item["kind"] == "public_activity"
                ]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["at"], receipt["occurred_at"])
                self.assertNotIn("chat_events", world)
                self.assertNotIn("last_chat_scope", world)
            finally:
                archive.close()

    async def test_public_receipt_without_current_day_waits_for_projection(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = LifeArchive(Path(folder) / "life.db")
            try:

                class Runtime(ExternalIntegrationMixin):
                    async def resolve_injection_target(self, now):
                        return "2026-10-08", False

                runtime = Runtime()
                runtime.archive = archive
                receipt = {
                    "scene": "qzone_reply",
                    "event_id": "reply-1",
                    "content": "谢谢",
                    "occurred_at": "2026-10-08T04:00:00+00:00",
                }
                self.assertFalse(await runtime.record_public_activity(receipt))
                await archive.save_day(DayRecord(date="2026-10-08"))
                self.assertTrue(await runtime.record_public_activity(receipt))
                tasks = await archive.get_durable_tasks(kind="public_activity_receipt")
                self.assertEqual(len(tasks), 1)
                world = await archive.get_continuous_life()
                event = world["kernel"]["events"][-1]
                self.assertEqual(event["at"], "2026-10-08T12:00:00")
            finally:
                archive.close()

    def test_snapshot_prefers_observed_paused_action_and_hides_kernel_payload(self):
        day = DayRecord(
            date="2026-10-08",
            meta={
                "continuous_execution": json.dumps(
                    {
                        "status": "paused",
                        "action": {
                            "action_type": "study",
                            "action_id": "action-1",
                            "target": "练习摄影",
                            "payload": {"private_note": "must-not-export"},
                        },
                        "reason": "private-reason",
                    }
                ),
                "continuous_body": json.dumps(
                    {
                        "energy": 48,
                        "social_battery": 35,
                        "private_note": "must-not-export",
                    }
                ),
            },
        )
        facts = SnapshotExportMixin._share_current_facts(day)
        self.assertEqual(facts["current_action"]["status"], "paused")
        self.assertEqual(facts["body"]["energy"], 48)
        self.assertNotIn("must-not-export", json.dumps(facts))
        self.assertNotIn("private-reason", json.dumps(facts))
        day.meta["residence_context_stale"] = "true"
        self.assertIs(SnapshotExportMixin._share_current_facts(day)["valid"], False)

    async def test_voice_semantics_keep_original_and_validate_style(self):
        class Runtime(ExternalIntegrationMixin):
            config = types.SimpleNamespace(chat_style=None)

            def get_share_chat_style(self, **kwargs):
                return {"enabled": True, "prompt": "自然接话"}

            async def get_text_provider(self, *args):
                return object()

            async def call_text_model(self, *args, **kwargs):
                return "test"

            def _semantic_segment_parse_payload(self, raw):
                return {
                    "text": "cannot replace voice source",
                    "voice_style": "sad",
                    "emotion_category": "sad",
                }

        result = await Runtime().prepare_share_expression(
            "今天有点累", scene="share_voice"
        )
        self.assertEqual(result["text"], "今天有点累")
        self.assertEqual(result["voice_style"], "sad")

    async def test_external_task_terminal_failure_and_temporary_errors_differ(self):
        for kind in ("image", "video"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                archive = LifeArchive(Path(folder) / "life.db")
                try:
                    runtime = _AssetRuntime(archive)
                    await getattr(runtime, f"generate_share_{kind}_task")(
                        None, "original", task_key="owner"
                    )
                    task = (
                        await archive.get_durable_tasks(kind=f"share_{kind}_generation")
                    )[0]
                    resume = getattr(
                        getattr(runtime.media, kind), f"resume_async_{kind}"
                    )
                    pending_error = (
                        ImageTaskError if kind == "image" else VideoTaskError
                    )
                    failed_error = (
                        ImageTaskFailed if kind == "image" else VideoTaskFailed
                    )
                    resume.side_effect = pending_error("network unavailable")
                    retry = await getattr(runtime, f"resume_share_{kind}_generation")(
                        task
                    )
                    self.assertIn("retry_at", retry)
                    self.assertEqual(retry["progress"]["task_id"], f"{kind}-original")
                    resume.side_effect = failed_error("provider explicitly failed")
                    with self.assertRaises(failed_error):
                        await getattr(runtime, f"resume_share_{kind}_generation")(task)
                    self.assertEqual(runtime.calls, 1)
                finally:
                    archive.close()

    async def test_expression_review_keeps_source_on_malformed_or_failed_response(self):
        class Runtime(ExternalIntegrationMixin):
            config = types.SimpleNamespace(chat_style=None)
            payload = None

            def get_share_chat_style(self, **kwargs):
                return {"enabled": True, "prompt": "随口说一句"}

            async def get_text_provider(self, *args):
                return object()

            async def call_text_model(self, *args, **kwargs):
                return "response"

            def _semantic_segment_parse_payload(self, raw):
                return self.payload

        runtime = Runtime()
        for payload in (
            None,
            {},
            {"text": "x" * 81},
            {"text": "", "voice_style": "invalid"},
        ):
            runtime.payload = payload
            result = await runtime.prepare_share_expression(
                "只想歇一会儿", scene="qzone_post"
            )
            self.assertEqual(result["text"], "只想歇一会儿")
            self.assertEqual(result["voice_style"], "neutral")
        runtime.payload = {"text": "歇会儿。"}
        result = await runtime.prepare_share_expression(
            "只想歇一会儿", scene="qzone_post"
        )
        self.assertEqual(result["text"], "歇会儿。")
        runtime.call_text_model = AsyncMock(
            side_effect=RuntimeError("provider unavailable")
        )
        result = await runtime.prepare_share_expression(
            "只想歇一会儿", scene="qzone_post"
        )
        self.assertEqual(result["text"], "只想歇一会儿")


if __name__ == "__main__":
    unittest.main()
