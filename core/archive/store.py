import asyncio
import contextvars
import sqlite3
from pathlib import Path
from typing import Any, Callable, TypeVar

from .categories import STORAGE_CATEGORIES
from .queue import ChatMemoryQueueArchiveMixin
from .promises import CommitmentArchiveMixin
from .common import CommonArchiveMixin
from .journal import DayArchiveMixin
from .gallery import MediaArchiveMixin
from .experience import ExperienceArchiveMixin
from .reflections import LifecycleArchiveMixin
from .memory import MemoryArchiveMixin
from .schema import init_schema
from .storage import StorageArchiveMixin
from .weeks import WeekArchiveMixin
from .vectors import MemoryVectorArchiveMixin


T = TypeVar("T")
_DIRECT_DB_READ = contextvars.ContextVar("daily_life_direct_db_read", default=False)

_CONTEXT_SNAPSHOT_KEYS = (
    "relationships",
    "places",
    "events",
    "summaries",
    "commitments",
    "environments",
    "decisions",
    "visibility",
    "episodes",
    "focus_targets",
    "feedback",
    "emotion_arcs",
    "physiological_rhythm_logs",
    "physiological_rhythm_trend",
    "reply_effects",
    "memory_corrections",
    "expression_profiles",
    "expression_reviews",
    "behavior_patterns",
    "behavior_scenes",
    "mid_summaries",
    "temporary_expression_states",
    "focus_slots",
    "expression_intents",
    "terms",
    "boundaries",
)


class LifeArchive(
    MemoryVectorArchiveMixin,
    ChatMemoryQueueArchiveMixin,
    DayArchiveMixin,
    WeekArchiveMixin,
    CommitmentArchiveMixin,
    MemoryArchiveMixin,
    ExperienceArchiveMixin,
    MediaArchiveMixin,
    LifecycleArchiveMixin,
    StorageArchiveMixin,
    CommonArchiveMixin,
):
    def __init__(self, db_path: Path, *, initialize: bool = True):
        self._path = Path(db_path)
        self._lock = asyncio.Lock()
        self._physiological_rhythm_trend_revision = 0
        self._physiological_rhythm_trend_cache: dict[str, dict] = {}
        self._conn: sqlite3.Connection | None = None
        self._initialized = False
        if initialize:
            self._initialize_sync()

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            init_schema(conn)
        except Exception:
            conn.close()
            raise
        self._conn = conn
        self._initialized = True

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if not self._initialized:
                await asyncio.to_thread(self._initialize_sync)

    def close(self) -> None:
        conn = self._conn
        self._conn = None
        self._initialized = False
        if conn is not None:
            conn.close()

    async def aclose(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self.close)

    async def _run_db(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if _DIRECT_DB_READ.get():
            return func(*args, **kwargs)
        if not self._initialized:
            await self.initialize()
        async with self._lock:
            return await asyncio.to_thread(func, *args, **kwargs)

    async def save(self) -> None:
        def write() -> None:
            self._conn.commit()

        await self._run_db(write)

    async def get_context_snapshot(
        self,
        *,
        max_summaries: int,
        experience_scope: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        """在一次数据库锁定期间读取完整的提示词注入快照。"""

        async def collect() -> dict[str, Any]:
            values = await asyncio.gather(
                self.get_recent_relationships(8),
                self.get_recent_places(10),
                self.get_recent_events(10),
                self.get_recent_chat_summaries(max_summaries),
                self.get_commitments(status="active", limit=8),
                self.get_recent_group_environments(3),
                self.get_recent_action_decisions(3),
                self.get_recent_message_visibility(3),
                self.get_life_episodes(limit=3),
                self.get_focus_targets(limit=4),
                self.get_behavior_feedback(limit=3),
                self.get_emotion_arcs(limit=4, scope=experience_scope),
                self.get_physiological_rhythm_logs(limit=3),
                self.get_physiological_rhythm_trend(days=7, limit=6),
                self.get_reply_effects(limit=4, scope=experience_scope),
                self.get_memory_corrections(limit=3, unapplied_only=True),
                self.get_expression_profiles(limit=4),
                self.get_expression_reviews(limit=3, scope=experience_scope),
                self.get_behavior_patterns(limit=4),
                self.get_behavior_scenes(limit=4, scope=experience_scope),
                self.get_session_mid_summaries(limit=3, session_id=session_id),
                self.get_temporary_expression_states(
                    limit=3, scope=experience_scope
                ),
                self.get_focus_slots(limit=4, scope=experience_scope),
                self.get_expression_intents(limit=3, scope=experience_scope),
                self.get_life_terms(limit=6, scope=experience_scope),
                self.get_memory_boundaries(limit=4),
            )
            return dict(zip(_CONTEXT_SNAPSHOT_KEYS, values))

        def read() -> dict[str, Any]:
            token = _DIRECT_DB_READ.set(True)
            try:
                return asyncio.run(collect())
            finally:
                _DIRECT_DB_READ.reset(token)

        return await self._run_db(read)

    async def reset_all(self):
        def write() -> None:
            cleared: set[str] = set()
            for category in STORAGE_CATEGORIES.values():
                for table in category.clear_order:
                    if table in cleared:
                        continue
                    if self._table_exists_unlocked(table):
                        cursor = self._conn.execute(f"DELETE FROM {table}")
                        if (
                            table == "physiological_rhythm_logs"
                            and cursor.rowcount
                            and cursor.rowcount > 0
                        ):
                            self._invalidate_physiological_rhythm_trend_cache()
                    cleared.add(table)
            self._conn.commit()

        await self._run_db(write)
