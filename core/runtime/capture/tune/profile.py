from __future__ import annotations

import json
import uuid
from typing import Any

from astrbot.api import logger

from ....life.tools import extract_json_from_text
from ....prompts import CORE_JSON_OUTPUT_RULES, CORE_PERSONA_PRONOUN_RULES, cache_friendly_prompt
from ...markers import LOG_PREFIX


class ProfileMixin:
    def _relationship_calibration_signature(self, relationship: Any, persona_hint: str) -> tuple[Any, ...]:
        notes = getattr(relationship, "notes", []) or []
        points = getattr(relationship, "memory_points", []) or []
        return (
            self._str_payload(persona_hint),
            self._str_payload(getattr(relationship, "persona_hint", "")),
            self._str_payload(getattr(relationship, "subjective_name", "")),
            tuple(self._str_payload(item) for item in (getattr(relationship, "subjective_tags", []) or []) if self._str_payload(item)),
            self._str_payload(getattr(relationship, "relationship_story", "")),
            tuple(self._str_payload(getattr(item, "content", "")) for item in notes[-3:] if self._str_payload(getattr(item, "content", ""))),
            tuple(self._str_payload(getattr(item, "content", "")) for item in points[-5:] if self._str_payload(getattr(item, "content", ""))),
        )

    def _build_relationship_calibration_prompt(self, relationship: Any, persona_hint: str) -> str:
        tags = [
            self._str_payload(item)
            for item in (getattr(relationship, "subjective_tags", []) or [])
            if self._str_payload(item)
        ]
        notes = [
            self._str_payload(getattr(item, "content", ""))
            for item in (getattr(relationship, "notes", []) or [])[-3:]
            if self._str_payload(getattr(item, "content", ""))
        ]
        points = [
            self._str_payload(getattr(item, "content", ""))
            for item in (getattr(relationship, "memory_points", []) or [])[-5:]
            if self._str_payload(getattr(item, "content", ""))
        ]
        fixed = f"""审阅一份关系档案是否和当前角色人设线索冲突。

人物称谓与性别规则：
{CORE_PERSONA_PRONOUN_RULES}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON 对象，不要 Markdown，不要解释：
{{
  "needs_revision": true/false,
  "reason": "如果需要修正，简短说明冲突点；不需要则写空字符串",
  "subjective_name": "修正后的主观称呼；不需要改可沿用原值或空字符串",
  "subjective_tags": ["修正后的主观标签，可为空"],
  "relationship_story": "修正后的一句关系叙事；必须保留原本关系事实，只修正和人设冲突的称谓或身份理解",
  "note": "可选，修正后的最近印象；没有必要则空字符串",
  "relationship_points": ["可选，修正后的稳定关系点；没有必要则空数组"]
}}

规则：
- 这不是文本清洗，不要机械替换字词；必须按语义判断档案是否和人设线索冲突。
- 如果档案没有冲突，needs_revision=false，其余字段可留空。
- 如果有冲突，只重写冲突相关内容，不要新增人设没有支持的新设定。
- 输出内容必须站在我的第一人称体验写，不要写成工具或平台视角。"""
        dynamic = f"""对象称呼：{self._str_payload(getattr(relationship, "name", "")) or self._str_payload(getattr(relationship, "id", ""))}
对方在人设中的线索：{self._str_payload(persona_hint)}
已保存人设线索：{self._str_payload(getattr(relationship, "persona_hint", "")) or "无"}
主观称呼：{self._str_payload(getattr(relationship, "subjective_name", "")) or "无"}
主观标签：{"、".join(tags) if tags else "无"}
关系叙事：{self._str_payload(getattr(relationship, "relationship_story", "")) or "无"}
最近印象：{"；".join(notes) if notes else "无"}
关系点：{"；".join(points) if points else "无"}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="待审阅关系档案")

    async def _calibrate_relationship_profile(
        self,
        profile_id: str,
        persona_hint: str,
        date_str: str,
    ) -> None:
        persona_hint = self._str_payload(persona_hint)
        if not profile_id or not persona_hint:
            return
        relationship = await self.archive.get_relationship(profile_id)
        if not relationship:
            return
        signature = self._relationship_calibration_signature(relationship, persona_hint)
        cache = getattr(self, "_relationship_calibration_cache", None)
        if cache is None:
            cache = {}
            self._relationship_calibration_cache = cache
        if cache.get(profile_id) == signature:
            return
        provider = await self._get_memory_provider()
        if not provider:
            return
        session_id = f"daily_life_relation_calibration_{uuid.uuid4().hex[:8]}"
        prompt = self._build_relationship_calibration_prompt(relationship, persona_hint)
        try:
            provider_id = self.config.memory.provider
            text = await self.composer._call_llm_text(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            payload = extract_json_from_text(text)
            if not isinstance(payload, dict):
                cache[profile_id] = signature
                return
            if not bool(payload.get("needs_revision")):
                cache[profile_id] = signature
                return
            await self.archive.revise_relationship_profile(
                profile_id,
                date_str=date_str,
                source="语义校准",
                subjective_name=self._str_payload(payload.get("subjective_name")),
                subjective_tags=[
                    self._str_payload(item)
                    for item in self._list_payload(payload.get("subjective_tags") or payload.get("tags"))
                    if self._str_payload(item)
                ][:8],
                relationship_story=self._str_payload(payload.get("relationship_story")),
                note=self._str_payload(payload.get("note")),
                relationship_points=[
                    self._str_payload(item)
                    for item in self._list_payload(payload.get("relationship_points") or payload.get("points"))
                    if self._str_payload(item)
                ][:6],
            )
            revised = await self.archive.get_relationship(profile_id)
            cache[profile_id] = self._relationship_calibration_signature(revised, persona_hint) if revised else signature
            logger.info(f"{LOG_PREFIX} 已语义校准关系档案：{self._str_payload(getattr(relationship, 'name', '')) or profile_id}")
        except Exception as e:
            logger.debug(f"{LOG_PREFIX} 关系档案语义校准跳过：{e}")
        finally:
            await self.composer._cleanup_conversation(session_id)
