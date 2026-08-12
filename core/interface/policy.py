from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any


class LifeActionScope(str, Enum):
    """生活动作影响范围。"""

    PUBLIC = "public"
    PRIVATE = "private"
    OWNED = "owned"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class LifeActorContext:
    """发起生活动作的消息侧身份。"""

    session_id: str
    sender_id: str
    is_admin: bool
    is_private: bool

    @classmethod
    def from_event(cls, event: Any) -> "LifeActorContext":
        admin_check = getattr(event, "is_admin", None)
        private_check = getattr(event, "is_private_chat", None)
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        sender_getter = getattr(event, "get_sender_id", None)
        sender_id = (
            str(sender_getter() or "")
            if callable(sender_getter)
            else str(getattr(event, "sender_id", "") or "")
        )
        is_private = (
            bool(private_check())
            if callable(private_check)
            else ":FriendMessage:" in origin
        )
        return cls(
            session_id=origin,
            sender_id=sender_id,
            is_admin=bool(admin_check()) if callable(admin_check) else False,
            is_private=is_private,
        )


@dataclass(frozen=True, slots=True)
class LifeActionProposal:
    """由聊天 Agent 提出、交给确定性策略裁定的生活动作。"""

    action: str
    scope: LifeActionScope
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    resource_owner: str = ""
    reason: str = ""

    @classmethod
    def build(
        cls,
        action: str,
        scope: LifeActionScope,
        *,
        payload: Mapping[str, Any] | None = None,
        resource_owner: str = "",
        reason: str = "",
    ) -> "LifeActionProposal":
        return cls(
            action=str(action or "").strip(),
            scope=scope,
            payload=MappingProxyType(dict(payload or {})),
            resource_owner=str(resource_owner or "").strip(),
            reason=str(reason or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class LifePolicyDecision:
    allowed: bool
    reason: str = ""


class LifeAccessPolicy:
    """统一裁定消息侧生活数据访问和状态变更权限。"""

    def decide(self, event: Any, proposal: LifeActionProposal) -> LifePolicyDecision:
        actor = LifeActorContext.from_event(event)
        if actor.is_admin or proposal.scope == LifeActionScope.PUBLIC:
            return LifePolicyDecision(True)
        if proposal.scope == LifeActionScope.ADMIN:
            return LifePolicyDecision(
                False,
                "该操作只允许 AstrBot 管理员执行。",
            )
        if proposal.scope == LifeActionScope.PRIVATE:
            if actor.is_private:
                return LifePolicyDecision(True)
            return LifePolicyDecision(
                False,
                "该操作会改变全局生活状态，请在私聊中操作。",
            )
        if proposal.scope == LifeActionScope.OWNED:
            if actor.session_id and actor.session_id == proposal.resource_owner:
                return LifePolicyDecision(True)
            return LifePolicyDecision(
                False,
                "未找到对应记录，或当前会话无权操作。",
            )
        return LifePolicyDecision(False, "当前会话无权执行该操作。")

    def denial(self, event: Any, proposal: LifeActionProposal) -> str:
        decision = self.decide(event, proposal)
        return "" if decision.allowed else decision.reason

    @staticmethod
    def owns(event: Any, resource_owner: str) -> bool:
        actor = LifeActorContext.from_event(event)
        return bool(
            actor.is_admin
            or (
                actor.session_id
                and actor.session_id == str(resource_owner or "").strip()
            )
        )
