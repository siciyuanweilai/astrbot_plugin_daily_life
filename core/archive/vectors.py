from typing import Any


class MemoryVectorArchiveMixin:
    async def get_memory_vectors(
        self, target_type: str, target_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        keys = [self._text(value) for value in target_ids if self._text(value)]
        if not target_type or not keys:
            return {}

        def dbwork():
            placeholders = ",".join("?" for _ in keys)
            rows = self._conn.execute(
                f"SELECT * FROM memory_vectors WHERE target_type = ? AND target_id IN ({placeholders})",
                (self._text(target_type), *keys),
            ).fetchall()
            return {
                str(row["target_id"]): {
                    "target_type": row["target_type"],
                    "target_id": row["target_id"],
                    "content_hash": row["content_hash"],
                    "provider_id": row["provider_id"],
                    "dimensions": int(row["dimensions"] or 0),
                    "vector_json": row["vector_json"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            }

        return await self._run_db(dbwork)

    async def upsert_memory_vectors(self, items: list[dict[str, Any]]) -> int:
        rows = []
        for item in items or []:
            target_type = self._text(item.get("target_type"))
            target_id = self._text(item.get("target_id"))
            content_hash = self._text(item.get("content_hash"))
            provider_id = self._text(item.get("provider_id"))
            vector_json = self._text(item.get("vector_json"))
            try:
                dimensions = max(0, int(item.get("dimensions") or 0))
            except (TypeError, ValueError):
                dimensions = 0
            if target_type and target_id and content_hash and vector_json and dimensions:
                rows.append(
                    (
                        target_type,
                        target_id,
                        content_hash,
                        provider_id,
                        dimensions,
                        vector_json,
                    )
                )
        if not rows:
            return 0

        def dbwork():
            self._conn.executemany(
                """
                INSERT INTO memory_vectors(
                    target_type, target_id, content_hash, provider_id, dimensions,
                    vector_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(target_type, target_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    provider_id = excluded.provider_id,
                    dimensions = excluded.dimensions,
                    vector_json = excluded.vector_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
            self._conn.commit()
            return len(rows)

        return await self._run_db(dbwork)


__all__ = ["MemoryVectorArchiveMixin"]
