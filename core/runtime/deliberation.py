from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger

from ..browsing.bookmarks import BrowseArchive
from ..epistemic import temporal_context
from ..life.achievement import review_candidates
from ..life.actionchain import ready as action_ready
from ..life.body import instant
from ..life.planalignment import protected_indexes
from ..life.tools import (
    extract_json_from_text,
    timeline_deferred_until,
    timeline_item_datetime,
)
from ..models import INTERNAL_SIMULATED_ACTION_TYPES
from ..prompts import cache_friendly_prompt
from ..telemetry import model_task
from .continuous import _TERMINAL_STATES, planned_actions
from .decisioncontext import decision_prompt_context, execution_context, run_context, skill_context
from .goalreview import review_goals
from .lifecontract import build_life_decision_contract
from .markers import LOG_PREFIX


class ContinuousDecisionService:
    """仅根据已确认的生活证据选择行动，不负责执行与归档。"""

    def __init__(
        self,
        *,
        archive: Callable[[], Any],
        config: Callable[[], Any],
        persona: Callable[[], Awaitable[str]],
        recent_chats: Callable[[str], Awaitable[str]],
        scope_messages: Callable[[str], Awaitable[list[dict[str, Any]]]],
        text_provider: Callable[[str], Awaitable[Any]],
        call_text_model: Callable[..., Awaitable[str]],
        close_text_session: Callable[[str], Awaitable[None]],
        wardrobe_context: Callable[..., Awaitable[str]],
    ) -> None:
        self._archive = archive
        self._config = config
        self._persona = persona
        self._recent_chats = recent_chats
        self._scope_messages = scope_messages
        self._text_provider = text_provider
        self._call_text_model = call_text_model
        self._close_text_session = close_text_session
        self._wardrobe_context = wardrobe_context

    async def choose(self, day, world, now) -> dict[str, Any]:
        candidates = []
        for action in planned_actions(day).values():
            if (
                action.action_type not in INTERNAL_SIMULATED_ACTION_TYPES
                or not action_ready(day, action)
            ):
                continue
            if action.timeline_index is None:
                if (action.payload.get("execution_chain_unbound") is True
                        and action.action_id in world.get("pending_action_ids", [])):
                    candidates.append({**action.as_dict(), "execution_state": "planned"})
                continue
            if not 0 <= action.timeline_index < len(day.timeline):
                continue
            item = day.timeline[action.timeline_index]
            at = timeline_item_datetime(item, day.date, meta=day.meta)
            deferred = timeline_deferred_until(item, day.date, day.meta)
            if (
                at is None
                or (deferred and deferred > now)
                or item.execution_state in _TERMINAL_STATES
            ):
                continue
            duration = action.duration_minutes or item.duration_minutes
            if duration <= 0:
                continue
            action.duration_minutes = duration
            candidates.append(
                {
                    **action.as_dict(), "activity": item.activity, "place": item.place,
                    "planned_at": at.isoformat(timespec="seconds"),
                    "execution_state": item.execution_state,
                    "delay_minutes": max(0, int((now - at).total_seconds() / 60)),
                }
            )
        persona = await self._persona()
        settings = self._config()
        capabilities = {
            "bilibili": bool(settings.browsing.enabled),
            "source_discovery": bool(settings.memory.enabled and settings.search.enabled
                                     and settings.search.tavily_api_keys),
        }
        preferences = await self._archive().get_preferences(limit=12)
        browsing = []
        if callable(getattr(self._archive(), "_run_db", None)):
            browsing = [
                {**{key: item.get(key) for key in (
                    "id", "title", "status", "position", "duration", "impression", "question", "interest", "interest_delta",
                )}, "source_id": f"browsing:{item['id']}", "evidence": item["evidence"][-3:]}
                for item in await BrowseArchive(self._archive()).list(scope="global", limit=12)
                if item.get("evidence") and item.get("impression")
            ][:6]
        memory_life = getattr(self._archive(), "memory_life", None)
        library = await memory_life.library(["global", "self"]) if memory_life is not None else []
        library = [{k: r[k] for k in ("id", "title", "origin", "read_count", "total")}
                   for r in library if r["status"] == "available"][:30]
        reading_notes = await memory_life.reading_notes(["global", "self"], 6) if memory_life is not None else []
        sources = {"persona", f"state:{day.date}:{day.revision}"}
        conversation_request = world.get("conversation_action_request") or {}
        if conversation_request.get("status") == "pending":
            sources.add(conversation_request["id"])
        sources.update(f"reading:{r['id']}" for r in reading_notes)
        sources.update(item["source_id"] for item in browsing)
        for run in [*(world.get("history") or [])[-4:], world.get("run") or {}]:
            if isinstance(run, dict) and isinstance(run.get("action"), dict) and run["action"].get("action_id"):
                sources.add(f"action:{run['action']['action_id']}")
        sources.update(f"preference:{item.id}" for item in preferences)
        kernel = world.get("kernel") if isinstance(world.get("kernel"), dict) else {}
        model = kernel.get("self_model") if isinstance(kernel.get("self_model"), dict) else {}
        sources.update(
            f"interest:{item['id']}"
            for item in model.get("interests", [])
            if isinstance(item, dict) and item.get("id") and item.get("status") == "supported"
            and item.get("disposition", "active") == "active"
        )
        unreflected_outcomes = [
            item
            for item in kernel.get("action_outcomes", [])
            if isinstance(item, dict)
            and not item.get("reflection")
            and item.get("observed_minutes", 0) > 0
        ][-2:]
        sources.update(f"action:{item['action_id']}" for item in unreflected_outcomes)
        sources.add(f"persona:{hashlib.sha256(str(persona).encode()).hexdigest()[:16]}")
        latest_exchange = []
        chat_scope = world.get("last_chat_scope")
        if chat_scope:
            try:
                messages = await asyncio.wait_for(self._scope_messages(chat_scope), timeout=3)
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 当前会话暂未读取完成，继续依据已有生活事实：{type(exc).__name__}"
                )
                messages = []
            latest_exchange = [
                {
                    "role": item.get("role"),
                    "name": item.get("name"),
                    "content": str(item.get("content") or "")[:1200],
                    "message_id": item.get("message_id"),
                }
                for item in messages[-6:]
                if isinstance(item, dict)
            ]
            if latest_exchange and world.get("chat_events"):
                sources.add(f"chat:{world['chat_events'][-1]}")
        goal_source_key = hashlib.sha256(
            json.dumps(
                [
                    persona,
                    [item.as_dict() for item in preferences],
                    world.get("chat_events", [])[-1:] if latest_exchange else [],
                    [item.get("action_id") for item in kernel.get("action_outcomes", [])
                     if isinstance(item, dict)][-8:],
                    [{key: item.get(key) for key in ("id", "text", "status", "disposition")}
                     for item in model.get("interests", []) if isinstance(item, dict)],
                    [{key: item.get(key) for key in ("id", "position", "impression", "interest")}
                     for item in browsing],
                    [{key: item.get(key) for key in ("id", "read_count", "total")} for item in library],
                    [{key: item.get(key) for key in ("id", "summary", "reflection", "question")} for item in reading_notes],
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        last_review = instant(world.get("goal_review_at"))
        goal_review_due = (
            last_review is None
            or goal_source_key != world.get("goal_source_key")
            and now - last_review >= datetime.timedelta(
                minutes=self._config().state.refresh_minutes,
            )
        )
        achievement_candidates = review_candidates(world)
        last_attempt = instant(world.get("achievement_review_at"))
        revisions = [item["revision"] for item in achievement_candidates]
        if (last_attempt and now - last_attempt < datetime.timedelta(minutes=20)
                and revisions == world.get("achievement_review_revisions")):
            achievement_candidates = []
        provider_id = self._config().domains.action_provider
        provider = await self._text_provider(provider_id)
        if provider is None:
            return {}
        run = world.get("run") or {}
        if (run.get("status") in {"running", "paused"} and not goal_review_due
                and not unreflected_outcomes and not achievement_candidates
                and conversation_request.get("status") != "pending"):
            # 已在做的事只复核是否还能继续；需要新行动或完整资料时由模型主动展开。
            protected = protected_indexes(day, world)
            continuation = {
                "persona": persona,
                "capabilities": capabilities,
                "state": day.state.as_dict() if day.state else {},
                "body": world.get("body"), "run": run_context(world),
                "current_place": day.meta.get("current_place", ""),
                "outfit": day.outfit, "weather": day.weather,
                "rest_delay_until": day.meta.get("rest_delay_until", ""),
                "life_kernel": execution_context(world),
                "preferences": [item.as_dict() for item in preferences],
                "goals": [{key: goal.get(key) for key in ("id", "title", "status", "obstacle")}
                          for goal in world.get("goals", []) if isinstance(goal, dict)],
                "self_model": {key: value for key, value in model.items() if key != "capabilities"},
                "resource_blocked": world.get("resource_blocked", ""),
                "plan_alignment_blocked": world.get("plan_alignment_blocked", ""),
                "browsing": browsing,
                "reading_library": library,
                "reading_notes": reading_notes,
                "latest_exchange": latest_exchange,
                "latest_exchange_at": world.get("last_chat_at", ""),
                "recent_state_evidence": day.state_log[-2:],
                "remaining_plans": [
                    {"at": str(timeline_item_datetime(item, day.date, meta=day.meta) or ""),
                     "activity": item.activity, "state": item.execution_state,
                     "protected": index in protected}
                    for index, item in enumerate(day.timeline)
                    if item.execution_state not in _TERMINAL_STATES
                ],
                "now": now.isoformat(),
            }
            session = "daily_life_execution_continuation_" + uuid.uuid4().hex[:8]
            try:
                with model_task("execution_continuation"):
                    text = await self._call_text_model(
                        provider,
                        cache_friendly_prompt(
                            "复核当前角色正在进行或暂缓的内部生活行动。只返回 JSON："
                            '{"decision":"continue|pause|resume|cancel|wait|expand","reason":"当前依据"}。'
                            "根据当前身体、对话、地点、穿搭和约定决定，不由时钟或关键词代替判断。"
                            "进展待确认不代表被打断，未观察的时长不算实际执行。"
                            "如果需要改日程、换装、处理身体需求、选择新行动、维护兴趣目标，或资料不足，"
                            "返回 expand 读取完整规划资料；不能用 continue 掩盖冲突。"
                            "browsing 是真实观看的检查点；rest.payload.activity=bilibili 可把此刻的浏览意愿交给真实浏览流程；"
                            "生活中有更重要的事可以安排，浏览流程会暂停。",
                            decision_prompt_context(continuation),
                        ), session, empty_retries=0,
                        primary_provider_id=provider_id,
                        timeout_seconds=min(45, self._config().llm_timeout_seconds),
                    )
                payload = extract_json_from_text(text)
                if (isinstance(payload, dict) and payload.get("decision") in {
                    "continue", "pause", "resume", "cancel", "wait",
                } and not any(payload.get(key) for key in (
                    "new_actions", "timeline_updates", "new_goals", "goal_updates",
                ))):
                    return {
                        "decision": payload["decision"],
                        "reason": str(payload.get("reason") or ""),
                        "achievement_reviews": [], "_sources": sources,
                        "_candidates": candidates, "_goal_review_due": False,
                        "_goal_source_key": goal_source_key, "_reflection_action_ids": set(),
                        "_achievement_candidates": [],
                        "_self_model": {"persona": persona,
                                        "preferences": [item.as_dict() for item in preferences],
                                        "skills": skill_context(world), "evidence_ids": sorted(sources)},
                    }
            finally:
                await self._close_text_session(session)
        living_objects = await self._archive().get_living_entities(
            str(chat_scope or "global"), kind="object", limit=24
        )
        # 仅传当前资源与作品摘要，避免每轮重复携带完整修改历史。
        living_objects = [{key: item[key] for key in (
            "id", "revision", "title", "state", "location", "owner", "quantity", "unit",
            "available_at", "reserved_by", "artifact_version", "series_id", "progress", "next_step",
            "content", "provenance",
        ) if key in item} for item in living_objects]
        for item in living_objects:
            if "content" in item:
                item["content"] = str(item["content"])[:1800]
        knowledge = await self._archive().get_temporal_facts(scope=str(chat_scope or "global"), limit=16, include_uncertain=True)
        try:
            recent_chats = await asyncio.wait_for(
                self._recent_chats(persona), timeout=5
            )
        except asyncio.TimeoutError:
            recent_chats = "参考会话暂未读取完成，优先依据当前已确认对话和生活事实"
        protected = protected_indexes(day, world)
        dynamic = {
            "persona": persona,
            "capabilities": capabilities,
            "preferences": [item.as_dict() for item in preferences],
            "self_model": {key: value for key, value in (kernel.get("self_model") or {}).items()
                           if key != "capabilities"},
            "allowed_source_ids": sorted(sources),
            "life_kernel": execution_context(world),
            "browsing": browsing,
            "unreflected_outcomes": unreflected_outcomes,
            "living_objects": living_objects,
            "knowledge": temporal_context(knowledge),
            "reading_library": library,
            "reading_notes": reading_notes,
            "resource_blocked": world.get("resource_blocked", ""),
            "plan_alignment_blocked": world.get("plan_alignment_blocked", ""),
            "open_threads": [
                item
                for item in (
                    world.get("kernel", {}).get("open_threads", [])
                    if isinstance(world.get("kernel"), dict)
                    else []
                )
                if isinstance(item, dict) and item.get("status") == "open"
            ][-12:],
            "state": day.state.as_dict() if day.state else {},
            "weather": day.weather,
            "current_place": day.meta.get("current_place", ""),
            "outfit": day.outfit,
            "wardrobe_context": day.meta.get("wardrobe_context", ""),
            "wardrobe_candidates": await self._wardrobe_context(day, now),
            "timeline": [
                {"timeline_index": index, "at": at.isoformat(), "activity": item.activity,
                 "execution_state": item.execution_state, "place": item.place,
                 "duration_minutes": item.duration_minutes,
                 "protected": index in protected}
                for index, item in enumerate(day.timeline)
                if item.execution_state not in _TERMINAL_STATES
                and (at := timeline_item_datetime(item, day.date, meta=day.meta))
            ],
            "rest_delay_until": day.meta.get("rest_delay_until", ""),
            "candidates": candidates,
            "world": {
                "body": world.get("body"),
                "run": run_context(world),
                "goals": [
                    {
                        **{
                            key: goal.get(key)
                            for key in (
                                "id",
                                "title",
                                "status",
                                "reason",
                                "skill",
                                "obstacle",
                            )
                        },
                        "steps": [
                            {
                                key: step.get(key)
                                for key in (
                                    "id",
                                    "title",
                                    "status",
                                    "action_type",
                                    "required_minutes",
                                    "practice_minutes",
                                    "depends_on",
                                    "acceptance",
                                    "evidence_kind",
                                    "next_step",
                                    "review_state",
                                    "review_pending",
                                )
                            }
                            for step in goal["steps"]
                        ],
                    }
                    for goal in world.get("goals", [])
                    if goal.get("status") in {"active", "blocked"}
                ],
                "skills": skill_context(world),
            },
            "recent_internal_actions": world.get("history", [])[-4:],
            "conversation_action_request": conversation_request,
            "recent_chats": recent_chats[-3000:],
            "pantry": await self._archive().get_pantry_items(limit=30),
            "latest_exchange": latest_exchange,
            "latest_exchange_at": world.get("last_chat_at", ""),
            "latest_exchange_scope": chat_scope or "",
            "recent_state_evidence": day.state_log[-4:],
            "goal_review_due": goal_review_due,
            "now": now.isoformat(),
        }
        fixed = build_life_decision_contract(dynamic)
        session = f"daily_life_execution_{uuid.uuid4().hex[:8]}"
        try:
            decision_call = self._call_text_model(
                provider,
                cache_friendly_prompt(fixed, decision_prompt_context(dynamic)),
                session,
                empty_retries=0,
                primary_provider_id=provider_id,
                timeout_seconds=min(45, self._config().llm_timeout_seconds),
            )
            text, reviews = await asyncio.gather(
                decision_call,
                review_goals(provider, achievement_candidates, call=self._call_text_model,
                             close=self._close_text_session,
                             provider_id=provider_id,
                             timeout=self._config().llm_timeout_seconds),
                return_exceptions=True,
            )
            payload = extract_json_from_text(text) if isinstance(text, str) else None
            reviews = reviews if isinstance(reviews, list) else []
            if not isinstance(payload, dict) and reviews:
                payload = {"decision": "wait", "reason": "先保存已经完成的阶段复核，稍后重新选择行动"}
            if isinstance(payload, dict):
                payload["achievement_reviews"] = reviews
                payload["_sources"] = sources
                payload["_candidates"] = candidates
                payload["_goal_review_due"] = goal_review_due
                payload["_goal_source_key"] = goal_source_key
                payload["_reflection_action_ids"] = {
                    item["action_id"] for item in unreflected_outcomes
                }
                payload["_achievement_candidates"] = achievement_candidates
                payload["_self_model"] = {
                    "persona": persona,
                    "preferences": dynamic["preferences"],
                    "skills": dynamic["world"].get("skills") or {},
                    "evidence_ids": sorted(sources),
                }
                return payload
            return {}
        finally:
            await self._close_text_session(session)


__all__ = ["ContinuousDecisionService"]
