from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal


class BackgroundTextMode(str, Enum):
    DIRECT = "direct"
    EXPRESSIVE = "expressive"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status: Literal["sent", "cancelled", "skipped", "failed"]
    sent_count: int = 0
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class EventDeliveryRequest:
    event: Any
    texts: tuple[str, ...]
    scope: str
    match: Literal["exact", "joined"]
    text_from_item: Callable[[Any], str]
    build_message: Callable[[int, Sequence[Any]], Any]
    delay_seconds: Callable[[int], float]
    sleep: Callable[[float], Awaitable[None]]
    is_current: Callable[[], bool]
    source: str = "chat"


@dataclass(frozen=True, slots=True)
class ScopeDeliveryRequest:
    scope: str
    texts: tuple[str, ...]
    build_message: Callable[[int], Any]
    delay_seconds: Callable[[int], float]
    sleep: Callable[[float], Awaitable[None]]
    is_current: Callable[[], bool]
    send: Callable[[Any], Awaitable[bool]]
    on_sent: Callable[[int, str], None]
    source_event: Any = None
    source_message_id: str = ""
    source: str = "background"
    decorate_addressing: bool = True


class ReplyDeliveryService:
    def __init__(self, runtime: Any):
        self._runtime = runtime

    @staticmethod
    def _chain_matches(request: EventDeliveryRequest, chain: Any) -> bool:
        if not isinstance(chain, list) or not chain:
            return False
        current = [request.text_from_item(item) for item in chain]
        if request.match == "exact":
            return len(current) == len(request.texts) and current == list(request.texts)
        return (
            bool(current)
            and all(text.strip() for text in current)
            and ("".join(current) == "".join(request.texts))
        )

    @staticmethod
    def _clear_result(event: Any) -> None:
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()

    async def _send_one(
        self,
        request: EventDeliveryRequest,
        chain: Sequence[Any],
        index: int,
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        message = request.build_message(index, chain)
        decorator = getattr(self._runtime, "decorate_group_addressing_chain", None)
        if callable(decorator):
            decorator(
                message,
                target_scope=request.scope,
                source_event=request.event,
                segment_index=index,
                source=request.source,
            )
        await send(message)

    async def send_event(self, request: EventDeliveryRequest) -> DeliveryResult:
        event = request.event
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not self._chain_matches(request, chain):
            return DeliveryResult("skipped")
        send = getattr(event, "send", None)
        if not callable(send):
            return DeliveryResult("skipped")

        sent_count = 0
        try:
            for index, _text in enumerate(request.texts):
                if index > 0:
                    if not request.is_current():
                        self._clear_result(event)
                        return DeliveryResult("cancelled", sent_count)
                    delay = max(float(request.delay_seconds(index) or 0.0), 0.0)
                    if delay > 0:
                        await request.sleep(delay)
                if not request.is_current():
                    self._clear_result(event)
                    return DeliveryResult("cancelled", sent_count)
                await self._send_one(request, chain, index, send)
                sent_count += 1
        except Exception as exc:
            if sent_count > 0:
                self._clear_result(event)
            return DeliveryResult("failed", sent_count, exc)

        self._clear_result(event)
        return DeliveryResult("sent", sent_count)

    async def send_scope(self, request: ScopeDeliveryRequest) -> DeliveryResult:
        sent_count = 0
        try:
            for index, text in enumerate(request.texts):
                if index > 0:
                    if not request.is_current():
                        return DeliveryResult("cancelled", sent_count)
                    delay = max(float(request.delay_seconds(index) or 0.0), 0.0)
                    if delay > 0:
                        await request.sleep(delay)
                if not request.is_current():
                    return DeliveryResult("cancelled", sent_count)
                message = request.build_message(index)
                if request.decorate_addressing:
                    decorator = getattr(
                        self._runtime, "decorate_group_addressing_chain", None
                    )
                    if callable(decorator):
                        decorator(
                            message,
                            target_scope=request.scope,
                            source_event=request.source_event,
                            source_message_id=request.source_message_id,
                            segment_index=index,
                            source=request.source,
                        )
                if not await request.send(message):
                    return DeliveryResult(
                        "failed",
                        sent_count,
                        RuntimeError("消息发送未完成"),
                    )
                request.on_sent(index, text)
                sent_count += 1
        except Exception as exc:
            return DeliveryResult("failed", sent_count, exc)
        return DeliveryResult("sent", sent_count)


__all__ = [
    "BackgroundTextMode",
    "DeliveryResult",
    "EventDeliveryRequest",
    "ReplyDeliveryService",
    "ScopeDeliveryRequest",
]
