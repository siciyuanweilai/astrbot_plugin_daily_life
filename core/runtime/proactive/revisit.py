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

    @staticmethod
    def _private_revisit_evidence_scope(
        *,
        recent_messages: list[dict[str, str]],
        note: str,
        relationship_story: str,
        memos_context: str,
    ) -> dict[str, Any]:
        has_recent = bool(recent_messages)
        has_relation_record = bool(note.strip() or relationship_story.strip())
        if has_recent:
            anchor = "近期私聊"
            reason = "有近期私聊片段，可以基于当前余温判断。"
        elif has_relation_record:
            anchor = "关系记录"
            reason = "没有近期私聊片段，但有关系记录，可以判断是否轻回访。"
        else:
            anchor = "无"
            reason = "缺少近期互动和关系记录，不适合主动回访。"
        memos_scope = "无"
        if memos_context.strip():
            memos_scope = "背景" if has_recent else "陈旧"
        return {
            "can_revisit": has_recent or has_relation_record,
            "anchor": anchor,
            "reason": reason,
            "recent_context": "当前" if has_recent else "无",
            "relationship_note": "背景" if note.strip() else "无",
            "relationship_story": "背景" if relationship_story.strip() else "无",
            "memos_context": memos_scope,
        }

    @staticmethod
    def _format_private_revisit_evidence_scope(evidence: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"- 可以回访：{'是' if evidence.get('can_revisit') else '否'}",
                f"- 当前锚点：{evidence.get('anchor') or '无'}",
                f"- 近期私聊：{evidence.get('recent_context') or '无'}",
                f"- 关系印象：{evidence.get('relationship_note') or '无'}",
                f"- 关系叙事：{evidence.get('relationship_story') or '无'}",
                f"- 外部长期记忆：{evidence.get('memos_context') or '无'}",
                f"- 判断原因：{evidence.get('reason') or '无'}",
            ]
        )

    @staticmethod
    def _private_revisit_relationship_snapshot(target_scope: str, relationship: Any | None) -> dict[str, str]:
        note = ""
        if relationship and getattr(relationship, "notes", None):
            last_note = relationship.notes[-1]
            note = str(getattr(last_note, "content", "") or "").strip()
        return {
            "target_name": str(getattr(relationship, "name", "") or target_scope or "").strip(),
            "subjective_name": str(getattr(relationship, "subjective_name", "") or "").strip(),
            "persona_hint": str(getattr(relationship, "persona_hint", "") or "").strip(),
            "relationship_story": str(getattr(relationship, "relationship_story", "") or "").strip(),
            "note": note,
        }

    async def _private_revisit_memory_context(
        self,
        target_scope: str,
        relationship: Any | None,
        target_name: str,
    ) -> dict[str, Any]:
        recent_messages = await self._read_recent_context_messages(target_scope, limit=5)
        recent_context = self._format_recent_context_messages(recent_messages)
        memos_query = self._private_revisit_memos_query(recent_messages)
        memos_context = ""
        if recent_messages and memos_query:
            memos_context = await self.build_memos_hidden_context(
                self._private_revisit_event(target_scope, memos_query, relationship),
                memos_query,
                sender_name=target_name,
            )
        return {
            "recent_messages": recent_messages,
            "recent_context": recent_context,
            "memos_context": memos_context,
        }

    async def _private_revisit_expression_context(
        self,
        target_scope: str,
        relationship: Any | None,
        now: datetime.datetime,
    ) -> dict[str, Any]:
        profile_id = str(getattr(relationship, "id", "") or "")
        expression_profiles = await self.archive.get_expression_profiles(limit=4, profile_id=profile_id)
        if not expression_profiles:
            expression_profiles = await self.archive.get_expression_profiles(limit=4, scope=target_scope)
        await self._settle_stale_reply_effects()
        return {
            "air_state": self._format_proactive_air_state(target_scope, now),
            "expression_profiles": expression_profiles,
            "behavior_patterns": await self.archive.get_behavior_patterns(limit=4, scope=target_scope),
            "reply_effects": await self.archive.get_reply_effects(limit=4, scope=target_scope),
            "expression_reviews": await self.archive.get_expression_reviews(limit=3, scope=target_scope),
            "behavior_scenes": await self.archive.get_behavior_scenes(limit=4, scope=target_scope),
            "focus_slots": await self.archive.get_focus_slots(limit=4, scope=target_scope),
            "mid_summaries": await self.archive.get_session_mid_summaries(limit=2, session_id=target_scope),
            "temporary_expression_states": await self.archive.get_temporary_expression_states(limit=3, scope=target_scope),
            "life_terms": await self.archive.get_life_terms(limit=5, scope=target_scope),
        }

    def _private_revisit_prompt(
        self,
        *,
        target_scope: str,
        now: datetime.datetime,
        persona_context: str,
        relationship_snapshot: dict[str, str],
        memory_context: dict[str, Any],
        revisit_evidence: dict[str, Any],
        expression_context: dict[str, Any],
    ) -> str:
        expression_style = self._format_proactive_expression_style(target_scope)
        expression_limit = self._proactive_expression_limit_for_scope(target_scope)
        expression_limit_text = f"{expression_limit} 字左右" if expression_limit > 0 else "自然短句"
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
  "expression_intent": {{"emotion": "这次表达背后的自然情绪短句", "emotion_category": "neutral|happy|sad|angry", "emoji_intent": "适合附加的表情意图；不需要则空", "action_intent": "隐藏动作意图，描述这句问候自然伴随的停顿、语气或动作", "send_emoji": true/false, "reason": "为什么"}},
  "send_timing": {{"delay_seconds": 0-12, "reason": "如果要像自然打字一样稍等，写原因；不需要则 0"}}
}}
裁定方式：
- 先看“回访依据”，判断 reply、observe、wait 或 skip。
- reply_text 只写一句自然短问候，口吻跟随角色本人、称谓边界和聊天表达设置。
- 私聊回访参考长度为 {expression_limit_text}；这是表达节奏参考，不是硬截断。
- 发送节奏只表达自然打字等待；不确定就 delay_seconds=0。
- expression_review 只做自然度复核；不顺口就 should_reply=false。
"""
        dynamic = f"""角色人设摘要：
{persona_context}

