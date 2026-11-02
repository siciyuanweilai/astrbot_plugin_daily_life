"""人物归属由账号和会话确定，称呼仅用于表达。"""
from __future__ import annotations

from typing import Any


def relationship_for_contact(relationships: Any, *, user_id: str, scope: str) -> dict:
    if not isinstance(relationships, list):
        return {}
    user_id, scope = str(user_id or "").strip(), str(scope or "").strip()
    origin = scope.rsplit(":", 2)
    platform = origin[0] if len(origin) == 3 else ""
    matches = []
    for row in relationships:
        if not isinstance(row, dict):
            continue
        compatible = not platform or not row.get("platform") or row["platform"] == platform
        matched = bool(user_id and compatible and user_id in {str(row.get("id") or ""), str(row.get("user_id") or "")})
        for contact in row.get("contacts") or []:
            if not isinstance(contact, dict):
                continue
            if platform and contact.get("platform") and contact["platform"] != platform:
                continue
            if user_id:
                matched |= user_id == str(contact.get("user_id") or "")
            elif scope and ":GroupMessage:" not in scope and contact.get("contact_type") != "group_member":
                matched |= scope == str(contact.get("target_scope") or "")
        if matched:
            matches.append(row)
    return matches[0] if len(matches) == 1 else {}
