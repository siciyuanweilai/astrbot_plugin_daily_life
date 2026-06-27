from __future__ import annotations

import datetime
from typing import Any

from ...models import ActionDecisionRecord, GroupEnvironmentRecord, MessageVisibilityRecord


class AwarenessMixin:
    _MEANINGFUL_VISIBILITY_LEVELS = {"focused", "ignored", "seen_but_ignored"}
    _MEANINGFUL_DECISION_ACTIONS = {
        "reply",
        "save_memory",
        "comfort",
        "push_back",
        "join_ritual",
        "eat_melon",
        "need_deep_analysis",
    }

    async def _event_context_meta(self, event: Any, sender_name: str, now: datetime.datetime) -> dict[str, str]:
        group_id, group_name = self._event_group_meta(event)
        if group_id and (not group_name or group_name == group_id):
            resolver = getattr(self, "contact_resolver", None)
            resolve_group_name = getattr(resolver, "resolve_group_name", None)
            if callable(resolve_group_name):
                group_name = await resolve_group_name(
                    group_id,
                    event=event,
                    target_umo=self._event_session_id(event),
                ) or group_name
        platform, user_id = self._event_platform_user(event)
        sender_profile_id = self._event_profile_id(event, sender_name)
        return {
            "session_id": self._event_session_id(event),
            "message_id": self._event_message_id(event),
            "platform": platform,
            "user_id": user_id,
            "sender_profile_id": sender_profile_id,
            "sender_name": sender_name,
            "group_id": group_id,
            "group_name": group_name,
            "date": now.strftime("%Y-%m-%d"),
            "is_group": "true" if self._event_is_group_message(event) else "false",
            "is_directed": "true" if self._event_is_directed(event) else "false",
            "is_quoted": "true" if self._event_has_quote(event) else "false",
            "quote_context": self._event_quote_context(event),
            "structured": self.format_structured_message_context(event, limit=8),
        }

    def _message_visibility_from_payload(
        self,
        visibility: dict,
        meta: dict[str, str],
    ) -> MessageVisibilityRecord:
        return MessageVisibilityRecord(
            session_id=meta["session_id"],
            message_id=meta["message_id"],
            sender_profile_id=meta["sender_profile_id"],
            sender_name=meta["sender_name"],
            group_id=meta["group_id"],
            group_name=meta["group_name"],
            date=meta["date"],
            visibility=self._str_payload(visibility.get("level"), "seen"),
            attention_level=self._score_payload(visibility.get("attention_level")),
            priority=self._str_payload(visibility.get("priority"), "normal"),
            is_directed_at_bot=self._bool_payload(visibility.get("is_directed_at_bot")),
            freshness=self._str_payload(visibility.get("freshness")),
            psychological_freshness=self._score_payload(visibility.get("psychological_freshness")),
            reactivated_from_id=max(self._int_payload(visibility.get("reactivated_from_id")), 0),
            reactivation_hint=self._str_payload(visibility.get("reactivation_hint")),
            reason=self._str_payload(visibility.get("reason")),
        )

    def _action_decision_from_payload(
        self,
        decision: dict,
        meta: dict[str, str],
    ) -> ActionDecisionRecord:
        return ActionDecisionRecord(
            session_id=meta["session_id"],
            message_id=meta["message_id"],
            sender_profile_id=meta["sender_profile_id"],
            sender_name=meta["sender_name"],
            group_id=meta["group_id"],
            group_name=meta["group_name"],
            date=meta["date"],
            action=self._str_payload(decision.get("action"), "skip_memory"),
            reason=self._str_payload(decision.get("reason")),
            confidence=max(self._float_payload(decision.get("confidence")), 0.0),
            scene_type=self._str_payload(decision.get("scene_type")),
            topic_owner=self._str_payload(decision.get("topic_owner")),
            understanding=self._str_payload(decision.get("understanding")),
            deep_analysis=self._bool_payload(decision.get("deep_analysis")),
            inner_monologue=self._str_payload(decision.get("inner_monologue")),
            reply_strategy=self._str_payload(decision.get("reply_strategy")),
        )

    def _group_environment_from_payload(
        self,
        environment: dict,
        meta: dict[str, str],
    ) -> GroupEnvironmentRecord:
        return GroupEnvironmentRecord(
            session_id=meta["session_id"],
            group_id=meta["group_id"],
            group_name=meta["group_name"],
            date=meta["date"],
            atmosphere=self._str_payload(environment.get("atmosphere")),
            topic=self._str_payload(environment.get("topic")),
            topic_owner=self._str_payload(environment.get("topic_owner")),
            active_users=max(self._int_payload(environment.get("active_users")), 0),
            is_multithread=self._bool_payload(environment.get("is_multithread")),
            is_spam=self._bool_payload(environment.get("is_spam")),
            is_repetition=self._bool_payload(environment.get("is_repetition")),
            is_discussing_bot=self._bool_payload(environment.get("is_discussing_bot")),
            suitable_to_join=self._str_payload(environment.get("suitable_to_join")),
            bot_watch_state=self._str_payload(environment.get("bot_watch_state")),
            participation_desire=self._score_payload(environment.get("participation_desire")),
            complexity_score=self._score_payload(environment.get("complexity_score")),
            understanding_confidence=self._score_payload(environment.get("understanding_confidence")),
            deep_analysis_needed=self._bool_payload(environment.get("deep_analysis_needed")),
            summary=self._str_payload(environment.get("summary")),
        )

    def _is_effective_message_visibility(self, item: MessageVisibilityRecord) -> bool:
        return bool(
            item.reason
            or item.reactivation_hint
            or item.reactivated_from_id > 0
            or item.freshness == "reactivated"
            or item.priority == "high"
            or item.is_directed_at_bot
            or item.attention_level >= 50
            or item.psychological_freshness >= 50
            or item.visibility in self._MEANINGFUL_VISIBILITY_LEVELS
        )

    def _is_effective_action_decision(self, item: ActionDecisionRecord) -> bool:
        return bool(
            item.reason
            or item.inner_monologue
            or item.reply_strategy
            or item.deep_analysis
            or item.action in self._MEANINGFUL_DECISION_ACTIONS
        )

    def _is_effective_group_environment(self, item: GroupEnvironmentRecord) -> bool:
        return bool(
            item.topic
            or item.summary
            or item.deep_analysis_needed
            or item.is_multithread
            or item.is_spam
            or item.is_repetition
            or item.is_discussing_bot
            or item.active_users > 1
            or item.participation_desire > 0
            or item.complexity_score > 0
            or item.understanding_confidence > 0
        )

    async def _save_memory_awareness_records(self, payload: dict, meta: dict[str, str]) -> None:
        visibility = payload.get("visibility") if isinstance(payload.get("visibility"), dict) else {}
        visibility_record = self._message_visibility_from_payload(visibility, meta)
        if self._is_effective_message_visibility(visibility_record):
            await self.archive.save_message_visibility(visibility_record)

        environment = payload.get("group_environment") if isinstance(payload.get("group_environment"), dict) else {}
        if meta.get("is_group") == "true" and meta["group_id"]:
            environment_record = self._group_environment_from_payload(environment, meta)
            if self._is_effective_group_environment(environment_record):
                await self.archive.save_group_environment(environment_record)

        decision = payload.get("action_decision") if isinstance(payload.get("action_decision"), dict) else {}
        decision_record = self._action_decision_from_payload(decision, meta)
        if self._is_effective_action_decision(decision_record):
            await self.archive.save_action_decision(decision_record)

    async def _append_memory_decision_log(
        self,
        payload: dict,
        meta: dict[str, str],
        now: datetime.datetime,
    ) -> None:
        day = await self.archive.get_day(meta["date"])
        if not day:
            return
        visibility = payload.get("visibility") if isinstance(payload.get("visibility"), dict) else {}
        decision = payload.get("action_decision") if isinstance(payload.get("action_decision"), dict) else {}
        environment = payload.get("group_environment") if isinstance(payload.get("group_environment"), dict) else {}
        visibility_record = self._message_visibility_from_payload(visibility, meta)
        decision_record = self._action_decision_from_payload(decision, meta)
        environment_record = self._group_environment_from_payload(environment, meta)
        keep_visibility = self._is_effective_message_visibility(visibility_record)
        keep_decision = self._is_effective_action_decision(decision_record)
        keep_environment = meta.get("is_group") == "true" and self._is_effective_group_environment(environment_record)

        parts = [
            f"{now.strftime('%H:%M')} 群聊观察" if meta.get("is_group") == "true" else f"{now.strftime('%H:%M')} 聊天观察",
            f"{meta.get('sender_name') or meta.get('sender_profile_id') or '未知'}",
        ]
        level = self._compact_log(visibility_record.visibility) if keep_visibility else ""
        action = self._compact_log(decision_record.action) if keep_decision else ""
        reason = self._compact_log(decision_record.reason or visibility_record.reason, 100)
        monologue = self._compact_log(decision_record.inner_monologue, 100) if keep_decision else ""
        topic = self._compact_log(environment_record.topic or environment_record.summary, 80) if keep_environment else ""
        if not (level or action or topic or reason or monologue):
            return
        if level:
            parts.append(f"留意={level}")
        if action:
            parts.append(f"裁定={action}")
        if topic:
            parts.append(f"话题={topic}")
        if reason:
            parts.append(f"原因={reason}")
        if monologue:
            parts.append(f"旁白={monologue}")
        log = "；".join(parts)
        logs = list(day.state_log)
        if log and (not logs or logs[-1] != log):
            logs.append(log)
            day.state_log = logs[-10:]
            await self.archive.save_day(day)
