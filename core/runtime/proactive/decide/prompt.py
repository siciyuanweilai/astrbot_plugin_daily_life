import datetime
from typing import Any

from ....life.condition import format_state_prompt, normalize_state
from ....life.tools import format_timeline_to_text
from ....prompts import CORE_HIDDEN_CONTEXT_RULES, CORE_JSON_OUTPUT_RULES, CORE_PERSONA_PRONOUN_RULES, cache_friendly_prompt


class ProactivePromptMixin:
    async def _proactive_prompt_records(
        self,
        event: Any,
        *,
        sender_name: str,
        message: str,
        group_id: str,
        session_id: str,
        scope_key: str,
        now: datetime.datetime,
    ) -> dict[str, Any]:
        expression_scope = group_id or session_id
        relationships = await self.archive.get_recent_relationships(6)
        summaries = await self.archive.get_recent_chat_summaries(6)
        focus_targets = await self.archive.get_focus_targets(5)
        expression_profiles = await self.archive.get_expression_profiles(limit=5, scope=expression_scope)
        if not expression_profiles:
            expression_profiles = await self.archive.get_expression_profiles(limit=5)
        behavior_patterns = await self.archive.get_behavior_patterns(limit=5, scope=expression_scope)
        await self._settle_stale_reply_effects()
        return {
            "relationships": relationships,
            "summaries": summaries,
            "focus_targets": focus_targets,
            "expression_profiles": expression_profiles,
            "behavior_patterns": behavior_patterns,
            "reply_effects": await self.archive.get_reply_effects(limit=4, scope=session_id),
            "expression_reviews": await self.archive.get_expression_reviews(limit=3, scope=session_id),
            "behavior_scenes": await self.archive.get_behavior_scenes(limit=4, scope=expression_scope),
            "focus_slots": await self.archive.get_focus_slots(limit=4, scope=expression_scope),
            "mid_summaries": await self.archive.get_session_mid_summaries(limit=3, session_id=session_id),
            "temporary_expression_states": await self.archive.get_temporary_expression_states(limit=3, scope=expression_scope),
            "life_terms": await self.archive.get_life_terms(limit=6, scope=expression_scope),
            "group_awareness": await self._build_recent_group_awareness_for_proactive(event),
            "recent_context": await self._build_recent_context_for_proactive(session_id, limit=6),
            "structured": self.format_structured_message_context(event, limit=8),
            "memos_context": await self.build_memos_hidden_context(event, message, sender_name=sender_name),
            "candidate_messages": self._format_candidate_recent_messages(event, sender_name),
            "air_state": self._format_proactive_air_state(scope_key, now) if scope_key else "暂无会话空气记录。",
        }

    @staticmethod
    def _proactive_relationship_context(relationships: list[Any]) -> str:
        relationship_lines = []
        for item in relationships[:5]:
            tags = "、".join(getattr(item, "subjective_tags", []) or [])
            persona_hint = str(getattr(item, "persona_hint", "") or "").strip()
            latest_note = item.notes[-1].content if getattr(item, "notes", []) else ""
            if item.name or tags or latest_note:
                details = [
                    f"人设线索：{persona_hint}" if persona_hint else "",
                    f"标签：{tags}" if tags else "无标签",
                    latest_note or item.relationship_story or "无最近印象",
                ]
                relationship_lines.append(f"- {item.name or item.id}: " + "；".join(part for part in details if part))
        if not relationship_lines:
            return "暂无稳定关系印象。"
        return (
            "称谓边界：人设线索优先；没有明确人设线索时，旧关系叙事、最近印象或记忆里的他/她不能当作性别依据。\n"
            + "\n".join(relationship_lines)
        )

    @staticmethod
    def _proactive_summary_lines(summaries: list[Any]) -> list[str]:
        return [
            f"- {item.brief or item.long_summary}"
            for item in summaries[:5]
            if item.brief or item.long_summary
        ]

    @staticmethod
    def _proactive_focus_lines(focus_targets: list[Any]) -> list[str]:
        return [
            f"- {item.label or item.target_id}: 优先级 {item.priority}/100；{item.reason}"
            for item in focus_targets[:5]
            if item.label or item.target_id
        ]

    def _proactive_reply_limit_for_prompt(self) -> int:
        proactive_limit = self.config.proactive.max_reply_length
        style = getattr(self.config, "chat_style", None)
        if not style:
            return proactive_limit
        try:
            style_limit = int(getattr(style, "proactive_max_chars", 0) or 0)
        except (TypeError, ValueError):
            style_limit = 0
        return min(proactive_limit, style_limit) if style_limit > 0 else proactive_limit

    @staticmethod
    def _proactive_readiness_text(readiness: dict[str, Any] | None) -> dict[str, Any]:
        readiness = readiness if isinstance(readiness, dict) else {}
        local_reasons = readiness.get("local_reasons") if isinstance(readiness.get("local_reasons"), list) else []
        local_reason_text = "；".join(str(item).strip() for item in local_reasons if str(item).strip()) or "无特别偏向"
        target_message_id = str(readiness.get("target_message_id") or "").strip()
        target_topic = str(readiness.get("target_topic") or "").strip()
        target_sender_name = str(readiness.get("target_sender_name") or "").strip()
        if target_message_id or target_topic:
            target_text = "；".join(
                part
                for part in (
                    f"消息ID {target_message_id}" if target_message_id else "",
                    f"说话人 {target_sender_name}" if target_sender_name else "",
                    f"话题 {target_topic}" if target_topic else "",
                )
                if part
            )
        else:
            target_text = "暂无明确本地承接目标"
        return {
            "local_reason_text": local_reason_text,
            "local_score": readiness.get("local_score"),
            "can_interrupt": readiness.get("can_interrupt"),
            "interrupt_level": str(readiness.get("interrupt_level") or "ordinary"),
            "interrupt_reason": str(readiness.get("interrupt_reason") or "无明显打断信号"),
            "target_text": target_text,
        }

    async def _proactive_prompt_scene(
        self,
        event: Any,
        sender_name: str,
        now: datetime.datetime,
        day: Any | None,
        target_date_str: str,
        using_extended_night: bool,
        readiness: dict[str, Any] | None,
    ) -> dict[str, Any]:
        message = str(getattr(event, "message_str", "") or "").strip()
        group_id, group_name = self._event_group_meta(event)
        session_id = self._event_session_id(event)
        scope_key = self._proactive_scope_key(event)
        last_activity_at = getattr(event, "proactive_last_activity_at", None)
        last_bot_reply_at = getattr(event, "proactive_last_bot_reply_at", None)
        pending_count = max(1, int(getattr(event, "proactive_pending_count", 1) or 1))
        silence_text = "未知"
        if isinstance(last_activity_at, datetime.datetime):
            silence_seconds = max(0, int((now - last_activity_at).total_seconds()))
            silence_text = f"{silence_seconds // 60} 分钟"
        bot_reply_line = ""
        if isinstance(last_bot_reply_at, datetime.datetime):
            bot_reply_line = f"普通聊天回复时间：{last_bot_reply_at.strftime('%H:%M')}"
        state = normalize_state(day.state.as_dict() if day and getattr(day, "state", None) else {})
        records = await self._proactive_prompt_records(
            event,
            sender_name=sender_name,
            message=message,
            group_id=group_id,
            session_id=session_id,
            scope_key=scope_key,
            now=now,
        )
        timeline = format_timeline_to_text(getattr(day, "timeline", []) if day else [])
        meta = getattr(day, "meta", {}) if day else {}
        activity = self.build_hidden_activity_hint(day, now, using_extended_night)[1] if day else "暂无当前生活记录"
        is_group = self._event_is_group_message(event)
        persona = await self._current_proactive_persona(session_id)
        style = getattr(self.config, "chat_style", None)
        summary_lines = self._proactive_summary_lines(records["summaries"])
        focus_lines = self._proactive_focus_lines(records["focus_targets"])
        return {
            "activity": activity,
            "audience_name": "群友" if is_group else "对方",
            "bot_reply_line": bot_reply_line,
            "chat_mode": "群聊" if is_group else "私聊",
            "conversation_id": group_id or session_id or "未知ID",
            "conversation_name": group_name or group_id or session_id or "未知会话",
            "focus_text": chr(10).join(focus_lines) if focus_lines else "暂无近期关注目标。",
            "frequency": self.config.proactive.talk_frequency if is_group else self.config.proactive.private_talk_frequency,
            "message": message,
            "meta": meta,
            "now": now,
            "pending_count": pending_count,
            "persona_context": self._format_proactive_persona_context(persona),
            "proactive_limit": self._proactive_reply_limit_for_prompt(),
            "readiness_text": self._proactive_readiness_text(readiness),
            "records": records,
            "relationship_context": self._proactive_relationship_context(records["relationships"]),
            "sender_name": sender_name,
            "silence_text": silence_text,
            "state_text": format_state_prompt(state),
            "style_prompt": str(getattr(style, "casual_short_prompt", "") or "").strip() if style else "",
            "summary_text": chr(10).join(summary_lines) if summary_lines else "暂无近期聊天摘要。",
            "target_date_str": target_date_str,
            "timeline_text": timeline or "暂无",
            "using_extended_night_text": "是" if using_extended_night else "否",
        }

    def _proactive_prompt_fixed(self, scene: dict[str, Any]) -> str:
        audience_name = scene["audience_name"]
        proactive_limit = scene["proactive_limit"]
        style_prompt = scene["style_prompt"]
        return f"""我在心里掂量：这段聊天安静了一会儿，我要不要自然续一句。
这不是被点名后的必答，也不是普通聊天回复；普通聊天机制已经优先处理过我最近看到的内容。这里只裁定沉默期后的主动性：可以轻轻续一句，也可以继续观察。

隐藏上下文规则：
{CORE_HIDDEN_CONTEXT_RULES}

人物称谓与性别规则：
{CORE_PERSONA_PRONOUN_RULES}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON 对象：
{{
  "should_reply": true,
  "confidence": 0.0,
  "decision": "reply|observe|wait|skip",
  "reason": "为什么此刻适合或不适合自然回应",
  "inner_monologue": "隐藏心理，不展示给{audience_name}",
  "reply_strategy": "这次自然采用的回应方式",
  "target_message_id": "如果回复，写最自然承接的消息ID；没有明确目标则空字符串",
  "target_topic": "如果回复，写自然承接的话题；没有明确目标则空字符串",
  "reply_text": "如果 should_reply=true，写一条自然短回复；否则空字符串",
  "memory_note": "这次闲时续话或克制观察留下的状态痕迹，可为空",
  "wait_reason": "decision=wait 时写我为什么要等一等；其他情况可为空",
  "expression_review": {{"passed": true/false, "risk": "哪里不自然；自然则空", "suggestion": "如果不顺口，写更自然的处理方式", "reason": "判断理由"}},
  "expression_intent": {{"emotion": "这次表达背后的自然情绪短句", "emotion_category": "neutral|happy|sad|angry", "emoji_intent": "适合附加的表情意图；不需要则空", "action_intent": "隐藏动作意图，描述这句话自然伴随的停顿、语气或动作", "send_emoji": true/false, "reason": "为什么"}},
  "send_timing": {{"delay_seconds": 0-12, "reason": "如果要像自然打字一样稍等，写原因；不需要则 0"}}
}}

裁定方式：
- 先看本地门控与候选消息，再判断 reply、observe 或 wait。
- reply_text 只写一句自然短文本，参考长度约 {proactive_limit} 字；口吻跟随角色人设和表达节奏。
- 表达节奏：{style_prompt or "闲时回复保持轻量。"}
- expression_review 只做自然度复核；不顺口就不要发。
"""

    def _proactive_prompt_dynamic(self, scene: dict[str, Any]) -> str:
        meta = scene["meta"]
        now = scene["now"]
        readiness_text = scene["readiness_text"]
        records = scene["records"]
        return f"""角色人设摘要：
{scene["persona_context"]}

关系印象：
{scene["relationship_context"]}

近期聊天记忆：
{scene["summary_text"]}

MemOS 外部长期记忆参考：
{records["memos_context"] or '暂无外部长期记忆参考。'}

会话中期摘要：
{self._format_mid_summaries_for_proactive(records["mid_summaries"])}

此刻表达状态：
{self._format_temporary_expression_states_for_proactive(records["temporary_expression_states"])}

表达习惯参考：
{self._format_expression_profiles_for_proactive(records["expression_profiles"])}

行为模式参考：
{self._format_behavior_patterns_for_proactive(records["behavior_patterns"])}

行为场景簇参考：
{self._format_behavior_scenes_for_proactive(records["behavior_scenes"])}

闲时回复效果参考：
{self._format_reply_effects_for_proactive(records["reply_effects"])}

表达自然度参考：
{self._format_expression_reviews_for_proactive(records["expression_reviews"])}

语言参考：
{self._format_life_terms_for_proactive(records["life_terms"])}

近期关注目标：
{scene["focus_text"]}

短期注意槽：
{self._format_focus_slots_for_proactive(records["focus_slots"])}

当前生活状态：
- 日期记录：{scene["target_date_str"]}；是否延续凌晨记录：{scene["using_extended_night_text"]}
- 此刻活动：{scene["activity"]}
- 此刻状态：{scene["state_text"]}
- 主题/心情：{meta.get('theme', '')}；{meta.get('mood', '')}
- 全天日程背景（连续性参考）：{scene["timeline_text"]}

聊天场景：{scene["chat_mode"]}
此刻日期时间：{now.strftime('%Y-%m-%d %H:%M')}
会话：{scene["conversation_name"]}（{scene["conversation_id"]}）
说话人：{scene["sender_name"]}
我最近看到的内容：{scene["message"]}
会话已安静：{scene["silence_text"]}
待看消息数：{scene["pending_count"]}
{scene["bot_reply_line"]}
本地门控：分数 {readiness_text["local_score"] if readiness_text["local_score"] is not None else "未知"}；可打断：{"是" if readiness_text["can_interrupt"] is not False else "否"}；信号 {readiness_text["interrupt_level"]}（{readiness_text["interrupt_reason"]}）；{readiness_text["local_reason_text"]}
本地候选承接：{readiness_text["target_text"]}

会话与记忆参考：
{records["group_awareness"]}

会话空气感：
{records["air_state"]}

结构化会话流（优先判断谁在对谁说话、引用和@关系；不是长期记忆）：
{records["structured"] or '暂无结构化会话流。'}

可自然承接的候选消息：
{records["candidate_messages"]}

刚才的对话余温（只作氛围参考，不要补答旧消息）：
{records["recent_context"]}

闲时发言频率：{scene["frequency"]:.2f}（0 表示几乎不开口，1 表示很愿意自然参与）"""

    async def _build_proactive_prompt(
        self,
        event: Any,
        sender_name: str,
        now: datetime.datetime,
        day: Any | None,
        target_date_str: str,
        using_extended_night: bool,
        readiness: dict[str, Any] | None = None,
    ) -> str:
        scene = await self._proactive_prompt_scene(
            event,
            sender_name,
            now,
            day,
            target_date_str,
            using_extended_night,
            readiness,
        )
        return cache_friendly_prompt(self._proactive_prompt_fixed(scene), self._proactive_prompt_dynamic(scene))
