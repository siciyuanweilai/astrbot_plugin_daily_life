import datetime
import json
import uuid

from astrbot.api import logger

from ..models import LifeState, TimelineItem
from ..prompts import (
    CORE_AUTONOMY_RULES,
    CORE_JSON_OUTPUT_RULES,
    CORE_STATE_BEHAVIOR_RULES,
    LIFE_PREFERENCE_CATEGORY_ENUM,
    cache_friendly_prompt,
)
from .condition import format_state_prompt
from .tools import extract_json_from_text
from .people import INVITE_PERSON_TEXT_PATHS


class InviteMixin:
    async def handle_invite(
        self,
        date_str,
        current_timeline: list,
        invite_text: str,
        current_time: datetime.datetime,
        user_name: str = "用户",
        current_state: LifeState | None = None,
    ):
        now_mins = current_time.hour * 60 + current_time.minute
        past_timeline = []
        future_timeline = []
        for item in current_timeline:
            timeline_item = TimelineItem.from_value(item)
            try:
                hour, minute = map(int, timeline_item.time.split(":"))
                if hour * 60 + minute <= now_mins:
                    past_timeline.append(timeline_item)
                else:
                    future_timeline.append(timeline_item)
            except (TypeError, ValueError):
                future_timeline.append(timeline_item)

        persona = await self._get_persona()
        person_facts = await self._build_person_fact_context(
            persona=persona,
            explicit_instruction=invite_text,
        )
        autonomy_context = await self._build_autonomous_life_context(current_time)
        fixed = f"""对方向我提出了共同活动或陪伴请求。我要结合自己的真实意愿、当前状态、已经确认的安排和双方关系，决定自然答应、换个时间，还是拒绝。

通用自主原则：
{CORE_AUTONOMY_RULES}

通用状态行为原则：
{CORE_STATE_BEHAVIOR_RULES}

裁定要求：
1. 严格符合我的【性格设定】，结合真实意愿、原计划的重要程度和当前时间，决定是否接受邀约。
   - 如果体力低、社交意愿低或睡眠质量差，可以更自然地拒绝或改为低负担安排。
   - 如果心情放松、社交意愿高且忙碌度不高，可以更愿意接受。
2. 简短地给出我决定接受、拒绝或改约的【内心真实理由】（reason）。注意：不要写成直接回复的台词；写成我的主观理由或现实顾虑。
3. 如果接受，请返回新的 future_timeline；activity 要自然写清楚和邀请者一起做什么。
4. 如果不接受但愿意改约，请给出 alternative_time；如果完全不想去则留空。
5. 允许输出 preference_points 和 life_events，但只能基于当前邀约和状态，不要编造。

严格返回 JSON：
{{
  "decision": "accept | reject | propose_alternative",
  "accept": true/false,
  "reason": "我的内心理由/现实顾虑（千万不要写成对白）",
  "response_stance": "最终回复应表达的态度，例如开心答应、温和改约、自然拒绝",
  "response_tone": "符合关系和当下状态的简短语气描述，不写最终台词",
  "alternative_time": "可选改约时间或空字符串",
  "impact": "这次邀约对今日状态、社交意愿或后续日程的影响",
  "new_future_timeline": [{{"time": "...", "activity": "...", "status": "..."}}],
  "preference_points": [{{"category": "{LIFE_PREFERENCE_CATEGORY_ENUM}", "content": "可复用偏好", "weight": 0.1-1.0, "evidence": "依据"}}],
  "life_events": [{{"title": "邀约相关生活事件", "detail": "细节", "effect": "未来影响", "status": "open"}}]
}}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}
"""
        dynamic = f"""我的性格设定：
{persona}

当前时间：{current_time.strftime("%H:%M")}
当前身体和情绪状态：{format_state_prompt(current_state)}
朋友/用户：{user_name}
邀约/打断内容：{invite_text}

我原本接下来的计划：
{json.dumps([item.as_dict() for item in future_timeline], ensure_ascii=False)}

短期目标、修正和近期决策参考：
{autonomy_context or "暂无"}"""
        if person_facts.has_external_people:
            dynamic += "\n\n" + person_facts.format_for_generation()
        prompt = cache_friendly_prompt(fixed, dynamic, dynamic_title="邀约现场")
        session_id = ""
        try:
            provider_id = self._task_provider_id(self.config.invite.provider)
            provider = await self._get_provider(provider_id)
            if not provider:
                return "当前没有可用的 LLM，暂时不想改变计划。", None, {}
            session_id = f"daily_life_invite_{uuid.uuid4().hex[:8]}"
            completion_text = await self._call_llm_text(
                provider,
                prompt,
                session_id,
                primary_provider_id=provider_id,
            )
            result = extract_json_from_text(completion_text)

            if result and "new_future_timeline" in result:
                audit = await self._audit_person_payload(
                    result,
                    context=person_facts,
                    patterns=INVITE_PERSON_TEXT_PATHS,
                    provider=provider,
                    provider_id=provider_id,
                    subject="邀约裁定与改排日程",
                )
                if audit.unresolved:
                    logger.warning("[邀约处理] 人物事实存在未解决冲突，保持原日程。")
                    return "人物关系信息暂时没有核对清楚，先不改变计划。", None, {}
                result = audit.payload
                decision = str(result.get("decision") or "").strip()
                accepted = result.get("accept") is True or decision == "accept"
                await self._save_life_decision_record(
                    kind="invite",
                    date=date_str,
                    subject=user_name,
                    decision=decision or ("accept" if accepted else "reject"),
                    reason=str(result.get("reason") or "").strip(),
                    evidence=invite_text,
                    outcome=str(
                        result.get("impact") or result.get("response_stance") or ""
                    ).strip(),
                    source="invite",
                )
                if accepted:
                    new_timeline = past_timeline + [
                        TimelineItem.from_value(item)
                        for item in result["new_future_timeline"]
                    ]
                    return (
                        result.get("reason", "内心觉得提议不错，顺其自然地答应了。"),
                        new_timeline,
                        result,
                    )
                return (
                    result.get("reason", "感觉当前日程安排太紧了，没有精力去。"),
                    None,
                    result,
                )
        except Exception as e:
            logger.error(f"[邀约处理] 处理失败：{e}")
        finally:
            if session_id:
                await self._cleanup_conversation(session_id)
        return "感觉脑子有点乱，目前不想改变计划。", None, {}
