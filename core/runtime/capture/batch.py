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
from ..markers import LOG_PREFIX
from .jsonclean import call_pure_json


class ChatMemoryBatchMixin:
    """Persist incoming chat snapshots and distill them in non-overlapping session batches."""

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
        if not message or self._event_has_command_handler(event) or self.event_was_recalled(event, log_skip=True):
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

    async def process_due_chat_memory_batches(self, now: datetime.datetime | None = None) -> int:
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
                and self._chat_memory_elapsed_seconds(str(state.get("last_message_at") or ""), now)
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
                    and self._chat_memory_elapsed_seconds(str(state.get("last_message_at") or ""), now)
                    >= self.config.memory.idle_flush_seconds
                )
        return processed

    def _build_chat_memory_batch_prompt(self, batch: dict[str, Any]) -> str:
        messages = []
        participants: dict[str, dict[str, str]] = {}
        for row in batch["messages"]:
            profile_id = str(row.get("sender_profile_id") or "").strip()
            if profile_id:
                participants[profile_id] = {
                    "profile_id": profile_id,
                    "name": str(row.get("sender_name") or profile_id),
                }
            messages.append(
                {
                    "row_id": row["id"],
                    "message_id": row.get("message_id", ""),
                    "time": row.get("occurred_at", ""),
                    "speaker_profile_id": profile_id,
                    "speaker_name": row.get("sender_name", ""),
                    "text": row.get("message_text", ""),
                    "message_facts": row.get("message_facts", ""),
                    "quote_context": row.get("quote_context", ""),
                }
            )
        source = {
            "session_id": batch["session_id"],
            "participants": list(participants.values()),
            "messages": messages,
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
            "commitments": [
                {
                    "content": "",
                    "kind": "plan",
                    "trigger_date": "",
                    "trigger_time": "",
                    "time_window": "",
                    "people": [],
                    "place": "",
                    "confidence": 0.0,
                    "source_message_ids": [],
                }
            ],
        }
        return (
            "你负责把一个连续聊天批次整理成可长期复用的记忆。只依据输入证据，保持每条信息的说话人归属；"
            "区分稳定事实、偏好、关系认识、生活事件、纠错和未来约定。暂时情绪、寒暄、无证据推断与重复信息不保存。"
            "无法可靠归属的信息不要输出。群聊中的人物必须使用输入给出的 profile_id。"
            "输出一个严格 JSON 对象，不要解释，不要 Markdown。没有长期信息时输出 worth_saving=false，其他数组可为空；"
            "即使没有长期摘要，也要保留证据明确的 commitments。brief 是简短主题，long_summary 是忠于证据的批次摘要。\n"
            f"输出结构：{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}\n"
            f"输入批次：{json.dumps(source, ensure_ascii=False, separators=(',', ':'))}"
        )

    async def _save_batch_commitments(self, payload: dict[str, Any], batch: dict[str, Any]) -> list[CommitmentRecord]:
        saved: list[CommitmentRecord] = []
        messages = batch["messages"]
        message_by_id: dict[str, dict[str, Any]] = {}
        for row in messages:
            message_by_id[str(row["id"])] = row
            if str(row.get("message_id") or "").strip():
                message_by_id[str(row["message_id"])] = row
        for raw in payload.get("commitments", []) if isinstance(payload.get("commitments"), list) else []:
            if not isinstance(raw, dict):
                continue
            commitment = CommitmentRecord.from_value(
                {
                    **raw,
                    "source": "chat_batch",
                    "source_session": batch["session_id"],
                    "source_message": "\n".join(
                        str(message_by_id[item].get("message_text") or "")
                        for item in [str(value) for value in raw.get("source_message_ids", [])]
                        if item in message_by_id
                    )[:1000],
                }
            )
            if not commitment or commitment.confidence < self.config.commitments.min_confidence:
                continue
            saved.append(await self.archive.save_commitment(commitment))
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
                    "sender_name": str(row.get("sender_name") or target.get("name") or profile_id),
                }
            )
            saved.extend(await self._save_memory_targets({"memory_targets": [target]}, meta))
        return saved

    async def _save_chat_memory_batch_payload(
        self, payload: dict[str, Any], batch: dict[str, Any]
    ) -> ChatSummaryRecord | None:
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
            "is_directed": "false",
            "is_quoted": "false",
            "quote_context": "",
            "structured": "",
        }
        commitments = await self._save_batch_commitments(payload, batch)
        if not payload.get("worth_saving"):
            return None
        summary = ChatSummaryRecord.from_value(
            {**payload, "session_id": batch["session_id"], "date": meta["date"], "source": "chat_batch"}
        )
        if not summary:
            if commitments:
                return None
            raise ValueError("模型标记值得保存，但没有给出有效摘要")
        saved = await self.archive.save_chat_summary(summary)
        saved_records = await self._save_experience_payload(payload, meta, saved)
        saved_records["memory_targets"] = await self._save_batch_memory_targets(payload, batch, meta)
        await self._save_chat_memory_preferences(payload, meta, saved_records)
        self._schedule_chat_memory_memos(
            payload,
            meta,
            saved,
            saved_records,
            "\n".join(str(row.get("message_text") or "") for row in rows),
        )
        logger.info(f"{LOG_PREFIX} 已完成聊天记忆批处理 #{saved.id}：{saved.brief}")
        return saved

    async def _process_chat_memory_batch(self, batch: dict[str, Any]) -> bool:
        provider = await self._get_memory_provider()
        if not provider:
            await self.archive.fail_chat_memory_batch(batch["id"], "未找到可用的记忆模型")
            return False
        llm_session = f"daily_life_memory_batch_{uuid.uuid4().hex[:8]}"
        logger.info(
            f"{LOG_PREFIX} 开始聊天记忆批处理：会话={batch['session_id']}，消息={len(batch['messages'])}"
        )
        try:
            payload = await call_pure_json(
                self.composer,
                provider,
                self._build_chat_memory_batch_prompt(batch),
                llm_session,
                primary_provider_id=self.config.memory.provider,
            )
            if not isinstance(payload, dict):
                raise ValueError("模型未返回 JSON 对象")
            saved = await self._save_chat_memory_batch_payload(payload, batch)
            await self.archive.complete_chat_memory_batch(batch["id"], saved.id if saved else 0)
            return True
        except asyncio.CancelledError:
            await self.archive.fail_chat_memory_batch(batch["id"], "任务被取消")
            raise
        except Exception as exc:
            await self.archive.fail_chat_memory_batch(batch["id"], str(exc))
            logger.warning(f"{LOG_PREFIX} 聊天记忆批处理失败：{exc}")
            return False
        finally:
            await self.composer._cleanup_conversation(llm_session)
