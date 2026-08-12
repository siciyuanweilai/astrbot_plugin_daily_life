from __future__ import annotations

from typing import Any, Protocol


class ContextSnapshotSource(Protocol):
    """归档服务向运行时提供的上下文快照契约。"""

    async def get_context_snapshot(
        self,
        *,
        max_summaries: int,
        experience_scope: str = "",
        session_id: str = "",
    ) -> dict[str, Any]: ...


class ContextSnapshotRepository:
    def __init__(self, archive: ContextSnapshotSource):
        reader = getattr(archive, "get_context_snapshot", None)
        if not callable(reader):
            raise TypeError("归档服务缺少上下文快照读取能力")
        self._archive = archive

    async def read(
        self,
        *,
        max_summaries: int,
        experience_scope: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._archive.get_context_snapshot(
            max_summaries=max_summaries,
            experience_scope=experience_scope,
            session_id=session_id,
        )


__all__ = ["ContextSnapshotRepository", "ContextSnapshotSource"]
