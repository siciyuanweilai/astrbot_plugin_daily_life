from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Node, Nodes, Plain

from ..sources.platforms import call_bot_action, get_onebot_client, is_onebot_event
from .markers import LOG_PREFIX


@dataclass(frozen=True)
class TextImageSource:
    text: str
    created_at: float
    message_id: str = ""


class TextForwardMixin:
    """暂存成功文本转图像的原文，并按需通过 QQ 合并转发发送。"""

    _T2I_SOURCE_ATTR = "_daily_life_t2i_source_text"
    _T2I_DECISION_ATTR = "_daily_life_t2i_default_send"
    _T2I_FORWARD_MAX_RECORDS = 5
    _T2I_FORWARD_TTL_SECONDS = 2 * 60 * 60

    def _init_t2i_forward_cache(self) -> None:
        self._t2i_forward_cache: dict[str, list[TextImageSource]] = {}

    def _t2i_forward_store(self) -> dict[str, list[TextImageSource]]:
        store = getattr(self, "_t2i_forward_cache", None)
        if not isinstance(store, dict):
            self._init_t2i_forward_cache()
            store = self._t2i_forward_cache
        return store

    @staticmethod
    def _t2i_forward_scope(event: Any) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "").strip()

    @staticmethod
    def _t2i_forward_message_id(event: Any) -> str:
        getter = getattr(event, "get_message_id", None)
        if callable(getter):
            try:
                value = str(getter() or "").strip()
                if value:
                    return value
            except (AttributeError, TypeError, ValueError):
                pass
        return str(getattr(event, "message_id", "") or "").strip()

    @staticmethod
    def _t2i_forward_now() -> float:
        return time.monotonic()

    @classmethod
    def _t2i_forward_is_image(cls, item: Any) -> bool:
        if isinstance(item, Image):
            return True
        if isinstance(item, dict):
            kind = item.get("type") or item.get("kind") or ""
        else:
            kind = getattr(item, "type", "")
        return str(getattr(kind, "value", kind) or "").strip().lower() == "image"

    @staticmethod
    def _t2i_forward_plain_component(item: Any) -> str | None:
        if isinstance(item, str):
            return str(item)
        if isinstance(item, dict):
            kind = str(item.get("type") or item.get("kind") or "").strip().lower()
            if kind not in {"", "text", "plain"}:
                return None
            data = item.get("data")
            if isinstance(data, dict):
                return str(data.get("text") or data.get("content") or "")
            return str(item.get("text") or item.get("content") or "")
        if isinstance(item, Plain):
            return str(getattr(item, "text", "") or "")
        kind = getattr(item, "type", "")
        kind_text = str(getattr(kind, "value", kind) or "").strip().lower()
        if kind_text not in {"plain", "text"}:
            return None
        return str(getattr(item, "text", "") or "")

    def capture_t2i_source_before_send(self, event: Any) -> bool:
        """在聊天表达处理之前记录可能被框架转图的原始文本。"""

        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not chain:
            return False
        parts: list[str] = []
        for item in chain:
            text = self._t2i_forward_plain_component(item)
            if text is None:
                break
            parts.append(text)
        source = "".join(parts)
        if not source:
            return False
        checker = getattr(self, "_chat_style_should_keep_default_send", None)
        return bool(checker(event, source)) if callable(checker) else False

    def _prune_t2i_forward_scope(
        self, scope: str, *, now: float | None = None
    ) -> list[TextImageSource]:
        store = self._t2i_forward_store()
        records = list(store.get(scope, []))
        cutoff = (self._t2i_forward_now() if now is None else now) - float(
            self._T2I_FORWARD_TTL_SECONDS
        )
        records = [record for record in records if record.created_at >= cutoff]
        records = records[-self._T2I_FORWARD_MAX_RECORDS :]
        if records:
            store[scope] = records
        else:
            store.pop(scope, None)
        return records

    def _remember_t2i_forward_source(self, event: Any, text: str) -> None:
        scope = self._t2i_forward_scope(event)
        source = str(text or "")
        if not scope or not source:
            return
        now = self._t2i_forward_now()
        records = self._prune_t2i_forward_scope(scope, now=now)
        records.append(
            TextImageSource(
                text=source,
                created_at=now,
                message_id=self._t2i_forward_message_id(event),
            )
        )
        self._t2i_forward_store()[scope] = records[-self._T2I_FORWARD_MAX_RECORDS :]
        logger.debug(
            f"{LOG_PREFIX} 文本转图像原文已暂存：会话记录={len(records[-self._T2I_FORWARD_MAX_RECORDS :])}；长度={len(source)}"
        )

    def note_t2i_image_sent(self, event: Any) -> bool:
        """只在框架已把候选长文本替换为图片并发送后提交缓存。"""

        candidate = bool(getattr(event, self._T2I_DECISION_ATTR, False))
        source = str(getattr(event, self._T2I_SOURCE_ATTR, "") or "")
        try:
            if not candidate or not source:
                return False
            result = getattr(event, "get_result", lambda: None)()
            chain = getattr(result, "chain", None)
            if not isinstance(chain, list) or not any(
                self._t2i_forward_is_image(item) for item in chain
            ):
                return False
            self._remember_t2i_forward_source(event, source)
            return True
        finally:
            for attr in (self._T2I_SOURCE_ATTR, self._T2I_DECISION_ATTR):
                try:
                    delattr(event, attr)
                except AttributeError:
                    pass

    def _recent_t2i_forward_source(
        self, event: Any, index: int
    ) -> TextImageSource | None:
        scope = self._t2i_forward_scope(event)
        if not scope or index < 1:
            return None
        records = self._prune_t2i_forward_scope(scope)
        if index > len(records):
            return None
        return records[-index]

    async def _t2i_forward_bot_name(self, event: Any) -> str:
        getter = getattr(event, "get_self_name", None)
        if callable(getter):
            try:
                name = str(getter() or "").strip()
                if name:
                    return name
            except (AttributeError, TypeError, ValueError):
                pass

        bot = get_onebot_client(getattr(self, "context", None), event=event)
        if bot:
            try:
                result = await call_bot_action(
                    bot,
                    "get_login_info",
                    raise_missing=True,
                )
                if isinstance(result, dict):
                    nested = result.get("data")
                    candidates = (
                        [nested, result] if isinstance(nested, dict) else [result]
                    )
                    for candidate in candidates:
                        for key in ("nickname", "nick", "name"):
                            name = str(candidate.get(key) or "").strip()
                            if name:
                                return name
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 获取机器人昵称失败：{type(exc).__name__}: {exc}"
                )
        return "机器人"

    @staticmethod
    def _t2i_forward_bot_id(event: Any) -> str:
        getter = getattr(event, "get_self_id", None)
        if callable(getter):
            try:
                return str(getter() or "0").strip() or "0"
            except (AttributeError, TypeError, ValueError):
                pass
        return "0"

    @staticmethod
    def _t2i_forward_result(status: str, **payload: Any) -> str:
        return json.dumps({"status": status, **payload}, ensure_ascii=False)

    async def forward_t2i_text(self, event: Any, *, index: int = 1) -> str:
        """发送指定会话最近一次文本转图像的原始文字。"""

        try:
            requested_index = int(index)
        except (TypeError, ValueError):
            requested_index = 0
        if requested_index < 1:
            return self._t2i_forward_result(
                "unavailable",
                reason="序号必须从 1 开始",
                response_stance="没有发送内容；自然说明找不到对应的文字版",
            )
        if not is_onebot_event(event):
            return self._t2i_forward_result(
                "unsupported",
                reason="当前平台不支持合并转发消息",
                response_stance="没有发送内容；自然说明当前平台暂不支持这种转发方式",
            )
        record = self._recent_t2i_forward_source(event, requested_index)
        if record is None:
            return self._t2i_forward_result(
                "unavailable",
                reason="最近没有可用的文本转图像原文，或记录已经过期",
                response_stance="没有发送内容；自然说明暂时找不到对应的文字版",
            )

        node = Node(
            uin=self._t2i_forward_bot_id(event),
            name=await self._t2i_forward_bot_name(event),
            content=[Plain(record.text)],
        )
        message = MessageChain()
        message.chain.append(Nodes([node]))
        try:
            await event.send(message)
        except Exception as exc:
            logger.warning(
                f"{LOG_PREFIX} 文本转图像原文转发失败：{type(exc).__name__}: {exc}"
            )
            return self._t2i_forward_result(
                "error",
                reason="合并转发发送失败",
                response_stance="没有发送成功；自然说明这次文字版没有发出去，不要复述原文",
            )

        logger.info(
            f"{LOG_PREFIX} 文本转图像原文已转发：序号={requested_index}；长度={len(record.text)}"
        )
        return self._t2i_forward_result(
            "sent",
            index=requested_index,
            response_stance="文字版已通过合并转发发送；可以自然确认一句，不要复述原文",
        )
