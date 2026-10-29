from __future__ import annotations

import copy
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain

try:
    from astrbot.api.message_components import Image, Record, Video
except Exception:
    Image = None
    Record = None
    Video = None

try:
    from astrbot.api.message_components import File
except ImportError:
    File = None

from ...clock import now as life_now
from ...models import ExpressionIntentRecord, ExpressionReviewRecord
from ...sources.dispatch import ScopeDeliveryError
from ..contact import contact_rhythm
from ..delivery import BackgroundTextMode
from ..markers import LOG_PREFIX

from typing import Protocol


class ProactiveSendComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _MEDIA_CHAT_TOKEN_ATTR: Any
    _VOICE_SWITCH_MIN_NATURAL_SCORE: Any
    _append_proactive_send_history: Any
    _apply_proactive_send_timing: Any
    _continuous_turn_identity: Any
    _event_message_id: Any
    _event_session_id: Any
    _media_chat_tokens: Any
    _note_voice_expression_decision: Any
    _proactive_scope_key: Any
    _proactive_send_is_current: Any
    _proactive_source_label: Any
    _record_message_chain: Any
    _send_proactive_emoji_if_needed: Any
    _send_segmented_proactive_message: Any
    _snapshot_proactive_send_event: Any
    _state_refresh_in_quiet_hours: Any
    _voice_allowed_for_scope: Any
    _voice_switch_structural_text_reason: Any
    archive: Any
    can_send_for_source: Any
    config: Any
    media: Any
    media_request_is_current_turn: Any
    note_structured_bot_message: Any
    send_background_text: Any
    send_message_if_not_recalled: Any


