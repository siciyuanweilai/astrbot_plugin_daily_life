from __future__ import annotations

from ...models import CatalogItemRecord


class PortalPoolMixin:
    async def page_catalog_create(self):
        async def handler():
            body = await self._page_json_body()
            category = self._page_catalog_category(body)
            description = str(body.get("description") or "").strip()
            if not description:
                raise ValueError("素材描述不能为空")
            item = await self.runtime.composer.compose_catalog_item_from_text(
                category,
                description,
                use_web=self._page_bool(body.get("use_web")),
            )
            item.category = category
            item.item_id = ""
            item.source = "custom"
            saved = await self.runtime.archive.save_custom_catalog_item(item)
            return {"item": saved.as_dict(), "status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_catalog_save(self):
        async def handler():
            body = await self._page_json_body()
            raw_item = body.get("item")
            if not isinstance(raw_item, dict):
                raise ValueError("素材数据不能为空")
            item = CatalogItemRecord.from_value(raw_item)
            if not item:
                raise ValueError("素材分类和内容不能为空")
            self._page_reject_builtin_id(item.item_id, "内置素材不能直接保存，请先复制成自定义素材")
            saved = await self.runtime.archive.save_custom_catalog_item(item)
            return {"item": saved.as_dict(), "status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_catalog_enabled(self):
        async def handler():
            body = await self._page_json_body()
            category = self._page_catalog_category(body)
            item_id = self._page_item_id(body, "素材标识不能为空")
            enabled = self._page_bool(body.get("enabled"))
            if self._page_is_builtin_id(item_id):
                if item_id not in self._page_builtin_catalog_ids(category):
                    raise ValueError(f"未找到内置素材：{item_id}")
                await self.runtime.archive.set_builtin_item_enabled("catalog", item_id, enabled, scope=category)
                return {"category": category, "item_id": item_id, "enabled": enabled}
            if not await self.runtime.archive.set_custom_catalog_item_enabled(category, item_id, enabled):
                raise ValueError(f"未找到自定义素材：{item_id}")
            return {"category": category, "item_id": item_id, "enabled": enabled}

        return await self._page_json(handler)

    async def page_catalog_delete(self):
        async def handler():
            body = await self._page_json_body()
            category = self._page_catalog_category(body)
            item_id = self._page_item_id(body, "素材标识不能为空")
            if not await self.runtime.archive.delete_custom_catalog_item(category, item_id):
                raise ValueError(f"未找到自定义素材：{item_id}")
            return {"category": category, "item_id": item_id, "status": await self._build_page_status()}

        return await self._page_json(handler)
