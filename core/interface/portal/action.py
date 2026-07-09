from __future__ import annotations

from ...clock import now as life_now


class PortalActionMixin:
    async def page_refresh_state(self):
        async def handler():
            now = life_now()
            target_date, _ = await self.runtime.resolve_injection_target(now)
            await self.runtime.refresh_state_for_day(
                target_date,
                now=now,
                source="dashboard",
                detail="面板手动刷新",
                force=True,
            )
            return {"status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_reset_day(self):
        async def handler():
            body = await self._page_json_body()
            now = life_now()
            target_date, _ = await self.runtime.resolve_injection_target(now)
            target_dt = self.runtime._target_datetime_for_command(target_date, now)
            extra = str(body.get("extra") or "").strip()
            web_inspiration = ""
            use_web = (
                self._page_bool(body["use_web"])
                if "use_web" in body
                else bool(self.runtime.config.web_inspiration.enabled)
            )
            if use_web:
                web_inspiration = await self.runtime.composer.web_inspiration.search(
                    extra or "今日生活",
                    self.runtime.config.web_inspiration.today_prompt,
                    category="今日生活背景",
                    persona=await self.runtime.composer._get_persona(),
                    today=target_date,
                )
            async with self.runtime.generation_lock:
                day = await self.runtime.composer.generate_daily(
                    date=target_dt,
                    force=True,
                    extra=extra,
                    web_inspiration=web_inspiration,
                )
            return {
                "day": day.as_dict() if day else None,
                "web_inspiration": web_inspiration,
                "status": await self._build_page_status(),
            }

        return await self._page_json(handler)

    async def page_generate_week(self):
        async def handler():
            body = await self._page_json_body()
            goals = str(body.get("goals") or "").strip()
            web_inspiration = ""
            if self._page_bool(body.get("use_web")):
                web_inspiration = await self.runtime.composer.web_inspiration.search(
                    goals or "本周计划",
                    self.runtime.config.web_inspiration.today_prompt,
                    category="周计划",
                    persona=await self.runtime.composer._get_persona(),
                    today=life_now().strftime("%Y-%m-%d"),
                )
            async with self.runtime.generation_lock:
                plan = await self.runtime.composer.generate_week_plan(
                    goals,
                    web_inspiration=web_inspiration,
                )
            return {
                "week_plan": self._page_week_plan(plan) if plan else None,
                "web_inspiration": web_inspiration,
                "status": await self._build_page_status(),
            }

        return await self._page_json(handler)
