from __future__ import annotations

import datetime
import uuid
from typing import Any, Protocol

from astrbot.api import logger

from ...clock import TIMEZONE, local_timestamp, now as life_now
from ...life.condition import format_state_prompt, normalize_state
from ...life.people import PROACTIVE_PERSON_TEXT_PATHS
from ...life.presence import kernel_context
from ...prompts import (
    CORE_HIDDEN_CONTEXT_RULES,
    CORE_JSON_OUTPUT_RULES,
    CORE_PERSONA_PRONOUN_RULES,
    cache_friendly_prompt,
)
from .initiative import proactive_decision_contract
from ..capture.jsonclean import call_pure_json
from ..markers import LOG_PREFIX
from ..expressionlearning import select_profiles
from ..contact import contact_rhythm


class ProactiveRevisitComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _PROACTIVE_UTILITY_THRESHOLD: Any
    _advance_proactive_decision_trace: Any
    _audit_proactive_continuity: Any
    _chat_style_enabled: Any
    _clamp_float: Any
    _commit_proactive_decision: Any
    _current_proactive_persona: Any
    _event_message_id: Any
    _expression_review_passed: Any
    _format_behavior_scenes_for_proactive: Any
    _format_expression_profiles_for_proactive: Any
    _format_expression_reviews_for_proactive: Any
    _format_hidden_schedule_window: Any
    _format_life_terms_for_proactive: Any
    _format_private_revisit_evidence_scope: Any
    _format_proactive_air_state: Any
    _format_proactive_expression_style: Any
    _format_proactive_persona_context: Any
    _format_recent_context_messages: Any
    _format_reply_effects_for_proactive: Any
    _get_proactive_provider: Any
    _normalize_proactive_utility: Any
    _private_revisit_event: Any
    _private_revisit_evidence_scope: Any
    _private_revisit_memos_query: Any
    _private_revisit_relationship_snapshot: Any
    _proactive_air_delay_remaining: Any
    _proactive_bool: Any
    _proactive_current_day: Any
    _proactive_chat_enabled: Any
    _proactive_idle_candidates: Any
    _refresh_private_proactive_timing: Any
    _proactive_expression_limit_for_scope: Any
    _proactive_lifecycle_snapshot: Any
    _proactive_private_last_revisit_at: Any
    _proactive_reply_text: Any
    _proactive_voice_capability: Any
    _read_recent_context_messages: Any
    _relationship_friend_target_scope: Any
    _reset_proactive_air_state: Any
    _resolve_private_target_umo: Any
    _save_pending_reply_effect: Any
    _save_proactive_decision: Any
    _save_proactive_expression_records: Any
    _send_proactive_message: Any
    _settle_stale_reply_effects: Any
    _track_proactive_reply_effect: Any
    _transition_proactive_lifecycle: Any
    _update_proactive_air_after_decision: Any
    archive: Any
    build_hidden_activity_hint: Any
    build_memos_hidden_context: Any
    close_text_session: Any
    composer: Any
    config: Any
    living_context_for_scope: Any
    mark_page_status_changed: Any
    resolve_interaction_context: Any


