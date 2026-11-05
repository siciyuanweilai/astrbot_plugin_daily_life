from __future__ import annotations

from ...relationship import ASSESSMENT_SCHEMA, RELATIONSHIP_RULES
from ...addresses import ADDRESS_UPDATE_SCHEMA, ADDRESS_UPDATE_CONTRACT, settle_batch_addresses
from ..bonding import (
    batch_relationship_context, settle_batch_relationships,
    relationship_only_batch, relationship_batch_prompt,
)

import asyncio
import copy
import datetime
import hashlib
import json
import uuid
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...life.reliability import NonRetryableProviderError
from ...models import ChatSummaryRecord, CommitmentRecord
from ...models.coerce import compact_explanation_text
from ...sources.botidentity import event_bot_id
from ...sources.platforms import parse_unified_origin
from ..context import INTERACTION_MODE_PREDICATE, interaction_fact_is_current
from ..markers import LOG_PREFIX
from ..elapsed import RECEIVED_AT, SENT_AT, local_message_time
from .jsonclean import call_pure_json
from .consolidation import memory_batch_rules
from .episodelink import episode_scope, link_batch_episode

from . import diction as _part_expression_learning_component
from typing import Protocol


class ChatMemoryBatchComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _append_memory_decision_log: Any
    _batch_expression_context: Any
    _bool_payload: Any
    _calibrate_chat_memory_payload: Any
    _chat_execution_context: Any
    _chat_memory_elapsed_seconds: Any
    _chat_memory_stopping: Any
    _chat_memory_wakeup: Any
    _chat_memory_worker_task: Any
    _chinese_text_payload: Any
    _current_role_label: Any
    _event_context_meta: Any
    _event_has_command_handler: Any
    _event_message_component_facts: Any
    _extract_speaker_persona_hint: Any
    _get_memory_provider: Any
    _normalize_batch_expression_learning: Any
    _safe_event_call: Any
    _sanitize_batch_readable_fields: Any
    _save_batch_execution_updates: Any
    _save_batch_expression_learning: Any
    _save_chat_memory_preferences: Any
    _save_experience_payload: Any
    _save_memory_awareness_records: Any
    _save_memory_targets: Any
    _schedule_chat_memory_memos: Any
    _str_payload: Any
    _structured_result_text: Any
    _validate_chat_memory_payload: Any
    apply_commitment_to_current_day: Any
    archive: Any
    close_text_session: Any
    config: Any
    contact_resolver: Any
    direct_media_was_delivered: Any
    event_was_recalled: Any
    format_structured_message_context: Any
    mark_page_status_changed: Any
    note_memory_corrections: Any
    process_due_chat_memory_batches: Any
    schedule_commitment_photo: Any
    schedule_commitment_video: Any
    schedule_proactive_commitment: Any


