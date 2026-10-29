from __future__ import annotations

import datetime
import math
from typing import Any

from typing import Protocol


class ProactiveLifecycleComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _PROACTIVE_LIFECYCLE_TRANSITIONS: Any
    _PROACTIVE_UTILITY_FIELDS: Any
    _PROACTIVE_UTILITY_THRESHOLD: Any
    _proactive_score: Any
    _proactive_social_lifecycle: Any
    _proactive_social_sessions: Any


class ProactiveLifecycleComponent:
    """管理主动社交生命周期与确定性收益裁定。"""

    def __init__(self, context: ProactiveLifecycleComponentContext) -> None:
        self.context = context

    _PROACTIVE_UTILITY_FIELDS = (
        "benefit",
        "timeliness",
        "continuity",
        "disruption",
        "uncertainty",
    )
    _PROACTIVE_UTILITY_THRESHOLD = 60
    _PROACTIVE_LIFECYCLE_TRANSITIONS = {
        "": {"candidate", "considering", "cooldown", "abandoned"},
        "candidate": {"considering", "interrupted", "abandoned"},
        "considering": {
            "waiting",
            "sending",
            "cooldown",
            "interrupted",
            "abandoned",
        },
        "waiting": {"considering", "interrupted", "abandoned", "closing"},
        "sending": {"engaged", "waiting", "interrupted", "abandoned"},
        "engaged": {"closing", "cooldown", "interrupted"},
        "closing": {"cooldown", "interrupted"},
        "cooldown": {"candidate", "considering", "abandoned"},
        "interrupted": {"candidate", "considering", "abandoned"},
        "abandoned": {"candidate", "considering"},
    }

    def _proactive_social_sessions(self) -> dict[str, dict[str, Any]]:
        """返回按会话保存的主动社交状态。"""
        sessions = getattr(self.context, "_proactive_social_lifecycle", None)
        if not isinstance(sessions, dict):
            sessions = {}
            self.context._proactive_social_lifecycle = sessions
        return sessions

    def _proactive_lifecycle_snapshot(self, key: str) -> dict[str, Any]:
        """获取会话当前主动社交状态的副本。

        Args:
            key: 会话作用域键。

        Returns:
            当前生命周期快照；尚无记录时返回空字典。
        """
        state = self.context._proactive_social_sessions().get(str(key or ""))
        return dict(state) if isinstance(state, dict) else {}

    def _transition_proactive_lifecycle(
        self,
        key: str,
        state: str,
        *,
        event: str,
        reason: str,
        now: datetime.datetime | None = None,
        revision: int = 0,
        candidate: dict[str, Any] | None = None,
        publish: bool = True,
    ) -> dict[str, Any]:
        """根据结构化事件推进主动社交生命周期。

        Args:
            key: 会话作用域键。
            state: 目标生命周期状态。
            event: 触发转移的结构化事件名。
            reason: 转移原因。
            now: 转移时间。
            revision: 候选修订号。
            candidate: 可选候选对象，用于同步其局部状态。
            publish: 是否更新会话级最新状态。

        Returns:
            转移后的生命周期记录。

        Raises:
            ValueError: 状态或状态转移不合法。
        """
        key = str(key or "").strip()
        state = str(state or "").strip()
        if state not in self.context._PROACTIVE_LIFECYCLE_TRANSITIONS:
            raise ValueError(f"未知主动社交状态：{state}")
        sessions = self.context._proactive_social_sessions()
        current = sessions.get(key) if key else None
        current_state = str((current or {}).get("state") or "")
        allowed = self.context._PROACTIVE_LIFECYCLE_TRANSITIONS.get(current_state, set())
        if state != current_state and state not in allowed:
            raise ValueError(
                f"主动社交状态不能从 {current_state or '初始'} 转为 {state}"
            )
        changed_at = now or datetime.datetime.now()
        history = list((current or {}).get("history") or [])[-19:]
        if state != current_state or event != str((current or {}).get("event") or ""):
            history.append(
                {
                    "from": current_state,
                    "to": state,
                    "event": str(event or ""),
                    "reason": str(reason or ""),
                    "revision": int(revision or 0),
                    "at": changed_at,
                }
            )
        record = {
            "key": key,
            "state": state,
            "previous_state": current_state,
            "event": str(event or ""),
            "reason": str(reason or ""),
            "revision": int(revision or 0),
            "updated_at": changed_at,
            "history": history,
        }
        if isinstance(candidate, dict):
            candidate["lifecycle_state"] = state
            candidate["lifecycle_event"] = str(event or "")
            candidate["lifecycle_reason"] = str(reason or "")
            candidate["lifecycle_updated_at"] = changed_at
        if key and publish:
            sessions[key] = record
        return record

    @staticmethod
    def _proactive_score(value: Any) -> tuple[int, bool]:
        """把模型评分规范到 0 至 100。"""
        if isinstance(value, bool):
            return 0, False
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0, False
        if not math.isfinite(score):
            return 0, False
        return max(0, min(100, round(score))), 0 <= score <= 100

    def _normalize_proactive_utility(
        self,
        payload: dict[str, Any],
    ) -> tuple[int, bool]:
        """五项评分必须完整有效；缺项时主动发送裁定无效。"""
        valid = True
        for field in self.context._PROACTIVE_UTILITY_FIELDS:
            field_present = field in payload
            score, field_valid = self.context._proactive_score(payload.get(field))
            payload[field] = score
            valid = valid and field_present and field_valid
        utility = (
            int(payload["benefit"])
            + int(payload["timeliness"])
            + int(payload["continuity"])
            - int(payload["disruption"])
            - int(payload["uncertainty"])
        )
        payload["utility"] = utility
        payload["utility_threshold"] = self.context._PROACTIVE_UTILITY_THRESHOLD
        payload["utility_scores_valid"] = valid
        return utility, valid
