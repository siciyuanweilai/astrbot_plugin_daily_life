"""会话级主动发送预约及只读事实快照；不获取普通聊天会话锁。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

from ..clock import TIMEZONE, now as life_now
from ..browsing.bookmarks import BrowseArchive
from ..browsing.receipts import BrowseReceiptStore
from .contact import contact_rhythm
from .mirror.sharefacts import project as project_share_facts


class ShareContactGate:
    def __init__(self, runtime):
        self.runtime = runtime
        self.leases = {}

    def claim(self, scope, owner):
        now = time.monotonic()
        current = self.leases.get(scope)
        if current and current[1] > now and current[0] != owner:
            return False
        self.leases[scope] = (owner, now + 120)
        if len(self.leases) > 1024:
            self.leases = {k: v for k, v in self.leases.items() if v[1] > now}
        return True

    def allow_life(self, event):
        scope = str(getattr(event, "unified_msg_origin", "") or "")
        if not scope:
            return True
        owner = getattr(event, "_daily_life_contact_owner", "")
        if not owner:
            owner = "life:" + uuid.uuid4().hex
            event._daily_life_contact_owner = owner
        current = self.leases.get(scope)
        if current and current[0].startswith("life:"):
            # 生活插件内部的回复节奏仍由原调度控制；此处只协调跨插件发送。
            self.leases.pop(scope, None)
        return self.claim(scope, owner)

    async def snapshot(self, scope):
        world = await self.runtime.archive.get_continuous_life()
        run = (world or {}).get("run") or {}
        action = run.get("action") or {}
        now = life_now().replace(tzinfo=TIMEZONE)
        previous = await self.runtime.archive.get_conversation_receipt_before(
            scope, now.timestamp()
        )
        chat = sorted(
            (person, str(token))
            for (target, person), token in getattr(
                self.runtime, "_media_chat_tokens", {}
            ).items()
            if target == scope
        )
        # 机器人发送回执只控制联系间隔，不代表用户对话或生活事实变化。
        payload = {
            "date": now.date().isoformat(),
            "action": {
                key: action.get(key) for key in ("action_id", "target", "action_type")
            },
            "status": run.get("status"),
            "observation": run.get("observation_state"),
            "chat": chat,
        }
        reader = getattr(self.runtime.archive, "get_day", None)
        if callable(reader):
            resolver = getattr(self.runtime, "resolve_injection_target", None)
            date = (await resolver(life_now()))[0] if callable(resolver) else now.date().isoformat()
            day = await reader(date)
            if day is not None:
                projected = project_share_facts(day)
                payload["appearance_version"] = projected["appearance_version"]
                payload["action_progress_version"] = projected["action_progress_version"]
        if callable(getattr(self.runtime.archive, "_run_db", None)):
            records = await BrowseArchive(self.runtime.archive).list(
                scope=scope, limit=200
            )
            payload["browsing_delivered"] = sorted(
                record["id"]
                for record in records
                if scope in (record.get("shared_to") or {})
                or "qzone_broadcast" in (record.get("shared_to") or {})
            )
            payload["browsing_pending"] = sorted(
                await BrowseReceiptStore(self.runtime.archive).pending_ids(scope)
            )
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(), previous

    async def coordinate(self, scope, *, owner, action="reserve", expected=None, shared=True):
        scope, owner = str(scope or ""), str(owner or "")
        if not scope or not owner:
            return {"allowed": False, "reason": "缺少发送目标或预约标识"}
        if action == "release":
            if self.leases.get(scope, (None,))[0] == owner:
                self.leases.pop(scope, None)
            return {"allowed": True}
        if action not in {"reserve", "check"}:
            return {"allowed": False, "reason": "不支持的预约操作"}
        if not self.claim(scope, owner):
            return {
                "allowed": False,
                "retryable": True,
                "reason": "该会话已有其他主动联系任务",
            }
        try:
            snapshot, previous = await self.snapshot(scope)
        except BaseException:
            if self.leases.get(scope, (None,))[0] == owner:
                self.leases.pop(scope, None)
            raise
        if not self.claim(scope, owner):
            return {
                "allowed": False,
                "retryable": True,
                "reason": "该会话已有其他主动联系任务",
            }
        if expected is not None and snapshot != expected:
            self.leases.pop(scope, None)
            return {
                "allowed": False,
                "stale": True,
                "reason": "新消息或生活事实已变化，保留成品并重新判断发送时机",
            }
        try:
            rhythm = await contact_rhythm(self.runtime, scope, life_now(), shared=shared)
        except BaseException:
            if self.leases.get(scope, (None,))[0] == owner:
                self.leases.pop(scope, None)
            raise
        if rhythm.get("ready") is False:
            self.leases.pop(scope, None)
            return {
                "allowed": False,
                "retryable": True,
                "retry_after": rhythm["retry_after"],
                "reason": "结合该对象最近的收发情况，稍后再联系",
            }
        if (
            previous
            and life_now().replace(tzinfo=TIMEZONE).timestamp() - previous < 120
        ):
            self.leases.pop(scope, None)
            return {
                "allowed": False,
                "retryable": True,
                "reason": "该会话刚收到主动消息，稍后再联系",
            }
        return {"allowed": True, "snapshot": snapshot}
