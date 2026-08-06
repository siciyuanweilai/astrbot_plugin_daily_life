from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import uuid
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...models import ChatSummaryRecord, CommitmentRecord
from ...prompts import CORE_PERSONA_PRONOUN_RULES
from ..markers import LOG_PREFIX
from .jsonclean import call_pure_json


class ChatMemoryBatchMixin:
    """保存收到的聊天快照，并按互不重叠的会话批次提炼。"""

    _BATCH_READABLE_FIELDS = {
        "brief",
        "content",
        "correction",
        "evidence",
        "feedback",
        "impression_delta",
        "inner_monologue",
        "long_summary",
        "meaning",
        "note",
        "reason",
        "reactivation_hint",
        "relationship_story",
        "reply_strategy",
        "summary",
        "title",
        "topic",
    }
    _BATCH_REFERENCE_SEPARATORS = {",", "，", ";", "；", "、", "|", "｜", "\n", "\r"}

    def _init_chat_memory_batcher(self) -> None:
        self._chat_memory_wakeup = asyncio.Event()
        self._chat_memory_worker_task: asyncio.Task | None = None
        self._chat_memory_stopping = False

    def _start_chat_memory_batcher(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._ensure_chat_memory_worker()
        if self._chat_memory_worker_task is not None:
            self._chat_memory_wakeup.set()

    def _ensure_chat_memory_worker(self) -> None:
        if self._chat_memory_stopping or not self.config.memory.enabled:
            return
        task = self._chat_memory_worker_task
        if task is None or task.done():
            self._chat_memory_worker_task = asyncio.create_task(
                self._chat_memory_worker(), name="daily-life-chat-memory-batcher"
            )

    async def _shutdown_chat_memory_batcher(self) -> None:
        self._chat_memory_stopping = True
        self._chat_memory_wakeup.set()
        task = self._chat_memory_worker_task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._chat_memory_worker_task = None

    async def capture_chat_memory_message(
        self,
        event: Any,
        now: datetime.datetime | None = None,
    ) -> bool:
        if not self.config.memory.enabled or event is None:
            return False
        message = str(getattr(event, "message_str", "") or "").strip()
        if (
            not message
            or self._event_has_command_handler(event)
            or self.event_was_recalled(event, log_skip=True)
        ):
            return False
        now = now or life_now()
        sender_name = await self.contact_resolver.resolve_event_sender(event)
        if self.event_was_recalled(event, log_skip=True):
            return False
        meta = await self._event_context_meta(event, sender_name, now)
        session_id = str(meta.get("session_id") or "").strip()
        if not session_id:
            return False
        message_id = str(meta.get("message_id") or "").strip()
        occurred_at = now.isoformat(timespec="seconds")
        if message_id:
            event_key = f"{session_id}:{message_id}"
        else:
            identity = json.dumps(
                [session_id, meta.get("sender_profile_id", ""), message, occurred_at],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            event_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        snapshot = {
            "event_key": event_key,
            "session_id": session_id,
            "role": "user",
            "message_id": message_id,
            "sender_profile_id": meta.get("sender_profile_id", ""),
            "sender_name": sender_name,
            "platform": meta.get("platform", ""),
            "user_id": meta.get("user_id", ""),
            "group_id": meta.get("group_id", ""),
            "group_name": meta.get("group_name", ""),
            "is_group": meta.get("is_group") == "true",
            "is_directed": meta.get("is_directed") == "true",
            "is_quoted": meta.get("is_quoted") == "true",
            "message_text": message,
            "message_facts": self._event_message_component_facts(event, message),
            "quote_context": meta.get("quote_context", ""),
            "structured_context": meta.get("structured", ""),
            "occurred_at": occurred_at,
        }
        _, inserted = await self.archive.enqueue_chat_memory_message(snapshot)
        if inserted:
            self._ensure_chat_memory_worker()
            self._chat_memory_wakeup.set()
        return inserted

    async def capture_chat_memory_bot_reply(
        self,
        event: Any,
        now: datetime.datetime | None = None,
    ) -> bool:
        if not self.config.memory.enabled or event is None:
            return False
        if self._event_has_command_handler(event) or self.event_was_recalled(
            event, log_skip=True
        ):
            return False
        result_reader = getattr(self, "_structured_result_text", None)
        if not callable(result_reader):
            return False
        message, media = result_reader(event)
        message = str(message or "").strip()
        if not message:
            return False
        now = now or life_now()
        sender_name = await self.contact_resolver.resolve_event_sender(event)
        meta = await self._event_context_meta(event, sender_name, now)
        session_id = str(meta.get("session_id") or "").strip()
        if not session_id:
            return False
        source_message_id = str(meta.get("message_id") or "").strip()
        identity = json.dumps(
            [session_id, source_message_id, message],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        event_key = "bot:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self_id = str(self._safe_event_call(event, "get_self_id") or "bot").strip()
        snapshot = {
            "event_key": event_key,
            "session_id": session_id,
            "role": "assistant",
            "message_id": "",
            "sender_profile_id": self_id or "bot",
            "sender_name": "我",
            "platform": meta.get("platform", ""),
            "user_id": self_id or "bot",
            "group_id": meta.get("group_id", ""),
            "group_name": meta.get("group_name", ""),
            "is_group": meta.get("is_group") == "true",
            "is_directed": False,
            "is_quoted": False,
            "message_text": message,
            "message_facts": f"已发送{media}" if media else "",
            "quote_context": "",
            "structured_context": self.format_structured_message_context(
                session_id, limit=6
            ),
            "occurred_at": now.isoformat(timespec="seconds"),
        }
        _, inserted = await self.archive.enqueue_chat_memory_message(snapshot)
        if inserted:
            self._ensure_chat_memory_worker()
            self._chat_memory_wakeup.set()
        return inserted

    async def _chat_memory_worker(self) -> None:
        poll = max(2, int(self.config.memory.worker_poll_seconds))
        while not self._chat_memory_stopping:
            try:
                await asyncio.wait_for(self._chat_memory_wakeup.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass
            self._chat_memory_wakeup.clear()
            if self._chat_memory_stopping:
                return
            try:
                await self.process_due_chat_memory_batches()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"{LOG_PREFIX} 聊天记忆批处理巡检失败：{exc}")

    @staticmethod
    def _chat_memory_elapsed_seconds(value: str, now: datetime.datetime) -> float:
        try:
            point = datetime.datetime.fromisoformat(str(value or ""))
            if point.tzinfo and not now.tzinfo:
                point = point.replace(tzinfo=None)
            if now.tzinfo and not point.tzinfo:
                point = point.replace(tzinfo=now.tzinfo)
            return max(0.0, (now - point).total_seconds())
        except (TypeError, ValueError):
            return 0.0

    async def process_due_chat_memory_batches(
        self, now: datetime.datetime | None = None
    ) -> int:
        if not self.config.memory.enabled:
            return 0
        now = now or life_now()
        processed = 0
        for state in await self.archive.list_chat_memory_sessions():
            pending_count = int(state.get("pending_count") or 0)
            threshold = (
                self.config.memory.group_message_threshold
                if bool(state.get("is_group"))
                else self.config.memory.private_message_threshold
            )
            idle_due = (
                pending_count >= self.config.memory.idle_flush_min_messages
                and self._chat_memory_elapsed_seconds(
                    str(state.get("last_message_at") or ""), now
                )
                >= self.config.memory.idle_flush_seconds
            )
            if pending_count < threshold and not idle_due:
                continue
            while pending_count >= threshold or idle_due:
                batch = await self.archive.begin_chat_memory_batch(
                    str(state["session_id"]),
                    max_messages=self.config.memory.max_batch_messages,
                    max_chars=self.config.memory.max_batch_chars,
                )
                if not batch:
                    break
                if not await self._process_chat_memory_batch(batch):
                    break
                processed += 1
                pending_count -= len(batch["messages"])
                idle_due = (
                    pending_count >= self.config.memory.idle_flush_min_messages
                    and self._chat_memory_elapsed_seconds(
                        str(state.get("last_message_at") or ""), now
                    )
                    >= self.config.memory.idle_flush_seconds
                )
        return processed

    def _build_chat_memory_batch_prompt(self, batch: dict[str, Any]) -> str:
        messages = []
        participants: dict[str, dict[str, str]] = {}
        for row in batch["messages"]:
            role = str(row.get("role") or "user").strip().lower()
            profile_id = str(row.get("sender_profile_id") or "").strip()
            if profile_id and role == "user":
                participants[profile_id] = {
                    "profile_id": profile_id,
                    "name": str(row.get("sender_name") or profile_id),
                }
            messages.append(
                {
                    "row_id": row["id"],
                    "message_id": row.get("message_id", ""),
                    "time": row.get("occurred_at", ""),
                    "role": role,
                    "speaker_profile_id": profile_id,
                    "speaker_name": row.get("sender_name", ""),
                    "text": row.get("message_text", ""),
                    "message_facts": row.get("message_facts", ""),
                    "is_directed": bool(row.get("is_directed")),
                    "is_quoted": bool(row.get("is_quoted")),
                    "quote_context": row.get("quote_context", ""),
                }
            )
        last = batch["messages"][-1]
        source = {
            "session_id": batch["session_id"],
            "scope": {
                "type": "group" if bool(last.get("is_group")) else "private",
                "group_id": str(last.get("group_id") or ""),
                "group_name": str(last.get("group_name") or ""),
            },
            "participants": list(participants.values()),
            "messages": messages,
            "current_temporal_facts": batch.get("current_temporal_facts", []),
        }
        schema = {
            "worth_saving": True,
            "brief": "",
            "long_summary": "",
            "people": [],
            "memory_targets": [
                {
                    "profile_id": "",
                    "name": "",
                    "note": "",
                    "points": [],
                    "subjective_name": "",
                    "subjective_tags": [],
                    "relationship_story": "",
                }
            ],
            "preferences": [],
            "life_episodes": [],
            "visibility": {
                "level": "focused|ignored|seen_but_ignored",
                "attention_level": 0,
                "priority": "low|normal|high",
                "is_directed_at_bot": False,
                "freshness": "fresh|recent|stale|reactivated",
                "psychological_freshness": 0,
                "reason": "",
            },
            "group_environment": {
                "atmosphere": "冷清|平稳|活跃|刷屏|争论|玩梗|欢迎|其他",
                "topic": "",
                "topic_owner": "self_topic|target_user_topic|shared_group_topic|external_topic|ambiguous_topic",
                "active_users": 0,
                "is_multithread": False,
                "is_spam": False,
                "is_repetition": False,
                "is_discussing_bot": False,
                "suitable_to_join": "yes|no|observe",
                "bot_watch_state": "blackout|peek|skim_window|active_watch|engaged",
                "participation_desire": 0,
                "complexity_score": 0,
                "understanding_confidence": 0,
                "deep_analysis_needed": False,
                "summary": "",
            },
            "action_decision": {
                "action": "save_memory|skip_memory|observe|reply|comfort|push_back|join_ritual|eat_melon|need_deep_analysis",
                "reason": "",
                "confidence": 0.0,
                "scene_type": "",
                "topic_owner": "",
                "understanding": "understood|partial|unclear",
                "deep_analysis": False,
                "inner_monologue": "",
                "reply_strategy": "",
            },
            "behavior_feedback": [
                {
                    "scene": "",
                    "action": "",
                    "feedback": "",
                    "result": "positive|neutral|negative|unknown",
                    "score": 0.0,
                    "reason": "",
                }
            ],
            "life_terms": [
                {
                    "term": "",
                    "meaning": "",
                    "scope": "",
                    "scene": "",
                    "examples": [],
                    "familiarity": 0,
                    "confidence": 0.0,
                    "evidence": "",
                }
            ],
            "commitments": [
                {
                    "content": "",
                    "kind": "plan",
                    "trigger_date": "",
                    "trigger_time": "",
                    "time_window": "",
                    "owner": "当前角色|说话人|共同|未定",
                    "people": [],
                    "place": "",
                    "confidence": 0.0,
                    "source_message_ids": [],
                }
            ],
            "temporal_facts": [
                {
                    "operation": "ADD|UPDATE|INVALIDATE|NONE",
                    "subject": "稳定主体编号或明确名称",
                    "predicate": "稳定、单一、可复用的关系名",
                    "object_value": "任意 JSON 值；INVALIDATE 和 NONE 时可为空",
                    "confidence": 0.0,
                    "source_message_id": "输入消息中真实存在的 message_id",
                    "evidence_signal": "reinforce|dispute",
                    "evidence_summary": "证据的简短中文概括",
                }
            ],
        }
        return (
            "你负责把一个连续聊天批次整理成可长期复用的记忆。只依据输入证据，保持每条信息的说话人归属；"
            "输入中的 role=user 是用户消息，role=assistant 是我实际已经发送的回复；"
            "区分稳定事实、偏好、关系认识、生活事件、纠错和未来约定。暂时情绪、寒暄、无证据推断与重复信息不保存。"
            "无法可靠归属的信息不要输出。群聊中的人物必须使用输入给出的 profile_id。"
            f"人物称谓与叙述视角规则：\n{CORE_PERSONA_PRONOUN_RULES}\n"
            "所有面向用户展示的自然语言字段必须使用简体中文；没有内容时字段留空，不要用任何语言解释为什么没有内容。"
            "自然语言字段必须写可读的事实摘要，不得复制 row_id、message_id、target_id 或其他内部编号；"
            "内部编号只能填写到名称明确的专用 ID 字段。"
            "输出一个严格 JSON 对象，不要解释，不要 Markdown。没有长期信息时输出 worth_saving=false，其他数组可为空；"
            "即使没有长期摘要，也要保留证据明确的 commitments。brief 是简短主题，long_summary 是忠于证据的批次摘要。"
            "visibility、group_environment、action_decision 只记录批次中有明确依据的实际感知，不生成空壳；私聊不填写 group_environment。"
            "worth_saving 只控制长期摘要；这三个实际感知字段有依据时可以独立输出。"
            "behavior_feedback 只记录发生在我的回复之后、由后续用户消息明确证实的真实反馈；life_terms 只记录后续理解仍有帮助的黑话、梗或代称。"
            "behavior_feedback 或 life_terms 有内容时属于可复用信息，应同时给出有效摘要并设置 worth_saving=true。\n"
            "temporal_facts 只记录有明确证据、以后仍需按时间查询的结构化事实；subject 和 predicate 必须是稳定结构键，不得从措辞关键词临时拼接。"
            "对照 current_temporal_facts：新增键用 ADD，同键值改变用 UPDATE，明确失效用 INVALIDATE，没有变化用 NONE；不得省略历史变化而直接覆盖。"
            "source_message_id 必须来自输入批次，evidence_signal 只能是 reinforce 或 dispute；没有可靠消息证据就不要输出该事实。\n"
            f"输出结构：{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}\n"
            f"输入批次：{json.dumps(source, ensure_ascii=False, separators=(',', ':'))}"
        )

    def _normalize_chat_memory_batch_payload(
        self,
        payload: dict[str, Any],
        batch: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = self._sanitize_batch_readable_fields(dict(payload), batch)
        worth_saving = self._bool_payload(normalized.get("worth_saving"))
        brief = self._chinese_text_payload(normalized.get("brief"))
        long_summary = self._chinese_text_payload(normalized.get("long_summary"))
        if not worth_saving:
            brief = ""
            long_summary = ""
        elif not (brief or long_summary):
            worth_saving = False
        normalized["worth_saving"] = worth_saving
        normalized["brief"] = brief
        normalized["long_summary"] = long_summary

        visibility = (
            dict(normalized.get("visibility"))
            if isinstance(normalized.get("visibility"), dict)
            else {}
        )
        for field in ("reason", "reactivation_hint"):
            visibility[field] = self._chinese_text_payload(visibility.get(field))
        visibility["is_directed_at_bot"] = any(
            self._bool_payload(row.get("is_directed"))
            for row in batch.get("messages", [])
            if isinstance(row, dict)
        )
        normalized["visibility"] = visibility

        environment = (
            dict(normalized.get("group_environment"))
            if isinstance(normalized.get("group_environment"), dict)
            else {}
        )
        for field in ("topic", "summary"):
            environment[field] = self._chinese_text_payload(environment.get(field))
        if not any(
            self._bool_payload(row.get("is_group"))
            for row in batch.get("messages", [])
            if isinstance(row, dict)
        ):
            environment = {}
        normalized["group_environment"] = environment

        decision = (
            dict(normalized.get("action_decision"))
            if isinstance(normalized.get("action_decision"), dict)
            else {}
        )
        for field in ("reason", "scene_type", "inner_monologue", "reply_strategy"):
            decision[field] = self._chinese_text_payload(decision.get(field))
        normalized["action_decision"] = decision
        return normalized

    @classmethod
    def _split_batch_reference_segments(cls, value: Any) -> list[str]:
        segments: list[str] = []
        current: list[str] = []
        for char in str(value or ""):
            if char in cls._BATCH_REFERENCE_SEPARATORS:
                segment = "".join(current).strip()
                if segment:
                    segments.append(segment)
                current = []
                continue
            current.append(char)
        segment = "".join(current).strip()
        if segment:
            segments.append(segment)
        return segments

    @staticmethod
    def _batch_reference_ids(batch: dict[str, Any]) -> set[str]:
        values: set[str] = set()
        for row in batch.get("messages", []):
            if not isinstance(row, dict):
                continue
            for key in ("id", "message_id"):
                value = str(row.get(key) or "").strip()
                if value:
                    values.add(value)
        return values

    @classmethod
    def _sanitize_batch_readable_text(
        cls,
        value: Any,
        reference_ids: set[str],
    ) -> str:
        body = str(value or "").strip()
        if not body or not reference_ids:
            return body
        segments = cls._split_batch_reference_segments(body)
        matched = [
            segment
            for segment in segments
            if segment.removeprefix("#").strip() in reference_ids
        ]
        if not matched:
            whitespace_parts = body.split()
            if whitespace_parts and all(
                part.removeprefix("#").strip() in reference_ids
                for part in whitespace_parts
            ):
                matched = whitespace_parts
                segments = whitespace_parts
        if not matched:
            return body
        readable = [
            segment
            for segment in segments
            if segment.removeprefix("#").strip() not in reference_ids
        ]
        if readable:
            return "；".join(readable)
        count = len(
            {
                segment.removeprefix("#").strip()
                for segment in matched
                if segment.removeprefix("#").strip()
            }
        )
        return f"来自 {count} 条聊天消息" if count else "来自聊天记录"

    @classmethod
    def _sanitize_batch_readable_fields(
        cls,
        value: Any,
        batch: dict[str, Any],
    ) -> Any:
        reference_ids = cls._batch_reference_ids(batch)

        def sanitize(item: Any, field: str = "") -> Any:
            if isinstance(item, dict):
                return {key: sanitize(raw, str(key)) for key, raw in item.items()}
            if isinstance(item, list):
                return [sanitize(raw, field) for raw in item]
            if field in cls._BATCH_READABLE_FIELDS and isinstance(item, str):
                return cls._sanitize_batch_readable_text(item, reference_ids)
            return item

        return sanitize(value)

    async def _save_batch_commitments(
        self, payload: dict[str, Any], batch: dict[str, Any]
    ) -> list[CommitmentRecord]:
        saved: list[CommitmentRecord] = []
        messages = batch["messages"]
        try:
            observed_at = datetime.datetime.fromisoformat(
                str(messages[-1].get("occurred_at") or "")
            )
        except (IndexError, TypeError, ValueError):
            observed_at = life_now()
        message_by_id: dict[str, dict[str, Any]] = {}
        for row in messages:
            message_by_id[str(row["id"])] = row
            if str(row.get("message_id") or "").strip():
                message_by_id[str(row["message_id"])] = row
        for raw in (
            payload.get("commitments", [])
            if isinstance(payload.get("commitments"), list)
            else []
        ):
            if not isinstance(raw, dict):
                continue
            commitment = CommitmentRecord.from_value(
                {
                    **raw,
                    "source": "chat_batch",
                    "source_session": batch["session_id"],
                    "source_message": "\n".join(
                        str(message_by_id[item].get("message_text") or "")
                        for item in [
                            str(value) for value in raw.get("source_message_ids", [])
                        ]
                        if item in message_by_id
                    )[:1000],
                }
            )
            if (
                not commitment
                or commitment.confidence < self.config.commitments.min_confidence
            ):
                continue
            stored = await self.archive.save_commitment(commitment)
            saved.append(stored)
            apply_to_day = getattr(self, "apply_commitment_to_current_day", None)
            if callable(apply_to_day):
                try:
                    await apply_to_day(
                        stored,
                        now=observed_at,
                        owner_hint=str(raw.get("owner") or "").strip(),
                    )
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 批次承诺合并到当天日程失败：{exc}")
            domain_settings = getattr(self.config, "domains", None)
            save_action_item = getattr(
                self.archive, "save_conversation_action_item", None
            )
            if (
                bool(getattr(domain_settings, "enabled", False))
                and bool(
                    getattr(domain_settings, "conversation_actions_enabled", False)
                )
                and callable(save_action_item)
            ):
                due_at = (
                    " ".join(
                        part
                        for part in (stored.trigger_date, stored.trigger_time)
                        if part
                    )
                    or stored.time_window
                )
                await save_action_item(
                    {
                        "commitment_id": stored.id,
                        "title": stored.content,
                        "owner": str(raw.get("owner") or "未定").strip(),
                        "due_at": due_at,
                        "status": "open",
                        "source_session": stored.source_session,
                        "source_message": stored.source_message,
                        "evidence": [stored.source_message]
                        if stored.source_message
                        else [],
                    }
                )
        return saved

    async def _save_batch_temporal_facts(
        self,
        payload: dict[str, Any],
        batch: dict[str, Any],
    ) -> list[Any]:
        """保存批次中由显式结构化操作描述的时间事实。

        Args:
            payload: 已归一化的聊天记忆结果。
            batch: 包含真实消息编号与时间的批次。

        Returns:
            成功写入或确认的事实记录。
        """

        writer = getattr(self.archive, "write_temporal_fact", None)
        evidence_saver = getattr(self.archive, "add_fact_evidence_signal", None)
        raw_facts = payload.get("temporal_facts")
        if not callable(writer) or not isinstance(raw_facts, list):
            return []
        rows_by_message_id: dict[str, dict[str, Any]] = {}
        for row in batch.get("messages", []):
            if not isinstance(row, dict):
                continue
            for key in ("message_id", "id"):
                message_id = str(row.get(key) or "").strip()
                if message_id:
                    rows_by_message_id[message_id] = row
        scope = str(batch.get("session_id") or "").strip()
        if not scope:
            return []
        saved: list[Any] = []
        for raw in raw_facts[:12]:
            if not isinstance(raw, dict):
                continue
            operation = str(raw.get("operation") or "NONE").strip().upper()
            if operation not in {"ADD", "UPDATE", "INVALIDATE", "NONE"}:
                continue
            source_message_id = str(raw.get("source_message_id") or "").strip()
            source_row = rows_by_message_id.get(source_message_id)
            if source_row is None:
                continue
            observed_at = str(source_row.get("occurred_at") or "").strip()
            try:
                confidence = max(0.0, min(float(raw.get("confidence") or 0.0), 1.0))
            except (TypeError, ValueError):
                confidence = 0.0
            subject = str(raw.get("subject") or "").strip()[:180]
            predicate = str(raw.get("predicate") or "").strip()[:120]
            if not subject or not predicate:
                continue
            try:
                fact = await writer(
                    operation,
                    {
                        "scope": scope,
                        "subject": subject,
                        "predicate": predicate,
                        "object_value": raw.get("object_value"),
                        "observed_at": observed_at,
                        "valid_from": observed_at,
                        "confidence": confidence,
                        "source": "chat_batch",
                        "source_type": "chat_message",
                        "source_id": source_message_id,
                        "provenance": {
                            "session_id": scope,
                            "message_id": source_message_id,
                            "batch_id": batch.get("id"),
                        },
                    },
                )
            except ValueError:
                continue
            if fact is None:
                continue
            saved.append(fact)
            signal = str(raw.get("evidence_signal") or "").strip().lower()
            if (
                operation != "NONE"
                and signal in {"reinforce", "dispute"}
                and callable(evidence_saver)
                and int(getattr(fact, "id", 0) or 0) > 0
            ):
                await evidence_saver(
                    {
                        "fact_id": int(fact.id),
                        "signal": signal,
                        "weight": 1.0,
                        "confidence": confidence,
                        "summary": self._chinese_text_payload(
                            raw.get("evidence_summary")
                        ),
                        "source": "chat_batch",
                        "source_id": source_message_id,
                        "observed_at": observed_at,
                        "provenance": {
                            "session_id": scope,
                            "message_id": source_message_id,
                        },
                    }
                )
        return saved

    async def _save_batch_memory_targets(
        self,
        payload: dict[str, Any],
        batch: dict[str, Any],
        fallback_meta: dict[str, str],
    ) -> list[dict[str, Any]]:
        rows_by_profile: dict[str, dict[str, Any]] = {}
        for row in batch["messages"]:
            profile_id = str(row.get("sender_profile_id") or "").strip()
            if profile_id:
                rows_by_profile[profile_id] = row
        saved: list[dict[str, Any]] = []
        targets = payload.get("memory_targets", [])
        if not isinstance(targets, list):
            return saved
        for target in targets[:8]:
            if not isinstance(target, dict):
                continue
            profile_id = str(target.get("profile_id") or "").strip()
            row = rows_by_profile.get(profile_id)
            if not row:
                continue
            meta = dict(fallback_meta)
            meta.update(
                {
                    "message_id": str(row.get("message_id") or row["id"]),
                    "platform": str(row.get("platform") or ""),
                    "user_id": str(row.get("user_id") or ""),
                    "sender_profile_id": profile_id,
                    "sender_name": str(
                        row.get("sender_name") or target.get("name") or profile_id
                    ),
                }
            )
            saved.extend(
                await self._save_memory_targets({"memory_targets": [target]}, meta)
            )
        return saved

    async def _save_chat_memory_batch_payload(
        self, payload: dict[str, Any], batch: dict[str, Any]
    ) -> ChatSummaryRecord | None:
        payload = self._normalize_chat_memory_batch_payload(payload, batch)
        rows = batch["messages"]
        last = rows[-1]
        meta = {
            "session_id": batch["session_id"],
            "message_id": str(last.get("message_id") or last["id"]),
            "platform": str(last.get("platform") or ""),
            "user_id": str(last.get("user_id") or ""),
            "sender_profile_id": str(last.get("sender_profile_id") or ""),
            "sender_name": str(last.get("sender_name") or ""),
            "group_id": str(last.get("group_id") or ""),
            "group_name": str(last.get("group_name") or ""),
            "date": str(last.get("occurred_at") or "")[:10],
            "is_group": "true" if last.get("is_group") else "false",
            "is_directed": "true" if last.get("is_directed") else "false",
            "is_quoted": "true" if last.get("is_quoted") else "false",
            "quote_context": str(last.get("quote_context") or ""),
            "structured": str(last.get("structured_context") or ""),
        }
        relationship = None
        profile_id = meta["sender_profile_id"]
        relationship_getter = getattr(self.archive, "get_relationship", None)
        if profile_id and callable(relationship_getter):
            try:
                relationship = await relationship_getter(profile_id)
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 读取聊天对象关系档案失败：{exc}")
        persona_hint = ""
        hint_getter = getattr(self, "_extract_speaker_persona_hint", None)
        if callable(hint_getter):
            try:
                persona_hint = self._str_payload(
                    await hint_getter(
                        meta["sender_name"],
                        relationship=relationship,
                    )
                )
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 提取聊天对象人设线索失败：{exc}")
        role_getter = getattr(self, "_current_role_label", None)
        if callable(role_getter):
            try:
                meta["current_role_label"] = (
                    self._str_payload(await role_getter()) or "我"
                )
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 读取当前角色称呼失败：{exc}")
                meta["current_role_label"] = "我"
        else:
            meta["current_role_label"] = "我"
        payload = await self._calibrate_chat_memory_payload(
            payload,
            meta,
            persona_hint,
        )
        payload = self._normalize_chat_memory_batch_payload(payload, batch)
        commitments = await self._save_batch_commitments(payload, batch)
        temporal_facts = await self._save_batch_temporal_facts(payload, batch)
        await self._save_memory_awareness_records(payload, meta)
        try:
            observed_at = datetime.datetime.fromisoformat(
                str(last.get("occurred_at") or "")
            )
        except (TypeError, ValueError):
            observed_at = life_now()
        await self._append_memory_decision_log(payload, meta, observed_at)
        if not payload.get("worth_saving"):
            return None
        summary = ChatSummaryRecord.from_value(
            {
                **payload,
                "session_id": batch["session_id"],
                "date": meta["date"],
                "source": "chat_batch",
            }
        )
        if not summary:
            if commitments or temporal_facts:
                return None
            raise ValueError("模型标记值得保存，但没有给出有效摘要")
        saved = await self.archive.save_chat_summary(summary)
        saved_records = await self._save_experience_payload(payload, meta, saved)
        saved_records["memory_targets"] = await self._save_batch_memory_targets(
            payload, batch, meta
        )
        await self._save_chat_memory_preferences(payload, meta, saved_records)
        self._schedule_chat_memory_memos(
            payload,
            meta,
            saved,
            saved_records,
            "\n".join(str(row.get("message_text") or "") for row in rows),
        )
        logger.info(
            f"{LOG_PREFIX} 聊天记忆批处理完成：批次={saved.id}；"
            f"记录={len(saved_records)}"
        )
        return saved

    async def _process_chat_memory_batch(self, batch: dict[str, Any]) -> bool:
        provider = await self._get_memory_provider()
        if not provider:
            await self.archive.fail_chat_memory_batch(
                batch["id"], "未找到可用的记忆模型"
            )
            return False
        llm_session = f"daily_life_memory_batch_{uuid.uuid4().hex[:8]}"
        logger.debug(
            f"{LOG_PREFIX} 开始聊天记忆批处理：会话={batch['session_id']}，消息={len(batch['messages'])}"
        )
        try:
            fact_getter = getattr(self.archive, "get_temporal_facts", None)
            if callable(fact_getter):
                current_facts = await fact_getter(
                    scope=str(batch.get("session_id") or ""),
                    limit=30,
                )
                batch = {
                    **batch,
                    "current_temporal_facts": [
                        item.as_dict() for item in current_facts
                    ],
                }
            payload = await call_pure_json(
                self,
                provider,
                self._build_chat_memory_batch_prompt(batch),
                llm_session,
                primary_provider_id=self.config.memory.provider,
            )
            if not isinstance(payload, dict):
                raise ValueError("模型未返回 JSON 对象")
            saved = await self._save_chat_memory_batch_payload(payload, batch)
            await self.archive.complete_chat_memory_batch(
                batch["id"], saved.id if saved else 0
            )
            mark_changed = getattr(self, "mark_page_status_changed", None)
            if callable(mark_changed):
                try:
                    await mark_changed("chat_memory")
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 聊天记忆面板刷新通知失败：{exc}")
            return True
        except asyncio.CancelledError:
            await self.archive.fail_chat_memory_batch(batch["id"], "任务被取消")
            raise
        except Exception as exc:
            await self.archive.fail_chat_memory_batch(batch["id"], str(exc))
            logger.warning(f"{LOG_PREFIX} 聊天记忆批处理失败：{exc}")
            return False
        finally:
            await self.close_text_session(llm_session)
