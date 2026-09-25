from __future__ import annotations

import datetime
import json
import math
from typing import Any

from astrbot.api import logger

from ..life.appearance_history import record_appearance_snapshot
from ..life.condition import state_is_stale
from ..life.tools import (
    get_current_timeline_status,
    reconcile_timeline_execution,
    timeline_item_datetime,
)
from ..life.wardrobe import (
    format_outfit_components,
    normalize_outfit_components,
    normalize_outfit_scene_category,
    scene_category_for_place_kind,
    serialize_outfit_components,
    synchronize_outfit_components_for_scene,
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
    def _has_legacy_outfit_expiration(data: DayRecord) -> bool:
        """判断是否有旧版本误标为过期的换装动作需要修复。"""
        raw_actions = str((data.meta or {}).get("planned_life_actions") or "")
        raw_expirations = str((data.meta or {}).get("life_action_expirations") or "")
        try:
            actions = json.loads(raw_actions) if raw_actions else []
            expirations = json.loads(raw_expirations) if raw_expirations else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(actions, list) or not isinstance(expirations, dict):
            return False
        for action in actions:
            if not isinstance(action, dict):
                continue
            if str(action.get("action_type") or "").strip().lower() != "change_outfit":
                continue
            action_id = str(action.get("action_id") or "").strip()
            expiration = expirations.get(action_id)
            if (
                action_id
                and isinstance(expiration, dict)
                and str(expiration.get("status") or "").strip().lower() == "expired"
            ):
                return True
        return False

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

    @staticmethod
    def _pending_commitment_outfit(
        data: DayRecord,
        now: datetime.datetime,
    ) -> tuple[str, datetime.datetime | None]:
        """读取已经进入临近换装窗口的承诺穿搭要求。

        Args:
            data: 当前日记录。
            now: 当前巡检时间。

        Returns:
            当前可生效的穿搭要求及其计划生效时间。
        """

        raw = str((data.meta or {}).get("pending_commitment_outfit") or "")
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return "", None
        if not isinstance(payload, dict):
            return "", None
        instruction = str(payload.get("instruction") or "").strip()
        if not instruction or str(payload.get("date") or data.date) != data.date:
            return "", None
        effective_time = str(payload.get("effective_time") or "").strip()
        if not effective_time:
            return instruction, None
        try:
            effective_at = datetime.datetime.strptime(
                f"{data.date} {effective_time}", "%Y-%m-%d %H:%M"
            )
        except (TypeError, ValueError):
            return instruction, None
        if effective_at <= now + datetime.timedelta(minutes=90):
            return instruction, effective_at
        return "", effective_at

    def _outfit_context_signature(
        self, data: DayRecord, now: datetime.datetime, period: str
    ) -> str:
        del period  # 普通时段切换本身不是换装事件。
        current, _ = get_current_timeline_status(data.timeline, now, data.date)
        weather = data.weather_info

        def item_field(item: Any, key: str) -> str:
            if item is None:
                return ""
            if isinstance(item, dict):
                return str(item.get(key) or "").strip()
            return str(getattr(item, key, "") or "").strip()

        current_index = -1
        if current is not None:
            current_index = next(
                (index for index, item in enumerate(data.timeline) if item is current),
                -1,
            )

        scene_category = self._schedule_outfit_scene(data, now)
        scene = {
            "home": "home",
            "sleep": "sleep",
            "outdoor": "away",
            "public": "away",
        }.get(scene_category, "")
        # online/none 只说明活动没有实体地点，不应让在家网聊之类的活动
        # 看起来像换了穿衣场景；沿用最近一个明确的实体生活场景。
        if not scene:
            for item in data.timeline[: current_index + 1]:
                place_kind = item_field(item, "place_kind").lower()
                scene_category = scene_category_for_place_kind(place_kind, default="")
                if scene_category == "home":
                    scene = "home"
                elif scene_category in {"public", "outdoor"}:
                    scene = "away"
        if not scene:
            scene = "unknown"

        component_states = normalize_outfit_components(
            (data.meta or {}).get("outfit_components")
        )
        component_signature = (
            ";".join(
                f"{key}:{item.get('state', 'unknown')}"
                for key, item in component_states.items()
                if key in {"footwear", "carried_accessories"}
            )
            or "none"
        )

        action_events: dict[int, str] = {}
        raw_actions = str((data.meta or {}).get("planned_life_actions") or "")
        try:
            planned_actions = json.loads(raw_actions) if raw_actions else []
        except (TypeError, ValueError, json.JSONDecodeError):
            planned_actions = []
        for action in planned_actions if isinstance(planned_actions, list) else []:
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("action_type") or "").strip().lower()
            if action_type not in {"change_outfit", "exercise", "groom"}:
                continue
            try:
                timeline_index = int(action.get("timeline_index"))
            except (TypeError, ValueError):
                continue
            if 0 <= timeline_index <= current_index:
                action_events[timeline_index] = action_type

        latest_event = "none"
        for index, item in enumerate(data.timeline[: current_index + 1]):
            if item_field(item, "execution_state").lower() in {
                "expired",
                "skipped",
                "cancelled",
            }:
                continue
            event_kind = action_events.get(index, "")
            if event_kind:
                latest_event = f"{index}:{event_kind}"

        try:
            temperature_bucket = (
                round(float(weather.temp) / 5) if weather.temp is not None else ""
            )
        except (TypeError, ValueError):
            temperature_bucket = ""
        condition = str(weather.condition or "").strip()
        if bool(getattr(weather, "is_rainy", False)) or any(
            token in condition for token in ("雨", "雷", "雹")
        ):
            weather_kind = "rain"
        elif "雪" in condition:
            weather_kind = "snow"
        else:
            weather_kind = "dry"
        pending_outfit, _ = self._pending_commitment_outfit(data, now)
        values = (
            # 普通活动、时段和实时数值都会频繁变化。只有实体生活场景、
            # 最近一次穿衣相关事件、穿衣相关天气或明确要求才触发重审。
            "event_driven_v3",
            data.date,
            scene,
            latest_event,
            weather_kind,
            str(temperature_bucket),
            pending_outfit,
            component_signature,
        )
        return "|".join(values)

    @staticmethod
    def _schedule_outfit_scene(data: DayRecord, now: datetime.datetime) -> str:
        sleep = getattr(getattr(data, "state", None), "sleep", None)
        sleep_depth = str(getattr(sleep, "depth", "") or "").strip().lower()
        if sleep_depth in {"light_sleep", "deep_sleep"}:
            return "sleep"
        current, _ = get_current_timeline_status(data.timeline, now, data.date)
        place_kind = ""
        if current is not None:
            place_kind = (
                str(current.get("place_kind") or "")
                if isinstance(current, dict)
                else str(getattr(current, "place_kind", "") or "")
            )
        scheduled_scene = scene_category_for_place_kind(place_kind, default="")
        if scheduled_scene:
            return scheduled_scene
        return normalize_outfit_scene_category(
            (data.meta or {}).get("outfit_scene_category"), default=""
        )

    @classmethod
    def _schedule_outfit_scene_sync_needed(
        cls, data: DayRecord, now: datetime.datetime
    ) -> bool:
        target = cls._schedule_outfit_scene(data, now)
        if not target:
            return False
        meta = data.meta or {}
        previous = normalize_outfit_scene_category(
            meta.get("outfit_scene_category"), default=""
        )
        current = normalize_outfit_components(meta.get("outfit_components"))
        synced = synchronize_outfit_components_for_scene(
            current, target, previous_scene_category=previous
        )
        visible = format_outfit_components(synced)
        return (
            target != previous
            or synced != current
            or bool(visible and visible != str(data.outfit or "").strip())
        )

    @classmethod
    def _synchronize_outfit_with_schedule(
        cls, data: DayRecord, now: datetime.datetime
    ) -> bool:
        target = cls._schedule_outfit_scene(data, now)
        if not target:
            return False
        meta = data.meta or {}
        previous = normalize_outfit_scene_category(
            meta.get("outfit_scene_category"), default=""
        )
        current = normalize_outfit_components(meta.get("outfit_components"))
        synced = synchronize_outfit_components_for_scene(
            current, target, previous_scene_category=previous
        )
        visible = format_outfit_components(synced)
        changed = target != previous or synced != current
        if synced:
            serialized = serialize_outfit_components(synced)
            if serialized != str(meta.get("outfit_components") or "").strip():
                meta["outfit_components"] = serialized
                changed = True
        if visible and visible != str(data.outfit or "").strip():
            data.outfit = visible
            changed = True
        if target != previous:
            meta["outfit_scene_category"] = target
            changed = True
        if changed:
            record_appearance_snapshot(data, now)
        return changed

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
        _, outfit_effective_at = self._pending_commitment_outfit(data, now)
        if outfit_effective_at is not None:
            outfit_check_at = outfit_effective_at - datetime.timedelta(minutes=90)
            if outfit_check_at > now:
                outfit_minutes = max(
                    1, math.ceil((outfit_check_at - now).total_seconds() / 60)
                )
                delay_minutes = min(delay_minutes, outfit_minutes)
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
        commitment_changed = await self.reconcile_due_commitments_for_day(
            target_date_str, now
        )
        if commitment_changed:
            data = await self.archive.get_day(target_date_str) or data
        execution_source = {
            "auto": "后台巡检",
            "chat": "聊天触发",
        }.get(source, source or "生活巡检")
        execution_changed = reconcile_timeline_execution(
            data.timeline,
            now,
            data.date,
            evidence=f"{execution_source}：时间轴时钟",
            timeline_end=(data.meta or {}).get("life_window_end"),
        )
        current_period = self._get_curr_period(now)
        schedule_outfit_sync_needed = self._schedule_outfit_scene_sync_needed(data, now)
        outfit_context_changed = str(
            (data.meta or {}).get("auto_outfit_context", "") or ""
        ) != self._outfit_context_signature(data, now, current_period)
        if (
            not self._auto_life_check_due(data, now)
            and not commitment_changed
            and not execution_changed
            and not outfit_context_changed
            and not schedule_outfit_sync_needed
            and not self._has_legacy_outfit_expiration(data)
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
                timeline_end=(data.meta or {}).get("life_window_end"),
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
            schedule_outfit_changed = self._synchronize_outfit_with_schedule(data, now)
            if schedule_outfit_changed:
                await self.archive.save_day(data)
            outfit_context_changed = outfit_context_changed or schedule_outfit_changed
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
            outfit_context_recorded = False
            if outfit_context_changed:
                # 自主巡检可能由定时器和聊天后台刷新同时触发。使用与
                # 明确换装相同的租约，并在拿到锁后重新读取上下文，避免
                # 两个已经排队的任务依次再次调用模型。
                async with operation_lock(self, f"outfit:{target_date_str}"):
                    latest = await self.archive.get_day(target_date_str)
                    if latest:
                        latest_context = self._outfit_context_signature(
                            latest, now, current_period
                        )
                        latest_recorded = str(
                            (latest.meta or {}).get("auto_outfit_context", "") or ""
                        )
                        if latest_recorded and latest_recorded == latest_context:
                            data = latest
                            current_outfit_context = latest_context
                            outfit_context_changed = False
                            outfit_context_recorded = True
                            logger.debug(
                                f"{LOG_PREFIX} 同一轮穿搭判断已由并发任务完成，跳过重复模型调用"
                            )
                        else:
                            data = latest
                            current_outfit_context = latest_context
                            outfit_before = (data.outfit, data.time_period)
                            outfit_kwargs: dict[str, Any] = {"current_time": now}
                            pending_outfit_instruction, _ = (
                                self._pending_commitment_outfit(data, now)
                            )
                            if pending_outfit_instruction:
                                outfit_kwargs["instruction"] = (
                                    pending_outfit_instruction
                                )
                                outfit_kwargs["instruction_source"] = "commitment"
                            if source_event is not None and self._event_message_id(
                                source_event
                            ):
                                outfit_kwargs["should_abort"] = lambda: (
                                    self.event_was_recalled(source_event, log_skip=True)
                                )
                            updated = await self.composer.update_outfit(
                                target_date_str, current_period, **outfit_kwargs
                            )
                            data = (
                                updated
                                or data
                                or await self.archive.get_day(target_date_str)
                            )
                            outfit_changed = bool(
                                data
                                and (data.outfit, data.time_period) != outfit_before
                            )
                            if data and updated:
                                if pending_outfit_instruction:
                                    data.meta.pop("pending_commitment_outfit", None)
                                # 在释放租约前落下去重标记，后续排队任务
                                # 才能看到这次自动判断已经完成。
                                data.meta["auto_outfit_context"] = (
                                    current_outfit_context
                                )
                                outfit_context_recorded = True
                                await self.archive.save_day(data)
            else:
                logger.debug(f"{LOG_PREFIX} 穿搭上下文未变化，跳过本次穿搭模型判断")
            if source_event is not None and self.event_was_recalled(
                source_event, log_skip=True
            ):
                return data
            if not state_due and not outfit_context_changed:
                if execution_changed or commitment_changed:
                    await self.mark_page_status_changed("timeline_execution")
                return data
            if data:
                if not state_due:
                    if outfit_context_recorded or not outfit_context_changed:
                        data.meta["auto_outfit_context"] = (
                            self._outfit_context_signature(data, now, current_period)
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
                        commitment_changed,
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
                if outfit_context_recorded or not outfit_context_changed:
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
