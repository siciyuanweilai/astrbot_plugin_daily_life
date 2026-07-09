import datetime
from typing import Any, AsyncIterator

from ..labels import commitment_kind_label, time_window_label
from ..models import CommitmentRecord, EventRecord
from .request import CommandRequest


class SocialCommandMixin:
    async def _memo(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        if not req.param_full:
            yield event.plain_result("请告诉我要记住的明日安排，例如：明天去超市买牛奶。")
            return
        tomorrow = (req.now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        await self.runtime.archive.set_memo(tomorrow, req.param_full)
        await self.runtime.mark_page_status_changed("memo")
        sender_name = await self.runtime.contact_resolver.resolve_event_sender(event)
        await self.runtime.remember_interaction(
            event,
            sender_name,
            f"约定明天：{req.param_full}",
            tomorrow,
            source="memo",
        )
        await self.runtime.archive.add_events(
            tomorrow,
            [
                EventRecord(
                    date=tomorrow,
                    summary=f"与【{sender_name}】约定：{req.param_full}",
                    people=[sender_name],
                    importance="high",
                    source="memo",
                )
            ],
        )
        yield event.plain_result(f"已记录！明天生成日程时会强制包含：{req.param_full}")

    async def _commitments(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        action = req.param1
        if not action or action == "列表":
            items = await self.runtime.archive.get_commitments(status="active", limit=20)
            if not items:
                yield event.plain_result("当前没有未完成承诺。")
                return
            lines = ["🧷 未完成承诺/约定"]
            for item in items:
                when = item.trigger_date or time_window_label(item.time_window) or "待触发"
                people = f"｜{'、'.join(item.people)}" if item.people else ""
                lines.append(f"#{item.id} [{commitment_kind_label(item.kind)}] {when}{people}\n- {item.content}")
            lines.append("\n支持完成、取消或延期未完成承诺。")
            yield event.plain_result("\n".join(lines))
            return

        if action in {"添加", "新增"}:
            content = " ".join(req.parts[3:]).strip()
            if not content:
                yield event.plain_result("请写明承诺内容，例如：周末一起看电影。")
                return
            sender_name = await self.runtime.contact_resolver.resolve_event_sender(event)
            commitment = await self.runtime.archive.save_commitment(
                CommitmentRecord(
                    content=content,
                    kind="plan",
                    trigger_date=self._infer_manual_commitment_date(content, req.now),
                    time_window="weekend" if "周末" in content else "",
                    people=[sender_name] if sender_name else [],
                    source="manual",
                    source_session=str(getattr(event, "unified_msg_origin", "") or ""),
                    source_message=str(getattr(event, "message_str", "") or ""),
                    confidence=1.0,
                )
            )
            yield event.plain_result(f"已记录承诺 #{commitment.id}：{commitment.content}")
            return

        if action in {"完成", "取消"}:
            commitment_id = self._parse_commitment_id(req.param2)
            if not commitment_id:
                yield event.plain_result("请指定承诺标识，例如：完成 3。")
                return
            status = "done" if action == "完成" else "cancelled"
            ok = await self.runtime.archive.set_commitment_status(
                commitment_id,
                status,
                req.now.strftime("%Y-%m-%d %H:%M:%S"),
            )
            yield event.plain_result("已更新承诺状态。" if ok else f"未找到承诺：{commitment_id}")
            return

        if action in {"延期", "推迟"}:
            commitment_id = self._parse_commitment_id(req.param2)
            target = " ".join(req.parts[4:]).strip()
            if not commitment_id or not target:
                yield event.plain_result("请说明要延期的承诺和新时间，例如：把 3 延期到周末。")
                return
            date_str = self._infer_manual_commitment_date(target, req.now)
            if not date_str:
                yield event.plain_result("无法识别日期，请使用 明天/周末/YYYY-MM-DD")
                return
            ok = await self.runtime.archive.reschedule_commitment(commitment_id, date_str, "weekend" if "周末" in target else "")
            yield event.plain_result("已延期承诺。" if ok else f"未找到承诺：{commitment_id}")
            return

        yield event.plain_result("未知承诺指令，支持：列表/添加/完成/取消/延期")

    @staticmethod
    def _parse_commitment_id(value: str) -> int:
        try:
            return int(str(value or "").lstrip("#"))
        except ValueError:
            return 0

    @staticmethod
    def _infer_manual_commitment_date(text: str, now: datetime.datetime) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        if text == "明天" or "明天" in text:
            return (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if "周末" in text:
            days_until_saturday = (5 - now.weekday()) % 7
            if days_until_saturday == 0:
                days_until_saturday = 7
            return (now + datetime.timedelta(days=days_until_saturday)).strftime("%Y-%m-%d")
        try:
            return datetime.datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return ""

    async def _invite(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        if not req.param_full:
            yield event.plain_result("邀约做什么呢？例如：下午一起看电影。")
            return

        data = await self.runtime.archive.get_day(req.target_date_str)
        if not data or not data.timeline:
            yield event.plain_result("今天还没有计划哦。")
            return

        sender_name = await self.runtime.contact_resolver.resolve_event_sender(event)
        await self.runtime.remember_interaction(
            event,
            sender_name,
            f"提出邀约：{req.param_full}",
            req.target_date_str,
            source="invite",
        )
        yield event.plain_result("稍等，我看下日程安排...")
        if self.runtime.config.state.enabled:
            data = await self.runtime.refresh_state_for_day(
                req.target_date_str,
                data,
                req.now,
                source="invite",
                detail=f"收到【{sender_name}】的邀约：{req.param_full}",
                force=True,
            )
        reply, new_timeline, decision = await self.runtime.composer.handle_invite(
            req.target_date_str,
            data.timeline,
            req.param_full,
            req.now,
            sender_name,
            current_state=data.state,
        )
        await self.runtime.composer.learn_preferences_from_payload(
            decision,
            date_str=req.target_date_str,
            source="invite",
        )
        await self.runtime.composer.persist_life_events_from_payload(
            decision,
            date_str=req.target_date_str,
            source="invite",
        )
        alt_time = str(decision.get("alternative_time") or "").strip() if isinstance(decision, dict) else ""
        if new_timeline:
            data.timeline = new_timeline
            if self.runtime.config.state.enabled:
                data = await self.runtime.refresh_state_for_day(
                    req.target_date_str,
                    data,
                    req.now,
                    source="invite",
                    detail=f"已接受【{sender_name}】的邀约：{req.param_full}",
                    force=True,
                )
            await self.runtime.archive.save_day(data)
            await self.runtime.archive.add_events(
                req.target_date_str,
                [
                    EventRecord(
                        date=req.target_date_str,
                        summary=f"接受了与【{sender_name}】的邀约：{req.param_full}",
                        people=[sender_name],
                        importance="high",
                        source="invite",
                    )
                ],
            )
            self.runtime.schedule_invite_outfit_sync(req.target_date_str, req.now)
        else:
            await self.runtime.archive.add_events(
                req.target_date_str,
                [
                    EventRecord(
                        date=req.target_date_str,
                        summary=f"暂未接受【{sender_name}】的邀约：{req.param_full}",
                        people=[sender_name],
                        importance="normal",
                        source="invite",
                    )
                ],
            )
            if self.runtime.config.state.enabled:
                await self.runtime.refresh_state_for_day(
                    req.target_date_str,
                    data,
                    req.now,
                    source="invite",
                    detail=f"暂未接受【{sender_name}】的邀约：{req.param_full}",
                    force=True,
                )
        suffix = ""
        if isinstance(decision, dict) and decision.get("decision") == "propose_alternative" and alt_time:
            suffix = f"\n可改约倾向：{alt_time}"
        yield event.plain_result(f"{reply}{suffix}")
