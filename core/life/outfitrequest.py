"""短期穿搭意愿：保留待解决需求，不把准备、洗护或生成写成穿着事实。"""

from __future__ import annotations

import datetime
import hashlib
import json

from .wardrobe import normalize_outfit_components


def clothing_identity(day) -> str:
    components = normalize_outfit_components(day.meta.get("outfit_components"))
    main = components.get("main_clothing") or {}
    return str(main.get("description") or day.outfit or "").strip()


def active_outfit_request(day, now: datetime.datetime) -> dict:
    raw = day.meta.get("outfit_request")
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(value, dict) or value.get("status") != "pending":
            return {}
        expires = datetime.datetime.fromisoformat(value["expires_at"])
        if now.replace(tzinfo=None) >= expires.replace(tzinfo=None):
            return {}
        if clothing_identity(day) != value.get("previous_clothing"):
            return {}
        return value if value.get("instruction") and value.get("id") else {}
    except (ValueError, TypeError, KeyError):
        return {}


def make_outfit_request(day, instruction: str, now: datetime.datetime) -> dict:
    instruction = str(instruction or "").strip()[:1000]
    existing = active_outfit_request(day, now)
    if existing.get("instruction") == instruction:
        return existing
    requested = now.replace(tzinfo=None).isoformat(sep=" ")
    identity = hashlib.sha256(
        json.dumps([day.date, instruction, requested], ensure_ascii=False).encode()
    ).hexdigest()[:24]
    return {
        "id": identity, "status": "pending", "instruction": instruction,
        "previous_clothing": clothing_identity(day), "requested_at": requested,
        "expires_at": (now.replace(tzinfo=None) + datetime.timedelta(hours=6)).isoformat(sep=" "),
    }
