import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

# Install AstrBot test stubs before importing runtime modules.
from support import LifeArchive  # isort: skip

from core.life.reliability import NonRetryableProviderError
from core.models import CommitmentRecord
from core.runtime.channel.summary import RuntimeMediaCommonMixin
from core.runtime.proactive.followup import ProactiveFollowupMixin
from core.runtime.receipt import RuntimeActionReceiptMixin
from core.runtime.spine.boot import SpineBootMixin


class _MediaRuntime(RuntimeActionReceiptMixin, SpineBootMixin):
    def __init__(self, archive, image_path):
        self.archive = archive
        self.image_path = image_path
        self.sent = []
        self.receipts = []
        self._durable_task_owner = "media-runtime"
        self._durable_runtime_handlers = {}
        self.context = SimpleNamespace(send_message=self._send)

    async def _send(self, scope, chain):
        self.sent.append((scope, chain))

    @staticmethod
    def image_message_chain(path):
        return {"type": "image", "file": str(path)}

    @staticmethod
    def images_message_chain(paths):
        return {"type": "images", "files": [str(path) for path in paths]}

    @staticmethod
    def video_message_chain(path):
        return {"type": "video", "file": str(path)}

    @staticmethod
    def video_file_message_chain(path):
        return {"type": "file", "file": str(path)}

    async def record_current_life_action_receipt(self, event, action_type, **kwargs):
        self.receipts.append((event, action_type, kwargs))


class DurableRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.directory.name) / "life.db")
        self.runtime = SpineBootMixin.__new__(SpineBootMixin)
        self.runtime.archive = self.archive
        self.runtime._durable_task_owner = "test-runtime"
        self.runtime._durable_runtime_handlers = {}

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.directory.cleanup()

    async def test_executes_registered_task_once_and_commits_result(self):
        calls = []

        async def handler():
            calls.append("done")

        self.runtime._durable_runtime_handlers["daily_review"] = handler
        await self.archive.enqueue_durable_task(
            "daily_review:2026-08-01",
            "daily_review",
            {"scheduled_at": "2026-08-01 23:45:00"},
        )

        completed = await self.runtime._run_durable_tasks_once()
        repeated = await self.runtime._run_durable_tasks_once()
        tasks = await self.archive.get_durable_tasks()

        self.assertEqual(completed, 1)
        self.assertEqual(repeated, 0)
        self.assertEqual(calls, ["done"])
        self.assertEqual(tasks[0].status, "completed")

    async def test_delivered_media_followup_uses_llm_and_sends_reply(self):
        runtime = RuntimeMediaCommonMixin.__new__(RuntimeMediaCommonMixin)
        prompts = []
        sent = []

        async def get_provider(provider_id=""):
            return object()

        async def call_text_model(provider, prompt, session_id, **kwargs):
            prompts.append(prompt)
            return '{"reply_text":"答应你的照片拍好啦。"}'

        async def get_persona_text(scope=""):
            return "说话自然简短。"

        async def send_background_text(scope, text, **kwargs):
            sent.append((scope, text, kwargs))
            return True

        async def append_history(scope, text):
            return None

        runtime.get_text_provider = get_provider
        runtime.call_text_model = call_text_model
        runtime.get_persona_text = get_persona_text
        runtime.send_background_text = send_background_text
        runtime._append_assistant_history = append_history

        result = await runtime._send_delivered_media_followup(
            "private:test",
            media_name="承诺的生活照片",
            request_text="到家后拍张照片给我",
            delivery_text="照片已成功送达",
        )

        self.assertTrue(result)
        self.assertIn("到家后拍张照片给我", prompts[0])
        self.assertEqual(sent[0][1], "答应你的照片拍好啦。")

    async def test_unknown_task_is_retried_without_executing_payload(self):
        await self.archive.enqueue_durable_task(
            "unknown:1",
            "unknown",
            {"callable": "os.system"},
            max_attempts=2,
        )

        completed = await self.runtime._run_durable_tasks_once()
        tasks = await self.archive.get_durable_tasks()

        self.assertEqual(completed, 0)
        self.assertEqual(tasks[0].status, "pending")
        self.assertIn("未知持久任务类型", tasks[0].last_error)

    async def test_non_retryable_provider_failure_immediately_ends_task(self):
        async def handler():
            raise NonRetryableProviderError(
                "模型不存在", status=404, provider_id="test-provider"
            )

        self.runtime._durable_runtime_handlers["daily_review"] = handler
        await self.archive.enqueue_durable_task(
            "daily_review:permanent-provider-error",
            "daily_review",
            {},
            max_attempts=48,
        )

        completed = await self.runtime._run_durable_tasks_once()
        task = (await self.archive.get_durable_tasks())[0]

        self.assertEqual(completed, 0)
        self.assertEqual(task.status, "dead")
        self.assertEqual(task.attempts, 1)
        self.assertIn("模型不存在", task.last_error)

    async def test_condition_wait_defers_without_consuming_attempt(self):
        async def handler(task):
            return {
                "retry_at": "2026-08-13 18:00:00",
                "reason": "等待条件成立",
            }

        self.runtime._durable_runtime_handlers["proactive_commitment"] = handler
        await self.archive.enqueue_durable_task(
            "proactive_commitment:wait",
            "proactive_commitment",
            {"commitment_id": 1},
        )

        completed = await self.runtime._run_durable_tasks_once()
        task = (await self.archive.get_durable_tasks())[0]

        self.assertEqual(completed, 0)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.attempts, 0)
        self.assertEqual(task.available_at, "2026-08-13 18:00:00")
        self.assertEqual(task.last_error, "")

    async def test_proactive_commitment_handler_receives_persisted_payload(self):
        received = []

        async def handler(task):
            received.append(task.payload)

        self.runtime._durable_runtime_handlers["proactive_commitment"] = handler
        await self.archive.enqueue_durable_task(
            "proactive_commitment:1",
            "proactive_commitment",
            {"commitment_id": 1, "scope": "test:FriendMessage:1"},
        )

        completed = await self.runtime._run_durable_tasks_once()

        self.assertEqual(completed, 1)
        self.assertEqual(received[0]["commitment_id"], 1)

    async def test_restart_releases_old_process_lease_immediately(self):
        await self.archive.enqueue_durable_task(
            "daily_review:2026-08-02",
            "daily_review",
            {},
            available_at="2026-08-02 00:00:00",
        )
        leased = await self.archive.lease_durable_tasks(
            "old-runtime",
            now="2026-08-02 00:00:00",
            lease_seconds=3600,
        )

        recovered = await self.archive.recover_leased_durable_tasks()
        tasks = await self.archive.get_durable_tasks()

        self.assertEqual(len(leased), 1)
        self.assertEqual(recovered, 1)
        self.assertEqual(tasks[0].status, "pending")
        self.assertEqual(tasks[0].lease_owner, "")

    async def test_media_delivery_recovery_sends_saved_artifact_and_records_receipt(
        self,
    ):
        image_path = Path(self.directory.name) / "generated.png"
        image_path.write_bytes(b"fake-image")
        runtime = _MediaRuntime(self.archive, image_path)
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="拍张照片给对方",
                owner="当前角色",
                media_kind="photo",
                source_session="private:test",
            )
        )
        followups = []

        async def followup(scope, **kwargs):
            followups.append((scope, kwargs))
            return True

        runtime._send_delivered_media_followup = followup
        task = await self.archive.enqueue_durable_task(
            "media_delivery:recovery-1",
            "media_delivery",
            {
                "scope": "private:test",
                "media_kind": "image",
                "artifacts": [str(image_path)],
                "action_type": "photo",
                "evidence": "重启后恢复投递",
                "commitment_id": commitment.id,
                "reply_context": {
                    "media_name": "承诺的生活照片",
                    "request_text": commitment.content,
                    "delivery_text": "照片已恢复并成功送达",
                },
            },
        )

        result = await runtime.resume_durable_media_delivery(task)

        self.assertEqual(result["delivery"], "recovered")
        self.assertEqual(runtime.sent[0][0], "private:test")
        self.assertEqual(runtime.receipts[0][1], "photo")
        self.assertTrue(result["reply_sent"])
        self.assertEqual(followups[0][0], "private:test")
        self.assertEqual(
            (await self.archive.get_commitment(commitment.id)).status,
            "done",
        )

    async def test_media_delivery_recovery_retries_when_platform_is_not_ready(self):
        image_path = Path(self.directory.name) / "pending.png"
        image_path.write_bytes(b"fake-image")
        runtime = _MediaRuntime(self.archive, image_path)

        async def unavailable_send(scope, chain):
            runtime.sent.append((scope, chain))
            return False

        runtime.context.send_message = unavailable_send
        task = await self.archive.enqueue_durable_task(
            "media_delivery:recovery-pending",
            "media_delivery",
            {
                "scope": "private:test",
                "media_kind": "image",
                "artifacts": [str(image_path)],
                "action_type": "photo",
                "evidence": "等待平台连接后恢复",
            },
        )

        with self.assertRaisesRegex(RuntimeError, "目标平台尚未就绪"):
            await runtime.resume_durable_media_delivery(task)

        self.assertEqual(len(runtime.sent), 1)
        self.assertEqual(runtime.receipts, [])

    async def test_webchat_video_recovery_uses_file_attachment(self):
        runtime = _MediaRuntime(self.archive, None)
        task = await self.archive.enqueue_durable_task(
            "media_delivery:webchat-video",
            "media_delivery",
            {
                "scope": "webchat:FriendMessage:webchat!admin!test-session",
                "media_kind": "video",
                "artifacts": ["https://cdn.example/video.mp4"],
            },
        )

        result = await runtime.resume_durable_media_delivery(task)

        self.assertEqual(result["delivery"], "recovered")
        self.assertEqual(runtime.sent[0][1]["type"], "file")

    async def test_active_media_delivery_cannot_be_claimed_by_worker(self):
        cases = (
            ("image", ["active.png"], "photo"),
            ("images", ["suite-1.png", "suite-2.png"], "photo"),
            ("video", ["active.mp4"], "video"),
        )
        for media_kind, names, action_type in cases:
            with self.subTest(media_kind=media_kind):
                artifacts = []
                for name in names:
                    path = Path(self.directory.name) / name
                    path.write_bytes(b"fake-media")
                    artifacts.append(str(path))
                runtime = _MediaRuntime(self.archive, Path(artifacts[0]))

                task = await runtime.stage_durable_media_delivery(
                    "private:test",
                    media_kind,
                    artifacts,
                    action_type=action_type,
                    evidence="媒体已生成，等待投递确认",
                )
                completed = await runtime._run_durable_tasks_once()
                stored = next(
                    item
                    for item in await self.archive.get_durable_tasks(
                        kind="media_delivery"
                    )
                    if item.id == task.id
                )

                self.assertEqual(task.status, "leased")
                self.assertEqual(task.lease_owner, "media-runtime")
                self.assertEqual(completed, 0)
                self.assertEqual(runtime.sent, [])
                self.assertEqual(stored.status, "leased")

                finalized = await runtime.finalize_durable_media_delivery(
                    task,
                    outcome="sent",
                    detail="媒体已发送",
                )
                stored = next(
                    item
                    for item in await self.archive.get_durable_tasks(
                        kind="media_delivery"
                    )
                    if item.id == task.id
                )

                self.assertTrue(finalized)
                self.assertEqual(stored.status, "completed")
                self.assertEqual(stored.attempts, 0)

    async def test_direct_video_delivery_settles_matching_immediate_commitment(self):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="现在拍个视频给我",
                trigger_date=datetime.datetime.now().strftime("%Y-%m-%d"),
                owner="当前角色",
                media_kind="video",
                source_session="private:video",
                source_message_id="video-request-1",
            )
        )
        unrelated = await self.archive.save_commitment(
            CommitmentRecord(
                content="明天拍另一段视频",
                trigger_date="2099-01-01",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="video",
                source_session="private:video",
                source_message_id="video-request-2",
            )
        )
        scheduled = await self.archive.save_commitment(
            CommitmentRecord(
                content="明天再拍一段视频",
                trigger_date="2099-01-01",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="video",
                source_session="private:video",
                source_message_id="video-request-3",
            )
        )
        runtime = _MediaRuntime(self.archive, None)
        task = await runtime.stage_durable_media_delivery(
            "private:video",
            "video",
            ["https://example.com/video.mp4"],
            action_type="video",
            evidence="视频已生成",
            source_message_id="video-request-1",
        )

        self.assertTrue(await runtime.finalize_durable_media_delivery(task, outcome="sent"))
        self.assertEqual((await self.archive.get_commitment(commitment.id)).status, "done")
        self.assertEqual((await self.archive.get_commitment(unrelated.id)).status, "active")
        scheduled_task = await runtime.stage_durable_media_delivery(
            "private:video",
            "video",
            ["https://example.com/scheduled-video.mp4"],
            action_type="video",
            evidence="预约视频已发送",
            source_message_id="video-request-3",
        )
        self.assertTrue(
            await runtime.finalize_durable_media_delivery(scheduled_task, outcome="sent")
        )
        self.assertEqual((await self.archive.get_commitment(scheduled.id)).status, "done")
        self.assertTrue(
            await runtime.direct_media_was_delivered(
                "private:video", "video", ["video-request-1"]
            )
        )
        self.assertFalse(
            await runtime.direct_media_was_delivered(
                "private:other", "video", ["video-request-1"]
            )
        )

    async def test_direct_image_deliveries_settle_matching_photo_commitments(self):
        runtime = _MediaRuntime(self.archive, None)
        for media_kind in ("image", "images"):
            with self.subTest(media_kind=media_kind):
                message_id = f"photo-request-{media_kind}"
                commitment = await self.archive.save_commitment(
                    CommitmentRecord(
                        content="拍照发给我",
                        trigger_date=datetime.datetime.now().strftime("%Y-%m-%d"),
                        owner="当前角色",
                        media_kind="photo",
                        source_session="private:photo",
                        source_message_id=message_id,
                    )
                )
                task = await runtime.stage_durable_media_delivery(
                    "private:photo",
                    media_kind,
                    [f"https://example.com/{media_kind}.png"],
                    action_type="photo",
                    evidence="照片已生成",
                    source_message_id=message_id,
                )
                self.assertTrue(
                    await runtime.finalize_durable_media_delivery(task, outcome="sent")
                )
                self.assertEqual(
                    (await self.archive.get_commitment(commitment.id)).status, "done"
                )
                self.assertTrue(
                    await runtime.direct_media_was_delivered(
                        "private:photo", "photo", [message_id]
                    )
                )

    async def test_restart_releases_active_media_for_single_recovery(self):
        video_path = Path(self.directory.name) / "active.mp4"
        video_path.write_bytes(b"fake-video")
        old_runtime = _MediaRuntime(self.archive, video_path)
        old_runtime._durable_task_owner = "old-runtime"
        task = await old_runtime.stage_durable_media_delivery(
            "private:test",
            "video",
            [str(video_path)],
            action_type="video",
            evidence="视频已生成，等待投递确认",
        )
        self.assertEqual(task.status, "leased")

        released = await self.archive.recover_leased_durable_tasks()
        new_runtime = _MediaRuntime(self.archive, video_path)
        new_runtime._durable_task_owner = "new-runtime"
        first = await new_runtime._run_durable_tasks_once()
        second = await new_runtime._run_durable_tasks_once()
        stored = (await self.archive.get_durable_tasks(kind="media_delivery"))[0]

        self.assertEqual(released, 1)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(new_runtime.sent), 1)
        self.assertEqual(new_runtime.sent[0][1]["type"], "video")
        self.assertEqual(stored.status, "completed")
        self.assertEqual(stored.result["delivery"], "recovered")


class ProactiveCommitmentScheduleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.directory.name) / "life.db")
        self.runtime = ProactiveFollowupMixin.__new__(ProactiveFollowupMixin)
        self.runtime.archive = self.archive

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.directory.cleanup()

    async def test_only_current_role_follow_up_creates_task(self):
        commitment = await self.archive.save_commitment(
            {
                "content": "傍晚出门前联系对方",
                "source_session": "test:FriendMessage:1",
            }
        )
        follow_up = {
            "action": "contact_person",
            "message_goal": "确认是否准备好",
            "execute_at": "2026-08-13 17:30",
        }
        observed_at = datetime.datetime(2026, 8, 13, 15, 0)

        speaker_owned = await self.runtime.schedule_proactive_commitment(
            commitment,
            owner="说话人",
            follow_up=follow_up,
            observed_at=observed_at,
        )
        current_role_owned = await self.runtime.schedule_proactive_commitment(
            commitment,
            owner="当前角色",
            follow_up=follow_up,
            observed_at=observed_at,
        )
        tasks = await self.archive.get_durable_tasks(kind="proactive_commitment")

        self.assertFalse(speaker_owned)
        self.assertTrue(current_role_owned)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].available_at, "2026-08-13 17:30:00")

    async def test_group_and_missing_time_do_not_create_task(self):
        group_commitment = await self.archive.save_commitment(
            {
                "content": "晚点通知",
                "source_session": "test:GroupMessage:1",
            }
        )
        private_commitment = await self.archive.save_commitment(
            {
                "content": "晚点通知",
                "source_session": "test:FriendMessage:1",
            }
        )
        observed_at = datetime.datetime(2026, 8, 13, 15, 0)

        group_result = await self.runtime.schedule_proactive_commitment(
            group_commitment,
            owner="当前角色",
            follow_up={
                "action": "contact_person",
                "execute_at": "2026-08-13 17:30",
            },
            observed_at=observed_at,
        )
        untimed_result = await self.runtime.schedule_proactive_commitment(
            private_commitment,
            owner="当前角色",
            follow_up={"action": "contact_person", "execute_at": ""},
            observed_at=observed_at,
        )

        self.assertFalse(group_result)
        self.assertFalse(untimed_result)
        self.assertEqual(
            await self.archive.get_durable_tasks(kind="proactive_commitment"), []
        )

    async def test_media_promises_never_create_text_followup_tasks(self):
        for media_kind in ("photo", "video"):
            with self.subTest(media_kind=media_kind):
                commitment = await self.archive.save_commitment(
                    CommitmentRecord(
                        content="明天白天把挑好的两张照片发给对方慢慢看",
                        trigger_date="2026-09-25",
                        owner="当前角色",
                        media_kind=media_kind,
                        source_session="test:FriendMessage:1",
                    )
                )
                scheduled = await self.runtime.schedule_proactive_commitment(
                    commitment,
                    owner="当前角色",
                    follow_up={
                        "action": "contact_person",
                        "condition": "明天白天合适时段且照片已准备妥当",
                        "message_goal": "把照片发给对方，并说可以慢慢看",
                    },
                    observed_at=datetime.datetime(2026, 9, 24, 22, 4),
                )
                self.assertFalse(scheduled)
        self.assertEqual(
            await self.archive.get_durable_tasks(kind="proactive_commitment"), []
        )

    async def test_queued_media_followup_routes_to_delivery_without_sending_text(self):
        for media_kind in ("photo", "video"):
            for trigger_time in ("09:00", ""):
                with self.subTest(media_kind=media_kind, trigger_time=trigger_time):
                    commitment = await self.archive.save_commitment(
                        CommitmentRecord(
                            content="把准备好的生活照片或视频发给对方",
                            trigger_date="2026-09-25",
                            trigger_time=trigger_time,
                            owner="当前角色",
                            media_kind=media_kind,
                            source_session="test:FriendMessage:1",
                        )
                    )
                    # No text evaluator or sender exists on this test runtime.
                    result = await self.runtime.run_proactive_commitment_task(
                        SimpleNamespace(
                            payload={
                                "scope": commitment.source_session,
                                "commitment_id": commitment.id,
                                "action": "contact_person",
                            }
                        )
                    )
                    stored = await self.archive.get_commitment(commitment.id)
                    self.assertNotEqual(stored.status, "done")
                    tasks = await self.archive.get_durable_tasks(
                        kind=f"commitment_{media_kind}"
                    )
                    matching = [
                        task
                        for task in tasks
                        if task.payload["commitment_id"] == commitment.id
                    ]
                    if trigger_time:
                        self.assertEqual(result["outcome"], "media_scheduled")
                        self.assertEqual(len(matching), 1)
                        self.assertEqual(
                            matching[0].payload["scope"], commitment.source_session
                        )
                    else:
                        self.assertEqual(result["outcome"], "media_pending")
                        self.assertEqual(stored.status, "pending")
                        self.assertEqual(matching, [])

    async def test_photo_task_requires_image_delivery_before_completing_promise(self):
        responses = (
            '{"status":"sent","media":"text"}',
            '{"status":"sent"}',
            '{"status":"pending","media":"image"}',
            "图片生成失败",
            OSError("DNS lookup failed"),
        )
        self.runtime.config = SimpleNamespace(
            image_generation=SimpleNamespace(enabled=True)
        )
        for response in responses:
            for attempts in (1, 4):
                with self.subTest(response=response, attempts=attempts):
                    commitment = await self.archive.save_commitment(
                        CommitmentRecord(
                            content="把照片发给对方",
                            owner="当前角色",
                            media_kind="photo",
                            source_session="test:FriendMessage:1",
                        )
                    )
                    await self.archive.save_conversation_action_item(
                        {
                            "commitment_id": commitment.id,
                            "title": commitment.content,
                            "owner": commitment.owner,
                            "source_session": commitment.source_session,
                        }
                    )

                    async def generate(event, prompt, **kwargs):
                        if isinstance(response, Exception):
                            raise response
                        return response

                    self.runtime.life_image_generate = generate
                    task = SimpleNamespace(
                        payload={
                            "scope": commitment.source_session,
                            "commitment_id": commitment.id,
                        },
                        attempts=attempts,
                        max_attempts=4,
                    )
                    with self.assertRaises((RuntimeError, OSError)):
                        await self.runtime.run_commitment_photo_task(task)
                    stored = await self.archive.get_commitment(commitment.id)
                    self.assertEqual(
                        stored.status, "delivery_failed" if attempts == 4 else "active"
                    )
                    items = await self.archive.get_conversation_action_items(limit=50)
                    item = next(
                        row for row in items if row["commitment_id"] == commitment.id
                    )
                    self.assertEqual(
                        item["status"], "failed" if attempts == 4 else "open"
                    )

    async def test_expired_media_followup_does_not_schedule_delivery(self):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="拍张照片给对方",
                trigger_date="2000-01-01",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="photo",
                source_session="test:FriendMessage:1",
            )
        )
        result = await self.runtime.run_proactive_commitment_task(
            SimpleNamespace(
                payload={
                    "scope": commitment.source_session,
                    "commitment_id": commitment.id,
                    "action": "contact_person",
                    "expires_at": "2000-01-02 09:00:00",
                }
            )
        )
        self.assertEqual(result["outcome"], "expired")
        self.assertEqual(
            await self.archive.get_durable_tasks(kind="commitment_photo"), []
        )
        self.assertEqual(
            (await self.archive.get_commitment(commitment.id)).status, "expired"
        )

    async def test_photo_commitment_creates_private_image_task_at_morning_window(self):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="明早醒来拍张照片给对方",
                trigger_date="2026-08-27",
                trigger_time="08:00",
                time_window="早晨醒后",
                owner="当前角色",
                media_kind="photo",
                source_session="test:FriendMessage:1",
                source_message="明早醒来拍张照片给对方",
            )
        )

        scheduled = await self.runtime.schedule_commitment_photo(
            commitment,
            owner="当前角色",
            observed_at=datetime.datetime(2026, 8, 26, 23, 0),
        )
        tasks = await self.archive.get_durable_tasks(kind="commitment_photo")

        self.assertTrue(scheduled)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].available_at, "2026-08-27 08:00:00")
        self.assertEqual(tasks[0].payload["scope"], "test:FriendMessage:1")

    async def test_photo_commitment_targets_original_group_scope_and_rejects_other_owned_promise(
        self,
    ):
        group_commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="拍张照片给对方",
                trigger_date="2026-08-27",
                trigger_time="08:00",
                owner="当前角色",
                media_kind="photo",
                source_session="test:GroupMessage:1",
            )
        )
        speaker_commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="对方给我拍张照片",
                trigger_date="2026-08-27",
                trigger_time="08:00",
                owner="说话人",
                media_kind="photo",
                source_session="test:FriendMessage:1",
            )
        )

        self.assertTrue(
            await self.runtime.schedule_commitment_photo(
                group_commitment,
                owner="当前角色",
                observed_at=datetime.datetime(2026, 8, 27, 7, 0),
            )
        )
        self.assertFalse(
            await self.runtime.schedule_commitment_photo(
                speaker_commitment,
                owner="说话人",
                observed_at=datetime.datetime(2026, 8, 27, 7, 0),
            )
        )
        self.assertEqual(
            [
                task.payload["scope"]
                for task in await self.archive.get_durable_tasks(
                    kind="commitment_photo"
                )
            ],
            [group_commitment.source_session],
        )

    async def test_photo_reconcile_recovers_due_group_commitments_to_original_group(
        self,
    ):
        stale = await self.archive.save_commitment(
            CommitmentRecord(
                content="昨天拍张照片给对方",
                trigger_date="2026-08-25",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="photo",
                source_session="test:GroupMessage:stale-photo",
            )
        )
        upcoming = await self.archive.save_commitment(
            CommitmentRecord(
                content="明早拍张照片给对方",
                trigger_date="2026-08-27",
                trigger_time="08:00",
                time_window="早晨",
                owner="当前角色",
                media_kind="photo",
                source_session="test:GroupMessage:upcoming-photo",
            )
        )

        created = await self.runtime.reconcile_commitment_photo_tasks(
            datetime.datetime(2026, 8, 26, 23, 0)
        )
        tasks = await self.archive.get_durable_tasks(kind="commitment_photo")

        self.assertEqual(created, 2)
        self.assertEqual(
            {task.payload["commitment_id"] for task in tasks},
            {stale.id, upcoming.id},
        )

    async def test_photo_commitment_recognizes_food_and_rejects_video_promises(self):
        food_commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="去糖水铺时给对方拍好吃的",
                trigger_date="2026-06-21",
                trigger_time="10:00",
                time_window="等会儿",
                owner="当前角色",
                media_kind="photo",
                source_session="target:FriendMessage:photo-food",
                source_message="拍好吃的给你看",
                confidence=1.0,
            )
        )
        video_commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="下次给对方拍视频",
                trigger_date="2026-06-21",
                trigger_time="10:00",
                owner="当前角色",
                media_kind="video",
                source_session="target:FriendMessage:photo-video",
                source_message="拍视频给你看",
                confidence=1.0,
            )
        )

        self.assertTrue(
            await self.runtime.schedule_commitment_photo(
                food_commitment,
                observed_at=datetime.datetime(2026, 6, 21, 10, 0),
            )
        )
        self.assertFalse(
            await self.runtime.schedule_commitment_photo(
                video_commitment,
                owner="当前角色",
                observed_at=datetime.datetime(2026, 6, 21, 10, 0),
            )
        )
        tasks = await self.archive.get_durable_tasks(kind="commitment_photo")
        self.assertEqual(
            [task.payload["commitment_id"] for task in tasks], [food_commitment.id]
        )

    async def test_photo_task_marks_commitment_done_after_image_is_sent(self):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="拍张照片给对方",
                owner="当前角色",
                media_kind="photo",
                source_session="test:FriendMessage:1",
            )
        )
        self.runtime.config = SimpleNamespace(
            image_generation=SimpleNamespace(enabled=True)
        )
        calls = []
        followups = []

        async def generate(event, prompt, **kwargs):
            calls.append((event, prompt, kwargs))
            return '{"status":"sent","media":"image"}'

        async def followup(scope, **kwargs):
            followups.append((scope, kwargs))
            return True

        self.runtime.life_image_generate = generate
        self.runtime._send_delivered_media_followup = followup
        task = SimpleNamespace(
            payload={
                "scope": commitment.source_session,
                "commitment_id": commitment.id,
                "prompt": commitment.content,
            },
            attempts=1,
            max_attempts=4,
        )

        result = await self.runtime.run_commitment_photo_task(task)

        self.assertEqual(result["outcome"], "sent")
        self.assertEqual(
            (await self.archive.get_commitment(commitment.id)).status, "done"
        )
        self.assertEqual(calls[0][0].unified_msg_origin, commitment.source_session)
        self.assertEqual(calls[0][0]._daily_life_commitment_id, commitment.id)
        self.assertTrue(result["reply_sent"])
        self.assertEqual(followups[0][1]["media_name"], "承诺的生活照片")

    async def test_photo_task_can_deliver_to_original_group_scope(self):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="拍张照片给对方",
                owner="当前角色",
                media_kind="photo",
                source_session="test:GroupMessage:photo-delivery",
            )
        )
        self.runtime.config = SimpleNamespace(
            image_generation=SimpleNamespace(enabled=True)
        )
        calls = []

        async def generate(event, prompt, **kwargs):
            calls.append((event, prompt, kwargs))
            return '{"status":"sent","media":"image"}'

        self.runtime.life_image_generate = generate
        task = SimpleNamespace(
            payload={
                "scope": commitment.source_session,
                "commitment_id": commitment.id,
                "prompt": commitment.content,
            },
            attempts=1,
            max_attempts=4,
        )

        result = await self.runtime.run_commitment_photo_task(task)

        self.assertEqual(result["outcome"], "sent")
        self.assertEqual(calls[0][0].unified_msg_origin, commitment.source_session)

    async def test_video_commitment_creates_private_video_task_at_morning_window(self):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="明早醒来拍一段视频给对方",
                trigger_date="2026-08-27",
                trigger_time="08:00",
                time_window="早晨醒后",
                owner="当前角色",
                media_kind="video",
                source_session="test:FriendMessage:1",
                source_message="明早醒来拍一段视频给你看",
            )
        )

        scheduled = await self.runtime.schedule_commitment_video(
            commitment,
            owner="当前角色",
            observed_at=datetime.datetime(2026, 8, 26, 23, 0),
        )
        tasks = await self.archive.get_durable_tasks(kind="commitment_video")

        self.assertTrue(scheduled)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].available_at, "2026-08-27 08:00:00")
        self.assertEqual(tasks[0].payload["scope"], "test:FriendMessage:1")

    async def test_rescheduling_pending_media_commitment_refreshes_task_time(self):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="明天拍视频给对方",
                trigger_date="2026-08-27",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="video",
                source_session="test:FriendMessage:reschedule",
            )
        )
        await self.runtime.schedule_commitment_video(
            commitment,
            owner="当前角色",
            observed_at=datetime.datetime(2026, 8, 26, 23, 0),
        )
        self.assertEqual(
            (await self.archive.get_durable_tasks(kind="commitment_video"))[
                0
            ].available_at,
            "2026-08-27 09:00:00",
        )

        self.assertTrue(
            await self.archive.reschedule_commitment(
                commitment.id, "2026-08-29", "", trigger_time="09:00"
            )
        )
        refreshed = await self.archive.get_commitment(commitment.id)
        await self.runtime.schedule_commitment_video(
            refreshed,
            owner="当前角色",
            observed_at=datetime.datetime(2026, 8, 26, 23, 0),
        )
        self.assertEqual(
            (await self.archive.get_durable_tasks(kind="commitment_video"))[
                0
            ].available_at,
            "2026-08-29 09:00:00",
        )

    async def test_video_commitment_accepts_selfie_video(self):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="明天录一个自拍视频给对方",
                trigger_date="2026-08-27",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="video",
                source_session="test:FriendMessage:selfie-video",
            )
        )

        self.assertTrue(
            await self.runtime.schedule_commitment_video(
                commitment,
                owner="当前角色",
                observed_at=datetime.datetime(2026, 8, 26, 23, 0),
            )
        )

    async def test_video_commitment_rejects_calls_and_mixed_media_but_targets_original_group_scope(
        self,
    ):
        call_commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="下次和对方视频通话",
                trigger_date="2026-08-27",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="none",
                source_session="test:FriendMessage:call",
            )
        )
        mixed_commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="拍照并录视频给对方",
                trigger_date="2026-08-27",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="none",
                source_session="test:FriendMessage:mixed",
            )
        )
        group_commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="拍视频给对方",
                trigger_date="2026-08-27",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="video",
                source_session="test:GroupMessage:video",
            )
        )

        self.assertFalse(
            await self.runtime.schedule_commitment_video(
                call_commitment,
                owner="当前角色",
                observed_at=datetime.datetime(2026, 8, 27, 7, 0),
            )
        )
        self.assertFalse(
            await self.runtime.schedule_commitment_video(
                mixed_commitment,
                owner="当前角色",
                observed_at=datetime.datetime(2026, 8, 27, 7, 0),
            )
        )
        self.assertTrue(
            await self.runtime.schedule_commitment_video(
                group_commitment,
                owner="当前角色",
                observed_at=datetime.datetime(2026, 8, 27, 7, 0),
            )
        )
        tasks = await self.archive.get_durable_tasks(kind="commitment_video")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].payload["scope"], group_commitment.source_session)

    async def test_video_reconcile_recovers_due_group_commitments_to_original_group(
        self,
    ):
        stale = await self.archive.save_commitment(
            CommitmentRecord(
                content="昨天拍视频给对方",
                trigger_date="2026-08-25",
                trigger_time="09:00",
                owner="当前角色",
                media_kind="video",
                source_session="test:GroupMessage:stale-video",
            )
        )
        upcoming = await self.archive.save_commitment(
            CommitmentRecord(
                content="明早拍视频给对方",
                trigger_date="2026-08-27",
                trigger_time="08:00",
                time_window="早晨",
                owner="当前角色",
                media_kind="video",
                source_session="test:GroupMessage:upcoming-video",
            )
        )

        created = await self.runtime.reconcile_commitment_video_tasks(
            datetime.datetime(2026, 8, 26, 23, 0)
        )
        tasks = await self.archive.get_durable_tasks(kind="commitment_video")

        self.assertEqual(created, 2)
        self.assertEqual(
            {task.payload["commitment_id"] for task in tasks},
            {stale.id, upcoming.id},
        )

    async def test_video_task_settles_only_after_async_video_delivery(self):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="拍视频给对方",
                owner="当前角色",
                media_kind="video",
                source_session="test:FriendMessage:video",
            )
        )
        self.runtime.config = SimpleNamespace(
            video_generation=SimpleNamespace(enabled=True, timeout_seconds=30)
        )

        async def generate(event, prompt, **kwargs):
            event._daily_life_commitment_video_future.set_result("sent")
            return '{"status":"pending","media":"video"}'

        self.runtime.life_video_generate = generate
        task = SimpleNamespace(
            payload={
                "scope": commitment.source_session,
                "commitment_id": commitment.id,
                "prompt": commitment.content,
            },
            attempts=1,
            max_attempts=4,
        )

        result = await self.runtime.run_commitment_video_task(task)

        self.assertEqual(result["outcome"], "sent")
        self.assertEqual(
            (await self.archive.get_commitment(commitment.id)).status, "done"
        )

    async def test_invite_contact_does_not_settle_shared_commitment(self):
        commitment = await self.archive.save_commitment(
            {
                "content": "傍晚一起散步",
                "trigger_date": "2026-08-13",
                "source_session": "test:FriendMessage:1",
            }
        )

        scheduled = await self.runtime.schedule_invite_contact(
            commitment,
            timeline_edits=[
                {
                    "operation": "insert",
                    "item": {"time": "18:00", "activity": "一起出门"},
                }
            ],
            observed_at=datetime.datetime(2026, 8, 13, 15, 0),
        )
        tasks = await self.archive.get_durable_tasks(kind="proactive_commitment")

        self.assertTrue(scheduled)
        self.assertEqual(tasks[0].available_at, "2026-08-13 17:55:00")
        self.assertFalse(tasks[0].payload["settle_commitment"])

    async def test_co_present_explicit_promise_is_spoken_instead_of_silently_completed(
        self,
    ):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="出门前叫对方",
                trigger_date="2026-08-13",
                status="scheduled",
                source_session="test:FriendMessage:1",
            )
        )
        self.runtime.resolve_interaction_context = lambda **kwargs: _async_value(
            SimpleNamespace(
                mode="co_present",
                mode_label="同处现场",
                evidence="双方正在家里准备出门",
            )
        )
        self.runtime._proactive_commitment_relationship = lambda scope: _async_value(
            None
        )
        decisions = []

        async def evaluate(**kwargs):
            decisions.append(kwargs)
            return {
                "should_send": True,
                "reply_text": "该走啦，东西带齐没有？",
                "settlement": "send",
                "expression_intent": {},
            }

        sent = []

        async def send(scope, text, failure_label, **kwargs):
            sent.append((scope, text, failure_label, kwargs))
            return True

        self.runtime._evaluate_proactive_commitment = evaluate
        self.runtime._send_proactive_message = send
        task = SimpleNamespace(
            payload={
                "scope": commitment.source_session,
                "commitment_id": commitment.id,
                "action": "contact_person",
                "settle_commitment": False,
            }
        )

        result = await self.runtime.run_proactive_commitment_task(task)

        self.assertEqual(result["outcome"], "sent")
        self.assertEqual(sent[0][1], "该走啦，东西带齐没有？")
        self.assertEqual(decisions[0]["interaction"].mode, "co_present")
        self.assertEqual(
            (await self.archive.get_commitment(commitment.id)).status, "scheduled"
        )


async def _async_value(value):
    return value


if __name__ == "__main__":
    unittest.main()
