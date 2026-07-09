import asyncio
import unittest

from support import DailyLifeRuntime
from core.runtime.background import BackgroundTaskScheduler


class BackgroundTaskSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_background_tasks_are_limited_to_avoid_framework_pressure(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._background_scheduler = BackgroundTaskScheduler(normal_limit=2, chat_limit=1)

        active = 0
        peak = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def job():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.set()
            await release.wait()
            active -= 1

        for index in range(4):
            self.assertTrue(runtime._schedule_background_task(job(), label="普通后台任务", key=f"task:{index}"))

        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertEqual(peak, 2)

        release.set()
        await asyncio.gather(*list(runtime._background_scheduler.tasks))
        self.assertEqual(runtime._background_scheduler.tasks, set())
        self.assertEqual(runtime._background_scheduler.keys, set())

    async def test_chat_capture_background_tasks_run_one_at_a_time(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._background_scheduler = BackgroundTaskScheduler(normal_limit=4, chat_limit=1)

        active = 0
        peak = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def job():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.set()
            await release.wait()
            active -= 1

        for index in range(3):
            runtime._schedule_background_task(job(), label="聊天记忆提炼", key=f"chat:{index}")

        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertEqual(peak, 1)

        release.set()
        await asyncio.gather(*list(runtime._background_scheduler.tasks))
        self.assertEqual(runtime._background_scheduler.tasks, set())
        self.assertEqual(runtime._background_scheduler.keys, set())

    async def test_background_task_duplicate_key_is_closed_without_queueing(self):
        scheduler = BackgroundTaskScheduler()
        ran = []

        class AwaitableProbe:
            def __init__(self, name):
                self.name = name
                self.closed = False

            def __await__(self):
                async def inner():
                    ran.append(self.name)

                return inner().__await__()

            def close(self):
                self.closed = True

        first = AwaitableProbe("first")
        duplicate = AwaitableProbe("duplicate")

        self.assertTrue(scheduler.schedule(first, label="去重测试", key="same-key"))
        self.assertFalse(scheduler.schedule(duplicate, label="去重测试", key="same-key"))
        self.assertTrue(duplicate.closed)
        self.assertEqual(len(scheduler.tasks), 1)

        await asyncio.gather(*list(scheduler.tasks))

        self.assertEqual(ran, ["first"])
        self.assertEqual(scheduler.keys, set())

    async def test_background_task_slow_completion_is_logged_and_cleaned(self):
        from core.runtime import background as background_module

        warnings = []
        old_warning = background_module.logger.warning
        background_module.logger.warning = lambda message, *args, **kwargs: warnings.append(str(message))
        try:
            scheduler = BackgroundTaskScheduler(slow_task_seconds=0)

            async def job():
                await asyncio.sleep(0)

            self.assertTrue(scheduler.schedule(job(), label="耗时测试", key="slow-key"))
            await asyncio.gather(*list(scheduler.tasks))
        finally:
            background_module.logger.warning = old_warning

        self.assertEqual(scheduler.tasks, set())
        self.assertEqual(scheduler.keys, set())
        self.assertTrue(any("后台任务完成（耗时测试）" in message for message in warnings))
        self.assertTrue(any("耗时 " in message and "总耗时" not in message for message in warnings))

    def test_background_completion_message_omits_duplicate_total_when_not_queued(self):
        message = BackgroundTaskScheduler._completion_message("图片上下文识别", 6.47, 6.47)

        self.assertIn("后台任务完成（图片上下文识别），耗时 6.47s。", message)
        self.assertNotIn("总耗时", message)
        self.assertNotIn("排队", message)

    def test_background_completion_message_includes_queue_when_waited(self):
        message = BackgroundTaskScheduler._completion_message("图片上下文识别", 6.47, 7.29)

        self.assertIn("执行耗时 6.47s", message)
        self.assertIn("排队 0.82s", message)
        self.assertIn("总耗时 7.29s", message)
