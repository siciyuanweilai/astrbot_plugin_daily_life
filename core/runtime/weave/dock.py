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

    def _structured_upsert_message(
        self, message: StructuredMessage
    ) -> StructuredMessage:
        messages = self._structured_scope_messages(message.scope)
        key = message.key
        existing = next((item for item in messages if key and item.key == key), None)
        counters = getattr(self, "_structured_sequence_counters", None)
        if not isinstance(counters, dict):
            counters = {}
            self._structured_sequence_counters = counters
        current_counter = max(
            int(counters.get(message.scope, 0) or 0),
            max((int(getattr(item, "sequence", 0) or 0) for item in messages), default=0),
        )
        if existing is not None and int(getattr(existing, "sequence", 0) or 0) > 0:
            message.sequence = int(existing.sequence)
        elif int(getattr(message, "sequence", 0) or 0) <= 0:
            current_counter += 1
            message.sequence = current_counter
        counters[message.scope] = max(current_counter, int(message.sequence or 0))
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
