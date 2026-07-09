import datetime
import uuid
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...models import CommitmentRecord
from ...prompts import CORE_JSON_OUTPUT_RULES, CORE_PERSONA_PRONOUN_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX
from .boundary import format_speaker_boundary
from .jsonclean import STRICT_JSON_REPLY_RULE, call_pure_json


class CapturePledgeMixin:
    async def _get_commitment_provider(self):
        provider_id = self.config.commitments.provider
        return await self.composer._get_provider(provider_id)

    @staticmethod
    def _relative_date_hint(now: datetime.datetime) -> str:
        tomorrow = now + datetime.timedelta(days=1)
        days_until_saturday = (5 - now.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7
        saturday = now + datetime.timedelta(days=days_until_saturday)
        sunday = saturday + datetime.timedelta(days=1)
        return (
            f"今天={now.strftime('%Y-%m-%d')}；"
            f"明天={tomorrow.strftime('%Y-%m-%d')}；"
            f"本周末={saturday.strftime('%Y-%m-%d')} 到 {sunday.strftime('%Y-%m-%d')}"
        )

    def _build_commitment_extract_prompt(
        self,
        message: str,
        sender_name: str,
        now: datetime.datetime,
        persona_hint: str = "",
        message_facts: str = "",
        current_role_label: str = "",
    ) -> str:
        persona_hint = self._str_payload(persona_hint)
        current_role_label = self._str_payload(current_role_label) or "我"
        message_facts = str(message_facts or message).strip()
        speaker_boundary = format_speaker_boundary(
            current_role_label=current_role_label,
            speaker_name=sender_name,
            persona_hint=persona_hint,
        )
        fixed = f"""我只需要在心里快速判断刚看到的内容里是否包含未来承诺、提醒、约定或下次续聊事项。
不要抽取普通闲聊、情绪表达、已经发生的事、无明确未来意图的愿望。
这是我的后台生活记忆裁定。

人物称谓与性别规则：
{CORE_PERSONA_PRONOUN_RULES}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

        {STRICT_JSON_REPLY_RULE}
        {{
  "has_commitment": true,
  "content": "简洁保留对方提到的承诺、提醒或约定",
  "kind": "reminder|plan|followup",
  "trigger_date": "YYYY-MM-DD 或空字符串",
  "trigger_time": "HH:MM 或空字符串",
  "time_window": "morning|daytime|night|weekend|next_chat|next_time 或空字符串",
  "people": ["相关人物名"],
  "place": "相关地点或空",
  "confidence": 0.0
}}

规则：
- “明天提醒我...” kind=reminder，trigger_date 填明天。
- “周末一起...” kind=plan，trigger_date 优先填本周六，time_window=weekend。
- “下次再聊这个/回头继续说” kind=followup，trigger_date 为空，time_window=next_chat。
- confidence 低于 0.7 时也可以输出，但系统不会自动保存。
- 没有未来承诺时输出 {{"has_commitment": false}}。
- 字段中涉及人物称谓时按人物边界判断。
- 这是极短内部裁定，不需要展开分析；如果服务端仍记录隐藏推理，只能保留一句第一人称内心判断。
- 隐藏推理只写我此刻的感受和判断；普通情绪表达不是未来约定。
"""
        dynamic = f"""当前角色：{current_role_label}
记录视角：当前角色第一人称
说话人：{sender_name}
人物边界：
{speaker_boundary}
当前日期时间：{now.strftime('%Y-%m-%d %H:%M')}
日期换算：{self._relative_date_hint(now)}
我刚看到的内容：
{message_facts}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="刚看到的聊天")

    async def maybe_capture_commitment_from_event(
        self,
        event: Any,
        now: datetime.datetime | None = None,
        sender_name: str = "",
    ) -> CommitmentRecord | None:
        message = str(getattr(event, "message_str", "") or "").strip()
        if not message or self._event_has_command_handler(event) or self.event_was_recalled(event, log_skip=True):
            return None
        now = now or life_now()
        sender_name = sender_name or await self.contact_resolver.resolve_event_sender(event)
        if self.event_was_recalled(event, log_skip=True):
            return None
        provider = await self._get_commitment_provider()
        if not provider:
            return None
        session_id = f"daily_life_commitment_{uuid.uuid4().hex[:8]}"
        profile_id = self._event_profile_id(event, sender_name)
        relationship = await self.archive.get_relationship(profile_id) if profile_id else None
        persona_hint = await self._extract_speaker_persona_hint(
            sender_name,
            event=event,
            relationship=relationship,
        )
        current_role_label = await self._current_role_label(event)
        if self.event_was_recalled(event, log_skip=True):
            return None
        prompt = self._build_commitment_extract_prompt(
            message,
            sender_name,
            now,
            persona_hint,
            self._event_message_component_facts(event, message),
            current_role_label,
        )
        try:
            provider_id = self.config.commitments.provider
            payload = await call_pure_json(
                self.composer,
                provider,
                prompt,
                session_id,
                primary_provider_id=provider_id,
            )
            if not isinstance(payload, dict) or not payload.get("has_commitment"):
                return None
            if self.event_was_recalled(event, log_skip=True):
                return None
            commitment = CommitmentRecord.from_value(
                {
                    **payload,
                    "source": "chat",
                    "source_session": self._event_session_id(event),
                    "source_message": message,
                }
            )
            if not commitment or commitment.confidence < self.config.commitments.min_confidence:
                return None
            if sender_name and sender_name not in commitment.people:
                commitment.people.insert(0, sender_name)
            if self.event_was_recalled(event, log_skip=True):
                return None
            saved = await self.archive.save_commitment(commitment)
            context_meta = await self._event_context_meta(event, sender_name, now)
            details = [
                f"约定追踪：{saved.content}",
                f"类型：{saved.kind}",
            ]
            if saved.trigger_date or saved.trigger_time or saved.time_window:
                details.append(f"触发时间：{saved.trigger_date} {saved.trigger_time or saved.time_window}".strip())
            if saved.people:
                details.append("相关人物：" + "、".join(saved.people[:5]))
            if saved.place:
                details.append(f"地点：{saved.place}")
            self.schedule_memos_selected_items(
                context_meta,
                details,
                reason="同步已确认的未来约定，供之后聊天和生活安排参考。",
                user_message=message,
                marker=f"commitment:{saved.id}",
            )
            logger.info(f"{LOG_PREFIX} 已记录聊天承诺 #{saved.id}：{saved.content}")
            return saved
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} 自动识别承诺失败：{e}")
            return None
        finally:
            await self.composer._cleanup_conversation(session_id)
