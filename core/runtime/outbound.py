from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from astrbot.api import logger

from ..sources.botidentity import (
    bot_id_value,
    event_bot_id,
    get_qq_official_client,
    get_weixin_oc_instance,
    qq_official_profile,
    qq_official_profile_value,
)
from ..sources.platforms import (
    call_bot_action,
    get_onebot_client,
    get_platform_id,
    parse_unified_origin,
)
from .elapsed import ConversationTimingService
from .markers import LOG_PREFIX

_TEXT_TYPES = {"", "text", "plain"}
_COMPONENT_LABELS = {
    "image": "[图片]",
    "record": "[语音]",
    "audio": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "face": "[表情]",
    "reply": "[回复]",
    "at": "[艾特]",
    "node": "[转发]",
    "nodes": "[转发]",
    "poke": "[戳一戳]",
}


def _kind_name(item: Any) -> str:
    if isinstance(item, dict):
        value = item.get("type") or item.get("kind") or ""
    else:
        value = getattr(item, "type", "")
    value = getattr(value, "value", value)
    return str(value or "").strip().lower().split(".")[-1]


def _text_value(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        data = item.get("data")
        if isinstance(data, dict):
            return str(data.get("text") or data.get("content") or "")
        return str(item.get("text") or item.get("content") or "")
    return str(getattr(item, "text", "") or getattr(item, "content", "") or "")


def _component_outline(item: Any) -> str:
    if isinstance(item, str):
        return item
    kind = _kind_name(item)
    if kind in _TEXT_TYPES:
        return _text_value(item)
    label = _COMPONENT_LABELS.get(kind)
    if label:
        return label
    return f"[组件:{kind or 'unknown'}]"


def message_outline(chain: Any) -> str:
    """将消息链转换成适合日志的正文摘要，不读取媒体文件内容。"""
    if chain is None:
        return ""
    if isinstance(chain, (list, tuple)):
        items = chain
    else:
        items = getattr(chain, "chain", None)
        if items is None:
            items = getattr(chain, "items", None)
        if items is None:
            items = (chain,)
    try:
        text = "".join(_component_outline(item) for item in items)
    except TypeError:
        text = _component_outline(chain)
    return str(text or "").strip()


class OutboundLogService:
    """记录统一出站正文。"""

    _OUTBOUND_LOGGED_DIRECT_ATTR = "_daily_life_logged_direct_bodies"

    def __init__(
        self,
        *,
        context: Any,
        timing: ConversationTimingService,
        schedule: Callable[..., bool],
        delivered: Callable[..., Any] | None = None,
    ) -> None:
        self._context = context
        self._timing = timing
        self._schedule = schedule
        self._delivered = delivered
        self._bot_names: dict[tuple[str, str, int], str] = {}

    def clear(self) -> None:
        self._bot_names.clear()

    @staticmethod
    def _outbound_bot_name_value(value: Any) -> str:
        # CQHttp 用动态属性映射 API 动作；例如 bot.nickname 可能是可调用的
        # partial，而不是机器人真实昵称。
        if callable(value):
            return ""
        name = str(value or "").strip().replace("\r", " ").replace("\n", " ")
        return name.replace("/", "／")

    @classmethod
    def _outbound_bot_identity_value(cls, value: Any) -> str:
        """清洗身份串，同时保留展示名与账号 ID 之间的唯一分隔符。"""
        raw = str(value or "").strip().replace("\r", " ").replace("\n", " ")
        if "/" not in raw:
            return cls._outbound_bot_name_value(raw)
        name, bot_id = raw.split("/", 1)
        return f"{cls._outbound_bot_name_value(name)}/{cls._outbound_bot_name_value(bot_id)}"

    @staticmethod
    def _outbound_bot_id_value(value: Any) -> str:
        return bot_id_value(value)

    async def prepare_outbound_bot_name(
        self, *, scope: str = "", source_event: Any = None
    ) -> str:
        """解析并缓存当前平台的机器人昵称与账号 ID。"""
        scope = str(scope or getattr(source_event, "unified_msg_origin", "") or "")
        # 后台决策事件不是平台入站事件，其 platform_name 可能是实例名。
        # 身份应从实际投递的 UMO 定位，不能将这个字段当成协议类型。
        identity_event = (
            None if getattr(source_event, "is_proactive_synthetic", False)
            else source_event
        )
        bot_id = event_bot_id(identity_event, context=self._context, scope=scope)
        platform_id, _ = parse_unified_origin(scope)
        official_bot = get_qq_official_client(self._context, event=identity_event, scope=scope)
        weixin = get_weixin_oc_instance(self._context, event=identity_event, scope=scope)
        if weixin is not None:
            bot = weixin
        elif official_bot is not None:
            bot = official_bot
        else:
            bot = get_onebot_client(self._context, target_umo=scope, event=identity_event)
        # 客户端重建或换账号后重新查询，避免不同机器人串用昵称。
        client_key = id(bot) if bot is not None else 0
        cache_key = (platform_id, bot_id, client_key)
        cache = self._bot_names
        if cache.get(cache_key):
            return cache[cache_key]

        name = ""
        getter = getattr(identity_event, "get_self_name", None)
        if callable(getter):
            try:
                name = self._outbound_bot_name_value(getter())
            except Exception:
                name = ""

        if weixin is not None:
            # Weixin OC exposes an account ID but no profile nickname. Use
            # the configured connection label only as a display fallback.
            name = name or self._outbound_bot_name_value(get_platform_id(weixin))
        elif official_bot is not None:
            official_name, official_id, _ = qq_official_profile(official_bot)
            name = name or self._outbound_bot_name_value(official_name)
            bot_id = bot_id or official_id
            if not name or not bot_id:
                me = getattr(getattr(official_bot, "api", None), "me", None)
                if callable(me):
                    try:
                        result = await asyncio.wait_for(me(), timeout=3.0)
                        official_name, official_id, _ = qq_official_profile_value(result)
                        name = name or self._outbound_bot_name_value(official_name)
                        bot_id = bot_id or official_id
                    except Exception as exc:
                        logger.debug(f"{LOG_PREFIX} 获取 QQ 官方机器人身份失败：{type(exc).__name__}")

        if official_bot is None and weixin is None and not name and bot is not None:
            for attr in ("nickname", "nick", "name"):
                name = self._outbound_bot_name_value(getattr(bot, attr, ""))
                if name:
                    break
        if official_bot is None and weixin is None and bot is not None and (not name or not bot_id):
            try:
                result = await call_bot_action(bot, "get_login_info", raise_missing=True)
                nested = result.get("data") if isinstance(result, dict) else None
                candidates = [nested, result] if isinstance(nested, dict) else [result]
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    if not bot_id:
                        for key in ("user_id", "self_id", "uin", "qq"):
                            bot_id = self._outbound_bot_id_value(candidate.get(key))
                            if bot_id:
                                break
                    if not name:
                        for key in ("nickname", "nick", "name"):
                            name = self._outbound_bot_name_value(candidate.get(key))
                            if name:
                                break
                    if name and bot_id:
                        break
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 获取机器人昵称/QQ号失败：{type(exc).__name__}: {exc}"
                )

        identity = f"{name or '机器人'}/{bot_id}" if bot_id else name or "机器人"
        # 暂时失败或缺字段不成为永久身份，下次成功查询即可恢复。
        if name and bot_id:
            cache[cache_key] = identity
            cache[(platform_id, bot_id, client_key)] = identity
        return identity

    def _outbound_direct_bodies(self, event: Any) -> list[str]:
        getter = getattr(event, "get_extra", None)
        setter = getattr(event, "set_extra", None)
        if callable(getter):
            values = getter(self._OUTBOUND_LOGGED_DIRECT_ATTR, [])
        else:
            values = getattr(event, self._OUTBOUND_LOGGED_DIRECT_ATTR, [])
        if not isinstance(values, list):
            values = []
        if callable(setter):
            setter(self._OUTBOUND_LOGGED_DIRECT_ATTR, values)
        else:
            setattr(event, self._OUTBOUND_LOGGED_DIRECT_ATTR, values)
        return values

    def log_outbound_message(
        self,
        chain: Any,
        *,
        scope: str = "",
        source_event: Any = None,
        source: str = "",
        bot_name: str = "",
        sent_at: float | None = None,
        persist_receipt: bool = True,
    ) -> bool:
        """统一记录已经完成投递的机器人出站正文。"""

        content = message_outline(chain)
        if not content:
            return False
        sent_at = time.time() if sent_at is None else sent_at
        scope = scope or str(getattr(source_event, "unified_msg_origin", "") or "")
        self._timing.note_conversation_sent(scope, sent_at, source_event)
        if persist_receipt:
            self._schedule(
                self._timing.record_conversation_delivery(scope, sent_at),
                label="保存对话发送时间",
            )
        # 框架已记录普通回复正文；这里只保留发送回执，避免同一条消息在日志中出现两次。
        if source == "reply":
            logger.debug(f"{LOG_PREFIX} 普通回复发送回执已记录。")
        else:
            # 插件直发消息没有框架的普通回复日志，需要保留其出站正文。
            bot_identity = self._outbound_bot_identity_value(bot_name) or "机器人"
            logger.info(
                f"{LOG_PREFIX} {bot_identity}: {content}",
                extra={
                    "category": "user_chat",
                    "outbound_source": str(source or "plugin"),
                },
            )
        if source_event is not None:
            self._outbound_direct_bodies(source_event).append(content)
        return True

    async def log_outbound_message_async(self, chain: Any, **kwargs: Any) -> bool:
        if not message_outline(chain):
            return False
        sent_at = time.time()
        source_event = kwargs.get("source_event")
        scope = str(kwargs.get("scope") or getattr(source_event, "unified_msg_origin", "") or "")
        # 先记录发送回执，再查询昵称，防止这段等待期间的新消息丢失时间锚点。
        await self._timing.record_conversation_delivery(scope, sent_at, source_event)
        if self._delivered is not None:
            await self._delivered(scope, message_outline(chain))
        name = await self.prepare_outbound_bot_name(scope=scope, source_event=source_event)
        return self.log_outbound_message(
            chain, bot_name=name, sent_at=sent_at, persist_receipt=False, **kwargs
        )

    def _outbound_result_chain(self, event: Any) -> Any:
        if hasattr(event, "_has_send_oper") and not event._has_send_oper:
            return None
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        content = message_outline(chain)
        if not content:
            return None
        direct_bodies = self._outbound_direct_bodies(event)
        if content in direct_bodies:
            direct_bodies.remove(content)
            return None
        return chain

    async def log_outbound_result_async(
        self, event: Any, *, source: str = "reply"
    ) -> bool:
        chain = self._outbound_result_chain(event)
        if chain is None:
            return False
        return await self.log_outbound_message_async(
            chain, scope=str(getattr(event, "unified_msg_origin", "") or ""),
            source_event=event, source=source,
        )

    def log_outbound_result(
        self, event: Any, *, source: str = "reply", bot_name: str = ""
    ) -> bool:
        """记录核心回包；已由插件直发记录的正文只消费去重标记。"""
        chain = self._outbound_result_chain(event)
        if chain is None:
            return False
        return self.log_outbound_message(
            chain, scope=str(getattr(event, "unified_msg_origin", "") or ""),
            source_event=event, source=source, bot_name=bot_name,
        )


__all__ = ["OutboundLogService", "message_outline"]
