from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    value: T
    expires_at: float


class TimedCache(Generic[T]):
    def __init__(self, *, max_items: int, ttl_seconds: int):
        self.max_items = max(int(max_items), 1)
        self.ttl_seconds = max(int(ttl_seconds), 1)
        self._items: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        async with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return entry.value

    async def set(self, key: str, value: T) -> None:
        async with self._lock:
            self._items[key] = CacheEntry(
                value=value,
                expires_at=time.monotonic() + self.ttl_seconds,
            )
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()


class SingleFlight(Generic[T]):
    """合并当前进程内同时发生的相同异步请求，不保留完成结果。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[T]] = {}

    async def run(
        self, key: str, factory: Callable[[], Awaitable[T]]
    ) -> tuple[T, bool]:
        task_key = str(key or "").strip()
        if not task_key:
            return await factory(), False
        task = self._tasks.get(task_key)
        shared = task is not None and not task.done()
        if not shared:
            task = asyncio.create_task(factory())
            self._tasks[task_key] = task
            task.add_done_callback(lambda done: self._forget(task_key, done))
        return await asyncio.shield(task), shared

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _forget(self, key: str, task: asyncio.Task[T]) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
