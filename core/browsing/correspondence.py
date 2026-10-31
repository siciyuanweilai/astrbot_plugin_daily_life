"""账号隔离的互动记录与原子发送占位；复用生活经历存储。"""
from __future__ import annotations

import hashlib
import json
import time


class BiliSocialStore:
    def __init__(self, archive, account):
        self.archive = archive
        self.account = str(account)
        self.key = "bili_social:" + self.account

    def identity(self, key):
        return "bilis:" + hashlib.sha256((self.account + ":" + key).encode()).hexdigest()[:40]

    async def state(self):
        def read():
            row = self.archive._conn.execute("SELECT value FROM meta WHERE key=?", (self.key,)).fetchone()
            return json.loads(row[0]) if row else {}
        return await self.archive._run_db(read)

    async def update(self, patch):
        def write():
            conn = self.archive._conn
            with conn:
                row = conn.execute("SELECT value FROM meta WHERE key=?", (self.key,)).fetchone()
                value = {**(json.loads(row[0]) if row else {}), **patch}
                conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (self.key, json.dumps(value, ensure_ascii=False, allow_nan=False)))
            return value
        return await self.archive._run_db(write)

    async def records(self, *, scope=None, limit=60, exact_scope=False):
        def read():
            clause, args = "", [self.account]
            if scope is not None:
                clause = " AND scope=?" if exact_scope else " AND scope IN ('global',?)"
                args.append(scope)
            args.append(max(1, min(1000, limit)))
            rows = self.archive._conn.execute(
                "SELECT payload_json FROM continuous_life_entries WHERE kind='bili_social' AND json_extract(payload_json,'$.account')=?" + clause + " ORDER BY occurred_at DESC,id DESC LIMIT ?", args).fetchall()
            return [json.loads(row[0]) for row in rows]
        return await self.archive._run_db(read)

    async def merge_identities(self, original, bindings, challenges):
        """网络轮询只合并仍未被绑定/解绑命令修改的条目。"""
        def write():
            with self.archive._conn:
                row = self.archive._conn.execute("SELECT value FROM meta WHERE key=?", (self.key,)).fetchone()
                state = json.loads(row[0]) if row else {}
                for field, proposed in (("bindings", bindings), ("challenges", challenges)):
                    before, current = original.get(field) or {}, dict(state.get(field) or {})
                    for scope in before.keys() | proposed.keys():
                        if current.get(scope) == before.get(scope):
                            if scope in proposed:
                                # 过期的验证结果不能恢复用户已取消的挑战。
                                if field == "bindings" and proposed[scope].get("uid") != (before.get(scope) or {}).get("uid"):
                                    if (state.get("challenges") or {}).get(scope) != (original.get("challenges") or {}).get(scope):
                                        continue
                                current[scope] = proposed[scope]
                            else:
                                current.pop(scope, None)
                    state[field] = current
                self.archive._conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                           (self.key, json.dumps(state, ensure_ascii=False, allow_nan=False)))
            return state
        return await self.archive._run_db(write)

    async def public_contact_records(self, uid):
        """按绑定 UID 取事实，不让其他会话和全局记录挤掉去重依据。"""
        def read():
            rows = self.archive._conn.execute(
                "SELECT payload_json FROM continuous_life_entries WHERE kind='bili_social' "
                "AND json_extract(payload_json,'$.account')=? AND json_extract(payload_json,'$.uid')=? "
                "AND json_extract(payload_json,'$.kind') IN ('mention','dm','reply') "
                "AND (json_extract(payload_json,'$.kind')='mention' OR json_extract(payload_json,'$.status') IN ('sending','uncertain') "
                "OR json_extract(payload_json,'$.bookkeeping_pending')=1) ORDER BY occurred_at DESC,id DESC",
                (self.account, str(uid))).fetchall()
            return [json.loads(row[0]) for row in rows]
        return await self.archive._run_db(read)

    @staticmethod
    def retryable(record):
        return (record.get("status") == "rejected" and record.get("attempts", 1) < 3 and
                time.time() >= float(record.get("retry_after") or 0))

    async def get(self, identity):
        def read():
            row = self.archive._conn.execute("SELECT payload_json FROM continuous_life_entries WHERE id=? AND kind='bili_social' AND json_extract(payload_json,'$.account')=?", (identity, self.account)).fetchone()
            return json.loads(row[0]) if row else None
        return await self.archive._run_db(read)

    async def pending(self, statuses, *, scope=None, bookkeeping_only=False):
        def read():
            clause, args = "", [self.account, *statuses]
            if scope is not None:
                clause = " AND scope=?"
                args.append(scope)
            if bookkeeping_only:
                clause += " AND json_extract(payload_json,'$.bookkeeping_pending')=1"
            rows = self.archive._conn.execute(
                "SELECT payload_json FROM continuous_life_entries WHERE kind='bili_social' AND json_extract(payload_json,'$.account')=? AND json_extract(payload_json,'$.status') IN (" + ",".join("?" for _ in statuses) + ")" + clause + " ORDER BY occurred_at,id LIMIT 1000", args).fetchall()
            return [json.loads(row[0]) for row in rows]
        return await self.archive._run_db(read)

    async def put(self, key, record):
        value = {**record, "id": self.identity(key), "account": self.account}
        def write():
            with self.archive._conn:
                self._save(value)
            return value
        return await self.archive._run_db(write)

    def _save(self, value):
        self.archive._conn.execute(
            "INSERT INTO continuous_life_entries(id,kind,date,scope,occurred_at,summary,payload_json) VALUES(?,'bili_social',?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET date=excluded.date,scope=excluded.scope,occurred_at=excluded.occurred_at,summary=excluded.summary,payload_json=excluded.payload_json",
            (value["id"], "" if value.get("status") in {"sending", "uncertain"} else value["at"][:10],
             value.get("scope", "global"), value["at"], str(value.get("text") or value.get("reason") or "")[:500],
             json.dumps(value, ensure_ascii=False, allow_nan=False)))

    async def claim(self, key, record, *, daily_limit=20):
        """网络请求前落盘；只允许冷却后重新考虑明确被拒绝的发送。"""
        value = {**record, "id": self.identity(key), "account": self.account, "status": "sending"}
        def write():
            conn = self.archive._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                previous = conn.execute("SELECT payload_json FROM continuous_life_entries WHERE id=?", (value["id"],)).fetchone()
                previous = json.loads(previous[0]) if previous else {}
                if value.get("kind") == "mention":
                    rows = conn.execute(
                        "SELECT payload_json FROM continuous_life_entries WHERE kind='bili_social' "
                        "AND json_extract(payload_json,'$.account')=? AND json_extract(payload_json,'$.uid')=? "
                        "AND json_extract(payload_json,'$.kind') IN ('mention','dm','reply')",
                        (self.account, value.get("uid"))).fetchall()
                    prior_attempts = []
                    for raw in rows:
                        other = json.loads(raw[0])
                        if other.get("outbound") and (other.get("status") in {"sending", "uncertain"} or other.get("bookkeeping_pending")):
                            conn.rollback()
                            return None
                        if other.get("kind") == "mention" and str(other.get("aid")) == str(value.get("aid")):
                            if not self.retryable(other):
                                conn.rollback()
                                return None
                            prior_attempts.append(other)
                    # 兼容旧版按会话/分 P 生成的键，明确拒绝的尝试次数仍保留。
                    if not previous and prior_attempts:
                        previous = max(prior_attempts, key=lambda item: item["at"])
                        value["id"] = previous["id"]
                if previous and (previous.get("status") != "rejected" or
                                 previous.get("attempts", 1) >= 3 or
                                 time.time() < float(previous.get("retry_after") or 0)):
                    conn.rollback()
                    return None
                count = conn.execute(
                    "SELECT coalesce(sum(coalesce(json_extract(payload_json,'$.attempts'),1)),0) FROM continuous_life_entries WHERE kind='bili_social' AND json_extract(payload_json,'$.account')=? AND substr(occurred_at,1,10)=? AND json_extract(payload_json,'$.outbound')=1",
                    (self.account, value["at"][:10])).fetchone()[0]
                if count >= daily_limit:
                    conn.rollback()
                    return None
                value["attempts"] = (int(previous.get("attempts") or 1) if previous else 0) + 1
                value["previous_attempts"] = (previous.get("previous_attempts", []) +
                    ([{k: previous.get(k) for k in ("at", "text", "reason", "status")}] if previous else []))[-2:]
                self._save(value)
                conn.commit()
                return value
            except BaseException:
                conn.rollback()
                raise
        return await self.archive._run_db(write)

    async def finish(self, record, **patch):
        value = {**record, **patch}
        def write():
            with self.archive._conn:
                self._save(value)
            return value
        return await self.archive._run_db(write)