class ProactiveSendComponent:
    def __init__(self, context: ProactiveSendComponentContext) -> None:
        self.context = context

    def _snapshot_proactive_send_event(
        self, scope: str, source_event: Any = None, *, bind_source: bool = False
    ) -> Any:
        event = (
            copy.copy(source_event)
            if source_event is not None
            else SimpleNamespace(unified_msg_origin=scope)
        )
        if not hasattr(event, "_daily_life_proactive_send_token"):
            event._daily_life_proactive_send_token = (
                scope,
                self._proactive_chat_snapshot(scope),
            )
        if bind_source and self.context._event_message_id(event):
            identity = self.context._continuous_turn_identity(event)
            if identity in getattr(self.context, "_media_chat_tokens", {}):
                setattr(
                    event,
                    self.context._MEDIA_CHAT_TOKEN_ATTR,
                    (*identity, self.context._event_message_id(event)),
                )
        return event

    def _proactive_chat_snapshot(self, scope: str) -> tuple:
        return tuple(
            sorted(
                (participant, token)
                for (current_scope, participant), token in getattr(
                    self.context, "_media_chat_tokens", {}
                ).items()
                if current_scope == scope
            )
        )

    def _proactive_send_is_current(self, event: Any) -> bool:
        quiet_guard = getattr(event, "_daily_life_quiet_guard", False)
        quiet_check = getattr(self.context, "_state_refresh_in_quiet_hours", None)
        if quiet_guard and callable(quiet_check) and quiet_check(life_now()):
            return False
        expires_at = getattr(event, "_daily_life_proactive_expires_at", None)
        if expires_at is not None and life_now() >= expires_at:
            return False
        token = getattr(event, "_daily_life_proactive_send_token", None)
        if not self.context.media_request_is_current_turn(event) or (
            token is not None and self._proactive_chat_snapshot(token[0]) != token[1]
        ):
            return False
        gate = getattr(self.context, "share_contact_gate", None)
        return gate is None or gate.allow_life(event)

    async def _send_proactive_voice_call_invite(
        self,
        target_scope: str,
        notice: str,
        link: str,
        *,
        source_event: Any = None,
    ) -> bool:
        """发送主动通话提示和单独链接，保持移动端可直接复制。"""

        parts = [str(notice or "").strip(), str(link or "").strip()]
        if not all(parts):
            return False
        source_event = self.context._snapshot_proactive_send_event(target_scope, source_event)
        source_event._daily_life_quiet_guard = True
        for part in parts:
            if not self.context._proactive_send_is_current(source_event):
                return False
            if not await self.context.send_background_text(
                target_scope,
                part,
                mode=BackgroundTextMode.DIRECT,
                source_event=source_event,
                source="proactive_voice_call",
            ):
                return False
        return True

    @staticmethod
    def _proactive_source_label(payload: dict[str, Any] | None) -> str:
        source = (
            str(payload.get("source") or "").strip()
            if isinstance(payload, dict)
            else ""
        )
        return {
            "private_revisit": "私聊回访",
            "proactive_commitment": "主动承诺",
            "shared_activity_contact": "共同活动联系",
            "proactive_reply": "闲时回复",
            "proactive_voice_call": "主动语音邀请",
        }.get(source, "闲时回复")

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
        raise_delivery_errors: bool = False,
    ) -> bool:
        target_scope = str(target_scope or "").strip()
        reply_text = self._normalize_proactive_reply_text(reply_text)
        payload = send_payload if isinstance(send_payload, dict) else {}
        sent_parts: list[str] = []
        delivered = False

        def receipt(status: str, code: str, reason: str) -> None:
            payload["_delivery"] = {
                "status": status, "reason_code": code, "reason": reason,
                "sent_text": "\n".join(sent_parts),
            }

        def record_sent(text: str) -> None:
            sent_parts.append(text)
            receipt("partial", "segments_sent", "部分消息已送达")

        if not target_scope or not reply_text:
            receipt("invalid", "empty_message", "发送对象或正文为空")
            return False
        source_event = self.context._snapshot_proactive_send_event(
            target_scope,
            source_event,
            bind_source=bool(
                send_payload and send_payload.get("source") == "proactive_reply"
            ),
        )
        if (send_payload or {}).get("source") in {"private_revisit", "proactive_reply", "proactive_voice_call"}:
            source_event._daily_life_quiet_guard = True
        try:
            if not self.context._proactive_send_is_current(source_event):
                receipt("interrupted", "conversation_changed", "新消息或撤回已改变本次发送依据")
                return False
            if not self.context.can_send_for_source(
                target_scope,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                receipt("interrupted", "source_unavailable", "本次消息来源已不可发送")
                return False
            await self.context._apply_proactive_send_timing(send_payload)
            if not self.context._proactive_send_is_current(source_event):
                receipt("interrupted", "conversation_changed", "新消息或撤回已改变本次发送依据")
                return False
            if not self.context.can_send_for_source(
                target_scope,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                receipt("interrupted", "source_unavailable", "本次消息来源已不可发送")
                return False
            if (send_payload or {}).get("source") in {"private_revisit", "proactive_reply"}:
                rhythm = await contact_rhythm(self.context, target_scope, life_now())
                if rhythm.get("ready") is False:
                    receipt("deferred", "contact_rhythm", "当前收发节奏暂不适合主动联系")
                    return False
            if await self._send_proactive_voice_if_enabled(
                target_scope,
                reply_text,
                payload,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                delivered = True
                sent_parts.append(reply_text)
                receipt("confirmed", "voice_sent", "语音已送达")
                self.context.note_structured_bot_message(target_scope, reply_text, media="语音")
                await self.context._append_proactive_send_history(
                    target_scope, reply_text, media="语音"
                )
                return True
            if not self.context._proactive_send_is_current(source_event):
                receipt("interrupted", "conversation_changed", "新消息或撤回已改变本次发送依据")
                return False
            complete = await self.context._send_segmented_proactive_message(
                target_scope,
                reply_text,
                source_event=source_event,
                source_message_id=source_message_id,
                raise_delivery_errors=True,
                on_sent=record_sent,
            )
            if not complete and not sent_parts:
                receipt("interrupted", "source_unavailable", "发送前来源已变化")
                return False
            if not sent_parts:
                sent_parts.append(reply_text)
            delivered = bool(complete)
            receipt("confirmed" if complete else "partial", "text_sent", "文字已送达" if complete else "已发送部分文字，后续发送已中断")
            await self.context._append_proactive_send_history(target_scope, "\n".join(sent_parts))
            if not complete:
                return True
            emoji_payload = dict(send_payload or {})
            if source_event is not None:
                emoji_payload["_source_event"] = source_event
            if source_message_id:
                emoji_payload["_source_message_id"] = source_message_id
            await self.context._send_proactive_emoji_if_needed(target_scope, emoji_payload)
            return True
        except Exception as exc:
            if delivered or sent_parts or (payload.get("_delivery") or {}).get("status") == "confirmed":
                if sent_parts:
                    receipt("confirmed" if delivered else "partial", "sent_with_followup_error", "消息已送达，后续记录或附加消息未完成")
                logger.debug(f"{LOG_PREFIX} 主动消息已送达，后续处理暂未完成：{exc}")
                return True
            unknown = not isinstance(exc, ScopeDeliveryError) or exc.code == "delivery_unknown"
            receipt("unknown" if unknown else "failed", "delivery_unknown" if unknown else exc.code,
                    "发送结果未确认，停止本次自动重发" if unknown else str(exc))
            logger.warning(f"{LOG_PREFIX} {failure_label}：{exc}")
            await self._mark_failed_proactive_contact(
                target_scope,
                exc,
                relationship=relationship,
                contact_type=contact_type,
            )
            if raise_delivery_errors and isinstance(exc, ScopeDeliveryError):
                raise
            return False

    async def _mark_failed_proactive_contact(
        self,
        target_scope: str,
        exc: Exception,
        *,
        relationship: Any | None = None,
        contact_type: str = "",
    ) -> None:
        # 只有平台明确拒绝发送才改变联系人可达性，网络抖动不代表关系失效。
        if (
            not target_scope
            or not isinstance(exc, ScopeDeliveryError)
            or not exc.permanent
            or exc.code != "platform_rejected"
        ):
            return
        scope_parts = target_scope.split(":")
        resolved_type = contact_type or (
            "friend" if len(scope_parts) > 1 and scope_parts[1] == "FriendMessage" else ""
        )
        if resolved_type != "friend":
            return
        marker = getattr(self.context.archive, "mark_relationship_contact_unreachable", None)
        if not callable(marker):
            return
        try:
            await marker(
                target_scope, str(exc), contact_type=resolved_type
            )
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
        scope = self.context._event_session_id(event) or self.context._proactive_scope_key(event)
        message_id = self.context._event_message_id(event)
        review = payload.get("expression_review")
        candidate = reply_text or str(payload.get("_expression_candidate_text") or "").strip()
        if isinstance(review, dict) and candidate and isinstance(review.get("passed"), bool):
            await self.context.archive.save_expression_review(
                ExpressionReviewRecord.from_value(
                    {
                        **review,
                        "scope": scope,
                        "reply_text": candidate,
                        "source": source,
                    },
                    source=source,
                )
            )
        intent = payload.get("expression_intent")
        if isinstance(intent, dict):
            await self.context.archive.save_expression_intent(
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

    def _proactive_voice_available(self, target_scope: str) -> bool:
        config = getattr(self.context.config, "voice_generation", None)
        service = getattr(getattr(self.context, "media", None), "voice", None)
        return bool(
            config
            and config.enabled
            and config.proactive_enabled
            and self.context._voice_allowed_for_scope(target_scope)
            and callable(getattr(service, "synthesize", None))
        )

    def _proactive_voice_capability(self, target_scope: str) -> str:
        if self._proactive_voice_available(target_scope):
            return "可用；是否使用由本轮表达意图和收听情境决定，不代表必须发语音"
        return "不可用；expression_intent.channel 必须选择 text"

    async def _send_proactive_voice_if_enabled(
        self,
        target_scope: str,
        reply_text: str,
        payload: dict[str, Any] | None,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        if not self._proactive_voice_available(target_scope):
            return False
        intent = payload.get("expression_intent") if isinstance(payload, dict) else None
        if not isinstance(intent, dict) or intent.get("channel") != "voice":
            return False
        raw_confidence = intent.get("confidence")
        if isinstance(raw_confidence, bool):
            return False
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            return False
        reason = str(intent.get("reason") or "").strip()
        if (
            not math.isfinite(confidence)
            or not self.context._VOICE_SWITCH_MIN_NATURAL_SCORE <= confidence <= 1.0
            or not reason
            or self.context._voice_switch_structural_text_reason(reply_text)
        ):
            return False
        voice_service = self.context.media.voice
        emotion = str(intent.get("emotion") or "").strip()
        emotion_category = str(intent.get("emotion_category") or "").strip()
        voice_style = str(intent.get("voice_style") or "").strip().lower()
        source_label = self.context._proactive_source_label(payload)
        source_event = self.context._snapshot_proactive_send_event(target_scope, source_event)
        if not self.context._proactive_send_is_current(source_event):
            return False
        voice_kwargs = {"emotion": emotion, "emotion_category": emotion_category}
        if voice_style:
            voice_kwargs["voice_style"] = voice_style
        try:
            generated = await voice_service.synthesize(reply_text, **voice_kwargs)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} {source_label}语音生成未完成，改用文字：{exc}")
            return False
        if not self.context._proactive_send_is_current(source_event):
            return False
        try:
            sent = await self.context.send_message_if_not_recalled(
                target_scope,
                self.context._record_message_chain(generated.path),
                source_event=source_event,
                source_message_id=source_message_id,
            )
        except ScopeDeliveryError:
            raise
        except Exception as exc:
            raise ScopeDeliveryError("语音投递结果未确认，停止自动补发", code="delivery_unknown", permanent=False) from exc
        if not sent:
            if not self.context._proactive_send_is_current(source_event):
                return False
            raise ScopeDeliveryError("语音投递结果未确认，停止自动补发", code="delivery_unknown", permanent=False)
        if isinstance(payload, dict):
            payload["_delivery"] = {"status": "confirmed", "reason_code": "voice_sent",
                                    "reason": "语音已送达", "sent_text": reply_text}
        try:
            await self.context._note_voice_expression_decision(
                scope=target_scope, channel="语音", source=source_label,
                reason=reason, result="已发送", text=reply_text,
                emotion=emotion, emotion_category=emotion_category, confidence=confidence,
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 语音已送达，表达记录暂未保存：{exc}")
        return True

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
    def images_message_chain(paths: list[Path]) -> Any:
        chain = MessageChain()
        items = getattr(chain, "chain", None)
        if Image is not None and isinstance(items, list):
            items.extend(Image.fromFileSystem(str(path)) for path in paths)
            return chain
        method = getattr(chain, "file_image", None)
        if callable(method):
            for path in paths:
                updated = method(str(path))
                if updated is not None:
                    chain = updated
            return chain
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

    @staticmethod
    def video_file_message_chain(url: str) -> Any:
        chain = MessageChain()
        items = getattr(chain, "chain", None)
        if File is None or not isinstance(items, list):
            raise RuntimeError("当前环境不支持视频文件附件")
        text = str(url)
        if text.startswith(("http://", "https://")):
            items.append(File(name="生活视频.mp4", url=text))
        else:
            items.append(File(name="生活视频.mp4", file=text))
        return chain
