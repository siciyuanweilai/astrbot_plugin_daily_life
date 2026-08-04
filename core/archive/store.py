import asyncio
import contextvars
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from .categories import STORAGE_CATEGORIES
from .cognition import CognitionArchiveMixin
from .common import CommonArchiveMixin
from .domains import DomainArchiveMixin
from .experience import ExperienceArchiveMixin
from .gallery import MediaArchiveMixin
from .journal import DayArchiveMixin
from .memory import MemoryArchiveMixin
from .promises import CommitmentArchiveMixin
from .queue import ChatMemoryQueueArchiveMixin
from .reflections import LifecycleArchiveMixin
from .schema import init_schema
from .storage import StorageArchiveMixin
from .vectors import MemoryVectorArchiveMixin
from .weeks import WeekArchiveMixin

T = TypeVar("T")
_DB_LOCK_HELD = contextvars.ContextVar("daily_life_db_lock_held", default=False)

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
    "temporal_facts",
    "persona_assertions",
    "scoped_persona_assertions",
    "affective_states",
    "scoped_affective_states",
    "grounded_diary_entries",
)


class LifeArchive(
    DomainArchiveMixin,
    CognitionArchiveMixin,
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
        self._closed = False
        if initialize:
            self._initialize_sync()

    def _initialize_sync(self) -> None:
        self._require_not_closed()
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
        self._require_not_closed()
        if self._initialized:
            return
        async with self._lock:
            self._require_not_closed()
            if not self._initialized:
                await asyncio.to_thread(self._initialize_sync)

    def close(self) -> None:
        self._closed = True
        conn = self._conn
        self._conn = None
        self._initialized = False
        if conn is not None:
            conn.close()

    async def aclose(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self.close)

    def _require_not_closed(self) -> None:
        """拒绝在归档关闭后继续复用实例。

        Raises:
            RuntimeError: 归档已经关闭。
        """

        if self._closed:
            raise RuntimeError("生活归档已关闭，不能继续访问数据库")

    def _require_open(self) -> sqlite3.Connection:
        """返回当前连接，并拒绝关闭后或初始化前的访问。

        Returns:
            已初始化的 SQLite 连接。

        Raises:
            RuntimeError: 归档已经关闭或尚未初始化。
        """

        self._require_not_closed()
        if self._conn is None:
            raise RuntimeError("生活归档数据库尚未初始化")
        return self._conn

    async def _run_db(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if _DB_LOCK_HELD.get():
            self._require_open()
            return await asyncio.to_thread(func, *args, **kwargs)
        if self._closed:
            self._require_open()
        if not self._initialized:
            await self.initialize()
        async with self._lock:
            self._require_open()
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
            scoped = bool(experience_scope and experience_scope != "global")
            values = [
                await self.get_recent_relationships(8),
                await self.get_recent_places(10),
                await self.get_recent_events(10),
                await self.get_recent_chat_summaries(max_summaries),
                await self.get_commitments(status="active", limit=8),
                await self.get_recent_group_environments(3),
                await self.get_recent_action_decisions(3),
                await self.get_recent_message_visibility(3),
                await self.get_life_episodes(limit=3),
                await self.get_focus_targets(limit=4),
                await self.get_behavior_feedback(limit=3),
                await self.get_emotion_arcs(limit=4, scope=experience_scope),
                await self.get_physiological_rhythm_logs(limit=3),
                await self.get_physiological_rhythm_trend(days=7, limit=6),
                await self.get_reply_effects(limit=4, scope=experience_scope),
                await self.get_memory_corrections(limit=3, unapplied_only=True),
                await self.get_expression_profiles(limit=4),
                await self.get_expression_reviews(limit=3, scope=experience_scope),
                await self.get_behavior_patterns(limit=4),
                await self.get_behavior_scenes(limit=4, scope=experience_scope),
                await self.get_session_mid_summaries(limit=3, session_id=session_id),
                await self.get_temporary_expression_states(
                    limit=3, scope=experience_scope
                ),
                await self.get_focus_slots(limit=4, scope=experience_scope),
                await self.get_expression_intents(limit=3, scope=experience_scope),
                await self.get_life_terms(limit=6, scope=experience_scope),
                await self.get_memory_boundaries(limit=4),
                await self.get_temporal_facts(scope=experience_scope, limit=12),
                await self.get_persona_assertions(scope="global", limit=6),
                await self.get_persona_assertions(scope=experience_scope, limit=6)
                if scoped
                else [],
                await self.get_affective_states(scope="global", limit=6),
                await self.get_affective_states(scope=experience_scope, limit=6)
                if scoped
                else [],
                await self.get_grounded_diary_entries(scope="global", limit=2),
            ]
            snapshot = dict(zip(_CONTEXT_SNAPSHOT_KEYS, values))
            snapshot["persona_assertions"] = list(
                snapshot.get("persona_assertions") or []
            ) + list(snapshot.pop("scoped_persona_assertions", []) or [])
            snapshot["affective_states"] = list(
                snapshot.get("affective_states") or []
            ) + list(snapshot.pop("scoped_affective_states", []) or [])
            return snapshot

        if not self._initialized:
            await self.initialize()
        async with self._lock:
            self._require_open()
            token = _DB_LOCK_HELD.set(True)
            try:
                return await collect()
            finally:
                _DB_LOCK_HELD.reset(token)

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
