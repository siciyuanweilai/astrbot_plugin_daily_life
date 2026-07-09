import datetime
import uuid
from typing import Callable

from astrbot.api import logger

from ..config.vocab import PERIOD_HOURS
from ..labels import (
    outfit_decision_label,
    schedule_intent_label,
    schedule_tone_label,
    sleep_mode_label,
)
from ..prompts import cache_friendly_prompt
from ..clock import now as life_now
from .condition import format_physiological_rhythm_prompt
from .tools import extract_json_from_text, get_current_timeline_status, get_time_period_cn, parse_time_minutes, timeline_item_datetime
from .future import future_outfit_timing_issue
from .fashion import outfit_style_contamination_reason
from .wardrobe import (
    OUTFIT_SCENE_CATEGORY_ENUM,
    normalize_outfit_decision,
    normalize_outfit_scene_category,
    outfit_scene_category_label,
    outfit_style_pool_label,
    style_pool_for_scene_category,
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
        rhythm = state.physiological_rhythm.as_dict() if getattr(state, "physiological_rhythm", None) else {}
        if rhythm:
            lines.append(f"生理节律：{format_physiological_rhythm_prompt(rhythm)}")
        return "\n".join(lines) if lines else "无"

    @staticmethod
    def _outfit_scene_context_text(
        *,
        current_timeline: str,
        next_timeline: str,
        past_timeline: str,
        future_timeline: str,
        weather: str,
        weather_info,
        old_data,
        old_meta: dict,
        daily_theme: str,
        mood_color: str,
        target_period: str,
    ) -> str:
        lines = [
            f"天气：{weather}",
            f"天气温度：{weather_info.temp if weather_info and weather_info.temp is not None else '未知'}°C",
            f"当前穿搭：{old_data.outfit or '未知'}",
            f"今日日程基调：{old_meta.get('life_mode', '未知')}",
            f"今日睡眠倾向：{old_meta.get('sleep_mode', '未知')}",
            f"当前穿搭决定：{outfit_decision_label(old_meta.get('outfit_decision')) or '未知'}",
            f"今日主题：{daily_theme}",
            f"今日心情色彩：{mood_color}（仅供氛围参考）",
            f"当前时间线索：{get_time_period_cn(target_period)}",
            f"当前日程位置：{current_timeline}",
            f"下一项安排：{next_timeline}",
            f"已发生日程：\n{past_timeline}",
            f"未发生日程预告：\n{future_timeline}",
        ]
        return "\n".join(lines)

    async def _outfit_update_context(
        self,
        *,
        old_data,
        date_str: str,
        target_period: str,
        current_time: datetime.datetime,
    ) -> dict:
        timeline_date = old_data.date or date_str
        current_item, next_item = get_current_timeline_status(old_data.timeline, current_time, timeline_date)
        current_timeline = self._timeline_item_text(current_item)
        next_timeline = self._timeline_item_text(next_item)
        past_timeline, future_timeline = self._timeline_context_text(old_data.timeline, current_time, timeline_date)
        old_meta = old_data.meta
        return {
            "old_data": old_data,
            "timeline_date": timeline_date,
            "current_timeline": current_timeline,
            "next_timeline": next_timeline,
            "past_timeline": past_timeline,
            "future_timeline": future_timeline,
            "state_context": self._state_context_text(old_data),
            "weather": old_data.weather or "未知",
            "weather_info": old_data.weather_info,
            "old_meta": old_meta,
            "daily_theme": old_meta.get("theme", "未设定"),
            "mood_color": old_meta.get("mood", "未设定"),
            "inertia": await self._build_life_inertia_context(current_time),
            "autonomy_context": await self._build_autonomous_life_context(current_time),
        }

    def _build_outfit_update_prompt(
        self,
        *,
        context: dict,
        target_period: str,
        current_time: datetime.datetime,
    ) -> str:
        fixed = f"""当前已有生活时间轴；根据当前时间线索、已有日程和实时生活状态，自主判断穿搭是否需要变化。
要求：
1. 只围绕当前实际时间、当前日程位置、实时生活状态和下一项安排判断；全天日程只作为背景。
2. 未发生的未来安排只能作为预告，不能提前覆盖当前穿搭；必须等对应时间/场景实际到达后再换成相应状态。
3. 我可以决定不换装、局部调整、换居家服、换睡衣或换外出服；不要因为时段标签变化而强制换装。
4. 如果当前或下一项安排处于出门、通勤、社交、看展、约会、购物、办事、运动等外出场景，必须先判断当前穿搭是否适合该外出场景和天气。
5. 如果当前穿搭明显偏居家、睡衣、松散休息或不适合天气/场合，不能直接 keep；应选择 partial_change、change 或 outdoor。
6. scene_category 只写当前真实场景，style_pool 由系统根据它自动派生，不需要你额外判断。
7. outfit/style/hair 只写最终视觉状态，不要写原因解释或日程流水账。
8. reason 必须使用自然中文，不要原样写内部枚举。

返回JSON格式：
{{
  "outfit_decision": "keep | change | partial_change | sleepwear | outdoor",
  "scene_category": "{OUTFIT_SCENE_CATEGORY_ENUM}",
  "outfit": "最终穿搭描述；如果 keep 可复述当前穿搭",
  "style": "最终风格",
  "hair": "最终发型",
  "reason": "一句很短的内部原因"
}}
"""
        dynamic = f"""生活日程日期：{context["timeline_date"]}
{self._outfit_scene_context_text(
    current_timeline=context["current_timeline"],
    next_timeline=context["next_timeline"],
    past_timeline=context["past_timeline"],
    future_timeline=context["future_timeline"],
    weather=context["weather"],
    weather_info=context["weather_info"],
    old_data=context["old_data"],
    old_meta=context["old_meta"],
    daily_theme=context["daily_theme"],
    mood_color=context["mood_color"],
    target_period=target_period,
)}
实时生活状态：
{context["state_context"]}
{context["inertia"]}
{context["autonomy_context"]}
当前实际时间：{current_time.strftime("%Y-%m-%d %H:%M")}
当前时间范围：{PERIOD_TIME_RANGES.get(target_period, "未知")}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="穿搭现场")

    async def _apply_outfit_update_result(
        self,
        result: dict,
        *,
        date_str: str,
        target_period: str,
        current_time: datetime.datetime,
        context: dict,
        should_abort: Callable[[], bool] | None = None,
    ):
        old_data = context["old_data"]
        old_meta = context["old_meta"]
        decision = normalize_outfit_decision(result.get("outfit_decision") or result.get("decision"))
        new_outfit = str(result.get("outfit") or "").strip()
        if not new_outfit and decision == "keep":
            new_outfit = old_data.outfit
        if not new_outfit:
            return None
        scene_category = normalize_outfit_scene_category(result.get("scene_category"))
        style_pool = style_pool_for_scene_category(scene_category)
        timing_issue = future_outfit_timing_issue(
            new_outfit,
            old_data.timeline,
            current_time=current_time,
            timeline_date=context["timeline_date"],
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
            "outfit_scene_category": scene_category,
            "outfit_style_pool": style_pool,
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
        await self._save_life_decision_record(
            kind="outfit",
            date=date_str,
            subject=f"{date_str}:{target_period}",
            decision=f"{decision or 'keep'}｜{new_outfit[:160]}",
            reason=self._localize_outfit_reason(result.get("reason")),
            evidence=f"当前：{context['current_timeline']}；下一项：{context['next_timeline']}；天气：{context['weather']}",
            outcome=(
                f"风格：{result.get('style') or ''}；"
                f"发型：{result.get('hair') or ''}；"
                f"场景：{outfit_scene_category_label(scene_category)}；"
                f"风格池：{outfit_style_pool_label(style_pool)}"
            ),
        )
        logger.info(f"[穿搭更新] 已根据自主判断更新 {date_str} 的穿搭状态")
        return old_data

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

            context = await self._outfit_update_context(
                old_data=old_data,
                date_str=date_str,
                target_period=target_period,
                current_time=current_time,
            )

            logger.info(
                f"[穿搭更新] 自主判断穿搭状态：主题「{context['daily_theme']}」，心情「{context['mood_color']}」，"
                f"时间标签「{get_time_period_cn(target_period)}」"
            )
            prompt = self._build_outfit_update_prompt(
                context=context,
                target_period=target_period,
                current_time=current_time,
            )
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
                    return await self._apply_outfit_update_result(
                        result,
                        date_str=date_str,
                        target_period=target_period,
                        current_time=current_time,
                        context=context,
                        should_abort=should_abort,
                    )
            except Exception as e:
                logger.error(f"[穿搭更新] 更新失败：{e}")
            finally:
                await self._cleanup_conversation(update_session_id)
            return None
