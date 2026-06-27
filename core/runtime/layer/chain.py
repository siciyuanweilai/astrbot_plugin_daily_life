from typing import Any

from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest

from ...life.surroundings import format_hidden_group_awareness, format_hidden_world_context, select_relevant_world


class LayerChainMixin:
    def _memos_injection_enabled(self) -> bool:
        service_getter = getattr(self, "_memos_service", None)
        if not callable(service_getter):
            return False
        service = service_getter()
        return bool(service and getattr(service, "enabled", False))

    async def _build_injection_memos_context(self, event: Any, message: str = "") -> str:
        if event is None or not self._memos_injection_enabled():
            return ""
        sender_name = await self.contact_resolver.resolve_event_sender(event)
        return await self.build_memos_hidden_context(event, message, sender_name=sender_name)

    def _event_message_text(self, event: Any) -> str:
        return str(getattr(event, "message_str", "") or "") if event is not None else ""

    def _schedule_chat_capture_context(self, event: Any, now: Any) -> None:
        if event is None:
            return
        session_id = self._event_session_id(event)
        message_id = self._event_message_id(event)
        task_key = f"chat_capture:{session_id}:{message_id or hash(self._event_message_text(event))}"
        self._schedule_background_task(
            self._capture_chat_context_background(event, now),
            label="聊天记忆提炼",
            key=task_key,
        )

    def _schedule_chat_state_refresh(self, target_date_str: str, now: Any, event: Any = None) -> None:
        if not self.config.state.enabled:
            return
        self._schedule_background_task(
            self._refresh_state_for_chat_background(target_date_str, now, source_event=event),
            label="聊天状态刷新",
            key=f"chat_state:{target_date_str}",
        )

    def _select_world_context(self, snapshot: dict[str, Any], data: Any, event_message: str) -> str:
        meta = data.meta or {}
        selected = select_relevant_world(
            list(snapshot.get("relationships") or []),
            list(snapshot.get("places") or []),
            list(snapshot.get("events") or []),
            list(snapshot.get("summaries") or []),
            hints=[
                event_message,
                data.memo,
                meta.get("theme", ""),
                meta.get("mood", ""),
                meta.get("schedule_intent", ""),
                data.state.summary if data.state else "",
            ],
            relationship_limit=5,
            place_limit=8,
            event_limit=8,
            summary_limit=self.config.memory.max_injection_items,
        )
        return format_hidden_world_context(
            selected["relationships"],
            selected["places"],
            selected["events"],
            selected["summaries"],
        )

    def _scope_snapshot_items(self, snapshot: dict[str, Any], event: Any = None) -> tuple[list[Any], list[Any], list[Any]]:
        environments = list(snapshot.get("environments") or [])
        decisions = list(snapshot.get("decisions") or [])
        visibility = list(snapshot.get("visibility") or [])
        if event is None:
            return environments, decisions, visibility

        session_id = self._event_session_id(event)
        group_id, _ = self._event_group_meta(event)

        def in_current_scope(item: Any) -> bool:
            item_session = str(getattr(item, "session_id", "") or "").strip()
            item_group = str(getattr(item, "group_id", "") or "").strip()
            return bool((group_id and item_group == group_id) or (session_id and item_session == session_id))

        scoped_environments = [item for item in environments if in_current_scope(item)]
        scoped_decisions = [item for item in decisions if in_current_scope(item)]
        scoped_visibility = [item for item in visibility if in_current_scope(item)]
        return (
            scoped_environments or environments[:1],
            scoped_decisions or decisions[:1],
            scoped_visibility or visibility[:1],
        )

    def _format_snapshot_experience_context(self, snapshot: dict[str, Any]) -> str:
        return self._format_hidden_experience_context(
            episodes=list(snapshot.get("episodes") or []),
            focus_targets=list(snapshot.get("focus_targets") or []),
            feedback=list(snapshot.get("feedback") or []),
            reply_effects=list(snapshot.get("reply_effects") or []),
            memory_corrections=list(snapshot.get("memory_corrections") or []),
            expression_profiles=list(snapshot.get("expression_profiles") or []),
            expression_reviews=list(snapshot.get("expression_reviews") or []),
            behavior_patterns=list(snapshot.get("behavior_patterns") or []),
            behavior_scenes=list(snapshot.get("behavior_scenes") or []),
            mid_summaries=list(snapshot.get("mid_summaries") or []),
            temporary_expression_states=list(snapshot.get("temporary_expression_states") or []),
            focus_slots=list(snapshot.get("focus_slots") or []),
            expression_intents=list(snapshot.get("expression_intents") or []),
            terms=list(snapshot.get("terms") or []),
            boundaries=list(snapshot.get("boundaries") or []),
        )

    async def _build_available_life_context(
        self,
        data: Any,
        now: Any,
        using_extended_night: bool,
        event: Any = None,
    ) -> str:
        snapshot = await self._gather_life_context_snapshot(event)
        event_message = self._event_message_text(event)
        environments, decisions, visibility = self._scope_snapshot_items(snapshot, event)
        return self.build_hidden_life_context(
            data,
            now,
            using_extended_night,
            world_context=self._select_world_context(snapshot, data, event_message),
            group_awareness_context=format_hidden_group_awareness(environments, decisions, visibility),
            commitments=await self.archive.get_commitments(status="active", limit=8),
            experience_context=self._format_snapshot_experience_context(snapshot),
            memos_context=await self._build_injection_memos_context(event, event_message),
            structured=self.format_structured_message_context(event, limit=8) if event is not None else "",
            expression_event=event,
        )

    async def inject_life_context(self, req: ProviderRequest, event: Any = None) -> None:
        if self.is_internal_llm_session(req):
            return

        now = self._life_injection_now()
        today_str = now.strftime("%Y-%m-%d")
        target_date_str, using_extended_night = await self.resolve_injection_target(now)
        data = await self.ensure_injection_day_data(target_date_str, now)
        data = await self.maybe_update_injection_outfit(today_str, data, using_extended_night)

        self._schedule_chat_capture_context(event, now)
        if not data:
            memos_context = await self._build_injection_memos_context(event, self._event_message_text(event))
            missing_context = self.build_missing_life_context(
                now,
                target_date_str,
                using_extended_night,
                event=event,
                memos_context=memos_context,
            )
            if self._voice_expression_channel_enabled(event):
                self.mark_voice_switch_available(event)
            req.system_prompt = (req.system_prompt or "") + missing_context
            logger.debug("[上下文注入] 当前暂无日常生活记录，已注入防编造约束")
            return

        self._schedule_chat_state_refresh(target_date_str, now, event)
        hidden_context = await self._build_available_life_context(data, now, using_extended_night, event)
        if self._voice_expression_channel_enabled(event):
            self.mark_voice_switch_available(event)
        req.system_prompt = (req.system_prompt or "") + hidden_context
        logger.debug("[上下文注入] 已注入日常生活背景上下文")
