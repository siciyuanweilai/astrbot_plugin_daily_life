import datetime
import uuid
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...models import ChatSummaryRecord
from ..markers import LOG_PREFIX
from .jsonclean import call_pure_json


class ImprintHarvestMixin:
    async def _get_memory_provider(self):
        provider_id = self.config.memory.provider
        return await self.composer._get_provider(provider_id)

    def _chat_memory_message_from_event(self, event: Any) -> str:
        message = str(getattr(event, "message_str", "") or "").strip()
        if (
            not message
            or self._event_has_command_handler(event)
            or self.event_was_recalled(event, log_skip=True)
            or len(message) < self.config.memory.min_message_length
        ):
            return ""
        return message

    async def _prepare_chat_memory_capture(
        self,
        event: Any,
        *,
        message: str,
        now: datetime.datetime,
        sender_name: str,
        recalled,
    ) -> dict[str, Any] | None:
        provider = await self._get_memory_provider()
        if not provider:
            return None
        session_id = f"daily_life_memory_{uuid.uuid4().hex[:8]}"
        context_meta = await self._event_context_meta(event, sender_name, now)
        day = await self.archive.get_day(context_meta["date"])
        current_state = day.state.as_dict() if day and day.state else {}
        speaker_profile = await self.archive.get_relationship(context_meta["sender_profile_id"])
        persona_hint = await self._extract_speaker_persona_hint(
            sender_name,
            event=event,
            relationship=speaker_profile,
        )
        current_role_label = await self._current_role_label(event)
        context_meta["current_role_label"] = current_role_label
        if recalled():
            return None
        prompt = self._build_chat_memory_prompt(
            message,
            sender_name,
            now,
            context_meta,
            current_state=current_state,
            speaker_profile=speaker_profile,
            persona_hint=persona_hint,
            message_facts=self._event_message_component_facts(event, message),
            current_role_label=current_role_label,
        )
        return {
            "event": event,
            "message": message,
            "now": now,
            "sender_name": sender_name,
            "provider": provider,
            "session_id": session_id,
            "context_meta": context_meta,
            "speaker_profile": speaker_profile,
            "persona_hint": persona_hint,
            "prompt": prompt,
        }

    async def _call_chat_memory_payload(self, capture: dict[str, Any]) -> dict[str, Any] | None:
        payload = await call_pure_json(
            self.composer,
            capture["provider"],
            capture["prompt"],
            capture["session_id"],
            primary_provider_id=self.config.memory.provider,
        )
        return payload if isinstance(payload, dict) else None

    async def _save_not_worth_chat_memory_payload(
        self,
        payload: dict[str, Any],
        context_meta: dict[str, str],
        persona_hint: str,
        recalled,
    ) -> None:
        await self._save_subjective_impression(payload, context_meta, persona_hint=persona_hint)
        if not recalled():
            await self._save_experience_payload(payload, context_meta)

    async def _save_speaker_relationship_memory(
        self,
        payload: dict[str, Any],
        context_meta: dict[str, str],
        saved: ChatSummaryRecord,
        sender_name: str,
        persona_hint: str,
        saved_records: dict[str, list[Any]],
        recalled,
    ) -> bool:
        profile_id = context_meta["sender_profile_id"]
        platform, user_id = context_meta["platform"], context_meta["user_id"]
        impression_data = self._subjective_impression_data(payload)
        await self.archive.touch_relationship(
            profile_id,
            name=sender_name,
            note=impression_data["note"] or saved.brief,
            date_str=context_meta["date"],
            source="chat_memory",
            platform=platform,
            user_id=user_id,
            alias=sender_name,
            persona_hint=persona_hint,
            subjective_name=impression_data["subjective_name"],
            subjective_tags=impression_data["subjective_tags"],
            relationship_story=impression_data["relationship_story"],
            **self._relationship_contact_payload(context_meta),
        )
        if recalled():
            return False
        saved_records["speaker_relationship"] = [
            {
                "name": sender_name,
                "subjective_name": impression_data["subjective_name"],
                "subjective_tags": impression_data["subjective_tags"],
                "relationship_story": impression_data["relationship_story"],
                "note": impression_data["note"] or saved.brief,
            }
        ]
        points = payload.get("relationship_points", []) if isinstance(payload.get("relationship_points"), list) else []
        for point in points:
            text_point = str(point or "").strip()
            if text_point:
                await self.archive.add_relationship_point(
                    profile_id,
                    text_point,
                    date_str=context_meta["date"],
                    source="chat_memory",
                )
                await self._save_experience_evidence(
                    "relationship",
                    profile_id,
                    text_point,
                    context_meta,
                    evidence_type="observation",
                    source_table="relationship_points",
                )
                await self._save_long_term_memory(
                    meta=context_meta,
                    category="relationship",
                    title=sender_name or profile_id,
                    content=text_point,
                    source_table="relationship_points",
                    source_id=profile_id,
                    confidence=1.0,
                    weight=1.2,
                )
            if recalled():
                return False
        return True

    async def _save_chat_memory_preferences(
        self,
        payload: dict[str, Any],
        context_meta: dict[str, str],
        saved_records: dict[str, list[Any]],
    ) -> None:
        saved_preferences = await self.composer.learn_preferences_from_payload(
            payload,
            date_str=context_meta["date"],
            source="chat_memory",
        )
        saved_records.setdefault("preferences", []).extend(list(saved_preferences or []))
        for preference in saved_preferences or []:
            await self._save_long_term_memory(
                meta=context_meta,
                category=f"preference:{preference.category}",
                title=preference.category,
                content=preference.content,
                source_table="preferences",
                source_id=str(preference.id),
                confidence=1.0,
                weight=preference.weight,
            )

    def _schedule_chat_memory_memos(
        self,
        payload: dict[str, Any],
        context_meta: dict[str, str],
        saved: ChatSummaryRecord,
        saved_records: dict[str, list[Any]],
        message: str,
    ) -> None:
        memos_items = self._memos_items_from_payload(payload, saved_summary=saved, saved_records=saved_records)
        self.schedule_memos_selected_items(
            context_meta,
            memos_items,
            reason="同步已确认的聊天长期记忆、关系画像、稳定偏好、表达方式和生活事件。",
            user_message=message,
            marker=str(saved.id),
        )

    async def _save_chat_memory_payload(self, payload: dict[str, Any], capture: dict[str, Any], recalled) -> ChatSummaryRecord | None:
        context_meta = capture["context_meta"]
        sender_name = capture["sender_name"]
        persona_hint = capture["persona_hint"]
        message = capture["message"]
        await self._save_memory_awareness_records(payload, context_meta)
        if recalled():
            return None
        await self._append_memory_decision_log(payload, context_meta, capture["now"])
        if recalled():
            return None
        if not payload.get("worth_saving"):
            await self._save_not_worth_chat_memory_payload(payload, context_meta, persona_hint, recalled)
            return None
        summary = ChatSummaryRecord.from_value(
            {
                **payload,
                "session_id": context_meta["session_id"],
                "date": context_meta["date"],
                "source": "chat",
            }
        )
        if not summary:
            return None
        if sender_name and sender_name not in summary.people:
            summary.people.insert(0, sender_name)
        if recalled():
            return None
        saved = await self.archive.save_chat_summary(summary)
        if recalled():
            return None
        saved_records = await self._save_experience_payload(payload, context_meta, saved)
        if recalled():
            return None

        profile_id = context_meta["sender_profile_id"]
        if not await self._save_speaker_relationship_memory(
            payload,
            context_meta,
            saved,
            sender_name,
            persona_hint,
            saved_records,
            recalled,
        ):
            return None
        await self._calibrate_relationship_profile(profile_id, persona_hint, context_meta["date"])
        if recalled():
            return None
        saved_records["memory_targets"] = await self._save_memory_targets(payload, context_meta)
        if recalled():
            return None
        await self._save_chat_memory_preferences(payload, context_meta, saved_records)
        if recalled():
            return None
        self._schedule_chat_memory_memos(payload, context_meta, saved, saved_records, message)
        logger.info(f"{LOG_PREFIX} 已沉淀聊天记忆 #{saved.id}：{saved.brief}")
        return saved

    async def maybe_capture_chat_memory_from_event(
        self,
        event: Any,
        now: datetime.datetime | None = None,
        sender_name: str = "",
    ) -> ChatSummaryRecord | None:
        message = self._chat_memory_message_from_event(event)
        if not message:
            return None
        recalled = lambda: self.event_was_recalled(event, log_skip=True)
        now = now or life_now()
        sender_name = sender_name or await self.contact_resolver.resolve_event_sender(event)
        if recalled():
            return None
        capture = await self._prepare_chat_memory_capture(
            event,
            message=message,
            now=now,
            sender_name=sender_name,
            recalled=recalled,
        )
        if not capture:
            return None
        try:
            payload = await self._call_chat_memory_payload(capture)
            if payload is None:
                return None
            payload = await self._calibrate_chat_memory_payload(
                payload,
                capture["context_meta"],
                capture["persona_hint"],
            )
            if recalled():
                return None
            return await self._save_chat_memory_payload(payload, capture, recalled)
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} 聊天记忆提炼失败：{e}")
            return None
        finally:
            await self.composer._cleanup_conversation(capture["session_id"])

    async def remember_interaction(
        self,
        event: Any,
        sender_name: str,
        note: str,
        date_str: str = "",
        source: str = "chat",
    ) -> None:
        now = life_now()
        profile_id = self._event_profile_id(event, sender_name)
        meta = await self._event_context_meta(event, sender_name, now)
        await self.archive.touch_relationship(
            profile_id,
            name=sender_name,
            note=note,
            date_str=date_str or now.strftime("%Y-%m-%d"),
            source=source,
            platform=meta["platform"],
            user_id=meta["user_id"],
            alias=sender_name,
            **self._relationship_contact_payload(meta),
        )
