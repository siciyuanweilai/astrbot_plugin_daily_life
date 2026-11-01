from __future__ import annotations

import json
import hashlib
import copy
from collections.abc import Callable
from typing import Any

from ..models import DayRecord
from ..models import LifeActionIntent, LongTermMemoryRecord

from typing import Protocol

_WORLD_KEY = "continuous_life_world"


class ContinuityArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _archive_continuous_kernel_unlocked: Any
    _archive_life_artifact_unlocked: Any
    _conn: Any
    _continuous_world_unlocked: Any
    _get_day_unlocked: Any
    _record_wardrobe_wear_unlocked: Any
    _run_db: Any
    _set_day_unlocked: Any
    _upsert_long_term_memory_unlocked: Any
    living_revision: Any


class ContinuityArchiveComponent:
    def __init__(self, context: ContinuityArchiveComponentContext) -> None:
        self.context = context

    def _archive_continuous_kernel_unlocked(
        self, world: dict[str, Any], previous_kernel: dict[str, Any]
    ) -> None:
        """与当前检查点同一事务归档历史，检查点裁剪不删除完整历史。"""
        kernel = world.get("kernel") or {}
        for kind, key in (
            ("event", "autobiography"),
            ("outcome", "action_outcomes"),
            ("causal", "causal_traces"),
            ("thread", "open_threads"),
        ):
            previous_items = {
                item.get("id"): item
                for item in previous_kernel.get(key, [])
                if isinstance(item, dict)
            }
            for item in kernel.get(key, []):
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if (
                    world.get("kernel_archive_initialized")
                    and previous_items.get(item["id"]) == item
                ):
                    continue
                entry_id = f"{kind}:{hashlib.sha256(str(item['id']).encode()).hexdigest()[:24]}"
                occurred_at = str(item.get("at") or item.get("last_at") or "")
                scope = str(item.get("scope") or "global")
                summary = str(
                    item.get("summary")
                    or item.get("target")
                    or item.get("consequence")
                    or item.get("title")
                    or ""
                )
                encoded = json.dumps(item, ensure_ascii=False, allow_nan=False)
                insert_sql = """INSERT INTO continuous_life_entries(id,kind,date,scope,occurred_at,summary,payload_json)
                    VALUES(?,?,?,?,?,?,?)"""
                conflict_sql = (
                    " ON CONFLICT(id) DO NOTHING"
                    if kind in {"event", "causal"}
                    else """ ON CONFLICT(id) DO UPDATE SET
                    date=excluded.date, scope=excluded.scope, occurred_at=excluded.occurred_at,
                    summary=excluded.summary, payload_json=excluded.payload_json
                    WHERE continuous_life_entries.payload_json <> excluded.payload_json"""
                )
                cursor = self.context._conn.execute(
                    insert_sql + conflict_sql,
                    (
                        entry_id,
                        kind,
                        occurred_at[:10],
                        scope,
                        occurred_at,
                        summary,
                        encoded,
                    ),
                )
                if not cursor.rowcount and kind in {"event", "causal"}:
                    archived = self.context._conn.execute(
                        "SELECT payload_json FROM continuous_life_entries WHERE id=?",
                        (entry_id,),
                    ).fetchone()
                    if archived:
                        original = json.loads(archived[0])
                        item.clear()
                        item.update(original)
                        if kind == "event":
                            for recent in kernel.get("events", []):
                                if (
                                    isinstance(recent, dict)
                                    and recent.get("id") == item["id"]
                                ):
                                    recent.clear()
                                    recent.update(original)
                if cursor.rowcount and kind == "outcome" and scope == "global":
                    self.context._archive_life_artifact_unlocked(item)
                    content = (
                        f"{summary}：{item.get('result') or item.get('status') or ''}"
                    )
                    reflection = item.get("reflection") or {}
                    if reflection.get("summary"):
                        content += (
                            f"。事后判断（不是观察事实）：{reflection['summary']}"
                        )
                    artifact = item.get("artifact") or {}
                    if artifact.get("content"):
                        content += f"。本轮生成的数字笔记/草稿：{artifact['content']}"
                    self.context._upsert_long_term_memory_unlocked(
                        LongTermMemoryRecord(
                            scope="global",
                            category="autobiography",
                            title=summary[:120],
                            content=content[:1000],
                            source_table="continuous_life_entries",
                            source_id=entry_id,
                            date=occurred_at[:10],
                            confidence=0.7 if reflection else 1.0,
                        )
                    )
        world["kernel_archive_initialized"] = True

    async def get_continuous_life_history(
        self, *, date: str = "", kind: str = "", limit: int = 40
    ) -> list[dict[str, Any]]:
        def read():
            clauses, params = ["scope='global'"], []
            if date:
                clauses.append("date=?")
                params.append(date)
            if kind:
                clauses.append("kind=?")
                params.append(kind)
            params.append(max(1, min(int(limit), 200)))
            rows = self.context._conn.execute(
                f"SELECT kind,payload_json FROM continuous_life_entries WHERE {' AND '.join(clauses)} ORDER BY occurred_at DESC,id DESC LIMIT ?",
                params,
            ).fetchall()
            return [{"entry_kind": row[0], **json.loads(row[1])} for row in rows]

        return await self.context._run_db(read)

    def consume_continuous_ingredients_unlocked(
        self, action: LifeActionIntent, *, consume: bool = False, occurred_at: str = ""
    ) -> str:
        """与执行检查点共用事务，库存不足时不提交完成事实。"""
        from ..life.body import nonnegative

        if action.action_type != "cook":
            return ""
        existing = self.context._conn.execute(
            "SELECT 1 FROM pantry_movements WHERE action_id=? AND source='continuous_executor' LIMIT 1",
            (action.action_id,),
        ).fetchone()
        if existing:
            return ""
        required = {}
        items = action.payload.get("ingredients", [])
        if not isinstance(items, list):
            return "烹饪缺少食材明细"
        for item in items:
            if not isinstance(item, dict):
                return "烹饪缺少有效食材用量"
            name = str(item.get("name") or "")
            quantity = nonnegative(item.get("quantity"))
            if not name or quantity <= 0:
                return "烹饪缺少有效食材用量"
            required[name] = required.get(name, 0.0) + quantity
        if not required:
            return "烹饪缺少食材明细"
        stock = {}
        for name, quantity in required.items():
            row = self.context._conn.execute(
                "SELECT quantity, unit FROM pantry_items WHERE name=?", (name,)
            ).fetchone()
            if row is None or float(row[0]) < quantity:
                return f"库存不足：{name}"
            units = {
                str(item.get("unit") or "")
                for item in items
                if item.get("name") == name
            }
            if any(unit and unit != row[1] for unit in units):
                return f"食材单位不一致：{name}"
            stock[name] = row
        if consume:
            for name, quantity in required.items():
                self.context._conn.execute(
                    "UPDATE pantry_items SET quantity=quantity-?, updated_at=CURRENT_TIMESTAMP WHERE name=?",
                    (quantity, name),
                )
                self.context._conn.execute(
                    "INSERT INTO pantry_movements(item_name,delta,unit,reason,action_id,occurred_at,source) VALUES(?,?,?,?,?,?,?)",
                    (
                        name,
                        -quantity,
                        stock[name][1],
                        f"自主烹饪：{action.target}",
                        action.action_id,
                        occurred_at or action.requested_at,
                        "continuous_executor",
                    ),
                )
        return ""

    async def continuous_ingredients_consumed(self, action_id: str) -> bool:
        return await self.context._run_db(
            lambda: (
                self.context._conn.execute(
                    "SELECT 1 FROM pantry_movements WHERE action_id=? AND source='continuous_executor' LIMIT 1",
                    (action_id,),
                ).fetchone()
                is not None
            )
        )

    def _continuous_world_unlocked(self) -> dict[str, Any]:
        row = self.context._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (_WORLD_KEY,)
        ).fetchone()
        raw = json.loads(row[0]) if row else {}
        if not isinstance(raw, dict):
            return {}
        from ..life.goals import remember_goal_review_evidence

        # 目标证据按原 action_id 精确读取，不依赖近期八十条结果窗口。
        missing = set()
        for goal in raw.get("goals", []):
            if goal.get("status") not in {"active", "blocked"}:
                continue
            for step in goal.get("steps", []):
                if step.get("status") == "completed":
                    continue
                saved = {item.get("action_id") for item in step.get("review_evidence", [])}
                missing.update(item["action_id"] for item in step.get("evidence", [])[-4:]
                               if item.get("action_id") and item["action_id"] not in saved)
        for action_id in sorted(missing):
            outcome_id = hashlib.sha256(f"{action_id}:committed".encode()).hexdigest()[:24]
            entry_id = "outcome:" + hashlib.sha256(outcome_id.encode()).hexdigest()[:24]
            source = self.context._conn.execute(
                "SELECT payload_json FROM continuous_life_entries WHERE id=? AND kind='outcome' AND scope='global'",
                (entry_id,),
            ).fetchone()
            if source:
                remember_goal_review_evidence(raw, json.loads(source[0]))
        return raw

    def clear_continuous_category_unlocked(self, category: str) -> None:
        from ..life.resources import settle as settle_resources

        if category not in {"daily", "experience"}:
            return
        if category == "experience":
            self.context._conn.execute("DELETE FROM meta WHERE key='bili_browse_state' OR key LIKE 'bili_social:%'")
        world = self.context._continuous_world_unlocked()
        if not world:
            return
        if category == "daily" and world.get("run"):
            settle_resources(self.context, LifeActionIntent.from_value(world["run"]["action"]), commit=False)
        keys = (
            (
                "body",
                "run",
                "history",
                "sleeping",
                "chat_events",
                "last_chat_at",
                "last_chat_scope",
                "observation_gap_at",
                "body_observations",
                "resource_blocked",
            )
            if category == "daily"
            else ("goals", "skills", "action_learning", "goal_review_at", "goal_source_key")
        )
        for key in (*keys, "next_decision_at"):
            world.pop(key, None)
        kernel = world.get("kernel")
        if isinstance(kernel, dict):
            if category == "daily":
                kernel["events"] = []
                kernel["social"] = {}
                kernel["environment"] = {}
                kernel.get("memory", {}).pop("recent_evidence_ids", None)
            else:
                kernel["reflection"] = {}
                for key in (
                    "autobiography",
                    "action_outcomes",
                    "causal_traces",
                    "open_threads",
                    "affect_layers",
                    "self_model",
                    "events",
                ):
                    kernel.pop(key, None)
                self.context._conn.execute(
                    "DELETE FROM long_term_memories WHERE source_table='continuous_life_entries'"
                )
                kernel.get("memory", {}).pop("recent_evidence_ids", None)
        world["revision"] = int(world.get("revision") or 0) + 1
        self.context._conn.execute(
            "UPDATE meta SET value=? WHERE key=?",
            (json.dumps(world, ensure_ascii=False), _WORLD_KEY),
        )
        # 清空后立即撤下聊天投影；下一次巡检再投影仍然保留的事实。
        self.context._conn.execute(
            "DELETE FROM day_meta WHERE key IN ('continuous_life_context', 'continuous_chat_context')"
        )

    async def get_continuous_life(self) -> dict[str, Any]:
        return await self.context._run_db(self.context._continuous_world_unlocked)

    async def mutate_continuous_life(
        self,
        date: str,
        mutator: Callable[[DayRecord, dict[str, Any]], Any],
    ) -> tuple[DayRecord | None, dict[str, Any]]:
        """在同一事务中提交身体、动作进度、长期目标与当日生活事实。"""
        from ..life.continuity import project_world
        from ..life.locationfacts import reconcile_place

        def write():
            self.context._conn.execute("BEGIN IMMEDIATE")
            try:
                day = self.context._get_day_unlocked(date)
                world = self.context._continuous_world_unlocked()
                if day is not None:
                    reconcile_place(day)
                previous_kernel = copy.deepcopy(world.get("kernel") or {})
                if day is None or mutator(day, world) is False:
                    self.context._conn.rollback()
                    return day, world
                world["revision"] = int(world.get("revision") or 0) + 1
                self.context._archive_continuous_kernel_unlocked(world, previous_kernel)
                project_world(day, world)
                self.context._conn.execute(
                    "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (
                        _WORLD_KEY,
                        json.dumps(world, ensure_ascii=False, allow_nan=False),
                    ),
                )
                revision = self.context._set_day_unlocked(day)
                if day.meta.get('outfit_fact_source') == 'life_action' or day.meta.get('style_usage_context'):
                    self.context._record_wardrobe_wear_unlocked(day, source='life_action_atomic_receipt')
                self.context._conn.commit()
                self.context.living_revision = getattr(self.context, "living_revision", 0) + 1
                day.mark_persisted(revision)
                return day, world
            except BaseException:
                self.context._conn.rollback()
                raise

        return await self.context._run_db(write)
