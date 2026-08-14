from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...models import CommitmentRecord
from ..capture.jsonclean import call_pure_json
from ..markers import LOG_PREFIX


class ProactiveFollowupMixin:
    """把当前角色明确许下的未来联系承诺接入持久执行队列。"""

    _FOLLOW_UP_ACTIONS = {"contact_person", "remind_person"}

    async def reconcile_scheduled_invite_contacts(
        self, now: datetime.datetime | None = None
    ) -> int:
        """为升级前已接受但尚未登记行前联系的邀约补建任务。"""

        now = now or life_now()
        getter = getattr(self.archive, "get_commitments", None)
        if not callable(getter):
            return 0
        scheduled = await getter(status="scheduled", limit=30)
        existing_tasks = await self.archive.get_durable_tasks(
            kind="proactive_commitment", limit=200
        )
        existing_keys = {str(item.task_key or "") for item in existing_tasks}
        created = 0
        for commitment in scheduled:
            if str(getattr(commitment, "source", "") or "") != "invite":
                continue
            if f"invite_contact:{commitment.id}" in existing_keys:
                continue
            day = await self.archive.get_day(str(commitment.trigger_date or ""))
            if day is None:
                continue
            future = []
            for item in day.timeline:
                try:
                    point = datetime.datetime.strptime(
                        f"{day.date} {item.time}", "%Y-%m-%d %H:%M"
                    )
                except (TypeError, ValueError):
                    continue
                if point > now:
                    future.append(item)
            if not future:
                continue
            if await self.schedule_invite_contact(
                commitment,
                timeline_edits=[{"item": future[0].as_dict()}],
                observed_at=now,
            ):
                created += 1
                existing_keys.add(f"invite_contact:{commitment.id}")
        return created

    @staticmethod
    def _follow_up_execute_at(value: Any) -> datetime.datetime | None:
        text = str(value or "").strip().replace("T", " ")
        if not text:
            return None
        try:
            point = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
        if point.tzinfo is not None:
            point = point.astimezone().replace(tzinfo=None)
        return point

    async def schedule_proactive_commitment(
        self,
        commitment: CommitmentRecord,
        *,
        owner: str,
        follow_up: dict[str, Any],
        observed_at: datetime.datetime,
    ) -> bool:
        """仅为当前角色明确承担的未来联系动作创建持久任务。"""

        action = str(follow_up.get("action") or "none").strip()
        execute_at = self._follow_up_execute_at(follow_up.get("execute_at"))
        condition = str(follow_up.get("condition") or "").strip()
        scope = str(commitment.source_session or "").strip()
        if (
            owner != "当前角色"
            or action not in self._FOLLOW_UP_ACTIONS
            or ":GroupMessage:" in scope
            or not scope
            or not commitment.id
        ):
            return False
        if execute_at is None:
            if not condition:
                return False
            try:
                delay_minutes = int(follow_up.get("check_after_minutes") or 10)
            except (TypeError, ValueError):
                delay_minutes = 10
            execute_at = observed_at + datetime.timedelta(
                minutes=max(5, min(delay_minutes, 60))
            )
        expires_at = (
            observed_at + datetime.timedelta(hours=24) if condition else None
        )
        if execute_at < observed_at:
            execute_at = observed_at
        await self.archive.enqueue_durable_task(
            f"proactive_commitment:{commitment.id}",
            "proactive_commitment",
            {
                "scope": scope,
                "commitment_id": commitment.id,
                "action": action,
                "message_goal": str(follow_up.get("message_goal") or "").strip(),
                "condition": condition,
                "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S")
                if expires_at
                else "",
                "execute_at": execute_at.strftime("%Y-%m-%d %H:%M:%S"),
                "source_message_id": commitment.source_message_id,
            },
            priority=90,
            available_at=execute_at.strftime("%Y-%m-%d %H:%M:%S"),
            max_attempts=48 if condition else 4,
        )
        return True

    async def schedule_invite_contact(
        self,
        commitment: CommitmentRecord,
        *,
        timeline_edits: Any,
        observed_at: datetime.datetime,
    ) -> bool:
        """为已接受邀约的首个受影响节点登记一次行前联系。"""

        if (
            not commitment.id
            or not commitment.source_session
            or ":GroupMessage:" in commitment.source_session
            or not isinstance(timeline_edits, list)
        ):
            return False
        candidates: list[datetime.datetime] = []
        for edit in timeline_edits:
            if not isinstance(edit, dict):
                continue
            item = edit.get("item")
            if not isinstance(item, dict):
                continue
            time_text = str(item.get("time") or "").strip()
            if not time_text:
                continue
            try:
                point = datetime.datetime.strptime(
                    f"{commitment.trigger_date} {time_text}", "%Y-%m-%d %H:%M"
                )
            except ValueError:
                continue
            if point > observed_at:
                candidates.append(point)
        if not candidates:
            return False
        first_node_at = min(candidates)
        if first_node_at <= observed_at + datetime.timedelta(minutes=10):
            return False
        execute_at = first_node_at - datetime.timedelta(minutes=5)
        await self.archive.enqueue_durable_task(
            f"invite_contact:{commitment.id}",
            "proactive_commitment",
            {
                "scope": commitment.source_session,
                "commitment_id": commitment.id,
                "action": "contact_person",
                "message_goal": "在共同安排开始前，自然联系对方确认准备或会合进度",
                "execute_at": execute_at.strftime("%Y-%m-%d %H:%M:%S"),
                "source_message_id": commitment.source_message_id,
                "settle_commitment": False,
            },
            priority=90,
            available_at=execute_at.strftime("%Y-%m-%d %H:%M:%S"),
            max_attempts=4,
        )
        return True

    async def _proactive_commitment_relationship(self, scope: str) -> Any | None:
        getter = getattr(self.archive, "get_relationships_for_target", None)
        if not callable(getter):
            return None
        relationships = await getter(scope, limit=1)
        return relationships[0] if relationships else None

    async def _proactive_commitment_life_context(
        self, now: datetime.datetime
    ) -> dict[str, Any]:
        date_str, using_extended_night, day = await self._proactive_current_day(now)
        if day is None:
            return {
                "date": date_str,
                "current_activity": "暂无可读取的当前生活记录",
                "timeline": [],
            }
        return {
            "date": date_str,
            "using_extended_night": using_extended_night,
            "current_activity": self.build_hidden_activity_hint(
                day, now, using_extended_night
            )[1],
            "timeline": [item.as_dict() for item in day.timeline],
        }

    async def _evaluate_proactive_commitment(
        self,
        *,
        scope: str,
        commitment: CommitmentRecord,
        task_payload: dict[str, Any],
        relationship: Any | None,
        interaction: Any,
        now: datetime.datetime,
    ) -> dict[str, Any]:
        provider = await self._get_proactive_provider()
        if not provider:
            raise RuntimeError("没有可用的主动承诺裁定模型")
        persona = await self._current_proactive_persona(scope)
        recent_messages = await self._read_recent_context_messages(scope, limit=10)
        recent_context = self._format_recent_context_messages(
            recent_messages, now=now
        )
        life_context = await self._proactive_commitment_life_context(now)
        target_name = str(getattr(relationship, "name", "") or "对方").strip()
        interaction_context = {
            "mode": str(getattr(interaction, "mode", "") or "unknown"),
            "mode_label": str(
                getattr(interaction, "mode_label", "") or "互动方式未知"
            ),
            "evidence": str(getattr(interaction, "evidence", "") or ""),
        }
        prompt = f"""你负责在一个已经到期的主动联系承诺真正发送前做最后语义复核。

当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}
当前角色人设：
{persona or '暂无额外人设。'}

承诺对象：{target_name}
已保存承诺：{json.dumps(commitment.as_dict(), ensure_ascii=False)}
主动动作：{json.dumps(task_payload, ensure_ascii=False)}
当前互动方式：{json.dumps(interaction_context, ensure_ascii=False)}
当前生活依据：{json.dumps(life_context, ensure_ascii=False)}
最近真实交流：
{recent_context}

判断规则：
1. 这是当前角色已经明确许下的未来联系或提醒，不受普通闲时回复的静默门槛和概率限制。
2. 如果近期证据表明事情已完成、已取消、已改期、对方已经主动出现，或现在发送明显失去意义，则不发送，并准确给出 settlement。
3. 如果仍应履行，只生成一条符合当前人设、关系和现场进展的简短自然消息；不要声称尚未发生的动作已经完成。
4. 如果主动动作含 condition 且当前证据不足以确认条件已经成立，返回 should_send=false、settlement=wait，并给出 5 到 60 分钟的 retry_after_minutes；不要提前发送。
5. 传输会话不代表现实分开。若双方正同处现场且承诺仍需履行，应生成一句面对面直接说出的自然招呼或提醒，不能仅因同处现场静默完成；这时不要使用“发消息、你那边、到哪了、上线”等远程措辞。
6. 只依据提供的证据，不补造地点、进度或对方反应。

只返回严格 JSON：
{{"should_send":true,"reply_text":"","reason":"","settlement":"send|wait|already_done|cancelled|superseded|invalid","retry_after_minutes":0,"expression_intent":{{"emotion":"","emotion_category":"","voice_style":"","send_emoji":false}}}}
"""
        session_id = f"daily_life_proactive_commitment_{uuid.uuid4().hex[:8]}"
        provider_id = self.config.proactive.provider
        try:
            payload = await call_pure_json(
                self,
                provider,
                prompt,
                session_id,
                primary_provider_id=provider_id,
                propagate_non_retryable=True,
            )
            if not isinstance(payload, dict):
                raise ValueError("主动承诺裁定未返回有效 JSON")
            return payload
        finally:
            await self.close_text_session(session_id)

    async def run_proactive_commitment_task(self, task: Any) -> dict[str, Any]:
        """复核并履行一条到期的主动联系承诺。"""

        payload = dict(getattr(task, "payload", {}) or {})
        scope = str(payload.get("scope") or "").strip()
        commitment_id = int(payload.get("commitment_id") or 0)
        action = str(payload.get("action") or "").strip()
        if not scope or action not in self._FOLLOW_UP_ACTIONS or not commitment_id:
            return {"outcome": "invalid", "reason": "任务载荷不完整"}
        commitment = await self.archive.get_commitment(commitment_id)
        if commitment is None:
            return {"outcome": "invalid", "reason": "承诺记录不存在"}
        if commitment.status in {"done", "cancelled", "expired"}:
            return {
                "outcome": commitment.status,
                "reason": "承诺已经进入终态",
            }
        now = life_now()
        expires_at = self._follow_up_execute_at(payload.get("expires_at"))
        if expires_at is not None and now > expires_at:
            await self.archive.set_commitment_status(
                commitment.id, "expired", now.isoformat(timespec="seconds")
            )
            return {"outcome": "expired", "reason": "条件承诺已超过有效期"}
        interaction = await self.resolve_interaction_context(
            target_scope=scope, now=now
        )
        settle_commitment = payload.get("settle_commitment") is not False
        relationship = await self._proactive_commitment_relationship(scope)
        decision = await self._evaluate_proactive_commitment(
            scope=scope,
            commitment=commitment,
            task_payload=payload,
            relationship=relationship,
            interaction=interaction,
            now=now,
        )
        should_send = decision.get("should_send") is True
        reply_text = str(decision.get("reply_text") or "").strip()
        settlement = str(decision.get("settlement") or "invalid").strip()
        if not should_send or not reply_text:
            if settlement == "invalid":
                raise RuntimeError("主动承诺复核结果不完整")
            if settlement == "co_present":
                raise RuntimeError("同处现场不能静默完成明确的主动联系承诺")
            if settlement == "wait":
                try:
                    retry_minutes = int(decision.get("retry_after_minutes") or 10)
                except (TypeError, ValueError):
                    retry_minutes = 10
                retry_at = now + datetime.timedelta(
                    minutes=max(5, min(retry_minutes, 60))
                )
                return {
                    "retry_at": retry_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "reason": str(decision.get("reason") or "等待承诺条件成立"),
                }
            status = "cancelled" if settlement in {"cancelled", "superseded"} else "done"
            if settle_commitment:
                await self.archive.set_commitment_status(
                    commitment.id, status, now.isoformat(timespec="seconds")
                )
            return {
                "outcome": settlement,
                "reason": str(decision.get("reason") or "无需发送").strip(),
            }
        send_payload = {
            "source": "proactive_commitment",
            "expression_intent": decision.get("expression_intent") or {},
        }
        sent = await self._send_proactive_message(
            scope,
            reply_text,
            "主动承诺消息发送失败",
            relationship=relationship,
            contact_type="friend" if ":FriendMessage:" in scope else "",
            send_payload=send_payload,
            source_message_id=str(payload.get("source_message_id") or ""),
        )
        if not sent:
            raise RuntimeError("主动承诺消息未成功投递")
        if settle_commitment:
            await self.archive.set_commitment_status(
                commitment.id, "done", now.isoformat(timespec="seconds")
            )
        logger.info(f"{LOG_PREFIX} 已履行主动联系承诺：编号={commitment.id}")
        return {"outcome": "sent", "reply_text": reply_text}
