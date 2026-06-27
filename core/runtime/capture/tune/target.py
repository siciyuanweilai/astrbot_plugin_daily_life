from __future__ import annotations

import copy
import json
import uuid

from astrbot.api import logger

from ....life.tools import extract_json_from_text
from ....prompts import CORE_JSON_OUTPUT_RULES, CORE_PERSONA_PRONOUN_RULES, cache_friendly_prompt
from ...markers import LOG_PREFIX


class TargetMixin:
    _MEMORY_TARGET_CALIBRATION_KEYS = {
        "subjective_name",
        "subjective_tags",
        "tags",
        "relationship_story",
        "points",
        "note",
    }

    def _memory_target_has_saveable_context(self, target: dict) -> bool:
        return isinstance(target, dict) and any(
            [
                self._str_payload(target.get("note")),
                self._str_payload(target.get("relationship_story")),
                self._list_payload(target.get("points")),
                self._list_payload(target.get("subjective_tags") or target.get("tags")),
            ]
        )

    def _build_memory_target_calibration_prompt(
        self,
        target: dict,
        meta: dict[str, str],
        persona_hint: str,
    ) -> str:
        fixed = f"""审阅一条目标人物记忆是否和该目标在人设中的明确线索冲突。

人物称谓与性别规则：
{CORE_PERSONA_PRONOUN_RULES}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON 对象，不要 Markdown，不要解释：
{{
  "needs_revision": true/false,
  "reason": "如果需要修正，简短说明冲突点；不需要则写空字符串",
  "revised": {{
    "subjective_name": "可选，修正后的主观称呼",
    "subjective_tags": ["可选，修正后的主观标签完整列表"],
    "relationship_story": "可选，修正后的关系叙事",
    "points": ["可选，修正后的关系点完整列表"],
    "note": "可选，修正后的备注"
  }}
}}

规则：
- 这不是文本清洗，不要机械替换字词；必须按语义判断目标记忆是否和人设线索冲突。
- 如果没有冲突，needs_revision=false，revised 为空对象。
- 如果有冲突，只修正冲突相关字段，不要改变 profile_id、name、alias、source 或消息事实。
- revised 只放需要修正的字段；列表字段一旦修正，必须返回修正后的完整列表。
- 不要新增人设没有支持的新设定，不要把目标人物当成我，也不要把我写成消息发送者。"""
        dynamic = f"""消息发送者：{meta.get("sender_name") or meta.get("sender_profile_id") or "未知"}
聊天场景：{"群聊" if meta.get("is_group") == "true" else "私聊"}
目标人物在人设中的线索：{self._str_payload(persona_hint)}
原始目标记忆 JSON：
{json.dumps(target, ensure_ascii=False, indent=2)}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="待审阅目标记忆")

    def _merge_memory_target_revision(self, target: dict, revision: dict) -> dict:
        if not isinstance(revision, dict) or not bool(revision.get("needs_revision")):
            return target
        revised = revision.get("revised")
        if not isinstance(revised, dict):
            revised = {
                key: value
                for key, value in revision.items()
                if key in self._MEMORY_TARGET_CALIBRATION_KEYS
            }
        if not revised:
            return target
        merged = copy.deepcopy(target)
        for key, value in revised.items():
            if key not in self._MEMORY_TARGET_CALIBRATION_KEYS:
                continue
            if key in {"subjective_name", "relationship_story", "note"}:
                text = self._str_payload(value)
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
        persona_hint = self._str_payload(persona_hint)
        if not persona_hint or not self._memory_target_has_saveable_context(target):
            return target
        provider = await self._get_memory_provider()
        if not provider:
            return target
        session_id = f"daily_life_memory_target_calibration_{uuid.uuid4().hex[:8]}"
        prompt = self._build_memory_target_calibration_prompt(target, meta, persona_hint)
        try:
            provider_id = self.config.memory.provider
            text = await self.composer._call_llm_text(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            revision = extract_json_from_text(text)
            if not isinstance(revision, dict):
                return target
            calibrated = self._merge_memory_target_revision(target, revision)
            if calibrated != target and bool(revision.get("needs_revision")):
                logger.info(f"{LOG_PREFIX} 已语义校准目标记忆：{target.get('name') or target.get('profile_id') or '未知'}")
            return calibrated
        except Exception as e:
            logger.debug(f"{LOG_PREFIX} 目标记忆语义校准跳过：{e}")
            return target
        finally:
            await self.composer._cleanup_conversation(session_id)
