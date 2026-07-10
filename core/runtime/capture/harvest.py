from typing import Any

from ...clock import now as life_now
from ...models import ChatSummaryRecord


class ImprintHarvestMixin:
    async def _get_memory_provider(self):
        provider_id = self.config.memory.provider
        return await self.composer._get_provider(provider_id)

    async def _save_chat_memory_preferences(
        self,
        payload: dict[str, Any],
        context_meta: dict[str, str],
        saved_records: dict[str, list[Any]],
    ) -> None:
        saved_preferences = await self.composer.learn_preferences_from_payload(
            payload,
            date_str=context_meta["date"],
            source="chat_memory_batch",
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
        memos_items = self._memos_items_from_payload(
            payload,
            saved_summary=saved,
            saved_records=saved_records,
        )
        self.schedule_memos_selected_items(
            context_meta,
            memos_items,
            reason="同步批量提炼确认的长期聊天记忆。",
            user_message=message,
            marker=str(saved.id),
        )

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
