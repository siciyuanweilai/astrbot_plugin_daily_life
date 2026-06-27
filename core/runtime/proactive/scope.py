import datetime
from typing import Any

from ...clock import now as life_now
from ...life.tools import resolve_business_now
from .procontext import ProactiveSyntheticEvent


class ProactiveScopeMixin:

    async def _get_proactive_provider(self):
        provider_id = self.config.proactive.provider
        return await self.composer._get_provider(provider_id)

    def _proactive_scope_key(self, event: Any) -> str:
        group_id, _ = self._event_group_meta(event)
        session_id = self._event_session_id(event)
        return group_id or session_id or self._safe_event_call(event, "get_sender_id")

    def _proactive_is_self_message(self, event: Any) -> bool:
        sender_id = self._safe_event_call(event, "get_sender_id")
        self_id = self._safe_event_call(event, "get_self_id")
        if sender_id and self_id and sender_id == self_id:
            return True
        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        return isinstance(raw_message, dict) and bool(raw_message.get("_proactive_send"))

    def _proactive_allowed_for_event(self, event: Any) -> bool:
        config = self.config.proactive
        if not config.enabled:
            return False
        if event.is_stopped() or bool(getattr(event, "_has_send_oper", False)):
            return False
        if self._event_has_command_handler(event):
            return False
        if self._proactive_is_self_message(event):
            return False
        message = str(getattr(event, "message_str", "") or "").strip()
        if not message or len(message) < config.min_message_length:
            return False
        is_group = self._event_is_group_message(event)
        if is_group and not config.group_enabled:
            return False
        if not is_group and not config.private_enabled:
            return False
        if is_group and self._event_is_directed(event):
            return False
        return True

    def _proactive_idle_seconds(self, event_or_candidate: Any) -> int:
        if isinstance(event_or_candidate, dict):
            is_group = bool(event_or_candidate.get("is_group"))
        else:
            is_group = self._event_is_group_message(event_or_candidate)
        minutes = (
            self.config.proactive.idle_minutes
            if is_group
            else self.config.proactive.private_idle_minutes
        )
        return max(1, int(minutes or 30)) * 60

    def _proactive_cooldown_remaining(self, event: Any, now: datetime.datetime) -> int:
        key = self._proactive_scope_key(event)
        if not key:
            return 0
        last_reply_at = self._proactive_last_reply_at.get(key)
        if not isinstance(last_reply_at, datetime.datetime):
            return 0
        if self._event_is_group_message(event):
            cooldown_minutes = self.config.proactive.cooldown_minutes
        else:
            cooldown_minutes = self.config.proactive.private_cooldown_minutes
        cooldown = max(1, int(cooldown_minutes or 20)) * 60
        remaining = cooldown - int((now - last_reply_at).total_seconds())
        return max(0, remaining)

    def _mark_proactive_reply_sent(self, event: Any, now: datetime.datetime) -> None:
        key = self._proactive_scope_key(event)
        if not key:
            return
        self._proactive_last_reply_at[key] = now
        self._reset_proactive_air_state(key)

    def note_proactive_activity(self, event: Any, now: datetime.datetime | None = None) -> None:
        key = self._proactive_scope_key(event)
        now = now or life_now()
        if key and key in self._proactive_feedback_watches():
            turns, seconds, reason = self._proactive_continuation_window(event, now, key)
            if turns > 0:
                marker = getattr(self, "_response_gate_mark_continuation", None)
                if callable(marker):
                    marker(
                        self._response_gate_scope_key(event),
                        now,
                        reason=reason,
                        turns=turns,
                        seconds=seconds,
                    )
                    watch = self._proactive_feedback_watches().get(key)
                    if isinstance(watch, dict):
                        watch["continuation_marked"] = True
            self._schedule_background_task(
                self._observe_proactive_reply_effect(event, now),
                label="闲时回复效果观察",
                key=f"proactive_feedback:{key}:{self._event_message_id(event) or now.isoformat()}",
            )
        if not self._proactive_allowed_for_event(event):
            return
        target_scope = self._event_session_id(event)
        if not key or not target_scope:
            return
        group_id, group_name = self._event_group_meta(event)
        recent_messages = self._recent_messages_for_candidate(self._proactive_idle_candidates.get(key))
        recent_messages.append(
            {
                "message_id": self._event_message_id(event),
                "sender_id": self._safe_event_call(event, "get_sender_id"),
                "sender_name": self._safe_event_call(event, "get_sender_name"),
                "content": str(getattr(event, "message_str", "") or "").strip(),
                "seen_at": now,
                "structured": self.format_structured_message_context(event, limit=4),
            }
        )
        recent_messages = recent_messages[-self._AIR_MAX_RECENT_MESSAGES:]
        self._proactive_idle_candidates[key] = {
            "key": key,
            "target_scope": target_scope,
            "message": str(getattr(event, "message_str", "") or "").strip(),
            "message_id": self._event_message_id(event),
            "sender_id": self._safe_event_call(event, "get_sender_id"),
            "sender_name": self._safe_event_call(event, "get_sender_name"),
            "platform_name": self._safe_event_call(event, "get_platform_name"),
            "group_id": group_id,
            "group_name": group_name,
            "is_group": self._event_is_group_message(event),
            "last_activity_at": now,
            "last_bot_reply_at": None,
            "recent_messages": recent_messages,
            "pending_count": len(recent_messages),
        }

    def note_proactive_bot_reply(self, event: Any, now: datetime.datetime | None = None) -> None:
        key = self._proactive_scope_key(event)
        if not key:
            return
        candidate = self._proactive_idle_candidates.get(key)
        if isinstance(candidate, dict):
            if not bool(candidate.get("is_group")):
                self._proactive_idle_candidates.pop(key, None)
                return
            candidate["last_bot_reply_at"] = now or life_now()

    def _proactive_candidate_event(self, candidate: dict[str, Any]) -> Any:
        return ProactiveSyntheticEvent(
            message=str(candidate.get("message") or ""),
            target_scope=str(candidate.get("target_scope") or ""),
            message_id=str(candidate.get("message_id") or ""),
            sender_id=str(candidate.get("sender_id") or ""),
            sender_name=str(candidate.get("sender_name") or ""),
            platform_name=str(candidate.get("platform_name") or ""),
            group_id=str(candidate.get("group_id") or ""),
            group_name=str(candidate.get("group_name") or ""),
            last_activity_at=candidate.get("last_activity_at"),
            last_bot_reply_at=candidate.get("last_bot_reply_at"),
            recent_messages=self._recent_messages_for_candidate(candidate),
            pending_count=self._candidate_pending_count(candidate),
        )

    async def _proactive_current_day(
        self,
        now: datetime.datetime,
    ) -> tuple[str, bool, Any | None]:
        today_str = now.strftime("%Y-%m-%d")
        target_date_str = resolve_business_now(self.config.schedule_time, now).strftime("%Y-%m-%d")
        using_extended_night = target_date_str != today_str
        day = await self.archive.get_day(target_date_str)
        if not day and using_extended_night:
            yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            day = await self.archive.get_day(yesterday)
            if day:
                target_date_str = yesterday
        return target_date_str, using_extended_night, day
