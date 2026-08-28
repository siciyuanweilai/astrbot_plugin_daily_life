from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from ..prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from .tools import extract_json_from_text

_RESIDENCE_KINDS = frozenset({"real", "fictional", "unknown"})
_RESIDENCE_PRECISIONS = frozenset({"address", "district", "city", "none"})
_MAP_PRECISIONS = frozenset({"address", "district"})


@dataclass(frozen=True, slots=True)
class PersonaResidence:
    kind: str = "unknown"
    city: str = ""
    address: str = ""
    precision: str = "none"
    confidence: float = 0.0
    reason: str = ""
    persona_hash: str = ""

    @property
    def weather_available(self) -> bool:
        return self.kind == "real" and bool(self.city) and self.confidence >= 0.6

    @property
    def map_available(self) -> bool:
        return (
            self.kind == "real"
            and self.precision in _MAP_PRECISIONS
            and bool(self.city and self.address)
            and self.confidence >= 0.72
        )


class PersonaResidenceResolver:
    """从全局默认人设提取经过边界约束的居住地事实。"""

    def __init__(self, composer: Any, *, provider_id: str = ""):
        self.composer = composer
        self.provider_id = str(provider_id or "").strip()
        self._cache: dict[str, PersonaResidence] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit].strip()

    @staticmethod
    def _persona_signature(persona: str) -> str:
        normalized = " ".join(str(persona or "").split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _unknown(persona_hash: str = "", reason: str = "") -> PersonaResidence:
        return PersonaResidence(
            kind="unknown",
            reason=str(reason or "").strip()[:120],
            persona_hash=persona_hash,
        )

    def _normalize(self, payload: Any, persona_hash: str) -> PersonaResidence:
        if not isinstance(payload, dict):
            return self._unknown(persona_hash, "模型没有返回有效对象")
        kind = self._text(payload.get("kind"), 16).lower()
        precision = self._text(payload.get("precision"), 16).lower()
        if kind not in _RESIDENCE_KINDS:
            kind = "unknown"
        if precision not in _RESIDENCE_PRECISIONS:
            precision = "none"
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        city = self._text(payload.get("city"), 64)
        address = self._text(payload.get("address"), 180)
        reason = self._text(payload.get("reason"), 120)
        if kind != "real":
            city = ""
            address = ""
            precision = "none"
        elif not city:
            kind = "unknown"
            address = ""
            precision = "none"
            confidence = min(confidence, 0.5)
        elif precision in _MAP_PRECISIONS and not address:
            precision = "city"
        return PersonaResidence(
            kind=kind,
            city=city,
            address=address,
            precision=precision,
            confidence=confidence,
            reason=reason,
            persona_hash=persona_hash,
        )

    @staticmethod
    def _prompt(persona: str) -> str:
        fixed = f"""判断当前角色人设是否明确给出了长期居住地，并提取可用于天气或地图的现实地理信息。

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON 对象：
{{
  "kind": "real|fictional|unknown",
  "city": "现实城市名称；没有则空字符串",
  "address": "人设明确支持、可交给地图定位的居住地；没有则空字符串",
  "precision": "address|district|city|none",
  "confidence": 0.0,
  "reason": "一句话说明判断依据"
}}

规则：
- 只判断角色长期或当前居住地，不把出生地、旅行地、剧情发生地、工作地或向往地点当成居住地。
- 现实世界中确实存在的城市、区县和地址才是 real；虚构世界、虚构城市、架空地名和明显属于作品设定的地点是 fictional。
- 地点是否真实无法可靠确认、信息相互冲突、只有模糊方位或没有居住事实时，必须是 unknown。
- 不得根据角色姓名、语言、国籍、职业、服饰、作品风格或常识猜测所在地。
- precision=address 需要街道、小区、建筑或门牌等明确地址；district 需要现实区县；只有城市时用 city。
- address 只能整理人设已有地点信息，不补造街道、门牌、区县或城市。
- kind 不是 real 时 city 和 address 必须为空，precision 必须为 none。"""
        return cache_friendly_prompt(
            fixed,
            persona,
            dynamic_title="当前角色人设",
        )

    async def resolve(self) -> PersonaResidence:
        getter = getattr(self.composer, "_get_persona", None)
        if not callable(getter):
            return self._unknown(reason="无法读取默认人设")
        try:
            persona = str(await getter() or "").strip()
        except Exception as exc:
            logger.debug(f"[日常生活] 读取居住地人设失败：{exc}")
            return self._unknown(reason="无法读取默认人设")
        persona_hash = self._persona_signature(persona)
        if not persona or persona == "一个热爱生活的人":
            return self._unknown(persona_hash, "人设没有提供居住地")
        cached = self._cache.get(persona_hash)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._cache.get(persona_hash)
            if cached is not None:
                return cached
            provider_getter = getattr(self.composer, "_get_provider", None)
            caller = getattr(self.composer, "_call_llm_text", None)
            cleaner = getattr(self.composer, "_cleanup_conversation", None)
            if not callable(provider_getter) or not callable(caller):
                return self._unknown(persona_hash, "没有可用的居住地解析模型")
            session_id = f"daily_life_residence_{uuid.uuid4().hex[:8]}"
            try:
                provider = await provider_getter(self.provider_id)
                if provider is None:
                    result = self._unknown(persona_hash, "没有可用的居住地解析模型")
                else:
                    text = await caller(
                        provider,
                        self._prompt(persona),
                        session_id,
                        empty_retries=0,
                        primary_provider_id=self.provider_id,
                        timeout_seconds=30,
                    )
                    result = self._normalize(extract_json_from_text(text), persona_hash)
            except Exception as exc:
                logger.warning(f"[日常生活] 从人设解析居住地失败：{exc}")
                result = self._unknown(persona_hash, "居住地解析失败")
            finally:
                if callable(cleaner):
                    await cleaner(session_id)
            self._cache = {persona_hash: result}
            return result


__all__ = ["PersonaResidence", "PersonaResidenceResolver"]
