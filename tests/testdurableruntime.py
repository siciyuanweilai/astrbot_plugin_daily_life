import tempfile
import unittest
from pathlib import Path

from core.archive import LifeArchive
from core.runtime.spine.boot import SpineBootMixin


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


if __name__ == "__main__":
    unittest.main()
