from __future__ import annotations

import asyncio
import contextvars
import sqlite3
from collections.abc import Callable, Coroutine
from functools import cached_property
from pathlib import Path
from typing import Any, TypeVar

from ..clock import local_timestamp
from ..composition import ComponentCall
from .acquaintances import AcquaintanceArchiveComponent
from .activity import DomainArchiveComponent
from .categories import STORAGE_CATEGORIES
from .common import CommonArchiveComponent
from .continuance import ContinuityArchiveComponent
from .experience.attention import FocusArchiveComponent
from .experience.cadence import PhysiologicalRhythmArchiveComponent
from .experience.clips import ClipArchiveComponent
from .experience.emotions import EmotionArchiveComponent
from .experience.episodes import EpisodeArchiveComponent
from .experience.lexicon import LexiconArchiveComponent
from .experience.maintenance import MaintenanceArchiveComponent
from .experience.patterns import BehaviorArchiveComponent
from .experience.proofs import EvidenceArchiveComponent
from .experience.reactions import FeedbackArchiveComponent
from .experience.selection import LifeDecisionArchiveComponent
from .experience.voices import ExpressionArchiveComponent
from .gallery import MediaArchiveComponent
from .garments import GarmentArchiveComponent
from .inventory import StyleCatalogArchiveComponent
from .journal import DayArchiveComponent
from .knowledge import CognitionArchiveComponent
from .longterm import LongTermMemoryArchiveComponent
from .perception import PerceptionArchiveComponent
from .places import PlaceArchiveComponent
from .possessions import LivingArchiveComponent
from .promises import CommitmentArchiveComponent
from .queue import ChatMemoryQueueArchiveComponent
from .recollection import RecollectionArchiveComponent
from .lifetraces import MemoryLifeArchive
from .reflections import LifecycleArchiveComponent
from .schema import init_schema
from .storage import StorageArchiveComponent
from .summaries import SummaryArchiveComponent
from .vectors import MemoryVectorArchiveComponent
from .weeks import WeekArchiveComponent

T = TypeVar("T")
_DB_LOCK_HELD = contextvars.ContextVar("daily_life_db_lock_held", default=False)
_DB_WORKER_ACTIVE = contextvars.ContextVar(
    "daily_life_db_worker_active", default=False
)

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
    "feedback",
    "emotion_arcs",
    "physiological_rhythm_logs",
    "physiological_rhythm_trend",
    "reply_effects",
    "memory_corrections",
    "expression_profiles",
    "expression_reviews",
    "behavior_scenes",
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


