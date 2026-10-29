"""面向分享的连续生活投影；不导出原始内核、证据、资源地址或历史版本。"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import math
import unicodedata

from ...clock import TIMEZONE
from ...clock import now as life_now


def _text(value, limit=160):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _progress_version(row: dict) -> str:
    """只对实质内容取版本；更新时间、存储 revision 和标点变化不制造新经历。"""
    meaningful = {}
    for key in (
        "state",
        "detail",
        "progress",
        "artifact_version",
        "owner",
        "quantity",
        "unit",
        "delivery",
        "available_at",
    ):
        value = row.get(key)
        if isinstance(value, str):
            value = "".join(
                ch
                for ch in value
                if not ch.isspace() and not unicodedata.category(ch).startswith("P")
            )
        if value is not None:
            meaningful[key] = value
    return hashlib.sha256(
        json.dumps(meaningful, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:24]


def project_continuity(items: list, world: dict, scope: str) -> dict:
    private = ":FriendMessage:" in scope
    result = {
        "version": 1,
        "corrections": [],
        "activities": [],
        "objects": [],
        "interests": [],
        "goals": [],
    }
    for item in items:
        if not isinstance(item, dict) or item.get("scope") not in {scope, "global"}:
            continue
        kind = item.get("kind")
        # 共同经历只回到该私聊，公开及群聊必须具有明确公开归属。
        if kind == "shared_activity" and (not private or item.get("scope") != scope):
            continue
        if not private and not (
            item.get("visibility") == "public"
            or (kind == "object" and item.get("scope") == "qzone_broadcast")
        ):
            continue
        fields = {"title": 160, "state": 32, "at": 40}
        if kind == "correction":
            fields["content"] = 400
            key = "corrections"
        elif kind == "shared_activity":
            fields.update(detail=200, progress=160)
            key = "activities"
        elif kind == "object":
            fields.update(
                progress=160,
                next_step=160,
                provenance=120,
                delivery=32,
                delivered_at=40,
                unit=30,
                available_at=40,
            )
            # 物品正文与位置可能含私人资料，只在其所属私聊使用。
            if private and item.get("scope") == scope:
                fields.update(content=240, location=120)
            key = "objects"
        else:
            continue
        if len(result[key]) < 4:
            row = {name: _text(item.get(name), limit) for name, limit in fields.items()}
            if kind == "object":
                owner = item.get("owner")
                if isinstance(owner, str) and owner in {
                    "self",
                    "user",
                    "shared",
                    "unknown",
                }:
                    row["owner"] = owner
                quantity = item.get("quantity")
                if (
                    isinstance(quantity, (int, float))
                    and not isinstance(quantity, bool)
                    and math.isfinite(quantity)
                    and quantity >= 0
                    and row["unit"]
                ):
                    row["quantity"] = quantity
                version = item.get("artifact_version")
                if (
                    isinstance(version, int)
                    and not isinstance(version, bool)
                    and version > 0
                ):
                    row["artifact_version"] = version
            if kind in {"shared_activity", "object"}:
                row["event_id"] = str(item.get("id") or "")[:120]
                row["progress_version"] = _progress_version(row)
            result[key].append(row)

    model = (world.get("kernel") or {}).get("self_model") or {}
    safe_interests = set()
    for item in model.get("interests", []):
        if not isinstance(item, dict) or item.get("status") != "supported":
            continue
        refs = item.get("evidence_ids") or []
        # 未标明公开范围的私人来源不能借自身兴趣进入公开文案。
        public = refs == ["persona"] or item.get("visibility") == "public"
        if not private and not public:
            continue
        if public:
            safe_interests.add(item.get("id"))
        result["interests"].append(
            {
                "label": _text(item.get("label")),
                "state": _text(item.get("disposition", "active"), 24),
                "last_practiced_at": _text(item.get("last_practiced_at"), 40),
            }
        )
    for goal in world.get("goals", []):
        if not isinstance(goal, dict):
            continue
        source = goal.get("source_id", "")
        if not private and not (
            source == "persona"
            or goal.get("visibility") == "public"
            or goal.get("interest_id") in safe_interests
        ):
            continue
        steps = []
        for step in goal.get("steps", []):
            if not isinstance(step, dict):
                continue
            state = step.get("status", "pending")
            reviews = step.get("reviews") or []
            if state == "completed" and not (
                reviews
                and isinstance(reviews[-1], dict)
                and reviews[-1].get("verdict") == "passed"
            ):
                state = "awaiting_review"
            steps.append({"title": _text(step.get("title")), "state": _text(state, 32)})
        state = goal.get("status")
        if state == "completed" and any(step["state"] != "completed" for step in steps):
            state = "active"
        result["goals"].append(
            {
                "title": _text(goal.get("title")),
                "state": _text(state, 32),
                "steps": steps[:4],
            }
        )
    result["interests"] = result["interests"][-4:]
    result["goals"] = result["goals"][-3:]
    return result


async def share_continuity(runtime, scope: str) -> dict:
    archive = getattr(runtime, "archive", None)
    reader = getattr(archive, "get_living_entities", None)
    world_reader = getattr(archive, "get_continuous_life", None)
    items, public_items, world = await asyncio.gather(
        reader(scope) if callable(reader) else asyncio.sleep(0, result=[]),
        reader("qzone_broadcast")
        if callable(reader) and scope != "qzone_broadcast"
        else asyncio.sleep(0, result=[]),
        world_reader() if callable(world_reader) else asyncio.sleep(0, result={}),
    )
    result = project_continuity(items or [], world or {}, scope)
    result["public"] = project_continuity(
        (items if scope == "qzone_broadcast" else public_items) or [],
        world or {},
        "qzone_broadcast",
    )
    return result


async def share_timing(runtime, scope: str) -> dict:
    if not any(kind in scope for kind in (":FriendMessage:", ":GroupMessage:")):
        return {}
    reader = getattr(
        getattr(runtime, "archive", None), "get_conversation_receipt_before", None
    )
    now = life_now().replace(tzinfo=TIMEZONE)
    previous = await reader(scope, now.timestamp()) if callable(reader) else 0
    return {
        "version": 1,
        "now": now.isoformat(timespec="seconds"),
        "last_sent_at": datetime.datetime.fromtimestamp(previous, TIMEZONE).isoformat(
            timespec="seconds"
        )
        if previous
        else "",
        "elapsed_seconds": max(0, int(now.timestamp() - previous))
        if previous
        else None,
        "scope_kind": "group" if ":GroupMessage:" in scope else "private",
    }
