from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .clip import SightClip
from .probe import clean_source


def sight_resource_keys(clip: SightClip) -> tuple[str, ...]:
    metadata = clip.metadata if isinstance(clip.metadata, dict) else {}
    file_size = str(metadata.get("file_size") or "").strip()
    identities = (
        ("content", str(metadata.get("content_fingerprint") or "").strip()),
        ("platform", str(metadata.get("platform_id") or "").strip()),
        ("name_size", f"{clip.name}|{file_size}" if clip.name and file_size else ""),
        ("file", str(clip.file_id or "").strip()),
        ("source", clean_source(clip.source)),
        ("name", str(clip.name or "").strip() if not file_size else ""),
    )
    result: list[str] = []
    for kind, identity in identities:
        if not identity:
            continue
        raw = "|".join((str(clip.scope or "").strip(), kind, identity))
        key = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
        if key not in result:
            result.append(key)
    return tuple(result)


def sight_flight_key(clip: SightClip) -> str:
    keys = sight_resource_keys(clip)
    return keys[0] if keys else clip.key


def sight_prepare_key(clip: SightClip) -> str:
    """生成与会话范围无关的物理媒体准备键。"""

    metadata = clip.metadata if isinstance(clip.metadata, dict) else {}
    identity = str(
        metadata.get("content_fingerprint")
        or metadata.get("platform_id")
        or clip.source
        or clip.file_id
        or clip.name
        or clip.key
    ).strip()
    return hashlib.sha256(f"prepare|{identity}".encode("utf-8", errors="ignore")).hexdigest()


def sight_resource_matches(first: SightClip, second: SightClip) -> bool:
    first_fingerprint = str(
        (first.metadata or {}).get("content_fingerprint") or ""
    ).strip()
    second_fingerprint = str(
        (second.metadata or {}).get("content_fingerprint") or ""
    ).strip()
    if first_fingerprint and second_fingerprint:
        return first.scope == second.scope and first_fingerprint == second_fingerprint
    return bool(
        set(sight_resource_keys(first)).intersection(sight_resource_keys(second))
    )


_FlightValue = TypeVar("_FlightValue")


class SightFlight:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def run(
        self, key: str, factory: Callable[[], Awaitable[_FlightValue]]
    ) -> _FlightValue:
        task_key = str(key or "").strip()
        if not task_key:
            return await factory()
        task = self._tasks.get(task_key)
        if task is None or task.done():
            task = asyncio.create_task(factory())
            self._tasks[task_key] = task
            task.add_done_callback(lambda done: self._forget(task_key, done))
        return await asyncio.shield(task)

    async def cancel_all(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def close(self) -> None:
        await self.cancel_all()

    def _forget(self, key: str, task: asyncio.Task[Any]) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
