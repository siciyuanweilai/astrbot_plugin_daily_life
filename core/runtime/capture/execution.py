"""以对应会话中已发送的消息为依据，确认聊天中虚拟动作的完成情况。"""

from __future__ import annotations

import datetime
import json
from typing import Any

from ...life.tools import timeline_item_datetime
from ...models import INTERNAL_SIMULATED_ACTION_TYPES, LifeActionIntent
from ..locks import operation_lock


class ChatExecutionMixin:
    async def _chat_execution_context(self, batch: dict[str, Any]) -> dict[str, Any]:
        scope = str(batch.get("session_id") or "")
        rows = batch.get("messages") or []
        date = str(rows[-1].get("occurred_at") or "")[:10] if rows else ""
        getter = getattr(self.archive, "get_open_commitments_for_scope", None)
        commitments = (
            await getter(scope, date) if callable(getter) and scope and date else []
        )
        days = {}
        for row in rows:
            row_date = str(row.get("occurred_at") or "")[:10]
            if row_date and row_date not in days:
                day = await self.archive.get_day(row_date)
                if day is not None:
                    days[row_date] = day
        actions = []
        for day in days.values():
            try:
                raw = json.loads(day.meta.get("planned_life_actions") or "[]")
            except (TypeError, ValueError):
                continue
            for value in raw if isinstance(raw, list) else []:
                action = LifeActionIntent.from_value(value)
                if action.action_type not in INTERNAL_SIMULATED_ACTION_TYPES:
                    continue
                index = action.timeline_index
                if index is None or not 0 <= index < len(day.timeline):
                    continue
                item = day.timeline[index]
                if item.execution_state in {"cancelled", "skipped"}:
                    continue
                actions.append(
                    {
                        **action.as_dict(),
                        "date": day.date,
                        "activity": item.activity,
                        "time": item.time,
                        "execution_state": item.execution_state,
                    }
                )
        return {
            "open_commitments": [item.as_dict() for item in commitments],
            "execution_candidates": actions,
        }

    async def _save_batch_execution_updates(
        self, payload: dict[str, Any], batch: dict[str, Any]
    ) -> int:
        updates = payload.get("execution_updates")
        if not isinstance(updates, list):
            return 0
        scope = str(batch.get("session_id") or "")
        if not scope:
            return 0
        # 仅允许写入本批次模型调用中已提供的 ID。
        commitments = {
            str(item["id"]): item
            for item in batch.get("open_commitments", [])
            if isinstance(item, dict) and item.get("id")
        }
        actions = {
            str(item["action_id"]): item
            for item in batch.get("execution_candidates", [])
            if isinstance(item, dict) and item.get("action_id")
        }
        rows = {}
        for row in batch.get("messages", []):
            if isinstance(row, dict):
                for key in ("id", "message_id"):
                    if row.get(key):
                        rows[str(row[key])] = row
        saved = 0
        for update in updates[:30]:
            if not isinstance(update, dict) or update.get("completed") is not True:
                continue
            row = rows.get(str(update.get("source_message_id") or ""))
            if not row or row.get("role") != "assistant" or row.get("is_quoted"):
                continue
            evidence = str(update.get("evidence") or "").strip()
            message = str(row.get("message_text") or "")
            if len(evidence) < 4 or evidence != message.strip():
                continue
            try:
                occurred = datetime.datetime.fromisoformat(
                    str(row.get("occurred_at") or "")
                )
            except ValueError:
                continue
            action_id = str(update.get("action_id") or "").strip()
            if action_id:
                candidate = actions.get(action_id)
                recorder = getattr(
                    getattr(self, "composer", None), "record_life_action_receipt", None
                )
                if not candidate or not callable(recorder):
                    continue
                async with operation_lock(self, f"action:{candidate['date']}"):
                    day = await self.archive.get_day(candidate["date"])
                    index = candidate.get("timeline_index")
                    if (
                        day is None
                        or not isinstance(index, int)
                        or not 0 <= index < len(day.timeline)
                    ):
                        continue
                    start = timeline_item_datetime(day.timeline[index], day.date)
                    if (
                        start is None
                        or start > occurred.replace(tzinfo=None)
                        or day.timeline[index].execution_state
                        in {"cancelled", "skipped"}
                    ):
                        continue
                    outcome = await recorder(
                        day,
                        action_id,
                        {
                            "status": "simulated",
                            "source": "chat_completion",
                            "source_id": str(update["source_message_id"]),
                            "evidence": [evidence],
                            "occurred_at": occurred.isoformat(sep=" "),
                        },
                        now=occurred,
                    )
                if outcome is None or outcome.status != "committed":
                    continue
            ids = update.get("commitment_ids")
            complete = getattr(self.archive, "complete_simulated_commitment", None)
            for item_id in ids if isinstance(ids, list) else []:
                item = commitments.get(str(item_id))
                if (
                    not item
                    or not callable(complete)
                    or item.get("source_session") != scope
                ):
                    continue
                saved += int(
                    await complete(
                        int(item["id"]),
                        scope=scope,
                        when=occurred.isoformat(sep=" "),
                        evidence=evidence,
                        source_id=str(update["source_message_id"]),
                    )
                )
        return saved
