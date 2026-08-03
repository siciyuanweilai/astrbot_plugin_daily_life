from __future__ import annotations

import datetime
import math
from typing import Any

from astrbot.api import logger

from ..life.condition import state_is_stale
from ..life.tools import (
    get_current_timeline_status,
    reconcile_timeline_execution,
    timeline_item_datetime,
)
from ..models import DayRecord
from .locks import operation_lock
from .markers import LOG_PREFIX


class RefreshMixin:
    async def _settle_timeline_planning(
        self,
        data: DayRecord,
        now: datetime.datetime,
    ) -> bool:
        """刷新近期锚点并结算已完成的显式生活动作。

        Args:
            data: 已完成时间轴时钟校准的当日日记录。
            now: 当前巡检时间。

        Returns:
            近期锚点或动作结算是否改变了日记录。
        """

        before = (
            str((data.meta or {}).get("near_term_anchors") or ""),
            str((data.meta or {}).get("life_action_settlements") or ""),
            str((data.meta or {}).get("life_action_expirations") or ""),
        )
        refine = getattr(self.composer, "refine_upcoming_anchors", None)
        if callable(refine):
            refine(data, now=now)
        sync_sessions = getattr(
            getattr(self, "domains", None), "sync_activity_sessions", None
        )
        if callable(sync_sessions):
            await sync_sessions(data, now=now)
        settle = getattr(self.composer, "settle_completed_planned_actions", None)
        if callable(settle):
            await settle(data, now=now)
        if callable(sync_sessions):
            await sync_sessions(data, now=now)
        after = (
            str((data.meta or {}).get("near_term_anchors") or ""),
            str((data.meta or {}).get("life_action_settlements") or ""),
            str((data.meta or {}).get("life_action_expirations") or ""),
        )
        return before != after

    @staticmethod
    def _meta_datetime(value: Any) -> datetime.datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return None

    def _auto_life_check_due(self, data: DayRecord, now: datetime.datetime) -> bool:
        next_check = self._meta_datetime(
            (data.meta or {}).get("auto_life_next_check_at", "")
        )
        if next_check is not None:
            return now >= next_check
        interval = max(5, int(self.config.state.refresh_minutes or 30))
        checked_at = (data.meta or {}).get("auto_life_last_checked_at", "")
        if not checked_at:
            return True
        last = self._meta_datetime(checked_at)
        if last is None:
            return True
        return (now - last).total_seconds() >= interval * 60

    @staticmethod
    def _state_stability_signature(data: DayRecord) -> tuple:
        state = data.state
        if state is None:
            return ()

        def bucket(value: Any) -> int | None:
            try:
                return int(round(float(value) / 10.0))
            except (TypeError, ValueError):
                return None

        numeric_fields = (
            "energy",
            "mood_score",
            "busyness",
            "social",
            "stress",
            "focus",
            "sleepiness",
            "outgoing",
            "interaction_capacity",
            "boredom",
            "attention_openness",
        )
        sleep = getattr(state, "sleep", None)
        return (
            *(bucket(getattr(state, field, None)) for field in numeric_fields),
            str(getattr(state, "watch_state", "") or ""),
            str(getattr(state, "interrupt_level", "") or ""),
            str(getattr(sleep, "depth", "") or ""),
        )

    def _outfit_context_signature(
        self, data: DayRecord, now: datetime.datetime, period: str
    ) -> str:
        current, next_item = get_current_timeline_status(data.timeline, now, data.date)
        weather = data.weather_info
        state = data.state

        def item_text(item: Any) -> str:
            if item is None:
                return ""
            return f"{getattr(item, 'time', '')}:{getattr(item, 'activity', '')}"

        try:
            temperature_bucket = (
                round(float(weather.temp) / 3) if weather.temp is not None else ""
            )
        except (TypeError, ValueError):
            temperature_bucket = ""
        try:
            outgoing_bucket = round(float(state.outgoing) / 20) if state else ""
        except (TypeError, ValueError):
            outgoing_bucket = ""
        sleep = getattr(state, "sleep", None) if state else None
        values = (
            data.date,
            period,
            item_text(current),
            item_text(next_item),
            str(weather.condition or ""),
            str(temperature_bucket),
            str(outgoing_bucket),
            str(getattr(sleep, "depth", "") or ""),
        )
        return "|".join(values)

    def _next_auto_life_check_at(
        self,
        data: DayRecord,
        now: datetime.datetime,
        *,
        stable_checks: int,
        stable: bool,
    ) -> datetime.datetime:
        base = max(5, int(self.config.state.refresh_minutes or 30))
        multiplier = min(3, stable_checks + 1) if stable else 1
        delay_minutes = min(90, base * multiplier)
        _, next_item = get_current_timeline_status(data.timeline, now, data.date)
        next_transition = timeline_item_datetime(next_item, data.date)
        if next_transition is not None and next_transition > now:
            transition_minutes = max(
                1, math.ceil((next_transition - now).total_seconds() / 60)
            )
            delay_minutes = min(delay_minutes, transition_minutes)
        return now + datetime.timedelta(minutes=delay_minutes)

    def _state_refresh_in_quiet_hours(self, now: datetime.datetime) -> bool:
        quiet_hours = str(getattr(self.config.state, "quiet_hours", "") or "").strip()
        if not quiet_hours:
            return False
        start_text, sep, end_text = quiet_hours.partition("-")
        if not sep:
            return False
        try:
            start_hour, start_minute = map(int, start_text.split(":", 1))
            end_hour, end_minute = map(int, end_text.split(":", 1))
        except (TypeError, ValueError):
            return False
        current = now.hour * 60 + now.minute
        start = start_hour * 60 + start_minute
        end = end_hour * 60 + end_minute
        if start < end:
            return start <= current < end
        if start > end:
            return current >= start or current < end
        return False

    async def _run_autonomous_life_check(
        self,
        target_date_str: str,
        now: datetime.datetime,
        *,
        source: str,
        detail: str,
        status_reason: str,
        respect_quiet_hours: bool = True,
        update_weather: bool = True,
        log_trigger: str = "",
        source_event: Any = None,
    ) -> DayRecord | None:
        if not self.config.state.enabled:
            return await self.archive.get_day(target_date_str)
        if source_event is not None and self.event_was_recalled(
            source_event, log_skip=True
        ):
            return await self.archive.get_day(target_date_str)
        if respect_quiet_hours and self._state_refresh_in_quiet_hours(now):
            logger.debug(
                f"{LOG_PREFIX} 实时状态巡检处于静默时段 {self.config.state.quiet_hours}，跳过本次巡检"
            )
            return await self.archive.get_day(target_date_str)
        if update_weather:
            if source_event is not None and self.event_was_recalled(
                source_event, log_skip=True
            ):
                return await self.archive.get_day(target_date_str)
            await self.try_update_weather(target_date_str)

        data = await self.archive.get_day(target_date_str)
        if not data:
            return None
        execution_source = {
            "auto": "后台巡检",
            "chat": "聊天触发",
        }.get(source, source or "生活巡检")
        execution_changed = reconcile_timeline_execution(
            data.timeline, now, data.date, evidence=f"{execution_source}：时间轴时钟"
        )
        current_period = self._get_curr_period(now)
        outfit_context_changed = str(
            (data.meta or {}).get("auto_outfit_context", "") or ""
        ) != self._outfit_context_signature(data, now, current_period)
        if (
            not self._auto_life_check_due(data, now)
            and not execution_changed
            and not outfit_context_changed
        ):
            return data

        async with operation_lock(self, f"state:{target_date_str}"):
            if source_event is not None and self.event_was_recalled(
                source_event, log_skip=True
            ):
                return await self.archive.get_day(target_date_str)
            data = await self.archive.get_day(target_date_str)
            if not data:
                return data
            execution_changed = reconcile_timeline_execution(
                data.timeline,
                now,
                data.date,
                evidence=f"{execution_source}：时间轴时钟",
            )
            planning_changed = await self._settle_timeline_planning(data, now)
            if execution_changed or planning_changed:
                await self.archive.save_day(data)
            state_due = self._auto_life_check_due(data, now)
            state_changed = False
            if state_due:
                if log_trigger:
                    logger.debug(
                        f"{LOG_PREFIX} 触发大语言模型自主生活状态/穿搭检查：{log_trigger}"
                    )
                state_before = self._state_stability_signature(data)
                state_kwargs: dict[str, Any] = {
                    "source": source,
                    "detail": detail,
                    "force": False,
                    "notify_page": False,
                }
                if source_event is not None:
                    state_kwargs["source_event"] = source_event
                refreshed = await self.refresh_state_for_day(
                    target_date_str, data, now, **state_kwargs
                )
                data = refreshed or data
                state_changed = self._state_stability_signature(data) != state_before
            if source_event is not None and self.event_was_recalled(
                source_event, log_skip=True
            ):
                return data
            current_period = self._get_curr_period(now)
            previous_outfit_context = str(
                (data.meta or {}).get("auto_outfit_context", "") or ""
            )
            current_outfit_context = self._outfit_context_signature(
                data, now, current_period
            )
            outfit_context_changed = (
                not previous_outfit_context
                or previous_outfit_context != current_outfit_context
            )
            outfit_changed = False
            if outfit_context_changed:
                outfit_before = (data.outfit, data.time_period)
                outfit_kwargs: dict[str, Any] = {"current_time": now}
                if source_event is not None and self._event_message_id(source_event):
                    outfit_kwargs["should_abort"] = lambda: self.event_was_recalled(
                        source_event, log_skip=True
                    )
                updated = await self.composer.update_outfit(
                    target_date_str, current_period, **outfit_kwargs
                )
                data = updated or data or await self.archive.get_day(target_date_str)
                outfit_changed = bool(
                    data and (data.outfit, data.time_period) != outfit_before
                )
            else:
                logger.debug(f"{LOG_PREFIX} 穿搭上下文未变化，跳过本次穿搭模型判断")
            if source_event is not None and self.event_was_recalled(
                source_event, log_skip=True
            ):
                return data
            if not state_due and not outfit_context_changed:
                if execution_changed:
                    await self.mark_page_status_changed("timeline_execution")
                return data
            if data:
                if not state_due:
                    data.meta["auto_outfit_context"] = self._outfit_context_signature(
                        data, now, current_period
                    )
                    await self.archive.save_day(data)
                    await self.mark_page_status_changed(
                        "outfit_update"
                        if outfit_context_changed
                        else "timeline_execution"
                    )
                    return data
                stable = not any(
                    (
                        execution_changed,
                        planning_changed,
                        state_changed,
                        outfit_context_changed,
                        outfit_changed,
                    )
                )
                try:
                    previous_stable_checks = int(
                        (data.meta or {}).get("auto_life_stable_checks", "0") or 0
                    )
                except (TypeError, ValueError):
                    previous_stable_checks = 0
                stable_checks = previous_stable_checks + 1 if stable else 0
                data.meta["auto_life_last_checked_at"] = now.strftime("%Y-%m-%d %H:%M")
                data.meta["auto_life_stable_checks"] = str(stable_checks)
                data.meta["auto_outfit_context"] = self._outfit_context_signature(
                    data, now, current_period
                )
                next_check = self._next_auto_life_check_at(
                    data,
                    now,
                    stable_checks=stable_checks,
                    stable=stable,
                )
                data.meta["auto_life_next_check_at"] = next_check.strftime(
                    "%Y-%m-%d %H:%M"
                )
                await self.archive.save_day(data)
            if source_event is not None and self.event_was_recalled(
                source_event, log_skip=True
            ):
                return data
            await self.mark_page_status_changed(status_reason)
            return data

    async def check_autonomous_life_update(self) -> None:
        if not self.config.state.enabled:
            return

        now = self._runtime_now()
        target_date_str, _ = await self.resolve_injection_target(now)
        await self._run_autonomous_life_check(
            target_date_str,
            now,
            source="auto",
            detail="后台自动检查：请根据当前时间、时间轴、天气、睡眠债和近期状态，自主判断此刻生活状态。",
            status_reason="autonomous_life_update",
            respect_quiet_hours=True,
            update_weather=True,
            log_trigger="后台巡检",
        )

    async def check_period_transition(self) -> None:
        await self.check_autonomous_life_update()

    def _schedule_context_state_refresh(
        self,
        target_date_str: str,
        data: DayRecord,
        now: datetime.datetime,
    ) -> None:
        if not self.config.state.enabled:
            return
        if not state_is_stale(data.state, now, self.config.state.refresh_minutes):
            return
        self._schedule_background_task(
            self.refresh_state_for_day(
                target_date_str,
                data,
                now,
                source="context",
                detail="外部读取生活上下文：按刷新间隔在后台检查实时状态。",
            ),
            label="上下文状态刷新",
            key=f"context_state:{target_date_str}",
        )
