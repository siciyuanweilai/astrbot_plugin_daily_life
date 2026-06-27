from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

LOG_PREFIX = "[日常生活]"
from .client import HostedMemOSClient


@dataclass(slots=True)
class MemOSMemoryItem:
    kind: str
    content: str
    timestamp: str = ""
    score: float = 0.0


CHAT_CONTEXT_ITEM_LIMIT = 3
CHAT_CONTEXT_CHAR_LIMIT = 360


def _compact_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].strip()


def _timestamp_text(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        stamp = value / 1000 if value > 1000000000000 else value
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))
        except Exception:
            return ""
    return str(value or "").strip()


def _first_text(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _compact_text(data.get(key), 400)
        if text:
            return text
    return ""


class HostedMemOSService:
    """daily_life 使用的 MemOS 托管服务适配层。"""

    def __init__(self, settings: Any):
        self.settings = settings
        self.client = HostedMemOSClient(
            base_url=getattr(settings, "base_url", ""),
            api_key=getattr(settings, "api_key", ""),
            timeout_seconds=getattr(settings, "timeout_seconds", 15.0),
        )

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "enabled", False) and self.client.available)

    @staticmethod
    def stable_hash(value: str, *, prefix: str = "dl") -> str:
        digest = hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:24]
        return f"{prefix}_{digest}"

    def _scope_key(self, meta: dict[str, str]) -> str:
        if meta.get("is_group") == "true" and meta.get("group_id") and meta.get("sender_profile_id"):
            return f"group:{meta['group_id']}:user:{meta['sender_profile_id']}"
        return meta.get("session_id") or meta.get("sender_profile_id") or "daily_life"

    def resolve_user_id(self, meta: dict[str, str]) -> str:
        return self._scope_key(meta)

    def conversation_id(self, meta: dict[str, str]) -> str:
        return self.stable_hash(self._scope_key(meta), prefix="dl_conv")

    def _base_payload(self, meta: dict[str, str]) -> dict[str, Any]:
        return {
            "user_id": self.resolve_user_id(meta),
            "conversation_id": self.conversation_id(meta),
        }

    async def search(self, query: str, meta: dict[str, str]) -> list[MemOSMemoryItem]:
        if not self.enabled or not str(query or "").strip():
            return []
        payload = {
            **self._base_payload(meta),
            "query": _compact_text(query, 500),
            "memory_limit_number": max(1, int(getattr(self.settings, "memory_limit_number", 5) or 5)),
            "include_preference": bool(getattr(self.settings, "include_preference", True)),
            "preference_limit_number": max(0, int(getattr(self.settings, "preference_limit_number", 4) or 4)),
        }
        result = await self.client.search_memory(payload)
        if not result.success:
            logger.debug(f"{LOG_PREFIX} MemOS 外部记忆检索跳过：{result.error}")
            return []
        return self._parse_search_result(result.data)

    def _parse_search_result(self, data: Any) -> list[MemOSMemoryItem]:
        if not isinstance(data, dict):
            return []
        items: list[MemOSMemoryItem] = []
        for raw in data.get("memory_detail_list") or []:
            if not isinstance(raw, dict):
                continue
            content = _first_text(raw, ("memory_value", "memory", "content", "text", "summary"))
            if content:
                items.append(
                    MemOSMemoryItem(
                        kind="fact",
                        content=content,
                        timestamp=_timestamp_text(raw.get("update_time") or raw.get("updated_at")),
                        score=float(raw.get("score") or raw.get("relativity") or 0.0),
                    )
                )
        for raw in data.get("preference_detail_list") or []:
            if not isinstance(raw, dict):
                continue
            content = _first_text(raw, ("preference", "memory_value", "content", "text", "summary"))
            if content:
                items.append(
                    MemOSMemoryItem(
                        kind="preference",
                        content=content,
                        timestamp=_timestamp_text(raw.get("update_time") or raw.get("updated_at")),
                        score=float(raw.get("score") or raw.get("relativity") or 0.0),
                    )
                )
        return items

    def format_hidden_context(self, items: list[MemOSMemoryItem]) -> str:
        if not items:
            return ""
        lines: list[str] = []
        configured_limit = max(1, int(getattr(self.settings, "max_context_items", CHAT_CONTEXT_ITEM_LIMIT) or CHAT_CONTEXT_ITEM_LIMIT))
        configured_chars = max(120, int(getattr(self.settings, "max_context_chars", CHAT_CONTEXT_CHAR_LIMIT) or CHAT_CONTEXT_CHAR_LIMIT))
        limit = min(configured_limit, CHAT_CONTEXT_ITEM_LIMIT)
        max_chars = min(configured_chars, CHAT_CONTEXT_CHAR_LIMIT)
        used = 0
        seen: set[str] = set()
        for item in items:
            content = _compact_text(item.content, 140)
            if not content or content in seen:
                continue
            seen.add(content)
            prefix = "偏好" if item.kind == "preference" else "事实"
            line = f"- {prefix}: {content}"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line)
            if len(lines) >= limit:
                break
        if not lines:
            return ""
        return "\n".join(lines)

    async def sync_selected_memory(
        self,
        *,
        meta: dict[str, str],
        user_message: str,
        summary: str,
        points: list[str] | None = None,
        preferences: list[str] | None = None,
    ) -> bool:
        if not self.enabled or not bool(getattr(self.settings, "sync_selected_memory", False)):
            return False
        details = [summary, *(points or [])[:4], *(preferences or [])[:4]]
        details_text = "\n".join(f"- {_compact_text(item, 180)}" for item in details if _compact_text(item, 180))
        if not details_text:
            return False
        payload = {
            **self._base_payload(meta),
            "messages": [
                {"role": "user", "content": _compact_text(user_message, 500)},
                {"role": "assistant", "content": f"长期记忆精选摘要：\n{details_text}"},
            ],
            "async_mode": False,
        }
        result = await self.client.add_message(payload)
        if not result.success:
            logger.debug(f"{LOG_PREFIX} MemOS 外部记忆精选同步失败：{result.error}")
        return result.success

    async def sync_selected_items(
        self,
        *,
        meta: dict[str, str],
        reason: str,
        items: list[str],
        user_message: str = "",
    ) -> bool:
        if not self.enabled or not bool(getattr(self.settings, "sync_selected_memory", False)):
            return False
        details_text = "\n".join(f"- {_compact_text(item, 220)}" for item in items[:12] if _compact_text(item, 220))
        if not details_text:
            return False
        prompt = _compact_text(reason or "同步本插件确认值得长期复用的生活记忆。", 120)
        messages = [{"role": "user", "content": _compact_text(user_message, 500) or prompt}]
        messages.append({"role": "assistant", "content": f"{prompt}\n{details_text}"})
        payload = {
            **self._base_payload(meta),
            "messages": messages,
            "async_mode": False,
        }
        result = await self.client.add_message(payload)
        if not result.success:
            logger.debug(f"{LOG_PREFIX} MemOS 外部记忆条目同步失败：{result.error}")
        return result.success

    async def sync_memo(self, *, meta: dict[str, str], memo: str) -> bool:
        if not self.enabled:
            return False
        memo = _compact_text(memo, 500)
        if not memo:
            return False
        payload = {
            **self._base_payload(meta),
            "messages": [
                {"role": "user", "content": "记录一条已确认的明日备忘录。"},
                {"role": "assistant", "content": f"明日备忘录：{memo}"},
            ],
            "async_mode": False,
        }
        result = await self.client.add_message(payload)
        if not result.success:
            logger.debug(f"{LOG_PREFIX} MemOS 外部记忆备忘录同步失败：{result.error}")
        return result.success

    async def sync_feedback(self, *, meta: dict[str, str], feedback: str) -> bool:
        if not self.enabled or not bool(getattr(self.settings, "sync_corrections", False)):
            return False
        feedback = _compact_text(feedback, 500)
        if not feedback:
            return False
        payload = {
            **self._base_payload(meta),
            "feedback_content": feedback,
        }
        result = await self.client.add_feedback(payload)
        if not result.success:
            logger.debug(f"{LOG_PREFIX} MemOS 外部记忆纠错同步失败：{result.error}")
        return result.success
