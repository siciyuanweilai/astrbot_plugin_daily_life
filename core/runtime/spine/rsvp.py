from __future__ import annotations

import datetime
import json
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...models import CommitmentRecord, DayRecord, EventRecord, PlaceRecord
from ..locks import operation_lock
from ..markers import LOG_PREFIX


class SpineInviteMixin:
    async def sync_outfit_after_invite(
        self,
        date_str: str,
        current_time: datetime.datetime | None = None,
        instruction: str = "",
    ) -> DayRecord | None:
        try:
            current_time = current_time or life_now()
            current_period = self._get_curr_period(current_time)
            kwargs: dict[str, Any] = {"current_time": current_time}
            if instruction:
                kwargs["instruction"] = instruction
            updated = await self.composer.update_outfit(
                date_str, current_period, **kwargs
            )
            if updated:
                if instruction:
                    updated.meta.pop("pending_commitment_outfit", None)
                    await self.archive.save_day(updated)
                await self.mark_page_status_changed("invite_outfit_update")
            return updated
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 邀约后的穿搭判断失败：{exc}")
            return None

    async def _sync_outfit_after_invite_background(
        self,
        date_str: str,
        current_time: datetime.datetime,
        instruction: str = "",
    ) -> None:
        await self.sync_outfit_after_invite(date_str, current_time, instruction)

    def schedule_invite_outfit_sync(
        self,
        date_str: str,
        current_time: datetime.datetime | None = None,
        instruction: str = "",
    ) -> bool:
        current_time = current_time or life_now()
        return self._schedule_background_task(
            self._sync_outfit_after_invite_background(
                date_str, current_time, instruction
            ),
            label="邀约穿搭判断",
        )

    @staticmethod
    def _commitment_reconcile_markers(data: DayRecord) -> dict[str, str]:
        raw = str((data.meta or {}).get("commitment_reconcile_markers") or "")
        try:
            value = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return (
            {
                str(key): str(item)
                for key, item in value.items()
                if str(key).strip() and str(item).strip()
            }
            if isinstance(value, dict)
            else {}
        )

    @staticmethod
    def _commitment_reconcile_signature(
        data: DayRecord,
        commitment: CommitmentRecord,
    ) -> str:
        return "|".join(
            (
                commitment.content,
                commitment.trigger_date,
                commitment.trigger_time,
                commitment.time_window,
                "\n".join(
                    f"{timeline_item.time}:{timeline_item.activity}"
                    for timeline_item in data.timeline
                ),
            )
        )

    @staticmethod
    def _commitment_reconcile_marker_value(outcome: str, signature: str) -> str:
        """生成带处理结果的承诺协调标记。"""

        return f"v2:{outcome}:{signature}"

    @classmethod
    def _store_commitment_reconcile_marker(
        cls,
        data: DayRecord,
        markers: dict[str, str],
        marker_key: str,
        signature: str,
        outcome: str,
    ) -> None:
        markers[marker_key] = cls._commitment_reconcile_marker_value(
            outcome, signature
        )
        while len(markers) > 48:
            markers.pop(next(iter(markers)))
        data.meta["commitment_reconcile_markers"] = json.dumps(
            markers, ensure_ascii=False, separators=(",", ":")
        )

    @staticmethod
    def _outfit_instruction_is_due(
        date_str: str,
        effective_time: str,
        now: datetime.datetime,
    ) -> bool:
        if not effective_time:
            return True
        try:
            effective_at = datetime.datetime.strptime(
                f"{date_str} {effective_time}", "%Y-%m-%d %H:%M"
            )
        except (TypeError, ValueError):
            return True
        return effective_at <= now + datetime.timedelta(minutes=90)

    async def apply_commitment_to_current_day(
        self,
        commitment: CommitmentRecord,
        *,
        now: datetime.datetime | None = None,
        owner_hint: str = "",
    ) -> bool:
        """把已确认且属于当前角色的当天承诺合并进现有日程。

        Args:
            commitment: 需要判断和执行的结构化承诺。
            now: 当前时间，留空时使用插件时钟。
            owner_hint: 承诺提取阶段得到的人物归属提示。

        Returns:
            承诺是否实际修改并写入了当天日程。
        """

        now = now or life_now()
        today_str = now.strftime("%Y-%m-%d")
        item = CommitmentRecord.from_value(commitment)
        if (
            item is None
            or item.status != "active"
            or (item.trigger_date and item.trigger_date != today_str)
            or item.time_window in {"next_chat", "next_time"}
        ):
            return False
        async with operation_lock(self, f"commitment_reconcile:{today_str}:{item.id}"):
            data = await self.archive.get_day(today_str)
            if not data or not data.timeline:
                return False
            signature = self._commitment_reconcile_signature(data, item)
            markers = self._commitment_reconcile_markers(data)
            marker_key = str(item.id or f"content:{item.content[:80]}")
            handled_markers = {
                self._commitment_reconcile_marker_value("applied", signature),
                self._commitment_reconcile_marker_value("skipped", signature),
            }
            if markers.get(marker_key) in handled_markers:
                return False
            (
                new_timeline,
                decision,
            ) = await self.composer.reconcile_commitment_with_timeline(
                today_str,
                data.timeline,
                item,
                now,
                owner_hint=owner_hint,
                current_state=data.state,
                current_places=data.places,
            )
            if not isinstance(decision, dict) or not decision:
                return False
            if not new_timeline:
                if decision.get("_retryable") is True:
                    return False
                self._store_commitment_reconcile_marker(
                    data, markers, marker_key, signature, "skipped"
                )
                await self.archive.save_day(data)
                return False

            data.timeline = new_timeline
            applied_signature = self._commitment_reconcile_signature(data, item)
            self._store_commitment_reconcile_marker(
                data, markers, marker_key, applied_signature, "applied"
            )
            audited_places = decision.get("_audited_places")
            if isinstance(audited_places, list):
                data.places = [
                    place
                    for place in (
                        PlaceRecord.from_value(value) for value in audited_places
                    )
                    if place is not None
                ]
                if data.places:
                    await self.archive.touch_places(
                        today_str, data.places, source="commitment"
                    )
            location_audit = decision.get("_location_audit")
            if isinstance(location_audit, dict):
                data.meta["location_audit_provider"] = str(
                    location_audit.get("map_provider") or ""
                )
                data.meta["location_audit_city"] = str(
                    location_audit.get("home_city") or ""
                )
            outfit_instruction = str(decision.get("outfit_instruction") or "").strip()
            outfit_effective_time = str(
                decision.get("outfit_effective_time") or ""
            ).strip()
            if outfit_instruction:
                data.meta["pending_commitment_outfit"] = json.dumps(
                    {
                        "commitment_id": item.id,
                        "instruction": outfit_instruction,
                        "effective_time": outfit_effective_time,
                        "date": today_str,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            await self.archive.save_day(data)
            if item.id:
                await self.archive.link_commitments_to_day(today_str, [item.id])
            await self.archive.add_events(
                today_str,
                [
                    EventRecord(
                        date=today_str,
                        summary=f"已将确认的约定合并到今日日程：{item.content}",
                        people=list(item.people),
                        importance="high",
                        source="commitment",
                    )
                ],
            )
            await self.mark_page_status_changed("commitment_schedule_update")
            if not outfit_instruction:
                self.schedule_invite_outfit_sync(today_str, now)
            elif self._outfit_instruction_is_due(today_str, outfit_effective_time, now):
                self.schedule_invite_outfit_sync(today_str, now, outfit_instruction)
            return True

    async def reconcile_due_commitments_for_day(
        self,
        date_str: str,
        now: datetime.datetime,
    ) -> bool:
        """补偿重启或后台保存后尚未合并的当天承诺。

        Args:
            date_str: 需要检查的生活日日期。
            now: 当前巡检时间。

        Returns:
            是否有承诺实际修改了当天日程。
        """

        changed = False
        for commitment in (await self.archive.get_due_commitments(date_str))[:8]:
            changed = (
                await self.apply_commitment_to_current_day(commitment, now=now)
                or changed
            )
        return changed

    async def _accept_invite_timeline_update(
        self,
        *,
        data: DayRecord,
        new_timeline: list[Any],
        today_str: str,
        now: datetime.datetime,
        sender_name: str,
        invite_details: str,
        reason: str,
        decision: dict[str, Any],
        context_meta: dict[str, str],
        raw_message: str,
    ) -> str:
        data.meta.pop("pending_invite_alternative", None)
        data.timeline = new_timeline
        audited_places = decision.get("_audited_places")
        if isinstance(audited_places, list):
            data.places = [
                place
                for place in (PlaceRecord.from_value(item) for item in audited_places)
                if place is not None
            ]
            if data.places:
                await self.archive.touch_places(today_str, data.places, source="invite")
        location_audit = decision.get("_location_audit")
        if isinstance(location_audit, dict):
            data.meta["location_audit_provider"] = str(
                location_audit.get("map_provider") or ""
            )
            data.meta["location_audit_city"] = str(
                location_audit.get("home_city") or ""
            )
        if self.config.state.enabled:
            data = await self.refresh_state_for_day(
                today_str,
                data,
                now,
                source="invite",
                detail=f"已接受【{sender_name}】的邀约：{invite_details}",
                force=True,
            )
        await self.archive.save_day(data)
        await self.archive.add_events(
            today_str,
            [
                EventRecord(
                    date=today_str,
                    summary=f"接受了与【{sender_name}】的邀约：{invite_details}",
                    people=[sender_name],
                    importance="high",
                    source="invite",
                )
            ],
        )
        self.schedule_invite_outfit_sync(today_str, now)
        self.schedule_memos_selected_items(
            context_meta,
            [
                f"邀约结果：接受了与【{sender_name}】的邀约：{invite_details}",
                f"接受原因：{reason}",
            ],
            reason="同步已处理的邀约结果，避免后续忘记接受过的安排。",
            user_message=raw_message or invite_details,
            marker=f"invite:accepted:{today_str}:{sender_name}:{invite_details}",
        )
        return json.dumps(
            {
                "decision": "accept",
                "person": sender_name,
                "activity": invite_details,
                "reason": reason,
                "response_stance": str(decision.get("response_stance") or "自然答应"),
                "response_tone": str(
                    decision.get("response_tone") or "符合双方关系和当前语境"
                ),
            },
            ensure_ascii=False,
        )

    async def _decline_invite_timeline_update(
        self,
        *,
        data: DayRecord,
        today_str: str,
        now: datetime.datetime,
        sender_name: str,
        invite_details: str,
        reason: str,
        decision: dict[str, Any],
        context_meta: dict[str, str],
        raw_message: str,
    ) -> str:
        await self.archive.add_events(
            today_str,
            [
                EventRecord(
                    date=today_str,
                    summary=f"因日程冲突暂未接受【{sender_name}】的邀约：{invite_details}",
                    people=[sender_name],
                    importance="normal",
                    source="invite",
                )
            ],
        )
        if self.config.state.enabled:
            await self.refresh_state_for_day(
                today_str,
                data,
                now,
                source="invite",
                detail=f"暂未接受【{sender_name}】的邀约：{invite_details}",
                force=True,
            )
        alternative = ""
        if (
            isinstance(decision, dict)
            and decision.get("decision") == "propose_alternative"
        ):
            alt_time = str(decision.get("alternative_time") or "").strip()
            if alt_time:
                alternative = f"\n可改约倾向：{alt_time}"
                data.meta["pending_invite_alternative"] = json.dumps(
                    {
                        "date": today_str,
                        "person": sender_name,
                        "activity": invite_details,
                        "alternative_time": alt_time,
                        "reason": reason,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        if not alternative:
            data.meta.pop("pending_invite_alternative", None)
        await self.archive.save_day(data)
        memos_invite_items = [
            f"邀约结果：暂未接受【{sender_name}】的邀约：{invite_details}",
            f"原因：{reason}",
        ]
        if alternative:
            memos_invite_items.append(alternative.strip())
        self.schedule_memos_selected_items(
            context_meta,
            memos_invite_items,
            reason="同步已处理的邀约结果，避免后续重复邀请或忘记改约倾向。",
            user_message=raw_message or invite_details,
            marker=f"invite:declined:{today_str}:{sender_name}:{invite_details}",
        )
        return json.dumps(
            {
                "decision": str(decision.get("decision") or "reject"),
                "person": sender_name,
                "activity": invite_details,
                "reason": reason,
                "alternative_time": str(decision.get("alternative_time") or ""),
                "response_stance": str(
                    decision.get("response_stance") or "自然拒绝或改约"
                ),
                "response_tone": str(
                    decision.get("response_tone") or "符合双方关系和当前语境"
                ),
            },
            ensure_ascii=False,
        )

    async def accept_user_invite(self, event: Any, invite_details: str) -> str:
        now = life_now()
        today_str = now.strftime("%Y-%m-%d")
        async with operation_lock(self, f"invite:{today_str}"):
            data = await self.archive.get_day(today_str)
            if not data or not data.timeline:
                return json.dumps(
                    {
                        "decision": "undetermined",
                        "reason": "当前没有足够的已确认安排用于判断冲突",
                        "response_stance": "结合当前语境和真实意愿自然回应，不声称已经记录安排",
                    },
                    ensure_ascii=False,
                )

            sender_name = await self.contact_resolver.resolve_event_sender(event)
            context_meta = await self._event_context_meta(event, sender_name, now)
            await self.remember_interaction(
                event,
                sender_name,
                f"提出邀约：{invite_details}",
                today_str,
                source="invite",
            )
            raw_message = str(getattr(event, "message_str", "") or "")
            pending_alternative = str(
                (data.meta or {}).get("pending_invite_alternative") or ""
            ).strip()
            invite_text = (
                f"用户的邀约意图：{invite_details} (用户刚才的原话：{raw_message})"
            )
            if pending_alternative:
                invite_text += f"；最近尚待对方确认的改约方案：{pending_alternative}"
            if self.config.state.enabled:
                data = await self.refresh_state_for_day(
                    today_str,
                    data,
                    now,
                    source="invite",
                    detail=f"收到【{sender_name}】的邀约：{invite_details}",
                    force=True,
                )
            reason, new_timeline, decision = await self.composer.handle_invite(
                today_str,
                data.timeline,
                invite_text,
                now,
                sender_name,
                current_state=data.state,
                current_places=data.places,
            )
            await self.composer.learn_preferences_from_payload(
                decision,
                date_str=today_str,
                source="invite_tool",
            )
            await self.composer.persist_life_events_from_payload(
                decision,
                date_str=today_str,
                source="invite_tool",
            )

            if new_timeline:
                return await self._accept_invite_timeline_update(
                    data=data,
                    new_timeline=new_timeline,
                    today_str=today_str,
                    now=now,
                    sender_name=sender_name,
                    invite_details=invite_details,
                    reason=reason,
                    decision=decision,
                    context_meta=context_meta,
                    raw_message=raw_message,
                )

            return await self._decline_invite_timeline_update(
                data=data,
                today_str=today_str,
                now=now,
                sender_name=sender_name,
                invite_details=invite_details,
                reason=reason,
                decision=decision,
                context_meta=context_meta,
                raw_message=raw_message,
            )

    async def add_memo_for_tomorrow(self, event: Any, memo_details: str) -> str:
        now = life_now()
        tomorrow_str = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        sender_name = await self.contact_resolver.resolve_event_sender(event)
        formatted_memo = f"与【{sender_name}】的约定：{memo_details}"
        await self.archive.set_memo(tomorrow_str, formatted_memo)
        await self.mark_page_status_changed("memo")
        await self.remember_interaction(
            event,
            sender_name,
            f"约定明天：{memo_details}",
            tomorrow_str,
            source="memo",
        )
        await self.archive.add_events(
            tomorrow_str,
            [
                EventRecord(
                    date=tomorrow_str,
                    summary=f"与【{sender_name}】约定：{memo_details}",
                    people=[sender_name],
                    importance="high",
                    source="memo",
                )
            ],
        )
        context_meta = await self._event_context_meta(event, sender_name, now)
        self.schedule_memos_memo(context_meta, formatted_memo)
        logger.info("[大语言模型工具] 已将明天邀约写入备忘录")
        return json.dumps(
            {
                "recorded": True,
                "person": sender_name,
                "memo": memo_details,
                "response_stance": "自然确认已经记住，并按双方关系表达对明天活动的态度",
            },
            ensure_ascii=False,
        )