此刻时间：{now.strftime('%Y-%m-%d %H:%M')}
目标对象：{relationship_snapshot["target_name"]}
主观称呼：{relationship_snapshot["subjective_name"] or '无'}
人设线索：{relationship_snapshot["persona_hint"] or '无'}
称谓边界：{relation_boundary}
关系叙事：{relationship_snapshot["relationship_story"] or '无'}
最近印象：{relationship_snapshot["note"] or '无'}

回访依据：
{self._format_private_revisit_evidence_scope(revisit_evidence)}

聊天表达设置：
{expression_style}

近期私聊片段：
{memory_context["recent_context"] or '无'}

外部长期记忆参考：
{memory_context["memos_context"] or '暂无外部长期记忆参考。'}

会话中期摘要：
{self._format_mid_summaries_for_proactive(expression_context["mid_summaries"])}

此刻表达状态：
{self._format_temporary_expression_states_for_proactive(expression_context["temporary_expression_states"])}

表达习惯参考：
{self._format_expression_profiles_for_proactive(expression_context["expression_profiles"])}

行为模式参考：
{self._format_behavior_patterns_for_proactive(expression_context["behavior_patterns"])}

行为场景簇参考：
{self._format_behavior_scenes_for_proactive(expression_context["behavior_scenes"])}

闲时回复效果参考：
{self._format_reply_effects_for_proactive(expression_context["reply_effects"])}

表达自然度参考：
{self._format_expression_reviews_for_proactive(expression_context["expression_reviews"])}

语言参考：
{self._format_life_terms_for_proactive(expression_context["life_terms"])}

会话空气感：
{expression_context["air_state"]}

短期注意槽：
{self._format_focus_slots_for_proactive(expression_context["focus_slots"])}

闲时发言频率：{self.config.proactive.private_talk_frequency:.2f}
回访间隔：{max(5, int(self.config.proactive.revisit_interval_minutes or 30))} 分钟
回访置信度阈值：{self.config.proactive.revisit_min_confidence:.2f}"""
        return cache_friendly_prompt(fixed, dynamic)

    def _private_revisit_normalize_payload(
        self,
        payload: dict[str, Any],
        *,
        target_scope: str,
        revisit_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        confidence = self._clamp_float(payload.get("confidence"))
        reply_text = self._proactive_reply_text(payload.get("reply_text"))
        style_reject_reason = self._proactive_reply_style_reject_reason(target_scope, reply_text)
        should_reply = (
            self._proactive_bool(payload.get("should_reply"))
            and confidence >= self.config.proactive.revisit_min_confidence
            and self._expression_review_passed(payload)
            and bool(reply_text)
            and bool(revisit_evidence["can_revisit"])
            and not style_reject_reason
        )
        if style_reject_reason:
            payload["decision"] = "observe"
            payload["reason"] = style_reject_reason
        if not revisit_evidence["can_revisit"]:
            payload["decision"] = "observe"
            payload["reason"] = payload.get("reason") or revisit_evidence["reason"]
        payload["should_reply"] = should_reply
        payload["confidence"] = confidence
        payload["reply_text"] = reply_text if should_reply else ""
        payload["revisit_evidence"] = revisit_evidence
        return payload

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
        relationship_snapshot = self._private_revisit_relationship_snapshot(target_scope, relationship)
        persona = await self._current_proactive_persona(target_scope)
        persona_context = self._format_proactive_persona_context(persona)
        memory_context = await self._private_revisit_memory_context(
            target_scope,
            relationship,
            relationship_snapshot["target_name"],
        )
        revisit_evidence = self._private_revisit_evidence_scope(
            recent_messages=memory_context["recent_messages"],
            note=relationship_snapshot["note"],
            relationship_story=relationship_snapshot["relationship_story"],
            memos_context=memory_context["memos_context"],
        )
        if not revisit_evidence["can_revisit"]:
            return {
                "should_reply": False,
                "decision": "observe",
                "reason": revisit_evidence["reason"],
                "reply_text": "",
                "revisit_evidence": revisit_evidence,
            }
        expression_context = await self._private_revisit_expression_context(target_scope, relationship, now)
        prompt = self._private_revisit_prompt(
            target_scope=target_scope,
            now=now,
            persona_context=persona_context,
            relationship_snapshot=relationship_snapshot,
            memory_context=memory_context,
            revisit_evidence=revisit_evidence,
            expression_context=expression_context,
        )
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
            return self._private_revisit_normalize_payload(
                payload,
                target_scope=target_scope,
                revisit_evidence=revisit_evidence,
            )
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
