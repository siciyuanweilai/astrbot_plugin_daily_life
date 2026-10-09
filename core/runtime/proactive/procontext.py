import datetime
import math
import uuid
from typing import Any

from astrbot.api import logger

from ...config.options.basis import format_chat_style_prompt
from ...prompts import (
    CORE_EMOJI_DELIVERY_RULES,
    CORE_JSON_OUTPUT_RULES,
    CORE_PROACTIVE_CONTINUITY_RULES,
    cache_friendly_prompt,
)
from ...sources.history import SavedHistoryReader
from ..capture.jsonclean import call_pure_json
from ..markers import LOG_PREFIX


class ProactiveSyntheticEvent:
    is_at_or_wake_command = False
    is_wake = False
    # 该消息是供后台决策重放的上下文条目，不是新的话轮。
    is_proactive_synthetic = True

    def __init__(
        self,
        *,
        message: str,
        target_scope: str,
        message_id: str = "",
        sender_id: str = "",
        sender_name: str = "",
        platform_name: str = "",
        group_id: str = "",
        group_name: str = "",
        last_activity_at: Any = None,
        last_bot_reply_at: Any = None,
        recent_messages: list[dict[str, Any]] | None = None,
        pending_count: int = 1,
    ):
        self.message_str = str(message or "")
        self.unified_msg_origin = str(target_scope or "")
        self.message_id = str(message_id or "")
        self.proactive_last_activity_at = last_activity_at
        self.proactive_last_bot_reply_at = last_bot_reply_at
        self.proactive_recent_messages = list(recent_messages or [])
        self.proactive_pending_count = max(1, int(pending_count or 1))
        self._sender_id = str(sender_id or "")
        self._sender_name = str(sender_name or "")
        self._platform_name = str(platform_name or "")
        self._group_id = str(group_id or "")
        self._group_name = str(group_name or "")

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return self._sender_name

    def get_platform_name(self):
        return self._platform_name

    def get_group_id(self):
        return self._group_id

    def get_group_name(self):
        return self._group_name

    def get_self_id(self):
        return ""

    def is_stopped(self):
        return False

    def get_extra(self, key=None, default=None):
        return default if key else {}


