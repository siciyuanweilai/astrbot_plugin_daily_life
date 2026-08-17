import asyncio
import datetime
import types
import unittest

import support  # noqa: F401 - 安装轻量级 AstrBot 测试替身
from core.runtime.generation import DailyGenerationBusy, DailyGenerationMixin
from core.runtime.spine.pulse import SpinePulseMixin


class GenerationArchive:
    def __init__(self):
        self.day = None
        self.deleted_dates = []

    async def get_day(self, date_str):
        if self.day is not None and self.day.date == date_str:
            return self.day
        return None

    async def delete_day(self, date_str):
        self.deleted_dates.append(date_str)
        if self.day is not None and self.day.date == date_str:
            self.day = None

    async def mutate_day(self, date_str, mutator):
        if self.day is None or self.day.date != date_str:
            return None
        mutator(self.day)
        return self.day

    async def cleanup_by_storage_policy(self, _storage):
        return []


class GenerationComposer:
    def __init__(self, archive):
        self.archive = archive
        self.search_started = asyncio.Event()
        self.allow_search = asyncio.Event()
        self.review_started = asyncio.Event()
        self.allow_review = asyncio.Event()
        self.search_calls = []
        self.generate_calls = []
        self.review_calls = []
        self.search = types.SimpleNamespace(inspiration=self.inspiration)

    async def inspiration(self, keyword, prompt, **kwargs):
        self.search_calls.append((keyword, prompt, kwargs))
        self.search_started.set()
        await self.allow_search.wait()
        return "联网参考"

    async def generate_daily(self, date=None, force=False, **kwargs):
        self.generate_calls.append((date, force, kwargs))
        self.archive.day = types.SimpleNamespace(
            date=date.strftime("%Y-%m-%d"),
            timeline=[types.SimpleNamespace(time="10:00")],
            meta={},
        )
        return self.archive.day

    async def compose_daily_review(self, date):
        self.review_calls.append(date)
        self.review_started.set()
        await self.allow_review.wait()


class GenerationRuntime(DailyGenerationMixin, SpinePulseMixin):
    def __init__(self):
        self.generation_lock = asyncio.Lock()
        self._init_daily_generation_state()
        self.archive = GenerationArchive()
        self.composer = GenerationComposer(self.archive)
        self.config = types.SimpleNamespace(storage=types.SimpleNamespace())
        self.failed_dates = {}
        self.status_reasons = []

    async def get_persona_text(self, scope=""):
        return "测试人格"

    async def mark_page_status_changed(self, reason=""):
        self.status_reasons.append(reason)
        return len(self.status_reasons)

    async def resolve_injection_target(self, now):
        return "2026-07-14", False

    @staticmethod
    def _target_datetime_for_command(date_str, now):
        target = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return target.replace(hour=now.hour, minute=now.minute, second=now.second)

    async def maintain_sight_cache(self):
        return None

    async def maintain_emoji_assets(self):
        return None

    async def maintain_plugin_file_cache(self):
        return None


class DailyGenerationTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_forced_generation_keeps_existing_day(self):
        runtime = GenerationRuntime()
        existing = types.SimpleNamespace(
            date="2026-07-14",
            timeline=[types.SimpleNamespace(time="08:00", activity="原有日程")],
        )
        runtime.archive.day = existing

        async def fail_generation(**kwargs):
            raise RuntimeError("模型暂时不可用")

        runtime.composer.generate_daily = fail_generation
        with self.assertRaisesRegex(RuntimeError, "模型暂时不可用"):
            await runtime.run_daily_generation(
                date=datetime.datetime(2026, 7, 14, 16, 30),
                source="command_reset",
                force=True,
                delete_existing=True,
            )

        self.assertIs(runtime.archive.day, existing)
        self.assertEqual(runtime.archive.deleted_dates, [])

    async def test_daily_generation_does_not_wait_for_plugin_global_lock(self):
        runtime = GenerationRuntime()
        await runtime.generation_lock.acquire()
        try:
            result = await asyncio.wait_for(
                runtime.run_daily_generation(
                    date=datetime.datetime(2026, 7, 14, 16, 30),
                    source="dashboard_reset",
                    force=True,
                ),
                timeout=0.5,
            )
        finally:
            runtime.generation_lock.release()

        self.assertEqual(result.day.date, "2026-07-14")
        self.assertEqual(len(runtime.composer.generate_calls), 1)

    async def test_manual_reset_marks_existing_day_as_replacement(self):
        runtime = GenerationRuntime()
        runtime.archive.day = types.SimpleNamespace(
            date="2026-07-14",
            outfit="米白衬衫和浅蓝短裙",
            timeline=[types.SimpleNamespace(time="08:00", activity="原有日程")],
        )

        result = await runtime.run_daily_generation(
            date=datetime.datetime(2026, 7, 14, 16, 30),
            source="dashboard_reset",
            force=True,
        )

        self.assertEqual(result.day.date, "2026-07-14")
        self.assertTrue(runtime.composer.generate_calls[0][2]["regenerate_existing"])

    async def test_same_date_manual_reset_has_one_search_and_one_writer(self):
        runtime = GenerationRuntime()
        date = datetime.datetime(2026, 7, 14, 16, 30)
        first = asyncio.create_task(
            runtime.run_daily_generation(
                date=date,
                source="dashboard_reset",
                force=True,
                use_web=True,
                reject_if_busy=True,
            )
        )
        await asyncio.wait_for(runtime.composer.search_started.wait(), timeout=1)

        with self.assertRaises(DailyGenerationBusy):
            await runtime.run_daily_generation(
                date=date,
                source="dashboard_reset",
                force=True,
                use_web=True,
                reject_if_busy=True,
            )

        runtime.composer.allow_search.set()
        result = await asyncio.wait_for(first, timeout=1)
        self.assertEqual(len(runtime.composer.search_calls), 1)
        self.assertEqual(len(runtime.composer.generate_calls), 1)
        self.assertEqual(
            runtime.composer.search_calls[0][2]["trace_id"],
            result.operation_id,
        )
        self.assertEqual(
            runtime.daily_generation_status("2026-07-14")["phase"], "completed"
        )

    async def test_cancelled_frontend_wait_does_not_cancel_generation(self):
        runtime = GenerationRuntime()
        date = datetime.datetime(2026, 7, 14, 16, 30)
        caller = asyncio.create_task(
            runtime.run_daily_generation(
                date=date,
                source="dashboard_reset",
                force=True,
                use_web=True,
            )
        )
        await asyncio.wait_for(runtime.composer.search_started.wait(), timeout=1)
        backend_task = runtime._daily_generation_active["2026-07-14"].task
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertFalse(backend_task.cancelled())
        self.assertTrue(runtime.daily_generation_status("2026-07-14")["running"])

        runtime.composer.allow_search.set()
        await asyncio.wait_for(backend_task, timeout=1)
        status = runtime.daily_generation_status("2026-07-14")
        self.assertFalse(status["running"])
        self.assertEqual(status["phase"], "completed")

    async def test_scheduled_refresh_reserves_date_before_review(self):
        runtime = GenerationRuntime()
        refresh = asyncio.create_task(runtime.run_daily_refresh())
        await asyncio.wait_for(runtime.composer.review_started.wait(), timeout=1)

        status = runtime.daily_generation_status("2026-07-14")
        self.assertTrue(status["running"])
        with self.assertRaises(DailyGenerationBusy):
            await runtime.run_daily_generation(
                date=datetime.datetime(2026, 7, 14, 16, 30),
                source="dashboard_reset",
                force=True,
                reject_if_busy=True,
            )

        runtime.composer.allow_review.set()
        await asyncio.wait_for(refresh, timeout=1)
        self.assertEqual(len(runtime.composer.review_calls), 1)
        self.assertEqual(len(runtime.composer.generate_calls), 1)

    async def test_scheduled_refresh_retry_reuses_persisted_generation(self):
        runtime = GenerationRuntime()
        runtime.composer.allow_review.set()

        await runtime.run_daily_refresh()
        await runtime.run_daily_refresh()

        self.assertEqual(len(runtime.composer.generate_calls), 1)
        self.assertEqual(len(runtime.composer.review_calls), 2)
        self.assertEqual(
            runtime.archive.day.meta["daily_refresh_generated_date"],
            "2026-07-14",
        )


if __name__ == "__main__":
    unittest.main()
