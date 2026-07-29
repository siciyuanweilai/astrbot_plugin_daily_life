import unittest
from unittest.mock import Mock

from support import LifeSettings

from core.runtime.timer import LifeRhythmClock


class LifeRhythmClockTest(unittest.TestCase):
    @staticmethod
    async def _task() -> None:
        return None

    def _job_ids(self, proactive_config: dict) -> set[str]:
        config = LifeSettings.from_dict(
            {
                "state_config": {"enabled": False},
                "proactive_config": proactive_config,
            }
        )
        clock = LifeRhythmClock(
            config,
            self._task,
            self._task,
            proactive_revisit_task=self._task,
            proactive_idle_task=self._task,
        )

        clock.start()

        self.assertTrue(clock.scheduler.running)
        return set(clock.scheduler.jobs)

    def test_idle_job_stays_off_when_both_chat_switches_are_off(self):
        jobs = self._job_ids(
            {
                "group_enabled": False,
                "private_enabled": False,
            }
        )

        self.assertNotIn("proactive_idle_check", jobs)
        self.assertNotIn("private_revisit_check", jobs)

    def test_idle_reply_uses_per_conversation_tasks_not_periodic_job(self):
        for field in ("group_enabled", "private_enabled"):
            with self.subTest(field=field):
                jobs = self._job_ids({field: True})
                self.assertNotIn("proactive_idle_check", jobs)
                self.assertNotIn("private_revisit_check", jobs)

    def test_private_revisit_job_is_independent_from_idle_reply(self):
        jobs = self._job_ids(
            {
                "group_enabled": False,
                "private_enabled": False,
                "private_revisit_enabled": True,
            }
        )

        self.assertNotIn("proactive_idle_check", jobs)
        self.assertIn("private_revisit_check", jobs)

    def test_scheduler_start_failure_is_propagated(self):
        config = LifeSettings.from_dict({})
        clock = LifeRhythmClock(config, self._task, self._task)
        clock.scheduler.add_job = Mock(side_effect=RuntimeError("无法启动"))

        with self.assertRaisesRegex(RuntimeError, "无法启动"):
            clock.start()

        self.assertFalse(clock.healthy)
        self.assertEqual(clock.last_error, "无法启动")
