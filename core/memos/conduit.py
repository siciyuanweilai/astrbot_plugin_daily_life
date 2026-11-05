from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import time
import unicodedata
from typing import Any

from astrbot.api import logger

from .hosted import HostedMemOSService
from ..addresses import habitual_address

from typing import Protocol

LOG_PREFIX = "[日常生活]"


class MemosComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _MEMOS_CONTEXT_CACHE_LIMIT: Any
    _MEMOS_CONTEXT_CACHE_TTL_SECONDS: Any
    _event_context_meta: Any
    _memos_context_cache_key: Any
    _memos_context_cache_store: Any
    _memos_context_pending_store: Any
    _memos_service: Any
    _schedule_background_task: Any
    config: Any
    contact_resolver: Any
    memos: Any
    sync_memos_correction: Any
    sync_memos_memo: Any
    sync_memos_selected_items: Any


class MemosComponent:
    def __init__(self, context: MemosComponentContext) -> None:
        self.context = context

    _MEMOS_CONTEXT_CACHE_TTL_SECONDS = 20.0
    _MEMOS_CONTEXT_CACHE_LIMIT = 256

    def _create_memos_service(self, config: Any | None = None) -> HostedMemOSService:
        settings = config if config is not None else getattr(self.context, "config", None)
        return HostedMemOSService(getattr(settings, "memos", None))

    async def close_memos_service(self) -> None:
        pending = list(self._memos_context_pending().values())
        self._memos_context_pending().clear()
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _memos_service(self) -> HostedMemOSService | None:
        service = getattr(self.context, "memos", None)
        return service if isinstance(service, HostedMemOSService) else None

    async def build_memos_hidden_context(
        self,
        event: Any,
        message: str = "",
        sender_name: str = "",
    ) -> str:
        service = self.context._memos_service()
        if not service or not service.enabled or event is None:
            return ""
        try:
            now = datetime.datetime.now()
            sender_name = (
                sender_name or self.context.contact_resolver.cached_event_sender(event)
            )
            meta = await self.context._event_context_meta(
                event, sender_name, now, lookup_group_name=False
            )
            query = str(message or getattr(event, "message_str", "") or "").strip()
            if meta.get("is_group") == "true" and sender_name and query:
                query = f"{sender_name}: {query}"
            cache_key = self.context._memos_context_cache_key(meta, query)
            cached = self._memos_context_from_cache(cache_key)
            if cached is not None:
                return cached
            timeout = max(
                0.2,
                float(
                    getattr(service.settings, "injection_timeout_seconds", 0.8) or 0.8
                ),
            )
            pending = self._memos_context_pending()
            task = pending.get(cache_key)
            if task is None or task.done():
                task = asyncio.create_task(
                    self._load_memos_hidden_context(
                        service,
                        query,
                        meta,
                        cache_key,
                    )
                )
                pending[cache_key] = task
                task.add_done_callback(
                    lambda done: self._forget_memos_context_task(cache_key, done)
                )
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.debug(f"{LOG_PREFIX} MemOS 记忆参考超时，已跳过本轮注入")
            return ""
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} MemOS 记忆参考跳过：{exc}")
            return ""

    @staticmethod
    def _memos_context_cache_key(meta: dict[str, str], query: str) -> str:
        normalized_query = unicodedata.normalize(
            "NFKC", " ".join(str(query or "").split())
        ).casefold()
        raw = json.dumps(
            {
                "session_id": str(meta.get("session_id") or "").strip(),
                "sender_profile_id": str(
                    meta.get("sender_profile_id") or ""
                ).strip(),
                "group_id": str(meta.get("group_id") or "").strip(),
                "query": normalized_query,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _memos_context_pending(self) -> dict[str, asyncio.Task[str]]:
        pending = getattr(self.context, "_memos_context_pending_store", None)
        if not isinstance(pending, dict):
            pending = {}
            self.context._memos_context_pending_store = pending
        return pending

    async def _load_memos_hidden_context(
        self,
        service: HostedMemOSService,
        query: str,
        meta: dict[str, str],
        cache_key: str,
    ) -> str:
        items = await service.search(query, meta)
        context = service.format_hidden_context(items)
        self._set_memos_context_cache(cache_key, context)
        return context

    def _forget_memos_context_task(
        self, key: str, task: asyncio.Task[str]
    ) -> None:
        pending = self._memos_context_pending()
        if pending.get(key) is task:
            pending.pop(key, None)
        if not task.cancelled():
            task.exception()

    def _memos_context_cache(self) -> dict[str, dict[str, Any]]:
        cache = getattr(self.context, "_memos_context_cache_store", None)
        if not isinstance(cache, dict):
            self.context._memos_context_cache_store = {}
            cache = self.context._memos_context_cache_store
        now_ts = time.monotonic()
        for key, item in list(cache.items()):
            if (
                not isinstance(item, dict)
                or now_ts - float(item.get("ts", 0.0) or 0.0)
                > self.context._MEMOS_CONTEXT_CACHE_TTL_SECONDS
            ):
                cache.pop(key, None)
        return cache

    def _memos_context_from_cache(self, key: str) -> str | None:
        item = self._memos_context_cache().get(key)
        if not isinstance(item, dict):
            return None
        return str(item.get("text") or "")

    def _set_memos_context_cache(self, key: str, text: str) -> None:
        cache = self._memos_context_cache()
        cache.pop(key, None)
        cache[key] = {
            "ts": time.monotonic(),
            "text": str(text or ""),
        }
        while len(cache) > self.context._MEMOS_CONTEXT_CACHE_LIMIT:
            cache.pop(next(iter(cache)))



    def _memos_text(self, value: Any, limit: int = 240) -> str:
        return " ".join(str(value or "").split())[:limit].strip()

    def _memos_record_text(self, label: str, *parts: Any, limit: int = 360) -> str:
        values = [self._memos_text(part, limit) for part in parts]
        values = [value for value in values if value]
        if not values:
            return ""
        return self._memos_text(f"{label}：" + "；".join(values), limit)

    def _memos_items_from_payload(
        self,
        payload: dict[str, Any],
        saved_summary: Any = None,
        saved_records: dict[str, list[Any]] | None = None,
    ) -> list[str]:
        saved_records = saved_records or {}
        items: list[str] = []
        seen: set[str] = set()

        def add(text: str) -> None:
            text = self._memos_text(text, 360)
            if text and text not in seen:
                seen.add(text)
                items.append(text)

        if saved_summary is not None:
            add(
                self._memos_record_text(
                    "聊天摘要",
                    getattr(saved_summary, "brief", ""),
                    getattr(saved_summary, "long_summary", ""),
                )
            )

        speaker_relationship = saved_records.get("speaker_relationship") or []
        for item in speaker_relationship:
            if isinstance(item, dict):
                add(
                    self._memos_record_text(
                        "关系画像",
                        item.get("name"),
                        "习惯称呼：" + habitual_address(item) if habitual_address(item) else "",
                        "、".join(
                            str(tag) for tag in item.get("subjective_tags", [])[:6]
                        )
                        if isinstance(item.get("subjective_tags"), list)
                        else "",
                        item.get("relationship_story"),
                        item.get("note"),
                    )
                )

        for point in (
            payload.get("relationship_points", [])
            if isinstance(payload.get("relationship_points"), list)
            else []
        ):
            add(self._memos_record_text("关系画像", point))

        target_records = saved_records.get("memory_targets")
        if target_records is None:
            target_records = (
                payload.get("memory_targets", [])
                if isinstance(payload.get("memory_targets"), list)
                else []
            )
        for target in target_records:
            if not isinstance(target, dict):
                continue
            name = (
                target.get("name")
                or target.get("alias")
                or target.get("profile_id")
            )
            points = (
                target.get("points") if isinstance(target.get("points"), list) else []
            )
            tags = target.get("subjective_tags") or target.get("tags")
            tags = tags if isinstance(tags, list) else []
            add(
                self._memos_record_text(
                    "关系画像",
                    name,
                    "习惯称呼：" + habitual_address(target) if habitual_address(target) else "",
                    target.get("relationship_story"),
                    target.get("note"),
                    "、".join(str(item) for item in tags[:6]),
                    "、".join(str(item) for item in points[:4]),
                )
            )

        preferences = saved_records.get("preferences")
        if preferences is None:
            preferences = (
                payload.get("preference_points", [])
                if isinstance(payload.get("preference_points"), list)
                else []
            )
        for pref in preferences:
            if isinstance(pref, dict):
                add(
                    self._memos_record_text(
                        "稳定偏好",
                        pref.get("category"),
                        pref.get("content"),
                        pref.get("evidence"),
                    )
                )
            else:
                add(
                    self._memos_record_text(
                        "稳定偏好",
                        getattr(pref, "category", ""),
                        getattr(pref, "content", pref),
                        getattr(pref, "evidence", ""),
                    )
                )

        for adjustment in saved_records.get("life_adjustments", []):
            add(
                self._memos_record_text(
                    "生活纠偏",
                    getattr(adjustment, "label", "")
                    or getattr(adjustment, "category", "")
                    or getattr(adjustment, "target_type", ""),
                    getattr(adjustment, "content", "")
                    or getattr(adjustment, "correction", "")
                    or getattr(adjustment, "focus_key", ""),
                    getattr(adjustment, "reason", "")
                    or getattr(adjustment, "evidence", ""),
                )
            )

        for feedback in saved_records.get("behavior_feedback", []):
            add(
                self._memos_record_text(
                    "行为反馈",
                    getattr(feedback, "scene", ""),
                    getattr(feedback, "action", ""),
                    getattr(feedback, "feedback", ""),
                    getattr(feedback, "result", ""),
                    getattr(feedback, "reason", ""),
                )
            )

        for profile in saved_records.get("expression_profiles", []):
            add(
                self._memos_record_text(
                    "表达方式",
                    getattr(profile, "label", ""),
                    getattr(profile, "tone", ""),
                    "、".join(getattr(profile, "habits", [])[:6]),
                    "避免 " + "、".join(getattr(profile, "avoid", [])[:4])
                    if getattr(profile, "avoid", [])
                    else "",
                    getattr(profile, "evidence", ""),
                )
            )

        for episode in saved_records.get("life_episodes", []):
            add(
                self._memos_record_text(
                    "长期生活事件",
                    getattr(episode, "title", ""),
                    getattr(episode, "summary", ""),
                    getattr(episode, "impact", ""),
                )
            )

        return items

    async def sync_memos_selected_items(
        self,
        meta: dict[str, str],
        items: list[str],
        *,
        reason: str,
        user_message: str = "",
    ) -> None:
        service = self.context._memos_service()
        if not service or not service.enabled:
            return
        await service.sync_selected_items(
            meta=meta, reason=reason, items=items, user_message=user_message
        )

    def schedule_memos_selected_items(
        self,
        meta: dict[str, str],
        items: list[str],
        *,
        reason: str,
        user_message: str = "",
        marker: str = "",
    ) -> bool:
        service = self.context._memos_service()
        if (
            not service
            or not service.enabled
            or not bool(getattr(service.settings, "sync_selected_memory", False))
        ):
            return False
        compact_items = [
            self._memos_text(item, 360) for item in items if self._memos_text(item, 360)
        ]
        if not compact_items:
            return False
        key = HostedMemOSService.stable_hash(
            json.dumps(
                {"user": service.resolve_user_id(meta), "reason": reason,
                 "marker": marker, "items": compact_items, "message": user_message},
                ensure_ascii=False, sort_keys=True,
            ),
            prefix="memos_items",
        )
        scheduler = getattr(self.context, "_schedule_background_task", None)
        coro = self.context.sync_memos_selected_items(
            meta,
            compact_items,
            reason=reason,
            user_message=user_message,
        )
        if callable(scheduler):
            return bool(scheduler(coro, label="MemOS 精选条目同步", key=key))
        coro.close()
        return False

    async def sync_memos_memo(self, meta: dict[str, str], memo: str) -> None:
        service = self.context._memos_service()
        if not service or not service.enabled:
            return
        await service.sync_memo(meta=meta, memo=memo)

    def schedule_memos_memo(self, meta: dict[str, str], memo: str) -> bool:
        service = self.context._memos_service()
        if not service or not service.enabled:
            return False
        marker = str(memo or "").strip()
        if not marker:
            return False
        key = HostedMemOSService.stable_hash(
            f"{meta.get('session_id', '')}:{marker}", prefix="memos_memo"
        )
        scheduler = getattr(self.context, "_schedule_background_task", None)
        coro = self.context.sync_memos_memo(meta, marker)
        if callable(scheduler):
            return bool(scheduler(coro, label="MemOS 备忘录同步", key=key))
        coro.close()
        return False

    async def sync_memos_correction(
        self, correction: Any, meta: dict[str, str]
    ) -> None:
        service = self.context._memos_service()
        if not service or not service.enabled:
            return
        text = str(getattr(correction, "correction", "") or "").strip()
        evidence = str(getattr(correction, "evidence", "") or "").strip()
        if evidence:
            text = f"{text}\n依据：{evidence}" if text else evidence
        await service.sync_feedback(meta=meta, feedback=text)

    def schedule_memos_correction(self, correction: Any, meta: dict[str, str]) -> bool:
        service = self.context._memos_service()
        if (
            not service
            or not service.enabled
            or not bool(getattr(service.settings, "sync_corrections", False))
        ):
            return False
        marker = str(
            getattr(correction, "id", "") or getattr(correction, "correction", "") or ""
        )
        key = HostedMemOSService.stable_hash(
            f"{meta.get('session_id', '')}:{marker}", prefix="memos_fix"
        )
        scheduler = getattr(self.context, "_schedule_background_task", None)
        coro = self.context.sync_memos_correction(correction, meta)
        if callable(scheduler):
            return bool(scheduler(coro, label="MemOS 记忆同步", key=key))
        coro.close()
        return False
