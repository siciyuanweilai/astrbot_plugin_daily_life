from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RuntimeScopeEntry:
    touched_at: float
    aliases: set[str] = field(default_factory=set)


class RuntimeScopeState:
    """统一管理只需在当前进程内保留的会话状态。"""

    DEFAULT_MAX_SCOPES = 512
    DEFAULT_IDLE_SECONDS = 7 * 24 * 60 * 60
    PRUNE_INTERVAL_SECONDS = 60.0

    _SCOPE_STORES = (
        "_response_gate_last_reply_at",
        "_response_gate_last_seen_at",
        "_response_gate_first_seen_at",
        "_response_gate_pending_count",
        "_response_gate_reply_times",
        "_response_gate_no_reply_count",
        "_response_gate_backoff_until",
        "_response_gate_continuation",
        "_continuous_turn_batches",
        "_continuous_turn_revisions",
        "_structured_messages",
        "_structured_sequence_counters",
        "_semantic_segment_revisions",
        "_semantic_segment_epochs",
        "_chat_pacing_state",
        "_proactive_last_reply_at",
        "_proactive_private_last_revisit_at",
        "_proactive_air_state",
        "_proactive_feedback_watch",
        "_emoji_sent_state",
        "_life_media_last_images",
        "_life_media_source_events",
        "_life_media_cadence",
        "_life_photo_suite_last_tasks",
        "_life_reverse_prompt_cache",
        "_t2i_forward_cache",
        "_tool_reply_rounds",
        "_voice_switch_rounds",
        "_voice_switch_cadence",
        "_recalled_messages",
    )

    def __init__(
        self,
        runtime: Any,
        *,
        max_scopes: int = DEFAULT_MAX_SCOPES,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
    ) -> None:
        self.runtime = runtime
        self.max_scopes = max(1, int(max_scopes))
        self.idle_seconds = max(60.0, float(idle_seconds))
        self._entries: dict[str, RuntimeScopeEntry] = {}
        self._last_pruned_at = 0.0

    def note_event(self, event: Any) -> None:
        canonical, aliases = self._event_scope_aliases(event)
        if not canonical:
            return
        now = time.monotonic()
        entry = self._entries.get(canonical)
        if entry is None:
            entry = RuntimeScopeEntry(touched_at=now)
            self._entries[canonical] = entry
        entry.touched_at = now
        entry.aliases.update(aliases)
        self.prune(now=now, protected={canonical})

    def prune(
        self,
        *,
        now: float | None = None,
        protected: set[str] | None = None,
        force: bool = False,
    ) -> int:
        current = time.monotonic() if now is None else float(now)
        protected = set(protected or ())
        if (
            not force
            and len(self._entries) <= self.max_scopes
            and current - self._last_pruned_at < self.PRUNE_INTERVAL_SECONDS
        ):
            return 0
        self._last_pruned_at = current
        self._prune_snapshot_cache()

        stale = {
            key
            for key, entry in self._entries.items()
            if key not in protected and current - entry.touched_at > self.idle_seconds
        }
        remaining = len(self._entries) - len(stale)
        if remaining > self.max_scopes:
            candidates = sorted(
                (
                    (entry.touched_at, key)
                    for key, entry in self._entries.items()
                    if key not in protected and key not in stale
                ),
                key=lambda item: item[0],
            )
            stale.update(key for _, key in candidates[: remaining - self.max_scopes])

        for key in stale:
            self._evict_scope(key)
        return len(stale)

    def clear(self) -> None:
        for key in list(self._entries):
            self._evict_scope(key)
        self._entries.clear()
        self._last_pruned_at = 0.0
        self._clear_store("_injection_snapshot_cache")

    def snapshot(self) -> dict[str, int]:
        return {
            "scopes": len(self._entries),
            "max_scopes": self.max_scopes,
        }

    def _event_scope_aliases(self, event: Any) -> tuple[str, set[str]]:
        session_getter = getattr(self.runtime, "_event_session_id", None)
        group_getter = getattr(self.runtime, "_event_group_meta", None)
        sender_getter = getattr(self.runtime, "_safe_event_call", None)
        session_id = (
            str(session_getter(event) or "").strip()
            if callable(session_getter)
            else str(getattr(event, "unified_msg_origin", "") or "").strip()
        )
        group_id = ""
        if callable(group_getter):
            group_meta = group_getter(event)
            if isinstance(group_meta, tuple) and group_meta:
                group_id = str(group_meta[0] or "").strip()
        sender_id = (
            str(sender_getter(event, "get_sender_id") or "").strip()
            if callable(sender_getter)
            else ""
        )
        canonical = session_id or group_id or sender_id
        return canonical, {item for item in (session_id, group_id, sender_id) if item}

    def _evict_scope(self, canonical: str) -> None:
        entry = self._entries.pop(canonical, None)
        if entry is None:
            return
        aliases = set(entry.aliases)
        shared_aliases = {
            alias for other in self._entries.values() for alias in other.aliases
        }
        aliases.difference_update(shared_aliases)
        if not aliases:
            return

        self._cancel_idle_tasks(aliases)
        for name in self._SCOPE_STORES:
            store = getattr(self.runtime, name, None)
            if not isinstance(store, dict):
                continue
            for alias in aliases:
                store.pop(alias, None)

        attention = getattr(self.runtime, "_response_gate_attention", None)
        if (
            isinstance(attention, dict)
            and str(attention.get("focus_key") or "") in aliases
        ):
            attention.clear()
        self._remove_snapshot_aliases(aliases)

    def _cancel_idle_tasks(self, aliases: set[str]) -> None:
        candidates = getattr(self.runtime, "_proactive_idle_candidates", None)
        tasks = getattr(self.runtime, "_proactive_idle_tasks", None)
        current_task = None
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            pass
        for alias in aliases:
            if isinstance(candidates, dict):
                candidates.pop(alias, None)
            task = tasks.pop(alias, None) if isinstance(tasks, dict) else None
            if (
                isinstance(task, asyncio.Task)
                and task is not current_task
                and not task.done()
            ):
                task.cancel()

    def _prune_snapshot_cache(self) -> None:
        cache = getattr(self.runtime, "_injection_snapshot_cache", None)
        if not isinstance(cache, dict):
            return
        cutoff = time.time() - 60.0
        for key, value in list(cache.items()):
            timestamp = value.get("ts") if isinstance(value, dict) else None
            try:
                expired = float(timestamp or 0.0) < cutoff
            except (TypeError, ValueError):
                expired = True
            if expired:
                cache.pop(key, None)

    def _remove_snapshot_aliases(self, aliases: set[str]) -> None:
        cache = getattr(self.runtime, "_injection_snapshot_cache", None)
        if not isinstance(cache, dict):
            return
        for key in list(cache):
            _, separator, scope = str(key).partition(":")
            if separator and scope in aliases:
                cache.pop(key, None)

    def _clear_store(self, name: str) -> None:
        store = getattr(self.runtime, name, None)
        clear = getattr(store, "clear", None)
        if callable(clear):
            clear()


__all__ = ["RuntimeScopeEntry", "RuntimeScopeState"]
