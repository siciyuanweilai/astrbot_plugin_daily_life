from __future__ import annotations

import datetime

from astrbot.api import logger

from ...clock import now as life_now
from ...models import BehaviorSceneRecord, MemoryMaintenanceRecord
from ..markers import LOG_PREFIX


class SpinePulseMixin:
    async def run_daily_refresh(self) -> None:
        logger.info(f"{LOG_PREFIX} 正在执行每日日常生活背景刷新……")
        now = life_now()
        async with self.generation_lock:
            target_date, _ = await self.resolve_injection_target(now)
            target_dt = self._target_datetime_for_command(target_date, now)
            yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            await self.composer.compose_daily_review(yesterday)
            await self.composer.generate_daily(date=target_dt, force=True)
            await self.mark_page_status_changed("daily_refresh")
        await self.archive.cleanup_by_storage_policy(self.config.storage)
        await self.maintain_sight_cache()
        await self.maintain_emoji_assets()
        await self.maintain_plugin_file_cache()

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
        proactive_scene_count = await self._consolidate_proactive_behavior_scenes(today_str)
        applied_count = sum(1 for item in corrections if bool(getattr(item, "applied", False)))
        pending_count = sum(1 for item in corrections if not bool(getattr(item, "applied", False)))
        pending_reply_count = sum(
            1 for item in reply_effects if str(getattr(item, "outcome", "") or "") == "pending"
        )
        ready_emoji_count = sum(
            1
            for item in emoji_assets
            if str(getattr(item, "status", "") or "") == "ready" and getattr(item, "sendable", True)
        )
        summary = (
            f"已应用记忆纠错 {applied_count} 条，未应用纠错 {pending_count} 条；"
            f"待观察回复效果 {pending_reply_count} 条；可用表情素材 {ready_emoji_count} 个；"
            f"行为经验归纳 {proactive_scene_count} 组。"
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
        await self.maintain_sight_cache()
        await self.maintain_emoji_assets()
        await self.maintain_plugin_file_cache()
        if saved:
            logger.info(f"{LOG_PREFIX} 长期记忆维护完成：{summary}")

    async def _consolidate_proactive_behavior_scenes(self, date_str: str) -> int:
        feedback_getter = getattr(self.archive, "get_behavior_feedback", None)
        scene_getter = getattr(self.archive, "get_behavior_scenes", None)
        scene_upserter = getattr(self.archive, "upsert_behavior_scene", None)
        if not callable(feedback_getter) or not callable(scene_getter) or not callable(scene_upserter):
            return 0
        try:
            feedback_items = await feedback_getter(limit=120)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时回复行为经验读取失败：{exc}")
            return 0

        groups: dict[str, list[object]] = {}
        for item in feedback_items:
            if str(getattr(item, "source", "") or "") != "proactive_reply":
                continue
            if str(getattr(item, "scene", "") or "") != "闲时回复读空气":
                continue
            if str(getattr(item, "date", "") or "") != date_str:
                continue
            scope = str(getattr(item, "target_id", "") or "").strip()
            if scope:
                groups.setdefault(scope, []).append(item)

        consolidated = 0
        for scope, items in groups.items():
            existing = await scene_getter(limit=20, scope=scope)
            if any(
                str(getattr(item, "scene", "") or "") == "闲时回复读空气"
                and str(getattr(item, "source", "") or "") == "proactive_feedback"
                and str(getattr(item, "last_seen", "") or "") == date_str
                for item in existing
            ):
                continue
            positives = sum(1 for item in items if str(getattr(item, "result", "") or "") == "positive")
            ignored = sum(1 for item in items if str(getattr(item, "result", "") or "") == "ignored")
            if positives <= 0 and ignored <= 0:
                continue
            preferred_action = "reply" if positives >= ignored else "observe"
            avoid_action = "reply" if ignored > positives else ""
            hint = (
                "这段会话里闲时续话更容易被接住"
                if positives >= ignored
                else "这段会话里闲时续话更容易冷掉，先观察更自然"
            )
            saved = await scene_upserter(
                BehaviorSceneRecord(
                    scope=scope,
                    scene="闲时回复读空气",
                    cues=[f"正反馈 {positives} 次", f"冷反馈 {ignored} 次"],
                    preferred_action=preferred_action,
                    avoid_action=avoid_action,
                    outcome_hint=hint,
                    confidence=min(1.0, 0.45 + min(len(items), 6) * 0.08),
                    support_count=max(1, len(items)),
                    last_seen=date_str,
                    source="proactive_feedback",
                )
            )
            if saved:
                consolidated += 1
        return consolidated

    async def run_weekly_refresh(self) -> None:
        logger.info(f"{LOG_PREFIX} 正在执行周计划刷新……")
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
