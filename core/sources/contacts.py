import inspect
import asyncio
from typing import Any, Dict, List

from astrbot.api import logger

from .events import event_attr, event_call, has_event_call, iter_event_sources
from .platforms import (
    call_bot_action,
    get_onebot_client,
    is_onebot_event,
    is_weixin_oc_event,
    parse_unified_origin,
)


class ContactNameResolver:
    """解析当前聊天发送者的用户昵称"""

    def __init__(self, context: Any, config: dict, log_prefix: str = "[日常生活]"):
        self.context = context
        self.config = config
        self.log_prefix = log_prefix
        self._onebot_name_cache: dict[str, str] = {}
        self._onebot_name_pending: dict[str, asyncio.Task[str]] = {}
        self._onebot_group_name_cache: dict[str, str] = {}

    async def resolve_event_sender(self, event: Any, default: str = "用户") -> str:
        event_source = self._event_source(event)
        sender_id = self._safe_event_call(event, "get_sender_id")
        origin = self._safe_event_attr(event, "unified_msg_origin")
        target_uid = sender_id or origin

        alias = self.get_relationship_alias(target_uid, event=event)
        if alias:
            return alias

        if is_weixin_oc_event(event_source):
            persona_name = await self.get_persona_user_name()
            if persona_name:
                return persona_name
        else:
            onebot_name = await self.get_onebot_nickname(target_uid, event=event_source)
            if onebot_name:
                return onebot_name

        sender_name = self._clean_nickname_candidate(
            self._safe_event_call(event, "get_sender_name"),
            target_uid,
            event=event,
        )
        if sender_name:
            return sender_name
        return default if is_weixin_oc_event(event_source) else sender_id or default

    def get_relationship_alias(self, target_uid: str, event: Any = None) -> str:
        aliases = self._normalize_relationship_aliases()
        for key in self._target_alias_keys(target_uid, event):
            alias = str(aliases.get(key, "") or "").strip()
            if alias:
                return alias
        return ""

    async def get_onebot_nickname(self, target_uid: str, event: Any = None) -> str:
        target_s = str(target_uid or "").strip()
        adapter_id, real_id = parse_unified_origin(target_s)
        probe_id = real_id or target_s
        if not str(probe_id).isdigit():
            return ""
        cache_key = f"{adapter_id or ''}:{probe_id}"
        cached = self._onebot_name_cache.get(cache_key)
        if cached:
            return cached
        pending = self._onebot_name_pending.get(cache_key)
        if pending:
            return await pending

        bot = get_onebot_client(self.context, target_s, event=event, adapter_id=adapter_id)
        task = asyncio.create_task(self._fetch_onebot_nickname(cache_key, target_s, probe_id, event, bot))
        self._onebot_name_pending[cache_key] = task
        try:
            return await task
        finally:
            self._onebot_name_pending.pop(cache_key, None)

    async def _fetch_onebot_nickname(
        self,
        cache_key: str,
        target_s: str,
        probe_id: str,
        event: Any,
        bot: Any,
    ) -> str:

        if not bot:
            if event and is_onebot_event(event):
                return self._clean_nickname_candidate(
                    self._safe_event_call(event, "get_sender_name"),
                    target_s,
                    event=event,
                )
            return ""

        try:
            ret = await call_bot_action(bot, "get_stranger_info", raise_missing=True, user_id=int(probe_id))
            if isinstance(ret, dict):
                remark = str(ret.get("remark", "") or "").strip()
                if remark:
                    logger.debug(f"{self.log_prefix} 获取到用户备注：{remark}")
                    self._onebot_name_cache[cache_key] = remark
                    return remark
                nickname = str(ret.get("nickname", "") or "").strip()
                if nickname:
                    logger.debug(f"{self.log_prefix} 获取到用户昵称：{nickname}")
                    self._onebot_name_cache[cache_key] = nickname
                    return nickname
        except Exception as e:
            logger.warning(f"{self.log_prefix} 获取平台昵称失败：{e}")

        if event and is_onebot_event(event):
            return self._clean_nickname_candidate(
                self._safe_event_call(event, "get_sender_name"),
                target_s,
                event=event,
            )
        return ""

    async def resolve_group_name(self, group_id: str, event: Any = None, target_umo: str = "") -> str:
        group_id_s = str(group_id or "").strip()
        if not group_id_s:
            return ""
        event_source = self._event_source(event)

        event_name = self._clean_group_name_candidate(
            self._safe_event_call(event, "get_group_name") or self._safe_event_attr(event, "group_name"),
            group_id_s,
        )
        if event_name:
            return event_name

        adapter_id, real_id = parse_unified_origin(target_umo or self._safe_event_attr(event, "unified_msg_origin"))
        probe_id = real_id or group_id_s
        if not str(probe_id).isdigit():
            return ""
        cache_key = f"{adapter_id or ''}:{probe_id}"
        cached = self._onebot_group_name_cache.get(cache_key)
        if cached:
            return cached

        bot = get_onebot_client(self.context, target_umo, event=event_source, adapter_id=adapter_id)
        if not bot:
            return ""

        try:
            ret = await call_bot_action(bot, "get_group_info", raise_missing=True, group_id=int(probe_id))
            if isinstance(ret, dict):
                for key in ("group_remark", "group_memo", "group_name"):
                    name = self._clean_group_name_candidate(ret.get(key), group_id_s)
                    if name:
                        logger.debug(f"{self.log_prefix} 获取到群聊名称：{name}")
                        self._onebot_group_name_cache[cache_key] = name
                        return name
        except Exception as e:
            logger.warning(f"{self.log_prefix} 获取群聊名称失败：{e}")
        return ""

    async def get_persona_user_name(self) -> str:
        manager = getattr(self.context, "persona_manager", None)
        if not manager:
            return ""

        persona = None
        getter = getattr(manager, "get_default_persona_v3", None)
        if callable(getter):
            try:
                persona = getter()
                if inspect.isawaitable(persona):
                    persona = await persona
            except Exception as e:
                logger.debug(f"{self.log_prefix} 读取默认人格用户称呼失败：{e}")

        if not persona:
            persona = getattr(manager, "selected_default_persona_v3", None)

        if isinstance(persona, dict):
            return str(persona.get("user_name", "") or "").strip()
        return str(getattr(persona, "user_name", "") or "").strip()

    def _normalize_relationship_aliases(self) -> Dict[str, str]:
        raw_aliases = self.config.get("relationship_aliases", [])

        aliases: Dict[str, str] = {}
        if isinstance(raw_aliases, dict):
            for key, value in raw_aliases.items():
                key_s = str(key or "").strip()
                value_s = str(value or "").strip()
                if key_s and value_s:
                    aliases[key_s] = value_s
            return aliases

        if not isinstance(raw_aliases, (list, tuple, set)):
            return aliases

        for item in raw_aliases:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                key_s = str(item[0] or "").strip()
                value_s = str(item[1] or "").strip()
            else:
                item_s = str(item or "").strip().replace("：", ":", 1)
                if ":" not in item_s:
                    continue
                key_s, value_s = [part.strip() for part in item_s.split(":", 1)]
            if key_s and value_s:
                aliases[key_s] = value_s
        return aliases

    def _target_alias_keys(self, target_uid: str, event: Any = None) -> List[str]:
        keys: List[str] = []

        def add(value: Any):
            value_s = str(value or "").strip()
            if value_s:
                keys.append(value_s)
                _, real_id = parse_unified_origin(value_s)
                if real_id:
                    keys.append(real_id)

        add(target_uid)
        if event:
            add(self._safe_event_attr(event, "unified_msg_origin"))
            add(self._safe_event_call(event, "get_sender_id"))
        return list(dict.fromkeys(keys))

    def _clean_nickname_candidate(self, nickname: str, target_uid: str, event: Any = None) -> str:
        name = str(nickname or "").strip()
        if not name:
            return ""
        keys = set(self._target_alias_keys(target_uid, event))
        if name in keys or name.endswith("@im.wechat") or name.endswith("@chatroom"):
            return ""
        return name

    @staticmethod
    def _clean_group_name_candidate(name: Any, group_id: str) -> str:
        text = str(name or "").strip()
        if not text or text == str(group_id or "").strip() or text.endswith("@chatroom"):
            return ""
        return text

    def _safe_event_call(self, event: Any, method_name: str) -> str:
        return event_call(event, method_name)

    def _safe_event_attr(self, event: Any, attr_name: str) -> str:
        return event_attr(event, attr_name)

    def _event_source(self, event: Any) -> Any:
        for source in iter_event_sources(event):
            if any(
                [
                    has_event_call(source, "get_sender_id"),
                    has_event_call(source, "get_sender_name"),
                    has_event_call(source, "get_platform_name"),
                    str(getattr(source, "unified_msg_origin", "") or "").strip(),
                ]
            ):
                return source
        return event
