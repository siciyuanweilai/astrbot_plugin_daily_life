from __future__ import annotations

from ..clock import now as life_now
from ..sources.platforms import parse_unified_origin
from .proactive.procontext import ProactiveSyntheticEvent


class ExternalIntegrationMixin:
    """记录其他插件已经完成的主动行为。"""

    @staticmethod
    def _external_activity_memos_meta(target_umo: str) -> dict[str, str]:
        scope = str(target_umo or "").strip()
        platform, real_id = parse_unified_origin(scope)
        is_group = ":GroupMessage:" in scope
        meta = {
            "session_id": scope or real_id or "daily_life",
            "platform": platform,
            "is_group": "true" if is_group else "false",
        }
        if is_group:
            meta["group_id"] = real_id or scope
        else:
            meta["sender_profile_id"] = real_id or scope
        return meta

    async def record_external_activity(
        self,
        target_umo: str,
        content: str,
        *,
        image_description: str = "",
        image_sent: bool = False,
        media_kind: str = "",
        reason: str = "外部主动活动",
        sync_memory: bool = False,
    ) -> bool:
        scope = str(target_umo or "").strip()
        text = str(content or "").strip()
        if not scope or not text:
            return False

        media_label = {
            "image": "图片",
            "video": "视频",
            "audio": "语音",
        }.get(str(media_kind or "").strip().lower(), "")
        self.note_structured_bot_message(scope, text, media=media_label)
        capture = getattr(self, "capture_proactive_chat_memory_reply", None)
        if callable(capture):
            await capture(scope, text, media=media_label)

        _, real_id = parse_unified_origin(scope)
        is_group = ":GroupMessage:" in scope
        now = life_now()
        event = ProactiveSyntheticEvent(
            message=text,
            target_scope=scope,
            group_id=real_id if is_group else "",
        )
        self._mark_proactive_reply_sent(event, now)
        self.note_proactive_bot_reply(event, now)

        if sync_memory:
            memory_text = text
            description = str(image_description or "").strip()
            if description:
                memory_text += f"\n[配图: {description}]"
            elif image_sent:
                memory_text += "\n[已发送配图]"
            self.schedule_memos_selected_items(
                self._external_activity_memos_meta(scope),
                [memory_text],
                reason=str(reason or "外部主动活动").strip() or "外部主动活动",
                user_message=str(reason or "").strip(),
                marker=memory_text,
            )
        return True


__all__ = ["ExternalIntegrationMixin"]
