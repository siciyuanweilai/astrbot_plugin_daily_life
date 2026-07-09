import copy
import datetime
import uuid
from typing import Any

from astrbot.api import logger

from ..life.condition import format_state_prompt, normalize_state, state_is_stale, state_log_entry
from ..life.signals import physiological_rhythm_log_from_state
from ..life.tools import extract_json_from_text, get_current_timeline_status
from ..models import DayRecord, EmotionArcRecord, LifeState
from ..prompts import (
    CORE_AUTONOMY_RULES,
    CORE_JSON_OUTPUT_RULES,
    CORE_STATE_BEHAVIOR_RULES,
    LIFE_PREFERENCE_CATEGORY_ENUM,
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
        emotion_context: str = "",
        rhythm_context: str = "",
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
  "physiological_rhythm": {{
    "energy_curve": "此刻到接下来一段时间的精力起伏",
    "body_condition": {{"label": "身体状态", "intensity": 0-100, "source": "依据来源", "expires_at": "YYYY-MM-DD 或空字符串"}},
    "recovery_actions": ["自然恢复动作"],
    "social_battery": 0-100,
    "attention_state": "注意力/感官负荷状态",
    "optional_cycle": {{"enabled": "布尔值，是否存在可选周期", "label": "可选周期标签", "intensity": 0-100, "source": "依据来源"}},
    "summary": "一句话概括此刻生理节律"
  }},
  "summary": "一句话概括此刻状态",
  "source": "触发来源原样写入",
  "emotion_arc": {{"label": "此刻最主要的情绪脉络", "valence": -100到100, "arousal": 0-100, "intensity": 0-100, "stability": 0-100, "trigger": "触发点", "evidence": "依据", "influence": "会怎样轻微影响生活判断"}},
  "preference_points": [{{"category": "{LIFE_PREFERENCE_CATEGORY_ENUM}", "content": "稳定偏好", "weight": 0.1-1.0, "evidence": "本次触发依据"}}],
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
- physiological_rhythm 是通用身体节律：包括精力曲线、身体状态、恢复动作、社交电量、注意力状态和可选周期字段。
- 不要输出 meta.life_mode 或 meta.sleep_mode；它们属于今日生成的日程基调，不属于实时状态刷新。
- emotion_arc 只记录当前情绪脉络的结构化摘要；没有新触发时可以延续原状态并降低强度。
- preference_points、life_events 都是可选结构；只有有明确依据时才写，不能为了填字段编造。
"""
        rhythm_section = f"\n近期生理节律：\n{rhythm_context}" if rhythm_context else ""
        dynamic = f"""生活日程日期：{data.date or "未知"}
天气：{data.weather or "未知"}
当前活动：{activity_text}
下一项安排：{next_text}
当前状态：{format_state_prompt(data.state)}
近期状态变化：
{state_log_text}
近期情绪脉络：
{emotion_context or "暂无"}
{rhythm_section}
{lifecycle_text}
当前时间：{now.strftime("%Y-%m-%d %H:%M")}
触发来源：{source}
触发信息：{detail or "无"}"""
        return cache_friendly_prompt(fixed, dynamic)

    @staticmethod
    def _state_score(value: Any, default: int = 50) -> int:
        try:
            score = int(float(value))
        except (TypeError, ValueError):
            score = default
        return max(0, min(score, 100))

    @classmethod
    def _emotion_arc_from_state_payload(
        cls,
        result: dict,
        state: LifeState,
        *,
        date_str: str,
        now: datetime.datetime,
        source: str,
        detail: str = "",
    ) -> EmotionArcRecord | None:
        raw_arc = result.get("emotion_arc")
        payload = raw_arc if isinstance(raw_arc, dict) else {}
        label = str(payload.get("label") or state.mood or "").strip()
        evidence = str(payload.get("evidence") or state.summary or "").strip()
        trigger = str(payload.get("trigger") or detail or source or "").strip()
        influence = str(payload.get("influence") or "").strip()
        if not (label or evidence or trigger or influence):
            return None

        mood_score = cls._state_score(state.mood_score, 60)
        sleepiness = cls._state_score(state.sleepiness, 35)
        focus = cls._state_score(state.focus, 55)
        interaction = cls._state_score(state.interaction_capacity, 55)
        default_valence = max(-100, min((mood_score - 50) * 2, 100))
        default_arousal = max(0, min(round((sleepiness + focus) / 2), 100))
        default_intensity = max(10, min(round(abs(default_valence) * 0.45 + interaction * 0.25 + 20), 100))
        expires_at = (now + datetime.timedelta(hours=18)).strftime("%Y-%m-%d %H:%M:%S")
        return EmotionArcRecord.from_value(
            {
                **payload,
                "scope": "",
                "date": date_str,
                "label": label,
                "valence": payload.get("valence", default_valence),
                "arousal": payload.get("arousal", default_arousal),
                "intensity": payload.get("intensity", default_intensity),
                "stability": payload.get("stability", state.emotional_stability),
                "trigger": trigger,
                "evidence": evidence,
                "influence": influence or state.interrupt_reason or state.summary,
                "expires_at": payload.get("expires_at") or expires_at,
                "source": source or "state",
            },
            date=date_str,
            source=source or "state",
        )

    @staticmethod
    def _format_emotion_arcs_for_state_prompt(arcs: list[Any]) -> str:
        lines = []
        for item in list(arcs or [])[:4]:
            label = str(getattr(item, "label", "") or "").strip()
            if not label:
                continue
            parts = [
                f"强度 {getattr(item, 'intensity', 0)}/100",
                f"正负向 {getattr(item, 'valence', 0)}",
                str(getattr(item, "evidence", "") or "").strip(),
                str(getattr(item, "influence", "") or "").strip(),
            ]
            lines.append(f"- {label}: " + "；".join(part for part in parts if part))
        return "\n".join(lines)

    @staticmethod
    def _format_rhythm_trend_for_state_prompt(trend: dict[str, Any]) -> str:
        if not isinstance(trend, dict):
            return ""
        summary = str(trend.get("summary") or "").strip()
        logs = trend.get("logs") if isinstance(trend.get("logs"), list) else []
        lines = [f"- {summary}"] if summary else []
        for item in logs[:3]:
            if not isinstance(item, dict):
                continue
            parts = [
                str(item.get("body_label") or item.get("summary") or "").strip(),
                f"身体负荷 {item.get('body_intensity')}/100" if item.get("body_intensity") is not None else "",
                f"社交电量 {item.get('social_battery')}/100" if item.get("social_battery") is not None else "",
                str(item.get("lifecycle_kind") or "").strip(),
            ]
            line = "；".join(part for part in parts if part)
            if line:
                lines.append(f"- {item.get('date') or '近期'}：{line}")
        return "\n".join(lines)

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
        emotion_context = ""
        get_emotion_arcs = getattr(self.archive, "get_emotion_arcs", None)
        if callable(get_emotion_arcs):
            emotion_context = self._format_emotion_arcs_for_state_prompt(
                await get_emotion_arcs(limit=4, scope="", include_global=True)
            )
        rhythm_context = ""
        get_rhythm_trend = getattr(self.archive, "get_physiological_rhythm_trend", None)
        if callable(get_rhythm_trend):
            rhythm_context = self._format_rhythm_trend_for_state_prompt(
                await get_rhythm_trend(days=7, limit=6)
            )
        prompt = self._build_state_update_prompt(
            data,
            now,
            source,
            detail,
            emotion_context=emotion_context,
            rhythm_context=rhythm_context,
        )
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
            save_rhythm_log = getattr(self.archive, "save_physiological_rhythm_log", None)
            if callable(save_rhythm_log):
                rhythm_log = physiological_rhythm_log_from_state(state, date=date_str, source=source)
                if rhythm_log:
                    await save_rhythm_log(rhythm_log)
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return data
            save_emotion_arc = getattr(self.archive, "save_emotion_arc", None)
            if callable(save_emotion_arc):
                arc = self._emotion_arc_from_state_payload(
                    result,
                    state,
                    date_str=date_str,
                    now=now,
                    source=source,
                    detail=detail,
                )
                if arc:
                    await save_emotion_arc(arc)
            if notify_page:
                await self.mark_page_status_changed("state")
            return data
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} 更新实时状态失败：{e}")
            return data
        finally:
            await self.composer._cleanup_conversation(session_id)
