import datetime
import uuid
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...life.condition import format_state_prompt, normalize_state
from ...life.people import PROACTIVE_PERSON_TEXT_PATHS
from ...life.tools import extract_json_from_text
from ...prompts import (
    CORE_HIDDEN_CONTEXT_RULES,
    CORE_JSON_OUTPUT_RULES,
    CORE_PERSONA_PRONOUN_RULES,
    cache_friendly_prompt,
)
from ..markers import LOG_PREFIX


class ProactiveRevisitMixin:
    async def _get_recent_private_targets(self, limit: int = 5) -> list[Any]:
        relationships = await self.archive.get_recent_relationships(20)
        targets = [
            item
            for item in relationships
            if self._relationship_friend_target_scope(item)
            and (
                getattr(item, "notes", None)
                or str(getattr(item, "relationship_story", "") or "").strip()
            )
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
    def _private_revisit_relationship_snapshot(
        target_scope: str, relationship: Any | None
    ) -> dict[str, str]:
        note = ""
        if relationship and getattr(relationship, "notes", None):
            last_note = relationship.notes[-1]
            note = str(getattr(last_note, "content", "") or "").strip()
        return {
            "target_name": str(
                getattr(relationship, "name", "") or target_scope or ""
            ).strip(),
            "subjective_name": str(
                getattr(relationship, "subjective_name", "") or ""
            ).strip(),
            "persona_hint": str(
                getattr(relationship, "persona_hint", "") or ""
            ).strip(),
            "relationship_story": str(
                getattr(relationship, "relationship_story", "") or ""
            ).strip(),
            "note": note,
        }

    async def _private_revisit_memory_context(
        self,
        target_scope: str,
        relationship: Any | None,
        target_name: str,
        now: datetime.datetime,
    ) -> dict[str, Any]:
        recent_messages = await self._read_recent_context_messages(
            target_scope, limit=8
        )
        recent_context = self._format_recent_context_messages(recent_messages, now=now)
        last_message_timestamp = 0.0
        for message in recent_messages:
            raw_timestamp = message.get("timestamp")
            try:
                timestamp = float(raw_timestamp or 0.0)
            except (TypeError, ValueError):
                try:
                    timestamp = datetime.datetime.fromisoformat(
                        str(raw_timestamp or "").replace("Z", "+00:00")
                    ).timestamp()
                except (TypeError, ValueError):
                    timestamp = 0.0
            last_message_timestamp = max(last_message_timestamp, timestamp)
        silence_seconds = (
            max(0, int(now.timestamp() - last_message_timestamp))
            if last_message_timestamp > 0
            else None
        )
        minimum_idle_seconds = (
            max(5, int(self.config.proactive.private_idle_minutes or 60)) * 60
        )
        too_recent = (
            silence_seconds is not None and silence_seconds < minimum_idle_seconds
        )
        memos_query = self._private_revisit_memos_query(recent_messages)
        memos_context = ""
        if recent_messages and memos_query and not too_recent:
            memos_context = await self.build_memos_hidden_context(
                self._private_revisit_event(target_scope, memos_query, relationship),
                memos_query,
                sender_name=target_name,
            )
        return {
            "recent_messages": recent_messages,
            "recent_context": recent_context,
            "memos_context": memos_context,
            "last_message_timestamp": last_message_timestamp,
            "silence_seconds": silence_seconds,
            "minimum_idle_seconds": minimum_idle_seconds,
            "too_recent": too_recent,
            "context_marker": {
                "role": str(recent_messages[-1].get("role") or ""),
                "content": str(recent_messages[-1].get("content") or ""),
                "media": str(recent_messages[-1].get("media") or ""),
                "timestamp": str(recent_messages[-1].get("timestamp") or ""),
            }
            if recent_messages
            else None,
        }

    async def _private_revisit_life_context(
        self, target_scope: str, now: datetime.datetime
    ) -> tuple[str, bool]:
        """为私聊回访构建权威的当前生活事实依据。

        Args:
            target_scope: 统一格式的私聊会话来源。
            now: 用于解析当前日程时间窗口的时间。

        Returns:
            格式化后的事实依据文本，以及是否存在权威事实依据。
        """
        target_date_str, using_extended_night, day = await self._proactive_current_day(
            now
        )
        commitments = []
        getter = getattr(self.archive, "get_commitments", None)
        if callable(getter):
            try:
                commitments = await getter(status="active", limit=8)
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 读取私聊回访承诺失败：{exc}")
        commitments = [
            item
            for item in commitments
            if not str(getattr(item, "source_session", "") or "").strip()
            or str(getattr(item, "source_session", "") or "").strip() == target_scope
        ][:5]
        lines = [f"- 当前时间：{now.strftime('%Y-%m-%d %H:%M')}"]
        if day:
            lines.append(
                f"- 生活记录：{target_date_str}"
                f"（{'延续昨日' if using_extended_night else '当日'}）"
            )
            lines.append(
                f"- 当前活动：{self.build_hidden_activity_hint(day, now, using_extended_night)[1]}"
            )
            if getattr(day, "state", None):
                state = normalize_state(day.state.as_dict())
                lines.append(f"- 当前身心状态：{format_state_prompt(state)}")
            if getattr(day, "timeline", None):
                schedule_window = self._format_hidden_schedule_window(day.timeline, now)
                if schedule_window:
                    lines.append(schedule_window)
        else:
            lines.append("- 当前生活记录：暂无")
        if commitments:
            lines.append("- 尚未完成的承诺/约定：")
            lines.extend(
                f"  - {str(getattr(item, 'content', '') or '').strip()}"
                for item in commitments
                if str(getattr(item, "content", "") or "").strip()
            )
        else:
            lines.append("- 尚未完成的承诺/约定：暂无可读取记录")
        return "\n".join(lines), bool(day or commitments)

    async def _audit_private_revisit_continuity(
        self,
        *,
        payload: dict[str, Any],
        recent_context: str,
        life_context: str,
        provider: Any,
        provider_id: str,
    ) -> tuple[bool, str]:
        """根据时间与运行时事实依据校验候选回复中的断言。

        Args:
            payload: 规范化后的私聊回访决策。
            recent_context: 带时间信息的近期对话依据。
            life_context: 当前日程、状态和约定依据。
            provider: 用于审计的文本模型提供商。
            provider_id: 配置的模型提供商标识。

        Returns:
            候选回复是否有事实依据，以及审计理由。
        """
        fixed = f"""审计一条待发送的私聊回访是否符合当前事实与时间连续性。
只核对事实，不评价文风、关系亲疏或是否有趣。

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON：
{{"valid": true, "reason": "简短结论", "conflicts": ["不一致事实"]}}

审计原则：
- 区分已经发生、正在发生、未来计划、承诺和推测，不得把计划或承诺当作已经完成。
- 涉及当前地点、当前动作、动作完成、状态变化或物品状态的断言，必须有当前生活事实或带时间消息直接支持。
- 旧消息中的媒体记录只证明当时发送过媒体；本轮发送能力为纯文本，不能暗示本轮刚附带或重新发送了媒体。
- 近期对话中的旧回复也可能有误；与当前结构化生活事实冲突时，以当前结构化事实为准。
- 纯问候、开放式提问或不改变事实的自然承接，在没有冲突时可以通过。
- 任一可见断言缺少证据或与证据冲突时 valid=false；不要替候选补造经过。
"""
        dynamic = f"""候选理由：{str(payload.get("reason") or "").strip()}
候选回复：{str(payload.get("reply_text") or "").strip()}
本轮发送能力：仅文字；没有执行图片、语音、视频或文件发送。

带时间的近期私聊：
{recent_context}

当前结构化生活事实：
{life_context}"""
        prompt = cache_friendly_prompt(
            fixed, dynamic, dynamic_title="私聊回访连续性审计资料"
        )
        session_id = f"daily_life_revisit_continuity_{uuid.uuid4().hex[:8]}"
        try:
            text = await self.call_text_model(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            audit = extract_json_from_text(text)
            if not isinstance(audit, dict) or not isinstance(audit.get("valid"), bool):
                return False, "连续性审计未返回有效结果"
            return bool(audit["valid"]), str(audit.get("reason") or "").strip()[:240]
        except Exception as exc:
            return False, f"连续性审计失败：{str(exc)[:180]}"
        finally:
            await self.close_text_session(session_id)

    async def _private_revisit_expression_context(
        self,
        target_scope: str,
        relationship: Any | None,
        now: datetime.datetime,
    ) -> dict[str, Any]:
        profile_id = str(getattr(relationship, "id", "") or "")
        expression_profiles = await self.archive.get_expression_profiles(
            limit=4, profile_id=profile_id
        )
        if not expression_profiles:
            expression_profiles = await self.archive.get_expression_profiles(
                limit=4, scope=target_scope
            )
        await self._settle_stale_reply_effects()
        return {
            "air_state": self._format_proactive_air_state(target_scope, now),
            "expression_profiles": expression_profiles,
            "behavior_patterns": await self.archive.get_behavior_patterns(
                limit=4, scope=target_scope
            ),
            "reply_effects": await self.archive.get_reply_effects(
                limit=4, scope=target_scope
            ),
            "expression_reviews": await self.archive.get_expression_reviews(
                limit=3, scope=target_scope
            ),
            "behavior_scenes": await self.archive.get_behavior_scenes(
                limit=4, scope=target_scope
            ),
            "focus_slots": await self.archive.get_focus_slots(
                limit=4, scope=target_scope
            ),
            "mid_summaries": await self.archive.get_session_mid_summaries(
                limit=2, session_id=target_scope
            ),
            "temporary_expression_states": await self.archive.get_temporary_expression_states(
                limit=3, scope=target_scope
            ),
            "life_terms": await self.archive.get_life_terms(
                limit=5, scope=target_scope
            ),
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
        life_context: str,
    ) -> str:
        expression_style = self._format_proactive_expression_style(target_scope)
        expression_limit = self._proactive_expression_limit_for_scope(target_scope)
        chat_style_enabled = self._chat_style_enabled()
        expression_guidance = (
            "口吻跟随角色本人、称谓边界和聊天表达设置。"
            if chat_style_enabled
            else "口吻跟随角色本人和称谓边界。"
        )
        expression_limit_line = (
            f"- 私聊回访参考长度为 {expression_limit} 字左右；这是表达节奏参考，不是硬截断。"
            if chat_style_enabled and expression_limit > 0
            else (
                "- 私聊回访保持一句完整、自然的问候，不额外套用聊天表达长度。"
                if not chat_style_enabled
                else ""
            )
        )
        expression_section = (
            f"聊天表达设置：\n{expression_style}\n" if expression_style else ""
        )
        relation_basis = (
            "明确人设线索"
            if relationship_snapshot["persona_hint"]
            else "证据不足，使用中性称呼"
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
  "benefit": 0,
  "timeliness": 0,
  "continuity": 0,
  "disruption": 0,
  "uncertainty": 0,
  "decision": "reply|observe|wait|skip",
  "reason": "为什么此刻适合或不适合发起私聊回访",
  "reply_text": "一句自然、短、像真人的私聊问候",
  "expression_intent": {{"emotion": "可选自然情绪", "emotion_category": "neutral|happy|sad|angry", "emoji_intent": "可选表情意图", "action_intent": "可选动作意图", "send_emoji": true/false, "reason": "可选理由"}}
}}
裁定方式：
- 先看“回访依据”，判断 reply、observe、wait 或 skip。
- benefit、timeliness、continuity、disruption、uncertainty 必须分别填写 0 至 100 的整数；前三项是回访收益，后两项是打扰与不确定风险。
- 只有回访收益确实高于风险时才设 should_reply=true；不值得打扰时选择 observe 或 wait。
- reply_text 只写一句自然短问候，{expression_guidance}
{expression_limit_line}
- 近期消息必须按其明确时间理解；旧照片、旧回复或未来约定不能表述成刚发生或已经完成。
- 只有“当前生活事实”明确支持时，才能断言当前地点、当前动作、动作完成或状态变化。
- 本轮只能发送 reply_text 文字；不得声称本轮已经附带、补发或重新发送任何媒体。
- 只输出上面列出的字段，不添加内部过程或发送控制字段。
"""
        dynamic = f"""角色人设摘要：
{persona_context}

此刻时间：{now.strftime("%Y-%m-%d %H:%M")}
目标对象：{relationship_snapshot["target_name"]}
主观称呼：{relationship_snapshot["subjective_name"] or "无"}
人设线索：{relationship_snapshot["persona_hint"] or "无"}
称谓依据：{relation_basis}
关系叙事：{relationship_snapshot["relationship_story"] or "无"}
最近印象：{relationship_snapshot["note"] or "无"}

回访依据：
{self._format_private_revisit_evidence_scope(revisit_evidence)}

{expression_section}

近期私聊片段：
{memory_context["recent_context"] or "无"}

当前生活事实：
{life_context}

本轮发送能力：仅发送 reply_text 文字，不会自动附带媒体。

外部长期记忆参考：
{memory_context["memos_context"] or "暂无外部长期记忆参考。"}

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
        requested = self._proactive_bool(payload.get("should_reply"))
        expression_passed = self._expression_review_passed(payload)
        utility, utility_valid = self._normalize_proactive_utility(
            payload, confidence=confidence
        )
        style_reject_reason = self._proactive_reply_style_reject_reason(
            target_scope, reply_text
        )
        should_reply = (
            requested
            and confidence >= self.config.proactive.revisit_min_confidence
            and expression_passed
            and bool(reply_text)
            and bool(revisit_evidence["can_revisit"])
            and not style_reject_reason
            and utility_valid
            and utility >= self._PROACTIVE_UTILITY_THRESHOLD
        )
        reason_code = "proposal_approved"
        if not requested:
            reason_code = "model_declined"
        elif confidence < self.config.proactive.revisit_min_confidence:
            reason_code = "confidence_below_threshold"
        elif not expression_passed:
            reason_code = "expression_review_failed"
        elif not reply_text:
            reason_code = "empty_reply"
        elif not utility_valid:
            reason_code = "invalid_utility_scores"
        elif utility < self._PROACTIVE_UTILITY_THRESHOLD:
            reason_code = "utility_below_threshold"
        if style_reject_reason:
            payload["decision"] = "observe"
            payload["reason"] = style_reject_reason
            reason_code = "style_rejected"
        if not revisit_evidence["can_revisit"]:
            payload["decision"] = "observe"
            payload["reason"] = payload.get("reason") or revisit_evidence["reason"]
            reason_code = "revisit_evidence_missing"
        if requested and reason_code in {
            "invalid_utility_scores",
            "utility_below_threshold",
        }:
            payload["decision"] = "observe"
            payload["reason"] = (
                "回访收益不足，继续观察以避免打扰"
                if utility_valid
                else "回访收益评分不完整，暂不发送"
            )
        payload["should_reply"] = should_reply
        payload["confidence"] = confidence
        payload["stage"] = "proposal"
        payload["reason_code"] = reason_code
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
        relationship_snapshot = self._private_revisit_relationship_snapshot(
            target_scope, relationship
        )
        persona = await self._current_proactive_persona(target_scope)
        persona_context = self._format_proactive_persona_context(persona)
        memory_context = await self._private_revisit_memory_context(
            target_scope,
            relationship,
            relationship_snapshot["target_name"],
            now,
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
        if memory_context["too_recent"]:
            silence_seconds = int(memory_context["silence_seconds"] or 0)
            minimum_seconds = int(memory_context["minimum_idle_seconds"] or 0)
            return {
                "should_reply": False,
                "decision": "wait",
                "reason": (
                    f"最近私聊仅安静 {silence_seconds // 60} 分钟，"
                    f"尚未达到 {minimum_seconds // 60} 分钟回访静默门槛"
                ),
                "reply_text": "",
                "retry_after": max(1, minimum_seconds - silence_seconds),
                "revisit_evidence": revisit_evidence,
            }
        (
            life_context,
            life_evidence_available,
        ) = await self._private_revisit_life_context(target_scope, now)
        expression_context = await self._private_revisit_expression_context(
            target_scope, relationship, now
        )
        prompt = self._private_revisit_prompt(
            target_scope=target_scope,
            now=now,
            persona_context=persona_context,
            relationship_snapshot=relationship_snapshot,
            memory_context=memory_context,
            revisit_evidence=revisit_evidence,
            expression_context=expression_context,
            life_context=life_context,
        )
        session_id = f"daily_life_private_revisit_{uuid.uuid4().hex[:8]}"
        try:
            provider_id = self.config.proactive.provider
            text = await self.call_text_model(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            payload = extract_json_from_text(text)
            if not isinstance(payload, dict):
                return {
                    "should_reply": False,
                    "decision": "skip",
                    "reason": "模型未返回有效裁定",
                }
            normalized = self._private_revisit_normalize_payload(
                payload,
                target_scope=target_scope,
                revisit_evidence=revisit_evidence,
            )
            if not normalized.get("should_reply"):
                return normalized
            latest_messages = await self._read_recent_context_messages(
                target_scope, limit=8
            )
            latest_marker = (
                {
                    "role": str(latest_messages[-1].get("role") or ""),
                    "content": str(latest_messages[-1].get("content") or ""),
                    "media": str(latest_messages[-1].get("media") or ""),
                    "timestamp": str(latest_messages[-1].get("timestamp") or ""),
                }
                if latest_messages
                else None
            )
            if latest_marker != memory_context["context_marker"]:
                normalized.update(
                    {
                        "should_reply": False,
                        "decision": "wait",
                        "reason": "回访生成期间出现了新消息，交给普通聊天优先处理",
                        "reply_text": "",
                        "reason_code": "context_changed_during_evaluation",
                    }
                )
                return normalized
            latest_recent_context = self._format_recent_context_messages(
                latest_messages, now=now
            )
            (
                latest_life_context,
                latest_life_evidence,
            ) = await self._private_revisit_life_context(target_scope, now)
            if (
                life_evidence_available
                or latest_life_evidence
                or any(
                    str(item.get("timestamp") or "").strip()
                    or str(item.get("media") or "").strip()
                    for item in latest_messages
                )
            ):
                (
                    continuity_valid,
                    continuity_reason,
                ) = await self._audit_private_revisit_continuity(
                    payload=normalized,
                    recent_context=latest_recent_context,
                    life_context=latest_life_context,
                    provider=provider,
                    provider_id=provider_id,
                )
                if not continuity_valid:
                    normalized.update(
                        {
                            "should_reply": False,
                            "decision": "observe",
                            "reason": continuity_reason
                            or "回访内容与当前事实或时间连续性不一致",
                            "reply_text": "",
                            "reason_code": "continuity_audit_failed",
                        }
                    )
                    return normalized
            normalized["_revisit_context_marker"] = latest_marker
            if latest_life_evidence:
                normalized["_revisit_life_context"] = latest_life_context
            builder = getattr(
                getattr(self, "composer", None), "_build_person_fact_context", None
            )
            auditor = getattr(
                getattr(self, "composer", None), "_audit_person_payload", None
            )
            if not callable(builder) or not callable(auditor):
                return normalized
            person_facts = await builder(
                persona=persona,
                explicit_instruction=memory_context["recent_context"],
                relationships=[relationship] if relationship is not None else [],
            )
            audit = await auditor(
                normalized,
                context=person_facts,
                patterns=PROACTIVE_PERSON_TEXT_PATHS,
                provider=provider,
                provider_id=provider_id,
                subject="私聊回访",
            )
            if audit.unresolved:
                normalized.update(
                    {
                        "should_reply": False,
                        "decision": "observe",
                        "reason": "人物事实尚未核对清楚",
                        "reply_text": "",
                        "reason_code": "person_audit_failed",
                    }
                )
                return normalized
            return self._private_revisit_normalize_payload(
                audit.payload,
                target_scope=target_scope,
                revisit_evidence=revisit_evidence,
            )
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 私聊回访裁定失败：{exc}")
            return {
                "should_reply": False,
                "decision": "skip",
                "reason": f"私聊回访裁定失败：{exc}",
            }
        finally:
            await self.close_text_session(session_id)

    async def evaluate_private_revisit_candidates(self) -> None:
        if not self.config.proactive.private_revisit_enabled:
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
            cooldown_seconds = (
                max(10, int(self.config.proactive.revisit_cooldown_minutes or 180)) * 60
            )
            if isinstance(last_revisit_at, datetime.datetime):
                if int((now - last_revisit_at).total_seconds()) < cooldown_seconds:
                    continue
            lifecycle_state = str(
                self._proactive_lifecycle_snapshot(key).get("state") or ""
            )
            if lifecycle_state == "engaged":
                self._transition_proactive_lifecycle(
                    key,
                    "closing",
                    event="engagement_timeout",
                    reason="上一轮主动互动已超过回访冷却期",
                    now=now,
                )
                self._transition_proactive_lifecycle(
                    key,
                    "cooldown",
                    event="engagement_closed",
                    reason="上一轮主动互动已闭环",
                    now=now,
                )
            elif lifecycle_state == "sending":
                self._transition_proactive_lifecycle(
                    key,
                    "interrupted",
                    event="stale_send_recovered",
                    reason="恢复时发现未完成的旧发送阶段",
                    now=now,
                )
            self._transition_proactive_lifecycle(
                key,
                "considering",
                event="revisit_evaluation_started",
                reason="开始评估私聊回访价值",
                now=now,
            )
            payload = await self._evaluate_private_revisit_payload(
                target_scope,
                relationship=relationship,
                now=now,
            )
            reply_text = str(payload.get("reply_text") or "").strip()
            payload["source"] = "private_revisit"
            event = self._private_revisit_event(target_scope, reply_text, relationship)
            sender_name = str(getattr(relationship, "name", "") or target_scope)
            await self._save_proactive_expression_records(
                event, payload, reply_text, source="private_revisit"
            )
            try:
                await self._save_proactive_decision(
                    event=event,
                    sender_name=sender_name,
                    payload=payload,
                    now=now,
                    sent=False,
                    reply_text=reply_text,
                    stage="proposal",
                )
            except Exception as exc:
                logger.warning(f"{LOG_PREFIX} 私聊回访提案审计记录失败：{exc}")
            if not payload.get("should_reply") or not reply_text:
                self._update_proactive_air_after_decision(key, payload, now, sent=False)
                decision_name = str(payload.get("decision") or "observe")
                reason_code = str(payload.get("reason_code") or "")
                target_state = (
                    "interrupted"
                    if reason_code == "context_changed_during_evaluation"
                    else "cooldown"
                    if decision_name == "cooldown"
                    else "waiting"
                    if decision_name in {"wait", "observe", "air_delay"}
                    else "abandoned"
                )
                self._transition_proactive_lifecycle(
                    key,
                    target_state,
                    event="revisit_deferred",
                    reason=str(payload.get("reason") or "本轮暂不回访"),
                    now=now,
                )
                await self._advance_proactive_decision_trace(
                    event,
                    payload,
                    stage=target_state,
                    reason_code=reason_code or "revisit_deferred",
                    outcome=str(payload.get("reason") or "本轮暂不回访"),
                )
                continue
            context_marker = payload.pop("_revisit_context_marker", None)
            life_context = str(payload.pop("_revisit_life_context", "") or "")
            current_messages = await self._read_recent_context_messages(
                target_scope, limit=8
            )
            current_marker = (
                {
                    "role": str(current_messages[-1].get("role") or ""),
                    "content": str(current_messages[-1].get("content") or ""),
                    "media": str(current_messages[-1].get("media") or ""),
                    "timestamp": str(current_messages[-1].get("timestamp") or ""),
                }
                if current_messages
                else None
            )
            current_life_context = ""
            if life_context:
                current_life_context, _ = await self._private_revisit_life_context(
                    target_scope, life_now()
                )
            if current_marker != context_marker or (
                life_context and current_life_context != life_context
            ):
                payload.update(
                    {
                        "should_reply": False,
                        "decision": "wait",
                        "reason": "发送前会话或生活状态已变化，取消旧回访",
                        "reply_text": "",
                        "stage": "proposal",
                        "reason_code": "context_changed_before_send",
                    }
                )
                self._update_proactive_air_after_decision(key, payload, now, sent=False)
                self._transition_proactive_lifecycle(
                    key,
                    "interrupted",
                    event="context_marker_changed",
                    reason="发送前会话或生活状态已变化",
                    now=now,
                )
                await self._advance_proactive_decision_trace(
                    event,
                    payload,
                    stage="interrupted",
                    reason_code="context_changed_before_send",
                    outcome="发送前会话或生活状态已变化",
                )
                continue
            self._transition_proactive_lifecycle(
                key,
                "sending",
                event="proposal_approved",
                reason=str(payload.get("reason") or "回访收益与连续性通过"),
                now=now,
            )
            await self._advance_proactive_decision_trace(
                event,
                payload,
                stage="sending",
                reason_code="proposal_approved",
                outcome="准备发送私聊回访",
            )
            if await self._send_proactive_message(
                target_scope,
                reply_text,
                "私聊回访发送失败",
                relationship=relationship,
                contact_type="friend",
                send_payload=payload,
            ):
                await self._commit_proactive_decision(
                    event,
                    sender_name,
                    payload,
                    reply_text,
                    now,
                    source="private_revisit",
                )
                self._reset_proactive_air_state(key)
                self._proactive_private_last_revisit_at[key] = now
                self._transition_proactive_lifecycle(
                    key,
                    "engaged",
                    event="send_succeeded",
                    reason="私聊回访已实际送达，开始观察互动效果",
                    now=now,
                )
                self._track_proactive_reply_effect(
                    key,
                    event,
                    payload,
                    reply_text,
                    now,
                    source="private_revisit",
                )
                await self._save_pending_reply_effect(key, event, payload, reply_text)
                try:
                    await self.mark_page_status_changed("private_revisit")
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 私聊回访面板刷新通知失败：{exc}")
                logger.info(f"{LOG_PREFIX} 私聊回访已发送：长度={len(reply_text)}")
            else:
                self._transition_proactive_lifecycle(
                    key,
                    "abandoned",
                    event="send_failed",
                    reason="私聊回访未能成功发送",
                    now=now,
                )
                await self._advance_proactive_decision_trace(
                    event,
                    payload,
                    stage="abandoned",
                    reason_code="send_failed",
                    outcome="私聊回访发送失败",
                )
                self._update_proactive_air_after_decision(
                    key,
                    {
                        "decision": "observe",
                        "reason": "私聊回访发送失败，暂时降低主动性",
                    },
                    now,
                    sent=False,
                )
