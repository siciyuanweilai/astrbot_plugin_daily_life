import asyncio
import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Callable, TypeVar

from .inventory import CatalogArchiveMixin
from .categories import STORAGE_CATEGORIES
from .promises import CommitmentArchiveMixin
from .common import CommonArchiveMixin
from .journal import DayArchiveMixin
from .experience import ExperienceArchiveMixin
from .reflections import LifecycleArchiveMixin
from .memory import MemoryArchiveMixin
from .schema import init_schema
from .storage import StorageArchiveMixin
from .weeks import WeekArchiveMixin


def builtin_entry_id(value: Any) -> str:
    body = str(value or "").strip()
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]
    return f"builtin_{digest}"


T = TypeVar("T")


class LifeArchive(
    DayArchiveMixin,
    WeekArchiveMixin,
    CatalogArchiveMixin,
    CommitmentArchiveMixin,
    MemoryArchiveMixin,
    ExperienceArchiveMixin,
    LifecycleArchiveMixin,
    StorageArchiveMixin,
    CommonArchiveMixin,
):
    def __init__(self, db_path: Path):
        self._path = Path(db_path)
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        init_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    async def _run_db(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        async with self._lock:
            return await asyncio.to_thread(func, *args, **kwargs)

    async def save(self) -> None:
        def write() -> None:
            self._conn.commit()

        await self._run_db(write)

    async def reset_all(self):
        def write() -> None:
            cleared: set[str] = set()
            for category in STORAGE_CATEGORIES.values():
                for table in category.clear_order:
                    if table in cleared:
                        continue
                    if self._table_exists_unlocked(table):
                        self._conn.execute(f"DELETE FROM {table}")
                    cleared.add(table)
            self._conn.commit()

        await self._run_db(write)
