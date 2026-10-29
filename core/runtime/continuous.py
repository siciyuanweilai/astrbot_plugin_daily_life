from __future__ import annotations

import asyncio
import copy
import datetime
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from astrbot.api import logger

from ..life.achievement import apply_reviews
from ..life.actionchain import actions as ordered_actions
from ..life.actionchain import ready as action_ready
from ..life.body import ContinuousBody, instant, project_body
from ..life.continuity import observation_pending
from ..life.goals import apply_goal_decisions, credit_goal, ready_steps
from ..life.interests import apply_interest_updates, bind_interest
from ..life.learning import expectation
from ..life.planalignment import prepare_updates, synchronize_run
from ..life.presence import (
    apply_action_reflections,
    apply_self_model_updates,
    record_action_outcome,
    record_event,
    sync_kernel_from_day,
    update_self_model,
)
from ..life.resources import reserve as reserve_resources
from ..life.resources import settle as settle_resources
from ..life.settlement import _ACTION_RULES
from ..life.tools import (
    timeline_deferred_until,
    timeline_item_datetime,
)
from ..models import INTERNAL_SIMULATED_ACTION_TYPES, LifeActionIntent
from .decisioncontext import decision_observation, decision_observation_changed
from .markers import LOG_PREFIX

_MAX_OBSERVED_GAP_SECONDS = 180
_TERMINAL_STATES = frozenset({"completed", "skipped", "cancelled", "expired"})


def planned_actions(day) -> dict[str, LifeActionIntent]:
    return {action.action_id: action for action in ordered_actions(day) if action.action_id}


def attached_action(day, run: dict[str, Any]) -> LifeActionIntent | None:
    original = LifeActionIntent.from_value(run["action"])
    if original.timeline_index is None:
        return original
    current = planned_actions(day).get(original.action_id)
    if (
        current is None
        or current.action_type != original.action_type
        or current.target != original.target
    ):
        return None
    if current.timeline_index is None or not 0 <= current.timeline_index < len(
        day.timeline
    ):
        return None
    if (
        current.payload != original.payload
        or current.preconditions != original.preconditions
        or (
            current.duration_minutes
            or day.timeline[current.timeline_index].duration_minutes
        )
        != original.duration_minutes
    ):
        return None
    original.timeline_index = current.timeline_index
    return original


def sleep_delayed(day, run, now) -> bool:
    if run.get("rest_kind") != "sleep":
        return False
    until = instant(day.meta.get("rest_delay_until"))
    return bool(until and until > now)


def next_decision_time(day, world, now, *, refresh_minutes: int):
    run = world.get("run") or {}
    remaining = max(
        1,
        float(run.get("action", {}).get("duration_minutes", 5))
        - float(run.get("active_seconds") or 0) / 60,
    )
    minutes = (
        min(refresh_minutes, remaining)
        if run.get("status") == "running" and not observation_pending(run)
        else refresh_minutes
    )
    due = now + datetime.timedelta(minutes=minutes)
    for item in day.timeline:
        if item.execution_state in _TERMINAL_STATES:
            continue
        at = timeline_item_datetime(item, day.date, meta=day.meta)
        deferred = timeline_deferred_until(item, day.date, day.meta)
        if deferred and at:
            at = max(at, deferred)
        if at and now < at < due:
            due = at
    return due


