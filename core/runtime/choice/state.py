from __future__ import annotations

import datetime
from typing import Any

from ...life.tools import resolve_business_now


class ResponseGateStateMixin:
    _RESPONSE_GATE_CONTINUATION_SECONDS = 8 * 60
    _RESPONSE_GATE_CONTINUATION_TURNS = 2

    def _init_response_gate_state(self) -> None:
        self._response_gate_last_reply_at: dict[str, datetime.datetime] = {}
        self._response_gate_last_seen_at: dict[str, datetime.datetime] = {}
        self._response_gate_first_seen_at: dict[str, datetime.datetime] = {}
        self._response_gate_pending_count: dict[str, int] = {}
        self._response_gate_reply_times: dict[str, list[datetime.datetime]] = {}
        self._response_gate_no_reply_count: dict[str, int] = {}
        self._response_gate_backoff_until: dict[str, datetime.datetime] = {}
        self._response_gate_continuation: dict[str, dict[str, Any]] = {}
        self._response_gate_attention: dict[str, Any] = {}
        self._observed_user_history_keys: set[str] = set()

    def _response_gate_continuation_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_response_gate_continuation", None)
        if not isinstance(store, dict):
            self._response_gate_continuation = {}
            store = self._response_gate_continuation
        return store

    def _response_gate_mark_continuation(
        self,
        key: str,
        now: datetime.datetime,
        *,
        reason: str = "",
        turns: int | None = None,
        seconds: int | None = None,
    ) -> None:
        if not key:
            return
        try:
            turn_count = int(turns if turns is not None else self._RESPONSE_GATE_CONTINUATION_TURNS)
        except (TypeError, ValueError):
            turn_count = self._RESPONSE_GATE_CONTINUATION_TURNS
        turn_count = max(1, min(turn_count, 3))
        try:
            window_seconds = int(seconds if seconds is not None else self._RESPONSE_GATE_CONTINUATION_SECONDS)
        except (TypeError, ValueError):
            window_seconds = self._RESPONSE_GATE_CONTINUATION_SECONDS
        window_seconds = max(60, min(window_seconds, 15 * 60))
        store = self._response_gate_continuation_store()
        current = store.get(key)
        if isinstance(current, dict):
            until = current.get("until")
            current_turns = int(current.get("turns") or 0)
            if isinstance(until, datetime.datetime) and now < until and current_turns > 0:
                return
        store[key] = {
            "until": now + datetime.timedelta(seconds=window_seconds),
            "turns": turn_count,
            "reason": reason,
        }
        no_reply_count = getattr(self, "_response_gate_no_reply_count", None)
        if not isinstance(no_reply_count, dict):
            self._response_gate_no_reply_count = {}
            no_reply_count = self._response_gate_no_reply_count
        backoff_until = getattr(self, "_response_gate_backoff_until", None)
        if not isinstance(backoff_until, dict):
            self._response_gate_backoff_until = {}
            backoff_until = self._response_gate_backoff_until
        no_reply_count[key] = 0
        backoff_until.pop(key, None)

    def _response_gate_continuation_reason(self, key: str, now: datetime.datetime) -> str:
        item = self._response_gate_continuation_store().get(key)
        if not isinstance(item, dict):
            return ""
        until = item.get("until")
        turns = int(item.get("turns") or 0)
        if not isinstance(until, datetime.datetime) or now >= until or turns <= 0:
            self._response_gate_continuation_store().pop(key, None)
            return ""
        item["turns"] = turns - 1
        if item["turns"] <= 0:
            self._response_gate_continuation_store().pop(key, None)
        reason = str(item.get("reason") or "").strip()
        return reason or "闲时回复刚被用户接住，先顺势继续聊一会儿"

    async def _response_gate_current_state(self, now: datetime.datetime) -> Any | None:
        archive = getattr(self, "archive", None)
        if archive is None:
            return None
        try:
            today = now.strftime("%Y-%m-%d")
            target_date = resolve_business_now(getattr(self.config, "schedule_time", "07:00"), now).strftime("%Y-%m-%d")
            day = await archive.get_day(target_date)
            if day is None and target_date != today:
                day = await archive.get_day(today)
            return getattr(day, "state", None) if day is not None else None
        except Exception:
            return None

    def _response_gate_record_seen(self, key: str, now: datetime.datetime) -> int:
        previous_seen_at = self._response_gate_last_seen_at.get(key)
        if not isinstance(previous_seen_at, datetime.datetime) or (now - previous_seen_at).total_seconds() > 180:
            pending_count = 1
            self._response_gate_first_seen_at[key] = now
        else:
            pending_count = self._response_gate_pending_count.get(key, 0) + 1
        self._response_gate_last_seen_at[key] = now
        self._response_gate_pending_count[key] = pending_count
        return pending_count

    def _response_gate_record_reply(self, key: str, now: datetime.datetime) -> None:
        self._response_gate_last_reply_at[key] = now
        reply_times = self._response_gate_reply_times.setdefault(key, [])
        reply_times.append(now)
        self._response_gate_reply_times[key] = [
            item for item in reply_times[-8:] if (now - item).total_seconds() <= 10 * 60
        ]
        self._response_gate_pending_count[key] = 0
        self._response_gate_first_seen_at.pop(key, None)
        self._response_gate_no_reply_count[key] = 0
        self._response_gate_backoff_until.pop(key, None)
        note_attention = getattr(self, "_response_gate_note_attention", None)
        if callable(note_attention):
            note_attention(key, now, replied=True)

    def _response_gate_record_no_reply(self, key: str, now: datetime.datetime) -> None:
        count = self._response_gate_no_reply_count.get(key, 0) + 1
        self._response_gate_no_reply_count[key] = count
        note_attention = getattr(self, "_response_gate_note_attention", None)
        if callable(note_attention):
            note_attention(key, now, replied=False)
        config = self.config.response_gate
        base = max(0, int(config.no_reply_backoff_seconds or 0))
        cap = max(0, int(config.no_reply_backoff_cap_seconds or 0))
        start_count = max(1, int(config.no_reply_backoff_start_count or 1))
        if base <= 0 or cap <= 0 or count < start_count:
            return
        seconds = min(cap, base * (2 ** max(0, count - start_count)))
        self._response_gate_backoff_until[key] = now + datetime.timedelta(seconds=seconds)

    def _response_gate_in_backoff(self, key: str, now: datetime.datetime, pending_count: int) -> bool:
        config = self.config.response_gate
        bypass = max(0, int(config.bypass_pending_count or 0))
        if bypass and pending_count >= bypass:
            self._response_gate_backoff_until.pop(key, None)
            return False
        until = self._response_gate_backoff_until.get(key)
        if not isinstance(until, datetime.datetime):
            return False
        if now >= until:
            self._response_gate_backoff_until.pop(key, None)
            return False
        return True
