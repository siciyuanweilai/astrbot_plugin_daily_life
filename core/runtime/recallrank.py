"""统一多路召回的次序；相关性优先，不按访问次数强化记忆。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date as date_type
from typing import Any

from ..clock import now as life_now


def memory_vector_text(item: Any) -> str:
    """索引与召回使用同一份文本，避免标题差异导致缓存失效。"""
    return " ".join(filter(None, (
        str(getattr(item, "title", "") or ""),
        str(getattr(item, "content", "") or ""),
    )))


def _retention_factor(item: Any) -> float:
    """普通经历随时间轻微淡出，重要事实和纠正不随访问次数变化。"""
    category = str(getattr(item, "category", ""))
    weight = float(getattr(item, "weight", 1.0))
    if category not in {"episode", "chat_summary", "feedback", "general"} or weight >= 2:
        return 1.0
    date = str(getattr(item, "date", "") or "")
    try:
        age = max(0, (life_now().date() - date_type.fromisoformat(date[:10])).days)
    except ValueError:
        return 1.0
    return 1.0 - 0.15 * min(age / 180.0, 1.0)


def memory_identity(item: Any) -> tuple:
    source = str(getattr(item, "source_table", "") or "")
    source_id = str(getattr(item, "source_id", "") or "")
    scope = str(getattr(item, "scope", "") or "")
    category = str(getattr(item, "category", "") or "")
    if source and source_id:
        if source == "life_episodes":
            # 同一经历的前后进展是不同事件，不能当作同一状态的旧修订丢掉。
            return scope, category, source, source_id, getattr(item, "id", 0)
        return scope, category, source, source_id
    return scope, category, "id", getattr(item, "id", 0)


def fuse_memory_routes(routes: Sequence[tuple[float, list[Any]]], limit: int) -> list[Any]:
    """用加权 RRF 合并不同分值尺度，重叠命中优先，关联补充不压过直接命中。"""
    scores: dict[tuple, float] = {}
    records: dict[tuple, Any] = {}
    order: dict[tuple, int] = {}
    for weight, items in routes:
        seen = set()
        for rank, item in enumerate(items, 1):
            key = memory_identity(item)
            if key in seen:
                continue
            seen.add(key)
            order.setdefault(key, len(order))
            previous = records.get(key)
            if previous is None or getattr(item, "id", 0) > getattr(previous, "id", 0):
                records[key] = item
            scores[key] = scores.get(key, 0.0) + weight / (20.0 + rank)
    # 内容完全一致的重复候选只占一个注入位置，不改写或删除其来源记录。
    output, seen_content = [], set()
    for key in sorted(scores, key=lambda key: (-scores[key] * _retention_factor(records[key]), order[key])):
        item = records[key]
        content_key = (getattr(item, "scope", ""), getattr(item, "category", ""),
                       " ".join(str(getattr(item, "content", "") or "").split()))
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        output.append(item)
        if len(output) >= limit:
            break
    return output


__all__ = ["fuse_memory_routes", "memory_identity"]