class ContinuousLifeService:
    """持续生活执行器；持有自身观察标识、检查锁和可取消检查任务。"""

    def __init__(
        self,
        *,
        archive: Callable[[], Any],
        config: Callable[[], Any],
        now: Callable[[], datetime.datetime],
        resolve_target: Callable[[datetime.datetime], Awaitable[tuple[str, bool]]],
        message_id: Callable[[Any], str],
        session_id: Callable[[Any], str],
        notify_status: Callable[[str], Awaitable[Any]],
        decide: Callable[..., Awaitable[dict[str, Any]]],
        validate_action: Callable[..., Awaitable[Any]],
        prepare_actions: Callable[..., Awaitable[str]],
        outfit_superseded: Callable[..., bool],
        settle_action: Callable[..., Any],
        save_receipt: Callable[..., Awaitable[Any]],
        settle_and_persist: Callable[..., Awaitable[Any]],
        load_settlements: Callable[..., Any],
        record_receipt: Callable[..., Awaitable[Any]],
        validate_preconditions: Callable[..., str],
        validate_contract: Callable[..., str],
        action_lock: Callable[[str], AbstractAsyncContextManager],
    ) -> None:
        self._archive = archive
        self._config = config
        self._now = now
        self._resolve_target = resolve_target
        self._message_id = message_id
        self._session_id = session_id
        self._notify_status = notify_status
        self._decide = decide
        self._validate_action = validate_action
        self._prepare_actions = prepare_actions
        self._outfit_superseded = outfit_superseded
        self._settle_action = settle_action
        self._save_receipt = save_receipt
        self._settle_and_persist = settle_and_persist
        self._load_settlements = load_settlements
        self._record_receipt = record_receipt
        self._validate_preconditions = validate_preconditions
        self._validate_contract = validate_contract
        self._action_lock = action_lock
        self._observer_id = uuid.uuid4().hex
        self._check_lock = asyncio.Lock()
        self._checks: set[asyncio.Task] = set()
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        current = asyncio.current_task()
        checks = [task for task in self._checks if task is not current]
        for task in checks:
            task.cancel()
        if checks:
            await asyncio.gather(*checks, return_exceptions=True)
        self._checks.clear()

    async def sync_continuous_kernel_state(
        self,
        day,
        *,
        now: datetime.datetime | None = None,
        event_kind: str = "",
        source_id: str = "",
        summary: str = "",
        evidence_ids: list[str] | None = None,
    ) -> None:
        """让外部状态刷新也回写统一生命内核。"""

        if self._closed or day is None:
            return
        observed_at = now or self._now().replace(tzinfo=None)

        def sync(latest, world):
            sync_kernel_from_day(latest, world, now=observed_at)
            if event_kind and source_id:
                record_event(
                    world,
                    kind=event_kind,
                    source_id=source_id,
                    at=observed_at,
                    summary=summary,
                    evidence_ids=evidence_ids,
                )

        await self._archive().mutate_continuous_life(day.date, sync)

    def _advance_continuous_world(self, day, world, now) -> None:
        from ..life.achievement import refresh_review_state

        refresh_review_state(world)
        body = ContinuousBody.from_value(world.get("body"), day)
        body.sync_condition(day)
        previous = instant(body.updated_at)
        seconds = max(0.0, (now - previous).total_seconds()) if previous else 0.0
        run = world.get("run")
        activity = "idle"
        sleeping = bool(world.get("sleeping")) if observation_pending(run) else False
        intensity = 1.0
        observer_id = self._observer_id
        restarted = previous is not None and world.get("observer_id") != observer_id
        if seconds > _MAX_OBSERVED_GAP_SECONDS or restarted:
            body.uncertain_minutes += seconds / 60
            body.advance(min(seconds / 60, 120))
            if isinstance(run, dict) and run["status"] == "running":
                run["observation_state"] = "pending"
                run["observation_gap"] = True
                run.setdefault("observed_at", body.updated_at)
                sleeping = bool(world.get("sleeping"))
                record_event(
                    world,
                    kind="observation_gap",
                    source_id=f"{run['action']['action_id']}:observation_gap:{now.isoformat()}",
                    at=now,
                    summary="行动观察中断，保留最后确认状态，等待重新确认进展",
                    evidence_ids=[run["action"]["action_id"]],
                )
            world["observation_gap_at"] = now.isoformat()
            world.pop("next_decision_at", None)
            seconds = 0
        world["observer_id"] = observer_id
        if observation_pending(run):
            body.uncertain_minutes += seconds / 60
        if isinstance(run, dict) and run["status"] == "running":
            action = attached_action(day, run)
            cancelled = action is None or (
                action.timeline_index is not None
                and day.timeline[action.timeline_index].execution_state
                in _TERMINAL_STATES
            )
            cancelled = cancelled or bool(
                action
                and self._outfit_superseded(day, action)
            )
            delayed = sleep_delayed(day, run, now) or (
                action is not None
                and action.timeline_index is not None
                and (
                    until := timeline_deferred_until(
                        day.timeline[action.timeline_index], day.date, day.meta
                    )
                )
                and until > now
            )
            from ..life.locationfacts import place_issue

            scene_issue = place_issue(day, action) if action is not None else ""
            if cancelled or delayed or scene_issue:
                reason = scene_issue or "原行动已调整或休息已顺延"
                run.update(
                    status="cancelled" if cancelled else "paused",
                    reason=reason,
                    changed_at=now.isoformat(),
                    observation_state="observed",
                    observed_at=now.isoformat(),
                )
                sleeping = False
                world.pop("next_decision_at", None)
                record_event(
                    world,
                    kind="action_cancel" if cancelled else "action_pause",
                    source_id=f"{run['action']['action_id']}:plan_change:{now.isoformat()}",
                    at=now,
                    summary=reason,
                    evidence_ids=[run["action"]["action_id"]],
                )
            elif not observation_pending(run):
                run["action"] = action.as_dict()
                run["observed_at"] = (
                    max(now, previous).isoformat() if previous else now.isoformat()
                )
                remaining = max(
                    0,
                    action.duration_minutes * 60
                    - float(run.get("active_seconds") or 0),
                )
                observed = min(seconds, remaining)
                run["active_seconds"] = float(run.get("active_seconds") or 0) + observed
                if observed > 0 and body.discomfort > 0:
                    observations = world.setdefault("body_observations", [])
                    observation = next((row for row in observations if row["action_id"] == action.action_id), None)
                    if observation is None:
                        observation = {"action_id": action.action_id, "activity": action.action_type,
                                       "rest_kind": run.get("rest_kind", "break"), "minutes": 0}
                        observations.append(observation)
                    observation.update(minutes=round(observation["minutes"] + observed / 60, 3),
                                       at=now.isoformat(), discomfort=body.discomfort)
                    world["body_observations"] = observations[-16:]
                activity = action.action_type
                sleeping = activity == "rest" and run.get("rest_kind") == "sleep"
                intensity = float(
                    action.payload.get("intensity") or action.payload.get("effort") or 1
                )
                body.advance(
                    observed / 60,
                    activity=activity,
                    sleeping=sleeping,
                    intensity=intensity,
                )
                body.advance((seconds - observed) / 60)
                if run["active_seconds"] >= action.duration_minutes * 60:
                    run["status"] = "ready"
                    run["finished_at"] = now.isoformat()
                    world.pop("next_decision_at", None)
                seconds = 0
        body.advance(seconds / 60)
        body.updated_at = (
            max(now, previous).isoformat() if previous else now.isoformat()
        )
        world["body"] = body.as_dict()
        project_body(day, body)
        if day.state is not None and world.get("sleeping") and not sleeping:
            day.state.sleep.depth = "awake"
        if sleeping and day.state is not None and not observation_pending(run):
            day.state.sleep.depth = (
                "deep_sleep"
                if float(run.get("active_seconds") or 0) >= 2700
                else "light_sleep"
            )
        world["sleeping"] = sleeping

    async def _finish_continuous_action(self, run, now) -> None:
        day = await self._archive().get_day(run["date"])
        if day is None:
            return
        action = attached_action(day, run)
        if action is None:
            await self._close_continuous_run(run, "cancelled", "原行动已被调整", now)
            return
        if (action.action_type == "study" and not action.payload.get("knowledge_source_id")
                and (action.payload.get("knowledge_query") or action.payload.get("knowledge_url"))):
            await self._close_continuous_run(run, "failed", "本次阅读未取得可读取的原文，保留疑问以便重新选择来源", now)
            return
        if action.action_type == "study" and action.payload.get("knowledge_source_id"):
            readings = await self._archive().memory_life.reading_receipts(
                action.action_id, int(action.payload["knowledge_source_id"])
            )
            if not readings:
                await self._close_continuous_run(run, "failed", "本次阅读尚未形成可核对的原文理解，阅读进度保持不变", now)
                return
        reason = f"自主执行器已记录开始和 {run['active_seconds'] / 60:.1f} 分钟执行过程：{run['reason']}"
        completed_at = instant(run.get("finished_at")) or now
        valid, validation_reason = (
            await self._validate_action(action, day=day)
            if run["status"] != "settling"
            else (True, "")
        )
        if not valid:
            await self._close_continuous_run(run, "failed", validation_reason, now)
            return
        outcome = None

        def settle(latest, world):
            nonlocal outcome
            current = world.get("run")
            if (
                not current
                or current["action"]["action_id"] != action.action_id
                or current["status"] not in {"ready", "settling"}
            ):
                return False
            rebound = attached_action(latest, current)
            if rebound is None or (
                rebound.timeline_index is not None
                and latest.timeline[rebound.timeline_index].execution_state
                in {"cancelled", "skipped", "expired"}
            ):
                current.update(status="cancelled", reason="执行结果提交前原行动已调整")
                return
            rebound.evidence = reason
            if (
                rebound.timeline_index is None
                and rebound.action_id not in planned_actions(latest)
            ):
                actions = [*planned_actions(latest).values(), rebound]
                latest.meta["planned_life_actions"] = json.dumps(
                    [item.as_dict() for item in actions], ensure_ascii=False
                )
            if self._outfit_superseded(latest, rebound):
                current.update(
                    status="cancelled", reason="实际穿搭已由新的聊天决定更新"
                )
                return
            prepared, wardrobe_issue = self._archive()._prepare_outfit_action_unlocked(latest, rebound)
            if wardrobe_issue:
                current.update(status="failed", reason=wardrobe_issue)
                return
            inventory_reason = (
                self._archive().consume_continuous_ingredients_unlocked(rebound)
                if self._config().domains.pantry_enabled
                else ""
            )
            if inventory_reason:
                current.update(status="failed", reason=inventory_reason)
                return
            resource_reason = settle_resources(self._archive(), rebound, commit=True, check_only=True)
            if resource_reason:
                current.update(status="failed", reason=resource_reason)
                return
            outcome = self._settle_action(
                latest, prepared, now=completed_at
            )
            if outcome.status != "committed":
                current.update(status="failed", reason=outcome.reason)
                return
            settle_resources(self._archive(), rebound, commit=True)
            if self._config().domains.pantry_enabled:
                self._archive().consume_continuous_ingredients_unlocked(
                    rebound, consume=True, occurred_at=completed_at.isoformat(sep=" ")
                )
            if not current.get("body_credited"):
                body = ContinuousBody.from_value(
                    json.loads(latest.meta["continuous_body"])
                )
                world["body"] = body.as_dict()
                project_body(latest, body)
                current["body_credited"] = True
            record_action_outcome(
                world,
                rebound,
                status="committed",
                completed_at=completed_at,
                reason=reason,
                run=current,
            )
            current["action"] = rebound.as_dict()
            current["status"] = "settling"

        day, world = await self._archive().mutate_continuous_life(run["date"], settle)
        if outcome is None or outcome.status != "committed":
            return
        current = world["run"]
        # 状态与执行检查点已经原子落库；重启只重放同一回执并补齐派生记录。
        action = LifeActionIntent.from_value(current["action"])
        await self._save_receipt(
            day,
            action,
            {
                "receipt_id": f"autonomous:{action.action_id}",
                "status": "simulated",
                "source": "continuous_executor",
                "source_id": action.action_id,
                "occurred_at": completed_at.isoformat(sep=" "),
                "evidence": [reason],
            },
            now=completed_at,
        )
        await self._settle_and_persist(
            day,
            action,
            now=completed_at,
            receipt_status="simulated",
            fact_source="continuous_executor",
            fact_evidence=reason,
        )

        def finish(latest, stored):
            executing = stored.get("run")
            if not executing or executing["action"]["action_id"] != action.action_id:
                return False
            credit_goal(stored, executing, completed_at)
            record_action_outcome(
                stored,
                action,
                status="committed",
                completed_at=completed_at,
                reason=reason,
                run=executing,
            )
            executing.update(
                status="completed", finished_at=completed_at.isoformat(), reason=reason
            )
            record_event(
                stored,
                kind="action_completed",
                source_id=f"{action.action_id}:completed",
                at=completed_at,
                summary=f"自主行动已完成：{action.target}",
                evidence_ids=[reason],
            )
            stored.setdefault("history", []).append(copy.deepcopy(executing))
            stored["history"] = stored["history"][-40:]
            stored.pop("run", None)
            stored.pop("next_decision_at", None)
            stored["sleeping"] = False
            project_body(latest, ContinuousBody.from_value(stored["body"]))

        await self._archive().mutate_continuous_life(run["date"], finish)

    async def _close_continuous_run(self, run, status, reason, now):
        action = LifeActionIntent.from_value(run["action"])
        day = await self._archive().get_day(run["date"])
        if day is not None:
            rebound = attached_action(day, run)
            if rebound is None:
                action.timeline_index = None
            else:
                action = rebound
            if (
                self._load_settlements(day)
                .get(action.action_id, {})
                .get("status")
                == "committed"
            ):
                status, reason = "completed", "相同行动已有确认回执，不重复结算"
        if day is not None and status in {"failed", "cancelled"}:
            await self._record_receipt(
                day,
                action.action_id,
                {
                    "receipt_id": f"autonomous:{action.action_id}:{status}",
                    "status": status,
                    "source": "continuous_executor",
                    "evidence": [reason],
                },
                now=now,
                planned_action=action,
            )

        def close(latest, world):
            current = world.get("run")
            if not current or current["action"]["action_id"] != action.action_id:
                return False
            current.update(status=status, reason=reason, finished_at=now.isoformat())
            settle_resources(self._archive(), action, commit=False)
            record_event(
                world,
                kind=f"action_{status}",
                source_id=f"{action.action_id}:{status}",
                at=now,
                summary=f"自主行动{status}：{action.target}",
                evidence_ids=[reason],
            )
            if action.action_type in {"work", "study", "exercise", "chore"}:
                credit_goal(world, current, now)
            record_action_outcome(
                world,
                action,
                status=status,
                completed_at=now,
                reason=reason,
                run=current,
            )
            world.setdefault("history", []).append(copy.deepcopy(current))
            world["history"] = world["history"][-40:]
            world.pop("run", None)
            world.pop("next_decision_at", None)
            world["sleeping"] = False

        await self._archive().mutate_continuous_life(run["date"], close)

    async def check_continuous_life(self) -> None:
        if (
            self._closed
            or not self._config().domains.enabled
            or not self._config().domains.simulate_internal_actions
            or self._check_lock.locked()
        ):
            return
        async with self._check_lock:
            task = asyncio.current_task()
            if task is not None:
                self._checks.add(task)
            try:
                await self._check_continuous_life_once()
            except Exception as exc:
                logger.warning(
                    f"{LOG_PREFIX} 持续生活检查暂未完成，保留执行检查点：{type(exc).__name__}"
                )
            finally:
                if task is not None:
                    self._checks.discard(task)

    async def _check_continuous_life_once(self) -> None:
        now = self._now().replace(tzinfo=None)
        date, _ = await self._resolve_target(now)
        world = await self._archive().get_continuous_life()
        active_date = (world.get("run") or {}).get("date") or date
        day, world = await self._archive().mutate_continuous_life(
            active_date,
            lambda day, world: self._advance_continuous_world(day, world, now),
        )
        if day is None:
            if active_date == date:
                return

            def detach(latest, stored):
                missing = stored.pop("run", None)
                if missing:
                    settle_resources(self._archive(), LifeActionIntent.from_value(missing["action"]), commit=False)
                    missing.update(
                        status="cancelled",
                        reason="原业务日记录已清理",
                        finished_at=now.isoformat(),
                    )
                    stored.setdefault("history", []).append(missing)
                    stored["history"] = stored["history"][-40:]
                    stored.pop("next_decision_at", None)
                self._advance_continuous_world(latest, stored, now)

            day, world = await self._archive().mutate_continuous_life(date, detach)
            active_date = date
            if day is None:
                return
        run = world.get("run")
        if run and run["status"] in {"ready", "settling"}:
            async with self._action_lock(f"continuous_action:{run['action']['action_id']}"):
                await self._finish_continuous_action(run, now)
            world = await self._archive().get_continuous_life()
        elif run and run["status"] in {"failed", "cancelled"}:
            await self._close_continuous_run(run, run["status"], run["reason"], now)
            world = await self._archive().get_continuous_life()
        if active_date != date:
            day, world = await self._archive().mutate_continuous_life(
                date,
                lambda day, world: project_body(
                    day, ContinuousBody.from_value(world.get("body"), day)
                ),
            )
            if day is None:
                return
        else:
            day = await self._archive().get_day(date)
        await self._notify_status("continuous_life")
        due = instant(world.get("next_decision_at"))
        last_decision = instant(world.get("last_decision_at"))
        interval = self._config().state.refresh_minutes
        if due and last_decision:
            due = min(due, last_decision + datetime.timedelta(minutes=interval))
        if due and due > now and not decision_observation_changed(
            world.get("decision_observation"), decision_observation(day, world)
        ):
            return
        expected_revision = world.get("revision")
        claimed = False

        def begin(latest, stored):
            nonlocal claimed
            if stored.get("revision") != expected_revision or latest.revision != day.revision:
                return False
            stored["last_decision_at"] = now.isoformat()
            stored["decision_observation"] = decision_observation(latest, stored)
            stored["next_decision_at"] = next_decision_time(
                latest, stored, now, refresh_minutes=interval,
            ).isoformat()
            claimed = True

        day, world = await self._archive().mutate_continuous_life(date, begin)
        if not claimed:
            return
        try:
            payload = await self._decide(day, world, now)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 自主行动选择暂未完成：{type(exc).__name__}")
            payload = {}
        if not payload:
            return
        await self._apply_continuous_decision(
            day, world, payload, self._now().replace(tzinfo=None)
        )

    async def _apply_continuous_decision(self, day, snapshot, payload, now):
        decision = payload.get("decision")
        reason = str(payload.get("reason") or "").strip()[:240]
        if not reason or decision not in {
            "start",
            "continue",
            "pause",
            "resume",
            "cancel",
            "wait",
        }:
            return
        proposed = prepare_updates(day, snapshot, payload, now)
        if proposed is None:
            def retry(latest, world):
                if world.get("revision") != snapshot.get("revision") or latest.revision != day.revision:
                    return False
                world["plan_alignment_blocked"] = "日程调整未通过事实与约定校验，请保留受保护节点，重新选择可执行的安排"
                world["next_decision_at"] = next_decision_time(
                    latest, world, now, refresh_minutes=self._config().state.refresh_minutes,
                ).isoformat()
                world["decision_observation"] = decision_observation(latest, world)

            await self._archive().mutate_continuous_life(day.date, retry)
            return
        day = proposed
        action = None
        action_issue = "自主步骤缺少可执行的类型、目标、耗时或当前节点"
        new_steps = []
        if decision == "start" and not snapshot.get("run"):
            candidate = next(
                (
                    item
                    for item in payload.get("_candidates", [])
                    if item["action_id"] == payload.get("action_id")
                    and item["action_id"] in planned_actions(day)
                ),
                None,
            )
            if candidate:
                action = planned_actions(day)[candidate["action_id"]]
                if not action.duration_minutes and action.timeline_index is not None:
                    action.duration_minutes = day.timeline[action.timeline_index].duration_minutes
                if not action_ready(day, action):
                    action = None
            else:
                raw_steps = payload.get("new_actions")
                if isinstance(raw_steps, list) and 1 <= len(raw_steps) <= 12:
                    chain_id = uuid.uuid4().hex
                    for raw in raw_steps:
                        if not isinstance(raw, dict) or raw.get("owner") != "self":
                            new_steps = []
                            break
                        step = LifeActionIntent.from_value(raw)
                        if (step.action_type not in INTERNAL_SIMULATED_ACTION_TYPES
                                or not step.target or not step.duration_minutes):
                            new_steps = []
                            break
                        if step.timeline_index is not None:
                            if not 0 <= step.timeline_index < len(day.timeline):
                                new_steps = []
                                break
                            item = day.timeline[step.timeline_index]
                            if any(existing.timeline_index == step.timeline_index for existing in ordered_actions(day)):
                                new_steps = []
                                break
                            at = timeline_item_datetime(item, day.date, meta=day.meta)
                            deferred = timeline_deferred_until(item, day.date, day.meta)
                            if (item.execution_state in _TERMINAL_STATES or at is None
                                    or at > now or (deferred and deferred > now)):
                                new_steps = []
                                break
                        step.action_id = f"self:{uuid.uuid4().hex}"
                        step.requested_at = now.isoformat(sep=" ")
                        step.payload["execution_chain_id"] = chain_id
                        step.payload["execution_chain_unbound"] = step.timeline_index is None
                        step.source = "continuous_plan"
                        new_steps.append(step)
                    if new_steps:
                        action_issue = await self._prepare_actions(day, new_steps)
                        if not action_issue:
                            action = new_steps[0]
            if action and (
                action.action_type not in INTERNAL_SIMULATED_ACTION_TYPES
                or not action.duration_minutes
                or not action.target
            ):
                action = None
            if action:
                valid, action_issue = await self._validate_action(action, day=day)
                if not valid:
                    action = None
        expected_revision = int(snapshot.get("revision") or 0)
        resume_valid = False
        if snapshot.get("run") and (
            decision == "resume"
            or decision == "continue" and observation_pending(snapshot["run"])
        ):
            resumed = attached_action(day, snapshot["run"])
            resume_valid = (
                resumed is not None and (await self._validate_action(resumed, day=day))[0]
            )
        if decision == "start" and not snapshot.get("run") and action is None:
            def blocked(latest, world):
                if (int(world.get("revision") or 0) != expected_revision
                        or latest.revision != day.revision or world.get("run")):
                    return False
                world["plan_alignment_blocked"] = action_issue
                world["last_decision_at"] = now.isoformat()
                world["next_decision_at"] = next_decision_time(
                    latest, world, now, refresh_minutes=self._config().state.refresh_minutes,
                ).isoformat()
                world["decision_observation"] = decision_observation(latest, world)

            await self._archive().mutate_continuous_life(day.date, blocked)
            return

        blocked_reason = ""

        def apply(latest, world):
            nonlocal blocked_reason
            if (
                int(world.get("revision") or 0) != expected_revision
                or latest.revision != day.revision
            ):
                return False
            latest.timeline = copy.deepcopy(day.timeline)
            latest.meta = copy.deepcopy(day.meta)
            world.pop("plan_alignment_blocked", None)
            self._advance_continuous_world(latest, world, now)
            world["last_decision_at"] = now.isoformat()
            goal_payload = (
                payload
                if payload.get("_goal_review_due", True)
                else {**payload, "new_goals": []}
            )
            apply_goal_decisions(
                world, goal_payload, sources=payload.get("_sources", set()), now=now
            )
            model_input = payload.get("_self_model")
            if isinstance(model_input, dict):
                update_self_model(
                    world,
                    persona=str(model_input.get("persona") or ""),
                    preferences=model_input.get("preferences") or [],
                    focus=model_input.get("focus") or [],
                    skills=model_input.get("skills") or {},
                    evidence_ids=model_input.get("evidence_ids") or [],
                    now=now,
                )
            apply_action_reflections(
                world,
                payload.get("action_reflections"),
                allowed_action_ids=payload.get("_reflection_action_ids", set()),
                now=now,
            )
            if payload.get("_achievement_candidates"):
                world["achievement_review_at"] = now.isoformat()
                world["achievement_review_revisions"] = [
                    item["revision"] for item in payload["_achievement_candidates"]
                ]
            apply_reviews(
                world, payload.get("achievement_reviews"),
                offered=payload.get("_achievement_candidates", []), now=now,
            )
            if payload.get("_goal_review_due", True):
                apply_interest_updates(world, payload.get("interest_updates"),
                                       sources=payload.get("_sources", set()), now=now)
                apply_self_model_updates(
                    world,
                    payload.get("self_model_updates"),
                    sources=payload.get("_sources", set()),
                    now=now,
                )
            if payload.get("_goal_review_due", True):
                world.update(
                    goal_review_at=now.isoformat(),
                    goal_source_key=payload.get("_goal_source_key", ""),
                )
            current = world.get("run")
            transition_event = ""
            if current:
                if decision == "cancel" and current["status"] in {"running", "paused"}:
                    current.update(status="cancelled", reason=reason)
                    transition_event = "cancel"
                elif decision == "pause" and current["status"] == "running":
                    current.update(status="paused", reason=reason)
                    transition_event = "pause"
                elif (
                    decision == "resume" and current["status"] == "paused"
                    or decision in {"resume", "continue"} and observation_pending(current)
                ):
                    resumed = attached_action(latest, current)
                    if (
                        resume_valid
                        and resumed is not None
                        and not sleep_delayed(latest, current, now)
                        and not self._validate_preconditions(
                            latest, latest.state, resumed
                        )
                        and not self._validate_contract(
                            latest,
                            resumed,
                            _ACTION_RULES[resumed.action_type],
                            latest.state,
                        )
                    ):
                        transition_event = (
                            "reconfirmed" if observation_pending(current) else "resume"
                        )
                        current.update(status="running", reason=reason)
                    else:
                        return False
                if transition_event:
                    current["changed_at"] = now.isoformat()
                    current["observation_state"] = "observed"
                    current["observed_at"] = now.isoformat()
                    record_event(
                        world,
                        kind=f"action_{transition_event}",
                        source_id=f"{current['action']['action_id']}:{transition_event}:{now.isoformat()}",
                        at=now,
                        summary=f"{transition_event}：{current['action'].get('target') or current['action'].get('action_type')}",
                        evidence_ids=[reason],
                    )
            elif decision == "start" and action is not None:
                if not action_ready(latest, action):
                    return False
                rule = _ACTION_RULES[action.action_type]
                if self._validate_contract(
                    latest, action, rule, latest.state
                ) or self._validate_preconditions(
                    latest, latest.state, action
                ):
                    return False
                if action.timeline_index is not None:
                    item = latest.timeline[action.timeline_index]
                    if item.execution_state in _TERMINAL_STATES:
                        return False
                    at = timeline_item_datetime(item, latest.date, meta=latest.meta)
                    deferred = timeline_deferred_until(item, latest.date, latest.meta)
                    if at is None or at > now or (deferred and deferred > now):
                        return False
                proposed_run = {
                    "rest_kind": "sleep"
                    if payload.get("rest_kind") == "sleep"
                    else "break"
                }
                if (
                    sleep_delayed(latest, proposed_run, now)
                    and action.action_type == "rest"
                ):
                    return False
                if self._outfit_superseded(latest, action):
                    return False
                parent = None
                parent_id = action.payload.get("artifact_parent_id")
                if parent_id:
                    parent = self._archive()._life_entity_unlocked(str(parent_id))
                    if not parent or parent.get("owner") != "self" or not parent.get("artifact_version") or parent.get("scope") != "global":
                        blocked_reason = "未找到可延续的自身数字作品。"
                        return False
                resource_reason = reserve_resources(self._archive(), action,
                    scope=str(world.get("last_chat_scope") or "global"),
                    location=str(latest.meta.get("current_place") or ""), now=now)
                if resource_reason:
                    blocked_reason = resource_reason
                    return False
                world.pop("resource_blocked", None)
                if new_steps:
                    latest.meta["planned_life_actions"] = json.dumps(
                        [item.as_dict() for item in planned_actions(latest).values()]
                        + [item.as_dict() for item in new_steps], ensure_ascii=False,
                    )
                if action.timeline_index is not None:
                    item = latest.timeline[action.timeline_index]
                    item.execution_state = "active"
                    item.execution_reason = "自主行动已开始"
                    item.execution_evidence = reason
                    item.execution_updated_at = now.isoformat(sep=" ")
                action.requested_at = now.isoformat(sep=" ")
                action.evidence = reason
                action.source = "continuous_executor"
                if action.timeline_index is None:
                    actions = planned_actions(latest)
                    actions[action.action_id] = action
                    latest.meta["planned_life_actions"] = json.dumps(
                        [item.as_dict() for item in actions.values()], ensure_ascii=False
                    )
                world["pending_action_ids"] = [
                    item for item in world.get("pending_action_ids", [])
                    if item != action.action_id
                ] + [item.action_id for item in new_steps[1:]]
                world["run"] = {
                    "date": latest.date,
                    "action": action.as_dict(),
                    "status": "running",
                    "observation_state": "observed",
                    "observed_at": now.isoformat(),
                    "started_at": now.isoformat(),
                    "active_seconds": 0.0,
                    "reason": reason,
                    "rest_kind": "sleep"
                    if payload.get("rest_kind") == "sleep"
                    else "break",
                    "goal_id": "",
                    "step_id": "",
                    "body_before": copy.deepcopy(world.get("body") or {}),
                    "expectation": expectation(payload.get("expectation")),
                    "artifact_parent": {key: parent[key] for key in ("id", "series_id", "artifact_version")} if parent else None,
                }
                record_event(
                    world,
                    kind="action_started",
                    source_id=action.action_id,
                    at=now,
                    summary=f"开始自主行动：{action.target}",
                    evidence_ids=[reason],
                )
                goal = next(
                    (
                        item
                        for item in world.get("goals", [])
                        if item["id"]
                        == (payload.get("goal_id") or action.payload.get("goal_id"))
                    ),
                    None,
                )
                step = next(
                    (
                        item
                        for item in ready_steps(goal or {})
                        if item["id"]
                        == (payload.get("step_id") or action.payload.get("step_id"))
                        and item["action_type"] == action.action_type
                    ),
                    None,
                )
                if step:
                    world["run"].update(goal_id=goal["id"], step_id=step["id"])
                bind_interest(world, world["run"], payload)
            synchronize_run(latest, world, now)
            running = world.get("run") or {}
            if not observation_pending(running):
                world["sleeping"] = (
                    running.get("status") == "running"
                    and running.get("action", {}).get("action_type") == "rest"
                    and running.get("rest_kind") == "sleep"
                )
            world["next_decision_at"] = next_decision_time(
                latest, world, now, refresh_minutes=self._config().state.refresh_minutes,
            ).isoformat()
            world["decision_observation"] = decision_observation(latest, world)

        await self._archive().mutate_continuous_life(day.date, apply)
        if blocked_reason:
            def record_blocked(latest, world):
                if int(world.get("revision") or 0) != expected_revision or latest.revision != day.revision:
                    return False
                world["resource_blocked"] = blocked_reason
                world["next_decision_at"] = next_decision_time(
                    latest, world, now, refresh_minutes=self._config().state.refresh_minutes,
                ).isoformat()
                world["decision_observation"] = decision_observation(latest, world)

            await self._archive().mutate_continuous_life(day.date, record_blocked)
        await self._notify_status("continuous_life")

    async def note_continuous_chat_exchange(self, event) -> None:
        """只记真实已发送的对话证据，不在消息钩子内调用模型。"""
        if (
            self._closed
            or not self._config().domains.enabled
            or not self._config().domains.simulate_internal_actions
        ):
            return
        message_id = self._message_id(event)
        scope = self._session_id(event)
        if not message_id or not scope:
            return
        now = self._now().replace(tzinfo=None)
        date, _ = await self._resolve_target(now)
        snapshot = await self._archive().get_continuous_life()
        date = (snapshot.get("run") or {}).get("date") or date
        token = f"{scope}:{message_id}"

        def record(day, world):
            if token in world.get("chat_events", []):
                return False
            if not world.get("body"):
                initial = ContinuousBody.from_value(None, day)
                initial.updated_at = now.isoformat()
                world["body"] = initial.as_dict()
            body = ContinuousBody.from_value(world["body"], day)
            last_exchange = instant(world.get("last_chat_at"))
            previous_tick = instant(body.updated_at)
            # 社交消耗只覆盖已确认连续交流的间隔，首条消息不凭空补时长。
            ongoing_chat = (
                last_exchange
                and previous_tick
                and 0
                <= (now - last_exchange).total_seconds()
                <= _MAX_OBSERVED_GAP_SECONDS
            )
            self._advance_continuous_world(day, world, now)
            if (
                ongoing_chat
                and previous_tick
                and (now - previous_tick).total_seconds() <= _MAX_OBSERVED_GAP_SECONDS
            ):
                body = ContinuousBody.from_value(world["body"])
                observed = max(0, (now - previous_tick).total_seconds()) / 60
                body.social_battery = max(0, body.social_battery - observed * 0.095)
                world["body"] = body.as_dict()
            run = world.get("run") or {}
            if world.get("sleeping") and run.get("status") == "running":
                run.update(
                    status="paused",
                    reason="已实际回应对话，短暂醒来后重新判断睡眠安排",
                    changed_at=now.isoformat(),
                    observation_state="observed",
                    observed_at=now.isoformat(),
                )
                record_event(
                    world,
                    kind="action_pause",
                    source_id=f"{run['action']['action_id']}:chat:{token}",
                    at=now,
                    summary="实际回应对话，短暂醒来后重新判断睡眠安排",
                    evidence_ids=[token],
                )
                world.pop("next_decision_at", None)
            world["sleeping"] = False
            world["last_chat_at"] = now.isoformat()
            world["last_chat_scope"] = scope
            record_event(
                world,
                kind="conversation",
                source_id=token,
                at=now,
                summary="完成一次真实对话交换",
                evidence_ids=[token],
            )
            events = world.setdefault("chat_events", [])
            events.append(token)
            world["chat_events"] = events[-128:]

        await self._archive().mutate_continuous_life(date, record)
        await self._notify_status("continuous_life")


__all__ = ["ContinuousLifeService", "next_decision_time"]
