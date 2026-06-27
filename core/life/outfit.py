import datetime
import uuid
from typing import Callable

from astrbot.api import logger

from ..config.vocab import OUTFIT_PROMPT_PERIOD_MAP, PERIOD_HOURS
from ..labels import (
    outfit_decision_label,
    schedule_intent_label,
    schedule_tone_label,
    sleep_mode_label,
)
from ..prompts import DEFAULT_OUTFIT_PROMPTS, cache_friendly_prompt
from ..clock import now as life_now
from .tools import (
    extract_json_from_text,
    format_timeline_to_text,
    get_current_timeline_status,
    parse_time_minutes,
    timeline_item_datetime,
    get_time_period_cn,
)
from .future import future_outfit_timing_issue
from .style import outfit_style_contamination_reason
from .wardrobe import (
    OUTFIT_SCENE_CATEGORY_ENUM,
    OUTFIT_SCENE_STYLE_RULES,
    OUTFIT_STYLE_POOL_ENUM,
)


PERIOD_TIME_RANGES = {
    "dawn": "00:00-06:00",
    "morning": "06:00-09:00",
    "forenoon": "09:00-12:00",
    "noon": "12:00-14:00",
    "afternoon": "14:00-16:00",
    "evening": "16:00-19:00",
    "night": "19:00-22:00",
    "late_night": "22:00-24:00",
}


