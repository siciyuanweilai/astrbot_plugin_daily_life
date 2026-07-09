from __future__ import annotations

import uuid
from typing import Any

from astrbot.api import logger

from ...life.tools import extract_json_from_text
from ...prompts import CORE_JSON_OUTPUT_RULES, CORE_PERSONA_PRONOUN_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX


class CapturePersonaMixin:
    def _get_persona_hint_cache(self) -> dict[tuple[str, str, str], str]:
        cache = getattr(self, "_persona_hint_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._persona_hint_cache = cache
        return cache

    async def _current_persona_text(self, event: Any = None) -> str:
        composer = getattr(self, "composer", None)
        get_persona = getattr(composer, "_get_persona", None)
        if not callable(get_persona):
            return ""
        try:
            scope = self._event_session_id(event) if event is not None else ""
            try:
                persona = get_persona(scope)
            except TypeError:
                persona = get_persona()
            if hasattr(persona, "__await__"):
                persona = await persona
            return self._str_payload(persona)
        except Exception as e:
            logger.debug(f"{LOG_PREFIX} 读取当前人设失败：{e}")
            return ""

    async def _current_role_label(self, event: Any = None) -> str:
        config = getattr(self, "config", None)
        for alias in getattr(config, "bot_identity_aliases", []) or []:
            text = self._str_payload(alias).lstrip("@＠").strip()
            if text:
                return text
        resolver = getattr(self, "contact_resolver", None)
        getter = getattr(resolver, "get_persona_user_name", None)
        if callable(getter):
            try:
                name = getter()
                if hasattr(name, "__await__"):
                    name = await name
                text = self._str_payload(name)
                if text:
                    return text
            except Exception as e:
                logger.debug(f"{LOG_PREFIX} 读取当前角色称呼失败：{e}")
        return "我"

    def _relationship_persona_names(self, relationship: Any = None, sender_name: str = "") -> list[str]:
        names: list[str] = []

        def add(value: Any) -> None:
            text = self._str_payload(value)
            if text and text not in names:
                names.append(text)

        add(sender_name)
        if relationship is not None:
            add(getattr(relationship, "name", ""))
            add(getattr(relationship, "alias", ""))
            add(getattr(relationship, "subjective_name", ""))
            for contact in getattr(relationship, "contacts", []) or []:
                add(getattr(contact, "user_id", ""))
        return names[:8]

    def _extract_persona_hint_exact(self, persona: str, names: list[str]) -> str:
        composer = getattr(self, "composer", None)
        extractor = getattr(composer, "_extract_reference_persona", None)
        if not callable(extractor):
            return ""
        for name in names:
            try:
                hint = self._str_payload(extractor(persona, name))
            except Exception:
                hint = ""
            if hint and name and name in hint:
                return hint
        return ""

    def _persona_match_context(self, persona: str) -> str:
        text = self._str_payload(persona)
        if len(text) <= 8000:
            return text
        return text[:4000].rstrip() + "\n\n……\n\n" + text[-4000:].lstrip()

    def _build_persona_hint_extract_prompt(self, persona: str, names: list[str], relationship: Any = None) -> str:
        saved_hint = self._str_payload(getattr(relationship, "persona_hint", "")) if relationship is not None else ""
        story = self._str_payload(getattr(relationship, "relationship_story", "")) if relationship is not None else ""
        persona_context = self._persona_match_context(persona)
        fixed = f"""从当前角色人设里找出某个聊天对象的明确人物线索。

人物称谓与性别规则：
{CORE_PERSONA_PRONOUN_RULES}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON 对象，不要 Markdown，不要解释：
{{
  "matched": true/false,
  "name": "人设中对应的称呼；没有就空字符串",
  "persona_hint": "只摘出人设明确支持的性别、关系、称呼、亲疏和稳定设定；不超过120字",
  "confidence": 0.0,
  "reason": "一句话说明为什么是同一个人；没有明确依据则空字符串"
}}

规则：
- 这不是关键词匹配；只能在当前角色人设明确支持时才 matched=true。
- 候选称呼可能是备注、群昵称、简称或日常称呼；可以和人设中的本名、常用称呼、括号内别称做语义对应。
- 如果只是昵称相似、语气像、平台标识、旧印象或猜测，matched=false。
- persona_hint 必须来自人设本身，不要把聊天内容、旧记忆或推测写进去。
- 性别、亲密称谓和关系必须有明确依据；没有明确依据就不要写。"""
        dynamic = f"""候选称呼：{"、".join(names) if names else "无"}
已保存人设线索：{saved_hint or "无"}
已保存关系叙事：{story or "无"}
当前角色人设：
{persona_context}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="待匹配对象")

    async def _extract_persona_hint_by_llm(
        self,
        persona: str,
        names: list[str],
        relationship: Any = None,
    ) -> str:
        if not persona or not names:
            return ""
        provider = await self._get_memory_provider()
        if not provider:
            return ""
        prompt = self._build_persona_hint_extract_prompt(persona, names, relationship)
        session_id = f"daily_life_persona_hint_{uuid.uuid4().hex[:8]}"
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
            if not isinstance(payload, dict) or not bool(payload.get("matched")):
                return ""
            hint = self._str_payload(payload.get("persona_hint"))
            confidence = self._float_payload(payload.get("confidence"))
            return hint if hint and confidence >= 0.6 else ""
        except Exception as e:
            logger.debug(f"{LOG_PREFIX} 语义提取人设线索失败：{e}")
            return ""
        finally:
            await self.composer._cleanup_conversation(session_id)

    async def _extract_speaker_persona_hint(
        self,
        sender_name: str,
        event: Any = None,
        relationship: Any = None,
    ) -> str:
        persona = await self._current_persona_text(event)
        if not persona:
            return ""
        if relationship is None and event is not None:
            profile_id = self._event_profile_id(event, sender_name)
            relationship = await self.archive.get_relationship(profile_id) if profile_id else None
        names = self._relationship_persona_names(relationship, sender_name)
        cache_key = (
            "|".join(names),
            str(hash(persona)),
            self._str_payload(getattr(relationship, "persona_hint", "")) if relationship is not None else "",
        )
        cache = self._get_persona_hint_cache()
        if cache_key in cache:
            return cache[cache_key]
        hint = self._extract_persona_hint_exact(persona, names)
        if not hint:
            is_private_chat = bool(event is not None and not self._event_is_group_message(event))
            has_saved_context = bool(
                relationship is not None
                and any(
                    [
                        self._str_payload(getattr(relationship, "persona_hint", "")),
                        self._str_payload(getattr(relationship, "relationship_story", "")),
                        self._str_payload(getattr(relationship, "subjective_name", "")),
                        getattr(relationship, "subjective_tags", []) or [],
                        getattr(relationship, "notes", []) or [],
                        getattr(relationship, "memory_points", []) or [],
                    ]
                )
            )
            if is_private_chat or has_saved_context:
                hint = await self._extract_persona_hint_by_llm(persona, names, relationship)
        cache[cache_key] = hint
        return hint
