from __future__ import annotations

import datetime
import hashlib
import json
import math
import sqlite3
import uuid
from dataclasses import replace
from typing import Any

from ..clock import local_datetime, now as life_now
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
from ..models.coerce import compact_explanation_text

from typing import Protocol


class CognitionArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _cognition_json: Any
    _cognition_now: Any
    _cognition_timestamp: Any
    _cognition_value: Any
    _compose_affective_state: Any
    _compose_decision_trace: Any
    _compose_durable_task: Any
    _compose_fact_evidence: Any
    _compose_grounded_diary: Any
    _compose_life_action_outcome: Any
    _compose_life_action_receipt: Any
    _compose_persona_assertion: Any
    _compose_reflection: Any
    _compose_temporal_fact: Any
    _conn: Any
    _flag: Any
    _run_db: Any
    _temporal_source_priority: Any
    _text: Any
    add_fact_evidence_signal: Any
    get_current_temporal_fact: Any
    write_temporal_fact: Any


class CognitionArchiveComponent:
    """持久化时间化认知、任务、情绪和动作结算。"""

    def __init__(self, context: CognitionArchiveComponentContext) -> None:
        self.context = context

    async def record_public_activity(self, receipt: dict[str, Any]) -> bool:
        """持久化幂等的发布回执，不触发聊天侧效果。"""
        event_id = str(receipt.get("event_id") or "").strip()
        if not event_id:
            return False
        key = "public_activity:" + hashlib.sha256(event_id.encode()).hexdigest()
        payload = {
            key: receipt[key]
            for key in (
                "event_id",
                "scene",
                "content",
                "post_id",
                "comment_id",
                "actor_id",
                "image_sent",
                "occurred_at", "scope", "photo_key", "post_context", "conversation",
                "reply_to_comment_id", "reply_to_author_id",
            )
            if key in receipt
        }

        def write() -> bool:
            cursor = self.context._conn.execute(
                "INSERT OR IGNORE INTO durable_tasks(task_key, kind, payload_json, status, priority, available_at, max_attempts, result_json, created_at, updated_at, completed_at) VALUES (?, 'public_activity_receipt', ?, 'completed', 0, ?, 1, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (key, self.context._cognition_json(payload, default={}), self.context._cognition_now()),
            )
            self.context._conn.commit()
            return bool(cursor.rowcount)

        await self.context._run_db(write)
        return True

    async def record_external_activity_receipt(self, receipt: dict) -> bool:
        """保存原始发送事件；返回是否首次插入，不把重试时间当成发生时间。"""
        event_id = str(receipt.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("活动回执缺少事件 ID")
        key = "external_activity:" + hashlib.sha256(event_id.encode()).hexdigest()

        def write():
            cursor = self.context._conn.execute(
                "INSERT OR IGNORE INTO durable_tasks(task_key, kind, payload_json, status, priority, available_at, max_attempts, result_json, created_at, updated_at, completed_at) VALUES (?, 'external_activity_receipt', ?, 'completed', 0, ?, 1, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (key, self.context._cognition_json(receipt, default={}), self.context._cognition_now()),
            )
            self.context._conn.commit()
            return bool(cursor.rowcount)
        return await self.context._run_db(write)

    async def read_share_activity_receipts(self, scope: str, *, limit: int = 24) -> list:
        """仅向原会话或公开空间返回活动，禁止扩大私聊可见范围。"""
        def read():
            rows = self.context._conn.execute(
                "SELECT kind, payload_json FROM durable_tasks WHERE kind IN ('external_activity_receipt', 'public_activity_receipt') AND status = 'completed' ORDER BY id DESC LIMIT 256"
            ).fetchall()
            result = []
            private = ":FriendMessage:" in scope or ":GroupMessage:" in scope
            for row in rows:
                payload = json.loads(row["payload_json"] or "{}")
                if row["kind"] == "external_activity_receipt":
                    if not private or payload.get("target_umo") != scope:
                        continue
                elif scope != "qzone_broadcast":
                    continue
                result.append(payload)
                if len(result) >= max(1, min(32, limit)):
                    break
            return result
        return await self.context._run_db(read)

    _TEMPORAL_SOURCE_PRIORITY = {
        "life_action_receipt": 500,
        "life_action": 450,
        "state_refresh": 400,
        "daily_state": 350,
        "invite": 300,
        "manual": 300,
        "chat": 200,
        "chat_batch_audited": 190,
        "chat_turn_semantic": 180,
        "chat_batch": 100,
    }

    @staticmethod
    def _cognition_now() -> str:
        return life_now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _cognition_timestamp(value: Any) -> datetime.datetime | None:
        return local_datetime(value)

    @staticmethod
    def _temporal_time_text(value: Any) -> str:
        """把事实时间统一成角色生活时区，保留已有记录的本地时间语义。"""
        parsed = CognitionArchiveComponent._cognition_timestamp(value)
        if parsed is None:
            raise ValueError("时间化事实的时间格式无效")
        return parsed.isoformat(
            sep=" ", timespec="microseconds" if parsed.microsecond else "seconds"
        )

    @classmethod
    def _temporal_source_priority(cls, source: Any) -> int:
        return cls._TEMPORAL_SOURCE_PRIORITY.get(str(source or "").strip(), 250)

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
            scope=self.context._text(row["scope"]),
            subject=self.context._text(row["subject"]),
            predicate=self.context._text(row["predicate"]),
            object_value=self.context._cognition_value(row["object_json"], default=None),
            observed_at=self.context._text(row["observed_at"]),
            valid_from=self.context._text(row["valid_from"]),
            valid_to=self.context._text(row["valid_to"]),
            confidence=max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
            status=self.context._text(row["status"]) or "active",
            source=self.context._text(row["source"]) or "observation",
            source_type=self.context._text(row["source_type"]),
            source_id=self.context._text(row["source_id"]),
            provenance=self.context._cognition_value(row["provenance_json"], default={}),
            supersedes_id=int(row["supersedes_id"] or 0),
            created_at=self.context._text(row["created_at"]),
            updated_at=self.context._text(row["updated_at"]),
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

        action = self.context._text(operation).upper()
        if action not in {"ADD", "UPDATE", "INVALIDATE", "NONE"}:
            raise ValueError("事实操作必须是 ADD、UPDATE、INVALIDATE 或 NONE")
        item = TemporalFactRecord.from_value(fact)
        if not item:
            raise ValueError("时间化事实必须包含 scope、subject 和 predicate")
        now = self.context._cognition_now()
        effective_at = self._temporal_time_text(item.valid_from or item.observed_at or now)
        observed_at = self._temporal_time_text(item.observed_at or now)
        valid_to = self._temporal_time_text(item.valid_to) if item.valid_to else ""
        if valid_to and valid_to <= effective_at:
            raise ValueError("时间化事实的失效时间必须晚于生效时间")
        object_json = self.context._cognition_json(item.object_value, default=None)

        def dbwork() -> TemporalFactRecord | None:
            def close_uncertain():
                self.context._conn.execute(
                    "UPDATE temporal_facts SET status='superseded',valid_to=?,updated_at=CURRENT_TIMESTAMP WHERE scope=? AND subject=? AND predicate=? AND status='uncertain' AND life_timestamp(valid_from)<=life_timestamp(?)",
                    (effective_at, item.scope, item.subject, item.predicate, effective_at),
                )

            epistemic = item.provenance.get("epistemic_status")
            if epistemic in {"inferred", "uncertain"} and action != "NONE":
                if action == "INVALIDATE":
                    return None
                newer = self.context._conn.execute(
                    "SELECT id FROM temporal_facts WHERE scope=? AND subject=? AND predicate=? AND status<>'uncertain' AND COALESCE(json_extract(provenance_json,'$.epistemic_status'),'') NOT IN ('inferred','uncertain') AND life_timestamp(valid_from)>life_timestamp(?) LIMIT 1",
                    (item.scope, item.subject, item.predicate, effective_at),
                ).fetchone()
                if newer:
                    return None
                # 推测独立保存，不能替代或撤销当前事实；同一来源重复处理不增加可信度。
                previous = self.context._conn.execute(
                    "SELECT * FROM temporal_facts WHERE scope=? AND subject=? AND predicate=? AND status='uncertain' AND source_id=? AND object_json=? ORDER BY id DESC LIMIT 1",
                    (item.scope, item.subject, item.predicate, item.source_id, object_json),
                ).fetchone()
                if previous:
                    return self.context._compose_temporal_fact(previous)
                confidence = min(item.confidence, 0.5 if epistemic == "inferred" else 0.4)
                start = self.context._cognition_timestamp(effective_at)
                end = (start + datetime.timedelta(days=7)).isoformat(sep=" ") if start else now
                cursor = self.context._conn.execute(
                    "INSERT INTO temporal_facts(scope,subject,predicate,object_json,observed_at,valid_from,valid_to,confidence,status,source,source_type,source_id,provenance_json) VALUES(?,?,?,?,?,?,?,?,'uncertain',?,?,?,?)",
                    (item.scope, item.subject, item.predicate, object_json, observed_at,
                     effective_at, end, confidence, item.source, item.source_type, item.source_id,
                     self.context._cognition_json(item.provenance, default={})),
                )
                self.context._conn.commit()
                return self.context._compose_temporal_fact(self.context._conn.execute("SELECT * FROM temporal_facts WHERE id=?", (cursor.lastrowid,)).fetchone())
            current = self.context._conn.execute(
                """
                SELECT * FROM temporal_facts
                WHERE scope = ? AND subject = ? AND predicate = ?
                  AND status IN ('active', 'superseded', 'invalidated')
                  AND life_timestamp(valid_from) <= life_timestamp(?)
                  AND (valid_to = '' OR life_timestamp(valid_to) > life_timestamp(?))
                ORDER BY life_timestamp(valid_from) DESC, id DESC LIMIT 1
                """,
                (item.scope, item.subject, item.predicate, effective_at, effective_at),
            ).fetchone()
            next_row = self.context._conn.execute(
                """
                SELECT * FROM temporal_facts
                WHERE scope = ? AND subject = ? AND predicate = ?
                  AND status IN ('active', 'superseded', 'invalidated')
                  AND life_timestamp(valid_from) > life_timestamp(?)
                ORDER BY life_timestamp(valid_from), id LIMIT 1
                """,
                (item.scope, item.subject, item.predicate, effective_at),
            ).fetchone()
            if action == "NONE":
                return self.context._compose_temporal_fact(current) if current else None
            if next_row is not None and (
                self.context._cognition_timestamp(next_row["valid_from"])
                <= self.context._cognition_timestamp(now)
            ):
                # 延迟到达的旧观察不能覆盖已经生效的较新记录。
                return self.context._compose_temporal_fact(next_row)
            if current is not None:
                incoming_time = self.context._cognition_timestamp(effective_at)
                current_time = self.context._cognition_timestamp(
                    current["valid_from"]
                    or current["observed_at"]
                    or current["created_at"]
                )
                is_older = (
                    incoming_time is not None
                    and current_time is not None
                    and incoming_time < current_time
                )
                is_lower_at_same_time = (
                    incoming_time is not None
                    and current_time is not None
                    and incoming_time == current_time
                    and self.context._temporal_source_priority(item.source)
                    < self.context._temporal_source_priority(current["source"])
                )
                if is_older or is_lower_at_same_time:
                    return self.context._compose_temporal_fact(current)
            if action == "INVALIDATE":
                if not current:
                    return None
                self.context._conn.execute(
                    """
                    UPDATE temporal_facts
                    SET valid_to = ?, status = 'invalidated', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (effective_at, int(current["id"])),
                )
                close_uncertain()
                self.context._conn.commit()
                row = self.context._conn.execute(
                    "SELECT * FROM temporal_facts WHERE id = ?", (int(current["id"]),)
                ).fetchone()
                return self.context._compose_temporal_fact(row)
            if action == "ADD" and current:
                if self.context._text(current["object_json"]) == object_json:
                    close_uncertain()
                    self.context._conn.commit()
                    return self.context._compose_temporal_fact(current)
                raise ValueError("同一结构化事实键已有当前值，请使用 UPDATE")
            if action == "UPDATE" and not current:
                raise ValueError("要更新的结构化事实不存在，请先使用 ADD")

            supersedes_id = int(current["id"] or 0) if current else 0
            bounded_valid_to = valid_to
            if next_row is not None:
                next_start = self._temporal_time_text(next_row["valid_from"])
                if not bounded_valid_to or next_start < bounded_valid_to:
                    bounded_valid_to = next_start
            if current:
                self.context._conn.execute(
                    """
                    UPDATE temporal_facts
                    SET valid_to = ?, status = 'superseded', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (effective_at, supersedes_id),
                )
            cursor = self.context._conn.execute(
                """
                INSERT INTO temporal_facts(
                    scope, subject, predicate, object_json, observed_at, valid_from,
                    valid_to, confidence, status, source, source_type, source_id,
                    provenance_json, supersedes_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    item.scope,
                    item.subject,
                    item.predicate,
                    object_json,
                    observed_at,
                    effective_at,
                    bounded_valid_to,
                    item.confidence,
                    item.source,
                    item.source_type,
                    item.source_id,
                    self.context._cognition_json(item.provenance, default={}),
                    supersedes_id or None,
                ),
            )
            fact_id = int(cursor.lastrowid)
            close_uncertain()
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM temporal_facts WHERE id = ?", (fact_id,)
            ).fetchone()
            return self.context._compose_temporal_fact(row)

        return await self.context._run_db(dbwork)

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
            now = self.context._cognition_now()
            row = self.context._conn.execute(
                """
                SELECT * FROM temporal_facts
                WHERE scope = ? AND subject = ? AND predicate = ?
                  AND status IN ('active', 'superseded', 'invalidated')
                  AND life_timestamp(valid_from) <= life_timestamp(?)
                  AND (valid_to = '' OR life_timestamp(valid_to) > life_timestamp(?))
                ORDER BY life_timestamp(valid_from) DESC, id DESC LIMIT 1
                """,
                (self.context._text(scope), self.context._text(subject), self.context._text(predicate), now, now),
            ).fetchone()
            return self.context._compose_temporal_fact(row) if row else None

        return await self.context._run_db(dbwork)

    async def get_temporal_facts(
        self,
        *,
        scope: str = "",
        subject: str = "",
        predicate: str = "",
        as_of: str = "",
        limit: int = 100,
        include_uncertain: bool = False,
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
            if self.context._text(scope):
                scope_text = self.context._text(scope)
                if scope_text != "global":
                    clauses.append("(scope = ? OR scope = 'global')")
                    params.append(scope_text)
                else:
                    clauses.append("scope = ?")
                    params.append(scope_text)
            for column, value in (("subject", subject), ("predicate", predicate)):
                if self.context._text(value):
                    clauses.append(f"{column} = ?")
                    params.append(self.context._text(value))
            point = self._temporal_time_text(as_of) if as_of else self.context._cognition_now()
            clauses.extend([
                "life_timestamp(valid_from) <= life_timestamp(?)",
                "(valid_to = '' OR life_timestamp(valid_to) > life_timestamp(?))",
            ])
            params.extend([point, point])
            if as_of:
                if not include_uncertain:
                    clauses.append("COALESCE(json_extract(provenance_json,'$.epistemic_status'),'') NOT IN ('inferred','uncertain')")
            else:
                if include_uncertain:
                    clauses.append("status IN ('active', 'superseded', 'invalidated', 'uncertain')")
                else:
                    clauses.append("status IN ('active', 'superseded', 'invalidated')")
            sql = "SELECT * FROM temporal_facts"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY life_timestamp(valid_from) DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_temporal_fact(row) for row in rows]

        return await self.context._run_db(dbwork)

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

        normalized_scope = self.context._text(scope)
        normalized_subject = self.context._text(subject)
        normalized_predicate = self.context._text(predicate)
        if not (normalized_scope and normalized_subject and normalized_predicate):
            return None
        current = await self.context.get_current_temporal_fact(
            normalized_scope, normalized_subject, normalized_predicate
        )
        operation = "ADD"
        if current is not None:
            operation = "NONE" if current.object_value == object_value else "UPDATE"
        fact = await self.context.write_temporal_fact(
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
        wrote_new_version = bool(
            fact and operation != "NONE" and (current is None or fact.id != current.id)
        )
        if wrote_new_version and evidence_summary:
            await self.context.add_fact_evidence_signal(
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
            signal=self.context._text(row["signal"]) or "reinforce",
            weight=max(float(row["weight"] or 0.0), 0.0),
            confidence=max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
            summary=self.context._text(row["summary"]),
            source=self.context._text(row["source"]) or "observation",
            source_id=self.context._text(row["source_id"]),
            observed_at=self.context._text(row["observed_at"]),
            provenance=self.context._cognition_value(row["provenance_json"], default={}),
            created_at=self.context._text(row["created_at"]),
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
            fact = self.context._conn.execute(
                "SELECT 1 FROM temporal_facts WHERE id = ?", (item.fact_id,)
            ).fetchone()
            if not fact:
                raise ValueError("目标事实不存在")
            cursor = self.context._conn.execute(
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
                    item.observed_at or self.context._cognition_now(),
                    self.context._cognition_json(item.provenance, default={}),
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM fact_evidence_signals WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
            return self.context._compose_fact_evidence(row)

        return await self.context._run_db(dbwork)

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
            fact = self.context._conn.execute(
                "SELECT confidence FROM temporal_facts WHERE id = ?", (int(fact_id),)
            ).fetchone()
            if not fact:
                raise ValueError("目标事实不存在")
            rows = self.context._conn.execute(
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

        return await self.context._run_db(dbwork)

    def _compose_reflection(self, row: sqlite3.Row) -> ReflectionRecord:
        return ReflectionRecord(
            id=int(row["id"] or 0),
            scope=self.context._text(row["scope"]),
            kind=self.context._text(row["kind"]) or "reflection",
            summary=self.context._text(row["summary"]),
            importance=max(0.0, min(float(row["importance"] or 0.0), 1.0)),
            evidence_ids=self.context._cognition_value(row["evidence_ids_json"], default=[]),
            assertion_subject=self.context._text(row["assertion_subject"]),
            assertion_predicate=self.context._text(row["assertion_predicate"]),
            assertion_object=self.context._cognition_value(
                row["assertion_object_json"], default=None
            ),
            confidence=max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
            status=self.context._text(row["status"]) or "pending",
            source=self.context._text(row["source"]) or "reflection",
            promoted_at=self.context._text(row["promoted_at"]),
            created_at=self.context._text(row["created_at"]),
            updated_at=self.context._text(row["updated_at"]),
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
            cursor = self.context._conn.execute(
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
                    self.context._cognition_json(item.evidence_ids, default=[]),
                    item.assertion_subject,
                    item.assertion_predicate,
                    self.context._cognition_json(item.assertion_object, default=None),
                    item.confidence,
                    item.status,
                    item.source,
                    item.promoted_at,
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM reflections WHERE id = ?", (int(cursor.lastrowid),)
            ).fetchone()
            return self.context._compose_reflection(row)

        return await self.context._run_db(dbwork)

    async def get_reflections(
        self, *, scope: str = "", status: str = "", limit: int = 100
    ) -> list[ReflectionRecord]:
        """读取候选反思，供面板和审计查看。"""

        def dbwork() -> list[ReflectionRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if scope:
                clauses.append("scope = ?")
                params.append(self.context._text(scope))
            if status:
                clauses.append("status = ?")
                params.append(self.context._text(status))
            sql = "SELECT * FROM reflections"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_reflection(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_persona_assertion(self, row: sqlite3.Row) -> PersonaAssertionRecord:
        return PersonaAssertionRecord(
            id=int(row["id"] or 0),
            scope=self.context._text(row["scope"]),
            subject=self.context._text(row["subject"]),
            predicate=self.context._text(row["predicate"]),
            object_value=self.context._cognition_value(row["object_json"], default=None),
            confidence=max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
            source_reflection_id=int(row["source_reflection_id"] or 0),
            valid_from=self.context._text(row["valid_from"]),
            valid_to=self.context._text(row["valid_to"]),
            status=self.context._text(row["status"]) or "active",
            created_at=self.context._text(row["created_at"]),
            updated_at=self.context._text(row["updated_at"]),
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
            rows = self.context._conn.execute(
                """
                SELECT * FROM reflections
                WHERE status = 'pending' AND importance >= ?
                  AND assertion_subject <> '' AND assertion_predicate <> ''
                ORDER BY importance DESC, id ASC LIMIT ?
                """,
                (threshold, max(int(limit), 1)),
            ).fetchall()
            promoted: list[PersonaAssertionRecord] = []
            now = self.context._cognition_now()
            for row in rows:
                evidence_ids = self.context._cognition_value(
                    row["evidence_ids_json"], default=[]
                )
                if (
                    not isinstance(evidence_ids, list)
                    or len(evidence_ids) < evidence_threshold
                ):
                    continue
                self.context._conn.execute(
                    """
                    UPDATE persona_assertions
                    SET valid_to = ?, status = 'superseded', updated_at = CURRENT_TIMESTAMP
                    WHERE scope = ? AND subject = ? AND predicate = ?
                      AND status = 'active' AND valid_to = ''
                    """,
                    (
                        now,
                        self.context._text(row["scope"]),
                        self.context._text(row["assertion_subject"]),
                        self.context._text(row["assertion_predicate"]),
                    ),
                )
                cursor = self.context._conn.execute(
                    """
                    INSERT INTO persona_assertions(
                        scope, subject, predicate, object_json, confidence,
                        source_reflection_id, valid_from, valid_to, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, '', 'active',
                              CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        self.context._text(row["scope"]),
                        self.context._text(row["assertion_subject"]),
                        self.context._text(row["assertion_predicate"]),
                        self.context._text(row["assertion_object_json"]) or "null",
                        max(0.0, min(float(row["confidence"] or 0.0), 1.0)),
                        int(row["id"]),
                        now,
                    ),
                )
                self.context._conn.execute(
                    """
                    UPDATE reflections
                    SET status = 'promoted', promoted_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (now, int(row["id"])),
                )
                saved = self.context._conn.execute(
                    "SELECT * FROM persona_assertions WHERE id = ?",
                    (int(cursor.lastrowid),),
                ).fetchone()
                promoted.append(self.context._compose_persona_assertion(saved))
            self.context._conn.commit()
            return promoted

        return await self.context._run_db(dbwork)

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
                params.append(self.context._text(scope))
            if active_only:
                clauses.extend(["status = 'active'", "valid_to = ''"])
            sql = "SELECT * FROM persona_assertions"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_persona_assertion(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_durable_task(self, row: sqlite3.Row) -> DurableTaskRecord:
        return DurableTaskRecord(
            id=int(row["id"] or 0),
            task_key=self.context._text(row["task_key"]),
            kind=self.context._text(row["kind"]),
            payload=self.context._cognition_value(row["payload_json"], default={}),
            status=self.context._text(row["status"]) or "pending",
            priority=int(row["priority"] or 0),
            available_at=self.context._text(row["available_at"]),
            lease_owner=self.context._text(row["lease_owner"]),
            lease_expires_at=self.context._text(row["lease_expires_at"]),
            attempts=int(row["attempts"] or 0),
            max_attempts=int(row["max_attempts"] or 0),
            last_error=self.context._text(row["last_error"]),
            result=self.context._cognition_value(row["result_json"], default={}),
            created_at=self.context._text(row["created_at"]),
            updated_at=self.context._text(row["updated_at"]),
            completed_at=self.context._text(row["completed_at"]),
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
        lease_owner: str = "",
        lease_seconds: int = 0,
    ) -> DurableTaskRecord:
        """幂等加入持久任务队列。

        Args:
            task_key: 全局幂等键。
            kind: 任务类型。
            payload: 结构化任务载荷。
            priority: 零到一百的优先级。
            available_at: 最早可执行时间。
            max_attempts: 最大租用次数。
            lease_owner: 可选的当前执行器标识；提供后任务直接进入执行中状态。
            lease_seconds: 当前执行器持有任务的最长秒数。

        Returns:
            新建或已存在的任务。

        Raises:
            ValueError: 幂等键或类型为空。
        """

        key = self.context._text(task_key)
        task_kind = self.context._text(kind)
        if not (key and task_kind):
            raise ValueError("持久任务必须包含 task_key 和 kind")
        owner = self.context._text(lease_owner)
        now = self.context._cognition_now()
        status = "leased" if owner else "pending"
        lease_expires_at = ""
        if owner:
            lease_expires_at = (
                datetime.datetime.fromisoformat(now)
                + datetime.timedelta(seconds=max(int(lease_seconds), 1))
            ).strftime("%Y-%m-%d %H:%M:%S")

        def dbwork() -> DurableTaskRecord:
            self.context._conn.execute(
                """
                INSERT OR IGNORE INTO durable_tasks(
                    task_key, kind, payload_json, status, priority, available_at,
                    lease_owner, lease_expires_at, max_attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    key,
                    task_kind,
                    self.context._cognition_json(payload, default={}),
                    status,
                    max(0, min(int(priority), 100)),
                    self.context._text(available_at) or now,
                    owner,
                    lease_expires_at,
                    max(int(max_attempts), 1),
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM durable_tasks WHERE task_key = ?", (key,)
            ).fetchone()
            return self.context._compose_durable_task(row)

        return await self.context._run_db(dbwork)

    async def reschedule_durable_task(self, task_key: str, available_at: str) -> bool:
        """刷新尚未执行的持久任务时间。

        只更新仍处于 pending 状态的任务，避免用户延期时并发中的任务被
        悄悄改写而继续使用旧的执行上下文。
        """

        key = self.context._text(task_key)
        point = self.context._text(available_at)
        if not key or not point:
            return False

        def dbwork() -> bool:
            cursor = self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET available_at = ?, last_error = '', result_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE task_key = ? AND status = 'pending'
                """,
                (
                    point,
                    self.context._cognition_json({}, default={}),
                    key,
                ),
            )
            self.context._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self.context._run_db(dbwork)

    async def get_durable_tasks(
        self, *, status: str = "", kind: str = "", limit: int = 100
    ) -> list[DurableTaskRecord]:
        """读取持久任务状态，不返回任何可执行代码。"""

        def dbwork() -> list[DurableTaskRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if status:
                clauses.append("status = ?")
                params.append(self.context._text(status))
            if kind:
                clauses.append("kind = ?")
                params.append(self.context._text(kind))
            sql = "SELECT * FROM durable_tasks"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_durable_task(row) for row in rows]

        return await self.context._run_db(dbwork)

    async def get_durable_task(self, task_key: str) -> DurableTaskRecord | None:
        def dbwork() -> DurableTaskRecord | None:
            row = self.context._conn.execute(
                "SELECT * FROM durable_tasks WHERE task_key = ?",
                (self.context._text(task_key),),
            ).fetchone()
            return self.context._compose_durable_task(row) if row else None

        return await self.context._run_db(dbwork)

    async def recover_expired_durable_tasks(self, *, now: str = "") -> int:
        """释放过期租约并终止超过重试上限的任务。

        Args:
            now: 用于判断租约的时间，默认当前本地时间。

        Returns:
            被恢复或终止的任务数量。
        """

        point = self.context._text(now) or self.context._cognition_now()

        def dbwork() -> int:
            terminal = self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'dead', lease_owner = '', lease_expires_at = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'leased' AND lease_expires_at <> ?
                  AND life_timestamp(lease_expires_at) <= life_timestamp(?)
                  AND attempts >= max_attempts
                """,
                ("", point),
            )
            recovered = self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'pending', lease_owner = '', lease_expires_at = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'leased' AND lease_expires_at <> ?
                  AND life_timestamp(lease_expires_at) <= life_timestamp(?)
                  AND attempts < max_attempts
                """,
                ("", point),
            )
            self.context._conn.commit()
            return int(terminal.rowcount or 0) + int(recovered.rowcount or 0)

        return await self.context._run_db(dbwork)

    async def recover_leased_durable_tasks(self) -> int:
        """在插件启动时释放旧进程留下的全部任务租约。"""

        def dbwork() -> int:
            cursor = self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                    lease_owner = '', lease_expires_at = '', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'leased'
                """
            )
            self.context._conn.commit()
            return max(int(cursor.rowcount or 0), 0)

        return await self.context._run_db(dbwork)

    async def lease_durable_tasks(
        self,
        owner: str,
        *,
        limit: int = 1,
        lease_seconds: int = 60,
        now: str = "",
        include_kinds: tuple[str, ...] | list[str] = (),
        exclude_kinds: tuple[str, ...] | list[str] = (),
        exclude_task_ids: tuple[int, ...] | list[int] = (),
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

        lease_owner = self.context._text(owner)
        if not lease_owner:
            raise ValueError("任务租约必须包含 owner")
        point = self.context._text(now) or self.context._cognition_now()
        point_value = local_datetime(point)
        if point_value is None:
            raise ValueError("任务租约时间格式无效")
        lease_until = (
            point_value + datetime.timedelta(seconds=max(lease_seconds, 1))
        ).strftime("%Y-%m-%d %H:%M:%S")

        def dbwork() -> list[DurableTaskRecord]:
            self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                    lease_owner = '', lease_expires_at = '', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'leased' AND lease_expires_at <> ''
                  AND life_timestamp(lease_expires_at) <= life_timestamp(?)
                """,
                (point,),
            )
            excluded = tuple(
                sorted({self.context._text(item) for item in exclude_kinds if self.context._text(item)})
            )
            included = tuple(
                sorted({self.context._text(item) for item in include_kinds if self.context._text(item)})
            )
            where = (
                "status = 'pending' AND attempts < max_attempts "
                "AND (available_at = '' OR life_timestamp(available_at) <= life_timestamp(?))"
            )
            params: list[Any] = [point]
            if included:
                placeholders = ",".join("?" for _ in included)
                where += f" AND kind IN ({placeholders})"
                params.extend(included)
            if excluded:
                placeholders = ",".join("?" for _ in excluded)
                where += f" AND kind NOT IN ({placeholders})"
                params.extend(excluded)
            if exclude_task_ids:
                placeholders = ",".join("?" for _ in exclude_task_ids)
                where += f" AND id NOT IN ({placeholders})"
                params.extend(int(item) for item in exclude_task_ids)
            params.append(max(int(limit), 1))
            rows = self.context._conn.execute(
                "SELECT id FROM durable_tasks WHERE "
                + where
                + " ORDER BY priority DESC, id ASC LIMIT ?",
                tuple(params),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            for task_id in ids:
                self.context._conn.execute(
                    """
                    UPDATE durable_tasks
                    SET status = 'leased', lease_owner = ?, lease_expires_at = ?,
                        attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pending'
                    """,
                    (lease_owner, lease_until, task_id),
                )
            self.context._conn.commit()
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            leased = self.context._conn.execute(
                f"SELECT * FROM durable_tasks WHERE id IN ({placeholders}) AND lease_owner = ? ORDER BY priority DESC, id ASC",
                (*ids, lease_owner),
            ).fetchall()
            return [self.context._compose_durable_task(row) for row in leased]

        return await self.context._run_db(dbwork)

    async def update_durable_task_progress(
        self, task_id: int, owner: str, progress: dict[str, Any]
    ) -> bool:
        """在释放租约之前持久化已取得的产物，避免恢复时重复获取。"""

        def dbwork() -> bool:
            cursor = self.context._conn.execute(
                """UPDATE durable_tasks SET result_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'leased' AND lease_owner = ?""",
                (
                    self.context._cognition_json({"progress": progress}, default={}),
                    int(task_id),
                    self.context._text(owner),
                ),
            )
            self.context._conn.commit()
            return bool(cursor.rowcount)

        return await self.context._run_db(dbwork)

    async def renew_durable_task_lease(
        self, task_id: int, owner: str, *, lease_seconds: int = 1800
    ) -> bool:
        """仅为当前仍有效的租约续期，过期租约不能重新取得所有权。"""
        point = self.context._cognition_now()
        expires = (
            datetime.datetime.fromisoformat(point)
            + datetime.timedelta(seconds=max(1, lease_seconds))
        ).strftime("%Y-%m-%d %H:%M:%S")

        def dbwork() -> bool:
            cursor = self.context._conn.execute(
                """UPDATE durable_tasks SET lease_expires_at = ?
                WHERE id = ? AND status = 'leased' AND lease_owner = ?
                AND lease_expires_at > ?""",
                (expires, int(task_id), self.context._text(owner), point),
            )
            self.context._conn.commit()
            return bool(cursor.rowcount)

        return await self.context._run_db(dbwork)

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
                params.append(self.context._text(owner))
            cursor = self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'completed', result_json = ?, lease_owner = '',
                    lease_expires_at = '', completed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE """
                + " AND ".join(clauses),
                (
                    self.context._cognition_json(result, default={}),
                    self.context._cognition_now(),
                    *params,
                ),
            )
            self.context._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self.context._run_db(dbwork)

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

        key = self.context._text(task_key)
        if not key:
            return False

        def dbwork() -> bool:
            cursor = self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'completed', result_json = ?, lease_owner = '',
                    lease_expires_at = '', completed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_key = ? AND status IN ('pending', 'leased')
                """,
                (
                    self.context._cognition_json(result, default={}),
                    self.context._cognition_now(),
                    key,
                ),
            )
            self.context._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self.context._run_db(dbwork)

    async def fail_durable_task_by_key(
        self, task_key: str, error: str, result: dict[str, Any] | None = None
    ) -> bool:
        """按幂等键收束不可重试的外部任务失败。

        外部异步任务已经有明确终态时，不能把错误结果写成 completed；
        失败状态必须在重启后仍可被恢复器和状态查询识别。
        """

        key = self.context._text(task_key)
        if not key:
            return False

        def dbwork() -> bool:
            cursor = self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'failed', result_json = ?, last_error = ?,
                    lease_owner = '', lease_expires_at = '',
                    completed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_key = ? AND status IN ('pending', 'leased')
                """,
                (
                    self.context._cognition_json(result, default={}),
                    self.context._text(error),
                    self.context._cognition_now(),
                    key,
                ),
            )
            self.context._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self.context._run_db(dbwork)

    async def finalize_durable_task(
        self,
        task_id: int,
        result: dict[str, Any],
        *,
        owner: str = "",
    ) -> bool:
        """收束待执行或由当前执行器持有的已完成任务。

        适用于“产物已生成，当前请求正在投递”的短窗口。任务先入库以便
        崩溃后恢复；当前投递成功或明确取消后无需等待工作器再次租用。

        Args:
            task_id: 任务编号。
            result: 最终投递结果。
            owner: 可选的当前执行器标识；提供后可收束自己持有的执行中任务。

        Returns:
            是否成功把待执行任务标记为已完成。
        """

        def dbwork() -> bool:
            normalized_owner = self.context._text(owner)
            if normalized_owner:
                status_clause = (
                    "(status = 'pending' OR (status = 'leased' AND lease_owner = ?))"
                )
                status_params: tuple[Any, ...] = (normalized_owner,)
            else:
                status_clause = "status = 'pending'"
                status_params = ()
            cursor = self.context._conn.execute(
                f"""
                UPDATE durable_tasks
                SET status = 'completed', result_json = ?, lease_owner = '',
                    lease_expires_at = '', completed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND {status_clause}
                """,
                (
                    self.context._cognition_json(result, default={}),
                    self.context._cognition_now(),
                    int(task_id),
                    *status_params,
                ),
            )
            self.context._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self.context._run_db(dbwork)

    async def recover_provider_blocked_task(self, task_id: int, fingerprint: str) -> bool:
        """只有配置确实变化的受阻主动承诺可以重新取得执行机会。"""
        def write() -> bool:
            row = self.context._conn.execute(
                "SELECT result_json FROM durable_tasks WHERE id=? AND status='dead' "
                "AND kind='proactive_commitment'", (task_id,),
            ).fetchone()
            if not row or not fingerprint:
                return False
            result = json.loads(row["result_json"] or "{}")
            block = (result.get("progress") or {}).get("provider_block") or {}
            if not block.get("fingerprint") or block["fingerprint"] == fingerprint:
                return False
            result["recovered_from"] = {"fingerprint": block["fingerprint"], "at": self.context._cognition_now()}
            result.get("progress", {}).pop("provider_block", None)
            cursor = self.context._conn.execute(
                "UPDATE durable_tasks SET status='pending',attempts=0,available_at=?,last_error='',"
                "result_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='dead'",
                (self.context._cognition_now(), self.context._cognition_json(result, default={}), task_id),
            )
            self.context._conn.commit()
            return bool(cursor.rowcount)
        return await self.context._run_db(write)

    async def fail_durable_task(
        self,
        task_id: int,
        error: str,
        *,
        owner: str = "",
        retry_at: str = "",
        permanent: bool = False,
    ) -> bool:
        """记录失败并按剩余次数重排或终止任务。

        Args:
            task_id: 任务编号。
            error: 失败原因。
            owner: 可选租约持有者校验。
            retry_at: 下次可执行时间。
            permanent: 是否立即进入不可重试终态。

        Returns:
            是否成功更新任务。
        """

        def dbwork() -> bool:
            clauses = ["id = ?", "status = 'leased'"]
            params: list[Any] = [int(task_id)]
            if owner:
                clauses.append("lease_owner = ?")
                params.append(self.context._text(owner))
            cursor = self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET status = CASE
                        WHEN ? = 1 OR attempts >= max_attempts THEN 'dead'
                        ELSE 'pending'
                    END,
                    last_error = ?, available_at = ?, lease_owner = '',
                    lease_expires_at = '', updated_at = CURRENT_TIMESTAMP
                WHERE """
                + " AND ".join(clauses),
                (
                    self.context._flag(permanent),
                    self.context._text(error),
                    self.context._text(retry_at) or self.context._cognition_now(),
                    *params,
                ),
            )
            self.context._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self.context._run_db(dbwork)

    async def defer_durable_task(
        self,
        task_id: int,
        available_at: str,
        *,
        owner: str = "",
        reason: str = "",
        progress: dict[str, Any] | None = None,
    ) -> bool:
        """把条件尚未满足的已租用任务延期，不记作执行失败。"""

        result = {"outcome": "deferred", "reason": self.context._text(reason)}
        if isinstance(progress, dict):
            result["progress"] = progress

        def dbwork() -> bool:
            clauses = ["id = ?", "status = 'leased'"]
            params: list[Any] = [int(task_id)]
            if owner:
                clauses.append("lease_owner = ?")
                params.append(self.context._text(owner))
            cursor = self.context._conn.execute(
                """
                UPDATE durable_tasks
                SET status = 'pending', available_at = ?, lease_owner = '',
                    lease_expires_at = '', attempts = MAX(attempts - 1, 0),
                    last_error = '', result_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE """
                + " AND ".join(clauses),
                (
                    self.context._text(available_at) or self.context._cognition_now(),
                    self.context._cognition_json(result, default={}),
                    *params,
                ),
            )
            self.context._conn.commit()
            return int(cursor.rowcount or 0) > 0

        return await self.context._run_db(dbwork)

    def _compose_decision_trace(self, row: sqlite3.Row) -> DecisionTraceRecord:
        return DecisionTraceRecord(
            id=int(row["id"] or 0),
            trace_id=self.context._text(row["trace_id"]),
            scope=self.context._text(row["scope"]),
            stage=self.context._text(row["stage"]),
            reason_code=self.context._text(row["reason_code"]),
            decision=self.context._text(row["decision"]),
            scores=self.context._cognition_value(row["score_json"], default={}),
            evidence=self.context._cognition_value(row["evidence_json"], default=[]),
            outcome=compact_explanation_text(row["outcome"]),
            created_at=self.context._text(row["created_at"]),
            updated_at=self.context._text(row["updated_at"]),
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
        trace_id = self.context._text(raw.get("trace_id")) or uuid.uuid4().hex
        stage = self.context._text(raw.get("stage"))
        if not stage:
            raise ValueError("决策轨迹必须包含 stage")

        def dbwork() -> DecisionTraceRecord:
            self.context._conn.execute(
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
                    self.context._text(raw.get("scope")),
                    stage,
                    self.context._text(raw.get("reason_code")),
                    self.context._text(raw.get("decision")),
                    self.context._cognition_json(raw.get("scores"), default={}),
                    self.context._cognition_json(raw.get("evidence"), default=[]),
                    compact_explanation_text(raw.get("outcome")),
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM decision_traces WHERE trace_id = ? AND stage = ?",
                (trace_id, stage),
            ).fetchone()
            return self.context._compose_decision_trace(row)

        return await self.context._run_db(dbwork)

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
                params.append(self.context._text(scope))
            if trace_id:
                clauses.append("trace_id = ?")
                params.append(self.context._text(trace_id))
            sql = "SELECT * FROM decision_traces"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_decision_trace(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_life_action_outcome(self, row: sqlite3.Row) -> LifeActionOutcomeRecord:
        return LifeActionOutcomeRecord(
            id=int(row["id"] or 0),
            action_id=self.context._text(row["action_id"]),
            date=self.context._text(row["date"]),
            action_type=self.context._text(row["action_type"]),
            target=self.context._text(row["target"]),
            preconditions=self.context._cognition_value(row["preconditions_json"], default={}),
            effects=self.context._cognition_value(row["effects_json"], default={}),
            status=self.context._text(row["status"]) or "proposed",
            reason=self.context._text(row["reason"]),
            evidence=self.context._cognition_value(row["evidence_json"], default=[]),
            started_at=self.context._text(row["started_at"]),
            committed_at=self.context._text(row["committed_at"]),
            created_at=self.context._text(row["created_at"]),
            updated_at=self.context._text(row["updated_at"]),
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
        action_id = self.context._text(raw.get("action_id"))
        action_type = self.context._text(raw.get("action_type"))
        if not (action_id and action_type):
            raise ValueError("生活动作结算必须包含 action_id 和 action_type")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list):
            evidence = [evidence] if self.context._text(evidence) else []

        def dbwork() -> LifeActionOutcomeRecord:
            self.context._conn.execute(
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
                    self.context._text(raw.get("date")),
                    action_type,
                    self.context._text(raw.get("target") or raw.get("timeline_index")),
                    self.context._cognition_json(raw.get("preconditions"), default={}),
                    self.context._cognition_json(
                        raw.get("effects", raw.get("state_changes")), default={}
                    ),
                    self.context._text(raw.get("status")) or "proposed",
                    self.context._text(raw.get("reason")),
                    self.context._cognition_json(evidence, default=[]),
                    self.context._text(raw.get("started_at")),
                    self.context._text(raw.get("committed_at")),
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM life_action_outcomes WHERE action_id = ?", (action_id,)
            ).fetchone()
            return self.context._compose_life_action_outcome(row)

        return await self.context._run_db(dbwork)

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
                params.append(self.context._text(date))
            if status:
                clauses.append("status = ?")
                params.append(self.context._text(status))
            sql = "SELECT * FROM life_action_outcomes"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY date DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_life_action_outcome(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_life_action_receipt(self, row: sqlite3.Row) -> LifeActionReceiptRecord:
        return LifeActionReceiptRecord(
            id=int(row["id"] or 0),
            receipt_id=self.context._text(row["receipt_id"]),
            action_id=self.context._text(row["action_id"]),
            date=self.context._text(row["date"]),
            action_type=self.context._text(row["action_type"]),
            status=self.context._text(row["status"]) or "confirmed",
            evidence=self.context._cognition_value(row["evidence_json"], default=[]),
            source=self.context._text(row["source"]),
            source_id=self.context._text(row["source_id"]),
            artifact_path=self.context._text(row["artifact_path"]),
            occurred_at=self.context._text(row["occurred_at"]),
            created_at=self.context._text(row["created_at"]),
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
        receipt_id = self.context._text(raw.get("receipt_id")) or uuid.uuid4().hex
        action_id = self.context._text(raw.get("action_id"))
        if not action_id:
            raise ValueError("动作回执必须包含 action_id")
        status = self.context._text(raw.get("status") or "confirmed").lower()
        if status not in {"confirmed", "simulated", "failed", "cancelled"}:
            raise ValueError("动作回执状态无效")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list):
            evidence = [evidence] if self.context._text(evidence) else []

        def dbwork() -> LifeActionReceiptRecord:
            self.context._conn.execute(
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
                    self.context._text(raw.get("date")),
                    self.context._text(raw.get("action_type")),
                    status,
                    self.context._cognition_json(evidence, default=[]),
                    self.context._text(raw.get("source")),
                    self.context._text(raw.get("source_id")),
                    self.context._text(raw.get("artifact_path"))[:1000],
                    self.context._text(raw.get("occurred_at")),
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM life_action_receipts WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
            return self.context._compose_life_action_receipt(row)

        return await self.context._run_db(dbwork)

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
            if self.context._text(action_id):
                sql += " WHERE action_id = ?"
                params.append(self.context._text(action_id))
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_life_action_receipt(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_affective_state(self, row: sqlite3.Row) -> AffectiveStateRecord:
        return AffectiveStateRecord(
            id=int(row["id"] or 0),
            scope=self.context._text(row["scope"]),
            layer=self.context._text(row["layer"]) or "transient",
            label=self.context._text(row["label"]),
            valence=max(-1.0, min(float(row["valence"] or 0.0), 1.0)),
            arousal=max(0.0, min(float(row["arousal"] or 0.0), 1.0)),
            intensity=max(0.0, min(float(row["intensity"] or 0.0), 1.0)),
            baseline=max(0.0, min(float(row["baseline"] or 0.0), 1.0)),
            decay_half_life_minutes=max(
                float(row["decay_half_life_minutes"] or 240.0), 1.0
            ),
            evidence=self.context._cognition_value(row["evidence_json"], default=[]),
            valid_from=self.context._text(row["valid_from"]),
            valid_to=self.context._text(row["valid_to"]),
            status=self.context._text(row["status"]) or "active",
            source=self.context._text(row["source"]) or "state",
            created_at=self.context._text(row["created_at"]),
            updated_at=self.context._text(row["updated_at"]),
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
        scope = self.context._text(raw.get("scope"))
        layer = self.context._text(raw.get("layer") or "transient")
        label = self.context._text(raw.get("label"))
        if (
            not scope
            or not label
            or layer not in {"transient", "daily", "relationship"}
        ):
            raise ValueError("情绪状态必须包含 scope、label 和有效 layer")
        now = self.context._cognition_now()
        valid_from = self.context._text(raw.get("valid_from")) or now
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
            self.context._conn.execute(
                """
                UPDATE affective_states
                SET valid_to = ?, status = 'superseded', updated_at = CURRENT_TIMESTAMP
                WHERE scope = ? AND layer = ? AND label = ?
                  AND status = 'active' AND valid_to = ''
                """,
                (valid_from, scope, layer, label),
            )
            cursor = self.context._conn.execute(
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
                    self.context._cognition_json(raw.get("evidence"), default=[]),
                    valid_from,
                    self.context._text(raw.get("source")) or "state",
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM affective_states WHERE id = ?", (int(cursor.lastrowid),)
            ).fetchone()
            return self.context._compose_affective_state(row)

        return await self.context._run_db(dbwork)

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
                params.append(self.context._text(scope))
            if layer:
                clauses.append("layer = ?")
                params.append(self.context._text(layer))
            if active_only:
                clauses.extend(["status = 'active'", "valid_to = ''"])
            sql = "SELECT * FROM affective_states"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY valid_from DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            states = [self.context._compose_affective_state(row) for row in rows]
            if not apply_decay:
                return states
            point = local_datetime(self.context._text(at) or self.context._cognition_now())
            if point is None:
                point = life_now()
            decayed: list[AffectiveStateRecord] = []
            for item in states:
                started = local_datetime(item.valid_from)
                if started is None:
                    decayed.append(item)
                    continue
                elapsed_minutes = max((point - started).total_seconds() / 60.0, 0.0)
                factor = math.pow(0.5, elapsed_minutes / item.decay_half_life_minutes)
                intensity = item.baseline + (item.intensity - item.baseline) * factor
                decayed.append(replace(item, intensity=max(0.0, min(intensity, 1.0))))
            return decayed

        return await self.context._run_db(dbwork)

    def _compose_grounded_diary(self, row: sqlite3.Row) -> GroundedDiaryEntryRecord:
        return GroundedDiaryEntryRecord(
            id=int(row["id"] or 0),
            date=self.context._text(row["date"]),
            scope=self.context._text(row["scope"]),
            title=self.context._text(row["title"]),
            summary=self.context._text(row["summary"]),
            evidence_ids=self.context._cognition_value(row["evidence_ids_json"], default=[]),
            mood_label=self.context._text(row["mood_label"]),
            source=self.context._text(row["source"]) or "daily_review",
            created_at=self.context._text(row["created_at"]),
            updated_at=self.context._text(row["updated_at"]),
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
        date = self.context._text(raw.get("date"))
        scope = self.context._text(raw.get("scope"))
        summary = self.context._text(raw.get("summary"))
        evidence_ids = raw.get("evidence_ids")
        if not date or not scope or not summary:
            raise ValueError("日记必须包含 date、scope 和 summary")
        if not isinstance(evidence_ids, list) or not any(
            self.context._text(item) for item in evidence_ids
        ):
            raise ValueError("日记必须引用至少一条已落库证据")

        def dbwork() -> GroundedDiaryEntryRecord:
            self.context._conn.execute(
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
                    self.context._text(raw.get("title")),
                    summary,
                    self.context._cognition_json(evidence_ids, default=[]),
                    self.context._text(raw.get("mood_label")),
                    self.context._text(raw.get("source")) or "daily_review",
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM grounded_diary_entries WHERE date = ? AND scope = ?",
                (date, scope),
            ).fetchone()
            return self.context._compose_grounded_diary(row)

        return await self.context._run_db(dbwork)

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
                params.append(self.context._text(scope))
            sql += " ORDER BY date DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            from .lifetraces import MemoryLifeArchive
            living = MemoryLifeArchive(self.context)
            result = []
            for row in rows:
                if row["source"] == "memory_life":
                    refs = json.loads(row["evidence_ids_json"])
                    sources = {p[1]: p[2] for ref in refs if len(p := str(ref).split(":")) == 3 and p[0] == "memory"}
                    if not living._valid_sources(sources, row["scope"]):
                        continue
                result.append(self.context._compose_grounded_diary(row))
            return result

        return await self.context._run_db(dbwork)


__all__ = ["CognitionArchiveComponent"]