class LifeArchive:
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
        # 归档层的写入方法各自负责显式事务；自动提交避免某个独立写入
        # 遗留隐式事务后，下一次 BEGIN IMMEDIATE 触发嵌套事务异常。
        conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            timeout=10.0,
            isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.create_function("life_timestamp", 1, local_timestamp, deterministic=True)
            conn.execute("PRAGMA foreign_keys = ON")
            # 缓解跨线程/跨进程写入竞争：先尝试 WAL，失败（如只读文件系统）保持默认日志模式。
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                pass
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = NORMAL")
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
                await self._run_db_worker(self._initialize_sync)

    def close(self) -> None:
        self._closed = True
        conn = self._conn
        self._conn = None
        self._initialized = False
        if conn is not None:
            conn.close()

    async def aclose(self) -> None:
        async with self._lock:
            await self._run_db_worker(self.close)

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

    @staticmethod
    async def _run_db_worker(
        func: Callable[..., T], *args: Any, **kwargs: Any
    ) -> T:
        worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                result = await asyncio.shield(worker)
                break
            except asyncio.CancelledError as exc:
                # 工作线程无法被强制停止；先等它收束，避免锁提前释放后并发访问连接。
                cancellation = cancellation or exc
            except BaseException:
                if cancellation is not None:
                    raise cancellation
                raise
        if cancellation is not None:
            raise cancellation
        return result

    async def _run_db(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if _DB_WORKER_ACTIVE.get():
            self._require_open()
            return func(*args, **kwargs)
        if _DB_LOCK_HELD.get():
            self._require_open()
            return await self._run_db_worker(func, *args, **kwargs)
        if self._closed:
            self._require_open()
        if not self._initialized:
            await self.initialize()
        async with self._lock:
            self._require_open()
            return await self._run_db_worker(func, *args, **kwargs)

    @staticmethod
    def _run_db_worker_coroutine(factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """在数据库工作线程的隔离事件循环中运行内部批量读取。"""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("数据库工作线程已存在事件循环，不能启动隔离读取")
        with asyncio.Runner() as runner:
            return runner.run(factory())

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
                await self.get_commitments(status="active", limit=8, source_session=session_id or None),
                await self.get_recent_group_environments(3),
                await self.get_recent_action_decisions(3),
                await self.get_recent_message_visibility(3),
                await self.get_life_episodes(limit=3),
                await self.get_behavior_feedback(limit=3),
                await self.get_emotion_arcs(limit=4, scope=experience_scope),
                await self.get_physiological_rhythm_logs(limit=3),
                await self.get_physiological_rhythm_trend(days=7, limit=6),
                await self.get_reply_effects(
                    limit=4, scope=session_id or experience_scope
                ),
                await self.get_memory_corrections(limit=3, unapplied_only=True),
                (
                    await self.get_expression_context(limit=16, scope=experience_scope)
                    if experience_scope
                    else await self.get_expression_context(limit=16, scope="self")
                ),
                await self.get_expression_reviews(
                    limit=3, scope=session_id or experience_scope
                ),
                await self.get_behavior_scenes(limit=4, scope=experience_scope),
                await self.get_expression_intents(limit=3, scope=experience_scope),
                await self.get_life_terms(limit=6, scope=experience_scope),
                await self.get_memory_boundaries(limit=4),
                await self.get_temporal_facts(scope=experience_scope, limit=12, include_uncertain=True),
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
            snapshot["living_entities"] = await self.get_living_entities(session_id or experience_scope)
            return snapshot

        def collect_in_worker() -> dict[str, Any]:
            lock_token = _DB_LOCK_HELD.set(True)
            worker_token = _DB_WORKER_ACTIVE.set(True)
            try:
                return self._run_db_worker_coroutine(collect)
            finally:
                _DB_WORKER_ACTIVE.reset(worker_token)
                _DB_LOCK_HELD.reset(lock_token)

        return await self._run_db(collect_in_worker)

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

    _living_projection_unlocked = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "_living_projection_unlocked")

    _life_entity_unlocked = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "_life_entity_unlocked")

    life_entity_id = staticmethod(LivingArchiveComponent.life_entity_id)

    _save_life_entity_unlocked = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "_save_life_entity_unlocked")

    get_living_entities = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "get_living_entities")

    get_living_entity = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "get_living_entity")

    ensure_shared_activity = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "ensure_shared_activity")

    update_shared_activity = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "update_shared_activity")

    update_life_object = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "update_life_object")

    _archive_life_artifact_unlocked = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "_archive_life_artifact_unlocked")

    get_artifact_versions = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "get_artifact_versions")

    correct_life_memory = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "correct_life_memory")

    record_share_media_object = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "record_share_media_object")

    record_media_object = ComponentCall("_part_living_archive_component", LivingArchiveComponent, "record_media_object")

    _archive_continuous_kernel_unlocked = ComponentCall("_part_continuity_archive_component", ContinuityArchiveComponent, "_archive_continuous_kernel_unlocked")

    get_continuous_life_history = ComponentCall("_part_continuity_archive_component", ContinuityArchiveComponent, "get_continuous_life_history")

    consume_continuous_ingredients_unlocked = ComponentCall("_part_continuity_archive_component", ContinuityArchiveComponent, "consume_continuous_ingredients_unlocked")

    continuous_ingredients_consumed = ComponentCall("_part_continuity_archive_component", ContinuityArchiveComponent, "continuous_ingredients_consumed")

    _continuous_world_unlocked = ComponentCall("_part_continuity_archive_component", ContinuityArchiveComponent, "_continuous_world_unlocked")

    clear_continuous_category_unlocked = ComponentCall("_part_continuity_archive_component", ContinuityArchiveComponent, "clear_continuous_category_unlocked")

    get_continuous_life = ComponentCall("_part_continuity_archive_component", ContinuityArchiveComponent, "get_continuous_life")

    mutate_continuous_life = ComponentCall("_part_continuity_archive_component", ContinuityArchiveComponent, "mutate_continuous_life")

    _style_catalog_status = staticmethod(StyleCatalogArchiveComponent._style_catalog_status)

    _style_attributes = staticmethod(StyleCatalogArchiveComponent._style_attributes)

    _compose_style_catalog_item = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "_compose_style_catalog_item")

    upsert_style_catalog_item = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "upsert_style_catalog_item")

    get_style_catalog_items = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "get_style_catalog_items")

    save_wardrobe_understanding = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "save_wardrobe_understanding")

    get_style_catalog_counts = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "get_style_catalog_counts")

    get_style_catalog_item = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "get_style_catalog_item")

    _style_hash_distance = staticmethod(StyleCatalogArchiveComponent._style_hash_distance)

    find_similar_style_catalog_item = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "find_similar_style_catalog_item")

    merge_style_catalog_item = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "merge_style_catalog_item")

    revise_style_catalog_item = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "revise_style_catalog_item")

    set_style_catalog_status = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "set_style_catalog_status")

    delete_style_catalog_items = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "delete_style_catalog_items")

    _style_catalog_ids = staticmethod(StyleCatalogArchiveComponent._style_catalog_ids)

    get_recent_style_catalog_items = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "get_recent_style_catalog_items")

    add_style_catalog_feedback = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "add_style_catalog_feedback")

    mark_style_catalog_used = ComponentCall("_part_style_catalog_archive_component", StyleCatalogArchiveComponent, "mark_style_catalog_used")

    _synchronize_wardrobe_unlocked = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "_synchronize_wardrobe_unlocked")

    _wardrobe_item_state_unlocked = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "_wardrobe_item_state_unlocked")

    _wardrobe_action_issue_unlocked = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "_wardrobe_action_issue_unlocked")

    _prepare_outfit_action_unlocked = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "_prepare_outfit_action_unlocked")

    prepare_outfit_action = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "prepare_outfit_action")

    get_wardrobe_snapshot = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "get_wardrobe_snapshot")

    _wardrobe_event_unlocked = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "_wardrobe_event_unlocked")

    adopt_wardrobe_items = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "adopt_wardrobe_items")

    _record_wardrobe_wear_unlocked = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "_record_wardrobe_wear_unlocked")

    record_wardrobe_wear = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "record_wardrobe_wear")

    record_wardrobe_feedback = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "record_wardrobe_feedback")

    save_wardrobe_review = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "save_wardrobe_review")

    start_wardrobe_care = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "start_wardrobe_care")

    advance_wardrobe_care = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "advance_wardrobe_care")

    enqueue_wardrobe_job = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "enqueue_wardrobe_job")

    wardrobe_auto_requested_since = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "wardrobe_auto_requested_since")

    claim_wardrobe_job = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "claim_wardrobe_job")

    update_wardrobe_job = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "update_wardrobe_job")

    recover_wardrobe_jobs = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "recover_wardrobe_jobs")

    release_wardrobe_jobs = ComponentCall("_part_garment_archive_component", GarmentArchiveComponent, "release_wardrobe_jobs")

    _PANTRY_QUANTITY_DECIMALS = DomainArchiveComponent._PANTRY_QUANTITY_DECIMALS

    _PANTRY_ZERO_EPSILON = DomainArchiveComponent._PANTRY_ZERO_EPSILON

    _pantry_quantity = classmethod(DomainArchiveComponent._pantry_quantity.__func__)

    _pantry_row = classmethod(DomainArchiveComponent._pantry_row.__func__)

    _domain_json = staticmethod(DomainArchiveComponent._domain_json)

    _domain_value = staticmethod(DomainArchiveComponent._domain_value)

    _domain_row = classmethod(DomainArchiveComponent._domain_row.__func__)

    upsert_activity_session = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "upsert_activity_session")

    get_activity_sessions = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_activity_sessions")

    reconcile_activity_sessions = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "reconcile_activity_sessions")

    upsert_route = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "upsert_route")

    get_route = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_route")

    delete_routes_for_place = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "delete_routes_for_place")

    reset_residence_context = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "reset_residence_context")

    get_residence_context_boundary = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_residence_context_boundary")

    update_place_coordinates = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "update_place_coordinates")

    upsert_recipe = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "upsert_recipe")

    get_recipes = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_recipes")

    adjust_pantry_item = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "adjust_pantry_item")

    get_pantry_items = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_pantry_items")

    save_meal_record = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "save_meal_record")

    upsert_chore = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "upsert_chore")

    save_chore_record = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "save_chore_record")

    save_fitness_record = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "save_fitness_record")

    has_domain_action_record = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "has_domain_action_record")

    _save_domain_action_record = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "_save_domain_action_record")

    get_chores = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_chores")

    get_chore_records = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_chore_records")

    get_meal_records = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_meal_records")

    get_fitness_records = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_fitness_records")

    _get_domain_rows = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "_get_domain_rows")

    save_conversation_action_item = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "save_conversation_action_item")

    get_conversation_action_items = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_conversation_action_items")

    get_unified_life_timeline = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_unified_life_timeline")

    get_domain_snapshot = ComponentCall("_part_domain_archive_component", DomainArchiveComponent, "get_domain_snapshot")

    _dedupe_chore_definitions = staticmethod(DomainArchiveComponent._dedupe_chore_definitions)

    _domain_unified_timeline = staticmethod(DomainArchiveComponent._domain_unified_timeline)

    record_external_activity_receipt = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "record_external_activity_receipt")

    read_share_activity_receipts = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "read_share_activity_receipts")

    record_public_activity = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "record_public_activity")

    _TEMPORAL_SOURCE_PRIORITY = CognitionArchiveComponent._TEMPORAL_SOURCE_PRIORITY

    _cognition_now = staticmethod(CognitionArchiveComponent._cognition_now)

    _cognition_timestamp = staticmethod(CognitionArchiveComponent._cognition_timestamp)

    _temporal_source_priority = classmethod(CognitionArchiveComponent._temporal_source_priority.__func__)

    _cognition_json = staticmethod(CognitionArchiveComponent._cognition_json)

    _cognition_value = staticmethod(CognitionArchiveComponent._cognition_value)

    _compose_temporal_fact = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_temporal_fact")

    write_temporal_fact = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "write_temporal_fact")

    get_current_temporal_fact = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_current_temporal_fact")

    get_temporal_facts = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_temporal_facts")

    upsert_current_temporal_fact = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "upsert_current_temporal_fact")

    _compose_fact_evidence = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_fact_evidence")

    add_fact_evidence_signal = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "add_fact_evidence_signal")

    get_temporal_fact_confidence = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_temporal_fact_confidence")

    _compose_reflection = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_reflection")

    save_reflection = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "save_reflection")

    get_reflections = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_reflections")

    _compose_persona_assertion = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_persona_assertion")

    promote_reflections = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "promote_reflections")

    get_persona_assertions = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_persona_assertions")

    _compose_durable_task = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_durable_task")

    enqueue_durable_task = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "enqueue_durable_task")

    reschedule_durable_task = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "reschedule_durable_task")

    get_durable_tasks = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_durable_tasks")

    get_durable_task = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_durable_task")

    recover_expired_durable_tasks = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "recover_expired_durable_tasks")

    recover_leased_durable_tasks = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "recover_leased_durable_tasks")

    lease_durable_tasks = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "lease_durable_tasks")

    update_durable_task_progress = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "update_durable_task_progress")

    renew_durable_task_lease = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "renew_durable_task_lease")

    complete_durable_task = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "complete_durable_task")

    complete_durable_task_by_key = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "complete_durable_task_by_key")

    fail_durable_task_by_key = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "fail_durable_task_by_key")

    finalize_durable_task = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "finalize_durable_task")

    fail_durable_task = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "fail_durable_task")

    recover_provider_blocked_task = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "recover_provider_blocked_task")

    defer_durable_task = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "defer_durable_task")

    _compose_decision_trace = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_decision_trace")

    save_decision_trace = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "save_decision_trace")

    get_decision_traces = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_decision_traces")

    _compose_life_action_outcome = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_life_action_outcome")

    save_life_action_outcome = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "save_life_action_outcome")

    get_life_action_outcomes = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_life_action_outcomes")

    _compose_life_action_receipt = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_life_action_receipt")

    save_life_action_receipt = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "save_life_action_receipt")

    get_life_action_receipts = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_life_action_receipts")

    _compose_affective_state = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_affective_state")

    save_affective_state = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "save_affective_state")

    get_affective_states = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_affective_states")

    _compose_grounded_diary = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "_compose_grounded_diary")

    save_grounded_diary_entry = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "save_grounded_diary_entry")

    get_grounded_diary_entries = ComponentCall("_part_cognition_archive_component", CognitionArchiveComponent, "get_grounded_diary_entries")

    get_memory_vectors = ComponentCall("_part_memory_vector_archive_component", MemoryVectorArchiveComponent, "get_memory_vectors")

    upsert_memory_vectors = ComponentCall("_part_memory_vector_archive_component", MemoryVectorArchiveComponent, "upsert_memory_vectors")

    save_conversation_arrival = ComponentCall(
        "_part_chat_memory_queue_archive_component",
        ChatMemoryQueueArchiveComponent,
        "save_conversation_arrival",
    )

    get_conversation_arrival_before = ComponentCall(
        "_part_chat_memory_queue_archive_component",
        ChatMemoryQueueArchiveComponent,
        "get_conversation_arrival_before",
    )

    save_conversation_receipt = ComponentCall(
        "_part_chat_memory_queue_archive_component",
        ChatMemoryQueueArchiveComponent,
        "save_conversation_receipt",
    )

    get_conversation_receipt_before = ComponentCall("_part_chat_memory_queue_archive_component", ChatMemoryQueueArchiveComponent, "get_conversation_receipt_before")
    get_conversation_contact_state = ComponentCall("_part_chat_memory_queue_archive_component", ChatMemoryQueueArchiveComponent, "get_conversation_contact_state")

    _purge_completed_chat_memory_messages_unlocked = ComponentCall("_part_chat_memory_queue_archive_component", ChatMemoryQueueArchiveComponent, "_purge_completed_chat_memory_messages_unlocked")

    enqueue_chat_memory_message = ComponentCall("_part_chat_memory_queue_archive_component", ChatMemoryQueueArchiveComponent, "enqueue_chat_memory_message")

    list_chat_memory_sessions = ComponentCall("_part_chat_memory_queue_archive_component", ChatMemoryQueueArchiveComponent, "list_chat_memory_sessions")

    begin_chat_memory_batch = ComponentCall("_part_chat_memory_queue_archive_component", ChatMemoryQueueArchiveComponent, "begin_chat_memory_batch")

    complete_chat_memory_batch = ComponentCall("_part_chat_memory_queue_archive_component", ChatMemoryQueueArchiveComponent, "complete_chat_memory_batch")

    fail_chat_memory_batch = ComponentCall("_part_chat_memory_queue_archive_component", ChatMemoryQueueArchiveComponent, "fail_chat_memory_batch")

    purge_completed_chat_memory_messages = ComponentCall("_part_chat_memory_queue_archive_component", ChatMemoryQueueArchiveComponent, "purge_completed_chat_memory_messages")

    _load_json_object = staticmethod(DayArchiveComponent._load_json_object)

    _get_day_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_get_day_unlocked")

    _compose_weather_info = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_compose_weather_info")

    _get_state_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_get_state_unlocked")

    _get_day_events_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_get_day_events_unlocked")

    _compose_day_event = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_compose_day_event")

    _set_day_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_set_day_unlocked")

    _save_day_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_save_day_unlocked")

    _replace_timeline_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_replace_timeline_unlocked")

    _replace_outfit_history_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_replace_outfit_history_unlocked")

    _replace_day_meta_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_replace_day_meta_unlocked")

    _replace_state_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_replace_state_unlocked")

    _replace_state_log_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_replace_state_log_unlocked")

    _replace_day_places_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_replace_day_places_unlocked")

    _replace_day_events_unlocked = ComponentCall("_part_day_archive_component", DayArchiveComponent, "_replace_day_events_unlocked")

    get_day = ComponentCall("_part_day_archive_component", DayArchiveComponent, "get_day")

    get_future_memo_days = ComponentCall("_part_day_archive_component", DayArchiveComponent, "get_future_memo_days")

    get_recent_appearance_days = ComponentCall("_part_day_archive_component", DayArchiveComponent, "get_recent_appearance_days")

    save_day = ComponentCall("_part_day_archive_component", DayArchiveComponent, "save_day")

    mutate_day = ComponentCall("_part_day_archive_component", DayArchiveComponent, "mutate_day")

    replace_day_timeline = ComponentCall("_part_day_archive_component", DayArchiveComponent, "replace_day_timeline")

    delete_day = ComponentCall("_part_day_archive_component", DayArchiveComponent, "delete_day")

    set_memo = ComponentCall("_part_day_archive_component", DayArchiveComponent, "set_memo")

    get_week_plan = ComponentCall("_part_week_archive_component", WeekArchiveComponent, "get_week_plan")

    save_week_plan = ComponentCall("_part_week_archive_component", WeekArchiveComponent, "save_week_plan")

    get_all_week_plans = ComponentCall("_part_week_archive_component", WeekArchiveComponent, "get_all_week_plans")

    _compose_week_plan = ComponentCall("_part_week_archive_component", WeekArchiveComponent, "_compose_week_plan")

    _replace_week_goals_unlocked = ComponentCall("_part_week_archive_component", WeekArchiveComponent, "_replace_week_goals_unlocked")

    _replace_week_hints_unlocked = ComponentCall("_part_week_archive_component", WeekArchiveComponent, "_replace_week_hints_unlocked")

    _replace_week_suggestions_unlocked = ComponentCall("_part_week_archive_component", WeekArchiveComponent, "_replace_week_suggestions_unlocked")

    _compose_commitment = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "_compose_commitment")

    save_commitment = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "save_commitment")

    save_commitment_follow_up = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "save_commitment_follow_up")

    get_commitments = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "get_commitments")

    get_commitment = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "get_commitment")

    get_due_commitments = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "get_due_commitments")

    get_open_commitments_for_scope = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "get_open_commitments_for_scope")

    get_open_commitments_for_message = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "get_open_commitments_for_message")

    complete_simulated_commitment = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "complete_simulated_commitment")
    reconcile_conversation_commitment = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "reconcile_conversation_commitment")

    set_commitment_status = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "set_commitment_status")

    reschedule_commitment = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "reschedule_commitment")

    link_commitments_to_day = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "link_commitments_to_day")

    reconcile_scheduled_commitments = ComponentCall("_part_commitment_archive_component", CommitmentArchiveComponent, "reconcile_scheduled_commitments")

    _insert_event_unlocked = ComponentCall("_part_place_archive_component", PlaceArchiveComponent, "_insert_event_unlocked")

    add_events = ComponentCall("_part_place_archive_component", PlaceArchiveComponent, "add_events")

    get_recent_events = ComponentCall("_part_place_archive_component", PlaceArchiveComponent, "get_recent_events")

    _compose_event = ComponentCall("_part_place_archive_component", PlaceArchiveComponent, "_compose_event")

    touch_places = ComponentCall("_part_place_archive_component", PlaceArchiveComponent, "touch_places")

    get_recent_places = ComponentCall("_part_place_archive_component", PlaceArchiveComponent, "get_recent_places")

    _trim_relationship_notes_unlocked = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_trim_relationship_notes_unlocked")

    _trim_relationship_points_unlocked = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_trim_relationship_points_unlocked")

    _insert_relationship_note_unlocked = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_insert_relationship_note_unlocked")

    _insert_relationship_point_unlocked = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_insert_relationship_point_unlocked")

    _ensure_relationship_unlocked = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_ensure_relationship_unlocked")

    _compose_relationship_unlocked = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_compose_relationship_unlocked")

    touch_relationship = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "touch_relationship")

    touch_relationship_contact = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "touch_relationship_contact")

    mark_relationship_contact_unreachable = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "mark_relationship_contact_unreachable")

    get_reachable_relationship_contacts = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "get_reachable_relationship_contacts")

    get_recent_relationships = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "get_recent_relationships")

    get_relationships_for_target = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "get_relationships_for_target")

    get_relationship = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "get_relationship")

    revise_relationship_profile = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "revise_relationship_profile")

    _get_relationship_notes_unlocked = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_get_relationship_notes_unlocked")

    _get_relationship_points_unlocked = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_get_relationship_points_unlocked")

    _compose_relationship_contact = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_compose_relationship_contact")

    _is_generic_contact_name = staticmethod(AcquaintanceArchiveComponent._is_generic_contact_name)

    _get_relationship_contacts_unlocked = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "_get_relationship_contacts_unlocked")

    add_relationship_point = ComponentCall("_part_acquaintance_archive_component", AcquaintanceArchiveComponent, "add_relationship_point")

    _compose_chat_summary = ComponentCall("_part_summary_archive_component", SummaryArchiveComponent, "_compose_chat_summary")

    save_chat_summary = ComponentCall("_part_summary_archive_component", SummaryArchiveComponent, "save_chat_summary")

    get_recent_chat_summaries = ComponentCall("_part_summary_archive_component", SummaryArchiveComponent, "get_recent_chat_summaries")

    get_chat_summaries_for_session = ComponentCall("_part_summary_archive_component", SummaryArchiveComponent, "get_chat_summaries_for_session")

    _recent_scoped_rows_unlocked = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "_recent_scoped_rows_unlocked")

    _compose_group_environment = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "_compose_group_environment")

    save_group_environment = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "save_group_environment")

    get_recent_group_environments = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "get_recent_group_environments")

    _compose_message_visibility = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "_compose_message_visibility")

    save_message_visibility = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "save_message_visibility")

    get_recent_message_visibility = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "get_recent_message_visibility")

    get_message_visibility_records = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "get_message_visibility_records")

    _compose_action_decision = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "_compose_action_decision")

    save_action_decision = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "save_action_decision")

    get_recent_action_decisions = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "get_recent_action_decisions")

    get_action_decision_records = ComponentCall("_part_perception_archive_component", PerceptionArchiveComponent, "get_action_decision_records")

    _ENTITY_CATEGORY_MAP = LongTermMemoryArchiveComponent._ENTITY_CATEGORY_MAP

    _compose_long_term_memory = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_compose_long_term_memory")

    _compose_memory_cluster = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_compose_memory_cluster")

    _compose_memory_entity = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_compose_memory_entity")

    _compose_memory_conflict = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_compose_memory_conflict")

    _compose_memory_decision_link = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_compose_memory_decision_link")

    _memory_search_query = staticmethod(LongTermMemoryArchiveComponent._memory_search_query)

    _memory_search_terms = staticmethod(LongTermMemoryArchiveComponent._memory_search_terms)

    _upsert_long_term_memory_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_upsert_long_term_memory_unlocked")

    _memory_lifecycle_kind = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_memory_lifecycle_kind")

    _apply_memory_lifecycle_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_apply_memory_lifecycle_unlocked")

    _memory_entity_name = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_memory_entity_name")

    _memory_entity_type = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_memory_entity_type")

    _upsert_memory_entity_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_upsert_memory_entity_unlocked")

    _memory_cluster_title = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_memory_cluster_title")

    _upsert_memory_cluster_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_upsert_memory_cluster_unlocked")

    _record_memory_conflicts_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_record_memory_conflicts_unlocked")

    _maintain_long_term_memory_quality_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_maintain_long_term_memory_quality_unlocked")

    upsert_long_term_memory = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "upsert_long_term_memory")

    link_memory_episode = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "link_memory_episode")

    upsert_long_term_memories = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "upsert_long_term_memories")

    _long_term_memory_scope_clause = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_long_term_memory_scope_clause")

    _search_long_term_memory_fts_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_search_long_term_memory_fts_unlocked")

    _search_long_term_memory_like_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_search_long_term_memory_like_unlocked")

    search_long_term_memories = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "search_long_term_memories")

    list_recent_long_term_memories = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "list_recent_long_term_memories")

    get_memory_episode_clusters = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "get_memory_episode_clusters")

    get_memory_entities = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "get_memory_entities")

    get_memory_conflicts = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "get_memory_conflicts")

    _upsert_memory_decision_link_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_upsert_memory_decision_link_unlocked")

    _decision_memory_query = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_decision_memory_query")

    _link_decision_memory_candidates_unlocked = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "_link_decision_memory_candidates_unlocked")

    link_decision_memories = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "link_decision_memories")

    get_memory_decision_links = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "get_memory_decision_links")

    get_memory_decision_sources = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "get_memory_decision_sources")

    cleanup_long_term_memories = ComponentCall("_part_long_term_memory_archive_component", LongTermMemoryArchiveComponent, "cleanup_long_term_memories")

    save_memory_sources = ComponentCall("_part_recollection_archive_component", RecollectionArchiveComponent, "save_memory_sources")
    get_memory_recall_context = ComponentCall("_part_recollection_archive_component", RecollectionArchiveComponent, "get_memory_recall_context")
    get_related_long_term_memories = ComponentCall("_part_recollection_archive_component", RecollectionArchiveComponent, "get_related_long_term_memories")
    read_memory_evidence = ComponentCall("_part_recollection_archive_component", RecollectionArchiveComponent, "read_memory_evidence")
    consolidate_living_memories = ComponentCall("_part_recollection_archive_component", RecollectionArchiveComponent, "consolidate_living_memories")

    _compose_life_episode = ComponentCall("_part_episode_archive_component", EpisodeArchiveComponent, "_compose_life_episode")

    save_life_episode = ComponentCall("_part_episode_archive_component", EpisodeArchiveComponent, "save_life_episode")

    get_life_episodes = ComponentCall("_part_episode_archive_component", EpisodeArchiveComponent, "get_life_episodes")

    correct_life_episode = ComponentCall("_part_episode_archive_component", EpisodeArchiveComponent, "correct_life_episode")

    set_life_episode_protected = ComponentCall("_part_episode_archive_component", EpisodeArchiveComponent, "set_life_episode_protected")

    _compose_life_decision = ComponentCall("_part_life_decision_archive_component", LifeDecisionArchiveComponent, "_compose_life_decision")

    save_life_decision = ComponentCall("_part_life_decision_archive_component", LifeDecisionArchiveComponent, "save_life_decision")

    get_life_decisions = ComponentCall("_part_life_decision_archive_component", LifeDecisionArchiveComponent, "get_life_decisions")

    update_life_decision_outcome = ComponentCall("_part_life_decision_archive_component", LifeDecisionArchiveComponent, "update_life_decision_outcome")

    _emotion_now_text = staticmethod(EmotionArchiveComponent._emotion_now_text)

    _parse_emotion_time = staticmethod(EmotionArchiveComponent._parse_emotion_time)

    _clamp_score = staticmethod(EmotionArchiveComponent._clamp_score)

    _default_emotion_expiry = ComponentCall("_part_emotion_archive_component", EmotionArchiveComponent, "_default_emotion_expiry")

    _compose_emotion_arc = ComponentCall("_part_emotion_archive_component", EmotionArchiveComponent, "_compose_emotion_arc")

    _decay_emotion_arc = ComponentCall("_part_emotion_archive_component", EmotionArchiveComponent, "_decay_emotion_arc")

    _compact_active_emotion_arcs = ComponentCall("_part_emotion_archive_component", EmotionArchiveComponent, "_compact_active_emotion_arcs")

    save_emotion_arc = ComponentCall("_part_emotion_archive_component", EmotionArchiveComponent, "save_emotion_arc")

    get_emotion_arcs = ComponentCall("_part_emotion_archive_component", EmotionArchiveComponent, "get_emotion_arcs")

    _RHYTHM_SINGLE_ACTIVE_LIFECYCLES = PhysiologicalRhythmArchiveComponent._RHYTHM_SINGLE_ACTIVE_LIFECYCLES

    _RHYTHM_LIFECYCLE_DAYS = PhysiologicalRhythmArchiveComponent._RHYTHM_LIFECYCLE_DAYS

    _rhythm_now_text = staticmethod(PhysiologicalRhythmArchiveComponent._rhythm_now_text)

    _invalidate_physiological_rhythm_trend_cache = ComponentCall("_part_physiological_rhythm_archive_component", PhysiologicalRhythmArchiveComponent, "_invalidate_physiological_rhythm_trend_cache")

    _physiological_rhythm_trend_cache_key = ComponentCall("_part_physiological_rhythm_archive_component", PhysiologicalRhythmArchiveComponent, "_physiological_rhythm_trend_cache_key")

    _parse_rhythm_time = staticmethod(PhysiologicalRhythmArchiveComponent._parse_rhythm_time)

    _rhythm_score = staticmethod(PhysiologicalRhythmArchiveComponent._rhythm_score)

    _loads_texts = staticmethod(PhysiologicalRhythmArchiveComponent._loads_texts)

    _compose_physiological_rhythm_log = ComponentCall("_part_physiological_rhythm_archive_component", PhysiologicalRhythmArchiveComponent, "_compose_physiological_rhythm_log")

    _rhythm_log_status = ComponentCall("_part_physiological_rhythm_archive_component", PhysiologicalRhythmArchiveComponent, "_rhythm_log_status")

    _rhythm_lifecycle_kind = staticmethod(PhysiologicalRhythmArchiveComponent._rhythm_lifecycle_kind)

    _rhythm_weight = staticmethod(PhysiologicalRhythmArchiveComponent._rhythm_weight)

    _latest_active_rhythm_logs = classmethod(PhysiologicalRhythmArchiveComponent._latest_active_rhythm_logs.__func__)

    _compact_active_lifecycle_rhythm_logs = ComponentCall("_part_physiological_rhythm_archive_component", PhysiologicalRhythmArchiveComponent, "_compact_active_lifecycle_rhythm_logs")

    save_physiological_rhythm_log = ComponentCall("_part_physiological_rhythm_archive_component", PhysiologicalRhythmArchiveComponent, "save_physiological_rhythm_log")

    get_physiological_rhythm_logs = ComponentCall("_part_physiological_rhythm_archive_component", PhysiologicalRhythmArchiveComponent, "get_physiological_rhythm_logs")

    get_physiological_rhythm_trend = ComponentCall("_part_physiological_rhythm_archive_component", PhysiologicalRhythmArchiveComponent, "get_physiological_rhythm_trend")

    _is_life_decision_evidence = staticmethod(EvidenceArchiveComponent._is_life_decision_evidence)

    _compose_memory_evidence = ComponentCall("_part_evidence_archive_component", EvidenceArchiveComponent, "_compose_memory_evidence")

    _compact_life_decision_evidence = ComponentCall("_part_evidence_archive_component", EvidenceArchiveComponent, "_compact_life_decision_evidence")

    save_memory_evidence = ComponentCall("_part_evidence_archive_component", EvidenceArchiveComponent, "save_memory_evidence")

    get_memory_evidence = ComponentCall("_part_evidence_archive_component", EvidenceArchiveComponent, "get_memory_evidence")

    _compose_behavior_feedback = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "_compose_behavior_feedback")

    add_behavior_feedback = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "add_behavior_feedback")

    get_behavior_feedback = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "get_behavior_feedback")

    _compose_reply_effect = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "_compose_reply_effect")

    save_reply_effect = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "save_reply_effect")

    update_reply_effect_outcome = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "update_reply_effect_outcome")

    expire_stale_reply_effects = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "expire_stale_reply_effects")

    get_reply_effects = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "get_reply_effects")

    _compose_memory_correction = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "_compose_memory_correction")

    save_memory_correction = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "save_memory_correction")

    mark_memory_correction_applied = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "mark_memory_correction_applied")

    get_memory_corrections = ComponentCall("_part_feedback_archive_component", FeedbackArchiveComponent, "get_memory_corrections")

    _compose_expression_profile = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "_compose_expression_profile")

    upsert_expression_profile = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "upsert_expression_profile")

    get_expression_profiles = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "get_expression_profiles")

    get_expression_context = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "get_expression_context")

    apply_expression_learning = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "apply_expression_learning")

    recover_expression_watch = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "recover_expression_watch")

    _compose_expression_review = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "_compose_expression_review")

    save_expression_review = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "save_expression_review")

    get_expression_reviews = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "get_expression_reviews")

    _compose_temporary_expression_state = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "_compose_temporary_expression_state")

    upsert_temporary_expression_state = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "upsert_temporary_expression_state")

    get_temporary_expression_states = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "get_temporary_expression_states")

    _compose_expression_intent = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "_compose_expression_intent")

    save_expression_intent = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "save_expression_intent")

    get_expression_intents = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "get_expression_intents")

    _compose_emoji_asset = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "_compose_emoji_asset")

    upsert_emoji_asset = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "upsert_emoji_asset")

    get_emoji_asset_by_hash = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "get_emoji_asset_by_hash")

    get_emoji_assets = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "get_emoji_assets")

    delete_emoji_assets = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "delete_emoji_assets")

    mark_emoji_used = ComponentCall("_part_expression_archive_component", ExpressionArchiveComponent, "mark_emoji_used")

    _json_dict_unlocked = staticmethod(ClipArchiveComponent._json_dict_unlocked)

    _video_lines_unlocked = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "_video_lines_unlocked")

    _replace_video_lines_unlocked = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "_replace_video_lines_unlocked")

    _compose_video_insight_unlocked = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "_compose_video_insight_unlocked")

    _delete_video_insight_ids_unlocked = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "_delete_video_insight_ids_unlocked")

    _prune_video_insights_unlocked = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "_prune_video_insights_unlocked")

    prune_video_insights = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "prune_video_insights")

    upsert_video_insight = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "upsert_video_insight")

    get_video_insight = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "get_video_insight")

    get_recent_video_insights = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "get_recent_video_insights")

    delete_video_insights_for_message = ComponentCall("_part_clip_archive_component", ClipArchiveComponent, "delete_video_insights_for_message")

    _compose_behavior_pattern = ComponentCall("_part_behavior_archive_component", BehaviorArchiveComponent, "_compose_behavior_pattern")

    upsert_behavior_pattern = ComponentCall("_part_behavior_archive_component", BehaviorArchiveComponent, "upsert_behavior_pattern")

    get_behavior_patterns = ComponentCall("_part_behavior_archive_component", BehaviorArchiveComponent, "get_behavior_patterns")

    _compose_behavior_scene = ComponentCall("_part_behavior_archive_component", BehaviorArchiveComponent, "_compose_behavior_scene")

    upsert_behavior_scene = ComponentCall("_part_behavior_archive_component", BehaviorArchiveComponent, "upsert_behavior_scene")

    get_behavior_scenes = ComponentCall("_part_behavior_archive_component", BehaviorArchiveComponent, "get_behavior_scenes")

    _compose_session_mid_summary = ComponentCall("_part_behavior_archive_component", BehaviorArchiveComponent, "_compose_session_mid_summary")

    upsert_session_mid_summary = ComponentCall("_part_behavior_archive_component", BehaviorArchiveComponent, "upsert_session_mid_summary")

    get_session_mid_summaries = ComponentCall("_part_behavior_archive_component", BehaviorArchiveComponent, "get_session_mid_summaries")

    _focus_expiry_clause = staticmethod(FocusArchiveComponent._focus_expiry_clause)

    _compose_focus_slot = ComponentCall("_part_focus_archive_component", FocusArchiveComponent, "_compose_focus_slot")

    upsert_focus_slot = ComponentCall("_part_focus_archive_component", FocusArchiveComponent, "upsert_focus_slot")

    get_focus_slots = ComponentCall("_part_focus_archive_component", FocusArchiveComponent, "get_focus_slots")

    update_focus_slot_progress = ComponentCall("_part_focus_archive_component", FocusArchiveComponent, "update_focus_slot_progress")

    _compose_focus_target = ComponentCall("_part_focus_archive_component", FocusArchiveComponent, "_compose_focus_target")

    upsert_focus_target = ComponentCall("_part_focus_archive_component", FocusArchiveComponent, "upsert_focus_target")

    get_focus_targets = ComponentCall("_part_focus_archive_component", FocusArchiveComponent, "get_focus_targets")

    set_focus_target_enabled = ComponentCall("_part_focus_archive_component", FocusArchiveComponent, "set_focus_target_enabled")

    _compose_life_term = ComponentCall("_part_lexicon_archive_component", LexiconArchiveComponent, "_compose_life_term")

    upsert_life_term = ComponentCall("_part_lexicon_archive_component", LexiconArchiveComponent, "upsert_life_term")

    get_life_terms = ComponentCall("_part_lexicon_archive_component", LexiconArchiveComponent, "get_life_terms")

    _compose_memory_boundary = ComponentCall("_part_lexicon_archive_component", LexiconArchiveComponent, "_compose_memory_boundary")

    set_memory_boundary = ComponentCall("_part_lexicon_archive_component", LexiconArchiveComponent, "set_memory_boundary")

    get_memory_boundaries = ComponentCall("_part_lexicon_archive_component", LexiconArchiveComponent, "get_memory_boundaries")

    _compose_memory_maintenance = ComponentCall("_part_maintenance_archive_component", MaintenanceArchiveComponent, "_compose_memory_maintenance")

    save_memory_maintenance = ComponentCall("_part_maintenance_archive_component", MaintenanceArchiveComponent, "save_memory_maintenance")

    get_memory_maintenance = ComponentCall("_part_maintenance_archive_component", MaintenanceArchiveComponent, "get_memory_maintenance")

    get_life_health_report = ComponentCall("_part_maintenance_archive_component", MaintenanceArchiveComponent, "get_life_health_report")

    _compose_reverse_prompt = ComponentCall("_part_media_archive_component", MediaArchiveComponent, "_compose_reverse_prompt")

    save_reverse_prompt = ComponentCall("_part_media_archive_component", MediaArchiveComponent, "save_reverse_prompt")

    get_latest_reverse_prompt = ComponentCall("_part_media_archive_component", MediaArchiveComponent, "get_latest_reverse_prompt")

    get_reverse_prompt_by_fingerprint = ComponentCall("_part_media_archive_component", MediaArchiveComponent, "get_reverse_prompt_by_fingerprint")

    cleanup_reverse_prompts = ComponentCall("_part_media_archive_component", MediaArchiveComponent, "cleanup_reverse_prompts")

    _compose_preference = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "_compose_preference")

    _compose_life_event = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "_compose_life_event")

    _get_review_points_unlocked = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "_get_review_points_unlocked")

    _replace_review_points_unlocked = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "_replace_review_points_unlocked")

    _get_review_unlocked = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "_get_review_unlocked")

    save_daily_review = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "save_daily_review")

    get_daily_review = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "get_daily_review")

    get_recent_daily_reviews = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "get_recent_daily_reviews")

    _upsert_preferences_unlocked = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "_upsert_preferences_unlocked")

    _merge_preferences_unlocked = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "_merge_preferences_unlocked")

    upsert_preferences = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "upsert_preferences")

    merge_preferences = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "merge_preferences")

    get_preferences = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "get_preferences")

    _insert_life_event_unlocked = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "_insert_life_event_unlocked")

    add_life_event = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "add_life_event")

    get_life_events = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "get_life_events")

    set_life_event_status = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "set_life_event_status")

    close_stale_life_events = ComponentCall("_part_lifecycle_archive_component", LifecycleArchiveComponent, "close_stale_life_events")

    _table_exists_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_table_exists_unlocked")

    _table_count_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_table_count_unlocked")

    _storage_keep_days = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_storage_keep_days")

    _storage_category_stats_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_storage_category_stats_unlocked")

    get_storage_categories = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "get_storage_categories")

    get_storage_overview = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "get_storage_overview")

    _delete_all_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_delete_all_unlocked")

    clear_storage_category = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "clear_storage_category")

    _cutoff_date = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cutoff_date")

    _cutoff_week_id = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cutoff_week_id")

    _cleanup_daily_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_daily_unlocked")

    _cleanup_review_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_review_unlocked")

    _cleanup_cognition_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_cognition_unlocked")

    _delete_statements_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_delete_statements_unlocked")

    _cleanup_relationships_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_relationships_unlocked")

    _cleanup_world_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_world_unlocked")

    _cleanup_conversation_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_conversation_unlocked")

    _cleanup_experience_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_experience_unlocked")

    _cleanup_expression_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_expression_unlocked")

    _cleanup_media_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_media_unlocked")

    _cleanup_longterm_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_longterm_unlocked")

    _cleanup_planning_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_planning_unlocked")

    _cleanup_domains_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_domains_unlocked")

    _cleanup_category_unlocked = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "_cleanup_category_unlocked")

    cleanup_storage_category = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "cleanup_storage_category")

    cleanup_by_storage_policy = ComponentCall("_part_storage_archive_component", StorageArchiveComponent, "cleanup_by_storage_policy")

    _text = staticmethod(CommonArchiveComponent._text)

    _int = staticmethod(CommonArchiveComponent._int)

    _flag = staticmethod(CommonArchiveComponent._flag)

    _get_ordered_texts_unlocked = ComponentCall("_part_common_archive_component", CommonArchiveComponent, "_get_ordered_texts_unlocked")

    _replace_texts_unlocked = ComponentCall("_part_common_archive_component", CommonArchiveComponent, "_replace_texts_unlocked")

    _replace_people_unlocked = ComponentCall("_part_common_archive_component", CommonArchiveComponent, "_replace_people_unlocked")

    _get_people_unlocked = ComponentCall("_part_common_archive_component", CommonArchiveComponent, "_get_people_unlocked")

    @cached_property
    def _part_living_archive_component(self):
        return LivingArchiveComponent(self)

    @cached_property
    def _part_continuity_archive_component(self):
        return ContinuityArchiveComponent(self)

    @cached_property
    def _part_style_catalog_archive_component(self):
        return StyleCatalogArchiveComponent(self)

    @cached_property
    def _part_garment_archive_component(self):
        return GarmentArchiveComponent(self)

    @cached_property
    def _part_domain_archive_component(self):
        return DomainArchiveComponent(self)

    @cached_property
    def _part_cognition_archive_component(self):
        return CognitionArchiveComponent(self)

    @cached_property
    def _part_memory_vector_archive_component(self):
        return MemoryVectorArchiveComponent(self)

    @cached_property
    def _part_chat_memory_queue_archive_component(self):
        return ChatMemoryQueueArchiveComponent(self)

    @cached_property
    def _part_day_archive_component(self):
        return DayArchiveComponent(self)

    @cached_property
    def _part_week_archive_component(self):
        return WeekArchiveComponent(self)

    @cached_property
    def _part_commitment_archive_component(self):
        return CommitmentArchiveComponent(self)

    @cached_property
    def _part_place_archive_component(self):
        return PlaceArchiveComponent(self)

    @cached_property
    def _part_acquaintance_archive_component(self):
        return AcquaintanceArchiveComponent(self)

    @cached_property
    def _part_summary_archive_component(self):
        return SummaryArchiveComponent(self)

    @cached_property
    def _part_perception_archive_component(self):
        return PerceptionArchiveComponent(self)

    @cached_property
    def _part_long_term_memory_archive_component(self):
        return LongTermMemoryArchiveComponent(self)

    @cached_property
    def memory_life(self):
        return MemoryLifeArchive(self)

    @cached_property
    def _part_recollection_archive_component(self):
        return RecollectionArchiveComponent(self)

    @cached_property
    def _part_episode_archive_component(self):
        return EpisodeArchiveComponent(self)

    @cached_property
    def _part_life_decision_archive_component(self):
        return LifeDecisionArchiveComponent(self)

    @cached_property
    def _part_emotion_archive_component(self):
        return EmotionArchiveComponent(self)

    @cached_property
    def _part_physiological_rhythm_archive_component(self):
        return PhysiologicalRhythmArchiveComponent(self)

    @cached_property
    def _part_evidence_archive_component(self):
        return EvidenceArchiveComponent(self)

    @cached_property
    def _part_feedback_archive_component(self):
        return FeedbackArchiveComponent(self)

    @cached_property
    def _part_expression_archive_component(self):
        return ExpressionArchiveComponent(self)

    @cached_property
    def _part_clip_archive_component(self):
        return ClipArchiveComponent(self)

    @cached_property
    def _part_behavior_archive_component(self):
        return BehaviorArchiveComponent(self)

    @cached_property
    def _part_focus_archive_component(self):
        return FocusArchiveComponent(self)

    @cached_property
    def _part_lexicon_archive_component(self):
        return LexiconArchiveComponent(self)

    @cached_property
    def _part_maintenance_archive_component(self):
        return MaintenanceArchiveComponent(self)

    @cached_property
    def _part_media_archive_component(self):
        return MediaArchiveComponent(self)

    @cached_property
    def _part_lifecycle_archive_component(self):
        return LifecycleArchiveComponent(self)

    @cached_property
    def _part_storage_archive_component(self):
        return StorageArchiveComponent(self)

    @cached_property
    def _part_common_archive_component(self):
        return CommonArchiveComponent(self)

