from __future__ import annotations

import datetime
import json
import math
import sqlite3
import uuid
from dataclasses import replace
from typing import Any

from ..models import (
    AffectiveStateRecord,
    DecisionTraceRecord,
    DurableTaskRecord,
    FactEvidenceSignalRecord,
    GroundedDiaryEntryRecord,
    LifeActionOutcomeRecord,
    LifeActionReceiptRecord,
    PersonaAssertionRecord,
    ReflectionRecord,
    TemporalFactRecord,
)


class CognitionArchiveMixin:
    """持久化时间化认知、任务、情绪和动作结算。"""

    @staticmethod
    def _cognition_now() -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _cognition_json(value: Any, *, default: Any) -> str:
        target = default if value is None else value
        return json.dumps(
            target,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _cognition_value(value: Any, *, default: Any) -> Any:
        if isinstance(value, (dict, list, tuple, int, float, bool)) or value is None:
            return value
        text = str(value or "").strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return default

    def _compose_temporal_fact(self, row: sqlite3.Row) -> TemporalFactRecord:
        return TemporalFactRecord(
            id=int(row["id"] or 0),
            scope=self._text(row["scope"]),
            subject=self._text(row["subject"]),
            predicate=self._text(row["predicate"]),
            object_value=self._cognition_value(row["object_json"], default=None),
            observed_at=self._text(row["observed_at"]),
            valid_from=self._text(row["valid_from"]),
            valid_to=self._text(row["valid_to"]),
            confidence=max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
            status=self._text(row["status"]) or "active",
            source=self._text(row["source"]) or "observation",
            source_type=self._text(row["source_type"]),
            source_id=self._text(row["source_id"]),
            provenance=self._cognition_value(row["provenance_json"], default={}),
            supersedes_id=int(row["supersedes_id"] or 0),
            created_at=self._text(row["created_at"]),
            updated_at=self._text(row["updated_at"]),
        )

    async def write_temporal_fact(
        self, operation: str, fact: TemporalFactRecord | dict[str, Any]
    ) -> TemporalFactRecord | None:
        """按显式操作写入同一结构化事实键。

        ``ADD`` 只创建不存在的事实，``UPDATE`` 会保留被替代版本，
        ``INVALIDATE`` 会关闭当前版本，``NONE`` 不产生写入。

        Args:
            operation: ``ADD``、``UPDATE``、``INVALIDATE`` 或 ``NONE``。
            fact: 包含 scope、subject、predicate 的事实数据。

        Returns:
            操作后的事实；当前事实不存在时可返回 ``None``。

        Raises:
            ValueError: 操作、结构化键或状态前置条件无效。
        """

        action = self._text(operation).upper()
        if action not in {"ADD", "UPDATE", "INVALIDATE", "NONE"}:
            raise ValueError("事实操作必须是 ADD、UPDATE、INVALIDATE 或 NONE")
        item = TemporalFactRecord.from_value(fact)
        if not item:
            raise ValueError("时间化事实必须包含 scope、subject 和 predicate")
        now = self._cognition_now()
        effective_at = item.valid_from or item.observed_at or now
        object_json = self._cognition_json(item.object_value, default=None)

        def dbwork() -> TemporalFactRecord | None:
            current = self._conn.execute(
                """
                SELECT * FROM temporal_facts
                WHERE scope = ? AND subject = ? AND predicate = ?
                  AND status = 'active' AND valid_to = ''
                ORDER BY id DESC LIMIT 1
                """,
                (item.scope, item.subject, item.predicate),
            ).fetchone()
            if action == "NONE":
                return self._compose_temporal_fact(current) if current else None
            if action == "INVALIDATE":
                if not current:
                    return None
                self._conn.execute(
                    """
                    UPDATE temporal_facts
                    SET valid_to = ?, status = 'invalidated', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (effective_at, int(current["id"])),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM temporal_facts WHERE id = ?", (int(current["id"]),)
                ).fetchone()
                return self._compose_temporal_fact(row)
            if action == "ADD" and current:
                if self._text(current["object_json"]) == object_json:
                    return self._compose_temporal_fact(current)
                raise ValueError("同一结构化事实键已有当前值，请使用 UPDATE")
            if action == "UPDATE" and not current:
                raise ValueError("要更新的结构化事实不存在，请先使用 ADD")

            supersedes_id = int(current["id"] or 0) if current else 0
            if current:
                self._conn.execute(
                    """
                    UPDATE temporal_facts
                    SET valid_to = ?, status = 'superseded', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (effective_at, supersedes_id),
                )
            cursor = self._conn.execute(
                """
                INSERT INTO temporal_facts(
                    scope, subject, predicate, object_json, observed_at, valid_from,
                    valid_to, confidence, status, source, source_type, source_id,
                    provenance_json, supersedes_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', ?, 'active', ?, ?, ?, ?, ?,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    item.scope,
                    item.subject,
                    item.predicate,
                    object_json,
                    item.observed_at or now,
                    effective_at,
                    item.confidence,
                    item.source,
                    item.source_type,
                    item.source_id,
                    self._cognition_json(item.provenance, default={}),
                    supersedes_id or None,
                ),
            )
            fact_id = int(cursor.lastrowid)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM temporal_facts WHERE id = ?", (fact_id,)
            ).fetchone()
            return self._compose_temporal_fact(row)

        return await self._run_db(dbwork)

    async def get_current_temporal_fact(
        self, scope: str, subject: str, predicate: str
    ) -> TemporalFactRecord | None:
        """读取结构化键的当前有效事实。

        Args:
            scope: 事实作用域。
            subject: 事实主体。
            predicate: 事实谓词。

        Returns:
            当前事实；不存在时返回 ``None``。
        """

        def dbwork() -> TemporalFactRecord | None:
            row = self._conn.execute(
                """
                SELECT * FROM temporal_facts
                WHERE scope = ? AND subject = ? AND predicate = ?
                  AND status = 'active' AND valid_to = ''
                ORDER BY id DESC LIMIT 1
                """,
                (self._text(scope), self._text(subject), self._text(predicate)),
            ).fetchone()
            return self._compose_temporal_fact(row) if row else None

        return await self._run_db(dbwork)

    async def get_temporal_facts(
        self,
        *,
        scope: str = "",
        subject: str = "",
        predicate: str = "",
        as_of: str = "",
        limit: int = 100,
    ) -> list[TemporalFactRecord]:
        """按当前状态或指定时间点查询事实。

        Args:
            scope: 可选作用域。
            subject: 可选主体。
            predicate: 可选谓词。
            as_of: 可选有效时间点；为空时只返回当前事实。
            limit: 最大返回数量，非正数表示不限制。

        Returns:
            按最近版本排序的事实列表。
        """

        def dbwork() -> list[TemporalFactRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if self._text(scope):
                scope_text = self._text(scope)
                if scope_text != "global":
                    clauses.append("(scope = ? OR scope = 'global')")
                    params.append(scope_text)
                else:
                    clauses.append("scope = ?")
                    params.append(scope_text)
            for column, value in (("subject", subject), ("predicate", predicate)):
                if self._text(value):
                    clauses.append(f"{column} = ?")
                    params.append(self._text(value))
            if as_of:
                point = self._text(as_of)
                clauses.extend(["valid_from <= ?", "(valid_to = '' OR valid_to > ?)"])
                params.extend([point, point])
            else:
                clauses.extend(["status = 'active'", "valid_to = ''"])
            sql = "SELECT * FROM temporal_facts"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY valid_from DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_temporal_fact(row) for row in rows]

        return await self._run_db(dbwork)

    async def upsert_current_temporal_fact(
        self,
        *,
        scope: str,
        subject: str,
        predicate: str,
        object_value: Any,
        observed_at: str = "",
        confidence: float = 1.0,
        source: str = "observation",
        source_type: str = "",
        source_id: str = "",
        provenance: dict[str, Any] | None = None,
        evidence_summary: str = "",
    ) -> TemporalFactRecord | None:
        """按当前值自动创建、替代或确认一条时间事实。

        Args:
            scope: 事实作用域。
            subject: 稳定主体标识。
            predicate: 稳定谓词标识。
            object_value: 当前有效的结构化值。
            observed_at: 本次观察时间。
            confidence: 观察置信度。
            source: 来源类别。
            source_type: 细分来源类型。
            source_id: 可追溯的来源编号。
            provenance: 补充来源信息。
            evidence_summary: 可供审计的证据摘要。

        Returns:
            当前有效事实；输入无效时返回空。
        """

        normalized_scope = self._text(scope)
        normalized_subject = self._text(subject)
        normalized_predicate = self._text(predicate)
        if not (normalized_scope and normalized_subject and normalized_predicate):
            return None
        current = await self.get_current_temporal_fact(
            normalized_scope, normalized_subject, normalized_predicate
        )
        operation = "ADD"
        if current is not None:
            operation = "NONE" if current.object_value == object_value else "UPDATE"
        fact = await self.write_temporal_fact(
            operation,
            {
                "scope": normalized_scope,
                "subject": normalized_subject,
                "predicate": normalized_predicate,
                "object_value": object_value,
                "observed_at": observed_at,
                "valid_from": observed_at,
                "confidence": confidence,
                "source": source,
                "source_type": source_type,
                "source_id": source_id,
                "provenance": provenance or {},
            },
        )
        if fact and operation != "NONE" and evidence_summary:
            await self.add_fact_evidence_signal(
                {
                    "fact_id": fact.id,
                    "signal": "reinforce",
                    "weight": 1.0,
                    "confidence": confidence,
                    "summary": evidence_summary,
                    "source": source,
                    "source_id": source_id,
                    "observed_at": observed_at,
                    "provenance": provenance or {},
                }
            )
        return fact

    def _compose_fact_evidence(self, row: sqlite3.Row) -> FactEvidenceSignalRecord:
        return FactEvidenceSignalRecord(
            id=int(row["id"] or 0),
            fact_id=int(row["fact_id"] or 0),
            signal=self._text(row["signal"]) or "reinforce",
            weight=max(float(row["weight"] or 0.0), 0.0),
            confidence=max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
            summary=self._text(row["summary"]),
            source=self._text(row["source"]) or "observation",
            source_id=self._text(row["source_id"]),
            observed_at=self._text(row["observed_at"]),
            provenance=self._cognition_value(row["provenance_json"], default={}),
            created_at=self._text(row["created_at"]),
        )

    async def add_fact_evidence_signal(
        self, signal: FactEvidenceSignalRecord | dict[str, Any]
    ) -> FactEvidenceSignalRecord:
        """记录事实的强化或反驳证据。

        Args:
            signal: 包含事实编号和显式 signal 类型的证据。

        Returns:
            已保存的证据信号。

        Raises:
            ValueError: 信号无效或目标事实不存在。
        """

        item = FactEvidenceSignalRecord.from_value(signal)
        if not item:
            raise ValueError("事实证据必须包含 fact_id 和有效的 signal")

        def dbwork() -> FactEvidenceSignalRecord:
            fact = self._conn.execute(
                "SELECT 1 FROM temporal_facts WHERE id = ?", (item.fact_id,)
            ).fetchone()
            if not fact:
                raise ValueError("目标事实不存在")
            cursor = self._conn.execute(
                """
                INSERT INTO fact_evidence_signals(
                    fact_id, signal, weight, confidence, summary, source,
                    source_id, observed_at, provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    item.fact_id,
                    item.signal,
                    item.weight,
                    item.confidence,
                    item.summary,
                    item.source,
                    item.source_id,
                    item.observed_at or self._cognition_now(),
                    self._cognition_json(item.provenance, default={}),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM fact_evidence_signals WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
            return self._compose_fact_evidence(row)

        return await self._run_db(dbwork)

    async def get_temporal_fact_confidence(self, fact_id: int) -> float:
        """聚合事实本体和正反证据的置信度。

        Args:
            fact_id: 事实编号。

        Returns:
            零到一之间的聚合置信度。

        Raises:
            ValueError: 目标事实不存在。
        """

        def dbwork() -> float:
            fact = self._conn.execute(
                "SELECT confidence FROM temporal_facts WHERE id = ?", (int(fact_id),)
            ).fetchone()
            if not fact:
                raise ValueError("目标事实不存在")
            rows = self._conn.execute(
                "SELECT signal, weight, confidence FROM fact_evidence_signals WHERE fact_id = ?",
                (int(fact_id),),
            ).fetchall()
            weighted_total = sum(
                max(float(row["weight"] or 0.0), 0.0)
                * max(0.0, min(float(row["confidence"] or 0.0), 1.0))
                for row in rows
            )
            signed_total = sum(
                (1.0 if row["signal"] == "reinforce" else -1.0)
                * max(float(row["weight"] or 0.0), 0.0)
                * max(0.0, min(float(row["confidence"] or 0.0), 1.0))
                for row in rows
            )
            adjustment = 0.35 * signed_total / (1.0 + weighted_total)
            return max(0.0, min(float(fact["confidence"] or 0.0) + adjustment, 1.0))

        return await self._run_db(dbwork)

    def _compose_reflection(self, row: sqlite3.Row) -> ReflectionRecord:
        return ReflectionRecord(
            id=int(row["id"] or 0),
            scope=self._text(row["scope"]),
            kind=self._text(row["kind"]) or "reflection",
            summary=self._text(row["summary"]),
            importance=max(0.0, min(float(row["importance"] or 0.0), 1.0)),
            evidence_ids=self._cognition_value(row["evidence_ids_json"], default=[]),
            assertion_subject=self._text(row["assertion_subject"]),
            assertion_predicate=self._text(row["assertion_predicate"]),
            assertion_object=self._cognition_value(
                row["assertion_object_json"], default=None
            ),
            confidence=max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
            status=self._text(row["status"]) or "pending",
            source=self._text(row["source"]) or "reflection",
            promoted_at=self._text(row["promoted_at"]),
            created_at=self._text(row["created_at"]),
            updated_at=self._text(row["updated_at"]),
        )

    async def save_reflection(
        self, reflection: ReflectionRecord | dict[str, Any]
    ) -> ReflectionRecord:
        """保存候选反思及其证据引用。

        Args:
            reflection: 反思内容、重要度和证据编号。

        Returns:
            已保存的反思记录。

        Raises:
            ValueError: 反思缺少摘要。
        """

        item = ReflectionRecord.from_value(reflection)
        if not item:
            raise ValueError("反思摘要不能为空")

        def dbwork() -> ReflectionRecord:
            cursor = self._conn.execute(
                """
                INSERT INTO reflections(
                    scope, kind, summary, importance, evidence_ids_json,
                    assertion_subject, assertion_predicate, assertion_object_json,
                    confidence, status, source, promoted_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    item.scope,
                    item.kind,
                    item.summary,
                    item.importance,
                    self._cognition_json(item.evidence_ids, default=[]),
                    item.assertion_subject,
                    item.assertion_predicate,
                    self._cognition_json(item.assertion_object, default=None),
                    item.confidence,
                    item.status,
                    item.source,
                    item.promoted_at,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM reflections WHERE id = ?", (int(cursor.lastrowid),)
            ).fetchone()
            return self._compose_reflection(row)

        return await self._run_db(dbwork)

    async def get_reflections(
        self, *, scope: str = "", status: str = "", limit: int = 100
    ) -> list[ReflectionRecord]:
        """读取候选反思，供面板和审计查看。"""

        def dbwork() -> list[ReflectionRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if scope:
                clauses.append("scope = ?")
                params.append(self._text(scope))
            if status:
                clauses.append("status = ?")
                params.append(self._text(status))
            sql = "SELECT * FROM reflections"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_reflection(row) for row in rows]

        return await self._run_db(dbwork)

    def _compose_persona_assertion(self, row: sqlite3.Row) -> PersonaAssertionRecord:
        return PersonaAssertionRecord(
            id=int(row["id"] or 0),
            scope=self._text(row["scope"]),
            subject=self._text(row["subject"]),
            predicate=self._text(row["predicate"]),
            object_value=self._cognition_value(row["object_json"], default=None),
            confidence=max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
            source_reflection_id=int(row["source_reflection_id"] or 0),
            valid_from=self._text(row["valid_from"]),
            valid_to=self._text(row["valid_to"]),
            status=self._text(row["status"]) or "active",
            created_at=self._text(row["created_at"]),
            updated_at=self._text(row["updated_at"]),
        )

    async def promote_reflections(
        self, *, min_importance: float = 0.7, min_evidence: int = 2, limit: int = 20
    ) -> list[PersonaAssertionRecord]:
        """将达到阈值且证据充分的反思晋升为人格断言。

        Args:
            min_importance: 最低重要度。
            min_evidence: 最少证据引用数量。
            limit: 单次最多晋升数量。

        Returns:
            本次创建的人格断言。
        """

        threshold = max(0.0, min(float(min_importance), 1.0))
        evidence_threshold = max(int(min_evidence), 1)

        def dbwork() -> list[PersonaAssertionRecord]:
            rows = self._conn.execute(
                """
                SELECT * FROM reflections
                WHERE status = 'pending' AND importance >= ?
                  AND assertion_subject <> '' AND assertion_predicate <> ''
                ORDER BY importance DESC, id ASC LIMIT ?
                """,
                (threshold, max(int(limit), 1)),
            ).fetchall()
            promoted: list[PersonaAssertionRecord] = []
            now = self._cognition_now()
            for row in rows:
                evidence_ids = self._cognition_value(
                    row["evidence_ids_json"], default=[]
                )
                if (
                    not isinstance(evidence_ids, list)
                    or len(evidence_ids) < evidence_threshold
                ):
                    continue
                self._conn.execute(
                    """
                    UPDATE persona_assertions
                    SET valid_to = ?, status = 'superseded', updated_at = CURRENT_TIMESTAMP
                    WHERE scope = ? AND subject = ? AND predicate = ?
                      AND status = 'active' AND valid_to = ''
                    """,
                    (
                        now,
                        self._text(row["scope"]),
                        self._text(row["assertion_subject"]),
                        self._text(row["assertion_predicate"]),
                    ),
                )
                cursor = self._conn.execute(
                    """
                    INSERT INTO persona_assertions(
                        scope, subject, predicate, object_json, confidence,
                        source_reflection_id, valid_from, valid_to, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, '', 'active',
                              CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        self._text(row["scope"]),
                        self._text(row["assertion_subject"]),
                        self._text(row["assertion_predicate"]),
                        self._text(row["assertion_object_json"]) or "null",
                        max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
                        int(row["id"]),
                        now,
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE reflections
                    SET status = 'promoted', promoted_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (now, int(row["id"])),
                )
                saved = self._conn.execute(
                    "SELECT * FROM persona_assertions WHERE id = ?",
                    (int(cursor.lastrowid),),
                ).fetchone()
                promoted.append(self._compose_persona_assertion(saved))
            self._conn.commit()
            return promoted

        return await self._run_db(dbwork)

    async def get_persona_assertions(
        self, *, scope: str = "", active_only: bool = True, limit: int = 100
    ) -> list[PersonaAssertionRecord]:
        """读取已晋升的人格断言。

        Args:
            scope: 可选作用域。
            active_only: 是否只返回当前断言。
            limit: 最大返回数量。

        Returns:
            人格断言列表。
        """

        def dbwork() -> list[PersonaAssertionRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if scope:
                clauses.append("scope = ?")
                params.append(self._text(scope))
            if active_only:
                clauses.extend(["status = 'active'", "valid_to = ''"])
            sql = "SELECT * FROM persona_assertions"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_persona_assertion(row) for row in rows]

        return await self._run_db(dbwork)

    def _compose_durable_task(self, row: sqlite3.Row) -> DurableTaskRecord:
        return DurableTaskRecord(
            id=int(row["id"] or 0),
            task_key=self._text(row["task_key"]),
            kind=self._text(row["kind"]),
            payload=self._cognition_value(row["payload_json"], default={}),
            status=self._text(row["status"]) or "pending",
            priority=int(row["priority"] or 0),
            available_at=self._text(row["available_at"]),
            lease_owner=self._text(row["lease_owner"]),
            lease_expires_at=self._text(row["lease_expires_at"]),
            attempts=int(row["attempts"] or 0),
            max_attempts=int(row["max_attempts"] or 0),
            last_error=self._text(row["last_error"]),
            result=self._cognition_value(row["result_json"], default={}),
            created_at=self._text(row["created_at"]),
            updated_at=self._text(row["updated_at"]),
            completed_at=self._text(row["completed_at"]),
        )

    async def enqueue_durable_task(
        self,
        task_key: str,
        kind: str,
        payload: dict[str, Any],
        *,
        priority: int = 50,
        available_at: str = "",
        max_attempts: int = 3,
    ) -> DurableTaskRecord:
        """幂等加入持久任务队列。

        Args:
            task_key: 全局幂等键。
            kind: 任务类型。
            payload: 结构化任务载荷。
            priority: 零到一百的优先级。
            available_at: 最早可执行时间。
            max_attempts: 最大租用次数。

        Returns:
            新建或已存在的任务。

        Raises:
            ValueError: 幂等键或类型为空。
        """

        key = self._text(task_key)
        task_kind = self._text(kind)
        if not (key and task_kind):
            raise ValueError("持久任务必须包含 task_key 和 kind")

        def dbwork() -> DurableTaskRecord:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO durable_tasks(
                    task_key, kind, payload_json, status, priority, available_at,
                    max_attempts, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    key,
                    task_kind,
                    self._cognition_json(payload, default={}),
                    max(0, min(int(priority), 100)),
                    self._text(available_at) or self._cognition_now(),
                    max(int(max_attempts), 1),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM durable_tasks WHERE task_key = ?", (key,)
            ).fetchone()
            return self._compose_durable_task(row)

        return await self._run_db(dbwork)

    async def get_durable_tasks(
        self, *, status: str = "", kind: str = "", limit: int = 100
    ) -> list[DurableTaskRecord]:
        """读取持久任务状态，不返回任何可执行代码。"""

        def dbwork() -> list[DurableTaskRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if status:
                clauses.append("status = ?")
                params.append(self._text(status))
            if kind:
                clauses.append("kind = ?")
                params.append(self._text(kind))
            sql = "SELECT * FROM durable_tasks"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_durable_task(row) for row in rows]

        return await self._run_db(dbwork)

    async def recover_expired_durable_tasks(self, *, now: str = "") -> int:
        """释放过期租约并终止超过重试上限的任务。

        Args:
            now: 用于判断租约的时间，默认当前本地时间。

        Returns:
            被恢复或终止的任务数量。
        """

        point = self._text(now) or self._cognition_now()

        def dbwork() -> int:
            terminal = self._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'dead', lease_owner = '', lease_expires_at = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'leased' AND lease_expires_at <> ?
                  AND lease_expires_at <= ? AND attempts >= max_attempts
                """,
                ("", point),
            )
            recovered = self._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'pending', lease_owner = '', lease_expires_at = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'leased' AND lease_expires_at <> ?
                  AND lease_expires_at <= ? AND attempts < max_attempts
                """,
                ("", point),
            )
            self._conn.commit()
            return int(terminal.rowcount or 0) + int(recovered.rowcount or 0)

        return await self._run_db(dbwork)

    async def recover_leased_durable_tasks(self) -> int:
        """在插件启动时释放旧进程留下的全部任务租约。"""

        def dbwork() -> int:
            cursor = self._conn.execute(
                """
                UPDATE durable_tasks
                SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                    lease_owner = '', lease_expires_at = '', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'leased'
                """
            )
            self._conn.commit()
            return max(int(cursor.rowcount or 0), 0)

        return await self._run_db(dbwork)

    async def lease_durable_tasks(
        self,
        owner: str,
        *,
        limit: int = 1,
        lease_seconds: int = 60,
        now: str = "",
        exclude_kinds: tuple[str, ...] | list[str] = (),
    ) -> list[DurableTaskRecord]:
        """原子租用到期且可执行的持久任务。

        Args:
            owner: 当前执行器标识。
            limit: 最大租用数量。
            lease_seconds: 租约持续秒数。
            now: 可注入的当前时间。

        Returns:
            已归属当前执行器的任务列表。

        Raises:
            ValueError: 执行器标识为空。
        """

        lease_owner = self._text(owner)
        if not lease_owner:
            raise ValueError("任务租约必须包含 owner")
        point = self._text(now) or self._cognition_now()
        try:
            point_value = datetime.datetime.fromisoformat(point.replace("Z", "+00:00"))
            point_value = point_value.replace(tzinfo=None)
        except ValueError as exc:
            raise ValueError("任务租约时间格式无效") from exc
        lease_until = (
            point_value + datetime.timedelta(seconds=max(lease_seconds, 1))
        ).strftime("%Y-%m-%d %H:%M:%S")

        def dbwork() -> list[DurableTaskRecord]:
            self._conn.execute(
                """
                UPDATE durable_tasks
                SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                    lease_owner = '', lease_expires_at = '', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'leased' AND lease_expires_at <> '' AND lease_expires_at <= ?
                """,
                (point,),
            )
            excluded = tuple(
                sorted({self._text(item) for item in exclude_kinds if self._text(item)})
            )
            where = (
                "status = 'pending' AND attempts < max_attempts "
                "AND (available_at = '' OR available_at <= ?)"
            )
            params: list[Any] = [point]
            if excluded:
                placeholders = ",".join("?" for _ in excluded)
                where += f" AND kind NOT IN ({placeholders})"
                params.extend(excluded)
            params.append(max(int(limit), 1))
            rows = self._conn.execute(
                "SELECT id FROM durable_tasks WHERE "
                + where
                + " ORDER BY priority DESC, id ASC LIMIT ?",
                tuple(params),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            for task_id in ids:
                self._conn.execute(
                    """
                    UPDATE durable_tasks
                    SET status = 'leased', lease_owner = ?, lease_expires_at = ?,
                        attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pending'
                    """,
                    (lease_owner, lease_until, task_id),
                )
            self._conn.commit()
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            leased = self._conn.execute(
                f"SELECT * FROM durable_tasks WHERE id IN ({placeholders}) AND lease_owner = ? ORDER BY priority DESC, id ASC",
                (*ids, lease_owner),
            ).fetchall()
            return [self._compose_durable_task(row) for row in leased]

        return await self._run_db(dbwork)

    async def complete_durable_task(
        self, task_id: int, result: dict[str, Any], *, owner: str = ""
    ) -> bool:
        """完成已租用任务并持久化结果。

        Args:
            task_id: 任务编号。
            result: 结构化执行结果。
            owner: 可选租约持有者校验。

        Returns:
            是否成功完成任务。
        """

        def dbwork() -> bool:
            clauses = ["id = ?", "status = 'leased'"]
            params: list[Any] = [int(task_id)]
            if owner:
                clauses.append("lease_owner = ?")
                params.append(self._text(owner))
            cursor = self._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'completed', result_json = ?, lease_owner = '',
                    lease_expires_at = '', completed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE """
                + " AND ".join(clauses),
                (
                    self._cognition_json(result, default={}),
                    self._cognition_now(),
                    *params,
                ),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self._run_db(dbwork)

    async def complete_durable_task_by_key(
        self, task_key: str, result: dict[str, Any]
    ) -> bool:
        """按幂等键完成尚未被租用的持久任务。

        Args:
            task_key: 入队时使用的全局幂等键。
            result: 任务最终结果。

        Returns:
            是否成功收束任务。
        """

        key = self._text(task_key)
        if not key:
            return False

        def dbwork() -> bool:
            cursor = self._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'completed', result_json = ?, lease_owner = '',
                    lease_expires_at = '', completed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_key = ? AND status IN ('pending', 'leased')
                """,
                (
                    self._cognition_json(result, default={}),
                    self._cognition_now(),
                    key,
                ),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self._run_db(dbwork)

    async def fail_durable_task_by_key(
        self, task_key: str, error: str, result: dict[str, Any] | None = None
    ) -> bool:
        """按幂等键收束不可重试的外部任务失败。

        外部异步任务已经有明确终态时，不能把错误结果写成 completed；
        失败状态必须在重启后仍可被恢复器和状态查询识别。
        """

        key = self._text(task_key)
        if not key:
            return False

        def dbwork() -> bool:
            cursor = self._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'failed', result_json = ?, last_error = ?,
                    lease_owner = '', lease_expires_at = '',
                    completed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_key = ? AND status IN ('pending', 'leased')
                """,
                (
                    self._cognition_json(result, default={}),
                    self._text(error),
                    self._cognition_now(),
                    key,
                ),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self._run_db(dbwork)

    async def finalize_durable_task(
        self, task_id: int, result: dict[str, Any]
    ) -> bool:
        """收束尚未被工作器租用的已完成任务。

        适用于“产物已生成，当前请求正在投递”的短窗口。任务先入库以便
        崩溃后恢复；当前投递成功或明确取消后无需等待工作器再次租用。

        Args:
            task_id: 任务编号。
            result: 最终投递结果。

        Returns:
            是否成功把待执行任务标记为已完成。
        """

        def dbwork() -> bool:
            cursor = self._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'completed', result_json = ?, lease_owner = '',
                    lease_expires_at = '', completed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (
                    self._cognition_json(result, default={}),
                    self._cognition_now(),
                    int(task_id),
                ),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self._run_db(dbwork)

    async def fail_durable_task(
        self,
        task_id: int,
        error: str,
        *,
        owner: str = "",
        retry_at: str = "",
    ) -> bool:
        """记录失败并按剩余次数重排或终止任务。

        Args:
            task_id: 任务编号。
            error: 失败原因。
            owner: 可选租约持有者校验。
            retry_at: 下次可执行时间。

        Returns:
            是否成功更新任务。
        """

        def dbwork() -> bool:
            clauses = ["id = ?", "status = 'leased'"]
            params: list[Any] = [int(task_id)]
            if owner:
                clauses.append("lease_owner = ?")
                params.append(self._text(owner))
            cursor = self._conn.execute(
                """
                UPDATE durable_tasks
                SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                    last_error = ?, available_at = ?, lease_owner = '',
                    lease_expires_at = '', updated_at = CURRENT_TIMESTAMP
                WHERE """
                + " AND ".join(clauses),
                (
                    self._text(error),
                    self._text(retry_at) or self._cognition_now(),
                    *params,
                ),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self._run_db(dbwork)

    def _compose_decision_trace(self, row: sqlite3.Row) -> DecisionTraceRecord:
        return DecisionTraceRecord(
            id=int(row["id"] or 0),
            trace_id=self._text(row["trace_id"]),
            scope=self._text(row["scope"]),
            stage=self._text(row["stage"]),
            reason_code=self._text(row["reason_code"]),
            decision=self._text(row["decision"]),
            scores=self._cognition_value(row["score_json"], default={}),
            evidence=self._cognition_value(row["evidence_json"], default=[]),
            outcome=self._text(row["outcome"]),
            created_at=self._text(row["created_at"]),
            updated_at=self._text(row["updated_at"]),
        )

    async def save_decision_trace(
        self, trace: DecisionTraceRecord | dict[str, Any]
    ) -> DecisionTraceRecord:
        """新建或推进一条统一决策轨迹。

        Args:
            trace: 决策阶段、原因码、分数和证据。

        Returns:
            已保存的轨迹。

        Raises:
            ValueError: 决策阶段为空。
        """

        raw = trace.as_dict() if isinstance(trace, DecisionTraceRecord) else dict(trace)
        trace_id = self._text(raw.get("trace_id")) or uuid.uuid4().hex
        stage = self._text(raw.get("stage"))
        if not stage:
            raise ValueError("决策轨迹必须包含 stage")

        def dbwork() -> DecisionTraceRecord:
            self._conn.execute(
                """
                INSERT INTO decision_traces(
                    trace_id, scope, stage, reason_code, decision, score_json,
                    evidence_json, outcome, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(trace_id, stage) DO UPDATE SET
                    scope = excluded.scope, stage = excluded.stage,
                    reason_code = excluded.reason_code, decision = excluded.decision,
                    score_json = excluded.score_json, evidence_json = excluded.evidence_json,
                    outcome = excluded.outcome, updated_at = CURRENT_TIMESTAMP
                """,
                (
                    trace_id,
                    self._text(raw.get("scope")),
                    stage,
                    self._text(raw.get("reason_code")),
                    self._text(raw.get("decision")),
                    self._cognition_json(raw.get("scores"), default={}),
                    self._cognition_json(raw.get("evidence"), default=[]),
                    self._text(raw.get("outcome")),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM decision_traces WHERE trace_id = ? AND stage = ?",
                (trace_id, stage),
            ).fetchone()
            return self._compose_decision_trace(row)

        return await self._run_db(dbwork)

    async def get_decision_traces(
        self, *, scope: str = "", trace_id: str = "", limit: int = 100
    ) -> list[DecisionTraceRecord]:
        """查询统一决策轨迹。

        Args:
            scope: 可选作用域。
            trace_id: 可选精确轨迹编号。
            limit: 最大返回数量。

        Returns:
            决策轨迹列表。
        """

        def dbwork() -> list[DecisionTraceRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if scope:
                clauses.append("scope = ?")
                params.append(self._text(scope))
            if trace_id:
                clauses.append("trace_id = ?")
                params.append(self._text(trace_id))
            sql = "SELECT * FROM decision_traces"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_decision_trace(row) for row in rows]

        return await self._run_db(dbwork)

    def _compose_life_action_outcome(self, row: sqlite3.Row) -> LifeActionOutcomeRecord:
        return LifeActionOutcomeRecord(
            id=int(row["id"] or 0),
            action_id=self._text(row["action_id"]),
            date=self._text(row["date"]),
            action_type=self._text(row["action_type"]),
            target=self._text(row["target"]),
            preconditions=self._cognition_value(row["preconditions_json"], default={}),
            effects=self._cognition_value(row["effects_json"], default={}),
            status=self._text(row["status"]) or "proposed",
            reason=self._text(row["reason"]),
            evidence=self._cognition_value(row["evidence_json"], default=[]),
            started_at=self._text(row["started_at"]),
            committed_at=self._text(row["committed_at"]),
            created_at=self._text(row["created_at"]),
            updated_at=self._text(row["updated_at"]),
        )

    async def save_life_action_outcome(
        self, outcome: LifeActionOutcomeRecord | dict[str, Any]
    ) -> LifeActionOutcomeRecord:
        """幂等保存生活动作的结算状态。

        Args:
            outcome: 动作编号、显式类型、前置条件、效果和状态。

        Returns:
            已保存的动作结算记录。

        Raises:
            ValueError: 动作编号或类型为空。
        """

        serializer = getattr(outcome, "as_dict", None)
        raw = serializer() if callable(serializer) else dict(outcome)
        action_id = self._text(raw.get("action_id"))
        action_type = self._text(raw.get("action_type"))
        if not (action_id and action_type):
            raise ValueError("生活动作结算必须包含 action_id 和 action_type")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list):
            evidence = [evidence] if self._text(evidence) else []

        def dbwork() -> LifeActionOutcomeRecord:
            self._conn.execute(
                """
                INSERT INTO life_action_outcomes(
                    action_id, date, action_type, target, preconditions_json,
                    effects_json, status, reason, evidence_json, started_at,
                    committed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(action_id) DO UPDATE SET
                    date = excluded.date, action_type = excluded.action_type,
                    target = excluded.target, preconditions_json = excluded.preconditions_json,
                    effects_json = excluded.effects_json, status = excluded.status,
                    reason = excluded.reason, evidence_json = excluded.evidence_json,
                    started_at = excluded.started_at, committed_at = excluded.committed_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    action_id,
                    self._text(raw.get("date")),
                    action_type,
                    self._text(raw.get("target") or raw.get("timeline_index")),
                    self._cognition_json(raw.get("preconditions"), default={}),
                    self._cognition_json(
                        raw.get("effects", raw.get("state_changes")), default={}
                    ),
                    self._text(raw.get("status")) or "proposed",
                    self._text(raw.get("reason")),
                    self._cognition_json(evidence, default=[]),
                    self._text(raw.get("started_at")),
                    self._text(raw.get("committed_at")),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM life_action_outcomes WHERE action_id = ?", (action_id,)
            ).fetchone()
            return self._compose_life_action_outcome(row)

        return await self._run_db(dbwork)

    async def get_life_action_outcomes(
        self, *, date: str = "", status: str = "", limit: int = 100
    ) -> list[LifeActionOutcomeRecord]:
        """查询生活动作结算记录。

        Args:
            date: 可选日期。
            status: 可选状态。
            limit: 最大返回数量。

        Returns:
            动作结算记录列表。
        """

        def dbwork() -> list[LifeActionOutcomeRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if date:
                clauses.append("date = ?")
                params.append(self._text(date))
            if status:
                clauses.append("status = ?")
                params.append(self._text(status))
            sql = "SELECT * FROM life_action_outcomes"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY date DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_life_action_outcome(row) for row in rows]

        return await self._run_db(dbwork)

    def _compose_life_action_receipt(
        self, row: sqlite3.Row
    ) -> LifeActionReceiptRecord:
        return LifeActionReceiptRecord(
            id=int(row["id"] or 0),
            receipt_id=self._text(row["receipt_id"]),
            action_id=self._text(row["action_id"]),
            date=self._text(row["date"]),
            action_type=self._text(row["action_type"]),
            status=self._text(row["status"]) or "confirmed",
            evidence=self._cognition_value(row["evidence_json"], default=[]),
            source=self._text(row["source"]),
            source_id=self._text(row["source_id"]),
            artifact_path=self._text(row["artifact_path"]),
            occurred_at=self._text(row["occurred_at"]),
            created_at=self._text(row["created_at"]),
        )

    async def save_life_action_receipt(
        self, receipt: LifeActionReceiptRecord | dict[str, Any]
    ) -> LifeActionReceiptRecord:
        """幂等保存一条动作执行回执。

        Args:
            receipt: 动作、来源、证据和执行状态。

        Returns:
            已持久化的回执。

        Raises:
            ValueError: 动作编号或回执编号缺失时抛出。
        """

        serializer = getattr(receipt, "as_dict", None)
        raw = serializer() if callable(serializer) else dict(receipt)
        receipt_id = self._text(raw.get("receipt_id")) or uuid.uuid4().hex
        action_id = self._text(raw.get("action_id"))
        if not action_id:
            raise ValueError("动作回执必须包含 action_id")
        status = self._text(raw.get("status") or "confirmed").lower()
        if status not in {"confirmed", "simulated", "failed", "cancelled"}:
            raise ValueError("动作回执状态无效")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list):
            evidence = [evidence] if self._text(evidence) else []

        def dbwork() -> LifeActionReceiptRecord:
            self._conn.execute(
                """
                INSERT INTO life_action_receipts(
                    receipt_id, action_id, date, action_type, status, evidence_json,
                    source, source_id, artifact_path, occurred_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(receipt_id) DO UPDATE SET
                    action_id = excluded.action_id, date = excluded.date,
                    action_type = excluded.action_type, status = excluded.status,
                    evidence_json = excluded.evidence_json, source = excluded.source,
                    source_id = excluded.source_id, artifact_path = excluded.artifact_path,
                    occurred_at = excluded.occurred_at
                """,
                (
                    receipt_id,
                    action_id,
                    self._text(raw.get("date")),
                    self._text(raw.get("action_type")),
                    status,
                    self._cognition_json(evidence, default=[]),
                    self._text(raw.get("source")),
                    self._text(raw.get("source_id")),
                    self._text(raw.get("artifact_path"))[:1000],
                    self._text(raw.get("occurred_at")),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM life_action_receipts WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
            return self._compose_life_action_receipt(row)

        return await self._run_db(dbwork)

    async def get_life_action_receipts(
        self, *, action_id: str = "", limit: int = 100
    ) -> list[LifeActionReceiptRecord]:
        """读取动作的来源回执。

        Args:
            action_id: 可选动作编号。
            limit: 最大返回数量。

        Returns:
            按最新顺序排列的动作回执。
        """

        def dbwork() -> list[LifeActionReceiptRecord]:
            params: list[Any] = []
            sql = "SELECT * FROM life_action_receipts"
            if self._text(action_id):
                sql += " WHERE action_id = ?"
                params.append(self._text(action_id))
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_life_action_receipt(row) for row in rows]

        return await self._run_db(dbwork)

    def _compose_affective_state(self, row: sqlite3.Row) -> AffectiveStateRecord:
        return AffectiveStateRecord(
            id=int(row["id"] or 0),
            scope=self._text(row["scope"]),
            layer=self._text(row["layer"]) or "transient",
            label=self._text(row["label"]),
            valence=max(-1.0, min(float(row["valence"] or 0.0), 1.0)),
            arousal=max(0.0, min(float(row["arousal"] or 0.0), 1.0)),
            intensity=max(0.0, min(float(row["intensity"] or 0.0), 1.0)),
            baseline=max(0.0, min(float(row["baseline"] or 0.0), 1.0)),
            decay_half_life_minutes=max(
                float(row["decay_half_life_minutes"] or 240.0), 1.0
            ),
            evidence=self._cognition_value(row["evidence_json"], default=[]),
            valid_from=self._text(row["valid_from"]),
            valid_to=self._text(row["valid_to"]),
            status=self._text(row["status"]) or "active",
            source=self._text(row["source"]) or "state",
            created_at=self._text(row["created_at"]),
            updated_at=self._text(row["updated_at"]),
        )

    async def save_affective_state(
        self, state: AffectiveStateRecord | dict[str, Any]
    ) -> AffectiveStateRecord:
        """保存一层可衰减情绪并关闭同标签旧状态。

        Args:
            state: 三层情绪之一及其强度、基线、半衰期和证据。

        Returns:
            已保存的情绪状态。

        Raises:
            ValueError: 层级、作用域或标签无效。
        """

        raw = (
            state.as_dict() if isinstance(state, AffectiveStateRecord) else dict(state)
        )
        scope = self._text(raw.get("scope"))
        layer = self._text(raw.get("layer") or "transient")
        label = self._text(raw.get("label"))
        if (
            not scope
            or not label
            or layer not in {"transient", "daily", "relationship"}
        ):
            raise ValueError("情绪状态必须包含 scope、label 和有效 layer")
        now = self._cognition_now()
        valid_from = self._text(raw.get("valid_from")) or now
        numeric_values: dict[str, float] = {}
        for key, default in (
            ("valence", 0.0),
            ("arousal", 0.5),
            ("intensity", 0.5),
            ("baseline", 0.5),
            ("decay_half_life_minutes", 240.0),
        ):
            try:
                numeric_values[key] = (
                    float(raw[key]) if raw.get(key) is not None else default
                )
            except (TypeError, ValueError):
                numeric_values[key] = default

        def dbwork() -> AffectiveStateRecord:
            self._conn.execute(
                """
                UPDATE affective_states
                SET valid_to = ?, status = 'superseded', updated_at = CURRENT_TIMESTAMP
                WHERE scope = ? AND layer = ? AND label = ?
                  AND status = 'active' AND valid_to = ''
                """,
                (valid_from, scope, layer, label),
            )
            cursor = self._conn.execute(
                """
                INSERT INTO affective_states(
                    scope, layer, label, valence, arousal, intensity, baseline,
                    decay_half_life_minutes, evidence_json, valid_from, valid_to,
                    status, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'active', ?,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    scope,
                    layer,
                    label,
                    max(-1.0, min(numeric_values["valence"], 1.0)),
                    max(0.0, min(numeric_values["arousal"], 1.0)),
                    max(0.0, min(numeric_values["intensity"], 1.0)),
                    max(0.0, min(numeric_values["baseline"], 1.0)),
                    max(numeric_values["decay_half_life_minutes"], 1.0),
                    self._cognition_json(raw.get("evidence"), default=[]),
                    valid_from,
                    self._text(raw.get("source")) or "state",
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM affective_states WHERE id = ?", (int(cursor.lastrowid),)
            ).fetchone()
            return self._compose_affective_state(row)

        return await self._run_db(dbwork)

    async def get_affective_states(
        self,
        *,
        scope: str = "",
        layer: str = "",
        active_only: bool = True,
        at: str = "",
        apply_decay: bool = True,
        limit: int = 100,
    ) -> list[AffectiveStateRecord]:
        """读取三层情绪，并按半衰期计算当前强度。

        Args:
            scope: 可选作用域。
            layer: 可选情绪层级。
            active_only: 是否只返回当前状态。
            at: 衰减计算时间。
            apply_decay: 是否返回衰减后的强度。
            limit: 最大返回数量。

        Returns:
            情绪状态列表。
        """

        def dbwork() -> list[AffectiveStateRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if scope:
                clauses.append("scope = ?")
                params.append(self._text(scope))
            if layer:
                clauses.append("layer = ?")
                params.append(self._text(layer))
            if active_only:
                clauses.extend(["status = 'active'", "valid_to = ''"])
            sql = "SELECT * FROM affective_states"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY valid_from DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            states = [self._compose_affective_state(row) for row in rows]
            if not apply_decay:
                return states
            try:
                point = datetime.datetime.fromisoformat(
                    (self._text(at) or self._cognition_now()).replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                point = datetime.datetime.now()
            decayed: list[AffectiveStateRecord] = []
            for item in states:
                try:
                    started = datetime.datetime.fromisoformat(
                        item.valid_from.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except ValueError:
                    decayed.append(item)
                    continue
                elapsed_minutes = max((point - started).total_seconds() / 60.0, 0.0)
                factor = math.pow(0.5, elapsed_minutes / item.decay_half_life_minutes)
                intensity = item.baseline + (item.intensity - item.baseline) * factor
                decayed.append(replace(item, intensity=max(0.0, min(intensity, 1.0))))
            return decayed

        return await self._run_db(dbwork)

    def _compose_grounded_diary(self, row: sqlite3.Row) -> GroundedDiaryEntryRecord:
        return GroundedDiaryEntryRecord(
            id=int(row["id"] or 0),
            date=self._text(row["date"]),
            scope=self._text(row["scope"]),
            title=self._text(row["title"]),
            summary=self._text(row["summary"]),
            evidence_ids=self._cognition_value(row["evidence_ids_json"], default=[]),
            mood_label=self._text(row["mood_label"]),
            source=self._text(row["source"]) or "daily_review",
            created_at=self._text(row["created_at"]),
            updated_at=self._text(row["updated_at"]),
        )

    async def save_grounded_diary_entry(
        self, entry: GroundedDiaryEntryRecord | dict[str, Any]
    ) -> GroundedDiaryEntryRecord:
        """保存带证据引用的第一人称日记。

        Args:
            entry: 日期、作用域、摘要和至少一个证据编号。

        Returns:
            已保存的日记条目。

        Raises:
            ValueError: 日期、作用域、摘要或证据引用缺失。
        """

        raw = (
            entry.as_dict()
            if isinstance(entry, GroundedDiaryEntryRecord)
            else dict(entry)
        )
        date = self._text(raw.get("date"))
        scope = self._text(raw.get("scope"))
        summary = self._text(raw.get("summary"))
        evidence_ids = raw.get("evidence_ids")
        if not date or not scope or not summary:
            raise ValueError("日记必须包含 date、scope 和 summary")
        if not isinstance(evidence_ids, list) or not any(
            self._text(item) for item in evidence_ids
        ):
            raise ValueError("日记必须引用至少一条已落库证据")

        def dbwork() -> GroundedDiaryEntryRecord:
            self._conn.execute(
                """
                INSERT INTO grounded_diary_entries(
                    date, scope, title, summary, evidence_ids_json, mood_label,
                    source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(date, scope) DO UPDATE SET
                    title = excluded.title, summary = excluded.summary,
                    evidence_ids_json = excluded.evidence_ids_json,
                    mood_label = excluded.mood_label, source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    date,
                    scope,
                    self._text(raw.get("title")),
                    summary,
                    self._cognition_json(evidence_ids, default=[]),
                    self._text(raw.get("mood_label")),
                    self._text(raw.get("source")) or "daily_review",
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM grounded_diary_entries WHERE date = ? AND scope = ?",
                (date, scope),
            ).fetchone()
            return self._compose_grounded_diary(row)

        return await self._run_db(dbwork)

    async def get_grounded_diary_entries(
        self, *, scope: str = "", limit: int = 30
    ) -> list[GroundedDiaryEntryRecord]:
        """读取最近的有证据日记。

        Args:
            scope: 可选作用域。
            limit: 最大返回数量。

        Returns:
            日记条目列表。
        """

        def dbwork() -> list[GroundedDiaryEntryRecord]:
            sql = "SELECT * FROM grounded_diary_entries"
            params: list[Any] = []
            if scope:
                sql += " WHERE scope = ?"
                params.append(self._text(scope))
            sql += " ORDER BY date DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_grounded_diary(row) for row in rows]

        return await self._run_db(dbwork)


__all__ = ["CognitionArchiveMixin"]
