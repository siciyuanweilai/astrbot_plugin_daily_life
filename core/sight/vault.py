from __future__ import annotations

import time
from typing import Any

from .clip import SightInsight


class SightVault:
    def __init__(self, archive: Any, *, ttl_seconds: int = 2 * 60 * 60, max_items: int = 60):
        self.archive = archive
        self.ttl_seconds = max(60, int(ttl_seconds or 7200))
        self.max_items = max(8, int(max_items or 60))

    async def upsert(self, insight: SightInsight) -> SightInsight:
        insight.updated_at = time.time()
        saver = getattr(self.archive, "upsert_video_insight", None)
        if not callable(saver):
            return insight
        payload = insight.as_dict()
        payload["cache_key"] = insight.key
        saved = await saver(payload, ttl_seconds=self.ttl_seconds, max_items=self.max_items)
        return SightInsight.from_dict(saved) or insight if isinstance(saved, dict) else insight

    async def get(self, key: str) -> SightInsight | None:
        getter = getattr(self.archive, "get_video_insight", None)
        if not callable(getter):
            return None
        value = await getter(
            str(key or ""),
            ttl_seconds=self.ttl_seconds,
            max_items=self.max_items,
        )
        return SightInsight.from_dict(value) if isinstance(value, dict) else None

    async def recent(self, scope: str, *, limit: int = 3) -> list[SightInsight]:
        getter = getattr(self.archive, "get_recent_video_insights", None)
        if not callable(getter):
            return []
        values = await getter(
            str(scope or "").strip(),
            limit=max(1, int(limit or 3)),
            ttl_seconds=self.ttl_seconds,
            max_items=self.max_items,
        )
        return [
            insight
            for value in values
            if isinstance(value, dict)
            for insight in [SightInsight.from_dict(value)]
            if insight is not None
        ]

    async def remove_message(self, scope: str, message_id: str) -> int:
        remover = getattr(self.archive, "delete_video_insights_for_message", None)
        if not callable(remover):
            return 0
        return int(await remover(str(scope or "").strip(), str(message_id or "").strip()) or 0)
