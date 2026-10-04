from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from astrbot.api import logger

from ...clock import TIMEZONE
from ...clock import now as life_now
from ...life.tools import timeline_item_datetime
from ...models import CommitmentRecord
from ...prompts import (
    CORE_EMOJI_DELIVERY_RULES,
    CORE_PROACTIVE_CONTINUITY_RULES,
    CORE_PROACTIVE_VOICE_RULES,
    cache_friendly_prompt,
)
from ...sources.dispatch import PermanentScopeDeliveryError
from ..capture.jsonclean import call_pure_json
from ..markers import LOG_PREFIX


class SharedActivityContactMixin:
    """共同活动行前协调：依据最新交流和日程决定联系时间与内容。"""

    async def reconcile_scheduled_invite_contacts(
        self, now: datetime.datetime | None = None
    ) -> int:
        now = now or life_now()
        commitments = await self.archive.get_due_commitments(
            now.strftime("%Y-%m-%d"), include_scheduled=True
        )
        days = {}
        created = 0
        for commitment in commitments:
            current_task = await self.archive.get_durable_task(
                f"invite_contact:{commitment.id}"
            )
            if (
                current_task
                and current_task.status == "pending"
                and current_task.payload.get("shared_activity_contact") is True
            ):
                progress = current_task.result.get("progress", {})
                if isinstance(progress, dict) and progress.get("timeline") is not None:
                    if commitment.trigger_date not in days:
                        days[commitment.trigger_date] = await self.archive.get_day(
                            commitment.trigger_date
                        )
                    day = days[commitment.trigger_date]
                    if (
                        progress.get("commitment") != commitment.as_dict()
                        or day is None
                        or progress.get("timeline")
                        != self._shared_activity_timeline(day)
                        or not any(
                            candidate["index"]
                            == progress.get("activity", {}).get("index")
                            for candidate in self._shared_activity_candidates(day, now)
                        )
                    ):
                        await self.archive.reschedule_durable_task(
                            current_task.task_key,
                            available_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                        )
            if (
                commitment.status != "scheduled"
                or (commitment.source != "invite" and commitment.owner != "共同")
                or current_task is not None
            ):
                continue
            if await self.archive.get_durable_task(
                f"proactive_commitment:{commitment.id}"
            ):
                continue
            if await self.schedule_invite_contact(commitment, observed_at=now):
                created += 1
        return created

    @staticmethod
    def _shared_activity_timeline(day: Any) -> list[dict]:
        fields = (
            "time",
            "activity",
            "place",
            "place_kind",
            "place_scope",
            "travel_mode",
        )
        return [
            {
                **{field: getattr(item, field, "") for field in fields},
                "starts_at": str(
                    timeline_item_datetime(item, day.date, meta=day.meta) or ""
                ),
            }
            for item in day.timeline
        ]

    async def schedule_invite_contact(
        self,
        commitment: CommitmentRecord,
        *,
        timeline_edits: Any = None,
        observed_at: datetime.datetime,
    ) -> bool:
        """确认入口只登记后台判断，不等待模型，也不猜首个编辑节点。"""
        if (
            not commitment.id
            or not commitment.source_session
            or ":GroupMessage:" in commitment.source_session
            or commitment.media_kind != "none"
            or commitment.status not in {"active", "scheduled"}
        ):
            return False
        try:
            date = datetime.date.fromisoformat(
                commitment.trigger_date or observed_at.strftime("%Y-%m-%d")
            )
        except ValueError:
            return False
        if date < observed_at.date():
            return False
        await self.archive.enqueue_durable_task(
            f"invite_contact:{commitment.id}",
            "proactive_commitment",
            {
                "scope": commitment.source_session,
                "commitment_id": commitment.id,
                "action": "contact_person",
                "shared_activity_contact": True,
                "settle_commitment": False,
                "source_message_id": commitment.source_message_id,
                "observed_at": observed_at.isoformat(timespec="seconds"),
                "activity_date": date.isoformat(),
                "timeline_edits": timeline_edits
                if isinstance(timeline_edits, list)
                else [],
            },
            priority=90,
            available_at=observed_at.strftime("%Y-%m-%d %H:%M:%S"),
            max_attempts=4,
        )
        return True

    @staticmethod
    def _shared_activity_candidates(day: Any, now: datetime.datetime) -> list[dict]:
        result = []
        for index, item in enumerate(day.timeline):
            point = timeline_item_datetime(item, day.date, meta=day.meta)
            if (
                point is None
                or point <= now
                or item.execution_state
                in {"active", "elapsed", "completed", "cancelled", "expired", "skipped"}
            ):
                continue
            result.append(
                {
                    "index": index,
                    "starts_at": point.isoformat(timespec="seconds"),
                    "item": item.as_dict(),
                }
            )
        return result

    async def _evaluate_shared_activity_contact(
        self,
        *,
        commitment: CommitmentRecord,
        task: Any,
        day: Any,
        candidates: list[dict],
        recent: list[dict],
        relationship: Any,
        interaction: Any,
        now: datetime.datetime,
    ) -> dict:
        scope = commitment.source_session
        provider = await self._get_proactive_provider()
        if not provider:
            raise RuntimeError("没有可用的共同活动联系裁定模型")
        persona = await self._current_proactive_persona(scope)
        fixed = f"""你正在过自己的生活，需要判断已确认的共同活动开始前，是否自然招呼对方一起准备。
这不是定时提醒模板，也不是对每个日程都发送通知。先判断是否真的有双方已确认的共同安排，再决定联系时机。

裁定要求：
1. 约定原文、最近真实交流是共同参与和确认程度的依据。单方计划、随口提议、媒体交付不得变成共同活动；日程文案不能证明对方同意。
2. 从候选中选择与约定对应的真正活动，activity_index 必须使用候选 index；准备、换装、交通、返回不能误当作共同活动的开始。已取消、改期到无对应节点、已经开始或准备联系已失去意义时 skip。
3. 结合活动所需准备、已确认路程、双方状态、互动方式和最近聊天，决定 contact_at。看电影前可以商量吃喝或准备播放；出门前可以确认会合、带什么或是否准备好。这些只是语义示例，不是固定活动清单或必须提及的事项。不要统一提前固定分钟。
4. contact_at 必须是当前时间至选定活动开始前的完整日期时间。需要稍后联系就 wait；现在自然合适则 send，即使刚确认、距离开始很近也可以。上次安排的联系时间已经到达且仍有必要时，优先履行，不要仅因时间过去而反复推迟。
5. 已经商量清楚准备、已经催过、对方明确不需要提醒，或此时正在交流同一准备事项且再单独联系会重复，就 skip。普通聊天或对方刚出现不等于准备事项已完成。改期可重新选择活动和时机。
6. send 只说一条顺着近期对话、人设和关系的简短闲聊，可以半句接话或一个具体问题；不要求凑字数，不复述整段日程，不写客服通知。不要为了显得贴心硬加清单、解释和甜腻称呼。
7. 不补造现有零食、库存、已经准备好、已经出门、到达、对方反应或共同活动已完成；可以提议或询问。只协商准备，不能重新许下未经确认的拍照、视频和其他交付。
8. 若双方同处现场，直接自然说话；不得用“你那边、上线、发消息”等远程措辞。共同观影不等于同处现场，依据互动证据判断。

{CORE_PROACTIVE_CONTINUITY_RULES}
{CORE_PROACTIVE_VOICE_RULES}
{CORE_EMOJI_DELIVERY_RULES}

只返回严格 JSON：
{{"decision":"wait|send|skip","activity_index":0,"contact_at":"YYYY-MM-DD HH:MM:SS","message_goal":"本次要协调的具体事项","reply_text":"仅 send 时填写","reason":"证据和时机依据","expression_intent":{{"channel":"text|voice","confidence":0.0,"emotion":"","emotion_category":"","voice_style":"","emoji_intent":"","action_intent":"","send_emoji":false,"reason":""}}}}
skip 时 activity_index 可以为 null，contact_at 和 reply_text 留空。
"""
        dynamic = f"""当前时间：{now.strftime("%Y-%m-%d %H:%M:%S")}
角色人设：{persona}
联系对象：{getattr(relationship, "name", "") or "对方"}
主动语音能力：{self._proactive_voice_capability(scope)}
已保存约定：{json.dumps(commitment.as_dict(), ensure_ascii=False)}
安排入口资料：{json.dumps(task.payload, ensure_ascii=False)}
上次后台判断：{json.dumps(task.result, ensure_ascii=False)}
互动方式：{getattr(interaction, "mode", "unknown")}；依据：{getattr(interaction, "evidence", "")}
当前生活状态：{json.dumps(day.state.as_dict() if day.state else {}, ensure_ascii=False)}
当天完整时间轴：{json.dumps([item.as_dict() for item in day.timeline], ensure_ascii=False)}
可选的尚未开始节点：{json.dumps(candidates, ensure_ascii=False)}
最近真实交流：
{self._format_recent_context_messages(recent, now=now)}"""
        session_id = f"daily_life_activity_contact_{uuid.uuid4().hex[:8]}"
        try:
            decision = await call_pure_json(
                self,
                provider,
                cache_friendly_prompt(fixed, dynamic, dynamic_title="共同活动行前协调"),
                session_id,
                primary_provider_id=self.config.proactive.provider,
                propagate_non_retryable=True,
                strict=True,
            )
            if not isinstance(decision, dict):
                raise ValueError("共同活动裁定未返回有效 JSON")
            return decision
        finally:
            await self.close_text_session(session_id)

    async def _run_shared_activity_contact(
        self,
        task: Any,
        commitment: CommitmentRecord,
    ) -> dict:
        now = life_now().replace(microsecond=0)
        scope = commitment.source_session
        if scope != task.payload.get("scope") or commitment.media_kind != "none":
            return {"outcome": "invalid", "reason": "约定对象或类型已变化"}
        date_str = commitment.trigger_date or str(
            task.payload.get("activity_date") or ""
        )
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            return {"outcome": "invalid", "reason": "共同安排没有明确日期"}
        if date < now.date():
            return {"outcome": "expired", "reason": "共同安排日期已过"}
        if commitment.status != "scheduled":
            return {
                "retry_at": (now + datetime.timedelta(seconds=30)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "reason": "等待已确认约定落实到日程",
            }
        # A separate explicit contact promise takes precedence over inferred preparation.
        explicit = await self.archive.get_durable_task(
            f"proactive_commitment:{commitment.id}"
        )
        if explicit is not None and explicit.status in {
            "pending",
            "leased",
            "completed",
        }:
            return {"outcome": "already_covered", "reason": "同一约定已有明确联系任务"}
        day = await self.archive.get_day(date_str)
        if day is None:
            retry_at = now + datetime.timedelta(minutes=5 if date == now.date() else 60)
            return {
                "retry_at": retry_at.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": "等待对应生活日生成",
            }
        previous_activity = task.result.get("progress", {}).get("activity", {})
        if isinstance(previous_activity, dict) and previous_activity.get("starts_at"):
            for item in day.timeline:
                point = timeline_item_datetime(item, day.date, meta=day.meta)
                if (
                    point is not None
                    and point <= now
                    and point.isoformat(timespec="seconds")
                    == previous_activity.get("starts_at")
                    and all(
                        getattr(item, field)
                        == previous_activity.get("item", {}).get(field)
                        for field in ("time", "activity", "place", "place_kind")
                    )
                ):
                    return {
                        "outcome": "expired",
                        "reason": "对应共同活动已经开始，不转向无关节点",
                    }
        candidates = self._shared_activity_candidates(day, now)
        if not candidates:
            return {"outcome": "expired", "reason": "已无尚未开始的活动"}
        snapshotter = getattr(self, "_snapshot_proactive_send_event", None)
        source_event = snapshotter(scope) if callable(snapshotter) else None
        recent = await self._read_recent_context_messages(scope, limit=20)
        relationship = await self._proactive_commitment_relationship(scope)
        interaction = await self.resolve_interaction_context(
            target_scope=scope, now=now
        )
        decision = await self._evaluate_shared_activity_contact(
            commitment=commitment,
            task=task,
            day=day,
            candidates=candidates,
            recent=recent,
            relationship=relationship,
            interaction=interaction,
            now=now,
        )
        # Model work is asynchronous: evidence can change while it is in progress.
        latest = await self.archive.get_commitment(commitment.id)
        latest_day = await self.archive.get_day(date_str)
        latest_recent = await self._read_recent_context_messages(scope, limit=20)
        if latest is None or latest.status in {
            "done",
            "cancelled",
            "expired",
            "delivery_failed",
        }:
            return {"outcome": "cancelled", "reason": "判断期间约定已结束或取消"}
        if (
            latest.as_dict() != commitment.as_dict()
            or latest_day is None
            or [item.as_dict() for item in latest_day.timeline]
            != [item.as_dict() for item in day.timeline]
            or latest_recent != recent
        ):
            return {
                "retry_at": (life_now() + datetime.timedelta(seconds=30)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "reason": "交流或日程已变化，重新判断",
                "progress": task.result.get("progress", {}),
            }
        choice = decision.get("decision")
        reason = str(decision.get("reason") or "").strip()
        if choice == "skip":
            return {"outcome": "skipped", "reason": reason}
        if choice not in {"wait", "send"}:
            raise ValueError("共同活动裁定缺少有效动作")
        index = decision.get("activity_index")
        selected = (
            next((item for item in candidates if item["index"] == index), None)
            if type(index) is int
            else None
        )
        if selected is None:
            raise ValueError("共同活动必须绑定真实的尚未开始节点")
        starts_at = datetime.datetime.fromisoformat(selected["starts_at"])
        try:
            contact_at = datetime.datetime.fromisoformat(
                str(decision.get("contact_at") or "")
            )
        except ValueError as exc:
            raise ValueError("共同活动联系时间无效") from exc
        if contact_at.tzinfo is not None:
            contact_at = contact_at.astimezone(TIMEZONE).replace(tzinfo=None)
        if not now <= contact_at < starts_at:
            raise ValueError("联系时间必须在判断时刻至活动开始之前")
        finished_at = life_now()
        if finished_at >= starts_at:
            return {
                "outcome": "expired",
                "reason": "判断期间活动开始，不再补发行前联系",
            }
        progress = {
            "activity": selected,
            "contact_at": contact_at.isoformat(timespec="seconds"),
            "message_goal": str(decision.get("message_goal") or ""),
            "reason": reason,
            "commitment": commitment.as_dict(),
            "timeline": self._shared_activity_timeline(day),
        }
        if choice == "wait":
            if contact_at <= now:
                raise ValueError("等待裁定必须指定尚未到达的联系时间")
            return {
                "retry_at": max(contact_at, finished_at).strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "progress": progress,
            }
        if contact_at > finished_at:
            raise ValueError("现在发送不能指定未来联系时间")
        reply_text = str(decision.get("reply_text") or "").strip()
        if not reply_text:
            raise ValueError("共同活动联系正文为空")
        if source_event is not None:
            source_event._daily_life_proactive_expires_at = starts_at
        try:
            sent = await self._send_proactive_message(
                scope,
                reply_text,
                "共同活动联系发送失败",
                relationship=relationship,
                contact_type="friend" if ":FriendMessage:" in scope else "",
                send_payload={
                    "source": "shared_activity_contact",
                    "expression_intent": decision.get("expression_intent") or {},
                },
                source_event=source_event,
                source_message_id=commitment.source_message_id,
                raise_delivery_errors=True,
            )
        except PermanentScopeDeliveryError as exc:
            return {"outcome": "undeliverable", "reason": str(exc), "code": exc.code}
        if not sent:
            if life_now() >= starts_at:
                return {
                    "outcome": "expired",
                    "reason": "消息生成期间活动开始，停止投递",
                }
            current = getattr(self, "_proactive_send_is_current", None)
            if (
                callable(current)
                and source_event is not None
                and not current(source_event)
            ):
                return {
                    "retry_at": (life_now() + datetime.timedelta(seconds=30)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "reason": "发送期间有新交流，重新判断",
                    "progress": progress,
                }
            raise RuntimeError("共同活动联系未成功投递，将重新判断")
        logger.info(f"{LOG_PREFIX} 已发送共同活动行前联系：约定={commitment.id}")
        return {"outcome": "sent", "reply_text": reply_text, "progress": progress}
