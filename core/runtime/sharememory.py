"""本地只读相关记忆投影，不触发联网或模型总结。"""

from __future__ import annotations

import re

from .mirror.sharing import project_continuity


def terms(text):
    text = str(text or "")[:1600].lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", text))
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(part[i : i + 2] for i in range(len(part) - 1))
    return words


async def search_share_memories(runtime, query, scope, limit=4):
    scope = str(scope or "qzone_broadcast")
    private = ":FriendMessage:" in scope or ":GroupMessage:" in scope
    if not private:
        scope = "qzone_broadcast"
    query_terms = terms(query)
    if not query_terms:
        return []
    archive = runtime.archive
    candidates = []
    # 长期记忆已过滤被取代的来源、过期记录和无效事实。
    # 通用 SQL 也会返回全局记录，因此此处必须严格校验归属。
    if private:
        memories = await archive.search_long_term_memories(
            str(query)[:240], scopes=[scope], limit=16
        )
        for row in memories:
            if row.scope == scope and row.status == "active":
                candidates.append(
                    {
                        "kind": "memory",
                        "text": row.content[:400],
                        "source_id": f"memory:{row.id}",
                        "occurred_at": row.updated_at,
                        "scope": scope,
                    }
                )
    items = await archive.get_living_entities(scope)
    world = await archive.get_continuous_life()
    projection = project_continuity(items or [], world or {}, scope)
    for kind in ("corrections", "activities", "objects"):
        for row in projection[kind]:
            if row.get("state") in {"cancelled", "expired", "superseded", "deleted"}:
                continue
            text = "；".join(
                str(row.get(key) or "")
                for key in ("title", "content", "detail", "progress", "state")
                if row.get(key)
            )
            candidates.append(
                {
                    "kind": kind,
                    "text": text[:400],
                    "source_id": "continuity:" + str(row.get("title") or ""),
                    "occurred_at": row.get("at", ""),
                    "scope": scope,
                }
            )
    for receipt in await archive.read_share_activity_receipts(scope):
        chunks = []
        original = receipt.get("post_context") or {}
        if original.get("content"):
            chunks.append(f"动态：{original['content'][:120]}")
        for row in (receipt.get("conversation") or [])[-2:]:
            chunks.append(
                f"{row.get('author_name') or row.get('author_id') or '评论者'}：{str(row.get('content') or '')[:120]}"
            )
        scene = receipt.get("scene")
        if scene == "qzone_observation":
            chunks.append("实际看到以上文字，未查看图片画面，未确认回应")
        elif scene == "qzone_like":
            chunks.append("已确认点赞以上动态，未表示发出评论")
        else:
            chunks.append(f"我：{str(receipt.get('content') or '')[:180]}")
        candidates.append(
            {
                "kind": (
                    "public_observation"
                    if scene == "qzone_observation"
                    else "public_like"
                    if scene == "qzone_like"
                    else "public_dialogue"
                )
                if scope == "qzone_broadcast"
                else "shared_activity",
                "text": "；".join(chunks)[:500],
                "source_id": receipt.get("event_id", ""),
                "occurred_at": receipt.get("occurred_at", ""),
                "scope": scope,
            }
        )
    for row in candidates:
        row["score"] = len(query_terms & terms(row["text"]))
    candidates = [row for row in candidates if row["score"]]
    candidates.sort(
        key=lambda row: (
            row["kind"] == "corrections",
            row["score"],
            str(row["occurred_at"]),
        ),
        reverse=True,
    )
    return candidates[: max(1, min(6, int(limit)))]
