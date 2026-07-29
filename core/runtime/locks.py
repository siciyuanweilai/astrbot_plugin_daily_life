from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _OperationLockEntry:
    lock: asyncio.Lock
    users: int = 0


class _OperationLockLease:
    def __init__(self, owner: Any, key: str, entry: _OperationLockEntry) -> None:
        self.owner = owner
        self.key = key
        self.entry = entry
        self.acquired = False

    async def __aenter__(self) -> asyncio.Lock:
        try:
            await self.entry.lock.acquire()
        except BaseException:
            self._release_reference()
            raise
        self.acquired = True
        return self.entry.lock

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        if self.acquired:
            self.entry.lock.release()
            self.acquired = False
        self._release_reference()

    def _release_reference(self) -> None:
        self.entry.users = max(0, self.entry.users - 1)
        locks = getattr(self.owner, "_operation_locks", None)
        if (
            isinstance(locks, dict)
            and self.entry.users == 0
            and not self.entry.lock.locked()
            and locks.get(self.key) is self.entry
        ):
            locks.pop(self.key, None)


def operation_lock(owner: Any, key: str) -> _OperationLockLease:
    locks = getattr(owner, "_operation_locks", None)
    if not isinstance(locks, dict):
        locks = {}
        owner._operation_locks = locks
    normalized = str(key or "default").strip() or "default"
    entry = locks.get(normalized)
    if not isinstance(entry, _OperationLockEntry):
        entry = _OperationLockEntry(asyncio.Lock())
        locks[normalized] = entry
    entry.users += 1
    return _OperationLockLease(owner, normalized, entry)


__all__ = ["operation_lock"]
