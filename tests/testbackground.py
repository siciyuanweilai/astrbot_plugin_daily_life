import asyncio
import unittest

from support import DailyLifeRuntime
from core.runtime.background import BackgroundTaskScheduler


class BackgroundTaskSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_background_tasks_are_limited_to_avoid_framework_pressure(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._background_scheduler = BackgroundTaskScheduler(
            normal_limit=2, chat_limit=1
        )

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
            self.assertTrue(
                runtime._schedule_background_task(
                    job(), label="普通后台任务", key=f"task:{index}"
                )
            )

        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertEqual(peak, 2)

        release.set()
        await asyncio.gather(*list(runtime._background_scheduler.tasks))
        self.assertEqual(runtime._background_scheduler.tasks, set())
        self.assertEqual(runtime._background_scheduler.keys, set())

    async def test_chat_capture_background_tasks_run_one_at_a_time(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._background_scheduler = BackgroundTaskScheduler(
            normal_limit=4, chat_limit=1
        )

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
            runtime._schedule_background_task(
                job(), label="聊天记忆提炼", key=f"chat:{index}"
            )

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
        self.assertFalse(
            scheduler.schedule(duplicate, label="去重测试", key="same-key")
        )
        self.assertTrue(duplicate.closed)
        self.assertEqual(len(scheduler.tasks), 1)

        await asyncio.gather(*list(scheduler.tasks))

        self.assertEqual(ran, ["first"])
        self.assertEqual(scheduler.keys, set())

    async def test_background_backlog_is_bounded_per_category(self):
        scheduler = BackgroundTaskScheduler(
            video_limit=1,
            video_backlog_limit=2,
        )
        release = asyncio.Event()

        async def job():
            await release.wait()

        self.assertTrue(scheduler.schedule(job(), key="sight:one"))
        self.assertTrue(scheduler.schedule(job(), key="sight:two"))
        self.assertTrue(scheduler.schedule(job(), key="sight:three"))
        rejected = job()
        self.assertFalse(scheduler.schedule(rejected, key="sight:four"))
        self.assertEqual(scheduler.snapshot()["video"]["scheduled"], 3)
        self.assertEqual(scheduler.snapshot()["video"]["capacity"], 3)

        release.set()
        await asyncio.gather(*list(scheduler.tasks))
        self.assertEqual(scheduler.snapshot()["video"]["scheduled"], 0)

    async def test_cancel_all_resets_backlog_accounting(self):
        scheduler = BackgroundTaskScheduler(normal_limit=1, normal_backlog_limit=1)
        release = asyncio.Event()

        async def job():
            await release.wait()

        self.assertTrue(scheduler.schedule(job(), key="normal:one"))
        self.assertTrue(scheduler.schedule(job(), key="normal:two"))
        self.assertEqual(scheduler.snapshot()["normal"]["scheduled"], 2)

        await scheduler.cancel_all()

        self.assertEqual(scheduler.snapshot()["normal"]["scheduled"], 0)
        self.assertEqual(scheduler.tasks, set())
        self.assertEqual(scheduler.keys, set())

    async def test_video_burst_is_bounded_without_starving_event_loop(self):
        scheduler = BackgroundTaskScheduler(video_limit=1, video_backlog_limit=8)
        release = asyncio.Event()
        heartbeat = 0

        async def job():
            await release.wait()

        async def tick():
            nonlocal heartbeat
            for _ in range(5):
                await asyncio.sleep(0)
                heartbeat += 1

        accepted = [
            scheduler.schedule(job(), key=f"sight:burst:{index}")
            for index in range(100)
        ]
        await tick()

        self.assertEqual(sum(accepted), 9)
        self.assertEqual(len(scheduler.tasks), 9)
        self.assertEqual(scheduler.snapshot()["video"]["scheduled"], 9)
        self.assertEqual(heartbeat, 5)

        release.set()
        await asyncio.gather(*list(scheduler.tasks))

    async def test_background_task_slow_completion_is_logged_and_cleaned(self):
        from core.runtime import background as background_module

        infos = []
        old_info = background_module.logger.info
        background_module.logger.info = lambda message, *args, **kwargs: (
            infos.append(str(message))
        )
        try:
            scheduler = BackgroundTaskScheduler(slow_task_seconds=0)

            async def job():
                await asyncio.sleep(0)

            self.assertTrue(scheduler.schedule(job(), label="耗时测试", key="slow-key"))
            await asyncio.gather(*list(scheduler.tasks))
        finally:
            background_module.logger.info = old_info

        self.assertEqual(scheduler.tasks, set())
        self.assertEqual(scheduler.keys, set())
        self.assertTrue(
            any("后台任务完成（耗时测试）" in message for message in infos)
        )
        self.assertTrue(
            any("耗时 " in message and "总耗时" not in message for message in infos)
        )

    def test_background_completion_message_omits_duplicate_total_when_not_queued(self):
        message = BackgroundTaskScheduler._completion_message(
            "图片上下文识别", 6.47, 6.47
        )

        self.assertIn("后台任务完成（图片上下文识别），耗时 6.47 秒。", message)
        self.assertNotIn("总耗时", message)
        self.assertNotIn("排队", message)

    def test_background_completion_message_includes_queue_when_waited(self):
        message = BackgroundTaskScheduler._completion_message(
            "图片上下文识别", 6.47, 7.29
        )

        self.assertIn("执行耗时 6.47 秒", message)
        self.assertIn("排队 0.82 秒", message)
        self.assertIn("总耗时 7.29 秒", message)
