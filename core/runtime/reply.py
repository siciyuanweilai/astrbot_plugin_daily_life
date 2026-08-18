from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain

from ..media.base import normalize_voice_style
from ..prompts import CORE_EMOJI_DELIVERY_RULES, cache_friendly_prompt
from .delivery import (
    BackgroundTextMode,
    EventDeliveryRequest,
    ReplyDeliveryService,
    ScopeDeliveryRequest,
)
from .markers import LOG_PREFIX


@dataclass(frozen=True, slots=True)
class SegmentPart:
    text: str
    relation: str = "standalone"
    pause: str = "none"


@dataclass(frozen=True, slots=True)
class SemanticSegmentPlan:
    segments: tuple[SegmentPart, ...]
    source: str = "semantic"
    valid: bool = True
    channel: str = "text"
    emotion: str = ""
    emotion_category: str = "neutral"
    voice_style: str = "neutral"
    emoji_intent: str = ""
    send_emoji: bool = False
    stance: str = "respond"
    confidence: float = 0.0
    reason: str = ""

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments)


class SemanticSegmentRuntimeMixin:
    """模型语义分段与可中断发送。

    该层不对正文做标点或关键词切分。语义分段器只能返回原文的完整分段，
    校验失败时保持单段发送。
    """

    _SEMANTIC_SEGMENT_PENDING_ATTR = "_daily_life_semantic_segment_pending"
    _SEMANTIC_SEGMENT_PLAN_ATTR = "_daily_life_semantic_segment_plan"
    _SEMANTIC_SEGMENT_SCOPE_ATTR = "_daily_life_semantic_segment_scope"
    _SEMANTIC_SEGMENT_ATTEMPTED_ATTR = "_daily_life_semantic_segment_attempted"

    def _semantic_segment_init_state(self) -> None:
        self._semantic_segment_revisions: dict[str, int] = {}
        self._semantic_segment_epochs: dict[str, int] = {}
        self._semantic_segment_metrics: dict[str, int] = {
            "segmented": 0,
            "planning_failed": 0,
            "fallback_single": 0,
            "fallback_natural": 0,
            "sent": 0,
            "cancelled": 0,
            "failed": 0,
        }
        self._chat_pacing_state: dict[str, dict[str, Any]] = {}

    def _semantic_segment_enabled(self) -> bool:
        if not isinstance(getattr(self, "_semantic_segment_metrics", None), dict):
            return False
        checker = getattr(self, "_chat_style_enabled", None)
        if callable(checker):
            return bool(checker())
        settings = getattr(getattr(self, "config", None), "chat_style", None)
        return bool(settings and getattr(settings, "enabled", False))

    @staticmethod
    def _semantic_segment_scope_from_event(event: Any) -> str:
        value = getattr(event, "unified_msg_origin", "") if event is not None else ""
        return str(value or "").strip()

    def _semantic_segment_revision(self, scope: str) -> int:
        return int(getattr(self, "_semantic_segment_revisions", {}).get(scope, 0))

    def _semantic_segment_epoch(self, scope: str) -> int:
        return int(getattr(self, "_semantic_segment_epochs", {}).get(scope, 0))

    def note_semantic_segment_incoming_message(self, event: Any) -> None:
        if not self._semantic_segment_enabled():
            return
        scope = self._semantic_segment_scope_from_event(event)
        if not scope:
            return
        revisions = getattr(self, "_semantic_segment_revisions", None)
        if not isinstance(revisions, dict):
            self._semantic_segment_init_state()
            revisions = self._semantic_segment_revisions
        revisions[scope] = int(revisions.get(scope, 0)) + 1
        now_value = time.monotonic()
        pacing = self._chat_pacing_state.setdefault(scope, {})
        previous = pacing.get("incoming_at")
        pacing["incoming_at"] = now_value
        if isinstance(previous, (int, float)) and now_value > previous:
            interval = min(max(now_value - previous, 0.2), 120.0)
            old = float(pacing.get("interval_ema") or interval)
            pacing["interval_ema"] = old * 0.7 + interval * 0.3

    def _note_chat_pacing_effect(self, scope: str, outcome: str) -> None:
        if not scope:
            return
        state = getattr(self, "_chat_pacing_state", None)
        if not isinstance(state, dict):
            state = {}
            self._chat_pacing_state = state
        pacing = state.setdefault(scope, {})
        current = float(pacing.get("effect") or 0.0)
        target = (
            1.0 if outcome == "positive" else (-1.0 if outcome == "negative" else 0.0)
        )
        pacing["effect"] = max(-1.0, min(current * 0.65 + target * 0.35, 1.0))

    def _semantic_expression_plan_from_event(
        self, event: Any
    ) -> SemanticSegmentPlan | None:
        plan = getattr(event, self._SEMANTIC_SEGMENT_PLAN_ATTR, None)
        return plan if isinstance(plan, SemanticSegmentPlan) and plan.valid else None

    @staticmethod
    def _semantic_segment_text(item: Any) -> str:
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            kind = str(item.get("type") or item.get("kind") or "text").strip().lower()
            return (
                str(item.get("text") or item.get("content") or "")
                if kind in {"", "text", "plain"}
                else ""
            )
        return str(getattr(item, "text", "") or getattr(item, "content", "") or "")

    @staticmethod
    def _semantic_segment_copy(item: Any, text: str) -> Any:
        if isinstance(item, str):
            return text
        if isinstance(item, dict):
            copied = dict(item)
            if "content" in copied and "text" not in copied:
                copied["content"] = text
            else:
                copied["text"] = text
            return copied
        try:
            import copy

            copied = copy.deepcopy(item)
            if hasattr(copied, "text"):
                copied.text = text
            elif hasattr(copied, "content"):
                copied.content = text
            return copied
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 分段消息组件复制失败：{type(exc).__name__}",
                exc_info=True,
            )
            return text

    @staticmethod
    def _semantic_segment_parse_payload(raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]).strip()
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _semantic_segment_normalize_source_text(text: str) -> str:
        raw = str(text or "").strip()
        lines = raw.splitlines()
        if len(lines) <= 1:
            return raw
        return "".join(line.strip() for line in lines if line.strip())

    @staticmethod
    def _semantic_segment_source_lines(text: str) -> tuple[str, ...]:
        return tuple(
            line.strip()
            for line in str(text or "").strip().splitlines()
            if line.strip()
        )

    @staticmethod
    def _semantic_segment_replace_parts(
        plan: SemanticSegmentPlan,
        segments: tuple[SegmentPart, ...],
    ) -> SemanticSegmentPlan:
        return SemanticSegmentPlan(
            segments,
            source=plan.source,
            valid=plan.valid,
            channel=plan.channel,
            emotion=plan.emotion,
            emotion_category=plan.emotion_category,
            voice_style=plan.voice_style,
            emoji_intent=plan.emoji_intent,
            send_emoji=plan.send_emoji,
            stance=plan.stance,
            confidence=plan.confidence,
            reason=plan.reason,
        )

    @classmethod
    def _semantic_segment_limit_delivery_count(
        cls,
        plan: SemanticSegmentPlan,
        max_segments: int,
    ) -> SemanticSegmentPlan:
        """将超出数量限制的语义分段合并到最后一条待发送消息。

        Args:
            plan: 限制发送数量前的有效语义分段方案。
            max_segments: 允许发送的文本消息数量上限。

        Returns:
            已限制发送数量且保持可读边界的语义分段方案。
        """
        limit = max(1, int(max_segments or 1))
        if len(plan.segments) <= limit:
            return plan
        head = list(plan.segments[: limit - 1])
        tail = list(plan.segments[limit - 1 :])
        merged_text = tail[0].text
        for segment in tail[1:]:
            right_text = str(segment.text or "")
            if (
                merged_text
                and right_text
                and not merged_text[-1].isspace()
                and not right_text[0].isspace()
                and merged_text[-1] not in "，。！？；、：,.!?;:~～…—-"
            ):
                merged_text += " "
            merged_text += segment.text
        head.append(
            SegmentPart(
                merged_text,
                relation=tail[0].relation,
                pause=tail[-1].pause,
            )
        )
        return cls._semantic_segment_replace_parts(plan, tuple(head))

    @classmethod
    def _semantic_segment_respect_source_lines(
        cls,
        plan: SemanticSegmentPlan,
        raw_text: str,
        source_text: str,
        *,
        length_hint: int,
        max_segments: int,
    ) -> SemanticSegmentPlan:
        lines = cls._semantic_segment_source_lines(raw_text)
        if len(lines) <= 1 or "".join(lines) != source_text:
            return plan

        segment_spans: list[tuple[int, int, SegmentPart]] = []
        cursor = 0
        for segment in plan.segments:
            end = cursor + len(segment.text)
            segment_spans.append((cursor, end, segment))
            cursor = end

        short_line_limit = max(12, min(60, int(length_hint or 0)))
        aligned: list[tuple[int, SegmentPart]] = []
        line_start = 0
        for line_index, line in enumerate(lines):
            line_end = line_start + len(line)
            line_parts: list[SegmentPart] = []
            for segment_start, segment_end, segment in segment_spans:
                overlap_start = max(line_start, segment_start)
                overlap_end = min(line_end, segment_end)
                if overlap_start >= overlap_end:
                    continue
                line_parts.append(
                    SegmentPart(
                        source_text[overlap_start:overlap_end],
                        relation=segment.relation,
                        pause=segment.pause,
                    )
                )
            if not line_parts:
                return plan
            if len("".join(line.split())) <= short_line_limit:
                line_parts = [
                    SegmentPart(
                        "".join(part.text for part in line_parts),
                        relation=line_parts[0].relation,
                        pause=line_parts[-1].pause,
                    )
                ]
            if line_index < len(lines) - 1 and line_parts[-1].pause == "none":
                last = line_parts[-1]
                line_parts[-1] = SegmentPart(
                    last.text,
                    relation=last.relation,
                    pause="short",
                )
            aligned.extend((line_index, part) for part in line_parts)
            line_start = line_end

        while len(aligned) > max_segments:
            candidates = [
                (
                    len(aligned[index][1].text) + len(aligned[index + 1][1].text),
                    index,
                )
                for index in range(len(aligned) - 1)
                if aligned[index][0] == aligned[index + 1][0]
            ]
            if not candidates:
                break
            _, merge_index = min(candidates)
            line_index, first = aligned[merge_index]
            _, second = aligned[merge_index + 1]
            aligned[merge_index : merge_index + 2] = [
                (
                    line_index,
                    SegmentPart(
                        first.text + second.text,
                        relation=first.relation,
                        pause=second.pause,
                    ),
                )
            ]

        segments = tuple(part for _, part in aligned)
        if "".join(segment.text for segment in segments) != source_text:
            return plan
        return cls._semantic_segment_replace_parts(plan, segments)

    @staticmethod
    def _semantic_segment_clean_punctuation(text: str, cleanup_chars: str = "") -> str:
        source = str(text or "")
        cleanup_text = str(cleanup_chars or "")
        single_cleanup_set: set[str] = set()
        sequence_cleanup_set: set[str] = set()
        cleanup_index = 0
        while cleanup_index < len(cleanup_text):
            cleanup_end = cleanup_index + 1
            while (
                cleanup_end < len(cleanup_text)
                and cleanup_text[cleanup_end] == cleanup_text[cleanup_index]
            ):
                cleanup_end += 1
            target = (
                sequence_cleanup_set
                if cleanup_end - cleanup_index >= 2
                else single_cleanup_set
            )
            target.add(cleanup_text[cleanup_index])
            cleanup_index = cleanup_end
        cleanup_set = single_cleanup_set | sequence_cleanup_set
        if not cleanup_set:
            return source.strip()
        clean_ascii_pause_runs = "." in sequence_cleanup_set
        cleaned: list[str] = []
        pending_space = False
        index = 0
        while index < len(source):
            char = source[index]
            if char == "." and char in cleanup_set:
                run_end = index + 1
                while run_end < len(source) and source[run_end] == char:
                    run_end += 1
                if run_end - index >= 2:
                    if clean_ascii_pause_runs:
                        following = source[run_end] if run_end < len(source) else ""
                        pending_space = bool(
                            cleaned and not cleaned[-1].isspace() and following.strip()
                        )
                    else:
                        if pending_space and cleaned and not cleaned[-1].isspace():
                            cleaned.append(" ")
                        cleaned.extend(source[index:run_end])
                        pending_space = False
                    index = run_end
                    continue
            if char == "." and char not in single_cleanup_set:
                if pending_space and cleaned and not cleaned[-1].isspace():
                    cleaned.append(" ")
                cleaned.append(char)
                pending_space = False
                index += 1
                continue
            if char in cleanup_set:
                previous = source[index - 1] if index > 0 else ""
                following = source[index + 1] if index + 1 < len(source) else ""
                if (
                    char.isascii()
                    and previous.isascii()
                    and bool(previous.strip())
                    and following.isascii()
                    and bool(following.strip())
                ):
                    if pending_space and cleaned and not cleaned[-1].isspace():
                        cleaned.append(" ")
                    cleaned.append(char)
                    pending_space = False
                    index += 1
                    continue
                pending_space = bool(
                    cleaned and not cleaned[-1].isspace() and following.strip()
                )
                index += 1
                continue
            if char.isspace():
                pending_space = bool(cleaned)
                index += 1
                continue
            if pending_space and cleaned and not cleaned[-1].isspace():
                cleaned.append(" ")
            cleaned.append(char)
            pending_space = False
            index += 1
        return "".join(cleaned).strip()

    def _semantic_segment_clean_plan_punctuation(
        self,
        event: Any,
        plan: SemanticSegmentPlan,
        source_text: str,
    ) -> SemanticSegmentPlan:
        config = self._chat_style_astrbot_send_config(event)
        if not isinstance(config, dict) or "t2i_word_threshold" not in config:
            return plan
        threshold = self._chat_style_int_config(
            config.get("t2i_word_threshold"), 150, minimum=50
        )
        if len(str(source_text or "")) > threshold:
            return plan
        style = getattr(getattr(self, "config", None), "chat_style", None)
        if style is None or not bool(
            getattr(style, "punctuation_cleanup_enabled", True)
        ):
            return plan
        cleanup_chars = str(getattr(style, "punctuation_cleanup_chars", "") or "")
        if not cleanup_chars:
            return plan
        if self._chat_style_text_is_structural(source_text):
            return plan
        segments = tuple(
            SegmentPart(
                text=self._semantic_segment_clean_punctuation(
                    segment.text, cleanup_chars
                )
                or segment.text.strip(),
                relation=segment.relation,
                pause=segment.pause,
            )
            for segment in plan.segments
        )
        return SemanticSegmentPlan(
            segments,
            source=plan.source,
            valid=plan.valid,
            channel=plan.channel,
            emotion=plan.emotion,
            emotion_category=plan.emotion_category,
            voice_style=plan.voice_style,
            emoji_intent=plan.emoji_intent,
            send_emoji=plan.send_emoji,
            stance=plan.stance,
            confidence=plan.confidence,
            reason=plan.reason,
        )

    def _semantic_segment_validate_payload(
        self, payload: dict[str, Any], source_text: str
    ) -> SemanticSegmentPlan | None:
        raw_segments = payload.get("segments")
        if not isinstance(raw_segments, list):
            return None
        if not raw_segments:
            return None
        segments: list[SegmentPart] = []
        for raw in raw_segments:
            if not isinstance(raw, dict):
                return None
            text = raw.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            relation = str(raw.get("relation") or "standalone").strip()
            pause = str(raw.get("pause") or "none").strip()
            if relation not in {
                "standalone",
                "lead",
                "continue",
                "add",
                "turn",
                "question",
                "correction",
                "closing",
            }:
                return None
            if pause not in {"none", "short", "normal", "long"}:
                return None
            segments.append(SegmentPart(text=text, relation=relation, pause=pause))
        segmented_text = "".join(segment.text for segment in segments)
        if segmented_text != source_text:
            return None
        channel = str(payload.get("channel") or "text").strip().lower()
        emotion_category = (
            str(payload.get("emotion_category") or "neutral").strip().lower()
        )
        voice_style = normalize_voice_style(
            payload.get("voice_style"), emotion_category
        )
        stance = str(payload.get("stance") or "respond").strip().lower()
        if channel not in {"text", "voice"}:
            channel = "text"
        if emotion_category not in {"neutral", "happy", "sad", "angry"}:
            emotion_category = "neutral"
        if stance not in {"respond", "comfort", "play", "reflect", "close"}:
            stance = "respond"
        try:
            confidence = max(0.0, min(float(payload.get("confidence") or 0.0), 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return SemanticSegmentPlan(
            tuple(segments),
            source="semantic",
            valid=True,
            channel=channel,
            emotion=str(payload.get("emotion") or "").strip(),
            emotion_category=emotion_category,
            voice_style=voice_style,
            emoji_intent=str(payload.get("emoji_intent") or "").strip(),
            send_emoji=payload.get("send_emoji") is True,
            stance=stance,
            confidence=confidence,
            reason=str(payload.get("reason") or "").strip(),
        )

    async def _semantic_segment_plan_text(
        self,
        text: str,
        *,
        scope: str,
        user_message: str = "",
        source: str = "chat",
        length_hint: int = 0,
    ) -> SemanticSegmentPlan:
        raw_text = str(text or "").strip()
        source_text = (
            self._semantic_segment_normalize_source_text(raw_text)
            if self._semantic_segment_enabled()
            else raw_text
        )
        fallback = (
            SemanticSegmentPlan(
                (SegmentPart(source_text),),
                source="fallback",
                valid=False,
            )
            if source_text
            else SemanticSegmentPlan(())
        )
        if not source_text or not self._semantic_segment_enabled():
            return fallback
        settings = getattr(self.config, "chat_style", None)
        provider_id = str(getattr(settings, "semantic_provider", "") or "").strip()
        max_segments = max(1, int(getattr(settings, "semantic_max_segments", 10) or 10))
        source_lines = self._semantic_segment_source_lines(raw_text)
        try:
            provider = await self.get_text_provider(provider_id)
            if not provider:
                return fallback
            fixed = (
                "你负责按语义划分回复分段。只把给定的最终回复原文划分为可直接发送的完整分段，不能改写、增删、纠正或调换任何字符。\n"
                "这是通用模型语义分段协议，不要根据关键词套模板，也不要解释理由。\n"
                '返回 JSON：{"segments":[{"text":"原文连续片段","relation":"standalone|lead|continue|add|turn|question|correction|closing","pause":"none|short|normal|long"}],"channel":"text|voice","emotion":"自然情绪或空","emotion_category":"neutral|happy|sad|angry","voice_style":"neutral|happy|light|sad|angry","emoji_intent":"可选表情语义或空","send_emoji":false,"stance":"respond|comfort|play|reflect|close","confidence":0.0,"reason":"简短表达依据"}。\n'
                "按自然聊天中的独立表达动作划分：每个分段短而完整，只承载一个主要意思。"
                "如果一个分段连续堆叠了多个可以分别发送的意思，应在不破坏语义的位置继续拆开；"
                "不要因为前后相关就合并成长段，也不要把一个不可分的意思拆碎。\n"
                f"最多返回 {max_segments} 个分段；每个 text 必须按原文顺序拼接后完全等于原文。\n"
                + "channel、情绪、语音风格、表情和姿态必须根据整轮语义判断；voice_style 只能填写枚举值，不要从 emotion 文本推导；列表、代码、链接、参数和需要回看的信息应选择文字。\n"
                + f"{CORE_EMOJI_DELIVERY_RULES}\n"
            )
            dynamic = (
                (
                    f"当前场景单个分段参考长度约为 {int(length_hint)} 字；这是自然表达倾向，不是硬性截断，"
                    "超过时优先寻找独立表达动作再拆分。\n"
                    if int(length_hint or 0) > 0
                    else "当前没有额外长度倾向，按自然表达决定分段边界。\n"
                )
                + f"当前对方消息：{user_message}\n"
                + (
                    "原文中的非空换行是已经确定的表达边界，分段不能跨行；"
                    "短行保持完整，较长行仍可在行内继续划分，返回的 text 不包含换行字符。\n"
                    f"原文行布局：{json.dumps(source_lines, ensure_ascii=False)}\n"
                    if len(source_lines) > 1
                    else ""
                )
                + f"回复原文：{source_text}"
            )
            prompt = cache_friendly_prompt(
                fixed,
                dynamic,
                dynamic_title="待分段回复",
            )
            configured_timeout = max(
                1.0,
                min(
                    float(
                        getattr(settings, "semantic_timeout_seconds", 8.0) or 8.0
                    ),
                    20.0,
                ),
            )
            started_at = time.monotonic()
            logger.debug(
                f"{LOG_PREFIX} 模型语义分段开始：超时={configured_timeout:g}秒；长度={len(source_text)}"
            )
            raw = await asyncio.wait_for(
                self.call_text_model(
                    provider,
                    prompt,
                    scope,
                    empty_retries=0,
                    primary_provider_id=provider_id,
                ),
                timeout=configured_timeout,
            )
            logger.debug(
                f"{LOG_PREFIX} 模型语义分段完成：耗时={time.monotonic() - started_at:.2f} 秒"
            )
            payload = self._semantic_segment_parse_payload(raw)
            plan = (
                self._semantic_segment_validate_payload(payload, source_text)
                if payload
                else None
            )
            if plan:
                plan = self._semantic_segment_respect_source_lines(
                    plan,
                    raw_text,
                    source_text,
                    length_hint=length_hint,
                    max_segments=max_segments,
                )
                plan = self._semantic_segment_limit_delivery_count(plan, max_segments)
                self._semantic_segment_metrics["segmented"] = (
                    self._semantic_segment_metrics.get("segmented", 0) + 1
                )
                return plan
            logger.debug(f"{LOG_PREFIX} 模型语义分段返回无效，已改用自然分段")
        except asyncio.TimeoutError:
            logger.debug(f"{LOG_PREFIX} 模型语义分段超时，已改用自然分段")
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 模型语义分段调用异常详情：{type(exc).__name__}: {exc}"
            )
            logger.debug(f"{LOG_PREFIX} 模型语义分段调用异常，已改用自然分段")
        self._semantic_segment_metrics["planning_failed"] = (
            self._semantic_segment_metrics.get("planning_failed", 0) + 1
        )
        return fallback

    def _semantic_segment_mark_pending(
        self, event: Any, plan: SemanticSegmentPlan, scope: str
    ) -> None:
        setattr(event, self._SEMANTIC_SEGMENT_PLAN_ATTR, plan)
        setattr(event, self._SEMANTIC_SEGMENT_SCOPE_ATTR, scope)
        setattr(
            event,
            self._SEMANTIC_SEGMENT_PENDING_ATTR,
            list(plan.segments) if len(plan.segments) > 1 else [],
        )

    def _semantic_segment_log_expression_trace(
        self,
        plan: SemanticSegmentPlan,
        *,
        event: Any = None,
        scope: str = "",
    ) -> None:
        if event is not None and getattr(
            event, "_daily_life_reply_trace_logged", False
        ):
            return
        scope_value = str(scope or "").strip()
        if not scope_value and event is not None:
            scope_value = self._semantic_segment_scope_from_event(event)
        is_group = ":GroupMessage:" in scope_value
        compact_lengths = [
            len("".join(str(segment.text or "").split())) for segment in plan.segments
        ]
        semantic = plan.valid and len(plan.segments) > 1
        logger.debug(
            f"{LOG_PREFIX} 表达节奏：通道={'群聊' if is_group else '私聊'}；"
            f"模型语义分段={'是' if semantic else '否'}；"
            f"长度={sum(compact_lengths)}"
        )
        if event is not None:
            setattr(event, "_daily_life_reply_trace_logged", True)

    @staticmethod
    def _semantic_segment_trace(segments: list[SegmentPart]) -> str:
        return "；".join(
            f"{index}={len(''.join(str(segment.text or '').split()))}字"
            for index, segment in enumerate(segments, start=1)
        )

    def _semantic_segment_try_natural_fallback(
        self, event: Any, source_text: str
    ) -> bool:
        if not self._semantic_segment_enabled():
            return False
        limit = int(self._chat_style_limit_for_event(event) or 0)
        settings = getattr(self.config, "chat_style", None)
        max_segments = max(1, int(getattr(settings, "semantic_max_segments", 10) or 10))
        segments = self._plan_chat_style_natural_segments(
            source_text, limit, max_segments_cap=max_segments
        )
        if len(segments) < 2:
            return False
        try:
            from .style import _ChatStyleSegmentPlan

            raw_plan = SemanticSegmentPlan(
                tuple(SegmentPart(segment.text, pause="short") for segment in segments),
                source="natural_fallback",
                valid=True,
            )
            clean_plan = self._semantic_segment_clean_plan_punctuation(
                event, raw_plan, source_text
            )
            natural_segments = [
                _ChatStyleSegmentPlan(
                    raw_text=source_segment.text,
                    text=planned_segment.text,
                    separator=source_segment.separator,
                    break_kind=source_segment.break_kind,
                )
                for source_segment, planned_segment in zip(
                    segments, clean_plan.segments
                )
            ]
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 自然分段降级失败：{type(exc).__name__}",
                exc_info=True,
            )
            return False
        if not self._replace_text_result_with_segments(event, natural_segments):
            return False
        # 这条路径由自然分段接管，不能保留先前单段模型计划；否则
        # ChatStyleRuntimeMixin 会误以为语义发送器仍负责投递并跳过发送。
        try:
            delattr(event, self._SEMANTIC_SEGMENT_PLAN_ATTR)
        except AttributeError:
            pass
        setattr(event, self._SEMANTIC_SEGMENT_PENDING_ATTR, [])
        setattr(event, "_daily_life_natural_fallback_active", True)
        context = self._chat_style_context(event)
        self.log_chat_style_trace(
            event,
            "\n".join(segment.text for segment in natural_segments),
            context,
            changed=True,
        )
        return True

    async def apply_semantic_segment_before_send(self, event: Any) -> bool:
        if event is None or not self._semantic_segment_enabled():
            return False
        if self._is_active_agent_intermediate_result(event):
            return False
        if getattr(event, self._SEMANTIC_SEGMENT_ATTEMPTED_ATTR, False):
            return False
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not chain:
            return False
        texts = [self._semantic_segment_text(item).strip() for item in chain]
        if not texts or not all(texts):
            return False
        source_text = "".join(texts)
        if self._chat_style_text_is_structural(source_text):
            logger.debug(
                f"{LOG_PREFIX} 模型语义分段跳过：保留列表、代码块或其他结构化排版。"
            )
            return False
        # 过短的回应不可能包含多个完整表达动作。正式回复仍使用语义分段，
        # 但像“煲仔饭！”这类单句不再在前台额外等待一次模型调用。
        if len("".join(source_text.split())) <= 5 and "\n" not in source_text:
            logger.debug(f"{LOG_PREFIX} 模型语义分段跳过：回复过短，保留单条发送。")
            return False
        if self._chat_style_should_keep_default_send(event, source_text):
            return False
        setattr(event, self._SEMANTIC_SEGMENT_ATTEMPTED_ATTR, True)
        scope = self._semantic_segment_scope_from_event(event)
        epochs = getattr(self, "_semantic_segment_epochs", None)
        if not isinstance(epochs, dict):
            self._semantic_segment_init_state()
            epochs = self._semantic_segment_epochs
        epochs[scope] = int(epochs.get(scope, 0)) + 1
        plan = await self._semantic_segment_plan_text(
            source_text,
            scope=scope,
            user_message=str(getattr(event, "message_str", "") or "").strip(),
            length_hint=self._chat_style_limit_for_event(event),
        )
        if not plan.valid:
            if self._semantic_segment_try_natural_fallback(event, source_text):
                self._semantic_segment_metrics["fallback_natural"] = (
                    self._semantic_segment_metrics.get("fallback_natural", 0) + 1
                )
                return True
            self._semantic_segment_metrics["fallback_single"] = (
                self._semantic_segment_metrics.get("fallback_single", 0) + 1
            )
            return False
        plan = self._semantic_segment_clean_plan_punctuation(event, plan, source_text)
        self._semantic_segment_log_expression_trace(plan, event=event, scope=scope)
        self._semantic_segment_mark_pending(event, plan, scope)
        if len(plan.segments) <= 1:
            # 有些模型会返回结构合法但把多句完整表达合并成一个分段。
            # 仅在通用自然分段器能找到明确停顿时降级，仍保持原文顺序，
            # 不根据关键词或具体案例打补丁。
            if self._semantic_segment_try_natural_fallback(event, source_text):
                self._semantic_segment_metrics["fallback_natural"] = (
                    self._semantic_segment_metrics.get("fallback_natural", 0) + 1
                )
                return True
            if plan.segments and plan.segments[0].text != source_text:
                chain[:] = [
                    self._semantic_segment_copy(chain[0], plan.segments[0].text)
                ]
                return True
            return False
        template = chain[0]
        chain[:] = [
            self._semantic_segment_copy(template, segment.text)
            for segment in plan.segments
        ]
        return True

    def _semantic_segment_delay_seconds(
        self, segment: SegmentPart, *, scope: str = ""
    ) -> float:
        delay = self._chat_style_segment_delay_seconds(
            "", segment.text, pause=segment.pause
        )
        state = getattr(self, "_chat_pacing_state", None)
        if not isinstance(state, dict):
            state = {}
            self._chat_pacing_state = state
        pacing = state.get(scope, {})
        interval = float(pacing.get("interval_ema") or 0.0)
        effect = float(pacing.get("effect") or 0.0)
        if interval and interval <= 4.0:
            delay *= 0.88
        elif interval >= 30.0:
            delay *= 1.08
        delay *= 1.0 - max(-0.12, min(effect * 0.12, 0.12))
        return round(max(0.0, delay), 2)

    async def send_semantic_segments_if_needed(self, event: Any) -> bool:
        if not self._semantic_segment_enabled():
            if event is not None:
                setattr(event, self._SEMANTIC_SEGMENT_PENDING_ATTR, [])
            return False
        pending = list(getattr(event, self._SEMANTIC_SEGMENT_PENDING_ATTR, []) or [])
        if len(pending) < 2:
            return False
        setattr(event, self._SEMANTIC_SEGMENT_PENDING_ATTR, [])
        scope = str(getattr(event, self._SEMANTIC_SEGMENT_SCOPE_ATTR, "") or "").strip()
        revision = self._semantic_segment_revision(scope)
        epoch = self._semantic_segment_epoch(scope)
        service = getattr(self, "reply_delivery", None) or ReplyDeliveryService(self)
        outcome = await service.send_event(
            EventDeliveryRequest(
                event=event,
                texts=tuple(segment.text for segment in pending),
                scope=scope,
                match="joined",
                text_from_item=self._semantic_segment_text,
                build_message=lambda index, chain: MessageChain().message(
                    pending[index].text
                ),
                delay_seconds=lambda index: self._semantic_segment_delay_seconds(
                    pending[index], scope=scope
                ),
                sleep=asyncio.sleep,
                is_current=lambda: (
                    self._semantic_segment_revision(scope) == revision
                    and self._semantic_segment_epoch(scope) == epoch
                ),
            )
        )
        if outcome.status == "skipped":
            logger.debug(
                f"{LOG_PREFIX} 模型语义分段发送跳过：当前结果已被其他发送通道接管或改写。"
            )
            return False
        if outcome.status == "cancelled":
            marker = getattr(self, "mark_structured_pending_bot_text", None)
            if callable(marker) and outcome.sent_count > 0:
                marker(
                    event,
                    "\n".join(
                        segment.text for segment in pending[: outcome.sent_count]
                    ),
                )
            capture = getattr(self, "capture_chat_memory_bot_reply", None)
            if callable(capture) and outcome.sent_count > 0:
                try:
                    await capture(event)
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 已发送分段记忆入队失败：{exc}")
            self._semantic_segment_metrics["cancelled"] += 1
            logger.debug(
                f"{LOG_PREFIX} 模型语义分段后续分段已取消：已发送 "
                f"{outcome.sent_count}/{len(pending)} 条。"
            )
            return True
        if outcome.status == "failed":
            self._semantic_segment_metrics["failed"] += 1
            if outcome.sent_count > 0:
                logger.debug(
                    f"{LOG_PREFIX} 模型语义分段发送中断：已发送 "
                    f"{outcome.sent_count}/{len(pending)} 条；为避免重复发送，"
                    "不再回退整段回复。"
                )
                return True

            try:
                from .style import _ChatStyleSegmentPlan

                source_text = "".join(segment.text for segment in pending)
                natural_segments = [
                    _ChatStyleSegmentPlan(
                        raw_text=segment.text,
                        text=segment.text,
                        separator="",
                        break_kind=("tail" if index == len(pending) - 1 else "strong"),
                    )
                    for index, segment in enumerate(pending)
                ]
                restored = self._replace_text_result(event, source_text)
                fallback_ready = restored and self._replace_text_result_with_segments(
                    event, natural_segments
                )
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 语义发送失败后的自然分段准备失败："
                    f"{type(exc).__name__}",
                    exc_info=True,
                )
                fallback_ready = False

            if fallback_ready:
                try:
                    delattr(event, self._SEMANTIC_SEGMENT_PLAN_ATTR)
                except AttributeError:
                    pass
                setattr(event, self._SEMANTIC_SEGMENT_PENDING_ATTR, [])
                setattr(event, "_daily_life_natural_fallback_active", True)
                self._semantic_segment_metrics["fallback_natural"] = (
                    self._semantic_segment_metrics.get("fallback_natural", 0) + 1
                )
                logger.debug(
                    f"{LOG_PREFIX} 模型语义分段发送失败，已改用自然分段："
                    f"{outcome.error}"
                )
                natural_sender = getattr(
                    self, "send_chat_style_segments_if_needed", None
                )
                if callable(natural_sender) and await natural_sender(event):
                    return True
                self._replace_text_result(
                    event, "\n".join(segment.text for segment in pending)
                )

            logger.debug(
                f"{LOG_PREFIX} 模型语义分段发送失败，保留默认发送：{outcome.error}"
            )
            return False
        try:
            marker = getattr(self, "mark_structured_pending_bot_text", None)
            if callable(marker):
                marker(event, "\n".join(segment.text for segment in pending))
            reaction = getattr(self, "note_tool_reaction_message_sent", None)
            if callable(reaction):
                await reaction(event)
            scheduler = getattr(self, "schedule_pending_chat_state_refresh", None)
            if callable(scheduler):
                scheduler(event)
            note_structured = getattr(self, "note_structured_sent_result", None)
            if callable(note_structured):
                note_structured(event)
            emoji_sender = getattr(self, "send_semantic_emoji_if_needed", None)
            if callable(emoji_sender):
                await emoji_sender(event)
            capture = getattr(self, "capture_chat_memory_bot_reply", None)
            if callable(capture):
                try:
                    await capture(event)
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 分段回复记忆入队失败：{exc}")
            self._semantic_segment_metrics["sent"] += 1
            logger.debug(
                f"{LOG_PREFIX} 语义分段发送：{outcome.sent_count} 段；"
                f"{self._semantic_segment_trace(pending)}"
            )
            return True
        except Exception as exc:
            self._semantic_segment_metrics["failed"] += 1
            logger.warning(f"{LOG_PREFIX} 模型语义分段发送后处理失败：{exc}")
            # 消息已经由投递服务成功送出；统计或状态回写失败不应让
            # 上层重复发送同一条回复。
            return True

    async def plan_semantic_segments_for_text(
        self,
        text: str,
        *,
        target_scope: str,
        user_message: str = "",
        source: str = "proactive",
        length_hint: int = 0,
        source_event: Any = None,
    ) -> SemanticSegmentPlan:
        plan = await self._semantic_segment_plan_text(
            text,
            scope=target_scope,
            user_message=user_message,
            source=source,
            length_hint=length_hint,
        )
        if not self._semantic_segment_enabled():
            return plan
        source_text = self._semantic_segment_normalize_source_text(text)
        plan = self._semantic_segment_clean_plan_punctuation(
            source_event, plan, source_text
        )
        self._semantic_segment_log_expression_trace(
            plan, event=source_event, scope=target_scope
        )
        return plan

    async def send_semantic_segments(
        self,
        target_scope: str,
        plan: SemanticSegmentPlan,
        *,
        source_event: Any = None,
        source_message_id: str = "",
        source: str = "semantic_segment",
    ) -> bool:
        if not self._semantic_segment_enabled():
            return False
        return await self._send_background_plan(
            str(target_scope or "").strip(),
            plan,
            mode=BackgroundTextMode.EXPRESSIVE,
            source_event=source_event,
            source_message_id=source_message_id,
            source=source,
        )

    async def send_background_text(
        self,
        target_scope: str,
        text: str,
        *,
        mode: BackgroundTextMode,
        source_event: Any = None,
        source_message_id: str = "",
        source: str = "background",
        raise_delivery_errors: bool = False,
        user_message: str = "",
        length_hint: int | None = None,
    ) -> bool:
        """通过统一发送管线投递后台可见文字。"""
        if not isinstance(mode, BackgroundTextMode):
            raise TypeError("后台文字发送模式无效")
        raw_text = str(text or "").strip()
        scope = str(target_scope or "").strip()
        if not scope or not raw_text:
            return False
        if not source_message_id and source_event is not None:
            try:
                source_message_id = str(self._event_message_id(source_event) or "")
            except Exception:
                source_message_id = ""

        if mode is BackgroundTextMode.DIRECT:
            return await self._send_background_plan(
                scope,
                SemanticSegmentPlan(
                    (SegmentPart(raw_text),), source="direct", valid=True
                ),
                mode=mode,
                source_event=source_event,
                source_message_id=source_message_id,
                source=source,
                raise_delivery_errors=raise_delivery_errors,
            )

        source_text = (
            self._semantic_segment_normalize_source_text(raw_text)
            if self._semantic_segment_enabled()
            else raw_text
        )
        if not self._semantic_segment_enabled():
            return await self._send_background_plan(
                scope,
                SemanticSegmentPlan(
                    (SegmentPart(source_text),), source="direct", valid=True
                ),
                mode=mode,
                source_event=source_event,
                source_message_id=source_message_id,
                source=source,
                raise_delivery_errors=raise_delivery_errors,
            )

        if length_hint is None:
            limit_getter = getattr(self, "_chat_style_limit_for_event", None)
            if callable(limit_getter) and source_event is not None:
                length_hint = int(limit_getter(source_event) or 0)
            else:
                limit_getter = getattr(self, "_chat_style_limit_for_scope", None)
                length_hint = (
                    int(limit_getter(scope) or 0) if callable(limit_getter) else 0
                )
        plan = await self._semantic_segment_plan_text(
            raw_text,
            scope=scope,
            user_message=str(user_message or "").strip(),
            source=source,
            length_hint=int(length_hint or 0),
        )
        if plan.valid:
            plan = self._semantic_segment_clean_plan_punctuation(
                source_event, plan, source_text
            )
            self._semantic_segment_log_expression_trace(
                plan, event=source_event, scope=scope
            )
            return await self._send_background_plan(
                scope,
                plan,
                mode=mode,
                source_event=source_event,
                source_message_id=source_message_id,
                source=source,
                raise_delivery_errors=raise_delivery_errors,
            )

        splitter = getattr(self, "_plan_chat_style_natural_segments", None)
        settings = getattr(self.config, "chat_style", None)
        max_segments = max(1, int(getattr(settings, "semantic_max_segments", 10) or 10))
        natural_segments = (
            splitter(raw_text, int(length_hint or 0), max_segments_cap=max_segments)
            if callable(splitter)
            else []
        )
        if len(natural_segments) > 1:
            self._semantic_segment_metrics["fallback_natural"] = (
                self._semantic_segment_metrics.get("fallback_natural", 0) + 1
            )
            natural_plan = SemanticSegmentPlan(
                tuple(
                    SegmentPart(
                        segment.text,
                        pause="short" if segment.break_kind == "soft" else "normal",
                    )
                    for segment in natural_segments
                ),
            source="natural",
            valid=True,
            )
            natural_plan = self._semantic_segment_clean_plan_punctuation(
                source_event, natural_plan, source_text
            )
            self.log_chat_style_trace(
                source_event,
                "\n".join(segment.text for segment in natural_plan.segments),
                self._chat_style_context(source_event)
                if source_event is not None
                else {},
                changed=True,
            )
            return await self._send_background_plan(
                scope,
                natural_plan,
                mode=mode,
                source_event=source_event,
                source_message_id=source_message_id,
                source=source,
                raise_delivery_errors=raise_delivery_errors,
            )

        self._semantic_segment_metrics["fallback_single"] = (
            self._semantic_segment_metrics.get("fallback_single", 0) + 1
        )
        fallback_plan = self._semantic_segment_clean_plan_punctuation(
            source_event,
            SemanticSegmentPlan(
                (SegmentPart(source_text),),
                source="natural",
                valid=True,
            ),
            source_text,
        )
        self.log_chat_style_trace(
            source_event,
            fallback_plan.text,
            self._chat_style_context(source_event) if source_event is not None else {},
            changed=False,
        )
        return await self._send_background_plan(
            scope,
            fallback_plan,
            mode=mode,
            source_event=source_event,
            source_message_id=source_message_id,
            source=source,
            raise_delivery_errors=raise_delivery_errors,
        )

    async def _send_background_plan(
        self,
        scope: str,
        plan: SemanticSegmentPlan,
        *,
        mode: BackgroundTextMode,
        source_event: Any = None,
        source_message_id: str = "",
        source: str = "background",
        raise_delivery_errors: bool = False,
    ) -> bool:
        segments = list(plan.segments)
        if not segments:
            return False
        expressive = mode is BackgroundTextMode.EXPRESSIVE
        enabled = expressive and self._semantic_segment_enabled()
        if enabled:
            revisions = getattr(self, "_semantic_segment_revisions", None)
            epochs = getattr(self, "_semantic_segment_epochs", None)
            if not isinstance(revisions, dict) or not isinstance(epochs, dict):
                self._semantic_segment_init_state()
                revisions = self._semantic_segment_revisions
                epochs = self._semantic_segment_epochs
            revision = int(revisions.get(scope, 0))
            epoch = int(epochs.get(scope, 0)) + 1
            epochs[scope] = epoch
        else:
            revisions = {}
            epochs = {}
            revision = 0
            epoch = 0
        sender = getattr(self, "send_message_if_not_recalled", None)
        if not callable(sender):
            return False
        reply_to_id = str(source_message_id or "").strip()
        service = getattr(self, "reply_delivery", None) or ReplyDeliveryService(self)
        outcome = await service.send_scope(
            ScopeDeliveryRequest(
                scope=scope,
                texts=tuple(segment.text for segment in segments),
                build_message=lambda index: MessageChain().message(
                    segments[index].text
                ),
                delay_seconds=lambda index: (
                    self._semantic_segment_delay_seconds(segments[index], scope=scope)
                    if enabled
                    else 0.0
                ),
                sleep=asyncio.sleep,
                is_current=lambda: (
                    not enabled
                    or (
                        int(revisions.get(scope, 0)) == revision
                        and int(epochs.get(scope, 0)) == epoch
                    )
                ),
                send=lambda chain: sender(
                    scope,
                    chain,
                    source_event=source_event,
                    source_message_id=source_message_id,
                    raise_delivery_errors=raise_delivery_errors,
                ),
                on_sent=lambda index, text: self.note_structured_bot_message(
                    scope,
                    text,
                    source_event=source_event if index == 0 else None,
                    reply_to_id=reply_to_id if index == 0 else "",
                ),
                source_event=source_event,
                source_message_id=source_message_id,
                source=source,
                decorate_addressing=expressive,
                raise_delivery_errors=raise_delivery_errors,
            )
        )
        if outcome.status == "cancelled":
            self._semantic_segment_metrics["cancelled"] += 1
            logger.info(
                f"{LOG_PREFIX} 后台文字后续分段已取消：已发送 "
                f"{outcome.sent_count}/{len(segments)} 条。"
            )
            return True
        if outcome.status != "sent":
            logger.warning(f"{LOG_PREFIX} 后台文字发送失败：{outcome.error}")
            if source == "proactive" and outcome.error is not None:
                raise outcome.error
            return False
        if plan.source == "semantic":
            self._semantic_segment_metrics["sent"] += 1
        if len(segments) > 1:
            label = "语义分段" if plan.source == "semantic" else "自然分段"
            logger.debug(
                f"{LOG_PREFIX} {label}发送：{len(segments)} 段；"
                f"{self._semantic_segment_trace(segments)}"
            )
        return True

    def semantic_segment_status(self) -> dict[str, Any]:
        return {
            "enabled": self._semantic_segment_enabled(),
            "metrics": dict(getattr(self, "_semantic_segment_metrics", {})),
            "active_scopes": len(getattr(self, "_semantic_segment_revisions", {})),
        }
