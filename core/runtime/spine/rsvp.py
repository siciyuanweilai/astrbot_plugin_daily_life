from __future__ import annotations

import datetime
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...models import DayRecord, EventRecord
from ..markers import LOG_PREFIX


class SpineInviteMixin:
    async def sync_outfit_after_invite(
        self,
        date_str: str,
        current_time: datetime.datetime | None = None,
    ) -> DayRecord | None:
        try:
            current_time = current_time or life_now()
            current_period = self._get_curr_period(current_time)
            updated = await self.composer.update_outfit(
                date_str,
                current_period,
                current_time=current_time,
            )
            if updated:
                await self.mark_page_status_changed("invite_outfit_update")
            return updated
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 邀约后的穿搭判断失败：{exc}")
            return None

    async def _sync_outfit_after_invite_background(
        self,
        date_str: str,
        current_time: datetime.datetime,
    ) -> None:
        await self.sync_outfit_after_invite(date_str, current_time)

    def schedule_invite_outfit_sync(
        self,
        date_str: str,
        current_time: datetime.datetime | None = None,
    ) -> bool:
        current_time = current_time or life_now()
        return self._schedule_background_task(
            self._sync_outfit_after_invite_background(date_str, current_time),
            label="邀约穿搭判断",
        )

    async def accept_user_invite(self, event: Any, invite_details: str) -> str:
        async with self.generation_lock:
            now = life_now()
            today_str = now.strftime("%Y-%m-%d")
            data = await self.archive.get_day(today_str)
            if not data or not data.timeline:
                return "今天还没有可用于判断冲突的日程记录。请结合人设、当前语境和用户邀约自然决定是否接受、改约或拒绝。"

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
            invite_text = f"用户的邀约意图：{invite_details} (用户刚才的原话：{raw_message})"
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
                data.timeline = new_timeline
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
                return (
                    f"隐藏生活状态：我已把与【{sender_name}】的邀约加入接下来的安排。\n"
                    "请结合先前聊天记录，直接用我平时自然的口吻回复对方。"
                    f"我接受的内心原因/客观理由是：{reason}"
                )

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
            if isinstance(decision, dict) and decision.get("decision") == "propose_alternative":
                alt_time = str(decision.get("alternative_time") or "").strip()
                if alt_time:
                    alternative = f"\n可改约倾向：{alt_time}"
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
            return (
                "系统：日程安排严重冲突或意愿不够，未能在时间轴上安排此次邀约。\n"
                "请结合你和TA先前的聊天记录，直接用你平时自然的口吻委婉地拒绝TA。"
                f"你拒绝的内心原因/客观理由是：{reason}{alternative}"
            )

    async def add_memo_for_tomorrow(self, event: Any, memo_details: str) -> str:
        now = life_now()
        tomorrow_str = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        sender_name = await self.contact_resolver.resolve_event_sender(event)
        formatted_memo = f"与【{sender_name}】的约定：{memo_details}"
        await self.archive.set_memo(tomorrow_str, formatted_memo)
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
        logger.info(f"[大语言模型工具] 已将明天邀约写入备忘录：{formatted_memo}")
        return (
            f"系统：已成功将【{memo_details}】加入明天的强制备忘录！"
            "明天早晨生成新日程时会自动为您安排进去。\n"
            f"请结合你和【{sender_name}】先前的聊天记录，用你平时自然的口吻回复【{sender_name}】，"
            "告诉TA你已经把明天的计划安排上了/记在小本本上了，并表达你对明天活动的期待。"
        )
