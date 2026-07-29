from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from astrbot.api import logger

from .markers import LOG_PREFIX


TOOL_REACTION_PROCESSING = 125
TOOL_REACTION_SUCCESS = 79
TOOL_REACTION_FAILED = 106

TOOL_REACTION_NAMES = frozenset(
    {
        "life_image_generate",
        "life_photo_suite_generate",
        "edit_life_image",
        "life_image_reverse_prompt",
        "life_video_generate",
        "life_video_understand",
        "life_video_note",
        "life_web_search",
        "life_web_fetch",
        "life_web_map",
        "life_web_crawl",
        "life_web_research",
        "life_web_research_status",
    }
)

BACKGROUND_TOOL_REACTION_NAMES = frozenset(
    {
        "life_photo_suite_generate",
        "life_video_generate",
        "life_web_research",
    }
)
DIRECT_DELIVERY_TOOL_REACTION_NAMES = frozenset(
    {"life_image_generate", "edit_life_image", "life_video_note"}
)

_TOOL_REACTION_STATE_TTL_SECONDS = 30 * 60
_TOOL_REACTION_STATE_LIMIT = 256

_TOOL_MEDIA_CONTRACTS = {
    "life_photo_suite_generate": "photo_suite",
    "life_image_reverse_prompt": "image_reverse_prompt",
    "life_video_understand": "video_understanding",
    "life_video_note": "video_note",
}


