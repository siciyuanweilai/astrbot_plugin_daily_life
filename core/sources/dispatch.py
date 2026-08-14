from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .platforms import (
    get_platform_id,
    is_weixin_oc_instance,
    iter_platform_instances,
    parse_unified_origin,
)


class _MessageType(str, Enum):
    GROUP_MESSAGE = "GroupMessage"
    FRIEND_MESSAGE = "FriendMessage"


@dataclass(frozen=True, slots=True)
class _MessageSession:
    platform_name: str
    message_type: Any
    session_id: str

    @property
    def platform_id(self) -> str:
        return self.platform_name

    def __str__(self) -> str:
        value = getattr(self.message_type, "value", self.message_type)
        return f"{self.platform_name}:{value}:{self.session_id}"


def _message_session(platform_id: str, target: str, *, is_group: bool) -> Any:
    try:
        from astrbot.core.platform.message_session import MessageSesion
        from astrbot.core.platform.message_type import MessageType
    except ImportError:
        message_type = (
            _MessageType.GROUP_MESSAGE if is_group else _MessageType.FRIEND_MESSAGE
        )
        return _MessageSession(platform_id, message_type, target)
    return MessageSesion(
        platform_id,
        MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE,
        target,
    )


def _weixin_target(scope: str) -> tuple[str, bool]:
    text = str(scope or "").strip()
    _platform_id, target = parse_unified_origin(text)
    target = str(target or text).strip()
    lowered = target.lower()
    if lowered.endswith("@chatroom"):
        return target, True
    if lowered.endswith("@im.wechat"):
        return target, False
    return "", False


def _weixin_instance(context: Any, preferred_id: str = "") -> Any:
    instances = iter_platform_instances(context)
    candidates = [item for item in instances if is_weixin_oc_instance(item)]
    preferred = str(preferred_id or "").strip()
    if preferred:
        for instance in candidates:
            if get_platform_id(instance) == preferred:
                return instance
    return candidates[0] if candidates else None


async def send_message_to_scope(context: Any, scope: str, chain: Any) -> bool:
    """按会话真实平台投递消息，兼容不同适配器使用相同实例 ID。"""

    normalized_scope = str(scope or "").strip()
    if not normalized_scope:
        return False

    target, is_group = _weixin_target(normalized_scope)
    instances = iter_platform_instances(context)
    if target and instances:
        platform_id, _real_id = parse_unified_origin(normalized_scope)
        instance = _weixin_instance(context, platform_id)
        if instance is None:
            return False

        session = _message_session(
            get_platform_id(instance), target, is_group=is_group
        )
        await instance.send_by_session(session, chain)
        return True

    sender = getattr(context, "send_message", None)
    if not callable(sender):
        return False
    result = await sender(normalized_scope, chain)
    return result is not False


__all__ = ["send_message_to_scope"]
