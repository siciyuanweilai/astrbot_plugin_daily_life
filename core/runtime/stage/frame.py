from __future__ import annotations

import datetime
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...life.condition import format_state_prompt
from ...life.tools import format_timeline_to_text, get_current_timeline_status, resolve_business_now
from ...sources.history import SavedHistoryReader
from ..markers import LOG_PREFIX


class StageFrameMixin:
    async def _media_director_current_day(self) -> tuple[Any | None, datetime.datetime, bool]:
        now = life_now()
        business_now = resolve_business_now(getattr(self.config, "schedule_time", "07:00"), now)
        target_date = business_now.strftime("%Y-%m-%d")
        using_extended_night = business_now.date() < now.date()
        day = await self.archive.get_day(target_date) if getattr(self, "archive", None) else None
        if day:
            return day, now, using_extended_night
        today = now.strftime("%Y-%m-%d")
        if target_date != today and getattr(self, "archive", None):
            day = await self.archive.get_day(today)
        return day, now, False

    def _media_director_context_text(self, day: Any | None, now: datetime.datetime, using_extended_night: bool) -> str:
        if not day:
            return f"当前时间：{now.strftime('%Y-%m-%d %H:%M')}；暂无今日生活记录。"
        current_item, next_item = get_current_timeline_status(
            getattr(day, "timeline", []) or [],
            now,
            getattr(day, "date", None),
        )
        state = getattr(day, "state", None)
        state_dict = state.as_dict() if hasattr(state, "as_dict") else state if isinstance(state, dict) else {}
        meta = getattr(day, "meta", {}) or {}
        weather = str(getattr(day, "weather", "") or "").strip()
        outfit = str(getattr(day, "outfit", "") or "").strip()

        parts = [
            f"生活记录范围：{'延续昨日生活记录' if using_extended_night else '今日生活记录'}",
            f"天气：{weather or '未知'}",
            f"当前穿搭：{outfit or meta.get('outfit_decision') or '未知'}",
            f"心情色彩：{meta.get('mood') or '未知'}",
            f"日程类型：{meta.get('theme') or meta.get('schedule_intent') or '未知'}",
            f"日程基调：{meta.get('life_mode') or '未知'}",
        ]
        if current_item:
            activity = getattr(current_item, "activity", "") if hasattr(current_item, "activity") else current_item.get("activity", "")
            status = getattr(current_item, "status", "") if hasattr(current_item, "status") else current_item.get("status", "")
            parts.append(f"当前活动：{activity or '未知'}{f'（{status}）' if status else ''}")
        if next_item:
            time_text = getattr(next_item, "time", "") if hasattr(next_item, "time") else next_item.get("time", "")
            activity = getattr(next_item, "activity", "") if hasattr(next_item, "activity") else next_item.get("activity", "")
            parts.append(f"下一段安排：{time_text} {activity}".strip())
        if state_dict:
            parts.append(f"身体与情绪状态：{format_state_prompt(state_dict)}")
        timeline = format_timeline_to_text(getattr(day, "timeline", []) or [])
        if timeline:
            parts.append(f"全天日程背景（连续性参考）：\n{timeline}")
        parts.append(f"当前时间：{now.strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(parts)

    @staticmethod
    def _media_director_compact_text(value: Any, limit: int = 100) -> str:
        text = " ".join(str(value or "").split())
        if len(text) > limit:
            return text[:limit].rstrip() + "..."
        return text

    @classmethod
    def _media_director_format_emotion_arcs(cls, arcs: list[Any], limit: int = 4) -> str:
        lines: list[str] = []
        for item in list(arcs or [])[:limit]:
            label = cls._media_director_compact_text(getattr(item, "label", ""), 60)
            if not label:
                continue
            parts = [
                f"强度 {getattr(item, 'intensity', 0)}/100",
                f"正负向 {getattr(item, 'valence', 0)}",
            ]
            evidence = cls._media_director_compact_text(getattr(item, "evidence", ""), 100)
            influence = cls._media_director_compact_text(getattr(item, "influence", ""), 100)
            if evidence:
                parts.append(f"依据：{evidence}")
            if influence:
                parts.append(f"影响：{influence}")
            lines.append(f"- {label}：" + "；".join(parts))
        if not lines:
            return ""
        return "近期情绪脉络（短期状态参考）：\n" + "\n".join(lines)

    async def _media_director_emotion_context_text(
        self,
        day: Any | None = None,
        event: Any = None,
        limit: int = 4,
    ) -> str:
        getter = getattr(getattr(self, "archive", None), "get_emotion_arcs", None)
        if not callable(getter):
            return ""
        scope = ""
        resolver = getattr(self, "_event_session_id", None)
        if callable(resolver) and event is not None:
            try:
                scope = str(resolver(event) or "").strip()
            except Exception:
                scope = ""
        date = str(getattr(day, "date", "") or "").strip()
        try:
            arcs = await getter(limit=limit, scope=scope, date=date, include_global=True)
            if not arcs and date:
                arcs = await getter(limit=limit, scope=scope, include_global=True)
            return self._media_director_format_emotion_arcs(arcs, limit=limit)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 读取媒体导演情绪脉络失败：{exc}")
            return ""

    def _media_director_current_user_message(self, event: Any) -> str:
        if not event:
            return ""
        getter = getattr(self, "_safe_event_attr", None)
        if callable(getter):
            return str(getter(event, "message_str") or "").strip()
        return str(getattr(event, "message_str", "") or "").strip()

    async def _media_director_recent_context_text(self, event: Any, limit: int = 8) -> str:
        limit = max(1, int(limit or 8))
        resolver = getattr(self, "_event_session_id", None)
        scope = ""
        if callable(resolver):
            try:
                scope = str(resolver(event) or "").strip()
            except Exception:
                scope = ""
        if not scope:
            return "暂无可读取的最近对话片段。"
        structured_reader = getattr(self, "structured_recent_history_messages", None)
        messages = structured_reader(scope, limit=limit) if callable(structured_reader) else []
        if not messages:
            try:
                messages = await SavedHistoryReader(self.context, LOG_PREFIX).fetch(
                    scope,
                    max_count=limit,
                    hours=12,
                    prefer_conversation=True,
                )
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 读取媒体生成最近对话片段失败：{exc}")
                messages = []
        current_message = self._media_director_current_user_message(event)
        if current_message and not any(
            str(item.get("role") or "").strip() == "user"
            and str(item.get("content") or "").strip() == current_message
            for item in messages[-4:]
        ):
            messages.append({"role": "user", "content": current_message, "name": ""})
        text_context = self._media_director_format_recent_messages(messages[-limit:])
        structured = self.format_structured_message_context(event, limit=8)
        if structured:
            return f"{text_context}\n\n结构化消息流（优先判断谁对谁说话、引用和@关系）：\n{structured}"
        return text_context

    @staticmethod
    def _media_director_message_label(message: dict[str, str]) -> str:
        role = str(message.get("role") or "").strip().lower()
        name = str(message.get("name") or "").strip()
        label = "我" if role == "assistant" else name or "对方"
        target = str(message.get("talking_to_name") or "").strip()
        target_id = str(message.get("talking_to_id") or "").strip()
        if role != "assistant" and target and target_id not in {"", "group", "private"}:
            label = f"{label} -> {target}"
        return label

    @staticmethod
    def _media_director_message_content(message: dict[str, str], limit: int = 160) -> str:
        content = " ".join(str(message.get("content") or "").split())
        quote = " ".join(str(message.get("reply_to_content") or "").split())
        reply_sender = str(message.get("reply_to_sender_name") or "").strip()
        if quote:
            prefix = f"引用{reply_sender}: " if reply_sender else "引用: "
            content = f"{content}（{prefix}{quote}）" if content else f"（{prefix}{quote}）"
        if len(content) > limit:
            content = content[:limit].rstrip() + "..."
        return content

    @staticmethod
    def _media_director_format_recent_messages(messages: list[dict[str, str]]) -> str:
        lines: list[str] = []
        for message in messages:
            label = StageFrameMixin._media_director_message_label(message)
            content = StageFrameMixin._media_director_message_content(message, limit=160)
            if content:
                lines.append(f"- {label}: {content}")
        return "\n".join(lines) if lines else "暂无可读取的最近对话片段。"
