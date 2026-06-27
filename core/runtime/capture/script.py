import datetime
from typing import Any

from ...life.condition import classify_message_interrupt, format_state_prompt, message_can_interrupt, normalize_state
from ...prompts import CORE_JSON_OUTPUT_RULES, CORE_MEMORY_RULES, CORE_PERSONA_PRONOUN_RULES, cache_friendly_prompt
from .jsonclean import STRICT_JSON_REPLY_RULE


class ImprintScriptMixin:
    def _build_chat_memory_prompt(
        self,
        message: str,
        sender_name: str,
        now: datetime.datetime,
        context_meta: dict[str, str] | None = None,
        current_state: dict | None = None,
        speaker_profile: Any | None = None,
        persona_hint: str = "",
        message_facts: str = "",
    ) -> str:
        meta = context_meta or {}
        message_facts = str(message_facts or message).strip()
        state = normalize_state(current_state or {})
        chat_scope = "群聊" if meta.get("is_group") == "true" else "私聊"
        profile_persona = self._str_payload(getattr(speaker_profile, "persona_hint", ""))
        persona_hint = self._str_payload(persona_hint)
        profile_story = self._str_payload(getattr(speaker_profile, "relationship_story", ""))
        profile_tags = [
            self._str_payload(item)
            for item in (getattr(speaker_profile, "subjective_tags", []) or [])
            if self._str_payload(item)
        ][:4]
        profile_lines = [
            f"对方已保存人设线索：{profile_persona}" if profile_persona else "",
            f"对方在人设中的线索：{persona_hint}" if persona_hint and persona_hint != profile_persona else "",
            f"对方已保存关系短标签：{'、'.join(profile_tags)}" if profile_tags else "",
            f"对方已保存关系叙事：{profile_story}" if profile_story else "",
        ]
        profile_context = "\n".join(item for item in profile_lines if item) or "暂无对方已保存人设线索。"
        if persona_hint:
            profile_context += (
                "\n称谓校准：对方在人设中的线索优先级最高；如果对方已保存关系叙事、已保存印象或既有记忆里的性别称谓与它冲突，"
                "必须按这条人设线索修正，但不要把对方当成我。"
                "即使最终只输出 {\"worth_saving\": false}，服务端记录的隐藏推理也必须遵守这条人设线索；"
                "不得在隐藏推理里继续使用与人设线索冲突的他/她、男/女、兄弟/姐妹等称谓。"
            )
        else:
            profile_context += (
                "\n称谓校准：本轮没有从当前人设提取到对方性别线索；已保存叙事里零散出现的他/她不能当作性别依据，"
                "隐藏推理和输出都必须使用中性称呼。即使最终只输出 {\"worth_saving\": false}，隐藏推理也不能猜测性别。"
            )
        interrupt = classify_message_interrupt(
            message,
            directed=meta.get("is_directed") == "true",
            quoted=meta.get("is_quoted") == "true",
        )
        can_interrupt = message_can_interrupt(state, interrupt)
        group_line = (
            f"群标识：{meta.get('group_id') or '未知'}\n群名：{meta.get('group_name') or '未知'}\n"
            if meta.get("is_group") == "true"
            else ""
        )
        fixed = f"""我刚看到一段聊天内容，需要决定它是否会成为日常生活背景里值得长期参考的轻量记忆。
不记录寒暄、表情、无信息闲聊、临时吐槽；不编造未出现的信息。

身份边界：
- 我=当前角色本人，只能从我的视角判断和记录。
- 消息发送者/对方=刚发来这条消息的人，不是我。
- 消息内容是对方说给我或发到群里的内容，不要写成我发给对方。
- 隐藏推理、brief、long_summary、reason、inner_monologue 和关系叙事都不能把我与对方互换。

通用记忆原则：
{CORE_MEMORY_RULES}

人物称谓与性别规则：
{CORE_PERSONA_PRONOUN_RULES}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

        {STRICT_JSON_REPLY_RULE}
        字段结构：
{{
  "worth_saving": true,
  "brief": "一句话摘要",
  "long_summary": "稍完整但不超过80字的摘要",
  "people": ["相关人物名"],
  "visibility": {{"level": "unseen|missed|scanned|skimmed|seen|focused|ignored|seen_but_ignored", "attention_level": 0, "priority": "low|normal|high", "is_directed_at_bot": false, "freshness": "fresh|recent|stale|reactivated", "psychological_freshness": 0, "reactivated_from_id": 0, "reactivation_hint": "", "reason": "我为什么看见或略过"}},
  "group_environment": {{"atmosphere": "冷清|平稳|活跃|刷屏|争论|玩梗|欢迎|其他", "topic": "当前话题", "topic_owner": "self_topic|target_user_topic|shared_group_topic|external_topic|ambiguous_topic", "active_users": 0, "is_multithread": false, "is_spam": false, "is_repetition": false, "is_discussing_bot": false, "suitable_to_join": "yes|no|observe", "bot_watch_state": "blackout|peek|skim_window|active_watch|engaged", "participation_desire": 0, "complexity_score": 0, "understanding_confidence": 0, "deep_analysis_needed": false, "summary": "群聊环境一句话摘要"}},
  "action_decision": {{"action": "save_memory|skip_memory|observe|reply|comfort|push_back|join_ritual|eat_melon|need_deep_analysis", "reason": "为什么这样裁定", "confidence": 0.0, "scene_type": "普通闲聊|群友档案|群环境|邀约线索|争论|玩梗|欢迎|刷屏|复读|吃瓜|提到我|其他", "topic_owner": "self_topic|target_user_topic|shared_group_topic|external_topic|ambiguous_topic", "understanding": "understood|partial|unclear", "deep_analysis": false, "inner_monologue": "隐藏心理", "reply_strategy": "如果最终回复，适合怎样处理"}},
  "subjective_impression": {{"subjective_name": "我心里的短称呼", "tags": ["主观短标签"], "relationship_story": "一句关系叙事", "impression_delta": "这次留下的主观痕迹"}},
  "relationship_points": ["只允许写说话人本人长期关系/偏好/背景点"],
  "memory_targets": [{{"profile_id": "目标人物唯一标识；说话人本人用给定 profile_id，其他群友可用 name:名字", "name": "目标人物称呼", "alias": "", "persona_hint": "", "subjective_name": "", "subjective_tags": [], "relationship_story": "", "points": [], "note": "", "source": "speaker|mentioned_person|group"}}],
  "preference_points": [{{"category": "activity|outfit|social|sleep|place|style|other", "content": "可复用偏好", "weight": 0.5, "evidence": "依据"}}],
  "life_episode": {{"title": "", "summary": "", "kind": "chat|group|relationship|outing|decision|mood|other", "related_people": [], "related_places": [], "impact": "", "confidence": 0.0}},
  "evidence_refs": [{{"target_type": "relationship|chat_summary|life_episode|focus|term|action_decision", "target_id": "", "evidence_type": "observation|correction|decision|feedback", "summary": "证据短句", "confidence": 0.0}}],
  "behavior_feedback": [{{"scene": "场景", "action": "本次行为或回复倾向", "feedback": "真实反馈", "result": "positive|neutral|negative|unknown", "score": 0.0, "reason": ""}}],
  "expression_profiles": [{{"scope": "", "profile_id": "", "label": "", "tone": "", "habits": [], "avoid": [], "confidence": 0.0, "evidence": ""}}],
  "behavior_patterns": [{{"scope": "", "scene": "", "pattern": "", "suggested_action": "observe|reply|wait|save_memory|comfort|push_back", "confidence": 0.0, "support_count": 1, "score": 0.0, "evidence": ""}}],
  "behavior_scenes": [{{"scope": "", "scene": "", "cues": [], "preferred_action": "observe|reply|wait|comfort|push_back|join_ritual", "avoid_action": "", "outcome_hint": "", "confidence": 0.0, "support_count": 1}}],
  "session_mid_summary": {{"summary": "", "topic": "", "mood": "", "participants": [], "message_count": 1}},
  "temporary_expression_state": {{"scope": "", "label": "", "tone": "", "reason": "", "intensity": 0, "expires_at": ""}},
  "focus_updates": [{{"target_type": "person|group|topic|place|event", "target_id": "", "label": "", "priority": 0, "reason": "", "scope": "", "enabled": true, "expires_at": ""}}],
  "focus_slots": [{{"scope": "", "focus_key": "", "label": "", "priority": 0, "reason": "", "expires_at": ""}}],
  "memory_corrections": [{{"target_type": "relationship|chat_summary|life_episode|preference|term|other", "target_id": "", "correction": "", "evidence": "", "confidence": 0.0}}],
  "expression_intent": {{"emotion": "", "emotion_category": "neutral|happy|sad|angry", "emoji_intent": "", "action_intent": "", "send_emoji": false, "reason": ""}},
  "life_terms": [{{"term": "群聊黑话/场景词", "meaning": "含义", "scope": "", "scene": "", "examples": [], "familiarity": 0, "confidence": 0.0, "evidence": ""}}],
  "memory_boundary_hint": {{"source_scope": "", "target_scope": "", "policy": "allow|deny|ask", "reason": ""}}
}}

规则：
- relationship_points 只写说话人本人以后值得引用的稳定信息；如果内容讲的是其他群友或第三人，必须写入 memory_targets，不能挂到说话人个人档案。
- 这里的“说话人”就是消息发送者/对方，不是我。
- 群聊里不确定属于谁的信息，只保留在 brief/long_summary 或 group_environment。
- visibility、action_decision、group_environment 只在本轮确实留下有效结果时填写；普通扫过、无信息闲聊、没有理由的 observe/skip_memory 不要硬填空壳。
- subjective_impression 是关系的主观层。seen_but_ignored/ignored 表示“看见但暂时不理”，可以留下回避、犹豫、不想接、觉得吵或轻微愧疚；unseen/missed 不应留下关系痕迹。
- psychological_freshness 是心理新鲜度，不是纯时间差；根据相关度、情绪强度、关系亲疏、当前兴趣、是否被接话/引用来判断。
- bot_watch_state 表示我在这个群此刻的观看姿态；客观打断信号只是线索，最终仍由我结合主体状态和语义判断。
- deep_analysis_needed/deep_analysis 只在多线程、争议、梗不懂、图片/引用链复杂、提到我或需要安抚/反驳时设为 true。
- 性别与亲密称谓必须有明确依据；没有明确性别依据时用对方、这个人或称呼，不要写他/她。
- memory_corrections 只在用户明确纠正我、否认既有记忆、提供反证或当前人设线索推翻已保存档案时填写。
- life_terms 只学习对后续理解群聊有帮助的黑话、梗、代称或场景词；不要靠固定关键词硬匹配。
- memory_boundary_hint 只用于跨群、群聊到私聊、私聊到群聊等跨范围引用边界；同一个群/同一个私聊内部讨论不要填写。
- 如果只是普通闲聊，输出 {{"worth_saving": false}}，不要额外补空的 visibility 或 action_decision。
"""
        dynamic = f"""聊天场景：{chat_scope}
当前角色：我（不是消息发送者）
{group_line}消息发送者/对方：{sender_name}
对方 profile_id：{meta.get("sender_profile_id") or "未知"}
对方已保存档案：
{profile_context}
消息 session_id：{meta.get("session_id") or "未知"}
当前主体状态：{format_state_prompt(state)}
当前观看状态：{state.get("watch_state")}；无聊值 {state.get("boredom")}/100；摸鱼值 {state.get("fishing")}/100；注意力开放度 {state.get("attention_openness")}/100；可打断等级 {state.get("interrupt_level")}（{state.get("interrupt_reason")}）
客观打断信号：{interrupt.get("level")}（{interrupt.get("reason")}）；按当前状态是否足以进入主体注意：{"是" if can_interrupt else "否"}
结构化消息上下文（优先判断群聊里谁在对谁说话；不是长期记忆）：
{meta.get("structured") or "暂无结构化消息上下文"}
引用/回复上下文：
{meta.get("quote_context") or "无结构化引用内容"}
当前日期时间：{now.strftime('%Y-%m-%d %H:%M')}
我刚看到对方发来的内容：
{message_facts}"""
        return cache_friendly_prompt(fixed, dynamic)
