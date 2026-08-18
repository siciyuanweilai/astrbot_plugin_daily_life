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


class ScopeDeliveryError(RuntimeError):
    """会话投递失败，带有是否应立即终止重试的分类。"""

    def __init__(self, message: str, *, code: str, permanent: bool):
        super().__init__(message)
        self.code = code
        self.permanent = permanent


class PermanentScopeDeliveryError(ScopeDeliveryError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message, code=code, permanent=True)


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


async def send_message_to_scope(
    context: Any, scope: str, chain: Any, *, raise_delivery_errors: bool = False
) -> bool:
    """按会话真实平台投递消息，兼容不同适配器使用相同实例 ID。"""

    normalized_scope = str(scope or "").strip()
    if not normalized_scope:
        if raise_delivery_errors:
            raise PermanentScopeDeliveryError("投递会话为空", code="invalid_scope")
        return False

    target, is_group = _weixin_target(normalized_scope)
    instances = iter_platform_instances(context)
    if target and instances:
        platform_id, _real_id = parse_unified_origin(normalized_scope)
        instance = _weixin_instance(context, platform_id)
        if instance is None:
            if raise_delivery_errors:
                raise PermanentScopeDeliveryError(
                    "未找到可用的微信适配器实例", code="adapter_missing"
                )
            return False

        session = _message_session(
            get_platform_id(instance), target, is_group=is_group
        )
        try:
            await instance.send_by_session(session, chain)
        except Exception as exc:
            reason = str(exc or "")
            lowered = reason.lower()
            permanent = any(
                marker in reason
                or marker in lowered
                for marker in ("不是好友", "无权限", "被拒绝", "不支持", "not friend", "permission")
            )
            if permanent:
                if raise_delivery_errors:
                    raise PermanentScopeDeliveryError(
                        reason or "微信平台拒绝投递", code="platform_rejected"
                    ) from exc
                return False
            raise
        return True

    sender = getattr(context, "send_message", None)
    if not callable(sender):
        if raise_delivery_errors:
            raise PermanentScopeDeliveryError(
                "当前平台不支持按会话发送", code="unsupported_platform"
            )
        return False
    try:
        result = await sender(normalized_scope, chain)
    except Exception as exc:
        reason = str(exc or "")
        lowered = reason.lower()
        permanent = any(
            marker in reason
            or marker in lowered
            for marker in ("不是好友", "无权限", "被拒绝", "不支持", "not friend", "permission")
        )
        if permanent:
            if raise_delivery_errors:
                raise PermanentScopeDeliveryError(
                    reason or "平台拒绝投递", code="platform_rejected"
                ) from exc
            return False
        raise
    if result is False:
        if raise_delivery_errors:
            raise ScopeDeliveryError("消息发送未完成", code="send_failed", permanent=False)
        return False
    return True


__all__ = [
    "PermanentScopeDeliveryError",
    "ScopeDeliveryError",
    "send_message_to_scope",
]
