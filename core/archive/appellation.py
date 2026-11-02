"""从真实消息队列核验称呼偏好，原子保存来源及撤销状态。"""
from __future__ import annotations

import json
import datetime
from typing import Any

from ..addresses import distinct_address


class RelationshipAddressArchive:
    def __init__(self, context: Any):
        self.context = context

    async def apply_relationship_address(self, update: dict, *, session_id: str, rows: list[dict]) -> str:
        profile_id = str(update.get("profile_id") or "").strip()
        operation = str(update.get("operation") or "")
        basis = str(update.get("basis") or "")
        name = str(update.get("name") or "").strip()
        if not profile_id or not session_id or operation not in {"set", "clear"}:
            return "invalid"
        if basis not in {"user_request", "accepted_use"} or (operation == "set" and (not name or len(name) > 40)):
            return "invalid"
        if operation == "clear" and basis != "user_request":
            return "invalid"

        def write() -> str:
            conn = self.context._conn
            conn.execute("BEGIN IMMEDIATE")
            with conn:
                person = conn.execute("SELECT * FROM relationships WHERE id=?", (profile_id,)).fetchone()
                candidates = {}
                for supplied in rows:
                    stored = conn.execute("SELECT * FROM chat_memory_messages WHERE id=? AND session_id=?",
                                          (supplied.get("id"), session_id)).fetchone()
                    if stored is None or stored["is_quoted"]:
                        continue
                    key = str(stored["message_id"] or stored["id"])
                    if key in candidates:
                        return "ambiguous_evidence"
                    candidates[key] = stored
                proof = candidates.get(str(update.get("source_message_id") or ""))
                if (proof is None or proof["role"] != "user" or proof["sender_profile_id"] != profile_id
                        or not proof["message_text"] or update.get("evidence") != proof["message_text"]):
                    return "invalid_evidence"
                # 群里其他人的消息、另一平台同号、引用正文均不能替本人表达称呼偏好。
                if person is not None and person["platform"] and (proof["platform"], proof["user_id"]) != (person["platform"], person["user_id"]):
                    return "invalid_identity"
                try:
                    proof_time = datetime.datetime.fromisoformat(proof["occurred_at"]).timestamp()
                except (ValueError, TypeError):
                    return "invalid_time"
                if operation == "set":
                    named = candidates.get(str(update.get("address_source_message_id") or ""))
                    if named is None or name not in named["message_text"] or named["id"] > proof["id"]:
                        return "invalid_address_source"
                    if basis == "user_request":
                        if named["id"] != proof["id"]:
                            return "invalid_address_source"
                    elif named["role"] != "assistant":
                        return "invalid_address_source"
                    names = (person["name"], person["alias"], person["id"], person["user_id"]) if person else (proof["sender_name"], profile_id, proof["user_id"])
                    name_value = distinct_address(name, *names)
                else:
                    name_value = ""
                previous = json.loads(person["address_basis"] or "{}") if person else {}
                if (proof["id"] <= int(previous.get("source_row_id") or 0)
                        or proof_time < float(previous.get("source_time") or 0)):
                    return "stale"
                record = {"operation":operation, "basis":basis, "name":name_value,
                          "source_row_id":proof["id"], "source_message_id":str(proof["message_id"] or proof["id"]),
                          "session_id":session_id, "evidence":proof["message_text"], "occurred_at":proof["occurred_at"],
                          "source_time":proof_time}
                if operation == "set":
                    record["address_source"] = {"id":named["id"], "text":named["message_text"]}
                if previous.get("legacy_unverified"):
                    record["legacy_unverified"] = previous["legacy_unverified"]
                if person is None:
                    conn.execute("INSERT INTO relationships(id,name,alias,platform,user_id,first_seen,last_seen,source) VALUES(?,?,?,?,?,?,?,?)",
                                 (profile_id, proof["sender_name"] or profile_id, proof["sender_name"],proof["platform"],proof["user_id"],
                                  proof["occurred_at"][:10],proof["occurred_at"][:10],"address_preference"))
                conn.execute("UPDATE relationships SET subjective_name=?, address_basis=? WHERE id=?",
                             (name_value, json.dumps(record,ensure_ascii=False), profile_id))
                return "applied"
        return await self.context._run_db(write)
