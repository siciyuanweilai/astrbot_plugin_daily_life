import copy
import datetime
import random
import uuid

from ..archive import builtin_entry_id
from ..presets import CATALOG_POOL_LABELS, DEFAULT_CATALOG_POOLS, DEFAULT_STYLE_TO_HAIR_MAP
from ..config.options import CatalogSettings
from ..models import CatalogItemRecord, HairStyleRecord
from ..prompts import cache_friendly_prompt, json_output_section
from .tools import extract_json_from_text


class MaterialMixin:
    async def compose_catalog_item_from_text(
        self,
        category: str,
        instruction: str,
        use_web: bool = False,
    ) -> CatalogItemRecord:
        category = str(category or "").strip()
        text = self._normalize_catalog_text(instruction)
        if category not in DEFAULT_CATALOG_POOLS:
            raise ValueError("素材分类不存在")
        if not text:
            raise ValueError("素材描述不能为空")

        label = CATALOG_POOL_LABELS.get(category, category)
        examples = "、".join(DEFAULT_CATALOG_POOLS.get(category, [])[:8])
        web_section = await self._web_material_inspiration(text, category, use_web=use_web)
        session_id = f"daily_life_catalog_{uuid.uuid4().hex[:8]}"
        fixed = f"""把用户描述整理成一条日常生活素材。

{json_output_section()}

输出结构：{{"text": "一条可直接加入该分类素材库的短句"}}

要求：
- 只生成一条素材，不要编号。
- 文风贴近参考素材，适合日常生活背景生成。
- 不要解释，不要复述分类名。"""
        dynamic = f"""分类：{label} ({category})
参考素材：{examples}
用户描述：{text}
{web_section}"""
        prompt = cache_friendly_prompt(fixed, dynamic, dynamic_title="素材输入")
        try:
            provider_id = self._task_provider_id(self.config.material.provider)
            provider = await self._get_provider(provider_id)
            if provider:
                completion_text = await self._call_llm_text(
                    provider,
                    prompt,
                    session_id,
                    primary_provider_id=provider_id,
                )
                result = extract_json_from_text(completion_text)
                generated = self._normalize_catalog_text(
                    result.get("text") if isinstance(result, dict) else ""
                )
                if generated:
                    return CatalogItemRecord(category=category, text=generated, enabled=True, source="custom")
        finally:
            await self._cleanup_conversation(session_id)
        return CatalogItemRecord(category=category, text=text, enabled=True, source="custom")

    async def compose_hair_style_from_text(self, instruction: str, use_web: bool = False) -> HairStyleRecord:
        text = self._normalize_catalog_text(instruction)
        if not text:
            raise ValueError("发型组描述不能为空")

        examples = "\n".join(
            f"- {name}: {'、'.join(styles[:3])}"
            for name, styles in list(DEFAULT_STYLE_TO_HAIR_MAP.items())[:8]
        )
        web_section = await self._web_outfit_inspiration(text, use_web=use_web)
        session_id = f"daily_life_hair_{uuid.uuid4().hex[:8]}"
        fixed = f"""把用户描述整理成一个发型组。

{json_output_section()}

输出结构：{{"name": "发型组名称", "hairstyles": ["发型1", "发型2", "发型3", "发型4"]}}

要求：
- name 是适合作为穿搭风格映射的短名称。
- hairstyles 生成 4 到 8 个具体长发造型，不能只写风格词。
- 文风贴近参考发型组，适合日常穿搭背景生成。
- 不要解释，不要编号。"""
        dynamic = f"""参考发型组：
{examples}

用户描述：{text}
{web_section}"""
        prompt = cache_friendly_prompt(fixed, dynamic, dynamic_title="发型组输入")
        try:
            provider_id = self._task_provider_id(self.config.material.provider)
            provider = await self._get_provider(provider_id)
            if provider:
                completion_text = await self._call_llm_text(
                    provider,
                    prompt,
                    session_id,
                    primary_provider_id=provider_id,
                )
                style = self._hair_style_from_result(extract_json_from_text(completion_text), text)
                if style:
                    return style
        finally:
            await self._cleanup_conversation(session_id)
        return self._fallback_hair_style(text)

    async def _web_material_inspiration(self, text: str, category: str = "", use_web: bool = False) -> str:
        if not use_web:
            return ""
        summary = await self.web_inspiration.search(
            text,
            self.config.web_inspiration.material_prompt,
            category=CATALOG_POOL_LABELS.get(category, category),
            persona=await self._get_persona(),
        )
        return f"\n\n{summary}" if summary else ""

    async def _web_outfit_inspiration(self, text: str, use_web: bool = False) -> str:
        if not use_web:
            return ""
        summary = await self.web_inspiration.search(
            text,
            self.config.web_inspiration.outfit_prompt,
            category="发型与穿搭",
            persona=await self._get_persona(),
        )
        return f"\n\n{summary}" if summary else ""

    def _hair_style_from_result(self, result: object, fallback_text: str) -> HairStyleRecord | None:
        if not isinstance(result, dict):
            return None
        name = self._normalize_catalog_text(result.get("name") or fallback_text)
        hairstyles = self._normalize_catalog_lines(result.get("hairstyles"))
        if not name or not hairstyles:
            return None
        return HairStyleRecord(
            name=name[:32],
            hairstyles=hairstyles[:10],
            enabled=True,
            source="custom",
        )

    def _fallback_hair_style(self, text: str) -> HairStyleRecord:
        name, raw_styles = self._split_hair_instruction(text)
        name = name or text
        hairstyles = self._normalize_catalog_lines(raw_styles)
        if not hairstyles:
            hairstyles = [f"{name}的低挽长发", f"{name}的半扎长发"]
        return HairStyleRecord(
            name=name[:32],
            hairstyles=hairstyles[:10],
            enabled=True,
            source="custom",
        )

    @staticmethod
    def _normalize_catalog_text(value: object) -> str:
        return " ".join(str(value or "").strip().split())

    @classmethod
    def _normalize_catalog_lines(cls, value: object) -> list[str]:
        if isinstance(value, str):
            parts = value.replace("，", "\n").replace("、", "\n").replace(",", "\n").splitlines()
        elif isinstance(value, list):
            parts = value
        else:
            parts = []
        result = []
        seen = set()
        for item in parts:
            text = cls._normalize_catalog_text(item)
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    @classmethod
    def _split_hair_instruction(cls, text: str) -> tuple[str, object]:
        lines = [line for line in str(text or "").splitlines() if line.strip()]
        if len(lines) > 1:
            return cls._normalize_catalog_text(lines[0]), lines[1:]
        for sep in ("：", ":", "，", ","):
            if sep in text:
                name, body = text.split(sep, 1)
                return cls._normalize_catalog_text(name), body
        return cls._normalize_catalog_text(text), []

    @staticmethod
    def _merge_text_pool(base: list[str], extras: list[str]) -> list[str]:
        result = []
        seen = set()
        for value in [*(base or []), *(extras or [])]:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    async def _get_catalog_settings(self) -> CatalogSettings:
        catalog = copy.deepcopy(self.config.catalog)
        for category in DEFAULT_CATALOG_POOLS:
            states = await self.archive.get_builtin_item_states("catalog", category)
            base = [
                item
                for item in getattr(catalog, category)
                if states.get(builtin_entry_id(item), True)
            ]
            setattr(catalog, category, base)

        hair_states = await self.archive.get_builtin_item_states("hair")
        catalog.style_to_hair_map = {
            name: list(styles)
            for name, styles in catalog.style_to_hair_map.items()
            if hair_states.get(builtin_entry_id(name), True)
        }

        custom_items = await self.archive.get_custom_catalog_items()
        for category, records in custom_items.items():
            extra = [item.text for item in records if item.enabled and item.text]
            setattr(catalog, category, self._merge_text_pool(getattr(catalog, category), extra))

        custom_styles = await self.archive.get_custom_hair_styles()
        for style in custom_styles.values():
            if style.enabled and style.name and style.hairstyles:
                catalog.style_to_hair_map[style.name] = self._merge_text_pool([], style.hairstyles)
        return catalog

    async def _pick_unique_meta_item(self, pool: list[str], current_date: datetime.date, meta_key: str) -> str:
        pool = list(pool or [])
        if not pool:
            return "默认"
        if len(pool) <= 1:
            return pool[0]

        lookback_days = self.config.reference_history_days
        if lookback_days <= 0:
            return random.choice(pool)

        used_items = set()
        for i in range(1, lookback_days + 1):
            date_str = (current_date - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            data = await self.archive.get_day(date_str)
            if not data:
                continue
            val = data.meta.get(meta_key, "")
            if val:
                used_items.add(val)

        available = [item for item in pool if item not in used_items]
        return random.choice(available or pool)
