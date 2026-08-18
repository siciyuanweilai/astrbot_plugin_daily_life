from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Protocol

from astrbot.api import logger

from ..clock import TIMEZONE
from ..clock import now as life_now
from ..sources.platforms import parse_unified_origin
from .markers import LOG_PREFIX

INTERACTION_MODE_PREDICATE = "interaction_mode"
INTERACTION_MODE_MAX_AGE = datetime.timedelta(hours=4)
INTERACTION_TURN_CONFIDENCE_MIN = 0.7
_INTERACTION_TURN_DECISION_ATTR = "_daily_life_interaction_turn_decision"
_INTERACTION_MODE_LABELS = {
    "co_present": "同处现场",
    "remote": "远程交流",
    "unknown": "未知",
}


def _interaction_fact_field(fact: Any, field: str, default: Any = "") -> Any:
    if isinstance(fact, dict):
        return fact.get(field, default)
    return getattr(fact, field, default)


def _interaction_timestamp(value: Any) -> datetime.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(TIMEZONE).replace(tzinfo=None)
    return parsed


def interaction_fact_is_current(
    fact: Any,
    *,
    now: datetime.datetime,
) -> bool:
    """判断互动方式事实是否仍在短期有效窗口内。"""

    if (
        str(_interaction_fact_field(fact, "predicate") or "").strip()
        != INTERACTION_MODE_PREDICATE
    ):
        return False
    observed_at = _interaction_timestamp(
        _interaction_fact_field(fact, "observed_at")
        or _interaction_fact_field(fact, "valid_from")
        or _interaction_fact_field(fact, "created_at")
    )
    if observed_at is None:
        return False
    age = now - observed_at
    return -datetime.timedelta(minutes=5) <= age <= INTERACTION_MODE_MAX_AGE


