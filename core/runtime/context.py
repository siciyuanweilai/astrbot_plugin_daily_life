from __future__ import annotations

from typing import Any


class ContextSnapshotRepository:
    def __init__(self, archive: Any):
        reader = getattr(archive, "get_context_snapshot", None)
        if not callable(reader):
            raise TypeError("归档服务缺少上下文快照读取能力")
        self._reader = reader

    async def read(
        self,
        *,
        max_summaries: int,
        experience_scope: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._reader(
            max_summaries=max_summaries,
            experience_scope=experience_scope,
            session_id=session_id,
        )


__all__ = ["ContextSnapshotRepository"]
