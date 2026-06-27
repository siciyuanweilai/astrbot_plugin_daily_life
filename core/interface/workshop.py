from typing import Any

from ..archive import builtin_entry_id
from ..presets import (
    CATALOG_POOL_LABELS,
    DEFAULT_CATALOG_POOLS,
    DEFAULT_STYLE_TO_HAIR_MAP,
)
from ..models import WeekTemplateRecord
from ..templates import DEFAULT_WEEK_TEMPLATES


class PageWorkshopMixin:
    @staticmethod
    def _page_template_id(body: dict[str, Any]) -> str:
        template_id = str(body.get("template_id") or "").strip()
        if not template_id:
            raise ValueError("模板标识不能为空")
        return template_id

    @staticmethod
    def _page_catalog_category(body: dict[str, Any]) -> str:
        category = str(body.get("category") or "").strip()
        if category not in DEFAULT_CATALOG_POOLS:
            raise ValueError("素材分类不存在")
        return category

    @staticmethod
    def _page_item_id(body: dict[str, Any], message: str, key: str = "item_id") -> str:
        item_id = str(body.get(key) or "").strip()
        if not item_id:
            raise ValueError(message)
        return item_id

    @staticmethod
    def _page_valid_template_id(template_id: str) -> bool:
        if not template_id or len(template_id) > 40:
            return False
        return all(ch == "_" or ch.isdigit() or ("a" <= ch <= "z") for ch in template_id)

    @staticmethod
    def _page_reject_builtin_id(item_id: str, message: str) -> None:
        if PageWorkshopMixin._page_is_builtin_id(item_id):
            raise ValueError(message)

    @staticmethod
    def _page_is_builtin_id(item_id: str) -> bool:
        return str(item_id or "").strip().startswith("builtin_")

    @staticmethod
    def _page_builtin_catalog_ids(category: str) -> set[str]:
        return {
            builtin_entry_id(value)
            for value in DEFAULT_CATALOG_POOLS.get(category, [])
            if str(value or "").strip()
        }

    @staticmethod
    def _page_builtin_hair_ids() -> set[str]:
        return {builtin_entry_id(name) for name in DEFAULT_STYLE_TO_HAIR_MAP if str(name or "").strip()}

    def _page_prepare_custom_template(self, template: WeekTemplateRecord) -> None:
        if not self._page_valid_template_id(template.template_id):
            raise ValueError("模板标识只能包含小写字母、数字和下划线")
        if template.template_id in DEFAULT_WEEK_TEMPLATES:
            raise ValueError("内置周模板不能直接保存，请先复制成自定义周模板")
        template.source = "custom"

    @staticmethod
    def _page_weight(body: dict[str, Any]) -> float:
        value = body.get("weight")
        try:
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            raise ValueError("自定义模板权重必须是数字")

    @staticmethod
    def _page_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "enable", "enabled", "启用", "开启", "是"}

    async def _page_templates(self) -> list[dict]:
        templates = await self.runtime.composer._get_week_templates(include_disabled=True)
        custom = await self.runtime.archive.get_custom_week_templates(include_disabled=True)
        items = []
        for template_id, template in templates.items():
            item = dict(template)
            item["template_id"] = template_id
            item["custom"] = template_id in custom
            item["editable"] = item["custom"]
            item["enabled"] = bool(item.get("enabled", True))
            item["weight"] = float(
                item.get("weight", self.runtime.config.week_template_weights.get(template_id, 0.1)) or 0.0
            )
            items.append(item)
        items.sort(key=lambda item: (not item["custom"], item.get("name", "")))
        return items

    async def _page_catalog(self) -> dict:
        custom_items = await self.runtime.archive.get_custom_catalog_items(include_disabled=True)
        pools = []
        for category, defaults in DEFAULT_CATALOG_POOLS.items():
            states = await self.runtime.archive.get_builtin_item_states("catalog", category)
            items = []
            for index, value in enumerate(defaults):
                text = str(value or "").strip()
                if text:
                    item_id = builtin_entry_id(text)
                    items.append(
                        {
                            "category": category,
                            "item_id": item_id,
                            "text": text,
                            "enabled": states.get(item_id, True),
                            "custom": False,
                            "editable": False,
                            "source": "builtin",
                            "sort_order": index,
                        }
                    )
            for item in custom_items.get(category, []):
                data = item.as_dict()
                data.update({"custom": True, "editable": True})
                items.append(data)
            pools.append(
                {
                    "category": category,
                    "label": CATALOG_POOL_LABELS.get(category, category),
                    "items": items,
                    "custom_count": sum(1 for item in items if item.get("custom")),
                }
            )

        custom_styles = await self.runtime.archive.get_custom_hair_styles(include_disabled=True)
        hair_states = await self.runtime.archive.get_builtin_item_states("hair")
        hair_styles = []
        for index, (name, hairstyles) in enumerate(DEFAULT_STYLE_TO_HAIR_MAP.items()):
            style_id = builtin_entry_id(name)
            hair_styles.append(
                {
                    "style_id": style_id,
                    "name": name,
                    "hairstyles": list(hairstyles),
                    "enabled": hair_states.get(style_id, True),
                    "custom": False,
                    "editable": False,
                    "source": "builtin",
                    "sort_order": index,
                }
            )
        for style in custom_styles.values():
            data = style.as_dict()
            data.update({"custom": True, "editable": True})
            hair_styles.append(data)

        return {
            "pools": pools,
            "hair_styles": hair_styles,
        }