class ProactiveContextMixin:
    def _private_revisit_event(
        self, target_scope: str, reply_text: str, relationship: Any
    ) -> Any:
        return ProactiveSyntheticEvent(
            message=reply_text,
            target_scope=target_scope,
            sender_id=str(
                getattr(relationship, "user_id", "")
                or getattr(relationship, "id", "")
                or ""
            ),
            sender_name=str(getattr(relationship, "name", "") or ""),
            platform_name=str(getattr(relationship, "platform", "") or ""),
        )

    @staticmethod
    def _clamp_float(value: Any, default: float = 0.0) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            result = default
        return max(0.0, min(1.0, result))

    @staticmethod
    def _proactive_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value or "").strip().lower() in {"1", "true", "yes", "是", "应该"}

    def _proactive_reply_text(self, value: Any) -> str:
        text = str(value or "").strip()
        lines = [" ".join(line.strip().split()) for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
        return text

    def _proactive_expression_limit_for_scope(self, target_scope: str) -> int:
        getter = getattr(self, "_chat_style_limit_for_scope", None)
        if callable(getter):
            try:
                return max(0, int(getter(target_scope) or 0))
            except (TypeError, ValueError):
                return 0
        style = getattr(getattr(self, "config", None), "chat_style", None)
        checker = getattr(self, "_chat_style_enabled", None)
        if not style or (callable(checker) and not checker()):
            return 0
        if not callable(checker) and not bool(getattr(style, "enabled", False)):
            return 0
        try:
            return max(0, int(getattr(style, "proactive_max_chars", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _format_proactive_expression_style(self, target_scope: str) -> str:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        checker = getattr(self, "_chat_style_enabled", None)
        enabled = (
            bool(checker())
            if callable(checker)
            else bool(style and getattr(style, "enabled", False))
        )
        if not enabled:
            return ""
        style_prompt = (
            format_chat_style_prompt(getattr(style, "casual_short_prompt", ""))
            if style and enabled
            else ""
        )
        limit = self._proactive_expression_limit_for_scope(target_scope)
        scope_label = "群聊" if ":GroupMessage:" in str(target_scope or "") else "私聊"
        lines = [f"- 消息传输范围：{scope_label}主动消息；不代表现实距离。"]
        if limit > 0:
            lines.append(
                f"- {scope_label}主动消息参考长度约 {limit} 字左右；只作节奏参考，主动开口优先简短、不打扰；必要的关心或解释说完整，不按字数删减。"
            )
        lines.append(f"- 表达节奏：{style_prompt or '轻量、自然、少展开。'}")
        return "\n".join(lines)

    def _expression_review_passed(self, payload: dict[str, Any]) -> bool:
        review = payload.get("expression_review")
        return isinstance(review, dict) and review.get("passed") is True

    async def _current_proactive_persona(self, target_scope: str = "") -> str:
        get_persona = getattr(self, "get_persona_text", None)
        if not callable(get_persona):
            return ""
        try:
            persona = await get_persona(str(target_scope or "").strip())
            return str(persona or "").strip()
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 读取闲时回复会话人设失败：{exc}")
            return ""

    @staticmethod
    def _format_proactive_persona_context(persona: str) -> str:
        text = str(persona or "").strip()
        return text or "暂无可读取的当前会话人设。"

    async def _read_recent_context_messages(
        self, target_scope: str, limit: int = 6
    ) -> list[dict[str, str]]:
        target_scope = str(target_scope or "").strip()
        if not target_scope or limit <= 0:
            return []
        structured_reader = getattr(self, "structured_recent_history_messages", None)
        structured = (
            structured_reader(target_scope, limit=limit)
            if callable(structured_reader)
            else []
        )
        try:
            reader = SavedHistoryReader(self.context, LOG_PREFIX)
            saved = await reader.fetch(
                target_scope, max_count=limit, hours=12, prefer_conversation=True
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 读取闲时回复最近对话片段失败：{exc}")
            saved = []
        return self._merge_recent_context_messages(saved, structured)[-limit:]

    @staticmethod
    def _context_message_timestamp(message: dict[str, str]) -> float:
        raw = message.get("timestamp")
        try:
            value = float(raw or 0.0)
        except (TypeError, ValueError):
            try:
                value = datetime.datetime.fromisoformat(
                    str(raw or "").replace("Z", "+00:00")
                ).timestamp()
            except (TypeError, ValueError, OverflowError, OSError):
                return 0.0
        return value if math.isfinite(value) and value > 0 else 0.0

    @classmethod
    def _merge_recent_context_messages(
        cls, saved: list[dict[str, str]], structured: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """补齐缓存之前的上下文，按出现位置匹配，保留真实重复及缓存元数据。"""

        def same_message(left: dict[str, str], right: dict[str, str]) -> bool:
            if left.get("role") != right.get("role"):
                return False
            left_id, right_id = left.get("message_id"), right.get("message_id")
            if left_id and right_id:
                return left_id == right_id
            left_user, right_user = left.get("user_id"), right.get("user_id")
            generic_ids = {
                "",
                None,
                "user",
                "assistant",
                left.get("name"),
                right.get("name"),
            }
            if left_user not in generic_ids and right_user not in generic_ids:
                if left_user != right_user:
                    return False
            elif left.get("role") == "user":
                left_name, right_name = left.get("name"), right.get("name")
                if left_name and right_name and left_name != right_name:
                    return False
            a = " ".join(str(left.get("content") or "").split())
            b = " ".join(str(right.get("content") or "").split())
            if not a or a != b:
                return False
            t1, t2 = (
                cls._context_message_timestamp(left),
                cls._context_message_timestamp(right),
            )
            return not (t1 and t2) or abs(t1 - t2) <= 2

        # 从最新出现处向前匹配，不能把两次“嗯”或相同问候全局去重。
        matches: list[tuple[int, int]] = []
        saved_end = len(saved)
        for cache_index in range(len(structured) - 1, -1, -1):
            for saved_index in range(saved_end - 1, -1, -1):
                if same_message(saved[saved_index], structured[cache_index]):
                    matches.append((saved_index, cache_index))
                    saved_end = saved_index
                    break
        result: list[dict[str, str]] = []
        saved_start = cache_start = 0
        for saved_index, cache_index in [
            *reversed(matches),
            (len(saved), len(structured)),
        ]:
            gap = [
                *saved[saved_start:saved_index],
                *structured[cache_start:cache_index],
            ]
            if gap and all(cls._context_message_timestamp(item) for item in gap):
                gap.sort(key=cls._context_message_timestamp)
            result.extend(gap)
            if saved_index < len(saved):
                result.append(
                    {
                        **saved[saved_index],
                        **{k: v for k, v in structured[cache_index].items() if v},
                    }
                )
            saved_start, cache_start = saved_index + 1, cache_index + 1
        return result

    async def _audit_proactive_continuity(
        self,
        *,
        payload: dict[str, Any],
        recent_context: str,
        life_context: str | None = None,
        provider: Any,
        provider_id: str,
    ) -> tuple[bool, str]:
        """复核话题、情绪、收尾意图；回访同时核对生活事实。"""
        fact_rules = (
            ""
            if life_context is None
            else """
- 区分已经发生、正在发生、未来计划、承诺和推测，不得把计划或承诺当作已经完成。
- 当前地点、动作完成、状态变化或物品状态的断言，必须有当前生活事实或带时间消息直接支持。
- 旧回复与当前结构化生活事实冲突时，以当前结构化事实为准，不为旧回复补造经过。
- 任一可见事实断言缺少证据或与证据冲突时 valid=false。
"""
        )
        fixed = f"""审计一条待发送的主动消息能否自然承接最近的交流。
判断语义联系、情绪、重复与收尾边界，不评价个人文风，不用关键词重合代替理解。

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

{CORE_PROACTIVE_CONTINUITY_RULES}

{CORE_EMOJI_DELIVERY_RULES}

只输出 JSON：
{{"valid": true, "reason": "简短结论", "conflicts": ["断裂或冲突依据"]}}

审计原则：
- 联系自然且尊重最近交流的走向才可通过；事实正确但突然转移话题、忽略情绪、重复发问或打断收尾，也应 valid=false。
- 问候和开放式提问不自动通过；有依据的新进展和约定提醒可以自然换话题，不要求复述上文。
- 候选尚未发送，旧媒体记录不能证明本轮已重新发送图片、语音、视频或文件。
- 没有明确依据判断自然承接时 valid=false；不要替候选补造过渡或经历。
- 存在生活事实资料时，同时遵循本轮事实审计范围。"""
        dynamic = f"""本轮事实审计范围：{fact_rules or '仅复核对话承接。'}
候选理由：{str(payload.get("reason") or "").strip()}
候选回复：{str(payload.get("reply_text") or "").strip()}
本轮待发送内容：候选回复可按表达意图以文字或语音送达；目前尚未发送，也没有附带图片、视频或文件。

最近真实交流（时间未知时不推断已过去多久）：
{recent_context}

当前结构化生活事实：
{life_context or "本轮仅复核对话承接。"}"""
        prompt = cache_friendly_prompt(
            fixed, dynamic, dynamic_title="主动消息连续性审计资料"
        )
        session_id = f"daily_life_proactive_continuity_{uuid.uuid4().hex[:8]}"
        try:
            audit = await call_pure_json(
                self,
                provider,
                prompt,
                session_id,
                primary_provider_id=provider_id,
                strict=True,
                validator=lambda value: {
                    **value,
                    "valid": value.get("valid") is True,
                    "reason": str(value.get("reason") or "").strip(),
                    "conflicts": value.get("conflicts")
                    if isinstance(value.get("conflicts"), list)
                    else [],
                },
                fallback={
                    "valid": False,
                    "reason": "模型未给出严格审计结果",
                    "conflicts": [],
                },
            )
            if not isinstance(audit, dict) or not isinstance(audit.get("valid"), bool):
                return False, "连续性审计未返回有效结果"
            return bool(audit["valid"]), str(audit.get("reason") or "").strip()[:240]
        except Exception as exc:
            return False, f"连续性审计失败：{str(exc)[:180]}"
        finally:
            await self.close_text_session(session_id)

    @staticmethod
    def _format_context_message_label(message: dict[str, str]) -> str:
        role = str(message.get("role") or "").lower()
        name = str(message.get("name") or "").strip()
        label = "我" if role == "assistant" else name or "对方"
        target = str(message.get("talking_to_name") or "").strip()
        target_id = str(message.get("talking_to_id") or "").strip()
        if role != "assistant" and target and target_id not in {"", "group", "private"}:
            label = f"{label} -> {target}"
        return label

    @staticmethod
    def _format_context_message_content(
        message: dict[str, str], limit: int = 1200
    ) -> str:
        content = "；".join(
            part.strip()
            for part in str(message.get("content") or "").splitlines()
            if part.strip()
        )
        quote = str(message.get("reply_to_content") or "").strip()
        if len(quote) > 240:
            quote = quote[:110] + " …[中间省略]… " + quote[-110:]
        reply_sender = str(message.get("reply_to_sender_name") or "").strip()
        if quote:
            prefix = f"引用{reply_sender}: " if reply_sender else "引用: "
            content = (
                f"{content}（{prefix}{quote}）" if content else f"（{prefix}{quote}）"
            )
        media = str(message.get("media") or "").strip()
        if media:
            marker = f"[媒体：{media}]"
            if marker not in content:
                content = f"{marker} {content}".strip()
        if len(content) > limit:
            marker = " …[中间省略]… "
            keep = limit - len(marker)
            content = (
                content[: keep // 2].rstrip()
                + marker
                + content[-(keep - keep // 2) :].lstrip()
            )
        return content

    def _format_recent_context_messages(
        self,
        messages: list[dict[str, str]],
        *,
        now: datetime.datetime | None = None,
    ) -> str:
        lines: list[str] = []
        remaining = 6000
        for message in reversed(messages):
            if remaining < 200:
                break
            label = self._format_context_message_label(message)
            content = self._format_context_message_content(
                message, limit=min(1200, remaining)
            )
            if content:
                timestamp = self._context_message_timestamp(message)
                time_prefix = ""
                if timestamp > 0:
                    occurred_at = datetime.datetime.fromtimestamp(timestamp)
                    time_text = (
                        occurred_at.strftime("%H:%M")
                        if now and occurred_at.date() == now.date()
                        else occurred_at.strftime("%m-%d %H:%M")
                    )
                    if now:
                        age_seconds = max(0, int(now.timestamp() - timestamp))
                        if age_seconds < 60:
                            age_text = "不到 1 分钟前"
                        elif age_seconds < 3600:
                            age_text = f"{age_seconds // 60} 分钟前"
                        else:
                            age_text = f"{age_seconds // 3600} 小时前"
                        time_text = f"{time_text}，{age_text}"
                    time_prefix = f"[{time_text}] "
                line = f"- {time_prefix}{label}: {content}"
                lines.append(line)
                remaining -= len(line)
        return "\n".join(reversed(lines)) if lines else "暂无可读取的最近对话片段。"

    async def _build_recent_context_for_proactive(
        self, target_scope: str, limit: int = 6
    ) -> str:
        messages = await self._read_recent_context_messages(target_scope, limit=limit)
        return self._format_recent_context_messages(messages)

    def _format_expression_profiles_for_proactive(self, profiles: list[Any]) -> str:
        lines: list[str] = []
        for item in list(profiles or [])[:4]:
            label = str(
                getattr(item, "label", "") or getattr(item, "scope", "") or ""
            ).strip()
            tone = str(getattr(item, "tone", "") or "").strip()
            habits = "；".join(
                str(text).strip()
                for text in list(getattr(item, "habits", []) or [])[:3]
                if str(text).strip()
            )
            avoid = "；".join(
                str(text).strip()
                for text in list(getattr(item, "avoid", []) or [])[:2]
                if str(text).strip()
            )
            parts = [tone, habits, f"避开：{avoid}" if avoid else ""]
            body = "；".join(part for part in parts if part)
            if label and body:
                profile_id = str(getattr(item, "profile_id", "") or "").strip()
                owner = f"（适用对象：{profile_id}）" if profile_id else ""
                lines.append(f"- {label}{owner}: {body}")
        return "\n".join(lines) if lines else "暂无稳定表达习惯。"

    def _format_behavior_patterns_for_proactive(self, patterns: list[Any]) -> str:
        lines: list[str] = []
        for item in list(patterns or [])[:5]:
            scene = str(getattr(item, "scene", "") or "").strip()
            pattern = str(getattr(item, "pattern", "") or "").strip()
            action = str(getattr(item, "suggested_action", "") or "").strip()
            confidence = getattr(item, "confidence", 0)
            if scene and pattern:
                suffix = f"；倾向 {action}" if action else ""
                lines.append(
                    f"- {scene}: {pattern}{suffix}；可信度 {float(confidence or 0):.2f}"
                )
        return "\n".join(lines) if lines else "暂无沉淀行为模式。"

    def _format_reply_effects_for_proactive(self, effects: list[Any]) -> str:
        lines: list[str] = []
        for item in list(effects or [])[:4]:
            text = str(getattr(item, "reply_text", "") or "").strip()
            outcome = str(getattr(item, "outcome", "") or "").strip()
            evidence = str(
                getattr(item, "evidence", "") or getattr(item, "reason", "") or ""
            ).strip()
            if text or evidence:
                lines.append(
                    f"- {text or '闲时回应'}: {outcome or '待观察'}；{evidence or '无补充'}"
                )
        return "\n".join(lines) if lines else "暂无闲时回复效果记录。"

    def _format_expression_reviews_for_proactive(self, reviews: list[Any]) -> str:
        lines: list[str] = []
        for item in list(reviews or [])[:3]:
            passed = "通过" if bool(getattr(item, "passed", True)) else "不宜发送"
            risk = str(getattr(item, "risk", "") or "").strip()
            suggestion = str(
                getattr(item, "suggestion", "") or getattr(item, "reason", "") or ""
            ).strip()
            if risk or suggestion:
                lines.append(
                    f"- {passed}: {risk or suggestion}"
                    + (f"；建议 {suggestion}" if risk and suggestion else "")
                )
        return "\n".join(lines) if lines else "暂无表达自然度记录。"

    def _format_behavior_scenes_for_proactive(self, scenes: list[Any]) -> str:
        lines: list[str] = []
        for item in list(scenes or [])[:4]:
            scene = str(getattr(item, "scene", "") or "").strip()
            cues = "；".join(
                str(text).strip()
                for text in list(getattr(item, "cues", []) or [])[:3]
                if str(text).strip()
            )
            action = str(getattr(item, "preferred_action", "") or "").strip()
            avoid = str(getattr(item, "avoid_action", "") or "").strip()
            if scene:
                lines.append(
                    f"- {scene}: {cues or '语义场景'}；倾向 {action or '观察'}"
                    + (f"；避免 {avoid}" if avoid else "")
                )
        return "\n".join(lines) if lines else "暂无行为场景簇。"

    def _format_focus_slots_for_proactive(self, slots: list[Any]) -> str:
        lines: list[str] = []
        for item in list(slots or [])[:4]:
            label = str(
                getattr(item, "label", "") or getattr(item, "focus_key", "") or ""
            ).strip()
            reason = str(getattr(item, "reason", "") or "").strip()
            priority = int(getattr(item, "priority", 0) or 0)
            if label:
                lines.append(
                    f"- {label}: 注意槽 {priority}/100；{reason or '短期仍会想起'}"
                )
        return "\n".join(lines) if lines else "暂无短期注意槽。"

    def _format_mid_summaries_for_proactive(self, summaries: list[Any]) -> str:
        lines: list[str] = []
        for item in list(summaries or [])[:3]:
            label = str(
                getattr(item, "scope_label", "")
                or getattr(item, "session_id", "")
                or ""
            ).strip()
            topic = str(getattr(item, "topic", "") or "").strip()
            mood = str(getattr(item, "mood", "") or "").strip()
            summary = str(getattr(item, "summary", "") or "").strip()
            parts = [
                f"话题：{topic}" if topic else "",
                f"氛围：{mood}" if mood else "",
                summary,
            ]
            body = "；".join(part for part in parts if part)
            if label and body:
                lines.append(f"- {label}: {body}")
        return "\n".join(lines) if lines else "暂无会话中期摘要。"

    def _format_temporary_expression_states_for_proactive(
        self, states: list[Any]
    ) -> str:
        lines: list[str] = []
        for item in list(states or [])[:3]:
            label = str(getattr(item, "label", "") or "").strip()
            tone = str(getattr(item, "tone", "") or "").strip()
            reason = str(getattr(item, "reason", "") or "").strip()
            intensity = getattr(item, "intensity", 0)
            if label or tone:
                parts = [tone, reason, f"强度 {int(intensity or 0)}/100"]
                lines.append(
                    f"- {label or '此刻表达状态'}: "
                    + "；".join(part for part in parts if part)
                )
        return "\n".join(lines) if lines else "暂无临时表达状态。"

    def _format_life_terms_for_proactive(self, terms: list[Any]) -> str:
        lines: list[str] = []
        for item in list(terms or [])[:6]:
            term = str(getattr(item, "term", "") or "").strip()
            meaning = str(getattr(item, "meaning", "") or "").strip()
            scene = str(getattr(item, "scene", "") or "").strip()
            examples = "；".join(
                str(text).strip()
                for text in list(getattr(item, "examples", []) or [])[:2]
                if str(text).strip()
            )
            familiarity = getattr(item, "familiarity", 0)
            if term and meaning:
                detail = "；".join(
                    part
                    for part in (
                        meaning,
                        f"场景：{scene}" if scene else "",
                        examples,
                        f"熟悉度 {int(familiarity or 0)}/100",
                    )
                    if part
                )
                lines.append(f"- {term}: {detail}")
        return "\n".join(lines) if lines else "暂无语言。"

    def _relationship_friend_target_scope(self, relationship: Any) -> str:
        for contact in getattr(relationship, "contacts", []) or []:
            if str(getattr(contact, "contact_type", "") or "").strip() != "friend":
                continue
            if not bool(getattr(contact, "is_reachable", True)):
                continue
            target_scope = str(getattr(contact, "target_scope", "") or "").strip()
            if target_scope:
                return target_scope
        return ""

    def _resolve_private_target_umo(self, relationship: Any) -> str:
        return self._relationship_friend_target_scope(relationship)