class OutfitMixin:
    @staticmethod
    def _replace_enum_token(text: str, token: str, label: str) -> str:
        if not token or token not in text:
            return text
        result = []
        cursor = 0
        size = len(token)
        while True:
            index = text.find(token, cursor)
            if index < 0:
                result.append(text[cursor:])
                break
            before = text[index - 1] if index > 0 else ""
            after_index = index + size
            after = text[after_index] if after_index < len(text) else ""
            if (before.isascii() and (before.isalnum() or before == "_")) or (
                after.isascii() and (after.isalnum() or after == "_")
            ):
                result.append(text[cursor:after_index])
            else:
                result.append(text[cursor:index])
                result.append(label)
            cursor = after_index
        return "".join(result)

    @classmethod
    def _localize_outfit_reason(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        labels = {}
        for getter in (
            outfit_decision_label,
            schedule_tone_label,
            schedule_intent_label,
            sleep_mode_label,
        ):
            for token in (
                "keep",
                "change",
                "partial_change",
                "sleepwear",
                "outdoor",
                "awake",
                "sleeping",
                "late_night",
                "all_nighter",
                "resting",
                "relax",
                "relaxing",
                "going_out",
                "mixed",
                "home",
                "work",
                "study",
                "social",
                "rest",
                "outing",
                "active",
                "normal",
                "nap",
                "early_sleep",
                "asleep",
            ):
                label = getter(token)
                if label and label != token:
                    labels.setdefault(token, label)
        for token in sorted(labels, key=len, reverse=True):
            text = cls._replace_enum_token(text, token, labels[token])
        return text

    def _get_outfit_prompt(self, period: str = None) -> str:
        if period is None:
            period = self._get_curr_period()
        prompt_period = OUTFIT_PROMPT_PERIOD_MAP.get(period, period)
        return self.config.outfit_prompts.get(
            prompt_period,
            DEFAULT_OUTFIT_PROMPTS.get(prompt_period, DEFAULT_OUTFIT_PROMPTS["daytime"]),
        )

    @staticmethod
    def _timeline_item_text(item: object) -> str:
        if not item:
            return "无"
        time = str(getattr(item, "time", "") if hasattr(item, "time") else item.get("time", "")).strip()
        activity = str(getattr(item, "activity", "") if hasattr(item, "activity") else item.get("activity", "")).strip()
        status = str(getattr(item, "status", "") if hasattr(item, "status") else item.get("status", "")).strip()
        prefix = f"{time} - " if time else ""
        suffix = f" [{status}]" if status else ""
        return f"{prefix}{activity or '未记录'}{suffix}"

    @classmethod
    def _timeline_context_text(
        cls,
        timeline: list,
        current_time: datetime.datetime,
        timeline_date: object = None,
    ) -> tuple[str, str]:
        if not timeline:
            return "暂无已发生日程", "暂无未发生日程"

        now_minutes = current_time.hour * 60 + current_time.minute
        past_lines: list[str] = []
        future_lines: list[str] = []
        for item in timeline:
            item_time = str(getattr(item, "time", "") if hasattr(item, "time") else item.get("time", "")).strip()
            item_minutes = parse_time_minutes(item_time)
            line = cls._timeline_item_text(item)
            item_datetime = timeline_item_datetime(item, timeline_date)
            if item_datetime is not None:
                if item_datetime <= current_time:
                    past_lines.append(line)
                else:
                    delta = max(1, round((item_datetime - current_time).total_seconds() / 60))
                    future_lines.append(f"{line}（约 {delta} 分钟后，尚未发生）")
            elif item_minutes <= now_minutes:
                past_lines.append(line)
            else:
                delta = item_minutes - now_minutes
                future_lines.append(f"{line}（约 {delta} 分钟后，尚未发生）")
        return "\n".join(past_lines) or "暂无已发生日程", "\n".join(future_lines) or "暂无未发生日程"

    @staticmethod
    def _state_context_text(data) -> str:
        state = getattr(data, "state", None)
        if not state:
            return "无"
        lines = []
        if state.summary:
            lines.append(f"实时状态摘要：{state.summary}")
        if state.mood:
            lines.append(f"实时心情：{state.mood}")
        scores = []
        for label, value in (
            ("体力", state.energy),
            ("出门意愿", state.outgoing),
            ("困倦", state.sleepiness),
            ("互动余力", state.interaction_capacity),
        ):
            if value is not None:
                scores.append(f"{label}{value}/100")
        if scores:
            lines.append("实时数值：" + "，".join(scores))
        if state.interrupt_reason:
            lines.append(f"注意力状态：{state.interrupt_reason}")
        sleep_summary = state.sleep.summary if state.sleep else ""
        if sleep_summary:
            lines.append(f"睡眠影响：{sleep_summary}")
        return "\n".join(lines) if lines else "无"

    async def update_outfit(
        self,
        date_str,
        target_period,
        current_time: datetime.datetime | None = None,
        should_abort: Callable[[], bool] | None = None,
    ):
        current_time = current_time or life_now()
        if should_abort and should_abort():
            return None
        old_data = await self.archive.get_day(date_str)
        if not old_data:
            try:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                target_date = life_now()
            target_hour = PERIOD_HOURS.get(target_period)
            if should_abort and should_abort():
                return None
            return await self.generate_daily(target_date, force=True, target_hour=target_hour)

        async with self._gen_lock:
            if should_abort and should_abort():
                return None
            old_data = await self.archive.get_day(date_str)
            if not old_data:
                return None

            timeline_date = old_data.date or date_str
            current_item, next_item = get_current_timeline_status(old_data.timeline, current_time, timeline_date)
            current_timeline = self._timeline_item_text(current_item)
            next_timeline = self._timeline_item_text(next_item)
            past_timeline, future_timeline = self._timeline_context_text(old_data.timeline, current_time, timeline_date)
            state_context = self._state_context_text(old_data)
            weather = old_data.weather or "未知"
            weather_info = old_data.weather_info
            old_meta = old_data.meta
            daily_theme = old_meta.get("theme", "未设定")
            mood_color = old_meta.get("mood", "未设定")
            catalog = await self._get_catalog_settings()
            inspiration = self._build_catalog_inspiration(catalog)

            logger.info(
                f"[穿搭更新] 自主判断穿搭状态：主题「{daily_theme}」，心情「{mood_color}」，"
                f"时间标签「{get_time_period_cn(target_period)}」"
            )
            fixed = f"""当前已有生活日时间轴；根据当前时间线索、已有日程和生活日程基调，自主判断穿搭是否需要变化。
要求：
1. 判断只能围绕当前实际时间、当前日程位置、实时生活状态和下一项安排；全天日程只作为背景。
2. 未发生的未来安排只能作为预告，不能提前覆盖当前穿搭；必须等对应时间/场景实际到达后，才可以换成那一项描述里的居家服、睡衣或睡前状态。
3. 我可以决定不换装、局部调整、换居家服、换睡衣或换外出服；不要因为时段标签变化而强制换装。
4. 必须与天气、当前/下一项日程、实时生活状态和今日日程基调相符。
5. 如果当前或下一项安排处于出门、通勤、社交、看展、约会、购物、办事、运动等外出场景，必须先判断【当前穿搭】是否适合该外出场景和天气。
6. 如果当前穿搭明显偏居家、睡衣、松散休息或不适合天气/场合，不能直接 keep；应选择 partial_change、change 或 outdoor，并在 outfit 写出外出后的最终视觉状态。
7. 如果选择 keep，表示当前穿搭本身已经适合接下来的外出/活动；reason 里用一句话说明“当前穿搭已适合外出”或“今天无外出需求”。
8. 如果当前仍在外出、路上、购物、吃饭或约会中，不能换成“赤脚/棉袜/睡裙/家居连衣裙/洗澡后/睡前”等室内状态，除非当前日程位置已经明确回到室内。
9. 场景与风格池合同：
{OUTFIT_SCENE_STYLE_RULES}
10. outfit 只写最终视觉状态，不要写原因解释或日程流水账。
11. reason 必须使用自然中文；不要把 keep、outdoor、relaxing、resting、going_out 等内部枚举原样写出来。

返回JSON格式：
{{
  "outfit_decision": "keep | change | partial_change | sleepwear | outdoor",
  "scene_category": "{OUTFIT_SCENE_CATEGORY_ENUM}",
  "style_pool": "{OUTFIT_STYLE_POOL_ENUM}",
  "outfit": "最终穿搭描述；如果 keep 可复述当前穿搭",
  "style": "最终风格",
  "hair": "最终发型",
  "reason": "一句很短的内部原因"
}}
"""
            dynamic = f"""当前实际时间：{current_time.strftime("%Y-%m-%d %H:%M")}
生活日程日期：{timeline_date}（如果和当前实际日期不同，表示凌晨延续该日程）
当前时间线索：{get_time_period_cn(target_period)}（只是当前时段标签，不是强制换装/睡觉/外出的规则）
当前时间范围：{PERIOD_TIME_RANGES.get(target_period, "未知")}
当前日程位置：{current_timeline}
下一项安排：{next_timeline}
已发生日程：
{past_timeline}
未发生日程预告（只能作为后续参考，不能提前生效）：
{future_timeline}
天气：{weather}
天气温度：{weather_info.temp if weather_info.temp is not None else '未知'}°C
当前穿搭：{old_data.outfit or '未知'}
今日日程基调：{old_meta.get('life_mode', '未知')}
今日睡眠倾向：{old_meta.get('sleep_mode', '未知')}
当前穿搭决定：{outfit_decision_label(old_meta.get('outfit_decision')) or '未知'}
今日主题/心情：{daily_theme} · {mood_color}
实时生活状态：
{state_context}
{inspiration}"""
            prompt = cache_friendly_prompt(fixed, dynamic, dynamic_title="穿搭现场")
            update_session_id = f"daily_life_outfit_{uuid.uuid4().hex[:8]}"
            try:
                provider_id = self._task_provider_id(self.config.outfit.provider)
                provider = await self._get_provider(provider_id)
                if not provider:
                    return None
                completion_text = await self._call_llm_text(
                    provider,
                    prompt,
                    update_session_id,
                    primary_provider_id=provider_id,
                )
                if not completion_text:
                    return None
                if should_abort and should_abort():
                    return None

                result = extract_json_from_text(completion_text)
                if result:
                    decision = str(result.get("outfit_decision") or result.get("decision") or "").strip()
                    new_outfit = str(result.get("outfit") or "").strip()
                    if not new_outfit and decision == "keep":
                        new_outfit = old_data.outfit
                    if not new_outfit:
                        return None
                    timing_issue = future_outfit_timing_issue(
                        new_outfit,
                        old_data.timeline,
                        current_time=current_time,
                        timeline_date=timeline_date,
                    )
                    if timing_issue:
                        logger.warning(f"[穿搭更新] 已忽略提前换装结果：{timing_issue}")
                        return None
                    style_issue = outfit_style_contamination_reason(
                        result.get("style"),
                        theme=old_meta.get("theme"),
                        mood=old_meta.get("mood"),
                        schedule_type=old_meta.get("schedule_type"),
                    )
                    if style_issue:
                        logger.warning(f"[穿搭更新] 已忽略穿搭风格异常结果：{style_issue}")
                        return None
                    if should_abort and should_abort():
                        return None
                    old_data.outfit_history[target_period] = new_outfit
                    for key, value in {
                        "outfit_decision": decision,
                        "outfit_scene_category": result.get("scene_category"),
                        "outfit_style_pool": result.get("style_pool"),
                        "style": result.get("style"),
                        "hair": result.get("hair"),
                        "outfit_reason": self._localize_outfit_reason(result.get("reason")),
                    }.items():
                        text = str(value or "").strip()
                        if text:
                            old_data.meta[key] = text
                    old_data.outfit = new_outfit
                    old_data.time_period = target_period
                    await self.archive.save_day(old_data)
                    logger.info(f"[穿搭更新] 已根据自主判断更新 {date_str} 的穿搭状态")
                    return old_data
            except Exception as e:
                logger.error(f"[穿搭更新] 更新失败：{e}")
            finally:
                await self._cleanup_conversation(update_session_id)
            return None
