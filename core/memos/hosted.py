from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from .connector import HostedMemOSClient, MemOSClientResult

LOG_PREFIX = "[日常生活]"


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
        self._pending_writes: dict[str, asyncio.Task[MemOSClientResult]] = {}
        self._recent_writes: dict[str, tuple[float, MemOSClientResult, int]] = {}
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._pending_writes.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending_writes.clear()
        self._recent_writes.clear()
        close = getattr(self.client, "close", None)
        if callable(close):
            await close()

    async def _submit_write(
        self, endpoint: str, payload: dict[str, Any], label: str
    ) -> bool:
        if self._closed:
            return False
        # 完整请求（含人物、会话、所有条目）相同才合并，避免串用记忆。
        key = self.stable_hash(endpoint + json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ))
        now = time.monotonic()
        cached = self._recent_writes.get(key)
        if cached and cached[0] > now:
            return cached[1].success
        pending = self._pending_writes.get(key)
        if pending is None:
            if len(self._pending_writes) >= 32:
                logger.debug(f"{LOG_PREFIX} MemOS 提交等待队列已满，本地记忆保留")
                return False
            pending = asyncio.create_task(self._perform_write(key, endpoint, payload, label))
            self._pending_writes[key] = pending
            pending.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        # 一个等待方取消不取消其他等待方共用的提交；卸载由 close 统一取消。
        return (await asyncio.shield(pending)).success

    async def _perform_write(
        self, key: str, endpoint: str, payload: dict[str, Any], label: str
    ) -> MemOSClientResult:
        try:
            try:
                result = await (
                    self.client.add_message(payload) if endpoint == "/add/message"
                    else self.client.add_feedback(payload)
                )
            except Exception as exc:
                result = MemOSClientResult(False, error=f"{type(exc).__name__}; operation={endpoint}")
            previous = self._recent_writes.pop(key, None)
            failures = 0 if result.success else min((previous[2] if previous else 0) + 1, 5)
            ttl = 300.0 if result.success else min(30.0 * 2 ** (failures - 1), 300.0)
            self._recent_writes[key] = (time.monotonic() + ttl, result, failures)
            while len(self._recent_writes) > 256:
                self._recent_writes.pop(next(iter(self._recent_writes)))
            if not result.success:
                logger.debug(
                    f"{LOG_PREFIX} MemOS 外部记忆{label}同步未确认：{result.error or 'UnknownError'}；"
                    f"相同请求退避={ttl:g}秒；本地记忆保留"
                )
            elif isinstance(result.data, dict) and result.data.get("status") == "running":
                logger.debug(f"{LOG_PREFIX} MemOS 外部记忆{label}已受理，服务端继续整理")
            return result
        finally:
            self._pending_writes.pop(key, None)

    @property
    def enabled(self) -> bool:
        return bool(not self._closed and getattr(self.settings, "enabled", False) and self.client.available)

    @staticmethod
    def stable_hash(value: str, *, prefix: str = "dl") -> str:
        digest = hashlib.sha256(
            str(value or "").encode("utf-8", errors="ignore")
        ).hexdigest()[:24]
        return f"{prefix}_{digest}"

    def _scope_key(self, meta: dict[str, str]) -> str:
        if (
            meta.get("is_group") == "true"
            and meta.get("group_id")
            and meta.get("sender_profile_id")
        ):
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
            "memory_limit_number": max(
                1, int(getattr(self.settings, "memory_limit_number", 5) or 5)
            ),
            "include_preference": bool(
                getattr(self.settings, "include_preference", True)
            ),
            "preference_limit_number": max(
                0, int(getattr(self.settings, "preference_limit_number", 4) or 4)
            ),
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
            content = _first_text(
                raw, ("memory_value", "memory", "content", "text", "summary")
            )
            if content:
                items.append(
                    MemOSMemoryItem(
                        kind="fact",
                        content=content,
                        timestamp=_timestamp_text(
                            raw.get("update_time") or raw.get("updated_at")
                        ),
                        score=float(raw.get("score") or raw.get("relativity") or 0.0),
                    )
                )
        for raw in data.get("preference_detail_list") or []:
            if not isinstance(raw, dict):
                continue
            content = _first_text(
                raw, ("preference", "memory_value", "content", "text", "summary")
            )
            if content:
                items.append(
                    MemOSMemoryItem(
                        kind="preference",
                        content=content,
                        timestamp=_timestamp_text(
                            raw.get("update_time") or raw.get("updated_at")
                        ),
                        score=float(raw.get("score") or raw.get("relativity") or 0.0),
                    )
                )
        return items

    def format_hidden_context(self, items: list[MemOSMemoryItem]) -> str:
        if not items:
            return ""
        lines: list[str] = []
        configured_limit = max(
            1,
            int(
                getattr(self.settings, "max_context_items", CHAT_CONTEXT_ITEM_LIMIT)
                or CHAT_CONTEXT_ITEM_LIMIT
            ),
        )
        configured_chars = max(
            120,
            int(
                getattr(self.settings, "max_context_chars", CHAT_CONTEXT_CHAR_LIMIT)
                or CHAT_CONTEXT_CHAR_LIMIT
            ),
        )
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


    async def sync_selected_items(
        self,
        *,
        meta: dict[str, str],
        reason: str,
        items: list[str],
        user_message: str = "",
    ) -> bool:
        if not self.enabled or not bool(
            getattr(self.settings, "sync_selected_memory", False)
        ):
            return False
        details_text = "\n".join(
            f"- {_compact_text(item, 220)}"
            for item in items[:12]
            if _compact_text(item, 220)
        )
        if not details_text:
            return False
        prompt = _compact_text(reason or "同步本插件确认值得长期复用的生活记忆。", 120)
        messages = [
            {"role": "user", "content": _compact_text(user_message, 500) or prompt}
        ]
        messages.append({"role": "assistant", "content": f"{prompt}\n{details_text}"})
        payload = {
            **self._base_payload(meta),
            "messages": messages,
            "async_mode": True,
        }
        return await self._submit_write("/add/message", payload, "条目")

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
            "async_mode": True,
        }
        return await self._submit_write("/add/message", payload, "备忘录")

    async def sync_feedback(self, *, meta: dict[str, str], feedback: str) -> bool:
        if not self.enabled or not bool(
            getattr(self.settings, "sync_corrections", False)
        ):
            return False
        feedback = _compact_text(feedback, 500)
        if not feedback:
            return False
        payload = {
            **self._base_payload(meta),
            "feedback_content": feedback,
        }
        return await self._submit_write("/add/feedback", payload, "纠错")
