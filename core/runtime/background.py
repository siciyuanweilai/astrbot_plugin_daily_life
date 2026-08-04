from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from astrbot.api import logger

from .markers import LOG_PREFIX

_TASK_RUN_SECONDS_ATTR = "_daily_life_run_seconds"
_TASK_TOTAL_SECONDS_ATTR = "_daily_life_total_seconds"
_TASK_CATEGORY_ATTR = "_daily_life_category"
_TASK_SOURCE_CORO_ATTR = "_daily_life_source_coro"
_TASK_STARTED_ATTR = "_daily_life_started"
QUEUE_LOG_SECONDS = 0.05
OVERFLOW_LOG_INTERVAL_SECONDS = 30.0
_CATEGORY_LABELS = {
    "normal": "普通后台任务",
    "chat": "聊天记忆",
    "video": "视频任务",
    "vision": "图片识别",
}


class BackgroundTaskScheduler:
    def __init__(
        self,
        *,
        normal_limit: int = 4,
        chat_limit: int = 1,
        video_limit: int = 1,
        vision_limit: int = 2,
        normal_backlog_limit: int = 64,
        chat_backlog_limit: int = 32,
        video_backlog_limit: int = 8,
        vision_backlog_limit: int = 16,
        chat_label: str = "聊天记忆提炼",
        slow_task_seconds: float = 30.0,
    ):
        self.tasks: set[asyncio.Task] = set()
        self.keys: set[str] = set()
        self.normal_gate = asyncio.Semaphore(normal_limit)
        self.chat_gate = asyncio.Semaphore(chat_limit)
        self.video_gate = asyncio.Semaphore(video_limit)
        self.vision_gate = asyncio.Semaphore(vision_limit)
        self._concurrency_limits = {
            "normal": max(1, int(normal_limit)),
            "chat": max(1, int(chat_limit)),
            "video": max(1, int(video_limit)),
            "vision": max(1, int(vision_limit)),
        }
        self._backlog_limits = {
            "normal": max(0, int(normal_backlog_limit)),
            "chat": max(0, int(chat_backlog_limit)),
            "video": max(0, int(video_backlog_limit)),
            "vision": max(0, int(vision_backlog_limit)),
        }
        self._scheduled_counts = dict.fromkeys(self._concurrency_limits, 0)
        self._dropped_counts = dict.fromkeys(self._concurrency_limits, 0)
        self._completed_counts = dict.fromkeys(self._concurrency_limits, 0)
        self._failed_counts = dict.fromkeys(self._concurrency_limits, 0)
        self._overflow_logged_at: dict[str, float] = {}
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
        self._scheduled_counts = dict.fromkeys(self._concurrency_limits, 0)
        self._dropped_counts = dict.fromkeys(self._concurrency_limits, 0)
        self._completed_counts = dict.fromkeys(self._concurrency_limits, 0)
        self._failed_counts = dict.fromkeys(self._concurrency_limits, 0)
        self._overflow_logged_at.clear()

    def schedule(
        self,
        coro: Awaitable,
        *,
        label: str = "",
        key: str = "",
        category: str = "",
        lease_factory: Callable[[], AbstractAsyncContextManager] | None = None,
    ) -> bool:
        task_name = self._task_name(label, key)
        category = self._normalize_category(category, label=label, key=key)
        if not self._accept_schedule(
            coro,
            task_name=task_name,
            key=key,
            category=category,
        ):
            return False
        if key:
            self.keys.add(key)

        try:
            task = asyncio.create_task(
                self._run_scheduled(
                    coro,
                    task_name=task_name,
                    label=label,
                    category=category,
                    lease_factory=lease_factory,
                )
            )
        except Exception:
            self._release(category)
            if key:
                self.keys.discard(key)
            self._close_coro(coro)
            raise
        setattr(task, _TASK_CATEGORY_ATTR, category)
        setattr(task, _TASK_SOURCE_CORO_ATTR, coro)
        setattr(task, _TASK_STARTED_ATTR, False)
        if label:
            task.set_name(label)
        self.tasks.add(task)
        task.add_done_callback(lambda done_task: self._on_done(done_task, label, key))
        return True

    def _accept_schedule(
        self,
        coro: Awaitable,
        *,
        task_name: str,
        key: str,
        category: str,
    ) -> bool:
        if key and key in self.keys:
            self._close_coro(coro)
            logger.debug(f"{LOG_PREFIX} 后台任务已跳过重复调度（{task_name}）。")
            return False
        if self._reserve(category):
            return True
        self._dropped_counts[category] = int(self._dropped_counts.get(category, 0)) + 1
        self._close_coro(coro)
        self._log_overflow(category, task_name)
        return False

    async def _run_scheduled(
        self,
        coro: Awaitable,
        *,
        task_name: str,
        label: str,
        category: str,
        lease_factory: Callable[[], AbstractAsyncContextManager] | None,
    ) -> None:
        started = False
        scheduled_at = time.monotonic()
        run_started_at = scheduled_at

        async def run_body() -> None:
            nonlocal started, run_started_at
            started = True
            current = asyncio.current_task()
            if current is not None:
                setattr(current, _TASK_STARTED_ATTR, True)
            run_started_at = time.monotonic()
            logger.debug(f"{LOG_PREFIX} 后台任务开始（{task_name}）。")
            await coro

        try:
            async with self.gate(label, category=category):
                if callable(lease_factory):
                    async with lease_factory():
                        await run_body()
                else:
                    await run_body()
        finally:
            current = asyncio.current_task()
            if current is not None and started:
                finished_at = time.monotonic()
                setattr(current, _TASK_RUN_SECONDS_ATTR, finished_at - run_started_at)
                setattr(current, _TASK_TOTAL_SECONDS_ATTR, finished_at - scheduled_at)
            if not started:
                self._close_coro(coro)

    def gate(self, label: str = "", *, category: str = "") -> asyncio.Semaphore:
        if category == "video":
            return self.video_gate
        if category == "vision":
            return self.vision_gate
        if category == "chat" or label == self.chat_label:
            return self.chat_gate
        return self.normal_gate

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {
            category: {
                "scheduled": int(self._scheduled_counts.get(category, 0)),
                "running_limit": self._concurrency_limits[category],
                "backlog_limit": self._backlog_limits[category],
                "capacity": self._capacity(category),
                "dropped": int(self._dropped_counts.get(category, 0)),
                "completed": int(self._completed_counts.get(category, 0)),
                "failed": int(self._failed_counts.get(category, 0)),
            }
            for category in self._concurrency_limits
        }

    def _normalize_category(self, category: str, *, label: str, key: str) -> str:
        value = str(category or "").strip().lower()
        if value in self._concurrency_limits:
            return value
        if label == self.chat_label:
            return "chat"
        inferred = self._category_for_key(key)
        return inferred if inferred in self._concurrency_limits else "normal"

    def _capacity(self, category: str) -> int:
        return self._concurrency_limits[category] + self._backlog_limits[category]

    def _reserve(self, category: str) -> bool:
        scheduled = int(self._scheduled_counts.get(category, 0))
        if scheduled >= self._capacity(category):
            return False
        self._scheduled_counts[category] = scheduled + 1
        return True

    def _release(self, category: str) -> None:
        scheduled = int(self._scheduled_counts.get(category, 0))
        self._scheduled_counts[category] = max(0, scheduled - 1)

    def _log_overflow(self, category: str, task_name: str) -> None:
        now = time.monotonic()
        previous = float(self._overflow_logged_at.get(category, 0.0) or 0.0)
        if now - previous < OVERFLOW_LOG_INTERVAL_SECONDS:
            return
        self._overflow_logged_at[category] = now
        category_label = _CATEGORY_LABELS.get(category, "后台任务")
        logger.warning(
            f"{LOG_PREFIX} 后台任务已丢弃（{task_name}）："
            f"{category_label}队列已满，容量={self._capacity(category)}。"
        )

    @staticmethod
    def _category_for_key(key: str) -> str:
        value = str(key or "")
        if value.startswith(
            ("sight:", "bili:", "life_video:", "life_suite:", "photo_suite:")
        ):
            return "video"
        if value.startswith(
            ("visual_context:", "emoji_capture:", "emoji_asset_vision:")
        ):
            return "vision"
        return "normal"

    def _on_done(self, done_task: asyncio.Task, label: str, key: str) -> None:
        self.tasks.discard(done_task)
        if key:
            self.keys.discard(key)
        category = str(getattr(done_task, _TASK_CATEGORY_ATTR, "normal") or "normal")
        self._release(category)
        task_name = self._task_name(label, key)
        try:
            done_task.result()
        except asyncio.CancelledError:
            if not bool(getattr(done_task, _TASK_STARTED_ATTR, False)):
                source_coro = getattr(done_task, _TASK_SOURCE_CORO_ATTR, None)
                if source_coro is not None:
                    self._close_coro(source_coro)
        except Exception as exc:
            self._failed_counts[category] = (
                int(self._failed_counts.get(category, 0)) + 1
            )
            logger.warning(f"{LOG_PREFIX} 后台任务失败（{task_name}）：{exc}")
        else:
            self._completed_counts[category] = (
                int(self._completed_counts.get(category, 0)) + 1
            )
            run_seconds = float(getattr(done_task, _TASK_RUN_SECONDS_ATTR, 0.0) or 0.0)
            total_seconds = float(
                getattr(done_task, _TASK_TOTAL_SECONDS_ATTR, run_seconds) or run_seconds
            )
            message = self._completion_message(task_name, run_seconds, total_seconds)
            if run_seconds >= self.slow_task_seconds:
                logger.info(message)
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
    def _completion_message(
        task_name: str, run_seconds: float, total_seconds: float
    ) -> str:
        run_seconds = max(0.0, float(run_seconds or 0.0))
        total_seconds = max(run_seconds, float(total_seconds or run_seconds))
        queue_seconds = max(0.0, total_seconds - run_seconds)
        if queue_seconds >= QUEUE_LOG_SECONDS:
            return (
                f"{LOG_PREFIX} 后台任务完成（{task_name}），"
                f"执行耗时 {run_seconds:.2f} 秒，排队 {queue_seconds:.2f} 秒，总耗时 {total_seconds:.2f} 秒。"
            )
        return f"{LOG_PREFIX} 后台任务完成（{task_name}），耗时 {run_seconds:.2f} 秒。"


class BackgroundTaskMixin:
    def _init_background_tasks(self) -> None:
        self._background_scheduler = BackgroundTaskScheduler()

    async def _cancel_background_tasks(self) -> None:
        await self._background_scheduler_for_runtime().cancel_all()

    def _schedule_background_task(
        self,
        coro: Awaitable,
        label: str = "",
        key: str = "",
        category: str = "",
    ) -> bool:
        scheduler = self._background_scheduler_for_runtime()
        lease_factory = getattr(self, "runtime_service_lease", None)
        return scheduler.schedule(
            coro,
            label=label,
            key=key,
            category=category,
            lease_factory=lease_factory if callable(lease_factory) else None,
        )

    def _background_gate_for_label(self, label: str = "") -> asyncio.Semaphore:
        return self._background_scheduler_for_runtime().gate(label)

    def _background_scheduler_for_runtime(self) -> BackgroundTaskScheduler:
        scheduler = getattr(self, "_background_scheduler", None)
        if isinstance(scheduler, BackgroundTaskScheduler):
            return scheduler
        scheduler = BackgroundTaskScheduler()
        self._background_scheduler = scheduler
        return scheduler
