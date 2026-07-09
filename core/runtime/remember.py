from __future__ import annotations

from typing import Any


class RuntimeMemoryMixin:
    def _event_memory_scopes(self, event: Any = None) -> list[str]:
        if event is None:
            return []
        scopes: list[str] = []
        group_id, _ = self._event_group_meta(event)
        session_id = self._event_session_id(event)
        for value in (group_id, session_id):
            text = str(value or "").strip()
            if text and text not in scopes:
                scopes.append(text)
        return scopes

    def _memory_query_from_event(self, event: Any = None, message: str = "") -> str:
        pieces: list[str] = []
        for item in (
            str(message or "").strip(),
            self._event_message_text(event) if event is not None else "",
        ):
            text = str(item or "").strip()
            if text and text not in pieces:
                pieces.append(text)
        return " ".join(item for item in pieces if item).strip()

    def _format_memory_hits(self, memories: list[Any], *, include_source: bool = True, limit: int = 5) -> str:
        lines: list[str] = []
        for item in memories[: max(1, limit)]:
            content = str(getattr(item, "content", "") or "").strip()
            if not content:
                continue
            label = str(getattr(item, "title", "") or getattr(item, "category", "") or "记忆").strip()
            source = ""
            if include_source:
                source_table = str(getattr(item, "source_table", "") or "").strip()
                source_id = str(getattr(item, "source_id", "") or "").strip()
                date = str(getattr(item, "date", "") or "").strip()
                bits = [bit for bit in (date, source_table, source_id) if bit]
                source = f"（来源：{' / '.join(bits)}）" if bits else ""
            lines.append(f"- {label}：{content}{source}")
        return "\n".join(lines)

    async def search_life_memory(
        self,
        query: str,
        *,
        event: Any = None,
        mode: str = "search",
        category: str = "",
        limit: int = 5,
    ) -> list[Any]:
        clean_query = str(query or "").strip()
        categories = [item.strip() for item in str(category or "").replace("，", ",").split(",") if item.strip()]
        scopes = self._event_memory_scopes(event)
        limit = max(1, min(int(limit or 5), 12))
        if clean_query:
            return await self.archive.search_long_term_memories(
                clean_query,
                scopes=scopes,
                categories=categories,
                limit=limit,
            )
        if str(mode or "").strip().lower() in {"recent", "time"}:
            return await self.archive.list_recent_long_term_memories(
                scopes=scopes,
                categories=categories,
                limit=limit,
            )
        return []

    async def life_memory_search(
        self,
        event: Any,
        query: str = "",
        mode: str = "search",
        category: str = "",
        limit: int = 5,
    ) -> str:
        hits = await self.search_life_memory(
            query,
            event=event,
            mode=mode,
            category=category,
            limit=limit,
        )
        if not hits:
            return "没有检索到相关长期记忆。"
        body = self._format_memory_hits(hits, include_source=True, limit=limit)
        return f"长期记忆检索结果：\n{body}"

    async def build_heuristic_memory_context(self, event: Any, message: str = "", limit: int = 4) -> str:
        query = self._memory_query_from_event(event, message)
        if not query:
            return ""
        hits = await self.search_life_memory(
            query,
            event=event,
            mode="search",
            limit=limit,
        )
        if not hits:
            return ""
        body = self._format_memory_hits(hits, include_source=True, limit=limit)
        if not body:
            return ""
        return (
            "\n[HiddenLongTermMemory]\n"
            "以下是根据本轮真实消息检索到的相关长期记忆，只作为内部判断依据；不要逐字复述给用户。\n"
            f"{body}\n"
        )
