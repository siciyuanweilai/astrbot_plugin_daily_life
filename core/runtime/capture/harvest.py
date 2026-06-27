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

    async def maybe_capture_chat_memory_from_event(
        self,
        event: Any,
        now: datetime.datetime | None = None,
        sender_name: str = "",
    ) -> ChatSummaryRecord | None:
        message = str(getattr(event, "message_str", "") or "").strip()
        if (
            not message
            or self._event_has_command_handler(event)
            or self.event_was_recalled(event, log_skip=True)
            or len(message) < self.config.memory.min_message_length
        ):
            return None
        recalled = lambda: self.event_was_recalled(event, log_skip=True)
        now = now or life_now()
        sender_name = sender_name or await self.contact_resolver.resolve_event_sender(event)
        if recalled():
            return None
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
        )
        try:
            provider_id = self.config.memory.provider
            payload = await call_pure_json(
                self.composer,
                provider,
                prompt,
                session_id,
                primary_provider_id=provider_id,
            )
            if not isinstance(payload, dict):
                return None
            payload = await self._calibrate_chat_memory_payload(payload, context_meta, persona_hint)
            if recalled():
                return None
            await self._save_memory_awareness_records(payload, context_meta)
            if recalled():
                return None
            await self._append_memory_decision_log(payload, context_meta, now)
            if recalled():
                return None
            if not payload.get("worth_saving"):
                await self._save_subjective_impression(payload, context_meta, persona_hint=persona_hint)
                if recalled():
                    return None
                await self._save_experience_payload(payload, context_meta)
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
                return None
            saved_records["speaker_relationship"] = [
                {
                    "name": sender_name,
                    "subjective_name": impression_data["subjective_name"],
                    "subjective_tags": impression_data["subjective_tags"],
                    "relationship_story": impression_data["relationship_story"],
                    "note": impression_data["note"] or saved.brief,
                }
            ]
            for point in payload.get("relationship_points", []) if isinstance(payload.get("relationship_points"), list) else []:
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
                if recalled():
                    return None
            await self._calibrate_relationship_profile(profile_id, persona_hint, context_meta["date"])
            if recalled():
                return None
            saved_records["memory_targets"] = await self._save_memory_targets(payload, context_meta)
            if recalled():
                return None
            saved_preferences = await self.composer.learn_preferences_from_payload(
                payload,
                date_str=context_meta["date"],
                source="chat_memory",
            )
            if recalled():
                return None
            saved_records["preferences"] = list(saved_preferences or [])
            memos_items = self._memos_items_from_payload(payload, saved_summary=saved, saved_records=saved_records)
            self.schedule_memos_selected_items(
                context_meta,
                memos_items,
                reason="同步已确认的聊天长期记忆、关系画像、稳定偏好、表达方式和生活事件。",
                user_message=message,
                marker=str(saved.id),
            )
            logger.info(f"{LOG_PREFIX} 已沉淀聊天记忆 #{saved.id}：{saved.brief}")
            return saved
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} 聊天记忆提炼失败：{e}")
            return None
        finally:
            await self.composer._cleanup_conversation(session_id)

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
