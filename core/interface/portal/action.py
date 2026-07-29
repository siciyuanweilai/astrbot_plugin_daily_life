from __future__ import annotations

from ...clock import now as life_now
from ...runtime.locks import operation_lock


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
                else bool(self.runtime.config.search.inspiration_enabled)
            )
            result = await self.runtime.run_daily_generation(
                date=target_dt,
                source="dashboard_reset",
                force=True,
                extra=extra,
                use_web=use_web,
                search_keyword=extra or "今日生活",
                search_prompt=self.runtime.config.search.today_prompt,
                search_category="今日生活背景",
                reject_if_busy=True,
            )
            day = result.day
            web_inspiration = result.web_inspiration
            return {
                "day": day.as_dict() if day else None,
                "web_inspiration": web_inspiration,
                "operation_id": result.operation_id,
                "status": await self._build_page_status(),
            }

        return await self._page_json(handler)

    async def page_generate_week(self):
        async def handler():
            body = await self._page_json_body()
            goals = str(body.get("goals") or "").strip()
            web_inspiration = ""
            use_web = (
                self._page_bool(body["use_web"])
                if "use_web" in body
                else bool(self.runtime.config.search.inspiration_enabled)
            )
            week = life_now().strftime("%G-W%V")
            async with operation_lock(self.runtime, f"week:{week}"):
                if use_web:
                    web_inspiration = await self.runtime.composer.search.inspiration(
                        goals or "本周计划",
                        self.runtime.config.search.today_prompt,
                        category="周计划",
                        persona=await self.runtime.get_persona_text(),
                        today=life_now().strftime("%Y-%m-%d"),
                    )
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
