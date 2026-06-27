from __future__ import annotations

from ...models import WeekTemplateRecord
from ...templates import DEFAULT_WEEK_TEMPLATES


class PortalWeekMixin:
    async def page_template_create(self):
        async def handler():
            body = await self._page_json_body()
            description = str(body.get("description") or "").strip()
            if not description:
                raise ValueError("模板描述不能为空")
            template = await self.runtime.composer.compose_week_template_from_text(
                description,
                use_web=self._page_bool(body.get("use_web")),
            )
            self._page_prepare_custom_template(template)
            saved = await self.runtime.archive.save_custom_week_template(template)
            return {"template": saved.as_dict(), "status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_template_save(self):
        async def handler():
            body = await self._page_json_body()
            raw_template = body.get("template")
            if not isinstance(raw_template, dict):
                raise ValueError("模板数据不能为空")
            template = WeekTemplateRecord.from_value(raw_template)
            if not template:
                raise ValueError("模板标识和名称不能为空")
            self._page_prepare_custom_template(template)
            saved = await self.runtime.archive.save_custom_week_template(template)
            return {"template": saved.as_dict(), "status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_template_weight(self):
        async def handler():
            body = await self._page_json_body()
            template_id = self._page_template_id(body)
            weight = self._page_weight(body)
            if not await self.runtime.archive.set_custom_week_template_weight(template_id, weight):
                raise ValueError(f"未找到自定义模板：{template_id}")
            return {"template_id": template_id, "weight": weight}

        return await self._page_json(handler)

    async def page_template_enabled(self):
        async def handler():
            body = await self._page_json_body()
            template_id = self._page_template_id(body)
            enabled = self._page_bool(body.get("enabled"))
            if template_id in DEFAULT_WEEK_TEMPLATES:
                await self.runtime.archive.set_builtin_item_enabled("template", template_id, enabled)
                return {"template_id": template_id, "enabled": enabled}
            if not await self.runtime.archive.set_custom_week_template_enabled(template_id, enabled):
                raise ValueError(f"未找到自定义模板：{template_id}")
            return {"template_id": template_id, "enabled": enabled}

        return await self._page_json(handler)

    async def page_template_delete(self):
        async def handler():
            body = await self._page_json_body()
            template_id = self._page_template_id(body)
            if not await self.runtime.archive.delete_custom_week_template(template_id):
                raise ValueError(f"未找到自定义模板：{template_id}")
            return {"template_id": template_id, "status": await self._build_page_status()}

        return await self._page_json(handler)
