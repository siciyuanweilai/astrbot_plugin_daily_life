from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable

from .clip import SightClip, SightInsight
from .probe import clean_source


def sight_flight_key(clip: SightClip) -> str:
    media = clip.file_id or clean_source(clip.source) or clip.name or clip.key
    raw = "|".join((clip.scope, media))
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


class SightFlight:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[SightInsight]] = {}

    async def run(self, key: str, factory: Callable[[], Awaitable[SightInsight]]) -> SightInsight:
        task_key = str(key or "").strip()
        if not task_key:
            return await factory()
        task = self._tasks.get(task_key)
        if task is None or task.done():
            task = asyncio.create_task(factory())
            self._tasks[task_key] = task
            task.add_done_callback(lambda done: self._forget(task_key, done))
        return await asyncio.shield(task)

    def _forget(self, key: str, task: asyncio.Task[SightInsight]) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
