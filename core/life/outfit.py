import datetime
import time
import uuid
from collections.abc import Callable

from astrbot.api import logger

from ..archive import DayRevisionConflict
from ..clock import now as life_now
from ..config.vocab import PERIOD_HOURS
from ..labels import (
    outfit_decision_label,
    schedule_intent_label,
    schedule_tone_label,
    sleep_mode_label,
)
from ..models.coerce import compact_explanation_text
from ..prompts import cache_friendly_prompt
from .appearance import (
    APPEARANCE_PREFERENCE_CATEGORIES,
    CURRENT_APPEARANCE_GENERATION_RULES,
    format_life_preference_context,
    normalize_appearance_fact,
    strip_hair_from_outfit,
)
from .condition import format_physiological_rhythm_prompt
from .fashion import outfit_style_contamination_reason
from .future import future_outfit_timing_issue
from .tools import (
    extract_json_from_text,
    format_timeline_travel,
    get_current_timeline_status,
    get_time_period_cn,
    parse_time_minutes,
    timeline_item_datetime,
)
from .wardrobe import (
    OUTFIT_CONTINUITY_RULES,
    OUTFIT_CURRENT_BASIS_ENUM,
    OUTFIT_SCENE_CATEGORY_ENUM,
    decision_for_occurred_outfit,
    normalize_outfit_current_basis,
    normalize_outfit_decision,
    normalize_outfit_scene_category,
    outfit_scene_category_label,
    outfit_style_pool_label,
    resolve_outfit_style_pool,
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

_OUTFIT_FACT_SOURCE_LABELS = {
    "user_instruction": "用户明确确认",
    "life_action": "已结算生活动作",
    "occurred_schedule": "已发生日程",
    "live_state": "实时生活状态",
    "autonomous": "自主生活判断",
    "daily_generation": "当日日程生成",
    "carried_previous_day": "前一日延续",
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
        text = compact_explanation_text(value, 360)
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
    def _compact_outfit_evidence(value: object, limit: int = 240) -> str:
        return " ".join(str(value or "").strip().split())[:limit]

    @classmethod
    def _outfit_fact_context_text(cls, meta: dict) -> str:
        source = str(meta.get("outfit_fact_source") or "").strip()
        source_label = _OUTFIT_FACT_SOURCE_LABELS.get(source, "普通历史记录")
        confirmed_at = cls._compact_outfit_evidence(
            meta.get("outfit_fact_confirmed_at"), 40
        )
        evidence = cls._compact_outfit_evidence(meta.get("outfit_fact_evidence"), 120)
        lines = [f"当前穿搭事实来源：{source_label}"]
        if confirmed_at:
            lines.append(f"当前穿搭确认时间：{confirmed_at}")
        if evidence:
            lines.append(f"当前穿搭确认依据：{evidence}")
        if source == "user_instruction":
            lines.append(
                "当前穿搭是用户明确要求后保存的已确认事实；普通舒适度判断、未来休息安排或模型偏好不能覆盖。"
            )
        return "\n".join(lines)

    @classmethod
    def _state_outfit_evidence_text(cls, data) -> str:
        state = getattr(data, "state", None)
        values = []
        if state:
            values.extend(
                (
                    getattr(state, "summary", ""),
                    getattr(state, "interrupt_reason", ""),
                    getattr(getattr(state, "sleep", None), "summary", ""),
                    getattr(
                        getattr(state, "physiological_rhythm", None), "summary", ""
                    ),
                )
            )
        values.extend(list(getattr(data, "state_log", []) or [])[-4:])
        return "\n".join(
            text
            for value in values
            if (text := cls._compact_outfit_evidence(value, 360))
        )

    @classmethod
    def _verified_outfit_change_source(cls, result: dict, context: dict) -> str:
        evidence = (
            result.get("change_evidence")
            if isinstance(result.get("change_evidence"), dict)
            else {}
        )
        if str(evidence.get("kind") or "").strip() != "explicit_outfit_change":
            return ""
        source = str(evidence.get("source") or "").strip()
        quote = cls._compact_outfit_evidence(evidence.get("quote"), 360)
        if not quote:
            return ""
        if source == "occurred_schedule":
            timeline_time = str(evidence.get("timeline_time") or "").strip()
            for item in context.get("occurred_timeline_items", []):
                item_time = str(getattr(item, "time", "") or "").strip()
                if not timeline_time or item_time != timeline_time:
                    continue
                item_evidence = "\n".join(
                    cls._compact_outfit_evidence(getattr(item, field, ""), 360)
                    for field in ("activity", "status", "execution_evidence")
                )
                if quote in item_evidence:
                    return "occurred_schedule"
            return ""
        if source == "live_state" and quote in context.get("state_evidence", ""):
            return "live_state"
        return ""

    @staticmethod
    def _timeline_item_text(item: object, *, previous_place: str = "") -> str:
        if not item:
            return "无"
        time = str(
            getattr(item, "time", "") if hasattr(item, "time") else item.get("time", "")
        ).strip()
        activity = str(
            getattr(item, "activity", "")
            if hasattr(item, "activity")
            else item.get("activity", "")
        ).strip()
        status = str(
            getattr(item, "status", "")
            if hasattr(item, "status")
            else item.get("status", "")
        ).strip()
        prefix = f"{time} - " if time else ""
        suffix = f" [{status}]" if status else ""
        text = f"{prefix}{activity or '未记录'}{suffix}"
        travel = format_timeline_travel(
            item,
            previous_place=previous_place,
            include_provider=False,
        )
        return f"{text}；出行：{travel}" if travel else text

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
        previous_place = ""
        for item in timeline:
            item_time = str(
                getattr(item, "time", "")
                if hasattr(item, "time")
                else item.get("time", "")
            ).strip()
            item_minutes = parse_time_minutes(item_time)
            line = cls._timeline_item_text(item, previous_place=previous_place)
            item_datetime = timeline_item_datetime(item, timeline_date)
            if item_datetime is not None:
                if item_datetime <= current_time:
                    past_lines.append(line)
                else:
                    delta = max(
                        1, round((item_datetime - current_time).total_seconds() / 60)
                    )
                    future_lines.append(f"{line}（约 {delta} 分钟后，尚未发生）")
            elif item_minutes <= now_minutes:
                past_lines.append(line)
            else:
                delta = item_minutes - now_minutes
                future_lines.append(f"{line}（约 {delta} 分钟后，尚未发生）")
            item_place = str(
                getattr(item, "place", "")
                if hasattr(item, "place")
                else item.get("place", "")
            ).strip()
            if item_place:
                previous_place = item_place
        return "\n".join(past_lines) or "暂无已发生日程", "\n".join(
            future_lines
        ) or "暂无未发生日程"

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
        rhythm = (
            state.physiological_rhythm.as_dict()
            if getattr(state, "physiological_rhythm", None)
            else {}
        )
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
            f"当前发型名称：{old_meta.get('hair_style') or '未知'}",
            f"当前发型细节：{old_meta.get('hair') or '未知'}",
            f"当前妆容：{old_meta.get('makeup') or '未知'}",
            f"当前美甲：{old_meta.get('nails') or '未知'}",
            f"当前穿着场景：{outfit_scene_category_label(old_meta.get('outfit_scene_category')) if old_meta.get('outfit_scene_category') else '未知'}",
            f"当前穿着风格池：{outfit_style_pool_label(old_meta.get('outfit_style_pool')) if old_meta.get('outfit_style_pool') else '未知'}",
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
        instruction: str = "",
    ) -> dict:
        timeline_date = old_data.date or date_str
        current_item, next_item = get_current_timeline_status(
            old_data.timeline, current_time, timeline_date
        )
        current_timeline = self._timeline_item_text(current_item)
        next_timeline = self._timeline_item_text(next_item)
        past_timeline, future_timeline = self._timeline_context_text(
            old_data.timeline, current_time, timeline_date
        )
        occurred_timeline_items = []
        for item in old_data.timeline:
            item_time = timeline_item_datetime(item, timeline_date)
            if item_time is not None and item_time <= current_time:
                occurred_timeline_items.append(item)
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
            "outfit_fact_context": self._outfit_fact_context_text(old_meta),
            "occurred_timeline_items": occurred_timeline_items,
            "state_evidence": self._state_outfit_evidence_text(old_data),
            "daily_theme": old_meta.get("theme", "未设定"),
            "mood_color": old_meta.get("mood", "未设定"),
            "instruction": str(instruction or "").strip(),
            "appearance_context": await self._outfit_appearance_context(),
            "style_catalog_context": await self._style_catalog_context(limit=10),
        }

    async def _outfit_appearance_context(self) -> str:
        preference_limit = max(0, self.config.lifecycle.max_preferences)
        preferences = []
        if preference_limit:
            for category in APPEARANCE_PREFERENCE_CATEGORIES:
                preferences.extend(
                    await self.archive.get_preferences(preference_limit, category)
                )
        return format_life_preference_context(
            preferences,
            self.config,
            limit=preference_limit,
            appearance_only=True,
        )

    def _build_outfit_update_prompt(
        self,
        *,
        context: dict,
        target_period: str,
        current_time: datetime.datetime,
    ) -> str:
        fixed = f"""当前已有生活时间轴；根据当前时间线索、已有日程和实时生活状态，自主判断穿搭是否需要变化。
共同穿搭规则：
{OUTFIT_CONTINUITY_RULES}

要求：
1. 只围绕当前实际时间、当前日程位置、实时生活状态和下一项安排判断；全天日程只作为背景。
2. 未发生的未来安排只能作为预告，不能提前覆盖当前穿搭；等对应时间/场景实际到达后再换装。
3. 当前或下一项安排需要外出时，先判断现有穿搭是否适合场景和天气；明显不合适时不能直接 keep。
4. current_outfit_basis 用于说明最终穿搭依据：stored 表示数据库中的当前穿搭仍有效；occurred_schedule 表示当前或已发生日程明确完成了换装；live_state 表示实时状态明确确认已经换装。未发生日程不能作为依据。
5. keep 只能与 stored 搭配，并原样返回当前 outfit、style、hair_style、hair、makeup、nails；已经换装则选择 change、partial_change、sleepwear 或 outdoor，不能用 keep 表示“换装后继续穿着”。
6. component_review 必须分别审视主体服装、鞋履、外层、随身配饰、发型、妆容和美甲；不存在的组成写 not_present，无法确认写 unknown。任一组成需要调整时，不能返回 keep。
7. partial_change 只写局部调整后的最终状态；component_review 标记 adjust 时，outfit 必须写调整后实际可见的完整穿搭。
8. outfit/style/hair_style/hair/makeup/nails 只写最终视觉状态；新换装或局部调整时遵循以下描述要求，keep 仍须原样返回已有状态：
{CURRENT_APPEARANCE_GENERATION_RULES}
reason 使用自然中文，不写内部枚举，也不复述具体日期、钟点或时间轴编号。
9. 用户明确提出穿搭要求时，在不违背当前真实场景和天气的前提下优先执行，不能用 keep 回避。
10. 只返回穿搭决策，不得改写时间轴、实时状态、主题、地点、事件或睡眠信息。
11. change_evidence 只描述本轮更换主体服装的事实依据：
- 已发生日程明确记载已经换装时，kind=explicit_outfit_change、source=occurred_schedule，timeline_time 填对应节点时间，quote 必须原样摘录该节点中明确确认换装的短句。
- 实时生活状态明确记载已经换装时，kind=explicit_outfit_change、source=live_state，quote 必须原样摘录实时状态中的确认短句。
- 只是基于舒适度自主建议换装时，kind=comfort_adjustment、source=autonomous；场景变化但没有已发生换装事实时使用 scene_transition；没有变化依据时使用 none。不得把未来安排、普通活动或换装建议标成已经换装。

返回JSON格式：
{{
  "outfit_decision": "keep | change | partial_change | sleepwear | outdoor",
  "current_outfit_basis": "{OUTFIT_CURRENT_BASIS_ENUM}",
  "scene_category": "{OUTFIT_SCENE_CATEGORY_ENUM}",
  "style_pool": "sleep_styles | outfit_styles | mixed",
  "component_review": {{"main_clothing": "keep | adjust | not_present | unknown", "footwear": "keep | adjust | not_present | unknown", "outer_layer": "keep | adjust | not_present | unknown", "carried_accessories": "keep | adjust | not_present | unknown", "hair": "keep | adjust | not_present | unknown", "makeup": "keep | adjust | not_present | unknown", "nails": "keep | adjust | not_present | unknown"}},
  "outfit": "当前实际可见的详细穿搭；keep 时必须原样返回当前穿搭",
  "style": "简短的最终风格",
  "hair_style": "简短发型名称",
  "hair": "当前可见的详细发型",
  "makeup": "当前实际妆容或空字符串",
  "nails": "当前实际美甲或空字符串",
  "catalog_reference_ids": ["实际采用的视觉衣橱候选编号"],
  "reason": "一句很短的内部原因",
  "change_evidence": {{"kind": "none | explicit_outfit_change | comfort_adjustment | scene_transition", "source": "stored | occurred_schedule | live_state | autonomous", "timeline_time": "已发生节点时间或空字符串", "quote": "原样证据短句或空字符串"}}
}}
"""
        dynamic = f"""生活日程日期：{context["timeline_date"]}
{
            self._outfit_scene_context_text(
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
            )
        }
当前穿搭事实：
{context["outfit_fact_context"]}
实时生活状态：
{context["state_context"]}
长期审美偏好：
{context["appearance_context"] or "无"}
视觉衣橱候选：
{context["style_catalog_context"] or "无"}
候选仅在本轮确实生成新造型时使用；keep 时必须返回空数组。可以采用完整套装，也可以组合上装、下装、鞋袜和配饰；发型、妆容、美甲分别选择。实际采用的编号写入 catalog_reference_ids，未采用写空数组。
当前实际时间：{current_time.strftime("%Y-%m-%d %H:%M")}
当前时间范围：{PERIOD_TIME_RANGES.get(target_period, "未知")}
用户本次明确穿搭要求：{context["instruction"] or "无"}"""
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
        decision = normalize_outfit_decision(
            result.get("outfit_decision") or result.get("decision")
        )
        current_basis = normalize_outfit_current_basis(
            result.get("current_outfit_basis")
        )
        verified_change_source = self._verified_outfit_change_source(result, context)
        generated_outfit = str(result.get("outfit") or "").strip()
        generated_style = normalize_appearance_fact(result.get("style"), 120)
        generated_hair_style = normalize_appearance_fact(
            result.get("hair_style"), 80
        )
        generated_hair = normalize_appearance_fact(result.get("hair"), 180)
        generated_makeup = normalize_appearance_fact(result.get("makeup"), 160)
        generated_nails = normalize_appearance_fact(result.get("nails"), 160)
        component_review = (
            result.get("component_review")
            if isinstance(result.get("component_review"), dict)
            else {}
        )
        scene_category = normalize_outfit_scene_category(
            result.get("scene_category"), default=""
        ) or normalize_outfit_scene_category(
            old_meta.get("outfit_scene_category"), default="mixed"
        )
        old_outfit = str(old_data.outfit or "").strip()
        old_style = normalize_appearance_fact(old_meta.get("style"), 120)
        old_hair_style = normalize_appearance_fact(old_meta.get("hair_style"), 80)
        old_hair = normalize_appearance_fact(old_meta.get("hair"), 180)
        old_makeup = normalize_appearance_fact(old_meta.get("makeup"), 160)
        old_nails = normalize_appearance_fact(old_meta.get("nails"), 160)
        occurred_outfit_change = (
            decision == "keep"
            and current_basis in {"occurred_schedule", "live_state"}
            and bool(generated_outfit)
            and generated_outfit != old_outfit
        )
        if occurred_outfit_change:
            decision = decision_for_occurred_outfit(
                scene_category,
                result.get("style_pool"),
            )
            basis_label = {
                "occurred_schedule": "已发生日程",
                "live_state": "实时状态",
            }.get(current_basis, "当前记录")
            logger.debug(
                "[穿搭更新] 已按发生后的生活状态校正穿搭决定："
                f"依据={basis_label}；决定={outfit_decision_label(decision)}"
            )
        component_states = {
            str(key or "").strip(): str(value or "").strip().lower()
            for key, value in component_review.items()
        }
        clothing_component_adjusted = any(
            component_states.get(key) == "adjust"
            for key in (
                "main_clothing",
                "footwear",
                "outer_layer",
                "carried_accessories",
            )
        )
        generated_appearance_differs = any(
            (
                bool(generated_outfit) and generated_outfit != old_outfit,
                bool(generated_style) and generated_style != old_style,
                bool(generated_hair_style)
                and generated_hair_style != old_hair_style,
                bool(generated_hair) and generated_hair != old_hair,
                bool(generated_makeup) and generated_makeup != old_makeup,
                bool(generated_nails) and generated_nails != old_nails,
            )
        )
        reviewed_partial_change = decision == "keep" and any(
            (
                clothing_component_adjusted
                and bool(generated_outfit)
                and generated_outfit != old_outfit,
                component_states.get("hair") == "adjust"
                and bool(generated_hair_style or generated_hair)
                and (generated_hair_style, generated_hair)
                != (old_hair_style, old_hair),
                component_states.get("makeup") == "adjust"
                and bool(generated_makeup)
                and generated_makeup != old_makeup,
                component_states.get("nails") == "adjust"
                and bool(generated_nails)
                and generated_nails != old_nails,
                bool(context.get("instruction")) and generated_appearance_differs,
            )
        )
        if reviewed_partial_change:
            decision = "partial_change"
            logger.debug("[穿搭更新] 已按组成部分审视结果校正穿搭决定：决定=局部调整")
        reference_ids = (
            self._style_catalog_reference_ids(result.get("catalog_reference_ids"))
            if decision != "keep"
            else []
        )
        if reference_ids:
            catalog_appearance = await self._style_catalog_reference_appearance(
                reference_ids
            )
            generated_outfit = generated_outfit or catalog_appearance.get(
                "outfit", ""
            )
            generated_hair_style = generated_hair_style or catalog_appearance.get(
                "hair_style", ""
            )
            generated_hair = generated_hair or catalog_appearance.get("hair", "")
            generated_makeup = generated_makeup or catalog_appearance.get(
                "makeup", ""
            )
            generated_nails = generated_nails or catalog_appearance.get("nails", "")
        if decision == "keep":
            new_outfit = old_outfit
            model_kept_outfit = not generated_outfit or generated_outfit == new_outfit
            final_style = old_style or (generated_style if model_kept_outfit else "")
            final_hair_style = old_hair_style or (
                generated_hair_style if model_kept_outfit else ""
            )
            final_hair = old_hair or (generated_hair if model_kept_outfit else "")
            final_makeup = old_makeup or (
                generated_makeup if model_kept_outfit else ""
            )
            final_nails = old_nails or (generated_nails if model_kept_outfit else "")
        elif decision == "partial_change":
            new_outfit = generated_outfit or old_outfit
            final_style = generated_style or old_style
            final_hair_style = generated_hair_style or old_hair_style
            final_hair = generated_hair or old_hair
            final_makeup = generated_makeup or old_makeup
            final_nails = generated_nails or old_nails
        else:
            new_outfit = generated_outfit
            final_style = generated_style
            final_hair_style = generated_hair_style
            final_hair = generated_hair
            final_makeup = generated_makeup or old_makeup
            final_nails = generated_nails or old_nails
        new_outfit = strip_hair_from_outfit(
            new_outfit,
            final_hair_style,
            final_hair,
        )
        if not new_outfit:
            return None
        appearance_changed = any(
            (
                new_outfit != old_outfit,
                final_style != old_style,
                final_hair_style != old_hair_style,
                final_hair != old_hair,
                final_makeup != old_makeup,
                final_nails != old_nails,
            )
        )
        user_confirmed = (
            str(old_meta.get("outfit_fact_source") or "").strip()
            == "user_instruction"
        )
        if (
            user_confirmed
            and not context.get("instruction")
            and decision != "keep"
            and appearance_changed
            and not verified_change_source
        ):
            logger.info(
                "[穿搭更新] 已保留用户确认的当前穿搭：本轮没有已发生换装证据"
            )
            return old_data
        style_pool = resolve_outfit_style_pool(
            scene_category,
            decision=decision,
            requested=result.get("style_pool"),
            current=old_meta.get("outfit_style_pool"),
        )
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
            final_style,
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
        clearable_keys = {
            "style",
            "hair_style",
            "hair",
            "makeup",
            "nails",
            "outfit_reason",
        }
        for key, value in {
            "outfit_decision": decision,
            "outfit_scene_category": scene_category,
            "outfit_style_pool": style_pool,
            "style": final_style,
            "hair_style": final_hair_style,
            "hair": final_hair,
            "makeup": final_makeup,
            "nails": final_nails,
            "outfit_reason": self._localize_outfit_reason(result.get("reason")),
        }.items():
            text = str(value or "").strip()
            if text:
                old_data.meta[key] = text
            elif key in clearable_keys:
                old_data.meta.pop(key, None)
        if context.get("instruction"):
            old_data.meta["outfit_fact_source"] = "user_instruction"
            old_data.meta["outfit_fact_confirmed_at"] = current_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            old_data.meta["outfit_fact_evidence"] = "用户本轮明确穿搭要求"
        elif decision != "keep" and appearance_changed:
            old_data.meta["outfit_fact_source"] = (
                verified_change_source or "autonomous"
            )
            old_data.meta["outfit_fact_confirmed_at"] = current_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            evidence = (
                result.get("change_evidence")
                if isinstance(result.get("change_evidence"), dict)
                else {}
            )
            evidence_text = self._compact_outfit_evidence(
                evidence.get("quote") or result.get("reason"), 160
            )
            if evidence_text:
                old_data.meta["outfit_fact_evidence"] = evidence_text
            else:
                old_data.meta.pop("outfit_fact_evidence", None)
        old_data.outfit = new_outfit
        old_data.time_period = target_period
        if reference_ids:
            old_data.meta["style_catalog_reference_ids"] = ",".join(
                str(item) for item in reference_ids
            )
        await self.archive.save_day(old_data)
        if reference_ids:
            await self._mark_style_catalog_references(reference_ids)
        outcome_parts = [f"风格：{final_style}"]
        if final_hair_style:
            outcome_parts.append(f"发型名称：{final_hair_style}")
        if final_hair:
            outcome_parts.append(f"发型细节：{final_hair}")
        if final_makeup:
            outcome_parts.append(f"妆容：{final_makeup}")
        if final_nails:
            outcome_parts.append(f"美甲：{final_nails}")
        outcome_parts.extend(
            (
                f"场景：{outfit_scene_category_label(scene_category)}",
                f"风格池：{outfit_style_pool_label(style_pool)}",
            )
        )
        # 保持原穿搭同样是一次已审计的生活决策。记录它能说明为何没有
        # 换装，也让后续日程重排、回顾和穿搭连续性判断拥有完整证据链。
        await self._save_life_decision_record(
            kind="outfit",
            date=date_str,
            subject=f"{date_str}:{target_period}",
            decision=f"{decision or 'keep'}｜{new_outfit[:160]}",
            reason=self._localize_outfit_reason(result.get("reason")),
            evidence=(
                f"当前：{context['current_timeline']}；下一项：{context['next_timeline']}；"
                f"天气：{context['weather']}；用户要求：{context['instruction'] or '无'}"
            ),
            outcome="；".join(outcome_parts),
        )
        return old_data

    async def update_outfit(
        self,
        date_str,
        target_period,
        current_time: datetime.datetime | None = None,
        instruction: str = "",
        should_abort: Callable[[], bool] | None = None,
    ):
        current_time = current_time or life_now()
        instruction = str(instruction or "").strip()
        started_at = time.monotonic()
        if should_abort and should_abort():
            return None
        old_data = await self.archive.get_day(date_str)
        if not old_data:
            logger.debug(f"[穿搭更新] {date_str} 尚无生活安排，先生成基础日程")
            try:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                target_date = life_now()
            target_hour = PERIOD_HOURS.get(target_period)
            if should_abort and should_abort():
                return None
            return await self.generate_daily(
                target_date,
                force=True,
                target_hour=target_hour,
                extra=instruction,
            )

        logger.info(
            f"[穿搭更新] 开始：日期={date_str}；"
            f"时段={get_time_period_cn(target_period)}"
        )
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
                instruction=instruction,
            )

            logger.debug(
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
                    updated = await self._apply_outfit_update_result(
                        result,
                        date_str=date_str,
                        target_period=target_period,
                        current_time=current_time,
                        context=context,
                        should_abort=should_abort,
                    )
                    if updated is not None:
                        logger.info(
                            f"[穿搭更新] 完成：日期={date_str}；日程保持不变；"
                            f"耗时={time.monotonic() - started_at:.2f} 秒"
                        )
                    return updated
            except DayRevisionConflict as exc:
                logger.debug(f"[穿搭更新] 模型结果已过期，保留较新的穿搭：{exc}")
            except Exception as e:
                logger.error(f"[穿搭更新] 更新失败：{e}")
            finally:
                await self._cleanup_conversation(update_session_id)
            return None
