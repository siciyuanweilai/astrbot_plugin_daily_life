from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class VoiceCallTranscriptTurn:
    """实时通话中的一条已归并发言。"""

    role: str
    text: str = ""
    upstream_id: str = ""
    finalized: bool = False
    interrupted: bool = False


class VoiceCallTranscriptMixin:
    """归并 WebSocket 与 RTC 通话共用的实时转写事件。"""

    @classmethod
    def _begin_transcript_turn(
        cls,
        invite: Any,
        role: str,
        event: dict[str, Any],
    ) -> VoiceCallTranscriptTurn:
        upstream_id = cls._transcript_upstream_id(event)
        latest = invite.transcript_turns[-1] if invite.transcript_turns else None
        if latest and latest.role != role and not latest.finalized:
            latest.interrupted = latest.role == "assistant"
            latest.finalized = True
        if latest and latest.role == role and not latest.finalized:
            if not latest.text or not upstream_id or latest.upstream_id == upstream_id:
                return latest
            latest.finalized = True
        turn = VoiceCallTranscriptTurn(role=role, upstream_id=upstream_id)
        invite.transcript_turns.append(turn)
        return turn

    @classmethod
    def _update_transcript_turn(
        cls,
        invite: Any,
        role: str,
        event: dict[str, Any],
        *,
        finalized: bool = False,
    ) -> None:
        upstream_id = cls._transcript_upstream_id(event)
        latest = invite.transcript_turns[-1] if invite.transcript_turns else None
        if latest and latest.role != role and not latest.finalized:
            latest.interrupted = latest.role == "assistant"
            latest.finalized = True
        turn = cls._find_transcript_turn(
            invite,
            role,
            upstream_id,
            allow_finalized=finalized,
        )
        if turn is None:
            turn = VoiceCallTranscriptTurn(role=role, upstream_id=upstream_id)
            invite.transcript_turns.append(turn)
        elif upstream_id and not turn.upstream_id:
            turn.upstream_id = upstream_id
        if finalized:
            incoming = event.get("text") or event.get("transcript") or event.get("delta")
        else:
            incoming = event.get("delta") or event.get("text") or event.get("transcript")
        if finalized and str(incoming or "").strip():
            turn.text = cls._deduplicate_transcript_text(incoming)
            turn.interrupted = False
        else:
            turn.text = cls._deduplicate_transcript_text(
                cls._merge_transcript_text(turn.text, incoming)
            )
        if finalized:
            turn.finalized = True
        cls._sync_legacy_transcripts(invite)

    @staticmethod
    def _find_transcript_turn(
        invite: Any,
        role: str,
        upstream_id: str,
        *,
        allow_finalized: bool,
    ) -> VoiceCallTranscriptTurn | None:
        for turn in reversed(invite.transcript_turns):
            if turn.role != role:
                continue
            if upstream_id and turn.upstream_id and turn.upstream_id != upstream_id:
                continue
            if not turn.finalized or (allow_finalized and upstream_id):
                return turn
        return None

    @staticmethod
    def _transcript_upstream_id(event: dict[str, Any]) -> str:
        for key in ("item_id", "response_id", "conversation_item_id"):
            value = str(event.get(key) or "").strip()
            if value:
                return value[:160]
        for container_key in ("item", "response"):
            container = event.get(container_key)
            if not isinstance(container, dict):
                continue
            value = str(container.get("id") or "").strip()
            if value:
                return value[:160]
        return ""

    @classmethod
    def _merge_transcript_text(cls, current: Any, value: Any) -> str:
        """兼容真实增量与“截至当前全文”两类上游转写事件。"""

        previous = str(current or "").strip()
        incoming = str(value or "").strip()
        if not incoming:
            return previous
        if not previous:
            return incoming
        previous_normalized = cls._normalized_transcript_text(previous)
        incoming_normalized = cls._normalized_transcript_text(incoming)
        if previous_normalized and incoming_normalized:
            if incoming_normalized == previous_normalized:
                return previous
            if previous_normalized.startswith(incoming_normalized):
                return previous
            if incoming_normalized.startswith(previous_normalized):
                repeated_tail = incoming_normalized[len(previous_normalized) :]
                if repeated_tail.startswith(previous_normalized):
                    while incoming_normalized.startswith(previous_normalized):
                        incoming_normalized = incoming_normalized[len(previous_normalized) :]
                    return previous + incoming_normalized
                return incoming
            shared_prefix = cls._shared_prefix_length(
                previous_normalized,
                incoming_normalized,
            )
            shortest = min(len(previous_normalized), len(incoming_normalized))
            if shared_prefix >= 12 and shared_prefix * 2 >= shortest:
                return incoming
        if incoming == previous or previous.endswith(incoming):
            return previous
        if incoming.startswith(previous):
            return incoming
        if previous.startswith(incoming) or incoming in previous:
            return previous
        overlap = min(len(previous), len(incoming))
        while overlap and not previous.endswith(incoming[:overlap]):
            overlap -= 1
        return previous + incoming[overlap:]

    @staticmethod
    def _normalized_transcript_text(value: Any) -> str:
        """供 ASR 去重使用，忽略空白和标点造成的同句差异。"""

        return "".join(char for char in str(value or "") if char.isalnum())

    @staticmethod
    def _shared_prefix_length(left: str, right: str) -> int:
        """返回两段规范化文本的公共前缀长度。"""

        length = min(len(left), len(right))
        index = 0
        while index < length and left[index] == right[index]:
            index += 1
        return index

    @classmethod
    def _deduplicate_transcript_text(cls, value: Any) -> str:
        """清除上游对同一轮文本的长片段重放。"""

        text = str(value or "").strip()
        while True:
            normalized, source_positions = cls._normalized_text_positions(text)
            replay_start = cls._replayed_snapshot_start(normalized)
            if replay_start is not None:
                text = text[source_positions[replay_start] :].strip()
                continue
            repeated_length = 0
            for length in range(len(normalized) // 2, 7, -1):
                if normalized[-2 * length : -length] == normalized[-length:]:
                    repeated_length = length
                    break
            if not repeated_length:
                return text
            repeat_start = source_positions[-repeated_length]
            collapsed = text[:repeat_start].rstrip()
            if collapsed == text:
                return text
            text = collapsed

    @staticmethod
    def _normalized_text_positions(value: str) -> tuple[str, list[int]]:
        normalized_chars: list[str] = []
        source_positions: list[int] = []
        for index, char in enumerate(value):
            if char.isalnum():
                normalized_chars.append(char)
                source_positions.append(index)
        return "".join(normalized_chars), source_positions

    @classmethod
    def _replayed_snapshot_start(cls, normalized: str) -> int | None:
        """找出从句首重放的后一份完整快照起点。"""

        minimum = 12
        total = len(normalized)
        if total < minimum * 2:
            return None
        maximum = min(96, total // 2)
        for prefix_length in range(maximum, minimum - 1, -1):
            second_start = normalized.find(normalized[:prefix_length], prefix_length)
            if second_start < prefix_length:
                continue
            if total - second_start < prefix_length:
                continue
            return second_start
        return None

    @staticmethod
    def _sync_legacy_transcripts(invite: Any) -> None:
        for role, text_field, finalized_field in (
            ("user", "user_transcript", "user_transcript_finalized"),
            ("assistant", "bot_transcript", "bot_transcript_finalized"),
        ):
            turns = [turn for turn in invite.transcript_turns if turn.role == role]
            setattr(invite, text_field, "\n".join(turn.text for turn in turns if turn.text))
            setattr(invite, finalized_field, bool(turns and turns[-1].finalized))