class ToolReactionMixin:
    """按用户消息聚合耗时工具状态，并在整轮结束后标记最终结果。"""

    def _tool_reaction_states(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_life_tool_reaction_states", None)
        if not isinstance(store, dict):
            store = {}
            self._life_tool_reaction_states = store
        return store

    def _tool_reaction_sent(self) -> dict[str, set[int]]:
        store = getattr(self, "_life_tool_reaction_sent", None)
        if not isinstance(store, dict):
            store = {}
            self._life_tool_reaction_sent = store
        return store

    @staticmethod
    def _tool_reaction_name(tool: Any) -> str:
        return str(getattr(tool, "name", tool) or "").strip()

    def _tool_reaction_message_id(self, event: Any) -> int | None:
        getter = getattr(self, "_event_message_id", None)
        value = str(getter(event) if callable(getter) else "").strip()
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _tool_reaction_bot(self, event: Any) -> Any | None:
        sources = getattr(self, "_event_sources", None)
        values = sources(event) if callable(sources) else [event]
        for source in values:
            bot = getattr(source, "bot", None)
            if callable(getattr(bot, "set_msg_emoji_like", None)):
                return bot
        return None

    def _tool_reaction_identity(self, event: Any) -> tuple[str, int] | None:
        message_id = self._tool_reaction_message_id(event)
        if message_id is None:
            return None
        scope_getter = getattr(self, "_event_session_id", None)
        scope = str(scope_getter(event) if callable(scope_getter) else "").strip()
        return scope, message_id

    def _tool_reaction_key(self, event: Any, tool_name: str = "") -> str:
        del tool_name
        identity = self._tool_reaction_identity(event)
        if identity is None:
            return ""
        scope, message_id = identity
        return f"{scope}\n{message_id}"

    def _tool_reaction_was_recalled(self, event: Any) -> bool:
        checker = getattr(self, "_event_message_was_recalled", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(event))
        except Exception:
            return False

    @staticmethod
    def _new_tool_reaction_state() -> dict[str, Any]:
        now = time.monotonic()
        return {
            "status": "processing",
            "started": 0,
            "active": 0,
            "pending_background": 0,
            "successes": 0,
            "failures": 0,
            "cancelled": 0,
            "tools": {},
            "requires_reply": False,
            "has_background": False,
            "agent_done": False,
            "agent_has_reply": False,
            "agent_failed": False,
            "reply_delivered": False,
            "finalized": False,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _touch_tool_reaction_state(state: dict[str, Any]) -> None:
        state["updated_at"] = time.monotonic()

    @staticmethod
    def _tool_reaction_tool_state(
        state: dict[str, Any], tool_name: str
    ) -> dict[str, int]:
        tools = state.setdefault("tools", {})
        current = tools.get(tool_name)
        if not isinstance(current, dict):
            current = {
                "started": 0,
                "active": 0,
                "pending": 0,
                "successes": 0,
                "failures": 0,
                "cancelled": 0,
            }
            tools[tool_name] = current
        return current

    async def _set_tool_reaction(self, event: Any, emoji_id: int) -> bool:
        identity = self._tool_reaction_identity(event)
        if identity is None or self._tool_reaction_was_recalled(event):
            return False
        bot = self._tool_reaction_bot(event)
        if bot is None:
            return False
        scope, message_id = identity
        sent_key = f"{scope}\n{message_id}"
        sent = self._tool_reaction_sent().setdefault(sent_key, set())
        if emoji_id in sent:
            return True
        try:
            await asyncio.wait_for(
                bot.set_msg_emoji_like(
                    message_id=message_id,
                    emoji_id=int(emoji_id),
                    emoji_type="1",
                    set=True,
                ),
                timeout=3.0,
            )
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 工具状态表情标记失败："
                f"消息={message_id}；表情={emoji_id}；原因={exc}"
            )
            return False
        sent.add(int(emoji_id))
        self._prune_tool_reaction_state()
        logger.debug(
            f"{LOG_PREFIX} 工具状态表情已标记：消息={message_id}；表情={emoji_id}"
        )
        return True

    def _prune_tool_reaction_state(self) -> None:
        states = self._tool_reaction_states()
        sent = self._tool_reaction_sent()
        deadline = time.monotonic() - _TOOL_REACTION_STATE_TTL_SECONDS
        expired = [
            key
            for key, state in states.items()
            if float(state.get("updated_at") or 0.0) < deadline
        ]
        for key in expired:
            states.pop(key, None)
            sent.pop(key, None)
        while len(states) > _TOOL_REACTION_STATE_LIMIT:
            key = next(iter(states))
            states.pop(key, None)
            sent.pop(key, None)
        while len(sent) > _TOOL_REACTION_STATE_LIMIT:
            sent.pop(next(iter(sent)), None)

    async def note_tool_reaction_start(
        self, event: Any, tool: Any, tool_args: dict | None = None
    ) -> bool:
        tool_name = self._tool_reaction_name(tool)
        if tool_name not in TOOL_REACTION_NAMES:
            return False
        key = self._tool_reaction_key(event)
        if not key:
            return False
        states = self._tool_reaction_states()
        state = states.get(key)
        if not isinstance(state, dict) or state.get("finalized"):
            state = self._new_tool_reaction_state()
            states[key] = state
        tool_state = self._tool_reaction_tool_state(state, tool_name)
        tool_state["started"] += 1
        tool_state["active"] += 1
        state["started"] += 1
        state["active"] += 1
        state["agent_done"] = False
        state["status"] = "processing"
        if tool_name not in (
            BACKGROUND_TOOL_REACTION_NAMES | DIRECT_DELIVERY_TOOL_REACTION_NAMES
        ):
            state["requires_reply"] = True
        if tool_name in BACKGROUND_TOOL_REACTION_NAMES:
            state["has_background"] = True
        self._touch_tool_reaction_state(state)
        self._prune_tool_reaction_state()
        return await self._set_tool_reaction(event, TOOL_REACTION_PROCESSING)

    async def note_tool_reaction_result(
        self,
        event: Any,
        tool: Any,
        tool_args: dict | None = None,
        tool_result: Any = None,
    ) -> bool:
        del tool_args
        tool_name = self._tool_reaction_name(tool)
        if tool_name not in TOOL_REACTION_NAMES:
            return False
        key = self._tool_reaction_key(event)
        state = self._tool_reaction_states().get(key)
        if not isinstance(state, dict) or state.get("finalized"):
            return False
        tool_state = self._tool_reaction_tool_state(state, tool_name)
        if tool_state["active"] <= 0:
            return False
        tool_state["active"] -= 1
        state["active"] = max(int(state.get("active") or 0) - 1, 0)
        outcome = self._tool_reaction_outcome(tool_name, tool_result)
        if outcome == "pending":
            tool_state["pending"] += 1
            state["pending_background"] += 1
            state["status"] = "pending"
        else:
            self._record_tool_reaction_outcome(state, tool_state, outcome)
        self._touch_tool_reaction_state(state)
        return await self._try_finish_tool_reaction(event, state)

    @staticmethod
    def _record_tool_reaction_outcome(
        state: dict[str, Any], tool_state: dict[str, int], outcome: str
    ) -> None:
        if outcome == "success":
            tool_state["successes"] += 1
            state["successes"] += 1
        elif outcome == "failed":
            tool_state["failures"] += 1
            state["failures"] += 1
        elif outcome == "cancelled":
            tool_state["cancelled"] += 1
            state["cancelled"] += 1

    @staticmethod
    def _tool_reaction_response_has_content(response: Any) -> bool:
        completion = str(getattr(response, "completion_text", "") or "").strip()
        if completion:
            return True
        result_chain = getattr(response, "result_chain", None)
        chain = getattr(result_chain, "chain", result_chain)
        return bool(chain)

    async def note_tool_reaction_agent_done(
        self, event: Any, response: Any = None
    ) -> bool:
        key = self._tool_reaction_key(event)
        state = self._tool_reaction_states().get(key)
        if not isinstance(state, dict) or state.get("finalized"):
            return False
        state["agent_done"] = True
        state["agent_has_reply"] = self._tool_reaction_response_has_content(response)
        role = str(getattr(response, "role", "") or "").strip().lower()
        state["agent_failed"] = role in {"err", "error"} or bool(
            getattr(response, "is_error", False)
        )
        self._touch_tool_reaction_state(state)
        return await self._try_finish_tool_reaction(event, state)

    async def note_tool_reaction_message_sent(self, event: Any) -> bool:
        key = self._tool_reaction_key(event)
        state = self._tool_reaction_states().get(key)
        if not isinstance(state, dict) or state.get("finalized"):
            return False
        if not state.get("agent_done"):
            return False
        state["reply_delivered"] = True
        self._touch_tool_reaction_state(state)
        return await self._try_finish_tool_reaction(event, state)

    async def finish_tool_reaction(
        self, event: Any, tool_name: str, *, success: bool
    ) -> bool:
        tool_name = str(tool_name or "").strip()
        if tool_name not in BACKGROUND_TOOL_REACTION_NAMES:
            return False
        key = self._tool_reaction_key(event)
        state = self._tool_reaction_states().get(key)
        if not isinstance(state, dict) or state.get("finalized"):
            return False
        tool_state = self._tool_reaction_tool_state(state, tool_name)
        if tool_state["pending"] <= 0:
            return False
        tool_state["pending"] -= 1
        state["pending_background"] = max(
            int(state.get("pending_background") or 0) - 1, 0
        )
        self._record_tool_reaction_outcome(
            state, tool_state, "success" if success else "failed"
        )
        self._touch_tool_reaction_state(state)
        return await self._try_finish_tool_reaction(event, state)

    def cancel_tool_reaction(self, event: Any, tool_name: str) -> None:
        tool_name = str(tool_name or "").strip()
        key = self._tool_reaction_key(event)
        state = self._tool_reaction_states().get(key)
        if not isinstance(state, dict) or state.get("finalized"):
            return
        tool_state = self._tool_reaction_tool_state(state, tool_name)
        if tool_state["pending"] > 0:
            tool_state["pending"] -= 1
            state["pending_background"] = max(
                int(state.get("pending_background") or 0) - 1, 0
            )
        elif tool_state["active"] > 0:
            tool_state["active"] -= 1
            state["active"] = max(int(state.get("active") or 0) - 1, 0)
        else:
            return
        self._record_tool_reaction_outcome(state, tool_state, "cancelled")
        self._touch_tool_reaction_state(state)
        if (
            state.get("agent_done")
            and not state.get("active")
            and not state.get("pending_background")
            and not state.get("successes")
            and not state.get("failures")
        ):
            self._tool_reaction_states().pop(key, None)

    async def _try_finish_tool_reaction(
        self, event: Any, state: dict[str, Any]
    ) -> bool:
        if state.get("finalized") or not state.get("agent_done"):
            return False
        if int(state.get("active") or 0) > 0:
            return False
        if int(state.get("pending_background") or 0) > 0:
            return False
        wait_for_reply = state.get("requires_reply") or not state.get("has_background")
        if (
            wait_for_reply
            and state.get("agent_has_reply")
            and not state.get("reply_delivered")
        ):
            return False
        successes = int(state.get("successes") or 0)
        failures = int(state.get("failures") or 0)
        if successes <= 0 and failures <= 0:
            key = self._tool_reaction_key(event)
            self._tool_reaction_states().pop(key, None)
            return False
        success = (
            successes > 0
            and not state.get("agent_failed")
            and not (state.get("requires_reply") and not state.get("agent_has_reply"))
        )
        state["status"] = "success" if success else "failed"
        state["finalized"] = True
        self._touch_tool_reaction_state(state)
        emoji_id = TOOL_REACTION_SUCCESS if success else TOOL_REACTION_FAILED
        logger.debug(
            f"{LOG_PREFIX} 工具状态整轮结算：调用={state.get('started', 0)}；"
            f"成功={successes}；失败={failures}；取消={state.get('cancelled', 0)}；"
            f"智能体={'失败' if state.get('agent_failed') else '完成'}；"
            f"结果={'成功' if success else '失败'}"
        )
        return await self._set_tool_reaction(event, emoji_id)

    @classmethod
    def _tool_reaction_outcome(cls, tool_name: str, value: Any) -> str:
        if isinstance(value, BaseException):
            return "failed"
        if bool(getattr(value, "isError", False) or getattr(value, "is_error", False)):
            return "failed"
        structured_status = str(getattr(value, "status", "") or "").strip().lower()
        structured_media = str(getattr(value, "media", "") or "").strip().lower()
        contract_media = _TOOL_MEDIA_CONTRACTS.get(tool_name)
        if contract_media and structured_media == contract_media:
            return cls._tool_reaction_structured_outcome(structured_status)
        raw = cls._tool_reaction_result_value(value)
        payload = cls._tool_reaction_json_payload(raw)

        if tool_name in BACKGROUND_TOOL_REACTION_NAMES:
            return cls._background_tool_reaction_outcome(payload)

        if tool_name in {"life_image_generate", "edit_life_image"}:
            return cls._image_tool_reaction_outcome(raw, payload)

        if tool_name in _TOOL_MEDIA_CONTRACTS:
            return "failed"

        if tool_name == "life_web_research_status":
            return cls._research_status_tool_reaction_outcome(payload)

        if tool_name in {
            "life_web_search",
            "life_web_fetch",
            "life_web_map",
            "life_web_crawl",
        }:
            return cls._web_tool_reaction_outcome(payload)

        return ""

    @staticmethod
    def _background_tool_reaction_outcome(
        payload: dict[str, Any] | None,
    ) -> str:
        if isinstance(payload, dict) and payload.get("status") == "pending":
            return "pending"
        return "failed"

    @staticmethod
    def _image_tool_reaction_outcome(
        raw: str, payload: dict[str, Any] | None
    ) -> str:
        if isinstance(payload, dict) and payload.get("status") == "sent":
            return "success"
        if raw.startswith("原消息已撤回"):
            return "cancelled"
        return "failed"

    @staticmethod
    def _research_status_tool_reaction_outcome(
        payload: dict[str, Any] | None,
    ) -> str:
        if not isinstance(payload, dict):
            return "failed"
        return (
            "success"
            if payload.get("status") in {"pending", "completed", "ok"}
            else "failed"
        )

    @staticmethod
    def _web_tool_reaction_outcome(payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return "failed"
        return "success" if payload.get("status") == "ok" else "failed"

    @staticmethod
    def _tool_reaction_structured_outcome(status: str) -> str:
        if status in {"ok", "sent", "completed"}:
            return "success"
        if status == "pending":
            return "pending"
        if status == "cancelled":
            return "cancelled"
        return "failed"

    @staticmethod
    def _tool_reaction_result_value(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, (list, tuple)):
            return ToolReactionMixin._tool_reaction_sequence_text(value)
        for attr in ("completion_text", "content", "text", "result"):
            current = getattr(value, attr, None)
            text = ToolReactionMixin._tool_reaction_nested_value(current)
            if text:
                return text
        return ""

    @staticmethod
    def _tool_reaction_sequence_text(value: list[Any] | tuple[Any, ...]) -> str:
        texts: list[str] = []
        for item in value:
            current = item.get("text") if isinstance(item, dict) else getattr(
                item, "text", None
            )
            if isinstance(current, str) and current.strip():
                texts.append(current.strip())
        return "\n".join(texts)

    @staticmethod
    def _tool_reaction_nested_value(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, (list, tuple)):
            return ToolReactionMixin._tool_reaction_sequence_text(value)
        return ""

    @staticmethod
    def _tool_reaction_json_payload(value: str) -> dict[str, Any] | None:
        text = str(value or "").strip()
        if not text.startswith("{") or not text.endswith("}"):
            return None
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None


__all__ = [
    "BACKGROUND_TOOL_REACTION_NAMES",
    "DIRECT_DELIVERY_TOOL_REACTION_NAMES",
    "TOOL_REACTION_FAILED",
    "TOOL_REACTION_NAMES",
    "TOOL_REACTION_PROCESSING",
    "TOOL_REACTION_SUCCESS",
    "ToolReactionMixin",
]
