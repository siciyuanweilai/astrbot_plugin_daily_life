from __future__ import annotations

from ...models import HairStyleRecord


class PortalHairdoMixin:
    async def page_hair_create(self):
        async def handler():
            body = await self._page_json_body()
            description = str(body.get("description") or "").strip()
            if not description:
                raise ValueError("发型组描述不能为空")
            style = await self.runtime.composer.compose_hair_style_from_text(
                description,
                use_web=self._page_bool(body.get("use_web")),
            )
            style.style_id = ""
            style.source = "custom"
            saved = await self.runtime.archive.save_custom_hair_style(style)
            return {"style": saved.as_dict(), "status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_hair_save(self):
        async def handler():
            body = await self._page_json_body()
            raw_style = body.get("style")
            if not isinstance(raw_style, dict):
                raise ValueError("发型组数据不能为空")
            style = HairStyleRecord.from_value(raw_style)
            if not style:
                raise ValueError("发型组名称和发型列表不能为空")
            self._page_reject_builtin_id(style.style_id, "内置发型组不能直接保存，请先复制成自定义发型组")
            saved = await self.runtime.archive.save_custom_hair_style(style)
            return {"style": saved.as_dict(), "status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_hair_enabled(self):
        async def handler():
            body = await self._page_json_body()
            style_id = self._page_item_id(body, "发型组标识不能为空", key="style_id")
            enabled = self._page_bool(body.get("enabled"))
            if self._page_is_builtin_id(style_id):
                if style_id not in self._page_builtin_hair_ids():
                    raise ValueError(f"未找到内置发型组：{style_id}")
                await self.runtime.archive.set_builtin_item_enabled("hair", style_id, enabled)
                return {"style_id": style_id, "enabled": enabled}
            if not await self.runtime.archive.set_custom_hair_style_enabled(style_id, enabled):
                raise ValueError(f"未找到自定义发型组：{style_id}")
            return {"style_id": style_id, "enabled": enabled}

        return await self._page_json(handler)

    async def page_hair_delete(self):
        async def handler():
            body = await self._page_json_body()
            style_id = self._page_item_id(body, "发型组标识不能为空", key="style_id")
            if not await self.runtime.archive.delete_custom_hair_style(style_id):
                raise ValueError(f"未找到自定义发型组：{style_id}")
            return {"style_id": style_id, "status": await self._build_page_status()}

        return await self._page_json(handler)
