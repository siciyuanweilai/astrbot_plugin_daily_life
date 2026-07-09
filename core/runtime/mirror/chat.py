from __future__ import annotations

import datetime
from typing import Any


class SnapshotChatMixin:
    async def _refresh_state_for_chat_background(
        self,
        target_date_str: str,
        now: datetime.datetime,
        source_event: Any = None,
    ) -> None:
        if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
            return
        await self._run_autonomous_life_check(
            target_date_str,
            now,
            source="chat",
            detail="收到消息准备回复：按刷新间隔在后台检查实时状态。",
            status_reason="chat_state_refresh",
            respect_quiet_hours=False,
            update_weather=False,
            source_event=source_event,
        )

    async def _capture_chat_context_background(
        self,
        event: Any,
        now: datetime.datetime,
    ) -> None:
        if self.event_was_recalled(event, log_skip=True):
            return
        sender_name = await self.contact_resolver.resolve_event_sender(event)
        if self.event_was_recalled(event, log_skip=True):
            return
        await self.maybe_collect_emoji_assets_from_event(event, now, sender_name=sender_name)
        if self.event_was_recalled(event, log_skip=True):
            return
        await self.maybe_capture_commitment_from_event(event, now, sender_name=sender_name)
        if self.event_was_recalled(event, log_skip=True):
            return
        await self.maybe_capture_chat_memory_from_event(event, now, sender_name=sender_name)
        if self.event_was_recalled(event, log_skip=True):
            return
        cache = getattr(self, "_injection_snapshot_cache", None)
        if isinstance(cache, dict):
            cache.clear()
