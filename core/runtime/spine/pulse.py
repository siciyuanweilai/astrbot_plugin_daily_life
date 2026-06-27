from __future__ import annotations

import datetime

from astrbot.api import logger

from ...clock import now as life_now
from ...models import MemoryMaintenanceRecord
from ..markers import LOG_PREFIX


class SpinePulseMixin:
    async def run_daily_refresh(self) -> None:
        logger.info(f"{LOG_PREFIX} 正在执行每日日常生活背景刷新……")
        async with self.generation_lock:
            yesterday = (life_now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            await self.composer.compose_daily_review(yesterday)
            await self.composer.generate_daily(force=True)
            await self.mark_page_status_changed("daily_refresh")
        await self.archive.cleanup_by_storage_policy(self.config.storage)
        await self.maintain_emoji_assets()

    async def run_nightly_review(self) -> None:
        logger.info(f"{LOG_PREFIX} 正在执行夜间复盘与记忆沉淀……")
        async with self.generation_lock:
            await self.composer.compose_daily_review()
            await self.run_memory_maintenance()
            await self.mark_page_status_changed("nightly_review")

    async def run_memory_maintenance(self) -> None:
        today_str = life_now().strftime("%Y-%m-%d")
        await self._settle_stale_reply_effects()
        corrections = await self.archive.get_memory_corrections(limit=100)
        reply_effects = await self.archive.get_reply_effects(limit=100)
        emoji_assets = await self.archive.get_emoji_assets(limit=100)
        applied_count = sum(1 for item in corrections if bool(getattr(item, "applied", False)))
        pending_count = sum(1 for item in corrections if not bool(getattr(item, "applied", False)))
        pending_reply_count = sum(
            1 for item in reply_effects if str(getattr(item, "outcome", "") or "") == "pending"
        )
        ready_emoji_count = sum(
            1 for item in emoji_assets if str(getattr(item, "status", "") or "") == "ready"
        )
        summary = (
            f"已应用记忆纠错 {applied_count} 条，未应用纠错 {pending_count} 条；"
            f"待观察回复效果 {pending_reply_count} 条；可用表情素材 {ready_emoji_count} 个。"
        )
        saved = await self.archive.save_memory_maintenance(
            MemoryMaintenanceRecord(
                date=today_str,
                summary=summary,
                corrected_count=applied_count,
                reason="夜间复盘后自动检查长期记忆、回复效果与表达素材。",
            )
        )
        await self.archive.cleanup_by_storage_policy(self.config.storage)
        await self.maintain_emoji_assets()
        if saved:
            logger.info(f"{LOG_PREFIX} 长期记忆维护完成：{summary}")

    async def run_weekly_refresh(self) -> None:
        logger.info(f"{LOG_PREFIX} 正在执行每周生活主题刷新……")
        async with self.generation_lock:
            await self.composer.generate_week_plan()
            await self.mark_page_status_changed("weekly_refresh")

    async def run_private_revisit_check(self) -> None:
        if not self.config.proactive.enabled or not self.config.proactive.private_revisit_enabled:
            return
        await self.evaluate_private_revisit_candidates()

    async def run_proactive_idle_check(self) -> None:
        if not self.config.proactive.enabled:
            return
        await self.evaluate_idle_proactive_candidates()