def _interaction_mode(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("mode")
    mode = str(value or "").strip().lower()
    return mode if mode in {"co_present", "remote"} else "unknown"


def normalize_interaction_turn_decision(value: Any) -> dict[str, Any] | None:
    """规范化当前话轮对现实互动方式的语义裁定。"""

    if not isinstance(value, dict):
        return None
    decision = str(value.get("decision") or "").strip().lower()
    mode = _interaction_mode(value.get("mode"))
    try:
        confidence = max(0.0, min(float(value.get("confidence") or 0.0), 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if decision not in {"keep", "set", "clear"}:
        return None
    if confidence < INTERACTION_TURN_CONFIDENCE_MIN:
        return None
    if decision == "set" and mode not in {"co_present", "remote"}:
        return None
    return {
        "decision": decision,
        "mode": mode,
        "confidence": confidence,
        "reason": str(value.get("reason") or "").strip()[:300],
    }


@dataclass(frozen=True, slots=True)
class InteractionContext:
    """消息传输范围与现实互动方式的结构化边界。"""

    transport: str
    mode: str = "unknown"
    scope: str = ""
    profile_id: str = ""
    confidence: float = 0.0
    observed_at: str = ""
    previous_mode: str = "unknown"
    pending_current: bool = False
    evidence: str = ""

    @property
    def mode_label(self) -> str:
        return _INTERACTION_MODE_LABELS.get(self.mode, "未知")

    @property
    def has_authoritative_mode(self) -> bool:
        return self.mode in {"co_present", "remote"}

    @property
    def previous_mode_label(self) -> str:
        return _INTERACTION_MODE_LABELS.get(self.previous_mode, "未知")

    def format_for_generation(self) -> str:
        lines = [
            "\n\n[HiddenInteractionContext]",
            f"- 消息传输范围：{self.transport}；这只描述平台承载方式，不代表双方现实距离。",
        ]
        if self.pending_current:
            lines.append("- 现实互动方式：待根据当前完整话轮的语义确认。")
            if self.previous_mode in {"co_present", "remote"}:
                lines.append(
                    f"- 本轮开始前最近依据：{self.previous_mode_label}；这不是当前话轮的结论。"
                )
        else:
            lines.append(f"- 现实互动方式：{self.mode_label}。")
        if self.evidence:
            lines.append(f"- 当前裁定依据：{self.evidence}。")
        if (
            self.observed_at
            and self.has_authoritative_mode
            and not self.pending_current
        ):
            lines.append(f"- 互动方式证据时间：{self.observed_at}。")
        lines.extend(
            [
                "- 不得仅凭 user/assistant 角色、私聊/群聊通道或收到消息这一事实，推断双方正在远程发消息。",
                "- 历史 assistant 回复只是已经说过的话，不是现实互动事实；如果其中出现手机、屏幕、线上或发消息等未经当前证据确认的说法，不得把它继续当作前提。",
                "- 会话摘要和关系记忆只用于回忆话题与关系；其中关于平台、设备或收发消息的叙述不能覆盖当前消息和当前互动事实。",
                "- 现实互动方式未知时，不主动描写手机、屏幕、线上、打字、收发消息或现实距离。",
                "- 这里记录的是本轮开始前的最近依据；当前消息若明确表达见面、分开、离场或互动方式变化，以当前消息的完整语义为准。",
            ]
        )
        if self.mode == "co_present":
            lines.append(
                "- 双方当前同处现场：把本轮输入理解为共享场景中的话语或动作记录，按面对面交流回应；除非当前内容明确谈到设备或平台，否则不要写成隔着屏幕收发消息。"
            )
        elif self.mode == "remote":
            lines.append(
                "- 当前有明确远程交流依据，可以按不在同一现场理解，但仍不要为了强调通道而生硬描述设备操作。"
            )
        return "\n".join(lines)


class InteractionContextMixin:
    """统一解析消息通道与现实互动方式。"""

    def note_interaction_turn_decision(
        self,
        event: Any,
        value: Any,
    ) -> dict[str, Any] | None:
        """缓存当前话轮的语义裁定，供后续上下文注入复用。"""

        decision = normalize_interaction_turn_decision(value)
        if event is not None and decision is not None:
            setattr(event, _INTERACTION_TURN_DECISION_ATTR, decision)
        return decision

    @staticmethod
    def _interaction_turn_decision(event: Any) -> dict[str, Any] | None:
        if event is None:
            return None
        return normalize_interaction_turn_decision(
            getattr(event, _INTERACTION_TURN_DECISION_ATTR, None)
        )

    def _interaction_event_has_current_content(self, event: Any) -> bool:
        if event is None:
            return False
        # Idle/revisit jobs replay the last platform message through a synthetic
        # event. That content is already observed and must not invalidate the
        # latest co-present/remote fact as if it were a new user turn.
        if bool(getattr(event, "is_proactive_synthetic", False)):
            return False
        turn_getter = getattr(self, "continuous_turn_messages", None)
        if callable(turn_getter):
            try:
                if any(str(item or "").strip() for item in turn_getter(event)):
                    return True
            except Exception:
                pass
        return bool(str(getattr(event, "message_str", "") or "").strip())

    @staticmethod
    def _interaction_event_time(
        event: Any,
        fallback: datetime.datetime,
    ) -> datetime.datetime:
        message_obj = getattr(event, "message_obj", None) if event is not None else None
        raw_message = getattr(message_obj, "raw_message", None)
        candidates = [
            getattr(event, "timestamp", None) if event is not None else None,
            getattr(message_obj, "timestamp", None),
            raw_message.get("time") if isinstance(raw_message, dict) else None,
        ]
        for value in candidates:
            if value in {None, ""}:
                continue
            if isinstance(value, (int, float)):
                try:
                    return datetime.datetime.fromtimestamp(
                        float(value), TIMEZONE
                    ).replace(tzinfo=None)
                except (OSError, OverflowError, ValueError):
                    continue
            parsed = _interaction_timestamp(value)
            if parsed is not None:
                return parsed
        return fallback

    async def apply_interaction_turn_decision(
        self,
        event: Any,
        value: Any,
        *,
        now: datetime.datetime | None = None,
    ) -> dict[str, Any] | None:
        """应用当前话轮裁定，并立即写入短期互动事实。"""

        decision = self.note_interaction_turn_decision(event, value)
        if decision is None or decision["decision"] == "keep":
            return decision
        scope_getter = getattr(self, "_event_session_id", None)
        scope = (
            str(scope_getter(event) or "").strip()
            if callable(scope_getter)
            else str(getattr(event, "unified_msg_origin", "") or "").strip()
        )
        profile_id = self._interaction_profile_id(event=event, scope=scope)
        archive = getattr(self, "archive", None)
        writer = getattr(archive, "write_temporal_fact", None)
        reader = getattr(archive, "get_current_temporal_fact", None)
        if not scope or not profile_id or not callable(writer):
            return decision
        point = self._interaction_event_time(event, now or life_now())
        observed_at = point.isoformat(timespec="seconds")
        message_getter = getattr(self, "_event_message_id", None)
        message_id = (
            str(message_getter(event) or "").strip()
            if callable(message_getter)
            else str(getattr(event, "message_id", "") or "").strip()
        )
        try:
            current = (
                await reader(scope, profile_id, INTERACTION_MODE_PREDICATE)
                if callable(reader)
                else None
            )
            if decision["decision"] == "clear":
                if current is not None:
                    await writer(
                        "INVALIDATE",
                        {
                            "scope": scope,
                            "subject": profile_id,
                            "predicate": INTERACTION_MODE_PREDICATE,
                            "observed_at": observed_at,
                            "valid_from": observed_at,
                            "confidence": decision["confidence"],
                            "source": "chat_turn_semantic",
                            "source_type": "chat_message",
                            "source_id": message_id,
                            "provenance": {"reason": decision["reason"]},
                        },
                    )
                return decision
            await writer(
                "UPDATE" if current is not None else "ADD",
                {
                    "scope": scope,
                    "subject": profile_id,
                    "predicate": INTERACTION_MODE_PREDICATE,
                    "object_value": {"mode": decision["mode"]},
                    "observed_at": observed_at,
                    "valid_from": observed_at,
                    "confidence": decision["confidence"],
                    "source": "chat_turn_semantic",
                    "source_type": "chat_message",
                    "source_id": message_id,
                    "provenance": {"reason": decision["reason"]},
                },
            )
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 当前话轮互动方式写入跳过：{type(exc).__name__}: {exc}"
            )
        return decision

    def _interaction_transport(self, scope: str, event: Any = None) -> str:
        if event is not None:
            checker = getattr(self, "_event_is_group_message", None)
            if callable(checker):
                try:
                    if checker(event):
                        return "群聊"
                except Exception:
                    pass
        return "群聊" if ":GroupMessage:" in scope else "私聊"

    def _interaction_profile_id(
        self,
        *,
        event: Any = None,
        scope: str = "",
        profile_id: str = "",
    ) -> str:
        explicit = str(profile_id or "").strip()
        if explicit:
            return explicit
        if event is not None:
            getter = getattr(self, "_event_profile_id", None)
            if callable(getter):
                resolved = str(getter(event) or "").strip()
                if resolved:
                    return resolved
        _, real_id = parse_unified_origin(scope)
        return str(real_id or "").strip()

    async def resolve_interaction_context(
        self,
        *,
        event: Any = None,
        target_scope: str = "",
        profile_id: str = "",
        snapshot: dict[str, Any] | None = None,
        now: datetime.datetime | None = None,
    ) -> InteractionContext:
        scope_getter = getattr(self, "_event_session_id", None)
        scope = str(target_scope or "").strip()
        if not scope and event is not None and callable(scope_getter):
            scope = str(scope_getter(event) or "").strip()
        target_profile_id = self._interaction_profile_id(
            event=event,
            scope=scope,
            profile_id=profile_id,
        )
        transport = self._interaction_transport(scope, event)
        point = now or life_now()
        facts = list((snapshot or {}).get("temporal_facts") or [])
        if not any(
            str(_interaction_fact_field(fact, "predicate") or "").strip()
            == INTERACTION_MODE_PREDICATE
            for fact in facts
        ):
            reader = getattr(getattr(self, "archive", None), "get_temporal_facts", None)
            if callable(reader) and scope:
                facts = await reader(
                    scope=scope,
                    predicate=INTERACTION_MODE_PREDICATE,
                    limit=12,
                )
        candidates = [
            fact
            for fact in facts
            if interaction_fact_is_current(fact, now=point)
            and (
                not target_profile_id
                or str(_interaction_fact_field(fact, "subject") or "").strip()
                == target_profile_id
            )
        ]
        candidates.sort(
            key=lambda fact: (
                _interaction_timestamp(
                    _interaction_fact_field(fact, "observed_at")
                    or _interaction_fact_field(fact, "valid_from")
                    or _interaction_fact_field(fact, "created_at")
                )
                or datetime.datetime.min
            ),
            reverse=True,
        )
        if not candidates:
            base = InteractionContext(
                transport=transport,
                scope=scope,
                profile_id=target_profile_id,
            )
        else:
            fact = candidates[0]
            mode = _interaction_mode(
                _interaction_fact_field(fact, "object_value", None)
            )
            confidence = max(
                0.0,
                min(
                    float(_interaction_fact_field(fact, "confidence", 0.0) or 0.0),
                    1.0,
                ),
            )
            if confidence < INTERACTION_TURN_CONFIDENCE_MIN:
                mode = "unknown"
            base = InteractionContext(
                transport=transport,
                mode=mode,
                scope=scope,
                profile_id=target_profile_id,
                confidence=confidence,
                observed_at=str(
                    _interaction_fact_field(fact, "observed_at") or ""
                ).strip(),
            )

        turn_decision = self._interaction_turn_decision(event)
        if turn_decision is not None:
            action = turn_decision["decision"]
            if action == "keep":
                return base
            if action == "clear":
                return InteractionContext(
                    transport=transport,
                    scope=scope,
                    profile_id=target_profile_id,
                    confidence=turn_decision["confidence"],
                    previous_mode=base.mode,
                    evidence=turn_decision["reason"],
                )
            return InteractionContext(
                transport=transport,
                mode=turn_decision["mode"],
                scope=scope,
                profile_id=target_profile_id,
                confidence=turn_decision["confidence"],
                observed_at=point.isoformat(timespec="seconds"),
                previous_mode=base.mode,
                evidence=turn_decision["reason"],
            )

        if self._interaction_event_has_current_content(event):
            return InteractionContext(
                transport=transport,
                scope=scope,
                profile_id=target_profile_id,
                previous_mode=base.mode,
                pending_current=True,
            )
        return base


class ContextSnapshotSource(Protocol):
    """归档服务向运行时提供的上下文快照契约。"""

    async def get_context_snapshot(
        self,
        *,
        max_summaries: int,
        experience_scope: str = "",
        session_id: str = "",
    ) -> dict[str, Any]: ...


class ContextSnapshotRepository:
    def __init__(self, archive: ContextSnapshotSource):
        reader = getattr(archive, "get_context_snapshot", None)
        if not callable(reader):
            raise TypeError("归档服务缺少上下文快照读取能力")
        self._archive = archive

    async def read(
        self,
        *,
        max_summaries: int,
        experience_scope: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._archive.get_context_snapshot(
            max_summaries=max_summaries,
            experience_scope=experience_scope,
            session_id=session_id,
        )


__all__ = [
    "INTERACTION_MODE_MAX_AGE",
    "INTERACTION_MODE_PREDICATE",
    "INTERACTION_TURN_CONFIDENCE_MIN",
    "ContextSnapshotRepository",
    "ContextSnapshotSource",
    "InteractionContext",
    "InteractionContextMixin",
    "interaction_fact_is_current",
    "normalize_interaction_turn_decision",
]
