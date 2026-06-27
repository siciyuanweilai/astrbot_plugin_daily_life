from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from astrbot.api import logger

from .markers import LOG_PREFIX


class BackgroundTaskScheduler:
    def __init__(
        self,
        *,
        normal_limit: int = 4,
        chat_limit: int = 1,
        chat_label: str = "聊天记忆提炼",
    ):
        self.tasks: set[asyncio.Task] = set()
        self.keys: set[str] = set()
        self.normal_gate = asyncio.Semaphore(normal_limit)
        self.chat_gate = asyncio.Semaphore(chat_limit)
        self.chat_label = chat_label

    async def cancel_all(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.keys.clear()

    def schedule(self, coro: Awaitable, *, label: str = "", key: str = "") -> bool:
        if key:
            if key in self.keys:
                self._close_coro(coro)
                return False
            self.keys.add(key)

        async def runner() -> None:
            started = False
            try:
                async with self.gate(label):
                    started = True
                    await coro
            finally:
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
        try:
            done_task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 后台任务失败（{label or key or '未命名'}）：{exc}")

    @staticmethod
    def _close_coro(coro: Awaitable) -> None:
        close = getattr(coro, "close", None)
        if callable(close):
            close()


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
