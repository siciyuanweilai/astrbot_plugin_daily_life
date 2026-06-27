import copy
import datetime
import uuid
from typing import Any

from astrbot.api import logger

from ..life.condition import format_state_prompt, normalize_state, state_is_stale, state_log_entry
from ..life.tools import extract_json_from_text, get_current_timeline_status
from ..models import DayRecord, LifeState
from ..prompts import (
    CORE_AUTONOMY_RULES,
    CORE_JSON_OUTPUT_RULES,
    CORE_STATE_BEHAVIOR_RULES,
    cache_friendly_prompt,
)
from ..clock import now as life_now
from .markers import LOG_PREFIX


class StatusMixin:
    async def _get_state_provider(self):
        provider_id = self.config.state.provider
        return await self.composer._get_provider(provider_id)

    def _build_state_update_prompt(
        self,
        data: DayRecord,
        now: datetime.datetime,
        source: str,
        detail: str = "",
    ) -> str:
        curr_act, next_act = get_current_timeline_status(data.timeline, now, data.date)
        activity_text = curr_act.activity if curr_act else "碎片时间"
        next_text = f"{next_act.time} {next_act.activity}" if next_act else "无"
        state_log = data.state_log
        state_log_text = "\n".join(str(item) for item in state_log[-6:]) or "无"
        lifecycle_text = ""
        meta = data.meta or {}
        lifecycle_text = (
            "\n连续生活参数："
            f"\n- 日程基调 life_mode: {meta.get('life_mode') or '未知'}"
            f"\n- 生成睡眠倾向 sleep_mode: {meta.get('sleep_mode') or '未知'}"
            f"\n- sleep_debt: {meta.get('sleep_debt') or '0'}/10"
            f"\n- energy_carryover: {meta.get('energy_carryover') or '未知'}/100"
        )
        fixed = f"""更新当前角色此刻的身体和情绪状态，不改写日程、穿搭或事件。

【通用自主原则】
{CORE_AUTONOMY_RULES}

【通用状态行为原则】
{CORE_STATE_BEHAVIOR_RULES}

请只输出 JSON 对象，不要 Markdown，不要解释：
{{
  "energy": 0-100,
  "mood": "此刻心情底色",
  "mood_score": 0-100,
  "busyness": 0-100,
  "social": 0-100,
  "stress": 0-100,
  "focus": 0-100,
  "sleepiness": 0-100,
  "outgoing": 0-100,
  "emotional_stability": 0-100,
  "interaction_capacity": 0-100,
  "boredom": 0-100,
  "fishing": 0-100,
  "attention_openness": 0-100,
  "watch_state": "blackout|peek|skim_window|active_watch|engaged",
  "interrupt_level": "ordinary|medium|high",
  "interrupt_reason": "当前为什么只适合被这种等级的消息打断",
  "sleep": {{"quality": 0-100, "depth": "awake|light_rest|light_sleep|deep_sleep", "summary": "睡眠影响是否仍在"}},
  "summary": "一句话概括此刻状态",
  "source": "触发来源原样写入",
  "preference_points": [{{"category": "activity|outfit|social|sleep|place|style|other", "content": "稳定偏好", "weight": 0.1-1.0, "evidence": "本次触发依据"}}],
  "life_events": [{{"title": "新的生活事件", "detail": "细节", "effect": "未来影响", "status": "open"}}]
}}

要求：
{CORE_JSON_OUTPUT_RULES}
- 这是实时微调，不是重新生成一天。变化要自然、克制，不要大起大落。
- 根据当前活动、时间、天气、触发信息和原状态推断。
- 体力低时 summary 可以体现“不太想出门/更想低强度互动”；社交意愿低时体现慢热和低负担。
- mood_score 表示当下心情正向程度，不等同于 emotional_stability；情绪稳定但低落、开心但容易波动都可以存在。
- stress 是主观压力感，busyness 是客观忙碌度；sleepiness 是实时困倦度，sleep.quality 是昨晚/近期睡眠质量。
- outgoing 表示是否愿意外出，interaction_capacity 表示当前场景下回应、接话、继续交流的意愿与余力。
- boredom 表示低刺激下想找点新鲜内容的倾向；fishing 表示持续低价值刺激后懒得看、懒得理、想退出的倾向。
- attention_openness 表示此刻愿意让外界消息进入主体注意力的开放度。
- watch_state 表示群聊观看姿态：blackout=基本不看，peek=偶尔瞥见，skim_window=扫读一小段，active_watch=持续关注，engaged=已经参与。
- interrupt_level 表示当前可打断等级：ordinary=普通消息也可自然进入注意，medium=熟悉用户/相关话题/异常热闹才进入，high=只有@、引用、提到我、高风险冲突或强相关事件才进入。
- watch_state、boredom、fishing、attention_openness 和 interrupt_level 是同一个主观注意力状态机；要根据体力、困倦、忙碌、社交意愿、当前活动和触发信息自主判断，不要套固定时间或固定文本规则。
- sleep.depth 是此刻睡眠/休息层级：awake=清醒，light_rest=浅休息但仍可能留意，light_sleep=浅睡眠且普通消息难进入，deep_sleep=深度睡眠只可能被强打断信号影响。它由能量、困意、当前活动、昨日睡眠债、时间线索和消息打断等级共同判断，不要由固定时间直接决定。
- 不要输出 meta.life_mode 或 meta.sleep_mode；它们属于今日生成的日程基调，不属于实时状态刷新。
- preference_points、life_events 都是可选结构；只有有明确依据时才写，不能为了填字段编造。
"""
        dynamic = f"""当前时间：{now.strftime("%Y-%m-%d %H:%M")}
触发来源：{source}
触发信息：{detail or "无"}
天气：{data.weather or "未知"}
当前活动：{activity_text}
下一项安排：{next_text}
当前状态：{format_state_prompt(data.state)}
近期状态变化：
{state_log_text}
{lifecycle_text}"""
        return cache_friendly_prompt(fixed, dynamic)

    async def refresh_state_for_day(
        self,
        date_str: str,
        data: DayRecord | None = None,
        now: datetime.datetime | None = None,
        source: str = "idle",
        detail: str = "",
        force: bool = False,
        source_event: Any = None,
        notify_page: bool = True,
    ) -> DayRecord | None:
        if not self.config.state.enabled:
            return data or await self.archive.get_day(date_str)
        if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
            return data or await self.archive.get_day(date_str)

        now = now or life_now()
        data = data or await self.archive.get_day(date_str)
        if not data:
            return None

        current_state = data.state
        if not force and not state_is_stale(
            current_state,
            now,
            self.config.state.refresh_minutes,
        ):
            return data

        provider = await self._get_state_provider()
        if not provider:
            return data

        session_id = f"daily_life_state_{uuid.uuid4().hex[:8]}"
        prompt = self._build_state_update_prompt(data, now, source, detail)
        try:
            provider_id = self.config.state.provider
            text = await self.composer._call_llm_text(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            result = extract_json_from_text(text)
            if not result:
                return data
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return data

            state_data = normalize_state(result.get("state", result), now=now, source=source, previous=current_state)
            state = LifeState.from_value(state_data)
            previous_date = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            previous_day = await self.archive.get_day(previous_date)
            continuity_target = copy.deepcopy(data) if source_event is not None else data
            continuity_target.state = state
            debt, delta, carryover = self.composer._compute_sleep_continuity(previous_day, continuity_target)
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return data
            if source_event is None:
                data.state = state
                data.meta["sleep_debt"] = f"{debt:.2f}".rstrip("0").rstrip(".")
                data.meta["sleep_debt_delta"] = f"{delta:.2f}".rstrip("0").rstrip(".")
                data.meta["energy_carryover"] = f"{carryover:.1f}".rstrip("0").rstrip(".")
            await self.composer.learn_preferences_from_payload(result, date_str=date_str, source=source)
            await self.composer.persist_life_events_from_payload(result, date_str=date_str, source=source)
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return data
            if source_event is not None:
                data.state = state
                data.meta["sleep_debt"] = f"{debt:.2f}".rstrip("0").rstrip(".")
                data.meta["sleep_debt_delta"] = f"{delta:.2f}".rstrip("0").rstrip(".")
                data.meta["energy_carryover"] = f"{carryover:.1f}".rstrip("0").rstrip(".")
            logs = list(data.state_log)
            logs.append(state_log_entry(state, now))
            data.state_log = logs[-10:]
            await self.archive.save_day(data)
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return data
            if notify_page:
                await self.mark_page_status_changed("state")
            return data
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} 更新实时状态失败：{e}")
            return data
        finally:
            await self.composer._cleanup_conversation(session_id)
