from __future__ import annotations

import datetime
import random
from typing import Any

from ...clock import now as life_now


class VoiceSwitchGateMixin:
    _VOICE_SWITCH_CADENCE_TTL_SECONDS = 6 * 60 * 60
    _VOICE_SWITCH_COOLDOWN_SECONDS = 10 * 60
    _VOICE_SWITCH_CHAIN_WINDOW_SECONDS = 5 * 60
    _VOICE_SWITCH_MAX_CONSECUTIVE_VOICE = 3
    _VOICE_SWITCH_CHAIN_LIMIT_CHOICES = (1, 2, 3)
    _VOICE_SWITCH_TEXT_TURNS_AFTER_VOICE = 2
    _VOICE_SWITCH_LONG_TEXT_LIMIT = 140
    _VOICE_SWITCH_LONG_LINE_LIMIT = 80
    _VOICE_SWITCH_MAX_SHORT_LINES = 4
    _VOICE_SWITCH_MIN_CONFIDENCE = 0.55

    def _voice_switch_scope_key(self, event: Any) -> str:
        return self._event_session_id(event)

    def _voice_switch_round_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_voice_switch_rounds", None)
        if not isinstance(store, dict):
            self._voice_switch_rounds = {}
            store = self._voice_switch_rounds
        return store

    def _prune_voice_switch_rounds(self, now: datetime.datetime | None = None) -> None:
        now = now or life_now()
        store = self._voice_switch_round_store()
        for key, item in list(store.items()):
            created_at = item.get("created_at") if isinstance(item, dict) else None
            if not isinstance(created_at, datetime.datetime):
                store.pop(key, None)
                continue
            try:
                expired = (now - created_at).total_seconds() > 300
            except TypeError:
                expired = True
            if expired:
                store.pop(key, None)

    def _voice_switch_cadence_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_voice_switch_cadence", None)
        if not isinstance(store, dict):
            self._voice_switch_cadence = {}
            store = self._voice_switch_cadence
        return store

    def _voice_switch_next_chain_limit(self) -> int:
        return random.choice(self._VOICE_SWITCH_CHAIN_LIMIT_CHOICES)

    def _voice_switch_chain_limit(self, item: dict[str, Any]) -> int:
        try:
            limit = int(item.get("voice_chain_limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            limit = self._VOICE_SWITCH_MAX_CONSECUTIVE_VOICE
        return max(1, min(limit, self._VOICE_SWITCH_MAX_CONSECUTIVE_VOICE))

    def _prune_voice_switch_cadence(self, now: datetime.datetime | None = None) -> None:
        now = now or life_now()
        store = self._voice_switch_cadence_store()
        for key, item in list(store.items()):
            if not isinstance(item, dict):
                store.pop(key, None)
                continue
            last_seen = item.get("last_seen")
            if not isinstance(last_seen, datetime.datetime):
                store.pop(key, None)
                continue
            try:
                expired = (now - last_seen).total_seconds() > self._VOICE_SWITCH_CADENCE_TTL_SECONDS
            except TypeError:
                expired = True
            if expired:
                store.pop(key, None)

    def _event_has_voice_input(self, event: Any) -> bool:
        for item in self._event_message_items(event):
            if isinstance(item, dict):
                type_text = str(item.get("type") or item.get("message_type") or "").lower()
            else:
                type_text = " ".join(
                    str(value or "").lower()
                    for value in (
                        item.__class__.__name__,
                        getattr(item, "type", ""),
                        getattr(item, "message_type", ""),
                    )
                )
            if any(token in type_text for token in ("record", "voice", "audio")):
                return True
        return False

    def _voice_switch_probability(self, event: Any) -> float:
        voice_config = getattr(self.config, "voice_generation", None)
        try:
            probability = float(getattr(voice_config, "smart_switch_probability", 35.0))
        except (TypeError, ValueError):
            probability = 35.0
        probability = max(0.0, min(probability, 100.0))
        if self._event_has_voice_input(event):
            probability = max(probability, 70.0)
        return probability

    def _voice_switch_cadence_snapshot(
        self,
        event: Any,
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        now = now or life_now()
        self._prune_voice_switch_cadence(now)
        scope = self._voice_switch_scope_key(event)
        item = self._voice_switch_cadence_store().get(scope, {}) if scope else {}
        last_voice_at = item.get("last_voice_at") if isinstance(item, dict) else None
        seconds_since_voice = None
        if isinstance(last_voice_at, datetime.datetime):
            try:
                seconds_since_voice = max(0, int((now - last_voice_at).total_seconds()))
            except TypeError:
                seconds_since_voice = None
        text_after_voice = int(item.get("text_after_voice") or 0) if isinstance(item, dict) else 0
        consecutive_voice = int(item.get("consecutive_voice") or 0) if isinstance(item, dict) else 0
        chain_limit = self._voice_switch_chain_limit(item) if isinstance(item, dict) else self._VOICE_SWITCH_MAX_CONSECUTIVE_VOICE
        voice_chain_open = (
            seconds_since_voice is not None
            and seconds_since_voice <= self._VOICE_SWITCH_CHAIN_WINDOW_SECONDS
            and text_after_voice == 0
            and 0 < consecutive_voice < chain_limit
        )
        voice_chain_exhausted = (
            seconds_since_voice is not None
            and seconds_since_voice < self._VOICE_SWITCH_COOLDOWN_SECONDS
            and consecutive_voice >= chain_limit
        )
        cooldown_active = (
            seconds_since_voice is not None
            and seconds_since_voice < self._VOICE_SWITCH_COOLDOWN_SECONDS
            and text_after_voice < self._VOICE_SWITCH_TEXT_TURNS_AFTER_VOICE
            and not voice_chain_open
        )
        return {
            "last_channel": str(item.get("last_channel") or "") if isinstance(item, dict) else "",
            "seconds_since_voice": seconds_since_voice,
            "text_after_voice": text_after_voice,
            "consecutive_voice": consecutive_voice,
            "voice_chain_open": voice_chain_open,
            "voice_chain_exhausted": voice_chain_exhausted,
            "max_consecutive_voice": chain_limit,
            "cooldown_active": cooldown_active,
            "probability": self._voice_switch_probability(event),
            "user_sent_voice": self._event_has_voice_input(event),
        }

    def _mark_voice_switch_channel(self, event: Any, channel: str, now: datetime.datetime | None = None) -> None:
        scope = self._voice_switch_scope_key(event)
        if not scope:
            return
        now = now or life_now()
        self._prune_voice_switch_cadence(now)
        store = self._voice_switch_cadence_store()
        item = store.setdefault(scope, {})
        normalized = "语音" if str(channel or "").lower() in {"voice", "语音"} else "文字"
        item["last_seen"] = now
        if normalized == "语音":
            if item.get("last_channel") == "语音":
                item["consecutive_voice"] = int(item.get("consecutive_voice") or 0) + 1
                if not isinstance(item.get("voice_chain_limit"), int):
                    item["voice_chain_limit"] = self._voice_switch_next_chain_limit()
            else:
                item["consecutive_voice"] = 1
                item["voice_chain_limit"] = self._voice_switch_next_chain_limit()
            item["text_after_voice"] = 0
            item["last_voice_at"] = now
        else:
            item["consecutive_voice"] = 0
            item.pop("voice_chain_limit", None)
            if isinstance(item.get("last_voice_at"), datetime.datetime):
                item["text_after_voice"] = int(item.get("text_after_voice") or 0) + 1
            item["last_text_at"] = now
        item["last_channel"] = normalized

    def _voice_switch_auto_gate(
        self,
        event: Any,
        reply_text: str,
        confidence: float,
    ) -> tuple[bool, str]:
        text = str(reply_text or "").strip()
        if not text:
            return False, "我还没有整理出要说的话，先不发语音。"
        if self._voice_switch_text_too_dense_for_voice(text):
            return False, "我这轮内容偏长，打字留下来更清楚。"
        if confidence < self._VOICE_SWITCH_MIN_CONFIDENCE:
            return False, "我对这轮是否适合语音还不够确定，先用文字更稳。"
        cadence = self._voice_switch_cadence_snapshot(event)
        if cadence["cooldown_active"]:
            if cadence.get("voice_chain_exhausted"):
                return False, "我已经连续发了几条语音，这轮先打字缓一下会更自然。"
            return False, "我刚刚才发过语音，这轮不在自然连发的节奏里，先用文字更合适。"
        probability = float(cadence["probability"])
        if probability <= 0:
            return False, "我现在把普通聊天的自动语音收住了，这轮用文字更合适。"
        if cadence.get("voice_chain_open"):
            probability = max(probability, 70.0)
        if probability < 100 and random.random() * 100 >= probability:
            return False, "我这轮虽然可以说出来，但按当前聊天节奏更适合把语音留到更需要的片刻。"
        return True, ""

    def _voice_switch_text_too_dense_for_voice(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if len(normalized) > self._VOICE_SWITCH_LONG_TEXT_LIMIT:
            return True
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if len(lines) > self._VOICE_SWITCH_MAX_SHORT_LINES:
            return True
        return any(len(line) > self._VOICE_SWITCH_LONG_LINE_LIMIT for line in lines)

    def mark_voice_switch_available(self, event: Any) -> bool:
        scope = self._voice_switch_scope_key(event)
        if not scope:
            return False
        self._prune_voice_switch_rounds()
        self._voice_switch_round_store()[scope] = {
            "created_at": life_now(),
            "used_voice": False,
        }
        return True

    def mark_voice_switch_used(self, event: Any) -> bool:
        scope = self._voice_switch_scope_key(event)
        if not scope:
            return False
        self._prune_voice_switch_rounds()
        store = self._voice_switch_round_store()
        item = store.setdefault(scope, {"created_at": life_now()})
        item["used_voice"] = True
        item["used_voice_tool"] = True
        return True
