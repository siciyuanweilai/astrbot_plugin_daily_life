from __future__ import annotations

import hashlib
import inspect
from typing import Any

from astrbot.api import logger

from ...prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX
from .lens import clean_director_text


class StageVisualMixin:
    def _media_visual_cache(self) -> dict[str, str]:
        cache = getattr(self, "_media_visual_profile_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._media_visual_profile_cache = cache
        return cache

    async def _media_current_persona_text(self, event: Any = None) -> str:
        composer = getattr(self, "composer", None)
        getter = getattr(composer, "_get_persona", None)
        if not callable(getter):
            return ""
        scope = ""
        resolver = getattr(self, "_event_session_id", None)
        if callable(resolver):
            try:
                scope = str(resolver(event) or "").strip()
            except Exception:
                scope = ""
        try:
            persona = getter(scope) if scope else getter()
        except TypeError:
            persona = getter()
        if inspect.isawaitable(persona):
            persona = await persona
        return str(persona or "").strip()

    @staticmethod
    def _media_visual_list(value: Any, limit: int = 6) -> list[str]:
        if isinstance(value, (list, tuple)):
            values = value
        elif value:
            values = [value]
        else:
            values = []
        result: list[str] = []
        seen = set()
        for item in values:
            text = clean_director_text(item, 80)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
            if len(result) >= limit:
                break
        return result

    def _media_visual_profile_from_payload(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        fields = [
            ("概要", clean_director_text(payload.get("appearance_summary"), 180)),
            ("脸部", clean_director_text(payload.get("face"), 140)),
            ("发型", clean_director_text(payload.get("hair"), 140)),
            ("体态", clean_director_text(payload.get("body"), 120)),
            ("风格", clean_director_text(payload.get("style"), 140)),
        ]
        lines = [f"{label}：{value}" for label, value in fields if value]
        identifiers = self._media_visual_list(payload.get("identifiers"))
        if identifiers:
            lines.append(f"辨识点：{'；'.join(identifiers)}")
        constraints = self._media_visual_list(payload.get("constraints"))
        if constraints:
            lines.append(f"稳定约束：{'；'.join(constraints)}")
        return "\n".join(lines)

    async def _media_director_visual_context_text(self, event: Any = None) -> str:
        persona = await self._media_current_persona_text(event)
        if not persona:
            return ""
        key = hashlib.sha1(persona.encode("utf-8", "ignore")).hexdigest()
        cache = self._media_visual_cache()
        if key in cache:
            return cache[key]

        fixed = f"""你是角色视觉设定提取器。请只从当前人设文本中提取稳定、长期、可用于图片生成保持一致性的视觉设定。
要求：
- 只提取人设明确写出的角色本人外貌、发型、体态、气质和视觉辨识点。
- 不提取当天穿搭、临时动作、剧情、口癖、关系称呼或普通性格说明。
- 人设没有明确视觉信息时，对应字段留空；不要根据名字、年龄、性格、头像或刻板印象推断。
- 输出短句，中文，供图片导演内部参考，不是给用户看的文案。
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"appearance_summary":"","face":"","hair":"","body":"","style":"","identifiers":[],"constraints":[]}}"""
        dynamic = f"当前人设文本：\n{persona}"
        try:
            payload = await self._media_director_call(
                cache_friendly_prompt(fixed, dynamic, dynamic_title="角色视觉设定提取")
            )
            summary = self._media_visual_profile_from_payload(payload)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 角色视觉设定提取失败：{exc}")
            summary = ""
        cache[key] = summary
        return summary
