"""体检发现问题的回归场景，隔离数据库与外部服务。"""

import asyncio
import collections
import datetime
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from runtimehelpers import (
    Context,
    DailyLifeRuntime,
    Event,
    LifeArchive,
    LifeSettings,
    Provider,
    SegmentPart,
    SemanticSegmentPlan,
    DayRecord,
    TimelineItem,
)
from core.life.future import future_outfit_timing_issue
from core.life.planner import LifeBackgroundComposer
from core.runtime.spine.boot import RuntimeServices
from core.media import GeminiImageService
from core.media.picture import polling
from testimagetasks import _Session, _Response, _route, _request, _accepted


class _Rhythm:
    def __init__(self, running=False):
        self.scheduler = types.SimpleNamespace(running=running)

    def start(self):
        self.scheduler.running = True

    def stop(self):
        self.scheduler.running = False


def _services(schedule, running=False):
    return RuntimeServices(
        config=LifeSettings.from_dict({"rhythm_config": {"schedule_time": schedule}}),
        media=types.SimpleNamespace(closed=False),
        rhythm=_Rhythm(running),
        memos=None,
        contact_resolver=None,
        weather_client=None,
        search=None,
        domains=None,
        composer=None,
        model_gateway=None,
    )


class AuditRepairsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.directory.name) / "regression.db")
        self.runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        self.runtime.archive = self.archive
        self.runtime.config = LifeSettings.from_dict({})
        for name in (
            "reconcile_commitment_photo_tasks",
            "reconcile_commitment_video_tasks",
            "reconcile_scheduled_invite_contacts",
        ):
            setattr(self.runtime, name, AsyncMock())

    async def asyncTearDown(self):
        await self.runtime._close_injection_snapshot_flight()
        self.archive.close()
        self.directory.cleanup()

    async def test_same_group_id_on_different_platforms_has_separate_cached_summaries(
        self,
    ):
        self.runtime._settle_stale_reply_effects = AsyncMock()
        events = []
        for platform in ("a", "b"):
            scope = f"{platform}:GroupMessage:777"
            await self.archive.upsert_session_mid_summary(
                {"session_id": scope, "summary": platform}
            )
            events.append(Event(unified_msg_origin=scope, group_id="777"))
        snapshots = await asyncio.gather(
            *(self.runtime._gather_life_context_snapshot(event) for event in events)
        )
        self.assertEqual(
            [item["mid_summaries"][0].summary for item in snapshots], ["a", "b"]
        )
        cached = await self.runtime._gather_life_context_snapshot(events[1])
        self.assertEqual(cached["mid_summaries"][0].summary, "b")

    def _configure_swap(self):
        runtime = self.runtime
        old, new = _services("07:00", True), _services("09:30")
        runtime._install_runtime_services(old)
        runtime.raw_config = {"rhythm_config": {"schedule_time": "07:00"}}
        runtime.generation_lock = asyncio.Lock()
        runtime._prune_disabled_proactive_candidates = lambda: None
        runtime._build_runtime_services = lambda *_: new

        async def close(services):
            services.media.closed = True

        runtime._close_runtime_services = close
        return old, new

    async def test_voice_reconfigure_failure_rolls_back_live_services_and_scheduler(
        self,
    ):
        old, new = self._configure_swap()
        self.runtime.voice_call = types.SimpleNamespace(
            reconfigure=AsyncMock(side_effect=[OSError("端口占用"), None])
        )
        with self.assertRaises(OSError):
            await self.runtime.apply_config(
                {"rhythm_config": {"schedule_time": "09:30"}}
            )
        self.assertIs(self.runtime.media, old.media)
        self.assertEqual(self.runtime.config.schedule_time, "07:00")
        self.assertEqual(
            self.runtime.raw_config["rhythm_config"]["schedule_time"], "07:00"
        )
        self.assertTrue(old.rhythm.scheduler.running)
        self.assertFalse(old.media.closed)
        self.assertTrue(new.media.closed)
        self.assertEqual(self.runtime.voice_call.reconfigure.await_count, 2)

    async def test_active_media_keeps_old_service_while_new_chat_uses_new_service(self):
        old, new = self._configure_swap()
        started, release = asyncio.Event(), asyncio.Event()

        async def media_work():
            async with self.runtime.runtime_service_lease():
                started.set()
                await release.wait()
                self.assertIs(self.runtime.media, old.media)
                self.assertFalse(old.media.closed)

        task = asyncio.create_task(media_work())
        await started.wait()
        try:
            await asyncio.wait_for(
                self.runtime.apply_config(
                    {"rhythm_config": {"schedule_time": "09:30"}}
                ),
                1,
            )
            async with self.runtime.runtime_service_lease():
                self.assertIs(self.runtime.media, new.media)
            self.assertFalse(old.media.closed)
        finally:
            release.set()
            await task
        self.assertTrue(old.media.closed)
        self.assertFalse(new.media.closed)

    async def test_durable_worker_claims_one_task_at_a_time_and_rejects_overlapping_run(
        self,
    ):
        first = await self.archive.enqueue_durable_task("first", "commitment_video", {})
        second = await self.archive.enqueue_durable_task(
            "second", "commitment_video", {}
        )
        started, release = asyncio.Event(), asyncio.Event()
        calls, owners = collections.Counter(), []

        async def handler(task):
            calls[task.id] += 1
            owners.append(task.lease_owner)
            if task.id == first.id:
                started.set()
                await release.wait()
            return {"ok": True}

        self.runtime._durable_runtime_handlers = {"commitment_video": handler}
        run = asyncio.create_task(self.runtime._run_durable_tasks_once())
        await started.wait()
        try:
            self.assertEqual(
                (await self.archive.get_durable_task("second")).status, "pending"
            )
            self.assertEqual(await self.runtime._run_durable_tasks_once(), 0)
        finally:
            release.set()
            self.assertEqual(await run, 2)
        self.assertEqual(calls, {first.id: 1, second.id: 1})
        self.assertEqual(len(set(owners)), 2)

    async def test_lease_renewal_rejects_expired_and_replaced_owner(self):
        clock = ["2026-10-05 12:00:00"]
        self.archive._cognition_now = lambda: clock[0]
        await self.archive.enqueue_durable_task("renew", "media_delivery", {})
        task = (await self.archive.lease_durable_tasks("attempt-a", lease_seconds=60))[
            0
        ]
        clock[0] = "2026-10-05 12:00:30"
        self.assertTrue(
            await self.archive.renew_durable_task_lease(
                task.id, "attempt-a", lease_seconds=60
            )
        )
        clock[0] = "2026-10-05 12:01:31"
        self.assertFalse(
            await self.archive.renew_durable_task_lease(task.id, "attempt-a")
        )
        replacement = (await self.archive.lease_durable_tasks("attempt-b"))[0]
        self.assertFalse(
            await self.archive.complete_durable_task(task.id, {}, owner="attempt-a")
        )
        self.assertTrue(
            await self.archive.complete_durable_task(
                replacement.id, {}, owner="attempt-b"
            )
        )

    async def test_recovered_media_settles_original_day_and_action_only(self):
        yesterday = DayRecord(date="2026-10-04")
        self.runtime.archive = types.SimpleNamespace(
            get_day=AsyncMock(return_value=yesterday)
        )
        recorder = AsyncMock(return_value=object())
        matcher = AsyncMock()
        self.runtime.composer = types.SimpleNamespace(
            record_life_action_receipt=recorder,
            record_matching_life_action_receipt=matcher,
        )
        self.runtime.resolve_injection_target = AsyncMock(
            return_value=("2026-10-05", False)
        )
        self.runtime.mark_page_status_changed = AsyncMock()
        self.runtime.context = object()
        self.runtime.video_message_chain = lambda *_: object()
        task = types.SimpleNamespace(
            payload={
                "scope": "platform:FriendMessage:1",
                "media_kind": "video",
                "artifacts": ["https://example.test/video.mp4"],
                "action_type": "video",
                "action_id": "yesterday-video",
                "action_date": yesterday.date,
            }
        )
        with patch(
            "core.runtime.receipt.send_message_to_scope",
            new=AsyncMock(return_value=True),
        ):
            await self.runtime.resume_durable_media_delivery(task)
            task.payload.pop("action_id")
            await self.runtime.resume_durable_media_delivery(task)
        self.runtime.archive.get_day.assert_awaited_once_with(yesterday.date)
        self.assertEqual(recorder.call_args.args[1], "yesterday-video")
        matcher.assert_not_called()
        self.runtime.resolve_injection_target.assert_not_called()

    async def test_expired_tts_does_not_rebuild_reply_or_write_sent_history(self):
        runtime = self.runtime
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:audit", sender_id="audit"
        )
        event.message_str = "你好"
        event.set_result(
            types.SimpleNamespace(chain=[types.SimpleNamespace(text="我在呢")])
        )
        runtime._init_continuous_turn_state()
        setattr(event, runtime._CONTINUOUS_TURN_SCOPE_ATTR, event.unified_msg_origin)
        setattr(event, runtime._CONTINUOUS_TURN_PARTICIPANT_ATTR, "audit")
        setattr(event, runtime._CONTINUOUS_TURN_REVISION_ATTR, 1)
        runtime._continuous_turn_revisions[event.unified_msg_origin] = {"audit": 1}
        setattr(
            event,
            runtime._SEMANTIC_SEGMENT_PLAN_ATTR,
            SemanticSegmentPlan(
                (SegmentPart("我在呢"),), channel="voice", confidence=0.9
            ),
        )
        runtime.mark_voice_switch_available(event)
        runtime._append_turn_history = AsyncMock()
        runtime._note_voice_expression_decision = AsyncMock()

        async def synthesize(*args, **kwargs):
            runtime._continuous_turn_revisions[event.unified_msg_origin]["audit"] = 2
            runtime.stop_stale_continuous_turn_event(event)
            return types.SimpleNamespace(path=Path("voice.mp3"))

        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(synthesize=synthesize)
        )
        self.assertFalse(await runtime.apply_voice_switch_before_send(event))
        self.assertIsNone(event.get_result())
        runtime._append_turn_history.assert_not_called()
        runtime._note_voice_expression_decision.assert_not_called()

    async def test_user_can_change_now_into_clothes_also_mentioned_in_future_activity(
        self,
    ):
        composer = LifeBackgroundComposer.__new__(LifeBackgroundComposer)
        composer.archive = self.archive
        composer._save_life_decision_record = AsyncMock()
        composer._style_catalog_reference_appearance = AsyncMock(return_value={})
        outfit = "白色棉质居家睡衣配柔软拖鞋"
        day = DayRecord(
            date="2026-10-05",
            outfit="外出运动服",
            timeline=[TimelineItem(time="23:00", activity="穿着" + outfit + "看电影")],
        )
        result = await composer._apply_outfit_update_result(
            {
                "outfit_decision": "sleepwear",
                "outfit": outfit,
                "scene_category": "home",
            },
            date_str=day.date,
            target_period="night",
            current_time=datetime.datetime(2026, 10, 5, 22),
            context={
                "old_data": day,
                "old_meta": {},
                "timeline_date": day.date,
                "instruction": "现在换上" + outfit,
                "instruction_source": "user",
                "current_timeline": "在家休息",
                "next_timeline": "看电影",
                "weather": "晴",
            },
        )
        self.assertIsNotNone(result)
        self.assertEqual(day.outfit, outfit)
        self.assertTrue(
            future_outfit_timing_issue(
                outfit,
                day.timeline,
                current_minutes=22 * 60,
                source_timeline_time="23:00",
            )
        )

    async def test_accepted_image_is_persisted_before_timeout_then_recovered_without_post(
        self,
    ):
        service = GeminiImageService(
            self.runtime.config.image_generation, Path(self.directory.name)
        )
        route = _route(timeout_seconds=0.01)
        session = _Session([_accepted()])
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:audit", sender_id="audit"
        )
        event.message_str = "拍张照片"
        self.runtime.media = types.SimpleNamespace(image=service)
        with self.assertRaises(polling.ImageTaskError):
            await self.runtime.track_image_generation(
                event,
                polling.request_image_task(
                    session,
                    route,
                    _request(route),
                    on_accepted=lambda task_id, selected: service._task_listener.get()(
                        task_id, selected
                    ),
                ),
            )
        records = await self.archive.get_durable_tasks(kind="image_generation")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "pending")
        self.assertEqual(records[0].payload["task_id"], "imgtask_test")
        self.assertNotIn("test-key", json.dumps(records[0].payload))
        self.archive.close()
        self.archive = LifeArchive(Path(self.directory.name) / "regression.db")
        self.runtime.archive = self.archive
        await self.archive.reschedule_durable_task(
            records[0].task_key, self.archive._cognition_now()
        )
        task = (await self.archive.lease_durable_tasks("recovery"))[0]
        recovered_session = _Session(
            [],
            [
                _Response(
                    payload={
                        "status": "completed",
                        "result": {"data": [{"url": "https://example.test/image.png"}]},
                    }
                )
            ],
        )
        path = Path(self.directory.name) / "recovered.png"
        path.write_bytes(b"image")

        async def recover(task_id, reference):
            await polling.request_image_task(
                recovered_session, _route(), _request(_route()), resume_task_id=task_id
            )
            return types.SimpleNamespace(path=path)

        service.resume_async_image = recover
        with patch.object(polling.asyncio, "sleep", new_callable=AsyncMock):
            result = await self.runtime.resume_durable_image_generation(task)
        self.assertEqual(result["artifact_path"], str(path))
        self.assertEqual([item[0] for item in session.calls], ["POST"])
        self.assertEqual([item[0] for item in recovered_session.calls], ["GET"])
        deliveries = await self.archive.get_durable_tasks(kind="media_delivery")
        self.assertEqual(deliveries[0].status, "pending")
        self.assertEqual(deliveries[0].payload["scope"], event.unified_msg_origin)

    async def test_deferred_image_query_does_not_starve_other_due_tasks(self):
        await self.archive.enqueue_durable_task(
            "image:waiting", "image_generation", {}, priority=85
        )
        await self.archive.enqueue_durable_task(
            "daily:ready", "daily_refresh", {}, priority=80
        )
        retry_at = self.archive._cognition_now()
        self.runtime.resume_durable_image_generation = AsyncMock(
            return_value={"retry_at": retry_at}
        )
        handler = AsyncMock(return_value={"ok": True})
        self.runtime._durable_runtime_handlers = {"daily_refresh": handler}
        self.assertEqual(await self.runtime._run_durable_tasks_once(), 1)
        self.runtime.resume_durable_image_generation.assert_awaited_once()
        handler.assert_awaited_once()

    async def test_durable_handler_renews_lease_while_waiting(self):
        await self.archive.enqueue_durable_task("heartbeat", "media_delivery", {})
        task = (await self.archive.lease_durable_tasks("attempt"))[0]
        release, renewed = asyncio.Event(), asyncio.Event()
        real_sleep = asyncio.sleep
        real_renew = self.archive.renew_durable_task_lease

        async def heartbeat(task_id, owner):
            result = await real_renew(task_id, owner)
            renewed.set()
            return result

        async def work(task):
            await release.wait()
            return {"ok": True}

        self.archive.renew_durable_task_lease = heartbeat

        async def fast_sleep(delay):
            await real_sleep(0.001)

        with patch(
            "core.runtime.spine.boot.asyncio.sleep",
            new=fast_sleep,
        ):
            run = asyncio.create_task(
                self.runtime._run_durable_handler(task, work, "attempt")
            )
            try:
                await asyncio.wait_for(renewed.wait(), 1)
                self.assertFalse(run.done())
            finally:
                release.set()
                self.assertEqual(await run, {"ok": True})

    async def test_lost_lease_cancels_the_old_handler(self):
        cancelled = asyncio.Event()
        real_sleep = asyncio.sleep

        async def work(task):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.archive.renew_durable_task_lease = AsyncMock(return_value=False)
        task = types.SimpleNamespace(id=1, kind="media_delivery")

        async def fast_sleep(delay):
            await real_sleep(0.001)

        with patch(
            "core.runtime.spine.boot.asyncio.sleep",
            new=fast_sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "租约已失效"):
                await self.runtime._run_durable_handler(task, work, "old-attempt")
        self.assertTrue(cancelled.is_set())

    async def test_not_started_child_does_not_reuse_closed_service(self):
        old, new = self._configure_swap()
        release = asyncio.Event()

        async def queued_work():
            await release.wait()
            async with self.runtime.runtime_service_lease():
                self.assertIs(self.runtime.media, new.media)

        async with self.runtime.runtime_service_lease():
            task = asyncio.create_task(queued_work())
        await self.runtime.apply_config({"rhythm_config": {"schedule_time": "09:30"}})
        self.assertTrue(old.media.closed)
        release.set()
        await task

    async def test_actual_image_recovery_download_uses_current_key_and_only_get(self):
        import base64
        import io
        from PIL import Image
        from core.media.base import normalize_openai_base_url

        service = GeminiImageService(
            self.runtime.config.image_generation, Path(self.directory.name)
        )
        route = _route()
        output = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(output, format="PNG")
        session = _Session(
            [],
            [
                _Response(
                    payload={
                        "status": "completed",
                        "result": {
                            "data": [
                                {
                                    "b64_json": base64.b64encode(
                                        output.getvalue()
                                    ).decode()
                                }
                            ]
                        },
                    }
                )
            ],
        )
        service._get_session = AsyncMock(return_value=session)
        service._request_routes = AsyncMock(return_value=[route])
        with patch.object(polling.asyncio, "sleep", new_callable=AsyncMock):
            generated = await service.resume_async_image(
                "accepted-before-restart",
                {
                    "api_url": normalize_openai_base_url(route.api_url),
                    "model": route.model,
                    "protocol": route.protocol,
                },
            )
        self.assertTrue(generated.path.is_file())
        self.assertEqual(generated.path.read_bytes(), output.getvalue())
        self.assertEqual([item[0] for item in session.calls], ["GET"])
        self.assertEqual(
            session.calls[0][2]["headers"], {"Authorization": "Bearer test-key"}
        )

    async def test_photo_suite_accepted_slot_is_not_submitted_again_after_timeout(self):
        service = GeminiImageService(
            self.runtime.config.image_generation, Path(self.directory.name)
        )
        self.runtime.media = types.SimpleNamespace(image=service)
        self.runtime._photo_suite_write_manifest = AsyncMock()
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:suite", sender_id="suite"
        )
        route = _route(timeout_seconds=0.01)
        session = _Session([_accepted()])

        async def generate(*args):
            return await polling.request_image_task(
                session,
                route,
                _request(route),
                on_accepted=service._task_listener.get(),
            )

        self.runtime._photo_suite_generate_asset = generate
        manifest = {"shots": [{"index": 1, "prompt": "照片", "status": "pending"}]}
        manifest_path = Path(self.directory.name) / "manifest.json"
        await self.runtime.track_image_generation(
            event,
            self.runtime._photo_suite_generate_slot(
                event, manifest_path, manifest, 1, asyncio.Lock()
            ),
        )
        self.assertEqual([item[0] for item in session.calls], ["POST"])
        self.assertEqual(manifest["shots"][0]["status"], "pending")
        record = (await self.archive.get_durable_tasks(kind="image_generation"))[0]
        self.assertEqual(record.status, "pending")
        self.assertEqual(
            record.payload["photo_suite"],
            {"manifest_path": str(manifest_path), "slot_index": 1},
        )

    async def test_photo_suite_handoff_preserves_still_processing_slots(self):
        ready = await self.archive.enqueue_durable_task(
            "suite:one",
            "image_generation",
            {"photo_suite": {"slot_index": 1}},
            lease_owner="one",
            lease_seconds=300,
        )
        waiting = await self.archive.enqueue_durable_task(
            "suite:two",
            "image_generation",
            {"photo_suite": {"slot_index": 2}},
            lease_owner="two",
            lease_seconds=300,
        )
        event = types.SimpleNamespace(
            _daily_life_async_image_tasks=[ready, waiting],
            _daily_life_photo_suite_ready_indexes={1},
        )
        await self.runtime.stage_durable_media_delivery(
            "platform:FriendMessage:suite",
            "images",
            ["ready.png"],
            action_type="photo",
            evidence="第一张已生成",
            source_event=event,
        )
        self.assertEqual(
            (await self.archive.get_durable_task("suite:one")).status, "completed"
        )
        self.assertEqual(
            (await self.archive.get_durable_task("suite:two")).status, "leased"
        )
        self.assertEqual(event._daily_life_async_image_tasks, [waiting])
