from __future__ import annotations

from typing import Any

from astrbot.api import logger

from .markers import LOG_PREFIX
from ..sources.platforms import (
    call_bot_action,
    get_onebot_client,
    parse_unified_origin,
)


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


class OutboundLogMixin:
    """记录统一出站正文。"""

    _OUTBOUND_LOGGED_DIRECT_ATTR = "_daily_life_logged_direct_bodies"
    _OUTBOUND_BOT_NAME_CACHE_ATTR = "_daily_life_outbound_bot_name_cache"

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
        """清洗身份串，同时保留昵称与 QQ 号之间的唯一分隔符。"""
        raw = str(value or "").strip().replace("\r", " ").replace("\n", " ")
        if "/" not in raw:
            return cls._outbound_bot_name_value(raw)
        name, bot_id = raw.split("/", 1)
        return f"{cls._outbound_bot_name_value(name)}/{cls._outbound_bot_name_value(bot_id)}"

    def _outbound_bot_name_cache(self) -> dict[str, str]:
        cache = getattr(self, self._OUTBOUND_BOT_NAME_CACHE_ATTR, None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, self._OUTBOUND_BOT_NAME_CACHE_ATTR, cache)
        return cache

    @staticmethod
    def _outbound_bot_id_value(value: Any) -> str:
        if callable(value):
            return ""
        return str(value or "").strip()

    async def prepare_outbound_bot_name(
        self, *, scope: str = "", source_event: Any = None
    ) -> str:
        """解析并缓存当前平台的机器人昵称/QQ号身份。"""
        bot_id = ""
        getter = getattr(source_event, "get_self_id", None)
        if callable(getter):
            try:
                bot_id = self._outbound_bot_id_value(getter())
            except Exception:
                bot_id = ""
        platform_id, _ = parse_unified_origin(str(scope or ""))
        cache_key = ":".join(item for item in (platform_id, bot_id) if item)
        cache_key = cache_key or platform_id or "default"
        cache = self._outbound_bot_name_cache()
        if cache.get(cache_key):
            return cache[cache_key]

        name = ""
        getter = getattr(source_event, "get_self_name", None)
        if callable(getter):
            try:
                name = self._outbound_bot_name_value(getter())
            except Exception:
                name = ""

        bot = get_onebot_client(
            getattr(self, "context", None),
            target_umo=str(scope or ""),
            event=source_event,
        )
        if not name and bot is not None:
            for attr in ("nickname", "nick", "name"):
                name = self._outbound_bot_name_value(getattr(bot, attr, ""))
                if name:
                    break
        if bot is not None and (not name or not bot_id):
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

        name = name or "机器人"
        identity = f"{name}/{bot_id}" if bot_id else name
        cache[cache_key] = identity
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
    ) -> bool:
        """统一记录已经完成投递的机器人出站正文。"""

        content = message_outline(chain)
        if not content:
            return False
        # 出站日志只记录机器人昵称/QQ号和正文，不记录用户身份或会话标识。
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
        name = await self.prepare_outbound_bot_name(
            scope=str(kwargs.get("scope", "") or ""),
            source_event=kwargs.get("source_event"),
        )
        return self.log_outbound_message(chain, bot_name=name, **kwargs)

    async def log_outbound_result_async(
        self, event: Any, *, source: str = "reply"
    ) -> bool:
        name = await self.prepare_outbound_bot_name(
            scope=str(getattr(event, "unified_msg_origin", "") or ""),
            source_event=event,
        )
        return self.log_outbound_result(event, source=source, bot_name=name)

    def log_outbound_result(
        self, event: Any, *, source: str = "reply", bot_name: str = ""
    ) -> bool:
        """记录核心回包；已由插件直发记录的正文只消费去重标记。"""
        if hasattr(event, "_has_send_oper") and not bool(
            getattr(event, "_has_send_oper", False)
        ):
            return False
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        content = message_outline(chain)
        if not content:
            return False
        direct_bodies = self._outbound_direct_bodies(event)
        if content in direct_bodies:
            direct_bodies.remove(content)
            return False
        return self.log_outbound_message(
            chain,
            scope=str(getattr(event, "unified_msg_origin", "") or ""),
            source_event=event,
            source=source,
            bot_name=bot_name,
        )

__all__ = ["OutboundLogMixin", "message_outline"]
