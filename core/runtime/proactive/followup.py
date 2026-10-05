from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from types import SimpleNamespace
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...models import CommitmentRecord
from ...prompts import (
    CORE_EMOJI_DELIVERY_RULES,
    CORE_PROACTIVE_CONTINUITY_RULES,
    CORE_PROACTIVE_VOICE_RULES,
    cache_friendly_prompt,
)
from ...sources.dispatch import PermanentScopeDeliveryError, ScopeDeliveryError
from ..capture.jsonclean import call_pure_json
from ..markers import LOG_PREFIX
from .rendezvous import SharedActivityContactMixin


class ProactiveFollowupMixin(SharedActivityContactMixin):
    """把当前角色明确许下的未来联系承诺接入持久执行队列。"""

    _FOLLOW_UP_ACTIONS = {"contact_person", "remind_person"}

    @staticmethod
    def _validate_proactive_commitment_decision(
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """把模型裁定收敛到固定字段，拒绝 should_send 与正文互相矛盾。"""

        result = dict(value)
        should_send = result.get("should_send") is True
        settlement = str(result.get("settlement") or "").strip()
        allowed = {"send", "wait", "already_done", "cancelled", "superseded", "invalid"}
        if settlement not in allowed:
            settlement = "invalid"
        reply_text = str(result.get("reply_text") or "").strip()
        if should_send and not reply_text:
            should_send = False
            settlement = "invalid"
        try:
            retry_after = int(result.get("retry_after_minutes") or 0)
        except (TypeError, ValueError):
            retry_after = 0
        intent = result.get("expression_intent")
        if not isinstance(intent, dict):
            intent = {}
        return {
            **result,
            "should_send": should_send,
            "reply_text": reply_text,
            "reason": str(result.get("reason") or "").strip(),
            "settlement": settlement,
            "retry_after_minutes": max(0, retry_after),
            "expression_intent": intent,
        }

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

    @classmethod
    def _commitment_requests_photo(cls, commitment: CommitmentRecord) -> bool:
        """媒体执行只信任提取阶段持久化的结构化类型。"""

        return (
            str(getattr(commitment, "media_kind", "") or "").strip().lower() == "photo"
        )

    @classmethod
    def _commitment_requests_video(cls, commitment: CommitmentRecord) -> bool:
        """视频任务与图片任务一样，只接受明确的结构化媒体类型。"""

        return (
            str(getattr(commitment, "media_kind", "") or "").strip().lower() == "video"
        )

    @classmethod
    def _photo_commitment_owner_allowed(
        cls, commitment: CommitmentRecord, owner: str = ""
    ) -> bool:
        """只由存档承担人决定是否是当前角色的执行义务。"""

        resolved_owner = str(getattr(commitment, "owner", "") or owner or "").strip()
        return resolved_owner in {"当前角色", "共同"}

    @staticmethod
    def _photo_commitment_execute_at(
        commitment: CommitmentRecord, observed_at: datetime.datetime
    ) -> datetime.datetime | None:
        """只接受承诺中明确给出的精确执行时间。"""

        trigger_date = str(getattr(commitment, "trigger_date", "") or "").strip()
        trigger_time = str(getattr(commitment, "trigger_time", "") or "").strip()
        del observed_at
        if trigger_date and trigger_time:
            try:
                return datetime.datetime.strptime(
                    f"{trigger_date} {trigger_time}", "%Y-%m-%d %H:%M"
                )
            except ValueError:
                try:
                    return datetime.datetime.fromisoformat(
                        f"{trigger_date} {trigger_time}"
                    )
                except ValueError:
                    return None
        return None

    async def schedule_commitment_photo(
        self,
        commitment: CommitmentRecord,
        *,
        owner: str = "",
        observed_at: datetime.datetime | None = None,
    ) -> bool:
        """为当前角色明确许下的拍照承诺登记生图任务。

        source_session 同时就是后续投递目标，因此群聊承诺只回到原群。
        """

        scope = str(getattr(commitment, "source_session", "") or "").strip()
        if (
            not commitment.id
            or not scope
            or not self._commitment_requests_photo(commitment)
            or not self._photo_commitment_owner_allowed(commitment, owner)
        ):
            return False
        observed_at = observed_at or life_now()
        execute_at = self._photo_commitment_execute_at(commitment, observed_at)
        if execute_at is None:
            await self.archive.set_commitment_status(
                commitment.id, "pending", observed_at.isoformat(timespec="seconds")
            )
            return False
        if execute_at < observed_at:
            execute_at = observed_at
        task_key = f"commitment_photo:{commitment.id}"
        refresh = getattr(self.archive, "reschedule_durable_task", None)
        if callable(refresh) and await refresh(
            task_key, execute_at.strftime("%Y-%m-%d %H:%M:%S")
        ):
            return True
        await self.archive.enqueue_durable_task(
            task_key,
            "commitment_photo",
            {
                "scope": scope,
                "commitment_id": commitment.id,
                "prompt": str(commitment.content or "").strip(),
                "execute_at": execute_at.strftime("%Y-%m-%d %H:%M:%S"),
                "source_message_id": str(commitment.source_message_id or ""),
            },
            priority=88,
            available_at=execute_at.strftime("%Y-%m-%d %H:%M:%S"),
            max_attempts=4,
        )
        return True

    async def reconcile_commitment_photo_tasks(
        self, now: datetime.datetime | None = None
    ) -> int:
        """补建尚未进入生图队列的拍照承诺，按原会话可靠恢复。"""

        getter = getattr(self.archive, "get_commitments", None)
        if not callable(getter):
            return 0
        now = now or life_now()
        commitments = await getter(status="", limit=200)
        existing_tasks = await self.archive.get_durable_tasks(
            kind="commitment_photo", limit=500
        )
        existing_keys = {str(item.task_key or "") for item in existing_tasks}
        created = 0
        for commitment in commitments:
            if str(getattr(commitment, "status", "") or "") not in {
                "active",
                "scheduled",
            }:
                continue
            key = f"commitment_photo:{getattr(commitment, 'id', 0)}"
            if key in existing_keys:
                continue
            if await self.schedule_commitment_photo(commitment, observed_at=now):
                existing_keys.add(key)
                created += 1
        return created

    async def schedule_commitment_video(
        self,
        commitment: CommitmentRecord,
        *,
        owner: str = "",
        observed_at: datetime.datetime | None = None,
    ) -> bool:
        """为当前角色明确许下的拍视频承诺登记持久视频任务。

        source_session 同时就是后续投递目标，因此群聊承诺只回到原群。
        """

        scope = str(getattr(commitment, "source_session", "") or "").strip()
        if (
            not commitment.id
            or not scope
            or not self._commitment_requests_video(commitment)
            or not self._photo_commitment_owner_allowed(commitment, owner)
        ):
            return False
        observed_at = observed_at or life_now()
        execute_at = self._photo_commitment_execute_at(commitment, observed_at)
        if execute_at is None:
            await self.archive.set_commitment_status(
                commitment.id, "pending", observed_at.isoformat(timespec="seconds")
            )
            return False
        if execute_at < observed_at:
            execute_at = observed_at
        task_key = f"commitment_video:{commitment.id}"
        refresh = getattr(self.archive, "reschedule_durable_task", None)
        if callable(refresh) and await refresh(
            task_key, execute_at.strftime("%Y-%m-%d %H:%M:%S")
        ):
            return True
        await self.archive.enqueue_durable_task(
            task_key,
            "commitment_video",
            {
                "scope": scope,
                "commitment_id": commitment.id,
                "prompt": str(commitment.content or "").strip(),
                "execute_at": execute_at.strftime("%Y-%m-%d %H:%M:%S"),
                "source_message_id": str(commitment.source_message_id or ""),
            },
            priority=87,
            available_at=execute_at.strftime("%Y-%m-%d %H:%M:%S"),
            max_attempts=4,
        )
        return True

    async def reconcile_commitment_video_tasks(
        self, now: datetime.datetime | None = None
    ) -> int:
        """补建尚未进入视频队列的拍视频承诺，按原会话可靠恢复。"""

        getter = getattr(self.archive, "get_commitments", None)
        if not callable(getter):
            return 0
        now = now or life_now()
        commitments = await getter(status="", limit=200)
        existing_tasks = await self.archive.get_durable_tasks(
            kind="commitment_video", limit=500
        )
        existing_keys = {str(item.task_key or "") for item in existing_tasks}
        created = 0
        for commitment in commitments:
            if str(getattr(commitment, "status", "") or "") not in {
                "active",
                "scheduled",
            }:
                continue
            key = f"commitment_video:{getattr(commitment, 'id', 0)}"
            if key in existing_keys:
                continue
            if await self.schedule_commitment_video(commitment, observed_at=now):
                existing_keys.add(key)
                created += 1
        return created

    async def run_commitment_photo_task(self, task: Any) -> dict[str, Any]:
        """执行一条承诺拍照生图，并在真实投递后结算承诺。"""

        payload = dict(getattr(task, "payload", {}) or {})
        scope = str(payload.get("scope") or "").strip()
        commitment_id = int(payload.get("commitment_id") or 0)
        if not scope or not commitment_id:
            return {"outcome": "invalid", "reason": "任务载荷不完整"}
        commitment = await self.archive.get_commitment(commitment_id)
        if commitment is None:
            return {"outcome": "invalid", "reason": "承诺记录不存在"}
        if commitment.status in {"done", "cancelled", "expired", "delivery_failed"}:
            return {"outcome": commitment.status, "reason": "承诺已经进入终态"}
        image_config = getattr(getattr(self, "config", None), "image_generation", None)
        if not bool(getattr(image_config, "enabled", False)):
            retry_at = life_now() + datetime.timedelta(minutes=60)
            return {
                "retry_at": retry_at.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": "图片生成功能未启用，等待配置开启",
            }
        prompt = str(payload.get("prompt") or commitment.content or "").strip()
        if not prompt or not self._commitment_requests_photo(commitment):
            await self.archive.set_commitment_status(
                commitment.id, "cancelled", life_now().isoformat(timespec="seconds")
            )
            return {"outcome": "invalid", "reason": "承诺没有结构化图片执行类型"}
        event = SimpleNamespace(
            unified_msg_origin=scope,
            session_id=scope,
            message_id=str(
                payload.get("source_message_id") or f"commitment-photo:{commitment.id}"
            ),
            message_str=prompt,
            _daily_life_commitment_id=commitment.id,
            _daily_life_media_reply_name="承诺的生活照片",
        )
        generator = getattr(self, "life_image_generate", None)
        if not callable(generator):
            raise RuntimeError("当前运行时没有可用的图片生成工具")

        async def mark_delivery_failed_on_last_attempt() -> None:
            if int(getattr(task, "attempts", 0) or 0) >= int(
                getattr(task, "max_attempts", 0) or 0
            ):
                await self.archive.set_commitment_status(
                    commitment.id,
                    "delivery_failed",
                    life_now().isoformat(timespec="seconds"),
                )

        try:
            result = await generator(
                event,
                f"按这项已到期的拍照承诺，生成并发送一张真实自然的生活照片：{prompt}",
                subject_route="free",
            )
        except Exception:
            await mark_delivery_failed_on_last_attempt()
            raise
        try:
            result_payload = json.loads(str(result or ""))
        except (TypeError, ValueError):
            result_payload = {}
        if (
            not isinstance(result_payload, dict)
            or result_payload.get("status") != "sent"
            or result_payload.get("media") != "image"
        ):
            error = str(result or "图片生成或发送未成功").strip()
            await mark_delivery_failed_on_last_attempt()
            raise RuntimeError(f"承诺拍照执行失败：{error}")
        await self.archive.set_commitment_status(
            commitment.id, "done", life_now().isoformat(timespec="seconds")
        )
        reply_sent = bool(getattr(event, "_daily_life_media_reply_sent", False))
        followup = getattr(self, "_send_delivered_media_followup", None)
        if not reply_sent and callable(followup):
            reply_sent = await followup(
                scope,
                media_name="承诺的生活照片",
                request_text=prompt,
                delivery_text="照片已成功送达，承诺已经履行",
                guidance="像角色本人履行先前约定后顺手接一句，不要写成系统通知。",
                source_event=event,
                source="commitment_photo_followup",
            )
        logger.info(f"{LOG_PREFIX} 已履行承诺拍照：编号={commitment.id}")
        return {
            "outcome": "sent",
            "commitment_id": commitment.id,
            "reply_sent": reply_sent,
        }

    async def run_commitment_video_task(self, task: Any) -> dict[str, Any]:
        """执行一条承诺拍视频，并等待真实视频投递完成后结算。"""

        payload = dict(getattr(task, "payload", {}) or {})
        scope = str(payload.get("scope") or "").strip()
        commitment_id = int(payload.get("commitment_id") or 0)
        if not scope or not commitment_id:
            return {"outcome": "invalid", "reason": "任务载荷不完整"}
        commitment = await self.archive.get_commitment(commitment_id)
        if commitment is None:
            return {"outcome": "invalid", "reason": "承诺记录不存在"}
        if commitment.status in {"done", "cancelled", "expired", "delivery_failed"}:
            return {"outcome": commitment.status, "reason": "承诺已经进入终态"}
        video_config = getattr(getattr(self, "config", None), "video_generation", None)
        if not bool(getattr(video_config, "enabled", False)):
            retry_at = life_now() + datetime.timedelta(minutes=60)
            return {
                "retry_at": retry_at.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": "视频生成功能未启用，等待配置开启",
            }
        prompt = str(payload.get("prompt") or commitment.content or "").strip()
        if not prompt or not self._commitment_requests_video(commitment):
            await self.archive.set_commitment_status(
                commitment.id, "cancelled", life_now().isoformat(timespec="seconds")
            )
            return {"outcome": "invalid", "reason": "承诺不包含明确拍视频动作"}
        completion = asyncio.get_running_loop().create_future()
        event = SimpleNamespace(
            unified_msg_origin=scope,
            session_id=scope,
            message_id=str(
                payload.get("source_message_id") or f"commitment-video:{commitment.id}"
            ),
            message_str=prompt,
            _daily_life_commitment_video_future=completion,
            _daily_life_commitment_id=commitment.id,
            _daily_life_media_reply_name="承诺的生活视频",
        )
        generator = getattr(self, "life_video_generate", None)
        if not callable(generator):
            raise RuntimeError("当前运行时没有可用的视频生成工具")

        async def mark_delivery_failed_on_last_attempt() -> None:
            if int(getattr(task, "attempts", 0) or 0) >= int(
                getattr(task, "max_attempts", 0) or 0
            ):
                await self.archive.set_commitment_status(
                    commitment.id,
                    "delivery_failed",
                    life_now().isoformat(timespec="seconds"),
                )

        try:
            result = await generator(
                event,
                f"按这项已到期的拍视频承诺，生成并发送一段真实自然的生活视频：{prompt}",
                subject_route="free",
            )
        except Exception:
            await mark_delivery_failed_on_last_attempt()
            raise
        try:
            result_payload = json.loads(str(result or ""))
        except (TypeError, ValueError):
            result_payload = {}
        status = (
            result_payload.get("status") if isinstance(result_payload, dict) else ""
        )
        if status == "sent" and result_payload.get("media") == "video":
            outcome = "sent"
        elif status == "pending" and result_payload.get("media") == "video":
            timeout_seconds = int(getattr(video_config, "timeout_seconds", 300) or 300)
            wait_seconds = max(60, min(timeout_seconds + 120, 1680))
            try:
                outcome = await asyncio.wait_for(
                    asyncio.shield(completion), timeout=wait_seconds
                )
            except asyncio.TimeoutError as exc:
                await mark_delivery_failed_on_last_attempt()
                raise RuntimeError("承诺拍视频等待生成或投递超时") from exc
        else:
            error = str(result or "视频生成或发送未成功").strip()
            await mark_delivery_failed_on_last_attempt()
            raise RuntimeError(f"承诺拍视频执行失败：{error}")
        if outcome == "cancelled":
            await self.archive.set_commitment_status(
                commitment.id, "cancelled", life_now().isoformat(timespec="seconds")
            )
            return {"outcome": "cancelled", "commitment_id": commitment.id}
        if outcome != "sent":
            await mark_delivery_failed_on_last_attempt()
            raise RuntimeError(f"承诺拍视频执行失败：{outcome}")
        await self.archive.set_commitment_status(
            commitment.id, "done", life_now().isoformat(timespec="seconds")
        )
        logger.info(f"{LOG_PREFIX} 已履行承诺拍视频：编号={commitment.id}")
        return {"outcome": "sent", "commitment_id": commitment.id}

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
            or self._commitment_requests_photo(commitment)
            or self._commitment_requests_video(commitment)
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
        expires_at = observed_at + datetime.timedelta(hours=24) if condition else None
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
        recent_context = self._format_recent_context_messages(recent_messages, now=now)
        life_context = await self._proactive_commitment_life_context(now)
        target_name = str(getattr(relationship, "name", "") or "对方").strip()
        interaction_context = {
            "mode": str(getattr(interaction, "mode", "") or "unknown"),
            "mode_label": str(getattr(interaction, "mode_label", "") or "互动方式未知"),
            "evidence": str(getattr(interaction, "evidence", "") or ""),
        }
        fixed = f"""你负责在一个已经到期的主动联系承诺真正发送前做最后语义复核。

判断规则：
1. 这是当前角色已经明确许下的未来联系或提醒，不受普通闲时回复的静默门槛和概率限制。
2. 如果近期证据表明事情已完成、已取消、已改期、对方已经主动出现，或现在发送明显失去意义，则不发送，并准确给出 settlement。
3. 如果仍应履行，只生成一条符合当前人设、关系和现场进展的简短自然消息；不要声称尚未发生的动作已经完成。
4. 如果主动动作含 condition 且当前证据不足以确认条件已经成立，返回 should_send=false、settlement=wait，并给出 5 到 60 分钟的 retry_after_minutes；不要提前发送。
5. 传输会话不代表现实分开。若双方正同处现场且承诺仍需履行，应生成一句面对面直接说出的自然招呼或提醒，不能仅因同处现场静默完成；这时不要使用“发消息、你那边、到哪了、上线”等远程措辞。
6. 只依据提供的证据，不补造地点、进度或对方反应。
7. 本通道只发送文字或语音，不发送照片和视频；不能用“照片给你了”“这两张慢慢看”等正文代替媒体投递。需要交付媒体的承诺不得在这里判为已完成。

{CORE_EMOJI_DELIVERY_RULES}
{CORE_PROACTIVE_VOICE_RULES}
{CORE_PROACTIVE_CONTINUITY_RULES}

只返回严格 JSON：
{{"should_send":true,"reply_text":"","reason":"","settlement":"send|wait|already_done|cancelled|superseded|invalid","retry_after_minutes":0,"expression_intent":{{"channel":"text|voice","confidence":0.0,"emotion":"","emotion_category":"","voice_style":"","emoji_intent":"","action_intent":"","send_emoji":false,"reason":""}}}}
"""
        dynamic = f"""当前时间：{now.strftime("%Y-%m-%d %H:%M:%S")}
当前角色人设：
{persona or "暂无额外人设。"}

主动语音消息能力：{self._proactive_voice_capability(scope)}
承诺对象：{target_name}
已保存承诺：{json.dumps(commitment.as_dict(), ensure_ascii=False)}
主动动作：{json.dumps(task_payload, ensure_ascii=False)}
当前互动方式：{json.dumps(interaction_context, ensure_ascii=False)}
当前生活依据：{json.dumps(life_context, ensure_ascii=False)}
最近真实交流：
{recent_context}"""
        prompt = cache_friendly_prompt(
            fixed,
            dynamic,
            dynamic_title="到期承诺复核资料",
        )
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
                strict=True,
                validator=self._validate_proactive_commitment_decision,
                fallback={
                    "should_send": False,
                    "reply_text": "",
                    "reason": "模型未给出可执行裁定",
                    "settlement": "wait",
                    "retry_after_minutes": 10,
                    "expression_intent": {},
                },
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
        if commitment.status in {"done", "cancelled", "expired", "delivery_failed"}:
            return {
                "outcome": commitment.status,
                "reason": "承诺已经进入终态",
            }
        if payload.get("shared_activity_contact") is True:
            return await self._run_shared_activity_contact(task, commitment)
        now = life_now()
        expires_at = self._follow_up_execute_at(payload.get("expires_at"))
        if expires_at is not None and now > expires_at:
            await self.archive.set_commitment_status(
                commitment.id, "expired", now.isoformat(timespec="seconds")
            )
            return {"outcome": "expired", "reason": "条件承诺已超过有效期"}
                # 已排队的后续任务实际可能需要发送媒体，而非文字消息。
        media_scheduler = None
        if self._commitment_requests_photo(commitment):
            media_scheduler = self.schedule_commitment_photo
        elif self._commitment_requests_video(commitment):
            media_scheduler = self.schedule_commitment_video
        if media_scheduler is not None:
            scheduled = await media_scheduler(commitment, observed_at=now)
            return {
                "outcome": "media_scheduled" if scheduled else "media_pending",
                "reason": "媒体承诺必须通过实际媒体投递履行",
                "commitment_id": commitment.id,
            }
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
            status = (
                "cancelled" if settlement in {"cancelled", "superseded"} else "done"
            )
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
        try:
            sent = await self._send_proactive_message(
                scope,
                reply_text,
                "主动承诺消息发送失败",
                relationship=relationship,
                contact_type="friend" if ":FriendMessage:" in scope else "",
                send_payload=send_payload,
                source_message_id=str(payload.get("source_message_id") or ""),
                raise_delivery_errors=True,
            )
        except PermanentScopeDeliveryError as exc:
            await self.archive.set_commitment_status(
                commitment.id, "delivery_failed", now.isoformat(timespec="seconds")
            )
            logger.warning(
                f"{LOG_PREFIX} 主动承诺无法投递，已标记终态：编号={commitment.id}；原因={exc}"
            )
            return {"outcome": "undeliverable", "reason": str(exc), "code": exc.code}
        except ScopeDeliveryError as exc:
            if int(getattr(task, "attempts", 0) or 0) >= int(
                getattr(task, "max_attempts", 0) or 0
            ):
                await self.archive.set_commitment_status(
                    commitment.id, "delivery_failed", now.isoformat(timespec="seconds")
                )
                return {
                    "outcome": "undeliverable",
                    "reason": str(exc),
                    "code": exc.code,
                }
            raise
        if not sent:
            raise RuntimeError("主动承诺消息未成功投递")
        if settle_commitment:
            await self.archive.set_commitment_status(
                commitment.id, "done", now.isoformat(timespec="seconds")
            )
        logger.info(f"{LOG_PREFIX} 已履行主动联系承诺：编号={commitment.id}")
        return {"outcome": "sent", "reply_text": reply_text}
