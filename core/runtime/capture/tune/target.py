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
from .attestation import audit_cache, audit_key


class TargetComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _MEMORY_TARGET_CALIBRATION_KEYS: Any
    _get_memory_provider: Any
    _list_payload: Any
    _str_payload: Any
    call_text_model: Any
    close_text_session: Any
    config: Any


class TargetComponent:
    def __init__(self, context: TargetComponentContext) -> None:
        self.context = context

    _MEMORY_TARGET_CALIBRATION_KEYS = {
        "subjective_tags",
        "tags",
        "relationship_story",
        "points",
        "note",
    }

    def _memory_target_has_saveable_context(self, target: dict) -> bool:
        return isinstance(target, dict) and any(
            [
                self.context._str_payload(target.get("note")),
                self.context._str_payload(target.get("relationship_story")),
                self.context._list_payload(target.get("points")),
                self.context._list_payload(target.get("subjective_tags") or target.get("tags")),
            ]
        )

    def _build_memory_target_calibration_prompt(
        self,
        target: dict,
        meta: dict[str, str],
        persona_hint: str,
    ) -> str:
        fixed = f"""审阅一条目标人物记忆是否和该目标在人设中的明确线索冲突。

{CORE_PERSONA_AUDIT_POLICY}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON 对象，不要 Markdown，不要解释：
{{
  "needs_revision": true/false,
  "reason": "如果需要修正，简短说明冲突点；不需要则写空字符串",
  "revised": {{
    "subjective_tags": ["可选，修正后的主观标签完整列表"],
    "relationship_story": "可选，修正后的关系叙事",
    "points": ["可选，修正后的关系点完整列表"],
    "note": "可选，修正后的备注"
  }}
}}

规则：
- 如果没有冲突，needs_revision=false，revised 为空对象。
- 如果有冲突，只修正冲突相关字段，不要改变 profile_id、name、alias、source 或消息事实。
- revised 只放需要修正的字段；列表字段一旦修正，必须返回修正后的完整列表。
- 身份字段：current_role=当前角色；speaker=消息发送者；target_person=原始目标记忆对应人物；perspective=当前角色第一人称。
- 修正范围=只处理与目标人物人设线索冲突的字段。"""
        current_role = meta.get("current_role_label") or "我"
        sender_name = meta.get("sender_name") or meta.get("sender_profile_id") or "未知"
        target_name = target.get("name") or target.get("profile_id") or "未知"
        dynamic = f"""当前角色：{current_role}
记录视角：当前角色第一人称
消息发送者：{sender_name}
目标人物：{target_name}
消息传输范围：{"群聊" if meta.get("is_group") == "true" else "私聊"}（只表示平台通道，不代表现实距离）
现实互动方式：{meta.get("interaction_mode_label") or "未知"}
目标人物在人设中的线索：{self.context._str_payload(persona_hint)}
原始目标记忆 JSON：
{compact_json(target)}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="待审阅目标记忆")

    def _merge_memory_target_revision(self, target: dict, revision: dict) -> dict:
        if not isinstance(revision, dict) or not bool(revision.get("needs_revision")):
            return target
        revised = revision.get("revised")
        if not isinstance(revised, dict):
            revised = {
                key: value
                for key, value in revision.items()
                if key in self.context._MEMORY_TARGET_CALIBRATION_KEYS
            }
        if not revised:
            return target
        merged = copy.deepcopy(target)
        for key, value in revised.items():
            if key not in self.context._MEMORY_TARGET_CALIBRATION_KEYS:
                continue
            if key in {"relationship_story", "note"}:
                text = self.context._str_payload(value)
                if text:
                    merged[key] = text
                continue
            if key in {"subjective_tags", "tags", "points"} and isinstance(value, list):
                merged[key] = value
        return merged

    async def _calibrate_memory_target_payload(
        self,
        target: dict,
        meta: dict[str, str],
        persona_hint: str,
    ) -> dict:
        persona_hint = self.context._str_payload(persona_hint)
        if not persona_hint or not self._memory_target_has_saveable_context(target):
            return target
        provider_id = self.context.config.memory.provider
        cache = audit_cache(self.context)
        key = audit_key("target", target, meta, persona_hint, provider_id)
        cached = cache.get(key)
        if cached is not None:
            return cached
        provider = await self.context._get_memory_provider()
        if not provider:
            return target
        session_id = f"daily_life_memory_target_calibration_{uuid.uuid4().hex[:8]}"
        prompt = self._build_memory_target_calibration_prompt(
            target, meta, persona_hint
        )
        try:
            provider_id = self.context.config.memory.provider
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
                return target
            calibrated = self._merge_memory_target_revision(target, revision)
            cache.put(key, calibrated)
            if calibrated != target and bool(revision.get("needs_revision")):
                logger.debug(
                    f"{LOG_PREFIX} 已语义校准目标记忆：{target.get('name') or target.get('profile_id') or '未知'}"
                )
            return calibrated
        except Exception as e:
            logger.debug(f"{LOG_PREFIX} 目标记忆语义校准跳过：{e}")
            return target
        finally:
            await self.context.close_text_session(session_id)
