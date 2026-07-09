from __future__ import annotations

import asyncio
import datetime
import time
from typing import Any

from astrbot.api import logger

from .hosted import HostedMemOSService

LOG_PREFIX = "[日常生活]"


class MemosMixin:
    def _create_memos_service(self) -> HostedMemOSService:
        return HostedMemOSService(getattr(self.config, "memos", None))

    async def close_memos_service(self) -> None:
        return None

    def _memos_service(self) -> HostedMemOSService | None:
        service = getattr(self, "memos", None)
        return service if isinstance(service, HostedMemOSService) else None

    async def build_memos_hidden_context(
        self,
        event: Any,
        message: str = "",
        sender_name: str = "",
    ) -> str:
        service = self._memos_service()
        if not service or not service.enabled or event is None:
            return ""
        try:
            now = datetime.datetime.now()
            sender_name = sender_name or await self.contact_resolver.resolve_event_sender(event)
            meta = await self._event_context_meta(event, sender_name, now)
            query = str(message or getattr(event, "message_str", "") or "").strip()
            if meta.get("is_group") == "true" and sender_name and query:
                query = f"{sender_name}: {query}"
            cache_key = self._memos_context_cache_key(meta, query)
            cached = self._memos_context_from_cache(cache_key)
            if cached is not None:
                return cached
            timeout = max(0.2, float(getattr(service.settings, "injection_timeout_seconds", 0.8) or 0.8))
            items = await asyncio.wait_for(service.search(query, meta), timeout=timeout)
            context = service.format_hidden_context(items)
            self._set_memos_context_cache(cache_key, context)
            return context
        except asyncio.TimeoutError:
            logger.debug(f"{LOG_PREFIX} MemOS 记忆参考超时，已跳过本轮注入")
            return ""
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} MemOS 记忆参考跳过：{exc}")
            return ""

    @staticmethod
    def _memos_context_cache_key(meta: dict[str, str], query: str) -> str:
        return "|".join(
            [
                str(meta.get("session_id") or ""),
                str(meta.get("sender_profile_id") or ""),
                str(meta.get("group_id") or ""),
                str(query or "")[:240],
            ]
        )

    def _memos_context_cache(self) -> dict[str, dict[str, Any]]:
        cache = getattr(self, "_memos_context_cache_store", None)
        if not isinstance(cache, dict):
            self._memos_context_cache_store = {}
            cache = self._memos_context_cache_store
        now_ts = time.monotonic()
        for key, item in list(cache.items()):
            if not isinstance(item, dict) or now_ts - float(item.get("ts", 0.0) or 0.0) > 20.0:
                cache.pop(key, None)
        return cache

    def _memos_context_from_cache(self, key: str) -> str | None:
        item = self._memos_context_cache().get(key)
        if not isinstance(item, dict):
            return None
        return str(item.get("text") or "")

    def _set_memos_context_cache(self, key: str, text: str) -> None:
        self._memos_context_cache()[key] = {"ts": time.monotonic(), "text": str(text or "")}

    async def sync_memos_selected_memory(
        self,
        payload: dict[str, Any],
        meta: dict[str, str],
        *,
        user_message: str = "",
        summary: Any = None,
    ) -> None:
        service = self._memos_service()
        if not service or not service.enabled:
            return
        summary_text = ""
        if summary is not None:
            summary_text = str(getattr(summary, "brief", "") or getattr(summary, "long_summary", "") or "").strip()
        summary_text = summary_text or str(payload.get("brief") or payload.get("long_summary") or "").strip()
        points = [
            str(item or "").strip()
            for item in payload.get("relationship_points", [])
            if str(item or "").strip()
        ] if isinstance(payload.get("relationship_points"), list) else []
        preferences = []
        if isinstance(payload.get("preference_points"), list):
            for item in payload["preference_points"]:
                if isinstance(item, dict):
                    text = str(item.get("content") or "").strip()
                else:
                    text = str(item or "").strip()
                if text:
                    preferences.append(text)
        await service.sync_selected_memory(
            meta=meta,
            user_message=user_message,
            summary=summary_text,
            points=points,
            preferences=preferences,
        )

    def schedule_memos_selected_memory(
        self,
        payload: dict[str, Any],
        meta: dict[str, str],
        *,
        user_message: str = "",
        summary: Any = None,
    ) -> bool:
        service = self._memos_service()
        if not service or not service.enabled or not bool(getattr(service.settings, "sync_selected_memory", False)):
            return False
        marker = str(getattr(summary, "id", "") or payload.get("brief") or payload.get("long_summary") or user_message)
        key = HostedMemOSService.stable_hash(f"{meta.get('session_id', '')}:{marker}", prefix="memos_memory")
        scheduler = getattr(self, "_schedule_background_task", None)
        coro = self.sync_memos_selected_memory(
            payload,
            meta,
            user_message=user_message,
            summary=summary,
        )
        if callable(scheduler):
            return bool(scheduler(coro, label="MemOS 记忆同步", key=key))
        coro.close()
        return False

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
            add(self._memos_record_text("聊天摘要", getattr(saved_summary, "brief", ""), getattr(saved_summary, "long_summary", "")))

        speaker_relationship = saved_records.get("speaker_relationship") or []
        for item in speaker_relationship:
            if isinstance(item, dict):
                add(
                    self._memos_record_text(
                        "关系画像",
                        item.get("name"),
                        item.get("subjective_name"),
                        "、".join(str(tag) for tag in item.get("subjective_tags", [])[:6])
                        if isinstance(item.get("subjective_tags"), list)
                        else "",
                        item.get("relationship_story"),
                        item.get("note"),
                    )
                )

        for point in payload.get("relationship_points", []) if isinstance(payload.get("relationship_points"), list) else []:
            add(self._memos_record_text("关系画像", point))

        target_records = saved_records.get("memory_targets")
        if target_records is None:
            target_records = payload.get("memory_targets", []) if isinstance(payload.get("memory_targets"), list) else []
        for target in target_records:
            if not isinstance(target, dict):
                continue
            name = target.get("subjective_name") or target.get("name") or target.get("alias") or target.get("profile_id")
            points = target.get("points") if isinstance(target.get("points"), list) else []
            tags = target.get("subjective_tags") or target.get("tags")
            tags = tags if isinstance(tags, list) else []
            add(
                self._memos_record_text(
                    "关系画像",
                    name,
                    target.get("relationship_story"),
                    target.get("note"),
                    "、".join(str(item) for item in tags[:6]),
                    "、".join(str(item) for item in points[:4]),
                )
            )

        preferences = saved_records.get("preferences")
        if preferences is None:
            preferences = payload.get("preference_points", []) if isinstance(payload.get("preference_points"), list) else []
        for pref in preferences:
            if isinstance(pref, dict):
                add(self._memos_record_text("稳定偏好", pref.get("category"), pref.get("content"), pref.get("evidence")))
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
                    getattr(adjustment, "label", "") or getattr(adjustment, "category", "") or getattr(adjustment, "target_type", ""),
                    getattr(adjustment, "content", "") or getattr(adjustment, "correction", "") or getattr(adjustment, "focus_key", ""),
                    getattr(adjustment, "reason", "") or getattr(adjustment, "evidence", ""),
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
                    "避免 " + "、".join(getattr(profile, "avoid", [])[:4]) if getattr(profile, "avoid", []) else "",
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
        service = self._memos_service()
        if not service or not service.enabled:
            return
        await service.sync_selected_items(meta=meta, reason=reason, items=items, user_message=user_message)

    def schedule_memos_selected_items(
        self,
        meta: dict[str, str],
        items: list[str],
        *,
        reason: str,
        user_message: str = "",
        marker: str = "",
    ) -> bool:
        service = self._memos_service()
        if not service or not service.enabled or not bool(getattr(service.settings, "sync_selected_memory", False)):
            return False
        compact_items = [self._memos_text(item, 360) for item in items if self._memos_text(item, 360)]
        if not compact_items:
            return False
        marker = marker or "|".join(compact_items[:3])
        key = HostedMemOSService.stable_hash(f"{meta.get('session_id', '')}:{reason}:{marker}", prefix="memos_items")
        scheduler = getattr(self, "_schedule_background_task", None)
        coro = self.sync_memos_selected_items(
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
        service = self._memos_service()
        if not service or not service.enabled:
            return
        await service.sync_memo(meta=meta, memo=memo)

    def schedule_memos_memo(self, meta: dict[str, str], memo: str) -> bool:
        service = self._memos_service()
        if not service or not service.enabled:
            return False
        marker = str(memo or "").strip()
        if not marker:
            return False
        key = HostedMemOSService.stable_hash(f"{meta.get('session_id', '')}:{marker}", prefix="memos_memo")
        scheduler = getattr(self, "_schedule_background_task", None)
        coro = self.sync_memos_memo(meta, marker)
        if callable(scheduler):
            return bool(scheduler(coro, label="MemOS 备忘录同步", key=key))
        coro.close()
        return False

    async def sync_memos_correction(self, correction: Any, meta: dict[str, str]) -> None:
        service = self._memos_service()
        if not service or not service.enabled:
            return
        text = str(getattr(correction, "correction", "") or "").strip()
        evidence = str(getattr(correction, "evidence", "") or "").strip()
        if evidence:
            text = f"{text}\n依据：{evidence}" if text else evidence
        await service.sync_feedback(meta=meta, feedback=text)

    def schedule_memos_correction(self, correction: Any, meta: dict[str, str]) -> bool:
        service = self._memos_service()
        if not service or not service.enabled or not bool(getattr(service.settings, "sync_corrections", False)):
            return False
        marker = str(getattr(correction, "id", "") or getattr(correction, "correction", "") or "")
        key = HostedMemOSService.stable_hash(f"{meta.get('session_id', '')}:{marker}", prefix="memos_fix")
        scheduler = getattr(self, "_schedule_background_task", None)
        coro = self.sync_memos_correction(correction, meta)
        if callable(scheduler):
            return bool(scheduler(coro, label="MemOS 记忆同步", key=key))
        coro.close()
        return False
