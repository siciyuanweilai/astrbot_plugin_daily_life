import datetime
from typing import Any

from ....life.condition import classify_message_interrupt, format_state_prompt, message_can_interrupt, normalize_state
from ....life.tools import format_timeline_to_text
from ....prompts import CORE_HIDDEN_CONTEXT_RULES, CORE_JSON_OUTPUT_RULES, CORE_PERSONA_PRONOUN_RULES, cache_friendly_prompt


class ProactivePromptMixin:

    async def _build_proactive_prompt(
        self,
        event: Any,
        sender_name: str,
        now: datetime.datetime,
        day: Any | None,
        target_date_str: str,
        using_extended_night: bool,
    ) -> str:
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
            bot_reply_line = (
                f"- 普通聊天机制已在 {last_bot_reply_at.strftime('%H:%M')} 回复过这轮会话；"
                "这次只能判断沉默后是否自然续一句，不要补答刚才那段内容。"
            )
        state = normalize_state(day.state.as_dict() if day and getattr(day, "state", None) else {})
        interrupt = classify_message_interrupt(message)
        can_interrupt = message_can_interrupt(state, interrupt)
        relationships = await self.archive.get_recent_relationships(6)
        summaries = await self.archive.get_recent_chat_summaries(6)
        focus_targets = await self.archive.get_focus_targets(5)
        expression_profiles = await self.archive.get_expression_profiles(limit=5, scope=group_id or session_id)
        if not expression_profiles:
            expression_profiles = await self.archive.get_expression_profiles(limit=5)
        behavior_patterns = await self.archive.get_behavior_patterns(limit=5, scope=group_id or session_id)
        await self._settle_stale_reply_effects()
        reply_effects = await self.archive.get_reply_effects(limit=4, scope=session_id)
        expression_reviews = await self.archive.get_expression_reviews(limit=3, scope=session_id)
        behavior_scenes = await self.archive.get_behavior_scenes(limit=4, scope=group_id or session_id)
        focus_slots = await self.archive.get_focus_slots(limit=4, scope=group_id or session_id)
        mid_summaries = await self.archive.get_session_mid_summaries(limit=3, session_id=session_id)
        temporary_expression_states = await self.archive.get_temporary_expression_states(limit=3, scope=group_id or session_id)
        life_terms = await self.archive.get_life_terms(limit=6, scope=group_id or session_id)
        group_awareness = await self._build_recent_group_awareness_for_proactive(event)
        recent_context = await self._build_recent_context_for_proactive(session_id, limit=6)
        structured = self.format_structured_message_context(event, limit=8)
        memos_context = await self.build_memos_hidden_context(event, message, sender_name=sender_name)
        candidate_messages = self._format_candidate_recent_messages(event, sender_name)
        air_state = self._format_proactive_air_state(scope_key, now) if scope_key else "暂无会话空气记录。"
        timeline = format_timeline_to_text(getattr(day, "timeline", []) if day else [])
        meta = getattr(day, "meta", {}) if day else {}
        activity = self.build_hidden_activity_hint(day, now, using_extended_night)[1] if day else "暂无当前生活记录"
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
        relationship_context = "\n".join(relationship_lines) if relationship_lines else "暂无稳定关系印象。"
        if relationship_lines:
            relationship_context = (
                "称谓边界：人设线索优先；没有明确人设线索时，旧关系叙事、最近印象或记忆里的他/她不能当作性别依据。\n"
                + relationship_context
            )
        summary_lines = [
            f"- {item.brief or item.long_summary}"
            for item in summaries[:5]
            if item.brief or item.long_summary
        ]
        focus_lines = [
            f"- {item.label or item.target_id}: 优先级 {item.priority}/100；{item.reason}"
            for item in focus_targets[:5]
            if item.label or item.target_id
        ]
        is_group = self._event_is_group_message(event)
        frequency = self.config.proactive.talk_frequency if is_group else self.config.proactive.private_talk_frequency
        chat_mode = "群聊" if is_group else "私聊"
        conversation_name = group_name or group_id or session_id or "未知会话"
        conversation_id = group_id or session_id or "未知ID"
        audience_name = "群友" if is_group else "对方"
        persona = await self._current_proactive_persona(session_id)
        persona_context = self._format_proactive_persona_context(persona)
        fixed = f"""我在心里掂量：这段聊天安静了一会儿，我要不要自然续一句。
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
  "reply_strategy": "轻插话/认真回答/安抚/跟梗/转移话题/继续观察等",
  "target_message_id": "如果回复，写最自然承接的消息ID；没有明确目标则空字符串",
  "target_topic": "如果回复，写自然承接的话题；没有明确目标则空字符串",
  "reply_text": "如果 should_reply=true，写一条自然短回复；否则空字符串",
  "memory_note": "这次闲时续话或克制观察留下的状态痕迹，可为空",
  "wait_reason": "decision=wait 时写我为什么要等一等；其他情况可为空",
  "expression_review": {{"passed": true/false, "risk": "哪里不自然；自然则空", "suggestion": "如果不顺口，写更自然的处理方式", "reason": "判断理由"}},
  "expression_intent": {{"emotion": "这次表达背后的自然情绪短句", "emotion_category": "neutral|happy|sad|angry", "emoji_intent": "适合附加的表情意图；不需要则空", "action_intent": "隐藏动作意图，例如停顿一下、顺手接话、轻轻笑一下", "send_emoji": true/false, "reason": "为什么"}},
  "send_timing": {{"delay_seconds": 0-12, "reason": "如果要像自然打字一样稍等，写原因；不需要则 0"}}
}}

规则：
- reply_text 必须服从角色人设摘要的口吻、关系视角和自我认知，像“我”自然想说的话。
- reply_text 涉及他人称谓时必须遵守人物称谓与性别规则；没有明确依据就用昵称、对方或这位群友。
- 默认克制：只有沉默后续一句会显得自然、轻量、不像补答，才主动回应。
- 群聊里必须先找到自然承接的 target_message_id 或 target_topic；找不到就不要硬接。
- 不要因为看见消息就回复；不要抢答未说完的话，必要时 decision=wait。
- 低体力、低社交意愿、深睡/浅睡、忙碌或注意力关闭时，除非很相关，否则继续观察。
- 会话里刷屏、复读、吵架、信息不足或我没看懂时，不要硬接。
- reply_text 必须像正常对话里顺手接的一句话，短、自然、没有系统说明。
- reply_text 不得暴露隐藏日程、分数、穿搭、内部状态、判断理由或“我根据系统看到”等痕迹。
- 近期对话片段只用于理解话题连续性和语气，不得逐条补答旧消息；当前判断仍以我最近看到的内容和沉默时机为准。
- 发送节奏只用于闲时回复显得更自然；不确定就 delay_seconds=0，不要为了拖延而拖延。
- expression_review 是发送前的自然度复核；如果没有顺口落点、接不上当前空气，或只是为了开口而开口，passed=false，并让 should_reply=false 或 decision=observe。
- expression_intent 只描述隐藏表达方式；emotion 写自然情绪，emotion_category 必须由我根据语境裁定为 neutral、happy、sad、angry 之一；send_emoji 只有在表情能让这句话更像顺手表达时才为 true。
- 如果这句话不像我此刻自然会接的一句，should_reply=false。
"""
        dynamic = f"""聊天场景：{chat_mode}
此刻日期时间：{now.strftime('%Y-%m-%d %H:%M')}
会话：{conversation_name}（{conversation_id}）
说话人：{sender_name}
我最近看到的内容：{message}
会话已安静：{silence_text}
待看消息数：{pending_count}
{bot_reply_line}

角色人设摘要：
{persona_context}

当前生活状态：
- 日期记录：{target_date_str}；是否延续凌晨记录：{"是" if using_extended_night else "否"}
- 此刻活动：{activity}
- 此刻状态：{format_state_prompt(state)}
- 主题/心情：{meta.get('theme', '')}；{meta.get('mood', '')}
- 全天日程背景：{timeline or '暂无'}（只作为背景，不要把稍后或夜间安排提前当成此刻状态）

会话与记忆参考：
{group_awareness}

会话空气感：
{air_state}

结构化会话流（优先判断谁在对谁说话、引用和@关系；不是长期记忆）：
{structured or '暂无结构化会话流。'}

可自然承接的候选消息：
{candidate_messages}

关系印象：
{relationship_context}

近期聊天记忆：
{chr(10).join(summary_lines) if summary_lines else '暂无近期聊天摘要。'}

MemOS 外部长期记忆参考：
{memos_context or '暂无外部长期记忆参考。'}

会话中期摘要：
{self._format_mid_summaries_for_proactive(mid_summaries)}

此刻表达状态：
{self._format_temporary_expression_states_for_proactive(temporary_expression_states)}

表达习惯参考：
{self._format_expression_profiles_for_proactive(expression_profiles)}

行为模式参考：
{self._format_behavior_patterns_for_proactive(behavior_patterns)}

行为场景簇参考：
{self._format_behavior_scenes_for_proactive(behavior_scenes)}

闲时回复效果参考：
{self._format_reply_effects_for_proactive(reply_effects)}

表达自然度参考：
{self._format_expression_reviews_for_proactive(expression_reviews)}

场景词参考：
{self._format_life_terms_for_proactive(life_terms)}

刚才的对话余温（只作氛围参考，不要补答旧消息）：
{recent_context}

近期关注目标：
{chr(10).join(focus_lines) if focus_lines else '暂无近期关注目标。'}

短期注意槽：
{self._format_focus_slots_for_proactive(focus_slots)}

客观打断信号：{interrupt.get('level')}（{interrupt.get('reason')}）
按此刻主体状态是否足以进入注意：{"是" if can_interrupt else "否"}
闲时发言频率：{frequency:.2f}（0 表示几乎不开口，1 表示很愿意自然参与）"""
        return cache_friendly_prompt(fixed, dynamic)