class ChatMemoryBatchComponent:
    """保存收到的聊天快照，并按互不重叠的会话批次提炼。"""

    def __init__(self, context: ChatMemoryBatchComponentContext) -> None:
        self.context = context

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

    @staticmethod
    def _validate_chat_memory_payload(value: dict[str, Any]) -> dict[str, Any]:
        result = dict(value)
        result["worth_saving"] = result.get("worth_saving") is True
        for key in (
            "commitments",
            "temporal_facts",
            "memory_targets",
            "relationship_updates",
            "experiences",
            "execution_updates",
            "expression_profiles",
            "expression_reviews",
            "memory_corrections",
        ):
            if not isinstance(result.get(key), list):
                result[key] = []
        return result

    def _init_chat_memory_batcher(self) -> None:
        self.context._chat_memory_wakeup = asyncio.Event()
        self.context._chat_memory_worker_task: asyncio.Task | None = None
        self.context._chat_memory_stopping = False

    def _start_chat_memory_batcher(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._ensure_chat_memory_worker()
        if self.context._chat_memory_worker_task is not None:
            self.context._chat_memory_wakeup.set()

    def _ensure_chat_memory_worker(self) -> None:
        if self.context._chat_memory_stopping or not self.context.config.memory.enabled:
            return
        task = self.context._chat_memory_worker_task
        if task is None or task.done():
            self.context._chat_memory_worker_task = asyncio.create_task(
                self._chat_memory_worker(), name="daily-life-chat-memory-batcher"
            )

    async def _shutdown_chat_memory_batcher(self) -> None:
        self.context._chat_memory_stopping = True
        self.context._chat_memory_wakeup.set()
        task = self.context._chat_memory_worker_task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.context._chat_memory_worker_task = None

    async def capture_chat_memory_message(
        self,
        event: Any,
        now: datetime.datetime | None = None,
    ) -> bool:
        if not self.context.config.memory.enabled or event is None:
            return False
        self_checker = getattr(self.context, "_proactive_is_self_message", None)
        if callable(self_checker) and self_checker(event):
            return False
        message = str(getattr(event, "message_str", "") or "").strip()
        if (
            not message
            or self.context._event_has_command_handler(event)
            or self.context.event_was_recalled(event, log_skip=True)
        ):
            return False
        now = now or local_message_time(event, RECEIVED_AT) or life_now()
        sender_name = await self.context.contact_resolver.resolve_event_sender(event)
        if self.context.event_was_recalled(event, log_skip=True):
            return False
        meta = await self.context._event_context_meta(event, sender_name, now)
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
            "message_facts": self.context._event_message_component_facts(event, message),
            "quote_context": meta.get("quote_context", ""),
            "structured_context": meta.get("structured", ""),
            "occurred_at": occurred_at,
        }
        _, inserted = await self.context.archive.enqueue_chat_memory_message(snapshot)
        if inserted:
            self._ensure_chat_memory_worker()
            self.context._chat_memory_wakeup.set()
        return inserted

    async def capture_chat_memory_bot_reply(
        self,
        event: Any,
        now: datetime.datetime | None = None,
    ) -> bool:
        if (
            not self.context.config.memory.enabled
            or event is None
            or getattr(self.context, "contact_resolver", None) is None
            or getattr(self.context, "archive", None) is None
        ):
            return False
        if self.context._event_has_command_handler(event) or self.context.event_was_recalled(
            event, log_skip=True
        ):
            return False
        result_reader = getattr(self.context, "_structured_result_text", None)
        if not callable(result_reader):
            return False
        message, media = result_reader(event)
        message = str(message or "").strip()
        if not message:
            return False
        now = now or local_message_time(event, SENT_AT) or life_now()
        sender_name = await self.context.contact_resolver.resolve_event_sender(event)
        meta = await self.context._event_context_meta(event, sender_name, now)
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
        self_id = event_bot_id(
            event, context=getattr(self.context, "context", None), scope=session_id
        ) or "bot"
        snapshot = {
            "event_key": event_key,
            "session_id": session_id,
            "role": "assistant",
            "message_id": "",
            "sender_profile_id": self_id,
            "sender_name": "我",
            "platform": meta.get("platform", ""),
            "user_id": self_id,
            "group_id": meta.get("group_id", ""),
            "group_name": meta.get("group_name", ""),
            "is_group": meta.get("is_group") == "true",
            "is_directed": False,
            "is_quoted": False,
            "message_text": message,
            "message_facts": f"已发送{media}" if media else "",
            "quote_context": "",
            "structured_context": self.context.format_structured_message_context(
                session_id, limit=6
            ),
            "occurred_at": now.isoformat(timespec="seconds"),
        }
        _, inserted = await self.context.archive.enqueue_chat_memory_message(snapshot)
        if inserted:
            self._ensure_chat_memory_worker()
            self.context._chat_memory_wakeup.set()
        return inserted

    async def capture_proactive_chat_memory_reply(
        self,
        scope: str,
        message: str,
        *,
        media: str = "",
        now: datetime.datetime | None = None,
    ) -> bool:
        """把已成功发送的主动消息纳入同一套语义记忆与承诺提炼。"""

        archive = getattr(self.context, "archive", None)
        enqueue = getattr(archive, "enqueue_chat_memory_message", None)
        if not self.context.config.memory.enabled or not callable(enqueue):
            return False
        scope = str(scope or "").strip()
        message = str(message or "").strip()
        if not scope or not message:
            return False
        now = now or life_now()
        occurred_at = now.isoformat(timespec="seconds")
        identity = json.dumps(
            [scope, occurred_at, message, str(media or "").strip()],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        platform, user_id = parse_unified_origin(scope)
        snapshot = {
            "event_key": "bot:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "session_id": scope,
            "role": "assistant",
            "message_id": "",
            "sender_profile_id": "bot",
            "sender_name": "我",
            "platform": platform,
            # 主动消息的接收方不是这条消息的说话人。
            "user_id": "",
            "group_id": user_id if ":GroupMessage:" in scope else "",
            "group_name": "",
            "is_group": ":GroupMessage:" in scope,
            "is_directed": False,
            "is_quoted": False,
            "message_text": message,
            "message_facts": f"已发送{media}" if media else "",
            "quote_context": "",
            "structured_context": self.context.format_structured_message_context(
                scope, limit=6
            ),
            "occurred_at": occurred_at,
        }
        _, inserted = await enqueue(snapshot)
        if inserted:
            self._ensure_chat_memory_worker()
            self.context._chat_memory_wakeup.set()
        return inserted

    async def _chat_memory_worker(self) -> None:
        poll = max(2, int(self.context.config.memory.worker_poll_seconds))
        while not self.context._chat_memory_stopping:
            try:
                await asyncio.wait_for(self.context._chat_memory_wakeup.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass
            self.context._chat_memory_wakeup.clear()
            if self.context._chat_memory_stopping:
                return
            try:
                await self.context.process_due_chat_memory_batches()
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
        if not self.context.config.memory.enabled:
            return 0
        now = now or life_now()
        processed = 0
        for state in await self.context.archive.list_chat_memory_sessions():
            pending_count = int(state.get("pending_count") or 0)
            idle_min_messages = self.context.config.memory.idle_flush_min_messages
            pending_user_count = int(state.get("pending_user_count") or 0)
            pending_assistant_count = int(state.get("pending_assistant_count") or 0)
            if relationship_only_batch(state):
                idle_min_messages = 1
            if pending_user_count > 0 and pending_assistant_count > 0:
                # 一轮完整问答已经具备双向语义。即使消息数量低于普通
                # 沉淀门槛，也应在会话安静后收束，避免遗漏我主动许下的承诺。
                idle_min_messages = min(idle_min_messages, 2)
            elif pending_assistant_count > 0 and pending_user_count == 0:
                # 私聊回访、闲时回复和承诺履约可能没有同批次用户消息，
                # 但其中仍可能产生新的未来联系承诺，应在安静后独立收束。
                idle_min_messages = 1
            threshold = (
                self.context.config.memory.group_message_threshold
                if bool(state.get("is_group"))
                else self.context.config.memory.private_message_threshold
            )
            idle_due = (
                pending_count >= idle_min_messages
                and self.context._chat_memory_elapsed_seconds(
                    str(state.get("last_message_at") or ""), now
                )
                >= self.context.config.memory.idle_flush_seconds
            )
            if pending_count < threshold and not idle_due:
                continue
            while pending_count >= threshold or idle_due:
                batch = await self.context.archive.begin_chat_memory_batch(
                    str(state["session_id"]),
                    max_messages=self.context.config.memory.max_batch_messages,
                    max_chars=self.context.config.memory.max_batch_chars,
                )
                if not batch:
                    break
                if not await self._process_chat_memory_batch(batch):
                    break
                processed += 1
                pending_count -= len(batch["messages"])
                idle_due = (
                    pending_count >= idle_min_messages
                    and self.context._chat_memory_elapsed_seconds(
                        str(state.get("last_message_at") or ""), now
                    )
                    >= self.context.config.memory.idle_flush_seconds
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
                    "message_id": str(row.get("message_id") or row["id"]),
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
            # 较稳定的已知资料先于本批新消息，延长同会话可复用的输入前缀。
            "current_relationships": batch.get("current_relationships", []),
            "current_expression_profiles": batch.get("current_expression_profiles", []),
            "expression_persona": batch.get("expression_persona", ""),
            "current_temporal_facts": batch.get("current_temporal_facts", []),
            "correctable_memories": batch.get("correctable_memories", []),
            "current_day_timeline": batch.get("current_day_timeline", []),
            "open_commitments": batch.get("open_commitments", []),
            "execution_candidates": batch.get("execution_candidates", []),
            "recent_reply_effects": batch.get("recent_reply_effects", []),
            "participants": list(participants.values()),
            "messages": messages,
        }
        schema = {
            "relationship_updates": [ASSESSMENT_SCHEMA],
            "address_updates": [ADDRESS_UPDATE_SCHEMA],
            "episode_link": {"memory_id": 0, "previous_quote": "已有经历原文",
                             "source_message_id": "本次用户消息编号", "evidence": "用户整条原话"},
            "memory_corrections": [{"memory_id": 0, "correction": "纠正后的完整事实",
                                    "source_message_id": "本批次用户消息编号", "evidence": "用户整条原话"}],
            "execution_updates": [
                {
                    "action_id": "输入候选中的编号，无对应动作时留空",
                    "commitment_ids": [],
                    "new_commitment_indexes": [],
                    "status": "in_progress|done|cancelled|expired",
                    "source_message_id": "证明进展的本批次真实消息编号",
                    "evidence": "对应消息的完整原文",
                }
            ],
            "worth_saving": True,
            "brief": "",
            "long_summary": "",
            "people": [],
            "memory_targets": [
                {
                    "profile_id": "",
                    "note": "",
                    "points": [],
                    "subjective_tags": [],
                    "relationship_story": "",
                }
            ],
            "preferences": [],
            "expression_profiles": [
                {
                    "profile_id": "表达偏好所属用户的输入编号",
                    "id": 0,
                    "breadth": "voice|relationship|situation",
                    "applicability": "语义适用边界，不列触发关键词",
                    "persona_fit": "这种表达方式为何符合自己的性格",
                    "label": "稳定适用场景；相同场景复用已有 label",
                    "tone": "角色面对这位用户时适用的语气",
                    "habits": [],
                    "avoid": [],
                    "evidence": "用户反馈及其适用边界的简短依据",
                    "confidence": 0.0,
                    "basis": "explicit|repeated|experience",
                    "source_message_ids": [],
                    "reply_message_ids": [],
                    "reply_effect_ids": [],
                }
            ],
            "expression_reviews": [
                {
                    "profile_id": "反馈用户的输入编号",
                    "reply_message_id": "被反馈的 assistant 消息编号",
                    "source_message_ids": [],
                    "passed": True,
                    "risk": "",
                    "suggestion": "",
                    "reason": "后续用户反馈证明的表达效果",
                }
            ],
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
                    "execution_mode": "immediate|future|standing",
                    "trigger_date": "",
                    "trigger_time": "",
                    "time_window": "",
                    "owner": "当前角色|说话人|共同|未定",
                    "media_kind": "none|photo|video",
                    "follow_up": {
                        "action": "none|contact_person|remind_person",
                        "message_goal": "",
                        "execute_at": "YYYY-MM-DD HH:MM 或空字符串",
                        "condition": "需要满足的客观条件或空字符串",
                        "check_after_minutes": 0,
                    },
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
                    "epistemic_status": "reported 明确陈述|inferred 推测|uncertain 不确定",
                    "evidence_quote": "来源消息中的逐字证据片段",
                    "evidence_signal": "reinforce|dispute",
                    "evidence_summary": "证据的简短中文概括",
                }
            ],
        }
        if not source["correctable_memories"]:
            schema.pop("episode_link")
            schema.pop("memory_corrections")
        if source["scope"]["type"] != "group":
            schema.pop("group_environment")
        return (
            memory_batch_rules(source) + "\n" + RELATIONSHIP_RULES + "\n" + ADDRESS_UPDATE_CONTRACT + "\n"
            f"输出结构：{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}\n"
            f"输入批次：{json.dumps(source, ensure_ascii=False, separators=(',', ':'))}"
        )

    def _normalize_chat_memory_batch_payload(
        self,
        payload: dict[str, Any],
        batch: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = self.context._sanitize_batch_readable_fields(dict(payload), batch)
        worth_saving = self.context._bool_payload(normalized.get("worth_saving"))
        brief = self.context._chinese_text_payload(normalized.get("brief"))
        long_summary = self.context._chinese_text_payload(normalized.get("long_summary"))
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
            visibility[field] = self.context._chinese_text_payload(visibility.get(field))
        visibility["reason"] = compact_explanation_text(visibility.get("reason"))
        visibility["is_directed_at_bot"] = any(
            self.context._bool_payload(row.get("is_directed"))
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
            environment[field] = self.context._chinese_text_payload(environment.get(field))
        if not any(
            self.context._bool_payload(row.get("is_group"))
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
            decision[field] = self.context._chinese_text_payload(decision.get(field))
        decision["reason"] = compact_explanation_text(decision.get("reason"))
        normalized["action_decision"] = decision
        self.context._normalize_batch_expression_learning(normalized, batch)
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
        payload["_saved_commitments"] = {}
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
        for raw_index, raw in enumerate(
            payload.get("commitments", [])
            if isinstance(payload.get("commitments"), list)
            else []
        ):
            if not isinstance(raw, dict):
                continue
            if str(raw.get("execution_mode") or "future").strip().lower() != "future":
                continue
            if str(raw.get("media_kind") or "").strip().lower() in {
                "photo",
                "video",
            } and str(raw.get("execution_mode") or "").strip().lower() != "future":
                continue
            source_rows: list[dict[str, Any]] = []
            seen_row_ids: set[int] = set()
            for value in raw.get("source_message_ids", []):
                source_row = message_by_id.get(str(value).strip())
                if source_row is None:
                    continue
                row_id = int(source_row.get("id") or 0)
                if row_id in seen_row_ids:
                    continue
                seen_row_ids.add(row_id)
                source_rows.append(source_row)
            source_message_ids = [
                str(row.get("message_id") or row.get("id") or "").strip()
                for row in source_rows
            ]
            media_kind = str(raw.get("media_kind") or "").strip().lower()
            delivery_check = getattr(self.context, "direct_media_was_delivered", None)
            if media_kind in {"photo", "video"} and callable(delivery_check):
                if await delivery_check(
                    batch["session_id"], media_kind, source_message_ids
                ):
                    continue
            commitment = CommitmentRecord.from_value(
                {
                    **raw,
                    "source": "chat_batch",
                    "source_session": batch["session_id"],
                    "source_message_id": (
                        source_message_ids[0] if len(source_message_ids) == 1 else ""
                    ),
                    "source_message": "\n".join(
                        str(row.get("message_text") or "") for row in source_rows
                    )[:1000],
                }
            )
            if (
                not commitment
                or commitment.confidence < self.context.config.commitments.min_confidence
            ):
                continue
            stored = await self.context.archive.save_commitment(commitment)
            payload["_saved_commitments"][str(raw_index)] = stored.as_dict()
            if media_kind in {"photo", "video"} and callable(delivery_check):
                if await delivery_check(
                    batch["session_id"], media_kind, source_message_ids
                ):
                    await self.context.archive.set_commitment_status(
                        stored.id, "done", life_now().isoformat(timespec="seconds")
                    )
                    continue
            apply_to_day = getattr(self.context, "apply_commitment_to_current_day", None)
            if callable(apply_to_day):
                try:
                    await apply_to_day(
                        stored,
                        now=observed_at,
                        owner_hint=str(raw.get("owner") or "").strip(),
                    )
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 批次承诺合并到当天日程失败：{exc}")
            get_commitment = getattr(self.context.archive, "get_commitment", None)
            if callable(get_commitment):
                final_commitment = await get_commitment(stored.id)
                if final_commitment is not None:
                    stored = final_commitment
            saved.append(stored)
            scheduler = getattr(self.context, "schedule_proactive_commitment", None)
            if callable(scheduler):
                try:
                    await scheduler(
                        stored,
                        owner=stored.owner,
                        follow_up=stored.follow_up,
                        observed_at=observed_at,
                    )
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 主动承诺任务登记失败：{exc}")
            photo_scheduler = getattr(self.context, "schedule_commitment_photo", None)
            if callable(photo_scheduler):
                try:
                    await photo_scheduler(
                        stored,
                        owner=str(raw.get("owner") or "").strip(),
                        observed_at=observed_at,
                    )
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 承诺拍照任务登记失败：{exc}")
            video_scheduler = getattr(self.context, "schedule_commitment_video", None)
            if callable(video_scheduler):
                try:
                    await video_scheduler(
                        stored,
                        owner=str(raw.get("owner") or "").strip(),
                        observed_at=observed_at,
                    )
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 承诺拍视频任务登记失败：{exc}")
            domain_settings = getattr(self.context.config, "domains", None)
            save_action_item = getattr(
                self.context.archive, "save_conversation_action_item", None
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
                        "status": {
                            "active": "open",
                            "scheduled": "pending",
                            "pending": "pending",
                            "done": "done",
                            "cancelled": "cancelled",
                            "expired": "expired",
                            "delivery_failed": "failed",
                        }.get(stored.status, "open"),
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

        payload["_evidenced_fact_ids"] = []
        writer = getattr(self.context.archive, "write_temporal_fact", None)
        current_reader = getattr(self.context.archive, "get_current_temporal_fact", None)
        evidence_saver = getattr(self.context.archive, "add_fact_evidence_signal", None)
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
            source_message_id = str(
                source_row.get("message_id") or source_row.get("id") or ""
            ).strip()
            observed_at = str(source_row.get("occurred_at") or "").strip()
            try:
                confidence = max(0.0, min(float(raw.get("confidence") or 0.0), 1.0))
            except (TypeError, ValueError):
                confidence = 0.0
            subject = str(raw.get("subject") or "").strip()[:180]
            predicate = str(raw.get("predicate") or "").strip()[:120]
            if not subject or not predicate:
                continue
            if predicate == INTERACTION_MODE_PREDICATE:
                interaction_audited = payload.get("_interaction_audited")
                # 互动模式涉及安全边界：校准缺失或失败时，绝不能把传输元数据写成
                # 已持久化的真实世界距离事实。
                if interaction_audited is not True:
                    continue
                participant_ids = {
                    str(row.get("sender_profile_id") or "").strip()
                    for row in batch.get("messages", [])
                    if isinstance(row, dict)
                    and str(row.get("role") or "").strip().lower() == "user"
                    and str(row.get("sender_profile_id") or "").strip()
                }
                object_value = raw.get("object_value")
                mode = (
                    str(object_value.get("mode") or "").strip().lower()
                    if isinstance(object_value, dict)
                    else ""
                )
                if subject not in participant_ids or confidence < 0.7:
                    continue
                if operation in {"ADD", "UPDATE"}:
                    if mode not in {"co_present", "remote"}:
                        continue
                    current = (
                        await current_reader(
                            scope,
                            subject,
                            INTERACTION_MODE_PREDICATE,
                        )
                        if callable(current_reader)
                        else None
                    )
                    fact = await writer(
                        "UPDATE" if current is not None else "ADD",
                        {
                            "scope": scope,
                            "subject": subject,
                            "predicate": INTERACTION_MODE_PREDICATE,
                            "object_value": {"mode": mode},
                            "observed_at": observed_at,
                            "valid_from": observed_at,
                            "confidence": confidence,
                            "source": (
                                "chat_batch_audited"
                                if interaction_audited is True
                                else "chat_batch"
                            ),
                            "source_type": "chat_message",
                            "source_id": source_message_id,
                            "provenance": {
                                "session_id": scope,
                                "message_id": source_message_id,
                                "batch_id": batch.get("id"),
                            },
                        },
                    )
                    if fact is not None:
                        saved.append(fact)
                        if callable(evidence_saver) and int(fact.id or 0) > 0:
                            await evidence_saver(
                                {
                                    "fact_id": int(fact.id),
                                    "signal": "reinforce",
                                    "weight": 1.0,
                                    "confidence": confidence,
                                    "summary": self.context._chinese_text_payload(
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
                    continue
                if operation == "NONE":
                    continue
            epistemic = str(raw.get("epistemic_status") or "uncertain")
            quote = str(raw.get("evidence_quote") or "").strip()[:1200]
            source_text = str(source_row.get("message_text") or "")
            if epistemic not in {"reported", "inferred", "uncertain"}:
                epistemic = "uncertain"
            if epistemic == "reported" and (
                source_row.get("role") != "user" or source_row.get("is_quoted")
                or not quote or quote not in source_text
            ):
                epistemic = "uncertain"
            if epistemic != "reported":
                confidence = min(confidence, 0.5 if epistemic == "inferred" else 0.4)
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
                            "epistemic_status": epistemic,
                            "evidence_quote": quote if quote in source_text else "",
                        },
                    },
                )
            except ValueError:
                continue
            if fact is None:
                continue
            saved.append(fact)
            if epistemic == "reported" and getattr(fact, "status", "") != "uncertain":
                # 重复陈述可沿用既有事实版本，但仍保留本批摘要与它的联系。
                payload.setdefault("_evidenced_fact_ids", []).append(int(fact.id))
            signal = str(raw.get("evidence_signal") or "").strip().lower()
            if (
                operation != "NONE"
                and getattr(fact, "status", "") == "active"
                and signal in {"reinforce", "dispute"}
                and callable(evidence_saver)
                and int(getattr(fact, "id", 0) or 0) > 0
                and str(getattr(fact, "source", "") or "") == "chat_batch"
                and str(getattr(fact, "source_id", "") or "") == source_message_id
            ):
                await evidence_saver(
                    {
                        "fact_id": int(fact.id),
                        "signal": signal,
                        "weight": 1.0,
                        "confidence": confidence,
                        "summary": self.context._chinese_text_payload(
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
        assistant_profiles: set[str] = set()
        for row in batch["messages"]:
            profile_id = str(row.get("sender_profile_id") or "").strip()
            role = str(row.get("role") or "user").strip().lower()
            if profile_id and role == "assistant":
                assistant_profiles.add(profile_id)
            elif profile_id and role == "user" and not row.get("is_quoted"):
                rows_by_profile[profile_id] = row
        for profile_id in assistant_profiles:
            rows_by_profile.pop(profile_id, None)
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
                        row.get("sender_name") or profile_id
                    ),
                }
            )
            saved.extend(
                await self.context._save_memory_targets({"memory_targets": [target]}, meta)
            )
        return saved

    async def _save_chat_memory_batch_payload(
        self, payload: dict[str, Any], batch: dict[str, Any]
    ) -> ChatSummaryRecord | None:
        payload = self._normalize_chat_memory_batch_payload(payload, batch)
        rows = batch["messages"]
        last = rows[-1]
        user_rows = [
            row
            for row in rows
            if str(row.get("role") or "user").strip().lower() == "user"
        ]
        # 用户侧的观察和关系归属取最近一条用户消息，不能取批次末尾的 bot 回复。
        source_row = user_rows[-1] if user_rows else last
        meta = {
            "session_id": batch["session_id"],
            "message_id": str(source_row.get("message_id") or source_row["id"]),
            "platform": str(source_row.get("platform") or ""),
            "user_id": str(source_row.get("user_id") or "") if user_rows else "",
            "sender_profile_id": str(source_row.get("sender_profile_id") or "") if user_rows else "",
            "sender_name": str(source_row.get("sender_name") or "") if user_rows else "",
            "group_id": str(source_row.get("group_id") or ""),
            "group_name": str(source_row.get("group_name") or ""),
            "date": str(last.get("occurred_at") or "")[:10],
            "is_group": "true" if source_row.get("is_group") else "false",
            "is_directed": "true" if source_row.get("is_directed") else "false",
            "is_quoted": "true" if source_row.get("is_quoted") else "false",
            "quote_context": str(source_row.get("quote_context") or ""),
            "structured": str(source_row.get("structured_context") or ""),
        }
        relationship = None
        profile_id = meta["sender_profile_id"]
        relationship_getter = getattr(self.context.archive, "get_relationship", None)
        if profile_id and callable(relationship_getter):
            try:
                relationship = await relationship_getter(profile_id)
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 读取聊天对象关系档案失败：{exc}")
        persona_hint = ""
        hint_getter = getattr(self.context, "_extract_speaker_persona_hint", None)
        if user_rows and callable(hint_getter):
            try:
                persona_hint = self.context._str_payload(
                    await hint_getter(
                        meta["sender_name"],
                        relationship=relationship,
                    )
                )
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 提取聊天对象人设线索失败：{exc}")
        role_getter = getattr(self.context, "_current_role_label", None)
        if callable(role_getter):
            try:
                meta["current_role_label"] = (
                    self.context._str_payload(await role_getter()) or "我"
                )
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 读取当前角色称呼失败：{exc}")
                meta["current_role_label"] = "我"
        else:
            meta["current_role_label"] = "我"
        interaction_mode = ""
        try:
            interaction_now = datetime.datetime.fromisoformat(
                str(last.get("occurred_at") or "")
            )
        except (TypeError, ValueError):
            interaction_now = life_now()
        current_interaction_facts: list[dict[str, Any]] = []
        for current_fact in list(batch.get("current_temporal_facts") or []):
            predicate = str(
                current_fact.get("predicate")
                if isinstance(current_fact, dict)
                else getattr(current_fact, "predicate", "")
            ).strip()
            if (
                predicate != INTERACTION_MODE_PREDICATE
                or not interaction_fact_is_current(
                    current_fact,
                    now=interaction_now,
                )
            ):
                continue
            fact_data = (
                dict(current_fact)
                if isinstance(current_fact, dict)
                else current_fact.as_dict()
            )
            current_interaction_facts.append(fact_data)
            object_value = fact_data.get("object_value")
            candidate = (
                str(object_value.get("mode") or "").strip().lower()
                if isinstance(object_value, dict)
                else ""
            )
            if candidate in {"co_present", "remote"} and not interaction_mode:
                interaction_mode = candidate
        meta["interaction_mode_label"] = {
            "co_present": "同处现场",
            "remote": "远程交流",
        }.get(interaction_mode, "未知")
        meta["interaction_messages_json"] = json.dumps(
            [
                {
                    "message_id": str(row.get("message_id") or row.get("id") or ""),
                    "occurred_at": str(row.get("occurred_at") or ""),
                    "role": str(row.get("role") or ""),
                    "sender_profile_id": str(row.get("sender_profile_id") or ""),
                    "content": str(row.get("message_text") or ""),
                }
                for row in rows
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        meta["current_interaction_facts_json"] = json.dumps(
            current_interaction_facts,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        if any(
            isinstance(item, dict)
            and str(item.get("predicate") or "").strip() == INTERACTION_MODE_PREDICATE
            for item in list(payload.get("temporal_facts") or [])
        ):
            payload = dict(payload)
            payload["_interaction_audited"] = False
        address_updates = copy.deepcopy(payload.get("address_updates", []))
        payload = await self.context._calibrate_chat_memory_payload(
            payload,
            meta,
            persona_hint,
        )
        payload = self._normalize_chat_memory_batch_payload(payload, batch)
        payload["address_updates"] = address_updates
        commitments = await self._save_batch_commitments(payload, batch)
        await self.context._save_batch_execution_updates(payload, batch)
        temporal_facts = await self._save_batch_temporal_facts(payload, batch)
        correction_notifier = getattr(self.context, "note_memory_corrections", None)
        if callable(correction_notifier) and isinstance(payload.get("memory_corrections"), list):
            await correction_notifier(payload.get("memory_corrections"), batch)
        if user_rows:
            await self.context._save_memory_awareness_records(payload, meta)
        try:
            observed_at = datetime.datetime.fromisoformat(
                str(last.get("occurred_at") or "")
            )
        except (TypeError, ValueError):
            observed_at = life_now()
        if user_rows:
            await self.context._append_memory_decision_log(payload, meta, observed_at)
        await self.context._save_batch_expression_learning(payload, batch, meta)
        await settle_batch_relationships(self.context.archive, payload, batch)
        await settle_batch_addresses(self.context.archive, payload, batch)
        if not payload.get("worth_saving"):
            return None
        user_names = {
            str(row.get("sender_profile_id") or "").strip(): str(
                row.get("sender_name") or ""
            ).strip()
            for row in user_rows
            if str(row.get("sender_profile_id") or "").strip()
        }
        assistant_profiles = {
            str(row.get("sender_profile_id") or "").strip()
            for row in rows
            if str(row.get("role") or "user").strip().lower() == "assistant"
            and str(row.get("sender_profile_id") or "").strip()
        }
        summary_people = []
        raw_people = payload.get("people")
        for person in raw_people if isinstance(raw_people, list) else []:
            if not isinstance(person, dict):
                summary_people.append(person)
                continue
            person_id = str(person.get("profile_id") or "").strip()
            if person_id in assistant_profiles:
                summary_people.append("我")
            elif person_id in user_names:
                summary_people.append(user_names[person_id])
            else:
                summary_people.append(person)
        summary = ChatSummaryRecord.from_value(
            {
                **payload,
                "people": summary_people,
                "session_id": batch["session_id"],
                "date": meta["date"],
                "source": "chat_batch",
            }
        )
        if not summary:
            if commitments or temporal_facts:
                return None
            raise ValueError("模型标记值得保存，但没有给出有效摘要")
        saved = await self.context.archive.save_chat_summary(summary)
        saved_records = await self.context._save_experience_payload(payload, meta, saved)
        source_saver = getattr(self.context.archive, "save_memory_sources", None)
        if callable(source_saver):
            await source_saver(saved.id, rows, fact_ids=payload.get("_evidenced_fact_ids", []))
        if callable(getattr(self.context.archive, "link_memory_episode", None)):
            await link_batch_episode(self.context.archive, payload, batch, saved.id)
        saved_records["memory_targets"] = await self._save_batch_memory_targets(
            payload, batch, meta
        )
        await self.context._save_chat_memory_preferences(payload, meta, saved_records)
        self.context._schedule_chat_memory_memos(
            payload,
            meta,
            saved,
            saved_records,
            "\n".join(str(row.get("message_text") or "") for row in rows),
        )
        logger.debug(
            f"{LOG_PREFIX} 聊天记忆批处理完成：批次={saved.id}；"
            f"记录={len(saved_records)}"
        )
        return saved

    async def _process_chat_memory_batch(self, batch: dict[str, Any]) -> bool:
        provider = await self.context._get_memory_provider()
        if not provider:
            await self.context.archive.fail_chat_memory_batch(
                batch["id"], "未找到可用的记忆模型"
            )
            return False
        llm_session = f"daily_life_memory_batch_{uuid.uuid4().hex[:8]}"
        logger.debug(
            f"{LOG_PREFIX} 开始聊天记忆批处理：会话={batch['session_id']}，消息={len(batch['messages'])}"
        )
        try:
            if relationship_only_batch(batch):
                if any(row.get("role") == "user" for row in batch["messages"]):
                    batch["current_relationships"] = await batch_relationship_context(self.context.archive, batch)
                    payload = await call_pure_json(
                        self.context, provider, relationship_batch_prompt(batch), llm_session,
                        primary_provider_id=self.context.config.memory.provider, strict=True,
                        validator=self.context._validate_chat_memory_payload,
                        fallback={"relationship_updates": []},
                    )
                    await settle_batch_relationships(self.context.archive, payload, batch)
                await self.context.archive.complete_chat_memory_batch(batch["id"], 0)
                return True
            fact_getter = getattr(self.context.archive, "get_temporal_facts", None)
            if callable(fact_getter):
                current_facts = await fact_getter(
                    scope=str(batch.get("session_id") or ""),
                    limit=30,
                )
                try:
                    fact_now = datetime.datetime.fromisoformat(
                        str(batch["messages"][-1].get("occurred_at") or "")
                    )
                except (IndexError, TypeError, ValueError):
                    fact_now = life_now()
                current_facts = [
                    item
                    for item in current_facts
                    if str(getattr(item, "predicate", "") or "").strip()
                    != INTERACTION_MODE_PREDICATE
                    or interaction_fact_is_current(item, now=fact_now)
                ]
                batch = {
                    **batch,
                    "current_temporal_facts": [
                        item.as_dict() for item in current_facts
                    ],
                }
            try:
                timeline_date = str(batch["messages"][-1].get("occurred_at") or "")[:10]
            except (IndexError, TypeError):
                timeline_date = ""
            if timeline_date:
                current_day = await self.context.archive.get_day(timeline_date)
                if current_day is not None:
                    batch = {
                        **batch,
                        "current_day_timeline": [
                            item.as_dict() for item in current_day.timeline
                        ],
                    }
            batch = {**batch, **await self.context._chat_execution_context(batch)}
            batch = {**batch, **await self.context._batch_expression_context(batch)}
            batch["current_relationships"] = await batch_relationship_context(self.context.archive, batch)
            memory_reader = getattr(self.context.archive, "list_recent_long_term_memories", None)
            if callable(memory_reader):
                scope = episode_scope(batch)
                recent_memories = await memory_reader(scopes=[scope], limit=12)
                batch = {**batch, "correctable_memories": [
                    {"id": item.id, "title": item.title, "content": item.content}
                    for item in recent_memories if item.scope == scope
                ]}
            payload = await call_pure_json(
                self.context,
                provider,
                self._build_chat_memory_batch_prompt(batch),
                llm_session,
                primary_provider_id=self.context.config.memory.provider,
                strict=True,
                validator=self.context._validate_chat_memory_payload,
                fallback={
                    "worth_saving": False,
                    "commitments": [],
                    "temporal_facts": [],
                    "memory_targets": [],
                    "experiences": [],
                },
            )
            if not isinstance(payload, dict):
                raise ValueError("模型未返回 JSON 对象")
            saved = await self._save_chat_memory_batch_payload(payload, batch)
            await self.context.archive.complete_chat_memory_batch(
                batch["id"], saved.id if saved else 0
            )
            mark_changed = getattr(self.context, "mark_page_status_changed", None)
            if callable(mark_changed):
                try:
                    await mark_changed("chat_memory")
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 聊天记忆面板刷新通知失败：{exc}")
            return True
        except asyncio.CancelledError:
            await self.context.archive.fail_chat_memory_batch(
                batch["id"], "任务被取消，等待恢复", retry_after_seconds=0
            )
            raise
        except NonRetryableProviderError as exc:
            await self.context.archive.fail_chat_memory_batch(
                batch["id"],
                str(exc),
                permanent=True,
                max_attempts=self.context.config.memory.batch_max_attempts,
            )
            logger.warning(f"{LOG_PREFIX} 聊天记忆批处理进入死信")
            return False
        except Exception as exc:
            attempts = int(batch.get("attempt_count") or 1)
            base = max(1, int(self.context.config.memory.batch_retry_base_seconds))
            delay = min(3600, base * (2 ** max(0, attempts - 1)))
            await self.context.archive.fail_chat_memory_batch(
                batch["id"],
                str(exc),
                retry_after_seconds=delay,
                max_attempts=self.context.config.memory.batch_max_attempts,
            )
            logger.warning(f"{LOG_PREFIX} 聊天记忆批处理失败：{exc}")
            return False
        finally:
            await self.context.close_text_session(llm_session)

    @staticmethod
    def _batch_expression_scope(batch: dict[str, _part_expression_learning_component.Any]) -> str:
        return _part_expression_learning_component.ExpressionLearningComponent._batch_expression_scope(batch)

