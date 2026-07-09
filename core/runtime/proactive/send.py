import random
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain

try:
    from astrbot.api.message_components import Image, Record, Video
except Exception:
    Image = None
    Record = None
    Video = None

from ...models import ExpressionIntentRecord, ExpressionReviewRecord
from ..markers import LOG_PREFIX


class ProactiveSendMixin:
    def _normalize_proactive_reply_text(self, reply_text: str) -> str:
        lines = [" ".join(line.split()) for line in str(reply_text or "").splitlines()]
        return "\n".join(line for line in lines if line)

    async def _send_proactive_message(
        self,
        target_scope: str,
        reply_text: str,
        failure_label: str,
        *,
        relationship: Any | None = None,
        contact_type: str = "",
        send_payload: dict[str, Any] | None = None,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        target_scope = str(target_scope or "").strip()
        reply_text = self._normalize_proactive_reply_text(reply_text)
        if not target_scope or not reply_text:
            return False
        try:
            if not self.can_send_for_source(
                target_scope,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                return False
            await self._apply_proactive_send_timing(send_payload)
            if not self.can_send_for_source(
                target_scope,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                return False
            if await self._send_proactive_voice_if_enabled(
                target_scope,
                reply_text,
                send_payload,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                self.note_structured_bot_message(target_scope, reply_text, media="语音")
                await self._append_proactive_send_history(target_scope, reply_text)
                return True
            if not await self._send_segmented_proactive_message(
                target_scope,
                reply_text,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                return False
            await self._append_proactive_send_history(target_scope, reply_text)
            await self._send_proactive_emoji_if_needed(target_scope, send_payload)
            return True
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} {failure_label}：{exc}")
            await self._mark_failed_proactive_contact(
                target_scope,
                exc,
                relationship=relationship,
                contact_type=contact_type,
            )
            return False

    async def _mark_failed_proactive_contact(
        self,
        target_scope: str,
        exc: Exception,
        *,
        relationship: Any | None = None,
        contact_type: str = "",
    ) -> None:
        reason = str(exc or "").strip()
        if not target_scope or not reason:
            return
        resolved_type = contact_type or ("friend" if "FriendMessage" in target_scope else "")
        if not resolved_type:
            return
        not_friend = "不是好友" in reason or "not friend" in reason.lower()
        if resolved_type != "friend" and not not_friend:
            return
        marker = getattr(self.archive, "mark_relationship_contact_unreachable", None)
        if not callable(marker):
            return
        try:
            await marker(target_scope, "不是好友或当前不可私聊", contact_type=resolved_type)
        except Exception as mark_exc:
            logger.debug(f"{LOG_PREFIX} 标记闲时回复目标不可达失败：{mark_exc}")

    async def _save_proactive_expression_records(
        self,
        event: Any,
        payload: dict[str, Any],
        reply_text: str,
        *,
        source: str = "proactive_reply",
    ) -> None:
        scope = self._event_session_id(event) or self._proactive_scope_key(event)
        message_id = self._event_message_id(event)
        review = payload.get("expression_review")
        if isinstance(review, dict):
            await self.archive.save_expression_review(
                ExpressionReviewRecord.from_value(
                    {
                        **review,
                        "scope": review.get("scope") or scope,
                        "reply_text": review.get("reply_text") or reply_text,
                        "source": review.get("source") or source,
                    },
                    source=source,
                )
            )
        intent = payload.get("expression_intent")
        if isinstance(intent, dict):
            await self.archive.save_expression_intent(
                ExpressionIntentRecord.from_value(
                    {
                        **intent,
                        "scope": intent.get("scope") or scope,
                        "message_id": intent.get("message_id") or message_id,
                        "reply_text": intent.get("reply_text") or reply_text,
                        "source": intent.get("source") or source,
                    },
                    source=source,
                )
            )

    async def _send_proactive_voice_if_enabled(
        self,
        target_scope: str,
        reply_text: str,
        payload: dict[str, Any] | None,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        voice_config = getattr(self.config, "voice_generation", None)
        if not voice_config or not voice_config.enabled or not voice_config.proactive_enabled:
            return False
        if not self._voice_allowed_for_scope(target_scope):
            return False
        if not self._proactive_voice_probability_hit(voice_config):
            return False
        media = getattr(self, "media", None)
        voice_service = getattr(media, "voice", None)
        if not voice_service:
            return False
        emotion = ""
        emotion_category = ""
        if isinstance(payload, dict):
            intent = payload.get("expression_intent")
            if isinstance(intent, dict):
                emotion = str(intent.get("emotion") or "").strip()
                emotion_category = str(intent.get("emotion_category") or "").strip()
        try:
            generated = await voice_service.synthesize(reply_text, emotion=emotion, emotion_category=emotion_category)
            if not await self.send_message_if_not_recalled(
                target_scope,
                self._record_message_chain(generated.path),
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                return False
            self.note_structured_bot_message(target_scope, reply_text, media="语音")
            await self._note_voice_expression_decision(
                scope=target_scope,
                channel="语音",
                source="闲时回复",
                reason="闲时消息设置允许语音且概率命中，本次直接用语音靠近。",
                result="已发送",
                text=reply_text,
                emotion=emotion,
                emotion_category=emotion_category,
                confidence=1.0,
            )
            return True
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时消息语音发送失败，改用文字：{exc}")
            await self._note_voice_expression_decision(
                scope=target_scope,
                channel="文字",
                source="闲时回复",
                reason=f"闲时消息语音发送失败，改用文字：{exc}",
                result="改用文字",
                text=reply_text,
                emotion=emotion,
                emotion_category=emotion_category,
                confidence=1.0,
            )
            return False

    @staticmethod
    def _proactive_voice_probability_hit(voice_config: Any) -> bool:
        try:
            probability = float(getattr(voice_config, "proactive_probability", 100.0))
        except (TypeError, ValueError):
            probability = 100.0
        if probability <= 0:
            return False
        if probability >= 100:
            return True
        return random.random() * 100 < probability

    @staticmethod
    def _record_message_chain(path: Path) -> Any:
        chain = MessageChain()
        if Record is not None:
            items = getattr(chain, "chain", None)
            if isinstance(items, list):
                items.append(Record(file=str(path)))
                return chain
        items = getattr(chain, "chain", None)
        if isinstance(items, list):
            items.append({"type": "record", "file": str(path)})
            return chain
        stub_items = getattr(chain, "items", None)
        if isinstance(stub_items, list):
            stub_items.append({"type": "record", "file": str(path)})
            return chain
        return chain

    @staticmethod
    def image_message_chain(path: Path) -> Any:
        chain = MessageChain()
        if Image is not None:
            items = getattr(chain, "chain", None)
            if isinstance(items, list):
                items.append(Image.fromFileSystem(str(path)))
                return chain
        method = getattr(chain, "file_image", None)
        if callable(method):
            return method(str(path))
        return chain

    @staticmethod
    def video_message_chain(url: str) -> Any:
        chain = MessageChain()
        text = str(url)
        is_local_file = False
        if not text.startswith(("http://", "https://")):
            try:
                is_local_file = Path(text).exists()
            except OSError:
                is_local_file = False
        if Video is not None:
            items = getattr(chain, "chain", None)
            if isinstance(items, list):
                if is_local_file and hasattr(Video, "fromFileSystem"):
                    items.append(Video.fromFileSystem(text))
                else:
                    items.append(Video.fromURL(text))
                return chain
        items = getattr(chain, "chain", None)
        if isinstance(items, list):
            item = {"type": "video", "file": text}
            if not is_local_file:
                item["url"] = text
            items.append(item)
            return chain
        stub_items = getattr(chain, "items", None)
        if isinstance(stub_items, list):
            item = {"type": "video", "file": text}
            if not is_local_file:
                item["url"] = text
            stub_items.append(item)
            return chain
        return chain
