from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from astrbot.api import logger
from astrbot.core.agent.message import TextPart
from astrbot.core.provider.entities import ProviderRequest

from ...life.appearance import format_current_appearance_context
from ...life.tools import timeline_item_datetime
from ...epistemic import knowledge_label
from ...life.surroundings import (
    format_hidden_group_awareness,
    format_hidden_world_context,
)
from ..expression import remember_expression_context
from ..expressionlearning import bounded_profiles, expose_profiles, general_profiles
from ..markers import LOG_PREFIX


class LayerChainComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _CHAT_MEMORY_RANK_BUDGET_SECONDS: Any
    _EXPERIENCE_RELEVANCE_FIELDS: Any
    _append_video_input_anchor: Any
    _apply_life_request_context: Any
    _build_person_fact_injection_context: Any
    _collect_emoji_context_background: Any
    _emoji_asset_source_kind: Any
    _emoji_auto_collect_enabled: Any
    _event_has_command_handler: Any
    _event_message_items: Any
    _event_group_meta: Any
    _event_message_id: Any
    _event_message_text: Any
    _event_session_id: Any
    _format_cognition_context: Any
    _format_hidden_experience_context: Any
    _gather_life_context_snapshot: Any
    _life_injection_now: Any
    _memos_service: Any
    _refresh_state_for_chat_background: Any
    _schedule_background_task: Any
    _semantic_snapshot_id: Any
    _voice_expression_channel_enabled: Any
    build_chat_style_injection_context: Any
    build_conversation_timing_context: Any
    build_heuristic_memory_context: Any
    build_hidden_life_context: Any
    build_memos_hidden_context: Any
    build_missing_life_context: Any
    composer: Any
    config: Any
    contact_resolver: Any
    domains: Any
    ensure_injection_day_data: Any
    event_was_recalled: Any
    format_recent_sight_context: Any
    format_structured_message_context: Any
    friend_reference_injection_context: Any
    get_persona_text: Any
    is_internal_llm_session: Any
    mark_voice_switch_available: Any
    maybe_update_injection_outfit: Any
    rank_semantic_groups: Any
    resolve_injection_target: Any
    resolve_interaction_context: Any


