import datetime
import json
import uuid

from astrbot.api import logger

from ..models import LifeState, TimelineItem
from ..prompts import CORE_AUTONOMY_RULES, CORE_JSON_OUTPUT_RULES, CORE_STATE_BEHAVIOR_RULES, cache_friendly_prompt
from .condition import format_state_prompt
from .tools import extract_json_from_text


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
        fixed = f"""我正在过自己的一天，需要判断朋友或用户的邀约是否能自然打断或合并进今天后续日程。

通用自主原则：
{CORE_AUTONOMY_RULES}

通用状态行为原则：
{CORE_STATE_BEHAVIOR_RULES}

裁定要求：
1. 严格符合我的【性格设定】，并结合原计划的【重要程度】和当前【时间】，决定是否接受邀约。
   - 如果体力低、社交意愿低或睡眠质量差，可以更自然地拒绝或改为低负担安排。
   - 如果心情放松、社交意愿高且忙碌度不高，可以更愿意接受。
2. 简短地给出我决定接受、拒绝或改约的【内心真实理由】（reason）。注意：不要写成直接回复的台词；写成我的主观理由或现实顾虑。
3. 如果接受，请返回一个新的 future_timeline（合并对方的邀约事件，调整后续时间点）。如果接受，请务必在 activity 中明确写出是和邀请者一起。
4. 如果不接受但愿意改约，请给出 alternative_time；如果完全不想去则留空。
5. 允许输出 preference_points 和 life_events，但只能基于当前邀约和状态，不要编造。

严格返回 JSON：
{{
  "decision": "accept | reject | propose_alternative",
  "accept": true/false,
  "reason": "我的内心理由/现实顾虑（千万不要写成对白）",
  "reply_hint": "给聊天回复使用的简短语气提示，不要代替最终回复",
  "alternative_time": "可选改约时间或空字符串",
  "impact": "这次邀约对今日状态、社交意愿或后续日程的影响",
  "new_future_timeline": [{{"time": "...", "activity": "...", "status": "..."}}],
  "preference_points": [{{"category": "social|activity|place|other", "content": "可复用偏好", "weight": 0.1-1.0, "evidence": "依据"}}],
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
{json.dumps([item.as_dict() for item in future_timeline], ensure_ascii=False)}"""
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
                decision = str(result.get("decision") or "").strip()
                accepted = result.get("accept") is True or decision == "accept"
                if accepted:
                    new_timeline = past_timeline + [
                        TimelineItem.from_value(item)
                        for item in result["new_future_timeline"]
                    ]
                    return result.get("reason", "内心觉得提议不错，顺其自然地答应了。"), new_timeline, result
                return result.get("reason", "感觉当前日程安排太紧了，没有精力去。"), None, result
        except Exception as e:
            logger.error(f"[邀约处理] 处理失败：{e}")
        finally:
            if session_id:
                await self._cleanup_conversation(session_id)
        return "感觉脑子有点乱，目前不想改变计划。", None, {}