class ProactiveRevisitComponent:
    def __init__(self, context: ProactiveRevisitComponentContext) -> None:
        self.context = context

    async def _get_recent_private_targets(self, limit: int = 5) -> list[Any]:
        relationships = await self.context.archive.get_recent_relationships(20)
        targets = [
            item
            for item in relationships
            if self.context._relationship_friend_target_scope(item)
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
        recent_messages = await self.context._read_recent_context_messages(
            target_scope, limit=8
        )
        recent_context = self.context._format_recent_context_messages(recent_messages, now=now)
        last_message_timestamp = 0.0
        for message in recent_messages:
            raw_timestamp = message.get("timestamp")
            try:
                timestamp = float(raw_timestamp or 0.0)
            except (TypeError, ValueError):
                try:
                    timestamp = local_timestamp(raw_timestamp) or 0.0
                except (TypeError, ValueError, OverflowError):
                    timestamp = 0.0
            if not 0 < timestamp <= local_timestamp(now):
                continue
            last_message_timestamp = max(last_message_timestamp, timestamp)
        silence_seconds = (
            max(0, int(local_timestamp(now) - last_message_timestamp))
            if last_message_timestamp > 0
            else None
        )
        timing = {
            "target_scope": target_scope,
            "last_activity_at": datetime.datetime.fromtimestamp(last_message_timestamp, TIMEZONE).replace(tzinfo=None)
            if last_message_timestamp > 0
            else None,
        }
        await self.context._refresh_private_proactive_timing(timing, now)
        opportunity_at = timing.get("private_opportunity_at")
        minimum_idle_seconds = (
            max(0, int((opportunity_at - timing["last_activity_at"]).total_seconds()))
            if isinstance(opportunity_at, datetime.datetime)
            else 0
        )
        too_recent = (
            silence_seconds is not None and silence_seconds < minimum_idle_seconds
        )
        memos_query = self.context._private_revisit_memos_query(recent_messages)
        memos_context = ""
        if recent_messages and memos_query and not too_recent:
            memos_context = await self.context.build_memos_hidden_context(
                self.context._private_revisit_event(target_scope, memos_query, relationship),
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
            "timing_reason": timing.get("timing_reason", ""),
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
        target_date_str, using_extended_night, day = await self.context._proactive_current_day(
            now
        )
        commitments = []
        getter = getattr(self.context.archive, "get_commitments", None)
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
        interaction_context = await self.context.resolve_interaction_context(
            target_scope=target_scope,
            now=now,
        )
        lines.append(f"- 现实互动方式：{interaction_context.mode_label}")
        if day:
            lines.append(
                f"- 生活记录：{target_date_str}"
                f"（{'延续昨日' if using_extended_night else '当日'}）"
            )
            lines.append(
                f"- 当前活动：{self.context.build_hidden_activity_hint(day, now, using_extended_night)[1]}"
            )
            if getattr(day, "state", None):
                state = normalize_state(day.state.as_dict())
                lines.append(f"- 当前身心状态：{format_state_prompt(state)}")
            if getattr(day, "timeline", None):
                schedule_window = self.context._format_hidden_schedule_window(day.timeline, now, timeline_date=day.date, meta=day.meta)
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
        continuous_getter = getattr(self.context.archive, "get_continuous_life", None)
        continuous_world = {}
        if callable(continuous_getter):
            try:
                continuous_world = await continuous_getter()
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 读取统一生命内核失败：{type(exc).__name__}")
        if isinstance(continuous_world, dict) and continuous_world.get("kernel"):
            lines.append("- 与其他决策共同使用的统一生命内核：")
            lines.append(kernel_context(continuous_world))
        living_reader = getattr(self.context, "living_context_for_scope", None)
        if callable(living_reader):
            lines.append(await living_reader(target_scope))
        browsing_reader = getattr(self.context, "bili_browse_share_context", None)
        browsing_context = await browsing_reader(target_scope) if callable(browsing_reader) else ""
        if browsing_context:
            lines.append(browsing_context)
        return "\n".join(lines), bool(
            day
            or commitments
            or interaction_context.has_authoritative_mode
            or bool(continuous_world.get("kernel"))
            or browsing_context
        )

    async def _private_revisit_expression_context(
        self,
        target_scope: str,
        relationship: Any | None,
        now: datetime.datetime,
    ) -> dict[str, Any]:
        profile_id = str(getattr(relationship, "user_id", "") or getattr(relationship, "id", "") or "")
        expression_profiles = await select_profiles(self.context, scope=target_scope, profile_id=profile_id,
            query=str(getattr(relationship, "note", "") or ""))
        await self.context._settle_stale_reply_effects()
        return {
            "air_state": self.context._format_proactive_air_state(target_scope, now),
            "expression_profiles": expression_profiles,
            "reply_effects": await self.context.archive.get_reply_effects(
                limit=4, scope=target_scope
            ),
            "expression_reviews": await self.context.archive.get_expression_reviews(
                limit=3, scope=target_scope
            ),
            "behavior_scenes": await self.context.archive.get_behavior_scenes(
                limit=4, scope=target_scope
            ),
            "life_terms": await self.context.archive.get_life_terms(
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
        expression_style = self.context._format_proactive_expression_style(target_scope)
        expression_limit = self.context._proactive_expression_limit_for_scope(target_scope)
        chat_style_enabled = self.context._chat_style_enabled()
        expression_guidance = (
            "口吻跟随角色本人、称谓边界和聊天表达设置。"
            if chat_style_enabled
            else "口吻跟随角色本人和称谓边界。"
        )
        expression_limit_line = (
            f"- 私聊回访参考长度为 {expression_limit} 字左右；这是表达节奏参考，不是硬截断。"
            if chat_style_enabled and expression_limit > 0
            else (
                "- 私聊回访保持完整、自然的表达，不额外套用聊天表达长度。"
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
        fixed = f"""我继续过着自己的生活，也会想起这个人。结合当下经历、关系和近期交流，决定是否想找对方说话、接着聊或一起做点什么。
联系意愿由当前主动程度、生活余力和真实互动反馈共同决定；选择适合此刻的一件事表达，也可以暂时观察。

隐藏上下文规则：
{CORE_HIDDEN_CONTEXT_RULES}

人物称谓与性别规则：
{CORE_PERSONA_PRONOUN_RULES}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

{proactive_decision_contract()}
"""
        dynamic = f"""主动交流倾向：{self.context.config.proactive.private_talk_frequency}（0 至 1，越高越愿意主动来往）

角色人设摘要：
{persona_context}

本轮表达设置：{expression_guidance}
{expression_limit_line}

此刻时间：{now.strftime("%Y-%m-%d %H:%M")}
目标对象：{relationship_snapshot["target_name"]}
主观称呼：{relationship_snapshot["subjective_name"] or "无"}
人设线索：{relationship_snapshot["persona_hint"] or "无"}
称谓依据：{relation_basis}
关系叙事：{relationship_snapshot["relationship_story"] or "无"}
最近印象：{relationship_snapshot["note"] or "无"}

回访依据：
{self.context._format_private_revisit_evidence_scope(revisit_evidence)}

{expression_section}

近期私聊片段：
{memory_context["recent_context"] or "无"}

当前生活事实：
{life_context}

主动语音消息能力：{self.context._proactive_voice_capability(target_scope)}
本轮发送能力：按表达意图发送 reply_text 文字或语音。文字发送成功后若需要表情，再从现有素材中选择一张；语音成功后不重复发送文字。

外部长期记忆参考：
{memory_context["memos_context"] or "暂无外部长期记忆参考。"}

表达习惯参考：
{self.context._format_expression_profiles_for_proactive(expression_context["expression_profiles"])}

行为场景簇参考：
{self.context._format_behavior_scenes_for_proactive(expression_context["behavior_scenes"])}

闲时回复效果参考：
{self.context._format_reply_effects_for_proactive(expression_context["reply_effects"])}

表达自然度参考：
{self.context._format_expression_reviews_for_proactive(expression_context["expression_reviews"])}

语言参考：
{self.context._format_life_terms_for_proactive(expression_context["life_terms"])}

会话空气感：
{expression_context["air_state"]}

闲时发言频率：{self.context.config.proactive.private_talk_frequency:.2f}
回访间隔：{max(5, int(self.context.config.proactive.revisit_interval_minutes or 30))} 分钟
回访置信度阈值：{self.context.config.proactive.revisit_min_confidence:.2f}"""
        return cache_friendly_prompt(fixed, dynamic)

    def _private_revisit_normalize_payload(
        self,
        payload: dict[str, Any],
        *,
        target_scope: str,
        revisit_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        confidence = self.context._clamp_float(payload.get("confidence"))
        reply_text = self.context._proactive_reply_text(payload.get("reply_text"))
        payload["_expression_candidate_text"] = reply_text
        requested = self.context._proactive_bool(payload.get("should_reply"))
        expression_passed = self.context._expression_review_passed(payload)
        utility, utility_valid = self.context._normalize_proactive_utility(payload)
        should_reply = (
            requested
            and confidence >= self.context.config.proactive.revisit_min_confidence
            and expression_passed
            and bool(reply_text)
            and bool(revisit_evidence["can_revisit"])
            and utility_valid
            and utility >= self.context._PROACTIVE_UTILITY_THRESHOLD
        )
        reason_code = "proposal_approved"
        if not requested:
            reason_code = "model_declined"
        elif confidence < self.context.config.proactive.revisit_min_confidence:
            reason_code = "confidence_below_threshold"
        elif not expression_passed:
            reason_code = "expression_review_failed"
        elif not reply_text:
            reason_code = "empty_reply"
        elif not utility_valid:
            reason_code = "invalid_utility_scores"
        elif utility < self.context._PROACTIVE_UTILITY_THRESHOLD:
            reason_code = "utility_below_threshold"
        if requested and not expression_passed:
            payload["decision"] = "observe"
            payload["reason"] = "表达检查未通过或缺少有效检查，暂缓回访"
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
        rhythm = await contact_rhythm(self.context, target_scope, now)
        if rhythm.get("ready") is False:
            return {
                "should_reply": False, "decision": "wait",
                "reason": "结合最近收发情况，稍后再考虑联系",
                "retry_after": rhythm["retry_after"],
            }
        relationship_profile_id = str(
            getattr(relationship, "user_id", "")
            or getattr(relationship, "id", "")
            or ""
        ).strip()
        interaction_context = await self.context.resolve_interaction_context(
            target_scope=target_scope,
            profile_id=relationship_profile_id,
            now=now,
        )
        provider = await self.context._get_proactive_provider()
        if not provider:
            return {"should_reply": False, "decision": "skip", "reason": "没有可用模型"}
        relationship_snapshot = self.context._private_revisit_relationship_snapshot(
            target_scope, relationship
        )
        persona = await self.context._current_proactive_persona(target_scope)
        persona_context = self.context._format_proactive_persona_context(persona)
        memory_context = await self._private_revisit_memory_context(
            target_scope,
            relationship,
            relationship_snapshot["target_name"],
            now,
        )
        revisit_evidence = self.context._private_revisit_evidence_scope(
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
                    f"最近私聊安静 {silence_seconds // 60} 分钟，"
                    "尚未到适合重新考虑联系的生活窗口；"
                    f"{memory_context['timing_reason']}"
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
            provider_id = self.context.config.proactive.provider
            payload = await call_pure_json(
                self.context,
                provider,
                prompt,
                session_id,
                primary_provider_id=provider_id,
                strict=True,
                validator=lambda value: {
                    **value,
                    "should_reply": (
                        value.get("should_reply") is True
                        and bool(str(value.get("reply_text") or "").strip())
                    ),
                    "reply_text": str(value.get("reply_text") or "").strip(),
                    "decision": str(value.get("decision") or "observe").strip()
                    or "observe",
                    "reason": str(value.get("reason") or "").strip(),
                },
                fallback={
                    "should_reply": False,
                    "decision": "observe",
                    "reason": "模型未给出严格回访裁定",
                    "reply_text": "",
                },
            )
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
            normalized["_interaction_mode"] = interaction_context.mode
            normalized["_expression_profiles"] = [item.as_dict() for item in expression_context["expression_profiles"]]
            if not normalized.get("should_reply"):
                return normalized
            latest_messages = await self.context._read_recent_context_messages(
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
            latest_recent_context = self.context._format_recent_context_messages(
                latest_messages, now=now
            )
            (
                latest_life_context,
                latest_life_evidence,
            ) = await self._private_revisit_life_context(target_scope, now)
            if (
                latest_messages
                or life_evidence_available
                or latest_life_evidence
            ):
                (
                    continuity_valid,
                    continuity_reason,
                ) = await self.context._audit_proactive_continuity(
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
                getattr(self.context, "composer", None), "_build_person_fact_context", None
            )
            auditor = getattr(
                getattr(self.context, "composer", None), "_audit_person_payload", None
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
            await self.context.close_text_session(session_id)

    async def evaluate_private_revisit_candidates(self) -> None:
        if not self.context.config.proactive.private_revisit_enabled:
            return
        now = life_now()
        targets = await self._get_recent_private_targets(limit=5)
        if not targets:
            return
        for relationship in targets:
            target_scope = self.context._resolve_private_target_umo(relationship)
            if not target_scope:
                continue
            key = target_scope
            # 将本次机会交给正在进行的对话，避免争抢。
            candidates = getattr(self.context, "_proactive_idle_candidates", {})
            if self.context._proactive_chat_enabled(False) and isinstance(
                candidates.get(key), dict
            ):
                continue
            air_delay = self.context._proactive_air_delay_remaining(key, now, pending_count=1)
            if air_delay > 0:
                continue
            last_revisit_at = self.context._proactive_private_last_revisit_at.get(key)
            cooldown_seconds = (
                max(10, int(self.context.config.proactive.revisit_cooldown_minutes or 180)) * 60
            )
            if isinstance(last_revisit_at, datetime.datetime):
                if int((now - last_revisit_at).total_seconds()) < cooldown_seconds:
                    continue
            lifecycle_state = str(
                self.context._proactive_lifecycle_snapshot(key).get("state") or ""
            )
            if lifecycle_state == "engaged":
                self.context._transition_proactive_lifecycle(
                    key,
                    "closing",
                    event="engagement_timeout",
                    reason="上一轮主动互动已超过回访冷却期",
                    now=now,
                )
                self.context._transition_proactive_lifecycle(
                    key,
                    "cooldown",
                    event="engagement_closed",
                    reason="上一轮主动互动已闭环",
                    now=now,
                )
            elif lifecycle_state == "sending":
                self.context._transition_proactive_lifecycle(
                    key,
                    "interrupted",
                    event="stale_send_recovered",
                    reason="恢复时发现未完成的旧发送阶段",
                    now=now,
                )
            self.context._transition_proactive_lifecycle(
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
            event = self.context._private_revisit_event(target_scope, reply_text, relationship)
            sender_name = str(getattr(relationship, "name", "") or target_scope)
            await self.context._save_proactive_expression_records(
                event, payload, reply_text, source="private_revisit"
            )
            try:
                await self.context._save_proactive_decision(
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
                self.context._update_proactive_air_after_decision(key, payload, now, sent=False)
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
                self.context._transition_proactive_lifecycle(
                    key,
                    target_state,
                    event="revisit_deferred",
                    reason=str(payload.get("reason") or "本轮暂不回访"),
                    now=now,
                )
                await self.context._advance_proactive_decision_trace(
                    event,
                    payload,
                    stage=target_state,
                    reason_code=reason_code or "revisit_deferred",
                    outcome=str(payload.get("reason") or "本轮暂不回访"),
                )
                continue
            context_marker = payload.pop("_revisit_context_marker", None)
            life_context = str(payload.pop("_revisit_life_context", "") or "")
            current_messages = await self.context._read_recent_context_messages(
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
                self.context._update_proactive_air_after_decision(key, payload, now, sent=False)
                self.context._transition_proactive_lifecycle(
                    key,
                    "interrupted",
                    event="context_marker_changed",
                    reason="发送前会话或生活状态已变化",
                    now=now,
                )
                await self.context._advance_proactive_decision_trace(
                    event,
                    payload,
                    stage="interrupted",
                    reason_code="context_changed_before_send",
                    outcome="发送前会话或生活状态已变化",
                )
                continue
            latest_interaction = await self.context.resolve_interaction_context(
                target_scope=target_scope,
                profile_id=str(
                    getattr(relationship, "user_id", "")
                    or getattr(relationship, "id", "")
                    or ""
                ).strip(),
                now=life_now(),
            )
            planned_mode = payload.get("_interaction_mode")
            if planned_mode is not None and latest_interaction.mode != planned_mode:
                payload.update(
                    {
                        "should_reply": False,
                        "decision": "observe",
                        "reason": "发送前现实互动方式已变化，取消旧的主动表达",
                        "reply_text": "",
                        "stage": "proposal",
                        "reason_code": "interaction_changed_before_send",
                    }
                )
                self.context._update_proactive_air_after_decision(key, payload, now, sent=False)
                self.context._transition_proactive_lifecycle(
                    key,
                    "interrupted",
                    event="interaction_mode_changed",
                    reason="发送前现实互动方式已变化",
                    now=now,
                )
                await self.context._advance_proactive_decision_trace(
                    event,
                    payload,
                    stage="interrupted",
                    reason_code="interaction_changed_before_send",
                    outcome="发送前现实互动方式已变化",
                )
                continue
            self.context._transition_proactive_lifecycle(
                key,
                "sending",
                event="proposal_approved",
                reason=str(payload.get("reason") or "回访收益与连续性通过"),
                now=now,
            )
            await self.context._advance_proactive_decision_trace(
                event,
                payload,
                stage="sending",
                reason_code="proposal_approved",
                outcome="准备发送私聊回访",
            )
            delivery_payload = {**payload, "source": "private_revisit"}
            sent = await self.context._send_proactive_message(
                target_scope,
                reply_text,
                "私聊回访发送失败",
                relationship=relationship,
                contact_type="friend",
                send_payload=delivery_payload,
                source_event=event,
                source_message_id=self.context._event_message_id(event),
            )
            delivery = delivery_payload.get("_delivery") or {}
            if delivery:
                payload["_delivery"] = delivery
            if sent:
                reply_text = str(delivery.get("sent_text") or reply_text)
                await self.context._commit_proactive_decision(
                    event,
                    sender_name,
                    payload,
                    reply_text,
                    now,
                    source="private_revisit",
                )
                self.context._reset_proactive_air_state(key)
                self.context._proactive_private_last_revisit_at[key] = now
                self.context._transition_proactive_lifecycle(
                    key,
                    "engaged",
                    event="send_succeeded",
                    reason="私聊回访已实际送达，开始观察互动效果",
                    now=now,
                )
                self.context._track_proactive_reply_effect(
                    key,
                    event,
                    payload,
                    reply_text,
                    now,
                    source="private_revisit",
                )
                await self.context._save_pending_reply_effect(key, event, payload, reply_text)
                try:
                    await self.context.mark_page_status_changed("private_revisit")
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 私聊回访面板刷新通知失败：{exc}")
                logger.info(f"{LOG_PREFIX} 私聊回访已发送：长度={len(reply_text)}")
            else:
                status = str(delivery.get("status") or "failed")
                stage = "waiting" if status == "deferred" else (
                    "interrupted" if status == "interrupted" else "abandoned"
                )
                reason = str(delivery.get("reason") or "私聊回访未能成功发送")
                code = str(delivery.get("reason_code") or "send_failed")
                self.context._transition_proactive_lifecycle(
                    key, stage, event=code, reason=reason, now=now,
                )
                await self.context._advance_proactive_decision_trace(
                    event, payload, stage=stage, reason_code=code, outcome=reason,
                )
                if status == "unknown":
                    self.context._proactive_private_last_revisit_at[key] = now
                if status == "failed":
                    self.context._update_proactive_air_after_decision(
                        key, {"decision": "observe", "reason": reason}, now, sent=False,
                    )
