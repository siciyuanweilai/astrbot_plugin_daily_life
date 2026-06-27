from __future__ import annotations

from collections import deque

from .grain import StructuredMessage


class StructuredDockMixin:
    def _structured_store(self) -> dict[str, deque[StructuredMessage]]:
        store = getattr(self, "_structured_messages", None)
        if not isinstance(store, dict):
            store = {}
            self._structured_messages = store
        return store

    def _structured_scope_messages(self, scope: str) -> deque[StructuredMessage]:
        scope = str(scope or "").strip()
        store = self._structured_store()
        messages = store.get(scope)
        if not isinstance(messages, deque):
            messages = deque(maxlen=self._STRUCTURED_CONTEXT_LIMIT)
            store[scope] = messages
        return messages

    def _structured_upsert_message(self, message: StructuredMessage) -> StructuredMessage:
        messages = self._structured_scope_messages(message.scope)
        key = message.key
        if key:
            kept = [item for item in messages if item.key != key]
            kept.append(message)
            self._structured_store()[message.scope] = deque(
                kept[-self._STRUCTURED_CONTEXT_LIMIT :],
                maxlen=self._STRUCTURED_CONTEXT_LIMIT,
            )
            return message
        messages.append(message)
        return message
