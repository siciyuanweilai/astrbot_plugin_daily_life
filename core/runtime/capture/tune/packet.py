from __future__ import annotations

import copy
import uuid
from typing import Any, Protocol

from astrbot.api import logger

from ....contextbudget import compact_json
from ....life.tools import extract_json_from_text
from ....prompts import (
    CORE_JSON_OUTPUT_RULES,
    CORE_PERSONA_AUDIT_POLICY,
    cache_friendly_prompt,
)
from ...markers import LOG_PREFIX
from ..boundary import format_speaker_boundary
from .attestation import audit_cache, audit_key


class PacketComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _MEMORY_PAYLOAD_CALIBRATION_KEYS: Any
    _get_memory_provider: Any
    _str_payload: Any
    call_text_model: Any
    close_text_session: Any
    config: Any


class PacketComponent:
    def __init__(self, context: PacketComponentContext) -> None:
        self.context = context

    _MEMORY_PAYLOAD_CALIBRATION_KEYS = {
        "brief",
        "long_summary",
        "people",
        "commitments",
        "visibility",
        "group_environment",
        "action_decision",
        "subjective_impression",
        "relationship_points",
        "memory_targets",
        "preference_points",
        "life_episode",
        "life_episodes",
        "evidence_refs",
        "memory_evidence",
        "behavior_feedback",
        "expression_profiles",
        "expression_habits",
        "behavior_scenes",
        "memory_correction",
        "memory_corrections",
        "expression_intent",
        "life_terms",
        "memory_boundary_hint",
        "memory_boundaries",
        "temporal_facts",
    }

    def _memory_payload_has_saveable_context(self, payload: dict) -> bool:
        if not isinstance(payload, dict):
            return False
        if bool(payload.get("worth_saving")):
            return True
        for key in self.context._MEMORY_PAYLOAD_CALIBRATION_KEYS - {
            "brief",
            "long_summary",
            "people",
        }:
            value = payload.get(key)
            if isinstance(value, dict) and value:
                return True
            if isinstance(value, list) and value:
                return True
        return False

    def _build_memory_payload_calibration_prompt(
        self,
        payload: dict,
        meta: dict[str, str],
        persona_hint: str,
    ) -> str:
        sender_name = meta.get("sender_name") or meta.get("sender_profile_id") or "未知"
        current_role_label = self.context._str_payload(meta.get("current_role_label")) or "我"
        speaker_boundary = format_speaker_boundary(
            current_role_label=current_role_label,
            speaker_name=sender_name,
            persona_hint=persona_hint,
        )
        fixed = f"""审阅一份聊天记忆提炼结果是否和当前角色人设线索冲突。

{CORE_PERSONA_AUDIT_POLICY}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON 对象，不要 Markdown，不要解释：
{{
  "needs_revision": true/false,
  "interaction_checked": true/false,
  "checked_target_ids": ["已逐一校验的 memory_targets.profile_id"],
  "reason": "如果需要修正，简短说明冲突点；不需要则写空字符串",
  "revised": {{
    "brief": "可选，修正后的一句话摘要",
    "long_summary": "可选，修正后的较完整摘要",
    "people": ["可选，修正后的人物列表"],
    "commitments": [{{"可选": "修正后的承诺完整列表"}}],
    "visibility": {{"可选": "修正后的留意结果"}},
    "group_environment": {{"可选": "修正后的群聊环境"}},
    "action_decision": {{"可选": "修正后的行动裁定"}},
    "subjective_impression": {{"可选": "修正后的主观印象"}},
    "relationship_points": ["可选，修正后的说话人关系点完整列表"],
    "memory_targets": [{{"可选": "修正后的目标记忆完整列表"}}],
    "preference_points": [{{"可选": "修正后的偏好完整列表"}}],
    "life_episode": {{"可选": "修正后的生活片段"}},
    "life_episodes": [{{"可选": "修正后的生活片段完整列表"}}],
    "evidence_refs": [{{"可选": "修正后的证据完整列表"}}],
    "behavior_feedback": [{{"可选": "修正后的反馈完整列表"}}],
    "expression_profiles": [{{"可选": "修正后的表达画像完整列表"}}],
    "behavior_scenes": [{{"可选": "修正后的行为场景完整列表"}}],
    "memory_corrections": [{{"可选": "修正后的记忆纠错完整列表"}}],
    "expression_intent": {{"可选": "修正后的表达意图"}},
    "life_terms": [{{"可选": "修正后的语言完整列表"}}],
    "memory_boundary_hint": {{"可选": "修正后的记忆边界"}},
    "temporal_facts": [{{"可选": "修正后的时间事实完整列表"}}]
  }}
}}

规则：
- 如果没有冲突，needs_revision=false，revised 为空对象。
- 如果有冲突，只修正冲突相关字段，不要改变消息事实、关系事实、profile_id、session_id、日期或 worth_saving。
- revised 只放需要修正的字段；列表字段一旦修正，必须返回该字段修正后的完整列表。
- interaction_checked 必须表示是否已根据下方有序消息证据独立审阅 interaction_mode；没有 interaction_mode 候选时也返回 true。
- interaction_mode 只能由消息完整语义证明。按消息发生顺序审阅，较晚且更明确的当前陈述优先；私聊、群聊、role、平台、收到消息和历史回复形式都不是远程交流证据。
- interaction_mode 候选与最新明确证据冲突时，needs_revision=true，并在 revised.temporal_facts 返回修正后的完整列表；新的事实必须引用真正支持结论的 source_message_id。没有可靠证据时删除该候选，不保留猜测。
- 输出内容必须站在我的第一人称体验写，不要写成工具或平台视角。"""
        fixed += "\n同时逐一审阅 memory_targets：依据各目标自身的 persona_hint 和同批消息核对人物归属及经历，不能把当前角色的人设套给对方；修正仍放 revised.memory_targets 完整列表。checked_target_ids 仅列真正完成该检查的明确 profile_id，没有人设线索或证据不足时不列。"
        dynamic = f"""当前角色：{current_role_label}
记录视角：当前角色第一人称
消息发送者：{sender_name}
发送者 profile_id：{meta.get("sender_profile_id") or "未知"}
消息传输范围：{"群聊" if meta.get("is_group") == "true" else "私聊"}（只表示平台通道，不代表现实距离）
现实互动方式：{meta.get("interaction_mode_label") or "未知"}
本批次有序消息证据：
{meta.get("interaction_messages_json") or "[]"}
本轮开始前互动事实：
{meta.get("current_interaction_facts_json") or "[]"}
人物边界：
{speaker_boundary}
原始提炼结果 JSON：
{compact_json(payload)}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="待审阅聊天记忆")

    def _merge_memory_payload_revision(self, payload: dict, revision: dict) -> dict:
        if not isinstance(revision, dict) or not bool(revision.get("needs_revision")):
            return payload
        revised = revision.get("revised")
        if not isinstance(revised, dict):
            revised = {
                key: value
                for key, value in revision.items()
                if key in self.context._MEMORY_PAYLOAD_CALIBRATION_KEYS
            }
        if not revised:
            return payload
        merged = copy.deepcopy(payload)
        for key, value in revised.items():
            if key not in self.context._MEMORY_PAYLOAD_CALIBRATION_KEYS:
                continue
            if key in {"brief", "long_summary"}:
                text = self.context._str_payload(value)
                if text:
                    merged[key] = text
                continue
            if key in {
                "visibility",
                "group_environment",
                "action_decision",
                "subjective_impression",
                "life_episode",
                "memory_correction",
                "expression_intent",
                "memory_boundary_hint",
            }:
                if isinstance(value, dict):
                    merged[key] = value
                continue
            if isinstance(value, list):
                merged[key] = value
        return merged

    async def _calibrate_chat_memory_payload(
        self,
        payload: dict,
        meta: dict[str, str],
        persona_hint: str,
    ) -> dict:
        persona_hint = self.context._str_payload(persona_hint)
        if not self._memory_payload_has_saveable_context(payload):
            return payload
        try:
            provider = await self.context._get_memory_provider()
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 聊天记忆语义校准无法获取模型：{exc}")
            return payload
        if not provider:
            return payload
        session_id = f"daily_life_memory_calibration_{uuid.uuid4().hex[:8]}"
        prompt = self._build_memory_payload_calibration_prompt(
            payload, meta, persona_hint
        )
        provider_id = self.context.config.memory.provider
        cache = audit_cache(self.context)
        key = audit_key("packet", payload, meta, persona_hint, provider_id)
        cached = cache.get(key)
        if cached is not None:
            return cached
        try:
            text = await self.context.call_text_model(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            revision = extract_json_from_text(text)
            if (not isinstance(revision, dict) or not isinstance(revision.get("needs_revision"), bool)
                    or (revision["needs_revision"] and not isinstance(revision.get("revised"), dict))):
                return payload
            calibrated = copy.deepcopy(self._merge_memory_payload_revision(payload, revision))
            # 校验凭据仅留在本地服务，不接受提炼模型在待保存数据中自填的标志。
            checked = revision.get("checked_target_ids")
            checked = {str(item) for item in checked} if isinstance(checked, list) else set()
            for target in calibrated.get("memory_targets") or []:
                if not isinstance(target, dict):
                    continue
                hint = str(target.get("persona_hint") or "").strip()
                if hint and str(target.get("profile_id") or "") in checked:
                    target_key = audit_key("target", target, meta, hint, provider_id)
                    cache.put(target_key, target)
            has_interaction_candidate = any(
                isinstance(item, dict)
                and str(item.get("predicate") or "").strip() == "interaction_mode"
                for item in list(payload.get("temporal_facts") or [])
            )
            if (
                has_interaction_candidate
                and revision.get("interaction_checked") is not True
            ):
                calibrated = copy.deepcopy(calibrated)
                calibrated["temporal_facts"] = [
                    item
                    for item in list(calibrated.get("temporal_facts") or [])
                    if not (
                        isinstance(item, dict)
                        and str(item.get("predicate") or "").strip()
                        == "interaction_mode"
                    )
                ]
                calibrated["_interaction_audited"] = False
            else:
                calibrated["_interaction_audited"] = True
            if calibrated != payload and bool(revision.get("needs_revision")):
                logger.debug(
                    f"{LOG_PREFIX} 已语义校准聊天记忆：{meta.get('sender_name') or meta.get('sender_profile_id') or '未知'}"
                )
            cache.put(key, calibrated)
            return calibrated
        except Exception as e:
            logger.debug(f"{LOG_PREFIX} 聊天记忆语义校准跳过：{e}")
            return payload
        finally:
            await self.context.close_text_session(session_id)
