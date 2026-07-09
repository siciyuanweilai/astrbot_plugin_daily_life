from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable

from astrbot.api import logger

from .markers import LOG_PREFIX

_TASK_RUN_SECONDS_ATTR = "_daily_life_run_seconds"
_TASK_TOTAL_SECONDS_ATTR = "_daily_life_total_seconds"
QUEUE_LOG_SECONDS = 0.05


class BackgroundTaskScheduler:
    def __init__(
        self,
        *,
        normal_limit: int = 4,
        chat_limit: int = 1,
        chat_label: str = "聊天记忆提炼",
        slow_task_seconds: float = 30.0,
    ):
        self.tasks: set[asyncio.Task] = set()
        self.keys: set[str] = set()
        self.normal_gate = asyncio.Semaphore(normal_limit)
        self.chat_gate = asyncio.Semaphore(chat_limit)
        self.chat_label = chat_label
        self.slow_task_seconds = max(0.0, float(slow_task_seconds or 0.0))

    async def cancel_all(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.keys.clear()

    def schedule(self, coro: Awaitable, *, label: str = "", key: str = "") -> bool:
        task_name = self._task_name(label, key)
        if key:
            if key in self.keys:
                self._close_coro(coro)
                logger.debug(f"{LOG_PREFIX} 后台任务已跳过重复调度（{task_name}）。")
                return False
            self.keys.add(key)

        async def runner() -> None:
            started = False
            scheduled_at = time.monotonic()
            run_started_at = scheduled_at
            try:
                async with self.gate(label):
                    started = True
                    run_started_at = time.monotonic()
                    logger.debug(f"{LOG_PREFIX} 后台任务开始（{task_name}）。")
                    await coro
            finally:
                current = asyncio.current_task()
                if current is not None and started:
                    finished_at = time.monotonic()
                    setattr(current, _TASK_RUN_SECONDS_ATTR, finished_at - run_started_at)
                    setattr(current, _TASK_TOTAL_SECONDS_ATTR, finished_at - scheduled_at)
                if not started:
                    self._close_coro(coro)

        task = asyncio.create_task(runner())
        if label:
            try:
                task.set_name(label)
            except Exception:
                pass
        self.tasks.add(task)
        task.add_done_callback(lambda done_task: self._on_done(done_task, label, key))
        return True

    def gate(self, label: str = "") -> asyncio.Semaphore:
        if label == self.chat_label:
            return self.chat_gate
        return self.normal_gate

    def _on_done(self, done_task: asyncio.Task, label: str, key: str) -> None:
        self.tasks.discard(done_task)
        if key:
            self.keys.discard(key)
        task_name = self._task_name(label, key)
        try:
            done_task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 后台任务失败（{task_name}）：{exc}")
        else:
            run_seconds = float(getattr(done_task, _TASK_RUN_SECONDS_ATTR, 0.0) or 0.0)
            total_seconds = float(getattr(done_task, _TASK_TOTAL_SECONDS_ATTR, run_seconds) or run_seconds)
            message = self._completion_message(task_name, run_seconds, total_seconds)
            if run_seconds >= self.slow_task_seconds:
                logger.warning(message)
            else:
                logger.debug(message)

    @staticmethod
    def _task_name(label: str, key: str) -> str:
        return str(label or key or "未命名").strip() or "未命名"

    @staticmethod
    def _close_coro(coro: Awaitable) -> None:
        close = getattr(coro, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _completion_message(task_name: str, run_seconds: float, total_seconds: float) -> str:
        run_seconds = max(0.0, float(run_seconds or 0.0))
        total_seconds = max(run_seconds, float(total_seconds or run_seconds))
        queue_seconds = max(0.0, total_seconds - run_seconds)
        if queue_seconds >= QUEUE_LOG_SECONDS:
            return (
                f"{LOG_PREFIX} 后台任务完成（{task_name}），"
                f"执行耗时 {run_seconds:.2f}s，排队 {queue_seconds:.2f}s，总耗时 {total_seconds:.2f}s。"
            )
        return f"{LOG_PREFIX} 后台任务完成（{task_name}），耗时 {run_seconds:.2f}s。"


class BackgroundTaskMixin:
    def _init_background_tasks(self) -> None:
        self._background_scheduler = BackgroundTaskScheduler()

    async def _cancel_background_tasks(self) -> None:
        await self._background_scheduler_for_runtime().cancel_all()

    def _schedule_background_task(self, coro: Awaitable, label: str = "", key: str = "") -> bool:
        scheduler = self._background_scheduler_for_runtime()
        return scheduler.schedule(coro, label=label, key=key)

    def _background_gate_for_label(self, label: str = "") -> asyncio.Semaphore:
        return self._background_scheduler_for_runtime().gate(label)

    def _background_scheduler_for_runtime(self) -> BackgroundTaskScheduler:
        scheduler = getattr(self, "_background_scheduler", None)
        if isinstance(scheduler, BackgroundTaskScheduler):
            return scheduler
        scheduler = BackgroundTaskScheduler()
        self._background_scheduler = scheduler
        return scheduler