class LayerChainComponent:
    def __init__(self, context: LayerChainComponentContext) -> None:
        self.context = context

    _CHAT_MEMORY_RANK_BUDGET_SECONDS = 1.5
    _EXPERIENCE_RELEVANCE_FIELDS = (
        "title",
        "summary",
        "impact",
        "label",
        "reason",
        "evidence",
        "scene",
        "action",
        "feedback",
        "reply_text",
        "tone",
        "pattern",
        "suggested_action",
        "outcome_hint",
        "topic",
        "term",
        "meaning",
        "content",
    )

    async def _build_person_fact_injection_context(
        self,
        event: Any = None,
        *,
        relationships: list[Any] | None = None,
    ) -> str:
        composer = getattr(self.context, "composer", None)
        builder = getattr(composer, "_build_person_fact_context", None)
        if not callable(builder):
            return ""
        try:
            scope = self.context._event_session_id(event) if event is not None else ""
            persona = await self.context.get_persona_text(scope)
            context = await builder(
                persona=persona,
                relationships=relationships,
            )
            text = context.format_for_generation()
            return f"\n\n[HiddenPersonFacts]\n{text}" if text else ""
        except Exception as exc:
            logger.debug(f"[上下文注入] 读取人物事实边界失败：{exc}")
            return ""

    def _experience_item_text(self, item: Any) -> str:
        parts: list[str] = []
        for field in self.context._EXPERIENCE_RELEVANCE_FIELDS:
            value = getattr(item, field, "")
            if isinstance(value, (list, tuple, set)):
                parts.extend(str(entry or "") for entry in value)
            else:
                parts.append(str(value or ""))
        for field in ("cues", "habits", "examples", "related_people"):
            parts.extend(
                str(entry or "") for entry in list(getattr(item, field, []) or [])
            )
        return " ".join(parts)

    @staticmethod
    def _semantic_snapshot_id(kind: str, item: Any, index: int) -> str:
        for field in ("id", "profile_id", "session_id", "message_id", "date"):
            value = str(getattr(item, field, "") or "").strip()
            if value:
                return f"{kind}:{value}"
        return f"{kind}:position:{index}"

    def _semantic_snapshot_group(
        self, kind: str, items: list[Any]
    ) -> list[tuple[str, str, Any]]:
        return [
            (
                self.context._semantic_snapshot_id(kind, item, index),
                self._experience_item_text(item),
                item,
            )
            for index, item in enumerate(items)
        ]

    def _request_has_visual_input(self, req: ProviderRequest) -> bool:
        if list(getattr(req, "image_urls", []) or []):
            return True
        for part in list(getattr(req, "extra_user_content_parts", []) or []):
            part_type = str(getattr(part, "type", "") or "").strip().lower()
            if part_type == "image_url":
                return True
            if (
                isinstance(part, dict)
                and str(part.get("type") or "").strip().lower() == "image_url"
            ):
                return True
            text = str(
                getattr(part, "text", "")
                or (part.get("text", "") if isinstance(part, dict) else "")
            ).strip()
            if "[Image Attachment" in text:
                return True
        return False

    def _append_visual_input_anchor(self, req: ProviderRequest) -> None:
        if not self._request_has_visual_input(req):
            return
        parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(parts, list):
            parts = []
            req.extra_user_content_parts = parts
        text = (
            "[HiddenVisualInputRule] 本轮消息包含真实图片输入或引用图片。"
            "回答与图片内容、人物、物品、场景、文字或身份判断相关的问题时，"
            "必须以本轮图片和图片说明为准；日常生活背景、当前穿搭和长期记忆只能辅助语气，不能替代图片事实。"
            "如果图片中看不清、无法确认或证据不足，要自然说明不确定。"
        )
        if any(
            str(
                getattr(part, "text", "")
                or (part.get("text", "") if isinstance(part, dict) else "")
            )
            == text
            for part in parts
        ):
            return
        part = TextPart(text=text)
        marker = getattr(part, "mark_as_temp", None)
        if callable(marker):
            part = marker()
        parts.append(part)

    def _memos_injection_enabled(self) -> bool:
        service_getter = getattr(self.context, "_memos_service", None)
        if not callable(service_getter):
            return False
        service = service_getter()
        return bool(service and getattr(service, "enabled", False))

    async def _build_injection_memos_context(
        self, event: Any, message: str = ""
    ) -> str:
        if event is None or not self._memos_injection_enabled():
            return ""
        service = self.context._memos_service()
        timeout = max(
            0.2,
            float(
                getattr(
                    getattr(service, "settings", None),
                    "injection_timeout_seconds", 0.8,
                ) or 0.8
            ),
        )
        try:
            async with asyncio.timeout(timeout):
                sender_name = self.context.contact_resolver.cached_event_sender(event)
                return await self.context.build_memos_hidden_context(
                    event, message, sender_name=sender_name
                )
        except TimeoutError:
            logger.debug(f"{LOG_PREFIX} MemOS 前台资料准备超时，继续本轮聊天")
            return ""

    def _event_message_text(self, event: Any) -> str:
        return str(getattr(event, "message_str", "") or "") if event is not None else ""

    def schedule_emoji_capture_from_event(self, event: Any, now: Any = None) -> bool:
        if (
            event is None
            or not self.context._emoji_auto_collect_enabled()
            or self.context._event_has_command_handler(event)
            or self.context.event_was_recalled(event, log_skip=True)
        ):
            return False
        if not any(
            self.context._emoji_asset_source_kind(item) in {"trusted", "review", "plain_image"}
            for item in self.context._event_message_items(event)
        ):
            return False
        now = now or self.context._life_injection_now()
        session_id = self.context._event_session_id(event)
        message_id = self.context._event_message_id(event)
        task_key = f"emoji_capture:{session_id}:{message_id or hash(self.context._event_message_text(event))}"
        return self.context._schedule_background_task(
            self.context._collect_emoji_context_background(event, now),
            label="表情素材采集",
            key=task_key,
        )

    def _schedule_chat_state_refresh(
        self, target_date_str: str, now: Any, event: Any = None
    ) -> None:
        if not self.context.config.state.enabled:
            return
        if event is not None:
            event._daily_life_pending_chat_state_refresh = (target_date_str, now)
            return
        self.context._schedule_background_task(
            self.context._refresh_state_for_chat_background(
                target_date_str, now, source_event=event
            ),
            label="聊天状态刷新",
            key=f"chat_state:{target_date_str}",
        )

    def schedule_pending_chat_state_refresh(self, event: Any) -> bool:
        pending = getattr(event, "_daily_life_pending_chat_state_refresh", None)
        if not isinstance(pending, tuple) or len(pending) != 2:
            return False
        event._daily_life_pending_chat_state_refresh = None
        target_date_str, now = pending
        return self.context._schedule_background_task(
            self.context._refresh_state_for_chat_background(
                str(target_date_str), now, source_event=event
            ),
            label="聊天状态刷新",
            key=f"chat_state:{target_date_str}",
        )

    async def _select_life_memory_contexts(
        self, snapshot: dict[str, Any], data: Any, event_message: str
    ) -> tuple[str, str]:
        meta = data.meta or {}
        boundary_getter = getattr(
            getattr(self.context, "domains", None),
            "residence_boundary_date",
            None,
        )
        residence_boundary = (
            str(await boundary_getter() or "").strip()[:10]
            if callable(boundary_getter)
            else ""
        )

        def record_date(item: Any, field: str) -> str:
            if isinstance(item, dict):
                return str(item.get(field) or "").strip()[:10]
            return str(getattr(item, field, "") or "").strip()[:10]

        place_items = list(snapshot.get("places") or [])
        event_items = list(snapshot.get("events") or [])
        if residence_boundary:
            place_items = [
                item
                for item in place_items
                if not record_date(item, "last_seen")
                or record_date(item, "last_seen") >= residence_boundary
            ]
            event_items = [
                item
                for item in event_items
                if not record_date(item, "date")
                or record_date(item, "date") >= residence_boundary
            ]
        world_limits = {
            "relationships": 2,
            "places": 2,
            "events": 2,
            "summaries": min(2, self.context.config.memory.max_injection_items),
        }
        experience_limits = {
            "expression_profiles": 3,
            "episodes": 2,
            "feedback": 0,
            "reply_effects": 0,
            "expression_reviews": 0,
            "behavior_scenes": 0,
            "terms": 2,
        }
        groups = {
            "relationships": self._semantic_snapshot_group(
                "relationships", list(snapshot.get("relationships") or [])
            ),
            "places": self._semantic_snapshot_group("places", place_items),
            "events": self._semantic_snapshot_group("events", event_items),
            "summaries": self._semantic_snapshot_group(
                "summaries", list(snapshot.get("summaries") or [])
            ),
        }
        groups.update(
            {
                key: self._semantic_snapshot_group(key, list(snapshot.get(key) or []))
                for key in experience_limits
            }
        )
        # 用当前交流找资料，不用自己的日程与困意作检索词引回更多同类状态。
        query = event_message.strip()
        details = []
        if str(meta.get("residence_context_stale") or "").lower() != "true":
            for key, label, value in (
                ("appearance", "当前外观", format_current_appearance_context(data)),
                ("wardrobe", "已有衣物", meta.get("wardrobe_context")),
                ("weather", "当前天气", data.weather),
                ("memo", "尚未完成的备忘", data.memo),
                ("domains", "生活资料", snapshot.get("chat_domain_context")),
            ):
                text = f"{label}：{str(value or '').strip()}"
                # 超长资料仍可用工具读取；不截断否定句或整份记录后送去排序。
                if value and len(text) <= 800:
                    details.append((key, text, text))
            for index, item in enumerate(data.timeline[:32]):
                # 明确保留时间、计划或完成性质，避免用时间流逝替代执行证据。
                state = getattr(item, "execution_state", "planned")
                label = {"completed": "已完成", "cancelled": "已取消", "skipped": "已跳过", "expired": "已过期"}.get(state, "计划，完成尚未确认")
                at = timeline_item_datetime(item, data.date, meta=meta)
                if at is None:
                    continue
                text = f"{at:%Y-%m-%d %H:%M} {item.activity}（{label}）"
                if len(text) <= 800:
                    details.append((f"schedule:{index}", text, text))
        groups["chat_details"] = details
        limits = {**world_limits, **experience_limits, "chat_details": 2}
        universal = [item for item in snapshot.get("expression_profiles", [])
                     if item.learning.get("breadth") in {"voice", "relationship"}]
        # 共同风格与对象偏好不依赖话题相似度；具体场景才参与语义选择。
        groups["expression_profiles"] = self._semantic_snapshot_group(
            "expression_profiles", [item for item in snapshot.get("expression_profiles", [])
                                    if item not in universal]
        )
        groups = {kind: values if limits[kind] else [] for kind, values in groups.items()}
        try:
            selected = await asyncio.wait_for(
                self.context.rank_semantic_groups(query, groups, limits, optional_groups={"chat_details"}),
                timeout=self.context._CHAT_MEMORY_RANK_BUDGET_SECONDS,
            )
        except TimeoutError:
            # 普通聊天不等远端向量/排序模型；保留原有顺序与独立的长期记忆召回。
            selected = {
                kind: [] if kind == "chat_details" else [item for _, _, item in values[: max(0, limits[kind])]]
                for kind, values in groups.items()
            }
            logger.debug("[上下文注入] 记忆语义排序超时，本轮使用已有记录顺序")
        world_context = format_hidden_world_context(
            selected["relationships"],
            selected["places"],
            selected["events"],
            selected["summaries"],
        )
        selected["expression_profiles"] = bounded_profiles(general_profiles(universal) + list(selected.get("expression_profiles") or []))
        snapshot["selected_expression_profiles"] = selected["expression_profiles"]
        snapshot["selected_chat_details"] = "\n".join(selected.get("chat_details") or [])
        experience_context = self._format_selected_experience_context(
            snapshot, selected
        )
        return world_context, experience_context

    def _scope_snapshot_items(
        self, snapshot: dict[str, Any], event: Any = None
    ) -> tuple[list[Any], list[Any], list[Any]]:
        environments = list(snapshot.get("environments") or [])
        decisions = list(snapshot.get("decisions") or [])
        visibility = list(snapshot.get("visibility") or [])
        if event is None:
            return environments, decisions, visibility

        session_id = self.context._event_session_id(event)
        group_id, _ = self.context._event_group_meta(event)

        def in_current_scope(item: Any) -> bool:
            item_session = str(getattr(item, "session_id", "") or "").strip()
            item_group = str(getattr(item, "group_id", "") or "").strip()
            return bool(
                (group_id and item_group == group_id)
                or (session_id and item_session == session_id)
            )

        scoped_environments = [item for item in environments if in_current_scope(item)]
        scoped_decisions = [item for item in decisions if in_current_scope(item)]
        scoped_visibility = [item for item in visibility if in_current_scope(item)]
        return scoped_environments, scoped_decisions, scoped_visibility

    def _format_selected_experience_context(
        self,
        snapshot: dict[str, Any],
        selected: dict[str, list[Any]],
    ) -> str:
        return self.context._format_hidden_experience_context(
            episodes=selected["episodes"],
            feedback=selected["feedback"],
            # 情绪和生理节律统一由 HiddenState/HiddenCognition 注入，避免同一批
            # 高频状态在体验上下文中再次重复占用模型上下文。
            emotion_arcs=[],
            physiological_rhythm_logs=[],
            physiological_rhythm_trend={},
            reply_effects=selected["reply_effects"],
            memory_corrections=list(snapshot.get("memory_corrections") or [])[:2],
            expression_profiles=list(selected.get("expression_profiles") or []),
            expression_reviews=selected["expression_reviews"],
            behavior_scenes=selected["behavior_scenes"],
            expression_intents=list(snapshot.get("expression_intents") or [])[:1],
            terms=selected["terms"],
            boundaries=list(snapshot.get("boundaries") or [])[:2],
        )

    @staticmethod
    def _format_cognition_context(snapshot: dict[str, Any]) -> str:
        """格式化时间事实、人格断言和三层情绪的隐藏上下文。

        Args:
            snapshot: 单次锁定读取的归档快照。

        Returns:
            可注入模型且保留时间边界的中文上下文。
        """

        sections: list[str] = []
        from ...life.entities import ACTIVITY_LABELS, OBJECT_LABELS

        living_lines = []
        for item in list(snapshot.get("living_entities") or [])[:3]:
            if not isinstance(item, dict):
                continue
            if item.get("kind") == "correction":
                living_lines.append(f"已确认纠正：{item.get('title', '')}；{item.get('content', '')}")
            else:
                labels = ACTIVITY_LABELS if item.get("kind") == "shared_activity" else OBJECT_LABELS
                living_lines.append(f"{item.get('title', '')}（{labels.get(item.get('state'), '进展待确认')}）；{item.get('progress') or item.get('detail') or ''}")
        if living_lines:
            sections.append("\n".join(living_lines))
        fact_lines = []
        for item in list(snapshot.get("temporal_facts") or [])[:4]:
            if str(getattr(item, "predicate", "") or "").strip() == "interaction_mode":
                continue
            value = json.dumps(
                getattr(item, "object_value", None),
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            valid_from = str(getattr(item, "valid_from", "") or "未记录")
            valid_to = str(getattr(item, "valid_to", "") or "当前")
            fact_lines.append(
                f"- {getattr(item, 'subject', '')}.{getattr(item, 'predicate', '')}="
                f"{value}；有效期 {valid_from} 至 {valid_to}；"
                + knowledge_label(item).partition("；")[0]
            )
        if fact_lines:
            sections.append("当前事实与待确认认识（推测不能代替事实或授权）：\n" + "\n".join(fact_lines))

        assertion_lines = []
        persona_assertions = list(snapshot.get("persona_assertions") or []) + list(
            snapshot.get("scoped_persona_assertions") or []
        )
        for item in persona_assertions[:2]:
            value = json.dumps(
                getattr(item, "object_value", None),
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            assertion_lines.append(
                f"- {getattr(item, 'subject', '')}.{getattr(item, 'predicate', '')}="
                f"{value}"
            )
        if assertion_lines:
            sections.append("已晋升人格认识：\n" + "\n".join(assertion_lines))

        affect_lines = []
        layer_labels = {
            "transient": "短时情绪",
            "daily": "当日心境",
            "relationship": "关系感受",
        }
        affective_states = list(snapshot.get("affective_states") or []) + list(
            snapshot.get("scoped_affective_states") or []
        )
        for item in affective_states[:2]:
            layer = str(getattr(item, "layer", "") or "")
            affect_lines.append(
                f"- {layer_labels.get(layer, '情绪状态')}："
                f"{getattr(item, 'label', '')}"
            )
        if affect_lines:
            sections.append("\n".join(affect_lines))

        diary_lines = [
            f"- {getattr(item, 'date', '')}：{getattr(item, 'summary', '')}"
            for item in list(snapshot.get("grounded_diary_entries") or [])[:1]
            if str(getattr(item, "summary", "") or "").strip()
        ]
        if diary_lines:
            sections.append("有证据的近期日记：\n" + "\n".join(diary_lines))
        if not sections:
            return ""
        rules = (
            "时间事实只在标注有效期内可作为当前事实；已结束版本不得当作当前状态。"
            "情绪状态只影响表达倾向，不能替代消息、图片、日程或人物事实。"
        )
        return "\n\n[HiddenCognition]\n" + rules + "\n" + "\n\n".join(sections)

    async def _build_available_life_context(
        self,
        data: Any,
        now: Any,
        using_extended_night: bool,
        event: Any = None,
    ) -> str:
        event_message = self.context._event_message_text(event)
        snapshot_task = self.context._gather_life_context_snapshot(event)
        recent_video_task = (
            self.context.format_recent_sight_context(event, limit=2)
            if event is not None
            else asyncio.sleep(0, result="")
        )
        heuristic_task = (
            self.context.build_heuristic_memory_context(event, event_message)
            if event is not None
            else asyncio.sleep(0, result="")
        )
        memos_task = (
            self._build_injection_memos_context(event, event_message)
            if event is not None
            else asyncio.sleep(0, result="")
        )
        domain_context_builder = getattr(
            getattr(self.context, "domains", None), "format_context", None
        )
        domain_task = (
            domain_context_builder()
            if callable(domain_context_builder)
            else asyncio.sleep(0, result="")
        )
        (
            snapshot,
            recent_video,
            heuristic_memory,
            memos_context,
            domain_context,
        ) = await asyncio.gather(
            snapshot_task,
            recent_video_task,
            heuristic_task,
            memos_task,
            domain_task,
        )
        environments, decisions, visibility = self._scope_snapshot_items(
            snapshot, event
        )
        snapshot["chat_domain_context"] = domain_context
        person_facts, memory_contexts = await asyncio.gather(
            self.context._build_person_fact_injection_context(
                event,
                relationships=list(snapshot.get("relationships") or []),
            ),
            self._select_life_memory_contexts(snapshot, data, event_message),
        )
        world_context, experience_context = memory_contexts
        cognition_context = self.context._format_cognition_context(snapshot)
        interaction_context = await self.context.resolve_interaction_context(
            event=event,
            snapshot=snapshot,
            now=now,
        )
        remember_expression_context(
            event,
            relationships=world_context,
            preferences=self.context._format_hidden_experience_context(
                expression_profiles=list(snapshot.get("selected_expression_profiles") or []),
                boundaries=list(snapshot.get("boundaries") or [])[:2],
            ),
            conversation=self.context.format_structured_message_context(event, limit=4)
            if event is not None else "",
            interaction=interaction_context.format_for_generation(),
        )
        expose_profiles(event, list(snapshot.get("selected_expression_profiles") or []))
        return (
            self.context.build_hidden_life_context(
                data,
                now,
                using_extended_night,
                world_context=world_context,
                group_awareness_context=format_hidden_group_awareness(
                    environments, decisions, visibility
                ),
                commitments=list(snapshot.get("commitments") or []),
                experience_context=experience_context,
                memos_context=memos_context,
                structured=self.context.format_structured_message_context(event, limit=8)
                if event is not None
                else "",
                recent_video=recent_video,
                expression_event=event,
                include_rules=False,
                detail_context=snapshot.get("selected_chat_details", ""),
            )
            + heuristic_memory
            + person_facts
            + cognition_context
            + interaction_context.format_for_generation()
        )

    async def inject_life_context(
        self, req: ProviderRequest, event: Any = None
    ) -> None:
        if self.context.is_internal_llm_session(req):
            return

        if event is not None:
            event._daily_life_expression_context = {}

        now = self.context._life_injection_now()
        timing_context = await self.context.build_conversation_timing_context(event)
        today_str = now.strftime("%Y-%m-%d")
        target_date_str, using_extended_night = await self.context.resolve_injection_target(now)
        data = await self.context.ensure_injection_day_data(target_date_str, now)
        data = await self.context.maybe_update_injection_outfit(
            today_str, data, using_extended_night
        )

        if not data:
            memos_context = await self._build_injection_memos_context(
                event, self.context._event_message_text(event)
            )
            recent_video = (
                await self.context.format_recent_sight_context(event, limit=2)
                if event is not None
                else ""
            )
            heuristic_memory = (
                await self.context.build_heuristic_memory_context(
                    event, self.context._event_message_text(event)
                )
                if event is not None
                else ""
            )
            style_context = await self.context.build_chat_style_injection_context(
                event, self.context._event_message_text(event)
            )
            person_facts = await self.context._build_person_fact_injection_context(event)
            interaction_context = await self.context.resolve_interaction_context(
                event=event,
                now=now,
            )
            remember_expression_context(
                event,
                conversation=self.context.format_structured_message_context(event, limit=4)
                if event is not None else "",
                interaction=interaction_context.format_for_generation(),
                timing=timing_context,
            )
            missing_context = (
                self.context.build_missing_life_context(
                    now,
                    target_date_str,
                    using_extended_night,
                    event=event,
                    memos_context=memos_context,
                    recent_video=recent_video,
                    include_rules=False,
                )
                + heuristic_memory
                + style_context
                + person_facts
                + interaction_context.format_for_generation()
                + self.context.friend_reference_injection_context(event)
            )
            if self.context._voice_expression_channel_enabled(event):
                self.context.mark_voice_switch_available(event)
            self.context._apply_life_request_context(req, missing_context + timing_context)
            self._append_visual_input_anchor(req)
            self.context._append_video_input_anchor(req, event)
            logger.debug("[上下文注入] 当前暂无日常生活记录，已注入防编造约束")
            return

        self._schedule_chat_state_refresh(target_date_str, now, event)
        hidden_context = await self._build_available_life_context(
            data, now, using_extended_night, event
        )
        hidden_context += await self.context.build_chat_style_injection_context(
            event, self.context._event_message_text(event)
        )
        hidden_context += self.context.friend_reference_injection_context(event)
        remember_expression_context(event, timing=timing_context)
        if self.context._voice_expression_channel_enabled(event):
            self.context.mark_voice_switch_available(event)
        self.context._apply_life_request_context(req, hidden_context + timing_context)
        self._append_visual_input_anchor(req)
        self.context._append_video_input_anchor(req, event)
        logger.debug("[上下文注入] 已注入日常生活背景上下文")
