import datetime
import uuid
from typing import Any

from astrbot.api import logger

from ...life.tools import extract_json_from_text
from ...prompts import CORE_HIDDEN_CONTEXT_RULES, CORE_JSON_OUTPUT_RULES, CORE_PERSONA_PRONOUN_RULES, cache_friendly_prompt
from ...clock import now as life_now
from ..markers import LOG_PREFIX


class ProactiveRevisitMixin:

    async def _get_recent_private_targets(self, limit: int = 5) -> list[Any]:
        relationships = await self.archive.get_recent_relationships(20)
        targets = [
            item for item in relationships
            if self._relationship_friend_target_scope(item)
            and (getattr(item, "notes", None) or str(getattr(item, "relationship_story", "") or "").strip())
        ]
        return targets[: max(0, limit)]

    @staticmethod
    def _private_revisit_memos_query(messages: list[dict[str, str]]) -> str:
        lines: list[str] = []
        for message in list(messages or [])[-3:]:
            role = str(message.get("role") or "").lower()
            label = "我" if role == "assistant" else "对方"
            content = " ".join(str(message.get("content") or "").split())
            if content:
                lines.append(f"{label}: {content}")
        return "\n".join(lines)[-500:].strip()

    async def _evaluate_private_revisit_payload(
        self,
        target_scope: str,
        *,
        relationship: Any | None,
        now: datetime.datetime,
    ) -> dict[str, Any]:
        provider = await self._get_proactive_provider()
        if not provider:
            return {"should_reply": False, "decision": "skip", "reason": "没有可用模型"}
        target_name = str(getattr(relationship, "name", "") or target_scope or "").strip()
        subjective_name = str(getattr(relationship, "subjective_name", "") or "").strip()
        persona_hint = str(getattr(relationship, "persona_hint", "") or "").strip()
        relationship_story = str(getattr(relationship, "relationship_story", "") or "").strip()
        note = ""
        if relationship and getattr(relationship, "notes", None):
            last_note = relationship.notes[-1]
            note = str(getattr(last_note, "content", "") or "").strip()
        persona = await self._current_proactive_persona(target_scope)
        persona_context = self._format_proactive_persona_context(persona)
        recent_messages = await self._read_recent_context_messages(target_scope, limit=5)
        recent_context = self._format_recent_context_messages(recent_messages)
        has_recent_context = bool(recent_messages)
        memos_query = self._private_revisit_memos_query(recent_messages)
        memos_context = await self.build_memos_hidden_context(
            self._private_revisit_event(target_scope, memos_query, relationship),
            memos_query,
            sender_name=target_name,
        ) if has_recent_context and memos_query else ""
        air_state = self._format_proactive_air_state(target_scope, now)
        expression_profiles = await self.archive.get_expression_profiles(
            limit=4,
            profile_id=str(getattr(relationship, "id", "") or ""),
        )
        if not expression_profiles:
            expression_profiles = await self.archive.get_expression_profiles(limit=4, scope=target_scope)
        behavior_patterns = await self.archive.get_behavior_patterns(limit=4, scope=target_scope)
        await self._settle_stale_reply_effects()
        reply_effects = await self.archive.get_reply_effects(limit=4, scope=target_scope)
        expression_reviews = await self.archive.get_expression_reviews(limit=3, scope=target_scope)
        behavior_scenes = await self.archive.get_behavior_scenes(limit=4, scope=target_scope)
        focus_slots = await self.archive.get_focus_slots(limit=4, scope=target_scope)
        mid_summaries = await self.archive.get_session_mid_summaries(limit=2, session_id=target_scope)
        temporary_expression_states = await self.archive.get_temporary_expression_states(limit=3, scope=target_scope)
        life_terms = await self.archive.get_life_terms(limit=5, scope=target_scope)
        relation_boundary = (
            "人设线索优先；如果人设线索为空，关系叙事或最近印象里零散出现的他/她不能当作性别依据，"
            "回复和理由都用目标对象、昵称或对方。"
        )
        fixed = f"""我在心里掂量：此刻要不要主动给一个私聊对象发一句自然问候或轻回访。
这不是任务提醒文案，也不是硬性打卡；只有在关系、最近印象或此刻状态自然适合时才回复。

隐藏上下文规则：
{CORE_HIDDEN_CONTEXT_RULES}

人物称谓与性别规则：
{CORE_PERSONA_PRONOUN_RULES}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON：
{{
  "should_reply": true,
  "confidence": 0.0,
  "decision": "reply|observe|wait|skip",
  "reason": "为什么此刻适合或不适合发起私聊回访",
  "reply_text": "一句自然、短、像真人的私聊问候",
  "memory_note": "可选的回访痕迹",
  "expression_review": {{"passed": true/false, "risk": "哪里不自然；自然则空", "suggestion": "如果不顺口，写更自然的处理方式", "reason": "判断理由"}},
  "expression_intent": {{"emotion": "这次表达背后的自然情绪短句", "emotion_category": "neutral|happy|sad|angry", "emoji_intent": "适合附加的表情意图；不需要则空", "action_intent": "隐藏动作意图，例如想了想、顺手问一句、轻轻笑一下", "send_emoji": true/false, "reason": "为什么"}},
  "send_timing": {{"delay_seconds": 0-12, "reason": "如果要像自然打字一样稍等，写原因；不需要则 0"}}
}}
规则：
- reply_text 必须服从角色人设摘要的口吻、关系视角和自我认知，像“我”自然想说的话。
- reply_text 涉及对方称谓时必须遵守人物称谓与性别规则；没有明确依据就用昵称或对方。
- 不要像系统提醒，不要像任务清单，不要像打卡。
- 如果只是想起了对方但没有自然落点，decision=observe。
- 最近真实互动是最高优先级锚点：reply_text 必须承接“刚才的私聊余温”，不能突然切到最近互动没有提到的旧地点、旧物品、旧动作或旧场景。
- 近期对话片段只用于理解私聊余温，不得逐条补答旧消息；如果最近互动不足以形成自然回访，decision=observe。
- MemOS 外部长期记忆只能补足长期事实、偏好和关系背景，不能单独作为当前正在发生的动作、地点、物品或剧情；如果 MemOS 与最近真实互动不一致，以最近真实互动为准。
- 如果只有 MemOS 旧记忆、没有最近真实互动支撑，不要用它发起“刚才/正在/人呢/快来”这类连续场景回访，decision=observe。
- 发送节奏只用于让主动问候更像顺手发出；不确定就 delay_seconds=0。
- expression_review 是发送前的自然度复核；如果没有真实近况承接、没有顺口落点，或只是为了回访而回访，passed=false，并让 should_reply=false 或 decision=observe。
- expression_intent 只描述隐藏表达方式；emotion 写自然情绪，emotion_category 必须由我根据语境裁定为 neutral、happy、sad、angry 之一；send_emoji 只有在表情能让这句问候更像顺手表达时才为 true。
- 如果这句问候不像我此刻自然会发的一句，should_reply=false。
"""
        dynamic = f"""角色人设摘要：
{persona_context}

此刻时间：{now.strftime('%Y-%m-%d %H:%M')}
目标对象：{target_name}
主观称呼：{subjective_name or '无'}
人设线索：{persona_hint or '无'}
称谓边界：{relation_boundary}
关系叙事：{relationship_story or '无'}
最近印象：{note or '无'}

刚才的私聊余温（最高优先级，必须优先承接；没有自然落点就观察）： 
{recent_context}

MemOS 外部长期记忆参考：
注意：这里只是长期背景，不代表当前正在发生；不得绕开上面的最近真实互动单独开启旧场景。
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

会话空气感：
{air_state}

短期注意槽：
{self._format_focus_slots_for_proactive(focus_slots)}

闲时发言频率：{self.config.proactive.private_talk_frequency:.2f}
回访间隔：{max(5, int(self.config.proactive.revisit_interval_minutes or 30))} 分钟
回访置信度阈值：{self.config.proactive.revisit_min_confidence:.2f}"""
        prompt = cache_friendly_prompt(fixed, dynamic)
        session_id = f"daily_life_private_revisit_{uuid.uuid4().hex[:8]}"
        try:
            provider_id = self.config.proactive.provider
            text = await self.composer._call_llm_text(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            payload = extract_json_from_text(text)
            if not isinstance(payload, dict):
                return {"should_reply": False, "decision": "skip", "reason": "模型未返回有效裁定"}
            confidence = self._clamp_float(payload.get("confidence"))
            reply_text = self._proactive_reply_text(payload.get("reply_text"))
            should_reply = (
                self._proactive_bool(payload.get("should_reply"))
                and confidence >= self.config.proactive.revisit_min_confidence
                and self._expression_review_passed(payload)
                and bool(reply_text)
            )
            payload["should_reply"] = should_reply
            payload["confidence"] = confidence
            payload["reply_text"] = reply_text if should_reply else ""
            return payload
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 私聊回访裁定失败：{exc}")
            return {"should_reply": False, "decision": "skip", "reason": f"私聊回访裁定失败：{exc}"}
        finally:
            await self.composer._cleanup_conversation(session_id)

    async def evaluate_private_revisit_candidates(self) -> None:
        if not self.config.proactive.enabled or not self.config.proactive.private_revisit_enabled:
            return
        now = life_now()
        targets = await self._get_recent_private_targets(limit=5)
        if not targets:
            return
        for relationship in targets:
            target_scope = self._resolve_private_target_umo(relationship)
            if not target_scope:
                continue
            key = target_scope
            air_delay = self._proactive_air_delay_remaining(key, now, pending_count=1)
            if air_delay > 0:
                continue
            last_revisit_at = self._proactive_private_last_revisit_at.get(key)
            cooldown_seconds = max(10, int(self.config.proactive.revisit_cooldown_minutes or 180)) * 60
            if isinstance(last_revisit_at, datetime.datetime):
                if int((now - last_revisit_at).total_seconds()) < cooldown_seconds:
                    continue
            payload = await self._evaluate_private_revisit_payload(
                target_scope,
                relationship=relationship,
                now=now,
            )
            reply_text = str(payload.get("reply_text") or "").strip()
            if not payload.get("should_reply") or not reply_text:
                self._update_proactive_air_after_decision(key, payload, now, sent=False)
                continue
            event = self._private_revisit_event(target_scope, reply_text, relationship)
            await self._save_proactive_expression_records(event, payload, reply_text, source="private_revisit")
            if await self._send_proactive_message(
                target_scope,
                reply_text,
                "私聊回访发送失败",
                relationship=relationship,
                contact_type="friend",
                send_payload=payload,
            ):
                self._reset_proactive_air_state(key)
                self._proactive_private_last_revisit_at[key] = now
                self._track_proactive_reply_effect(key, event, payload, reply_text, now)
                await self._save_pending_reply_effect(key, event, payload, reply_text)
                try:
                    await self._save_proactive_decision(
                        event=event,
                        sender_name=str(getattr(relationship, "name", "") or target_scope),
                        payload=payload,
                        now=now,
                        sent=True,
                        reply_text=reply_text,
                    )
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 私聊回访审计记录失败：{exc}")
                try:
                    await self.mark_page_status_changed("private_revisit")
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 私聊回访面板刷新通知失败：{exc}")
                logger.info(f"{LOG_PREFIX} 私聊回访发送至 {target_scope}：{reply_text}")
            else:
                self._update_proactive_air_after_decision(
                    key,
                    {"decision": "observe", "reason": "私聊回访发送失败，暂时降低主动性"},
                    now,
                    sent=False,
                )
