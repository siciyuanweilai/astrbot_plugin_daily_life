import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path

from core.archive import LifeArchive
from core.runtime.action import RuntimeActionReceiptMixin
from core.runtime.spine.boot import SpineBootMixin


class _MediaRuntime(RuntimeActionReceiptMixin):
    def __init__(self, archive, image_path):
        self.archive = archive
        self.image_path = image_path
        self.sent = []
        self.receipts = []
        self.context = SimpleNamespace(send_message=self._send)

    async def _send(self, scope, chain):
        self.sent.append((scope, chain))

    @staticmethod
    def image_message_chain(path):
        return {"type": "image", "file": str(path)}

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


if __name__ == "__main__":
    unittest.main()
