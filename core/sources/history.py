import datetime
import json
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from ..clock import timestamp as life_timestamp
from .platforms import first_weixin_oc_adapter_id, parse_unified_origin


class SavedHistoryReader:
    def __init__(self, context: Any, log_prefix: str = "[日常生活]"):
        self.context = context
        self.log_prefix = log_prefix

    def resolve_target_umo(self, target_id: str, is_group: bool) -> str:
        target = str(target_id or "").strip()
        if not target:
            return ""

        parts = target.split(":")
        adapter_id, real_id = parse_unified_origin(target)
        if adapter_id and real_id:
            if is_group and (
                adapter_id.strip().lower() == "weixin_oc" or self._is_weixin_session(real_id)
            ):
                return ""
            if len(parts) >= 3 and parts[1].lower() == "groupmessage" and self._is_weixin_session(real_id):
                return ""
            return target

        if self._is_weixin_session(target):
            if is_group:
                return ""
            adapter_id = first_weixin_oc_adapter_id(self.context) or "weixin_oc"
            return f"{adapter_id}:FriendMessage:{target}"

        return ""

    async def fetch(
        self,
        target_umo: str,
        max_count: int,
        hours: int = 0,
        *,
        prefer_conversation: bool = False,
    ) -> List[Dict[str, str]]:
        target = str(target_umo or "").strip()
        if not target or max_count <= 0:
            return []

        if prefer_conversation:
            messages = await self._fetch_conversation_history(target, max_count, hours)
            if messages:
                return messages

        messages = await self._fetch_platform_history(target, max_count, hours)
        if messages:
            return messages
        return await self._fetch_conversation_history(target, max_count, hours)

    async def _fetch_platform_history(self, target_umo: str, max_count: int, hours: int) -> List[Dict[str, str]]:
        adapter_id, real_id = parse_unified_origin(target_umo)
        if not adapter_id or not real_id:
            return []

        manager = getattr(self.context, "message_history_manager", None)
        get_history = getattr(manager, "get", None)
        if not callable(get_history):
            return []

        try:
            records = []
            for user_id in self._history_user_ids(adapter_id, real_id):
                records = await get_history(
                    platform_id=adapter_id,
                    user_id=user_id,
                    page=1,
                    page_size=max_count,
                )
                if records:
                    break

            cutoff = self._cutoff_ts(hours)
            messages = [
                msg
                for msg in (self._normalize_platform_record(record) for record in records or [])
                if msg and self._within_cutoff(msg.get("timestamp", ""), cutoff)
            ]
            if messages:
                logger.debug(f"{self.log_prefix} 已读取平台保存的聊天历史：{target_umo}（{len(messages)} 条消息）")
            return messages[-max_count:]
        except Exception as e:
            logger.debug(f"{self.log_prefix} 读取平台保存的聊天历史失败：{e}")
            return []

    async def _fetch_conversation_history(self, target_umo: str, max_count: int, hours: int) -> List[Dict[str, str]]:
        manager = getattr(self.context, "conversation_manager", None)
        if not manager:
            return []

        try:
            conversation_id = await manager.get_curr_conversation_id(target_umo)
            if not conversation_id:
                return []

            conversation = await manager.get_conversation(target_umo, conversation_id)
            if not conversation:
                return []

            raw_history = getattr(conversation, "history", [])
            if isinstance(raw_history, str):
                try:
                    raw_history = json.loads(raw_history or "[]")
                except json.JSONDecodeError:
                    return []
            if not isinstance(raw_history, list):
                return []

            cutoff = self._cutoff_ts(hours)
            window = raw_history[-(max_count + 5):]
            messages = [
                msg
                for msg in (self._normalize_conversation_item(item) for item in window)
                if msg and self._within_cutoff(msg.get("timestamp", ""), cutoff)
            ]
            if messages:
                logger.debug(f"{self.log_prefix} 已读取会话保存的聊天历史：{target_umo}（{len(messages)} 条消息）")
            return messages[-max_count:]
        except Exception as e:
            logger.debug(f"{self.log_prefix} 读取会话保存的聊天历史失败：{e}")
            return []

    def _normalize_platform_record(self, record: Any) -> Optional[Dict[str, str]]:
        content = getattr(record, "content", None)
        role = "user"
        text = ""

        if isinstance(content, dict):
            content_type = str(content.get("type") or "").lower()
            role = "assistant" if content_type in ("bot", "assistant") else "user"
            payload = content.get("message", content.get("content", ""))
            text = self._extract_text(payload)
            if not text:
                text = str(content.get("text") or content.get("data") or "").strip()
        elif content is not None:
            text = str(content).strip()

        if not text:
            return None

        sender_id = str(getattr(record, "sender_id", "") or "").strip()
        if not sender_id:
            sender_id = role
        if sender_id.lower() in ("bot", "assistant"):
            role = "assistant"

        return {
            "role": role,
            "content": text,
            "user_id": sender_id,
            "name": str(getattr(record, "sender_name", "") or "").strip(),
            "timestamp": self._format_timestamp(getattr(record, "created_at", "")),
        }

    def _normalize_conversation_item(self, item: Any) -> Optional[Dict[str, str]]:
        if not isinstance(item, dict):
            return None

        role = str(item.get("role") or item.get("type") or "user").lower()
        if role not in ("user", "assistant"):
            role = "assistant" if role in ("ai", "bot") else "user"

        text = self._extract_text(item.get("content", ""))
        if not text:
            return None

        return {
            "role": role,
            "content": text,
            "user_id": str(item.get("user_id") or item.get("name") or role),
            "name": str(item.get("name") or ""),
            "timestamp": self._format_timestamp(item.get("timestamp") or item.get("time") or ""),
        }

    def _extract_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            part_type = str(value.get("type") or "").lower()
            if "text" in value:
                return str(value.get("text") or "").strip()
            if part_type == "text":
                data = value.get("data") or {}
                return str(data.get("text") or value.get("content") or "").strip()
            if part_type == "plain":
                return str(value.get("text") or value.get("content") or "").strip()
            if part_type in ("image", "img"):
                return "[image]"
            if part_type in ("record", "audio"):
                return "[audio]"
            if part_type == "video":
                return "[video]"
            if part_type == "file":
                return "[file]"
            for key in ("message", "content", "data"):
                if key in value:
                    return self._extract_text(value.get(key))
            return ""
        if isinstance(value, list):
            texts = [self._extract_text(item) for item in value]
            return " ".join(text for text in texts if text).strip()
        text = getattr(value, "text", None)
        return str(text or "").strip()

    def _history_user_ids(self, adapter_id: str, real_id: str) -> List[str]:
        ids = [str(real_id or "").strip()]
        if str(adapter_id or "").lower().startswith("webchat") and ids[0].startswith("webchat!"):
            parts = ids[0].split("!", 2)
            if len(parts) == 3 and parts[2]:
                ids.append(parts[2])
        return list(dict.fromkeys(item for item in ids if item))

    def _is_weixin_session(self, target_id: str) -> bool:
        target = str(target_id or "").strip().lower()
        return target.endswith("@im.wechat") or target.endswith("@chatroom")

    def _cutoff_ts(self, hours: int) -> float:
        try:
            hours_i = int(hours)
        except Exception:
            hours_i = 0
        return life_timestamp() - hours_i * 3600 if hours_i > 0 else 0

    def _within_cutoff(self, timestamp: str, cutoff: float) -> bool:
        if cutoff <= 0:
            return True
        parsed = self._parse_timestamp(timestamp)
        return parsed <= 0 or parsed >= cutoff

    def _format_timestamp(self, value: Any) -> str:
        if isinstance(value, datetime.datetime):
            return value.isoformat()
        return str(value or "")

    def _parse_timestamp(self, value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if not text:
            return 0
        try:
            return float(text)
        except ValueError:
            pass
        try:
            return datetime.datetime.fromisoformat(text).timestamp()
        except ValueError:
            return 0
