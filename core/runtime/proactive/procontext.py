from typing import Any

from astrbot.api import logger

from ...sources.history import SavedHistoryReader
from ..markers import LOG_PREFIX


class ProactiveSyntheticEvent:
    is_at_or_wake_command = False
    is_wake = False

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
    def _private_revisit_event(self, target_scope: str, reply_text: str, relationship: Any) -> Any:
        return ProactiveSyntheticEvent(
            message=reply_text,
            target_scope=target_scope,
            sender_id=str(getattr(relationship, "user_id", "") or getattr(relationship, "id", "") or ""),
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
        limit = max(10, int(self.config.proactive.max_reply_length or 80))
        return text[:limit].rstrip()

    def _expression_review_passed(self, payload: dict[str, Any]) -> bool:
        review = payload.get("expression_review")
        if not isinstance(review, dict) or "passed" not in review:
            return True
        return self._proactive_bool(review.get("passed"))

    async def _current_proactive_persona(self, target_scope: str = "") -> str:
        composer = getattr(self, "composer", None)
        get_persona = getattr(composer, "_get_persona", None)
        if not callable(get_persona):
            return ""
        try:
            persona = get_persona(str(target_scope or "").strip())
            if hasattr(persona, "__await__"):
                persona = await persona
            return str(persona or "").strip()
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 读取闲时回复会话人设失败：{exc}")
            return ""

    @staticmethod
    def _format_proactive_persona_context(persona: str) -> str:
        text = str(persona or "").strip()
        return text or "暂无可读取的当前会话人设。"

    async def _read_recent_context_messages(self, target_scope: str, limit: int = 6) -> list[dict[str, str]]:
        target_scope = str(target_scope or "").strip()
        if not target_scope or limit <= 0:
            return []
        try:
            reader = SavedHistoryReader(self.context, LOG_PREFIX)
            return await reader.fetch(target_scope, max_count=limit, hours=12, prefer_conversation=True)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 读取闲时回复最近对话片段失败：{exc}")
            return []

    def _format_recent_context_messages(self, messages: list[dict[str, str]]) -> str:
        lines: list[str] = []
        for message in messages[-6:]:
            role = str(message.get("role") or "").lower()
            name = str(message.get("name") or "").strip()
            label = "我" if role == "assistant" else name or "对方"
            content = "；".join(part.strip() for part in str(message.get("content") or "").splitlines() if part.strip())
            if len(content) > 140:
                content = content[:140].rstrip() + "..."
            if content:
                lines.append(f"- {label}: {content}")
        return "\n".join(lines) if lines else "暂无可读取的最近对话片段。"

    async def _build_recent_context_for_proactive(self, target_scope: str, limit: int = 6) -> str:
        messages = await self._read_recent_context_messages(target_scope, limit=limit)
        return self._format_recent_context_messages(messages)

    def _format_expression_profiles_for_proactive(self, profiles: list[Any]) -> str:
        lines: list[str] = []
        for item in list(profiles or [])[:4]:
            label = str(getattr(item, "label", "") or getattr(item, "scope", "") or "").strip()
            tone = str(getattr(item, "tone", "") or "").strip()
            habits = "；".join(str(text).strip() for text in list(getattr(item, "habits", []) or [])[:3] if str(text).strip())
            avoid = "；".join(str(text).strip() for text in list(getattr(item, "avoid", []) or [])[:2] if str(text).strip())
            parts = [tone, habits, f"避开：{avoid}" if avoid else ""]
            body = "；".join(part for part in parts if part)
            if label and body:
                lines.append(f"- {label}: {body}")
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
                lines.append(f"- {scene}: {pattern}{suffix}；可信度 {float(confidence or 0):.2f}")
        return "\n".join(lines) if lines else "暂无沉淀行为模式。"

    def _format_reply_effects_for_proactive(self, effects: list[Any]) -> str:
        lines: list[str] = []
        for item in list(effects or [])[:4]:
            text = str(getattr(item, "reply_text", "") or "").strip()
            outcome = str(getattr(item, "outcome", "") or "").strip()
            evidence = str(getattr(item, "evidence", "") or getattr(item, "reason", "") or "").strip()
            if text or evidence:
                lines.append(f"- {text or '闲时回应'}: {outcome or '待观察'}；{evidence or '无补充'}")
        return "\n".join(lines) if lines else "暂无闲时回复效果记录。"

    def _format_expression_reviews_for_proactive(self, reviews: list[Any]) -> str:
        lines: list[str] = []
        for item in list(reviews or [])[:3]:
            passed = "通过" if bool(getattr(item, "passed", True)) else "不宜发送"
            risk = str(getattr(item, "risk", "") or "").strip()
            suggestion = str(getattr(item, "suggestion", "") or getattr(item, "reason", "") or "").strip()
            if risk or suggestion:
                lines.append(f"- {passed}: {risk or suggestion}" + (f"；建议 {suggestion}" if risk and suggestion else ""))
        return "\n".join(lines) if lines else "暂无表达自然度记录。"

    def _format_behavior_scenes_for_proactive(self, scenes: list[Any]) -> str:
        lines: list[str] = []
        for item in list(scenes or [])[:4]:
            scene = str(getattr(item, "scene", "") or "").strip()
            cues = "；".join(str(text).strip() for text in list(getattr(item, "cues", []) or [])[:3] if str(text).strip())
            action = str(getattr(item, "preferred_action", "") or "").strip()
            avoid = str(getattr(item, "avoid_action", "") or "").strip()
            if scene:
                lines.append(f"- {scene}: {cues or '语义场景'}；倾向 {action or '观察'}" + (f"；避免 {avoid}" if avoid else ""))
        return "\n".join(lines) if lines else "暂无行为场景簇。"

    def _format_focus_slots_for_proactive(self, slots: list[Any]) -> str:
        lines: list[str] = []
        for item in list(slots or [])[:4]:
            label = str(getattr(item, "label", "") or getattr(item, "focus_key", "") or "").strip()
            reason = str(getattr(item, "reason", "") or "").strip()
            priority = int(getattr(item, "priority", 0) or 0)
            if label:
                lines.append(f"- {label}: 注意槽 {priority}/100；{reason or '短期仍会想起'}")
        return "\n".join(lines) if lines else "暂无短期注意槽。"

    def _format_mid_summaries_for_proactive(self, summaries: list[Any]) -> str:
        lines: list[str] = []
        for item in list(summaries or [])[:3]:
            label = str(getattr(item, "scope_label", "") or getattr(item, "session_id", "") or "").strip()
            topic = str(getattr(item, "topic", "") or "").strip()
            mood = str(getattr(item, "mood", "") or "").strip()
            summary = str(getattr(item, "summary", "") or "").strip()
            parts = [f"话题：{topic}" if topic else "", f"氛围：{mood}" if mood else "", summary]
            body = "；".join(part for part in parts if part)
            if label and body:
                lines.append(f"- {label}: {body}")
        return "\n".join(lines) if lines else "暂无会话中期摘要。"

    def _format_temporary_expression_states_for_proactive(self, states: list[Any]) -> str:
        lines: list[str] = []
        for item in list(states or [])[:3]:
            label = str(getattr(item, "label", "") or "").strip()
            tone = str(getattr(item, "tone", "") or "").strip()
            reason = str(getattr(item, "reason", "") or "").strip()
            intensity = getattr(item, "intensity", 0)
            if label or tone:
                parts = [tone, reason, f"强度 {int(intensity or 0)}/100"]
                lines.append(f"- {label or '此刻表达状态'}: " + "；".join(part for part in parts if part))
        return "\n".join(lines) if lines else "暂无临时表达状态。"

    def _format_life_terms_for_proactive(self, terms: list[Any]) -> str:
        lines: list[str] = []
        for item in list(terms or [])[:6]:
            term = str(getattr(item, "term", "") or "").strip()
            meaning = str(getattr(item, "meaning", "") or "").strip()
            scene = str(getattr(item, "scene", "") or "").strip()
            examples = "；".join(str(text).strip() for text in list(getattr(item, "examples", []) or [])[:2] if str(text).strip())
            familiarity = getattr(item, "familiarity", 0)
            if term and meaning:
                detail = "；".join(
                    part
                    for part in (meaning, f"场景：{scene}" if scene else "", examples, f"熟悉度 {int(familiarity or 0)}/100")
                    if part
                )
                lines.append(f"- {term}: {detail}")
        return "\n".join(lines) if lines else "暂无场景词。"

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
