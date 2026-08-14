import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.archive import LifeArchive
from core.life.reliability import NonRetryableProviderError
from core.models import CommitmentRecord
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

    async def test_media_delivery_recovery_sends_saved_artifact_and_records_receipt(self):
        image_path = Path(self.directory.name) / "generated.png"
        image_path.write_bytes(b"fake-image")
        runtime = _MediaRuntime(self.archive, image_path)
        task = await self.archive.enqueue_durable_task(
            "media_delivery:recovery-1",
            "media_delivery",
            {
                "scope": "private:test",
                "media_kind": "image",
                "artifacts": [str(image_path)],
                "action_type": "photo",
                "evidence": "重启后恢复投递",
            },
        )

        result = await runtime.resume_durable_media_delivery(task)

        self.assertEqual(result["delivery"], "recovered")
        self.assertEqual(runtime.sent[0][0], "private:test")
        self.assertEqual(runtime.receipts[0][1], "photo")

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
