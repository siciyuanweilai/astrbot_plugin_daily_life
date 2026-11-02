"""原子结算关系事件、证据占用和长期状态，保留可纠正的因果账本。"""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

from ..relationship import BASELINE, DIMENSIONS, MAX_CHANGE, normalize_assessment


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class RelationshipStateArchive:
    def __init__(self, context: Any):
        self.context = context

    def _relationship_state_unlocked(self, profile_id: str) -> dict[str, Any]:
        row = self.context._conn.execute(
            "SELECT * FROM relationship_states WHERE profile_id=?", (profile_id,)
        ).fetchone()
        if row is None:
            return {**BASELINE, "observed_dimensions": [], "revision": 0}
        return {key: row[key] for key in DIMENSIONS} | {
            "observed_dimensions": json.loads(row["observed_json"]),
            "revision": row["revision"], "updated_at": row["updated_at"],
        }

    async def get_relationship_changes(self, profile_id: str, *, scope: str = "", limit: int = 8) -> list[dict]:
        def read():
            sql = "SELECT * FROM relationship_changes WHERE profile_id=?"
            args: list[Any] = [profile_id]
            if scope:
                sql += " AND scope=?"
                args.append(scope)
            rows = self.context._conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, max(1, min(limit, 50)))).fetchall()
            return [{"id": row["id"], "profile_id": row["profile_id"], "scope": row["scope"],
                     "occurred_at": row["occurred_at"], "reason": row["reason"], "confidence": row["confidence"],
                     "deltas": json.loads(row["applied_json"]), "status": row["status"],
                     "source_message_ids": [x["message_id"] for x in json.loads(row["evidence_json"])],
                     "supersedes_id": row["supersedes_id"]} for row in rows]
        return await self.context._run_db(read)

    async def apply_relationship_assessment(self, raw: dict, *, session_id: str, rows: list[dict]) -> dict:
        """只接受实际消息队列中的同一人物证据；所有写入共用一个事务。"""
        item = normalize_assessment(raw)
        if not item or not session_id:
            return {"status": "invalid"}

        def write():
            conn = self.context._conn
            # rows 只是候选编号；以数据库里的角色、身份、文本和时间为准。
            candidates: dict[str, Any] = {}
            for supplied in rows:
                row_id = supplied.get("id")
                stored = conn.execute("SELECT * FROM chat_memory_messages WHERE id=? AND session_id=?", (row_id, session_id)).fetchone()
                if stored is None or stored["role"] != "user" or stored["sender_profile_id"] != item["profile_id"]:
                    continue
                if not stored["message_text"]:
                    continue
                key = str(stored["message_id"] or stored["id"])
                candidates[key] = stored
            if not item["source_message_ids"] or any(ref not in candidates for ref in item["source_message_ids"]):
                return {"status": "invalid_evidence"}
            selected = {candidates[ref]["event_key"]: candidates[ref] for ref in item["source_message_ids"]}
            evidence = [{"source_key": _json([session_id, row["event_key"]]),
                         "message_id": str(row["message_id"] or row["id"]),
                         "text": row["message_text"], "occurred_at": row["occurred_at"]}
                        for row in selected.values()]
            occurred_at = max(x["occurred_at"] for x in evidence)
            try:
                datetime.datetime.fromisoformat(occurred_at)
            except (ValueError, TypeError):
                return {"status": "invalid_time"}
            profile_id = item["profile_id"]
            if not conn.execute("SELECT 1 FROM relationships WHERE id=?", (profile_id,)).fetchone():
                return {"status": "unknown_person"}
            event_key = hashlib.sha256(_json(sorted(x["source_key"] for x in evidence)).encode()).hexdigest()
            conn.execute("BEGIN IMMEDIATE")
            with conn:
                for proof in evidence:
                    previous = conn.execute("SELECT change_id FROM relationship_evidence_claims WHERE profile_id=? AND source_key=?",
                                            (profile_id, proof["source_key"])).fetchone()
                    if previous:
                        return {"status": "duplicate", "change_id": previous["change_id"]}
                if item["change_id"]:
                    previous = conn.execute("SELECT id,evidence_json FROM relationship_changes WHERE id=? AND profile_id=? AND scope=?",
                                            (item["change_id"], profile_id, session_id)).fetchone()
                    if not previous:
                        return {"status": "invalid_reference"}
                    # 重述只补充证据，不重新改变关系。
                    conn.executemany("INSERT INTO relationship_evidence_claims VALUES(?,?,?)",
                                     [(profile_id, x["source_key"], previous["id"]) for x in evidence])
                    conn.execute("UPDATE relationship_changes SET evidence_json=? WHERE id=?",
                                 (_json(json.loads(previous["evidence_json"]) + evidence), previous["id"]))
                    return {"status": "duplicate", "change_id": previous["id"]}
                supersedes = item["supersedes_change_id"]
                if not supersedes and not any(item["deltas"].values()) and item["confidence"] >= 0.65:
                    return {"status": "unchanged"}
                if supersedes:
                    previous = conn.execute("SELECT * FROM relationship_changes WHERE id=? AND profile_id=? AND scope=? AND status<>'superseded'",
                                            (supersedes, profile_id, session_id)).fetchone()
                    if previous is None or item["confidence"] < 0.65:
                        return {"status": "invalid_correction"}
                before = self._relationship_state_unlocked(profile_id)
                active = conn.execute("SELECT * FROM relationship_changes WHERE profile_id=? AND status<>'uncertain' ORDER BY id", (profile_id,)).fetchall()
                values = dict(BASELINE)
                observed: set[str] = set()
                spent = dict.fromkeys(DIMENSIONS, 0.0)
                for previous in active:
                    deltas = json.loads(previous["applied_json"])
                    for key in DIMENSIONS:
                        if previous["occurred_at"][:10] == occurred_at[:10]:
                            spent[key] += abs(deltas[key])
                        if previous["status"] == "applied" and previous["id"] != supersedes:
                            values[key] += deltas[key]
                            if deltas[key]:
                                observed.add(key)
                values = {key: max(0.0, min(1.0, values[key])) for key in DIMENSIONS}
                applied = dict.fromkeys(DIMENSIONS, 0.0)
                confident = item["confidence"] >= 0.65
                if confident:
                    for key in DIMENSIONS:
                        budget = max(0.0, MAX_CHANGE - spent[key])
                        delta = max(-budget, min(budget, item["deltas"][key]))
                        new_value = max(0.0, min(1.0, values[key] + delta))
                        applied[key] = round(new_value - values[key], 8)
                        values[key] = round(new_value, 8)
                        if applied[key]:
                            observed.add(key)
                if supersedes:
                    conn.execute("UPDATE relationship_changes SET status='superseded' WHERE id=?", (supersedes,))
                cursor = conn.execute("""INSERT INTO relationship_changes(profile_id,scope,event_key,occurred_at,reason,confidence,
                    requested_json,applied_json,evidence_json,status,supersedes_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (profile_id, session_id, event_key, occurred_at, item["reason"], item["confidence"], _json(item["deltas"]),
                     _json(applied), _json(evidence), "applied" if confident else "uncertain", supersedes or None))
                change_id = cursor.lastrowid
                conn.executemany("INSERT INTO relationship_evidence_claims VALUES(?,?,?)",
                                 [(profile_id, x["source_key"], change_id) for x in evidence])
                revision = before["revision"] + 1
                conn.execute("""INSERT INTO relationship_states VALUES(?,?,?,?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET
                    familiarity=excluded.familiarity, trust=excluded.trust, affinity=excluded.affinity,
                    observed_json=excluded.observed_json,revision=excluded.revision,updated_at=excluded.updated_at""",
                    (profile_id, *(values[key] for key in DIMENSIONS), _json(sorted(observed)), revision, occurred_at))
                return {"status": "applied" if confident else "uncertain", "change_id": change_id,
                        "before": before, "after": self._relationship_state_unlocked(profile_id), "deltas": applied}
        return await self.context._run_db(write)
