from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any

from ..clock import local_datetime, now as life_now
from ..models import CommitmentRecord
from .schedulelinks import reconcile_scheduled_commitments

from typing import Protocol

_COMMITMENT_SOURCE_PRIORITY = {
    "invite": 400,
    "manual": 300,
    "chat": 200,
    "chat_batch": 100,
}


class CommitmentArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _compose_commitment: Any
    _conn: Any
    _get_people_unlocked: Any
    _replace_people_unlocked: Any
    _run_db: Any
    _text: Any


class CommitmentArchiveComponent:
    def __init__(self, context: CommitmentArchiveComponentContext) -> None:
        self.context = context

    async def reconcile_scheduled_commitments(
        self, *, now: datetime.datetime | None = None
    ) -> int:
        return await self.context._run_db(
            lambda: reconcile_scheduled_commitments(self.context._conn, now=now)
        )

    def _compose_commitment(self, row: sqlite3.Row) -> CommitmentRecord:
        return CommitmentRecord(
            id=int(row["id"] or 0),
            content=row["content"],
            kind=row["kind"],
            trigger_date=row["trigger_date"],
            trigger_time=row["trigger_time"],
            time_window=row["time_window"],
            owner=row["owner"],
            media_kind=row["media_kind"],
            people=self.context._get_people_unlocked(
                "commitment_people", "commitment_id", row["id"]
            ),
            place=row["place"],
            status=row["status"],
            confidence=float(row["confidence"] or 0.0),
            source=row["source"],
            source_session=row["source_session"],
            source_message_id=row["source_message_id"],
            source_message=row["source_message"],
            created_at=row["created_at"],
            activated_at=row["activated_at"],
            completed_at=row["completed_at"],
            follow_up=json.loads(row["follow_up_json"] or "{}"),
        )

    async def save_commitment(self, commitment: CommitmentRecord) -> CommitmentRecord:
        item = CommitmentRecord.from_value(
            commitment.as_dict()
            if isinstance(commitment, CommitmentRecord)
            else commitment
        )
        if not item:
            raise ValueError("承诺内容不能为空")
        item.kind = self.context._text(item.kind) or "plan"
        item.status = self.context._text(item.status) or "active"
        item.owner = self.context._text(item.owner) or "未定"
        item.media_kind = self.context._text(item.media_kind).lower() or "none"
        item.confidence = max(min(float(item.confidence or 0.0), 1.0), 0.0)

        def dbwork():
            if (
                not item.id
                and _COMMITMENT_SOURCE_PRIORITY.get(item.source, 0) > 0
                and item.source_session
                and item.source_message_id
            ):
                evidence_rows = self.context._conn.execute(
                    """
                    SELECT *
                    FROM commitments
                    WHERE source IN ('invite', 'manual', 'chat', 'chat_batch')
                      AND source_session = ?
                      AND source_message_id = ?
                    ORDER BY id
                    """,
                    (item.source_session, item.source_message_id),
                ).fetchall()
                evidence_matches = []
                for row in evidence_rows:
                    existing = self.context._compose_commitment(row)
                    same_slot = True
                    for field in (
                        "kind",
                        "trigger_date",
                        "trigger_time",
                        "time_window",
                    ):
                        old_value = str(getattr(existing, field, "") or "").strip()
                        new_value = str(getattr(item, field, "") or "").strip()
                        if old_value and new_value and old_value != new_value:
                            same_slot = False
                            break
                    if same_slot:
                        evidence_matches.append(existing)
                evidence_matches.sort(
                    key=lambda existing: (
                        -_COMMITMENT_SOURCE_PRIORITY.get(existing.source, 0),
                        existing.id,
                    )
                )
                if evidence_matches:
                    existing = evidence_matches[0]
                    # 同一证据的重复提炼可以补齐联系动作，不能覆盖已结算状态。
                    if (
                        existing.status in {"active", "scheduled"}
                        and existing.owner == item.owner == "当前角色"
                        and item.follow_up.get("action") in {"contact_person", "remind_person"}
                        and existing.follow_up.get("action") not in {"contact_person", "remind_person"}
                    ):
                        existing.follow_up = dict(item.follow_up)
                        self.context._conn.execute(
                            "UPDATE commitments SET follow_up_json = ? WHERE id = ?",
                            (json.dumps(existing.follow_up, ensure_ascii=False), existing.id),
                        )
                        self.context._conn.commit()
                    if not item.follow_up:
                        item.follow_up = dict(existing.follow_up)
                    incoming_priority = _COMMITMENT_SOURCE_PRIORITY.get(item.source, 0)
                    existing_priority = _COMMITMENT_SOURCE_PRIORITY.get(
                        existing.source, 0
                    )
                    if incoming_priority < existing_priority:
                        return existing
                    if incoming_priority > existing_priority:
                        item.id = existing.id
                        item.status = existing.status
                        item.activated_at = existing.activated_at
                        item.completed_at = existing.completed_at
                    elif item.source != "chat_batch" or all(
                        str(getattr(existing, field, "") or "").strip()
                        == str(getattr(item, field, "") or "").strip()
                        for field in (
                            "content",
                            "kind",
                            "trigger_date",
                            "trigger_time",
                            "time_window",
                            "place",
                        )
                    ):
                        return existing
            if item.id:
                self.context._conn.execute(
                    """
                    UPDATE commitments
                    SET content = ?, kind = ?, trigger_date = ?, trigger_time = ?,
                        time_window = ?, owner = ?, media_kind = ?, place = ?, status = ?, confidence = ?,
                        source = ?, source_session = ?, source_message_id = ?,
                        source_message = ?, follow_up_json = ?,
                        activated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        item.content,
                        item.kind,
                        item.trigger_date,
                        item.trigger_time,
                        item.time_window,
                        item.owner,
                        item.media_kind,
                        item.place,
                        item.status,
                        item.confidence,
                        item.source,
                        item.source_session,
                        item.source_message_id,
                        item.source_message,
                        json.dumps(item.follow_up, ensure_ascii=False),
                        item.activated_at,
                        item.completed_at,
                        item.id,
                    ),
                )
                commitment_id = item.id
            else:
                cursor = self.context._conn.execute(
                    """
                    INSERT INTO commitments(
                        content, kind, trigger_date, trigger_time, time_window, owner, media_kind, place,
                        status, confidence, source, source_session,
                        source_message_id, source_message, follow_up_json,
                        activated_at, completed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.content,
                        item.kind,
                        item.trigger_date,
                        item.trigger_time,
                        item.time_window,
                        item.owner,
                        item.media_kind,
                        item.place,
                        item.status,
                        item.confidence,
                        item.source,
                        item.source_session,
                        item.source_message_id,
                        item.source_message,
                        json.dumps(item.follow_up, ensure_ascii=False),
                        item.activated_at,
                        item.completed_at,
                    ),
                )
                commitment_id = int(cursor.lastrowid)
            self.context._replace_people_unlocked(
                "commitment_people", "commitment_id", commitment_id, item.people
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM commitments WHERE id = ?", (commitment_id,)
            ).fetchone()
            return self.context._compose_commitment(row)

        return await self.context._run_db(dbwork)

    async def save_commitment_follow_up(
        self, commitment_id: int, follow_up: dict[str, Any]
    ) -> bool:
        """保存独立联系动作，不重新激活已经结算或取消的约定。"""
        def dbwork():
            cursor = self.context._conn.execute(
                "UPDATE commitments SET follow_up_json = ? WHERE id = ? AND status IN ('active', 'scheduled')",
                (json.dumps(follow_up, ensure_ascii=False), int(commitment_id)),
            )
            self.context._conn.commit()
            return cursor.rowcount > 0

        return await self.context._run_db(dbwork)

    async def get_commitments(
        self, status: str = "active", limit: int = 20, *, source_session: str | None = None,
        source_message_id: str | None = None,
    ) -> list[CommitmentRecord]:
        def dbwork():
            reconcile_scheduled_commitments(self.context._conn)
            params: list[Any] = []
            sql = "SELECT * FROM commitments"
            conditions = []
            if status:
                conditions.append("status = ?")
                params.append(self.context._text(status))
            if source_session is not None:
                conditions.append("(source_session = ? OR source_session = '')")
                params.append(source_session)
            if source_message_id is not None:
                conditions.append("source_message_id = ?")
                params.append(source_message_id)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            sql += " ORDER BY COALESCE(NULLIF(trigger_date, ''), '9999-12-31'), id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_commitment(row) for row in rows]

        return await self.context._run_db(dbwork)

    async def get_commitment(self, commitment_id: int) -> CommitmentRecord | None:
        def dbwork():
            row = self.context._conn.execute(
                "SELECT * FROM commitments WHERE id = ?", (int(commitment_id),)
            ).fetchone()
            return self.context._compose_commitment(row) if row else None

        return await self.context._run_db(dbwork)

    async def get_due_commitments(
        self, date_str: str, include_scheduled: bool = False
    ) -> list[CommitmentRecord]:
        statuses = ("active", "scheduled") if include_scheduled else ("active",)
        placeholders = ",".join("?" for _ in statuses)

        def dbwork():
            reconcile_scheduled_commitments(self.context._conn)
            rows = self.context._conn.execute(
                f"""
                SELECT *
                FROM commitments
                WHERE status IN ({placeholders})
                  AND (trigger_date = ? OR (trigger_date = '' AND time_window IN ('next_chat', 'next_time')))
                ORDER BY trigger_time, id
                """,
                (*statuses, self.context._text(date_str)),
            ).fetchall()
            return [self.context._compose_commitment(row) for row in rows]

        return await self.context._run_db(dbwork)

    async def get_open_commitments_for_scope(
        self, scope: str, before_date: str, limit: int = 80
    ) -> list[CommitmentRecord]:
        def read():
            rows = self.context._conn.execute(
                """SELECT * FROM commitments WHERE source_session = ?
                AND kind IN ('plan', 'reminder', 'followup')
                AND (status IN ('active', 'scheduled', 'pending') OR (
                    status = 'expired'
                    AND COALESCE(NULLIF(trigger_date, ''), date(completed_at)) >= date(?, '-7 days')
                    AND kind = 'plan' AND media_kind IN ('', 'none')))
                ORDER BY CASE WHEN status = 'expired' THEN 1 ELSE 0 END,
                    id DESC LIMIT ?""",
                (scope, before_date, max(1, min(200, int(limit)))),
            ).fetchall()
            return [self.context._compose_commitment(row) for row in rows]

        return await self.context._run_db(read)

    async def reconcile_conversation_commitment(
        self, commitment_id: int, *, scope: str, status: str,
        source_id: str, evidence: str,
    ) -> bool:
        """依据留存原消息同步生活进展，时间流逝和口头交付不能充当完成凭据。"""
        if not scope or not source_id or not evidence or status not in {
            "in_progress", "done", "cancelled", "expired",
        }:
            return False

        def write():
            conn = self.context._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                item = conn.execute(
                    "SELECT * FROM commitments WHERE id = ? AND source_session = ?",
                    (int(commitment_id), scope),
                ).fetchone()
                # 平台消息编号与本地行号各自匹配，全文与会话同时校验。
                row = conn.execute(
                    """SELECT * FROM chat_memory_messages WHERE session_id = ?
                    AND (message_id = ? OR CAST(id AS TEXT) = ?)
                    AND trim(message_text) = ? AND is_quoted = 0
                    ORDER BY id DESC LIMIT 1""",
                    (scope, source_id, source_id, evidence.strip()),
                ).fetchone()
                if not item or not row or item["status"] not in {
                    "active", "scheduled", "pending", "expired",
                } or item["kind"] != "plan":
                    return False
                occurred = local_datetime(row["occurred_at"])
                if occurred is None or row["role"] not in {"user", "assistant"}:
                    return False
                # 已过期记录只允许以当时的完成或取消证据补正，不能重新启动。
                if item["status"] == "expired" and status == "in_progress":
                    return False
                if status != "cancelled" and item["trigger_date"] > occurred.date().isoformat():
                    return False
                follow_up = json.loads(item["follow_up_json"] or "{}")
                if status != "cancelled" and (
                    item["media_kind"] not in {"", "none"}
                    or follow_up.get("action") in {"contact_person", "remind_person"}
                    or conn.execute(
                        """SELECT 1 FROM durable_tasks WHERE task_key IN (?, ?)
                        AND status IN ('pending', 'leased') LIMIT 1""",
                        (f"proactive_commitment:{commitment_id}",
                         f"commitment_contact_review:{commitment_id}"),
                    ).fetchone()
                ):
                    return False
                origins = conn.execute(
                    """SELECT * FROM chat_memory_messages WHERE session_id = ?
                    AND id <= ? AND is_quoted = 0 AND (
                        (? != '' AND (message_id = ? OR CAST(id AS TEXT) = ?))
                        OR (trim(message_text) != '' AND instr(?, trim(message_text)) > 0))
                    ORDER BY id DESC LIMIT 200""",
                    (scope, row["id"], item["source_message_id"], item["source_message_id"],
                     item["source_message_id"], item["source_message"]),
                ).fetchall()
                if not origins:
                    return False
                if row["role"] == "assistant":
                    if item["owner"] not in {"当前角色", "共同"}:
                        return False
                elif item["owner"] not in {"说话人", "共同"} and status != "cancelled":
                    return False
                elif row["is_group"] and not any(
                    origin["role"] == "user" and row["sender_profile_id"]
                    and origin["sender_profile_id"] == row["sender_profile_id"]
                    for origin in origins
                ):
                    return False
                target = "active" if status == "in_progress" else status
                action_status = "active" if status == "in_progress" else status
                prior_trace = conn.execute(
                    "SELECT 1 FROM decision_traces WHERE trace_id = ? AND stage = ?",
                    (f"commitment:{commitment_id}", f"conversation_{status}"),
                ).fetchone()
                unfinished_action = conn.execute(
                    """SELECT 1 FROM conversation_action_items WHERE commitment_id = ?
                    AND status != ? LIMIT 1""", (int(commitment_id), action_status),
                ).fetchone()
                if prior_trace and target == item["status"] and not unfinished_action:
                    return False
                when = occurred.isoformat(sep=" ", timespec="seconds")
                conn.execute(
                    "UPDATE commitments SET status = ?, completed_at = ? WHERE id = ?",
                    (target, "" if target == "active" else when, int(commitment_id)),
                )
                actions = conn.execute(
                    "SELECT id, evidence_json FROM conversation_action_items WHERE commitment_id = ?",
                    (int(commitment_id),),
                ).fetchall()
                for action in actions:
                    proof = json.loads(action["evidence_json"] or "[]")
                    if evidence not in proof:
                        proof.append(evidence)
                    conn.execute(
                        """UPDATE conversation_action_items SET status = ?, evidence_json = ?,
                        updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                        (action_status, json.dumps(proof, ensure_ascii=False), action["id"]),
                    )
                if target in {"done", "cancelled", "expired"}:
                    # 已领取的任务发送前仍需复查承诺；未领取任务立即停止。
                    conn.execute(
                        """UPDATE durable_tasks SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                        WHERE status = 'pending' AND (task_key IN (?, ?, ?, ?, ?)
                        OR (json_valid(payload_json) AND
                            CAST(json_extract(payload_json, '$.commitment_id') AS TEXT) = ?))""",
                        (f"proactive_commitment:{commitment_id}", f"commitment_contact_review:{commitment_id}",
                         f"commitment_photo:{commitment_id}", f"commitment_video:{commitment_id}",
                         f"invite_contact:{commitment_id}", str(commitment_id)),
                    )
                conn.execute(
                    """INSERT INTO decision_traces
                    (trace_id, scope, stage, reason_code, decision, evidence_json, outcome)
                    VALUES (?, ?, ?, 'conversation_action_evidence', ?, ?, ?)
                    ON CONFLICT(trace_id, stage) DO NOTHING""",
                    (f"commitment:{commitment_id}", scope, f"conversation_{status}", target,
                     json.dumps([evidence, source_id], ensure_ascii=False),
                     "依据对应会话中原始消息同步行动进展"),
                )
                return True
            except Exception:
                conn.rollback()
                raise
            finally:
                # 拒绝更新时没有写入；成功时连同行动项及任务一起提交。
                if conn.in_transaction:
                    conn.commit()

        return await self.context._run_db(write)

    async def get_open_commitments_for_message(
        self, scope: str, message_id: str
    ) -> list[CommitmentRecord]:
        def read():
            rows = self.context._conn.execute(
                """SELECT * FROM commitments
                WHERE source_session = ? AND source_message_id = ?
                AND status IN ('active', 'scheduled', 'pending')
                ORDER BY id""",
                (scope, message_id),
            ).fetchall()
            return [self.context._compose_commitment(row) for row in rows]

        return await self.context._run_db(read)

    async def complete_simulated_commitment(
        self,
        commitment_id: int,
        *,
        scope: str,
        when: str,
        evidence: str,
        source_id: str,
    ) -> bool:
        """以原子操作结算虚拟动作；对外交付必须有独立回执。"""
        if not scope or not evidence or not source_id:
            return False

        def write():
            self.context._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.context._conn.execute(
                    "SELECT follow_up_json FROM commitments WHERE id = ?", (int(commitment_id),)
                ).fetchone()
                follow_up = json.loads(row[0] or "{}") if row else {}
                external_task = self.context._conn.execute(
                    """SELECT 1 FROM durable_tasks WHERE task_key IN (?, ?)
                    AND status IN ('pending', 'leased') LIMIT 1""",
                    (f"proactive_commitment:{commitment_id}", f"commitment_contact_review:{commitment_id}"),
                ).fetchone()
                if follow_up.get("action") in {"contact_person", "remind_person"} or external_task:
                    self.context._conn.commit()
                    return False
                cursor = self.context._conn.execute(
                    """UPDATE commitments SET status = 'done', completed_at = ?
                    WHERE id = ? AND source_session = ? AND owner = '当前角色'
                    AND media_kind IN ('', 'none') AND kind = 'plan'
                    AND status IN ('active', 'scheduled', 'pending')
                    AND (trigger_date = '' OR trigger_date <= ?)""",
                    (when, int(commitment_id), scope, when[:10]),
                )
                changed = cursor.rowcount > 0
                if changed:
                    self.context._conn.execute(
                        "UPDATE conversation_action_items SET status = 'done', updated_at = CURRENT_TIMESTAMP WHERE commitment_id = ?",
                        (int(commitment_id),),
                    )
                    self.context._conn.execute(
                        """INSERT INTO decision_traces(trace_id, scope, stage, reason_code, decision, evidence_json, outcome)
                        VALUES (?, ?, 'completed', 'virtual_action_evidence', 'done', ?, ?)
                        ON CONFLICT(trace_id, stage) DO NOTHING""",
                        (
                            f"commitment:{commitment_id}",
                            scope,
                            json.dumps([evidence, source_id], ensure_ascii=False),
                            "依据已发送的角色行动完成陈述同步虚拟承诺",
                        ),
                    )
                self.context._conn.commit()
                return changed
            except Exception:
                self.context._conn.rollback()
                raise

        return await self.context._run_db(write)

    async def set_commitment_status(
        self, commitment_id: int, status: str, when: str = ""
    ) -> bool:
        status = self.context._text(status)
        if status not in {
            "active",
            "scheduled",
            "done",
            "cancelled",
            "expired",
            "pending",
            "delivery_failed",
        }:
            return False
        field = (
            "completed_at"
            if status in {"done", "cancelled", "expired", "delivery_failed"}
            else "activated_at"
        )

        def dbwork():
            cursor = self.context._conn.execute(
                f"UPDATE commitments SET status = ?, {field} = ? WHERE id = ?",
                (status, self.context._text(when), int(commitment_id)),
            )
            action_status = {
                "active": "open",
                "scheduled": "pending",
                "pending": "pending",
                "done": "done",
                "cancelled": "cancelled",
                "expired": "expired",
                "delivery_failed": "failed",
            }.get(status, status)
            self.context._conn.execute(
                """
                UPDATE conversation_action_items
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE commitment_id = ?
                """,
                (action_status, int(commitment_id)),
            )
            self.context._conn.commit()
            return cursor.rowcount > 0

        return await self.context._run_db(dbwork)

    async def reschedule_commitment(
        self,
        commitment_id: int,
        trigger_date: str,
        time_window: str = "",
        trigger_time: str = "",
    ) -> bool:
        def dbwork():
            row = self.context._conn.execute(
                "SELECT follow_up_json FROM commitments WHERE id = ?",
                (int(commitment_id),),
            ).fetchone()
            cursor = self.context._conn.execute(
                """
                UPDATE commitments
                SET trigger_date = ?, trigger_time = ?, time_window = ?, status = 'active', activated_at = ''
                WHERE id = ?
                """,
                (
                    self.context._text(trigger_date),
                    self.context._text(trigger_time),
                    self.context._text(time_window),
                    int(commitment_id),
                ),
            )
            follow_up = json.loads(row["follow_up_json"] or "{}") if row else {}
            if follow_up.get("action") in {"contact_person", "remind_person"}:
                now = life_now()
                due = local_datetime(f"{trigger_date} {trigger_time}") if trigger_time else None
                if due is None:
                    # 改成模糊时段后按新条件复核，不能继续沿用旧钟点发出招呼。
                    date_start = local_datetime(f"{trigger_date} 00:00:00")
                    due = max(now + datetime.timedelta(minutes=10), date_start or now)
                    follow_up["condition"] = (
                        f"改期后的联系时机已经到来：日期 {trigger_date or '以最新约定为准'}，"
                        f"时段 {time_window or '以最新约定为准'}；结合最新交流确认，不提前联系"
                    )
                follow_up["execute_at"] = due.isoformat(timespec="seconds")
                follow_up["observed_at"] = now.isoformat(timespec="seconds")
                follow_up["expires_at"] = (
                    (max(due, now) + datetime.timedelta(hours=24)).isoformat(timespec="seconds")
                    if follow_up.get("condition") else ""
                )
                self.context._conn.execute(
                    "UPDATE commitments SET follow_up_json = ? WHERE id = ?",
                    (json.dumps(follow_up, ensure_ascii=False), int(commitment_id)),
                )
                # 在同一事务中移动待执行任务；执行中的任务还会在发送前复查记录。
                self.context._conn.execute(
                    "UPDATE durable_tasks SET available_at = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE task_key = ? AND status = 'pending'",
                    (due.strftime("%Y-%m-%d %H:%M:%S"), f"proactive_commitment:{int(commitment_id)}"),
                )
            self.context._conn.execute(
                """
                UPDATE conversation_action_items
                SET due_at = ?, status = 'open', updated_at = CURRENT_TIMESTAMP
                WHERE commitment_id = ?
                """,
                (
                    " ".join(
                        value
                        for value in (
                            self.context._text(trigger_date),
                            self.context._text(trigger_time),
                        )
                        if value
                    )
                    or self.context._text(time_window),
                    int(commitment_id),
                ),
            )
            self.context._conn.commit()
            return cursor.rowcount > 0

        return await self.context._run_db(dbwork)

    async def link_commitments_to_day(
        self, date_str: str, commitment_ids: list[int]
    ) -> None:
        ids = sorted({int(item) for item in commitment_ids if int(item or 0) > 0})
        if not ids:
            return

        def dbwork():
            self.context._conn.executemany(
                "INSERT OR IGNORE INTO day_commitments(date, commitment_id) VALUES (?, ?)",
                [(self.context._text(date_str), commitment_id) for commitment_id in ids],
            )
            self.context._conn.executemany(
                """
                UPDATE commitments
                SET status = 'scheduled', activated_at = COALESCE(NULLIF(activated_at, ''), CURRENT_TIMESTAMP)
                WHERE id = ? AND status = 'active'
                """,
                [(commitment_id,) for commitment_id in ids],
            )
            self.context._conn.executemany(
                """
                UPDATE conversation_action_items
                SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                WHERE commitment_id = ? AND status = 'open'
                """,
                [(commitment_id,) for commitment_id in ids],
            )
            self.context._conn.commit()

        return await self.context._run_db(dbwork)
