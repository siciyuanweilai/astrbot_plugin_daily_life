from __future__ import annotations

import copy
import datetime
import json
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

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
from ..models import LifeActionIntent, normalize_timeline_day_offsets
from ..models.coerce import compact_explanation_text
from ..prompts import cache_friendly_prompt
from .actionchain import execution_evidence
from .appearance import (
    APPEARANCE_PREFERENCE_CATEGORIES,
    CURRENT_APPEARANCE_GENERATION_RULES,
    format_life_preference_context,
    strip_hair_from_outfit,
)
from .condition import format_physiological_rhythm_prompt
from .dressing import wardrobe_conditions
from .fashion import outfit_style_contamination_reason
from .future import future_outfit_timing_issue
from .lookback import record_appearance_snapshot
from .outfitstate import (
    OutfitCommit,
    normalize_outfit_appearance,
    prepare_outfit_components,
    review_outfit_decision,
    select_outfit_appearance,
)
from .tools import (
    extract_json_from_text,
    format_timeline_travel,
    get_current_timeline_status,
    get_time_period_cn,
    parse_life_datetime,
    parse_time_minutes,
    timeline_item_datetime,
)
from .wardrobe import (
    OUTFIT_CONTINUITY_RULES,
    OUTFIT_CURRENT_BASIS_ENUM,
    OUTFIT_SCENE_CATEGORY_ENUM,
    format_outfit_component_ledger,
    format_outfit_components,
    merge_outfit_catalog_references,
    normalize_outfit_components,
    normalize_outfit_current_basis,
    normalize_outfit_decision,
    normalize_outfit_scene_category,
    outfit_scene_category_label,
    outfit_style_pool_label,
    project_outfit_components_for_scene,
    resolve_outfit_style_pool,
    scene_category_for_place_kind,
    serialize_outfit_components,
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
    "commitment": "已确认约定",
    "life_action": "已结算生活动作",
    "occurred_schedule": "已发生日程",
    "live_state": "实时生活状态",
    "autonomous": "自主生活判断",
    "daily_generation": "当日日程生成",
    "carried_previous_day": "前一日延续",
}


def _has_user_outfit_instruction(context: dict) -> bool:
    return bool(context.get("instruction")) and str(
        context.get("instruction_source") or ""
    ).strip() in {"", "user"}




class OutfitComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _audit_persona_appearance: Any
    _call_llm_text: Any
    _cleanup_conversation: Any
    _compact_outfit_evidence: Any
    _gen_lock: Any
    _get_persona: Any
    _get_provider: Any
    _localize_outfit_reason: Any
    _mark_style_catalog_references: Any
    _outfit_fact_context_text: Any
    _outfit_scene_context_text: Any
    _save_life_decision_record: Any
    _state_context_text: Any
    _state_outfit_evidence_text: Any
    _style_catalog_context: Any
    _style_catalog_has_clothing_candidates: Any
    _style_catalog_has_eligible_clothing: Any
    _style_catalog_new_outfit_selection: Any
    _style_catalog_reference_appearance: Any
    _style_catalog_reference_ids: Any
    _style_catalog_resolve_new_outfit_reference_ids: Any
    _task_provider_id: Any
    _timeline_context_text: Any
    _timeline_item_text: Any
    _verified_outfit_change_source: Any
    archive: Any
    config: Any
    generate_daily: Any
    record_life_action_receipt: Any


class OutfitComponent:
    def __init__(self, context: OutfitComponentContext) -> None:
        self.context = context

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
        fact_confirmed_at = parse_life_datetime(
            context.get("old_meta", {}).get("outfit_fact_confirmed_at")
        )
        if source == "occurred_schedule":
            timeline_time = str(evidence.get("timeline_time") or "").strip()
            for item in context.get("occurred_timeline_items", []):
                if getattr(item, "execution_state", "planned") != "completed":
                    continue
                item_time = str(getattr(item, "time", "") or "").strip()
                if not timeline_time or item_time != timeline_time:
                    continue
                item_datetime = timeline_item_datetime(
                    item, context.get("timeline_date")
                )
                if (
                    fact_confirmed_at is not None
                    and item_datetime is not None
                    and item_datetime <= fact_confirmed_at
                ):
                    continue
                item_evidence = "\n".join(
                    cls._compact_outfit_evidence(getattr(item, field, ""), 360)
                    for field in ("activity", "status", "execution_evidence")
                )
                if quote in item_evidence:
                    return "occurred_schedule"
            return ""
        if source == "live_state" and quote in context.get("state_evidence", ""):
            state_updated_at = parse_life_datetime(context.get("state_updated_at"))
            if fact_confirmed_at is not None and (
                state_updated_at is None or state_updated_at <= fact_confirmed_at
            ):
                return ""
            state_source = str(context.get("state_source") or "").strip().lower()
            if not state_source.startswith(("life_action:", "action_receipt:")):
                return ""
            return "live_state"
        return ""

    @staticmethod
    def _timeline_item_text(
        item: object,
        *,
        previous_place: str = "",
        timeline_date: object = None,
        meta: dict | None = None,
    ) -> str:
        if not item:
            return "无"
        time = str(
            getattr(item, "time", "") if hasattr(item, "time") else item.get("time", "")
        ).strip()
        effective = timeline_item_datetime(item, timeline_date, meta=meta)
        if meta and effective:
            time = effective.strftime("%m-%d %H:%M")
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
        *,
        meta: dict | None = None,
    ) -> tuple[str, str]:
        if not timeline:
            return "暂无已发生日程", "暂无未发生日程"
        normalize_timeline_day_offsets(timeline)

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
            line = cls._timeline_item_text(
                item,
                previous_place=previous_place,
                timeline_date=timeline_date,
                meta=meta,
            )
            item_datetime = timeline_item_datetime(item, timeline_date, meta=meta)
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
        execution = execution_evidence(data)
        if execution["current"] or execution["recent_completed"]:
            lines.append("实际动作执行记录：" + json.dumps(execution, ensure_ascii=False))
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
        current_scene_category: str = "",
        current_outfit: str = "",
        current_components: object = None,
    ) -> str:
        scene_value = current_scene_category or old_meta.get("outfit_scene_category")
        components_value = (
            current_components
            if current_components is not None
            else old_meta.get("outfit_components")
        )
        lines = [
            f"天气：{weather}",
            f"天气温度：{weather_info.temp if weather_info and weather_info.temp is not None else '未知'}°C",
            f"当前穿搭：{current_outfit or old_data.outfit or '未知'}",
            f"当前发型名称：{old_meta.get('hair_style') or '未知'}",
            f"当前发型细节：{old_meta.get('hair') or '未知'}",
            f"当前妆容名称：{old_meta.get('makeup_style') or '未知'}",
            f"当前妆容细节：{old_meta.get('makeup') or '未知'}",
            f"当前美甲名称：{old_meta.get('nails_style') or '未知'}",
            f"当前美甲细节：{old_meta.get('nails') or '未知'}",
            f"当前穿着场景：{outfit_scene_category_label(scene_value) if scene_value else '未知'}",
            f"当前穿着风格池：{outfit_style_pool_label(old_meta.get('outfit_style_pool')) if old_meta.get('outfit_style_pool') else '未知'}",
            f"今日日程基调：{old_meta.get('life_mode', '未知')}",
            f"今日睡眠倾向：{old_meta.get('sleep_mode', '未知')}",
            f"当前穿搭决定：{outfit_decision_label(old_meta.get('outfit_decision')) or '未知'}",
            f"当前穿搭组成状态：{format_outfit_component_ledger(components_value) or '未知'}",
            f"外出备选组成（当前未穿/未携带）：{old_meta.get('outing_outfit_reserve') or '无'}",
            f"今日主题：{daily_theme}",
            f"今日心情色彩：{mood_color}（仅供氛围参考）",
            f"当前时间线索：{get_time_period_cn(target_period)}",
            f"当前日程位置：{current_timeline}",
            f"下一项安排：{next_timeline}",
            f"已到时段的日程（仅已确认完成的节点属于事实）：\n{past_timeline}",
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
        source_instruction: str | None = None,
        instruction_source: str | None = None,
    ) -> dict:
        timeline_date = old_data.date or date_str
        current_item, next_item = get_current_timeline_status(
            old_data.timeline, current_time, timeline_date, meta=old_data.meta
        )
        current_timeline = self.context._timeline_item_text(
            current_item, timeline_date=timeline_date, meta=old_data.meta
        )
        next_timeline = self.context._timeline_item_text(
            next_item, timeline_date=timeline_date, meta=old_data.meta
        )
        past_timeline, future_timeline = self.context._timeline_context_text(
            old_data.timeline, current_time, timeline_date, meta=old_data.meta
        )
        occurred_timeline_items = []
        for item in old_data.timeline:
            item_time = timeline_item_datetime(item, timeline_date, meta=old_data.meta)
            if item_time is not None and item_time <= current_time:
                occurred_timeline_items.append(item)
        old_meta = old_data.meta
        current_scene_category = scene_category_for_place_kind(
            getattr(current_item, "place_kind", "")
            if current_item is not None and not isinstance(current_item, dict)
            else (current_item or {}).get("place_kind", ""),
            default=normalize_outfit_scene_category(
                old_meta.get("outfit_scene_category"), default=""
            ),
        )
        current_components = project_outfit_components_for_scene(
            old_meta.get("outfit_components"), current_scene_category
        )
        current_outfit = format_outfit_components(current_components)
        persona = await self.context._get_persona()
        wardrobe_weather = wardrobe_conditions(old_data, current_time, residence=str(getattr(getattr(self.context.config, "domains", None), "home_address", "") or ""))
        style_catalog_context = await self.context._style_catalog_context(
            limit=14, conditions=wardrobe_weather, scene_category=current_scene_category
        )
        catalog_has_clothing = await self.context._style_catalog_has_clothing_candidates()
        instruction_text = str(instruction or "").strip()
        if not instruction_text:
            pending_request = wardrobe_weather.get("outfit_request") or {}
            instruction_text = str(pending_request.get("instruction") or "").strip()
            if instruction_text:
                instruction_source = "autonomous"
        normalized_instruction_source = (
            str(
                instruction_source
                if instruction_source is not None
                else ("user" if instruction_text else "")
            )
            .strip()
            .lower()
        )
        resolved_source_instruction = (
            instruction_text
            if source_instruction is None
            and normalized_instruction_source in {"", "user"}
            else str(source_instruction or "").strip()
        )
        return {
            "old_data": old_data,
            "timeline_date": timeline_date,
            "current_timeline": current_timeline,
            "current_place_kind": str(
                getattr(current_item, "place_kind", "")
                if current_item is not None and not isinstance(current_item, dict)
                else (current_item or {}).get("place_kind", "")
            ).strip(),
            "current_scene_category": current_scene_category,
            "current_components": current_components or None,
            "current_outfit": current_outfit,
            "next_timeline": next_timeline,
            "past_timeline": past_timeline,
            "future_timeline": future_timeline,
            "state_context": self.context._state_context_text(old_data),
            "weather": old_data.weather or "未知",
            "weather_info": old_data.weather_info,
            "old_meta": old_meta,
            "outfit_fact_context": self.context._outfit_fact_context_text(old_meta),
            "occurred_timeline_items": occurred_timeline_items,
            "state_evidence": self.context._state_outfit_evidence_text(old_data),
            "state_updated_at": getattr(
                getattr(old_data, "state", None), "updated_at", ""
            ),
            "state_source": getattr(getattr(old_data, "state", None), "source", ""),
            "daily_theme": old_meta.get("theme", "未设定"),
            "mood_color": old_meta.get("mood", "未设定"),
            "instruction": instruction_text,
            "instruction_source": normalized_instruction_source,
            "source_instruction": resolved_source_instruction,
            "instruction_source_label": {
                "user": "用户明确要求",
                "commitment": "已确认约定",
                "autonomous": "角色自主决定",
            }.get(normalized_instruction_source, "无明确来源"),
            "persona": str(persona or "").strip(),
            "appearance_context": await self._outfit_appearance_context(
                catalog_backed=catalog_has_clothing
            ),
            "style_catalog_context": style_catalog_context,
            "wardrobe_conditions": wardrobe_weather,
        }

    async def _outfit_appearance_context(self, *, catalog_backed: bool = False) -> str:
        preference_limit = max(0, self.context.config.lifecycle.max_preferences)
        preferences = []
        if preference_limit:
            for category in APPEARANCE_PREFERENCE_CATEGORIES:
                preferences.extend(
                    await self.context.archive.get_preferences(preference_limit, category)
                )
        return format_life_preference_context(
            preferences,
            limit=preference_limit,
            appearance_only=True,
            catalog_backed=catalog_backed,
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
3.1 scene_category 为 home 或 sleep 时，outfit 只能写组成状态中当前仍穿着或携带的内容；已放下、待用或不适配当前场景的组成写入对应状态，不得混入当前 outfit。实际场景转换时再更新组成状态。
4. current_outfit_basis 用于说明最终穿搭依据：stored 表示数据库中的当前穿搭仍有效；occurred_schedule 表示当前或已发生日程明确完成了换装；live_state 表示实时状态明确确认已经换装；action_intent 表示角色决定现在实际执行换装，由执行器校验衣物并结算。未发生日程不能作为完成依据。
5. keep 只能与 stored 搭配，并原样返回当前 outfit、style、hair_style、hair、makeup_style、makeup、nails_style、nails；已经换装则选择 change、partial_change、sleepwear 或 outdoor，不能用 keep 表示“换装后继续穿着”。
6. component_review 必须分别审视主体服装、鞋履、外层、随身配饰、发型、妆容和美甲；不存在的组成写 not_present，无法确认写 unknown。任一组成需要调整时，不能返回 keep。
7. partial_change 只写局部调整后的最终状态；component_review 标记 adjust 时，outfit 必须写调整后实际可见的完整穿搭。
8. outfit/style/hair_style/hair/makeup_style/makeup/nails_style/nails 只写最终视觉状态；新换装或局部调整时遵循以下描述要求，keep 仍须原样返回已有状态：
{CURRENT_APPEARANCE_GENERATION_RULES}
9. reason 使用自然中文，不写内部枚举，也不复述具体日期、钟点或时间轴编号。
10. 用户明确提出穿搭要求时，在不违背当前真实场景和天气的前提下优先执行，不能用 keep 回避。
11. 只返回穿搭决策，不得改写时间轴、实时状态、主题、地点、事件或睡眠信息。
12. change_evidence 描述本轮更换主体服装的依据；完成证据发生时间必须晚于当前穿搭确认时间：
- 只有 execution_state=completed 的日程明确确认已经换装时，kind=explicit_outfit_change、source=occurred_schedule，timeline_time 填对应节点时间，quote 必须原样摘录该节点中明确确认换装的短句。到点、已过和当前计划均不算完成证据。
- 实时生活状态明确记载已经换装时，kind=explicit_outfit_change、source=live_state，quote 必须原样摘录实时状态中的确认短句。
- 当前换装决定使用 kind=action_intent、source=autonomous，由执行器产生完成记录。
- 只是基于舒适度自主建议换装时，kind=comfort_adjustment、source=autonomous；场景变化但没有已发生换装事实时使用 scene_transition；没有变化依据时使用 none。不得把未来安排、普通活动或换装建议标成已经换装。
13. 本轮提供视觉衣橱候选且自主决定产生新主体服装时，必须选择一条完整套装，或同时选择上装与下装；采用编号写入 catalog_reference_ids，未采用候选时写空数组。长期偏好只能帮助比较候选，不能直接改写成具体衣服。用户本轮明确指定衣服时不受此限制。

返回JSON格式：
{{
  "outfit_decision": "keep | change | partial_change | sleepwear | outdoor",
  "current_outfit_basis": "{OUTFIT_CURRENT_BASIS_ENUM}",
  "scene_category": "{OUTFIT_SCENE_CATEGORY_ENUM}",
  "style_pool": "sleep_styles | outfit_styles | mixed",
  "outfit_components": {{"main_clothing": {{"state": "worn | removed | unknown", "description": "当前主体服装"}}, "footwear": {{"state": "worn | removed | unknown", "description": "当前鞋袜或空字符串"}}, "outer_layer": {{"state": "worn | removed | staged | unknown", "description": "当前外层或空字符串"}}, "carried_accessories": {{"state": "worn | carried | removed | staged | unknown", "description": "当前佩戴/携带的配饰或空字符串"}}}},
  "component_review": {{"main_clothing": "keep | adjust | not_present | unknown", "footwear": "keep | adjust | not_present | unknown", "outer_layer": "keep | adjust | not_present | unknown", "carried_accessories": "keep | adjust | not_present | unknown", "hair": "keep | adjust | not_present | unknown", "makeup": "keep | adjust | not_present | unknown", "nails": "keep | adjust | not_present | unknown"}},
  "outfit": "当前实际可见的详细穿搭；keep 时必须原样返回当前穿搭",
  "style": "简短的最终风格",
  "hair_style": "简短发型名称",
  "hair": "当前可见的详细发型",
  "makeup_style": "简短妆容名称",
  "makeup": "当前实际妆容细节或空字符串",
  "nails_style": "简短美甲名称",
  "nails": "当前实际美甲细节或空字符串",
  "catalog_reference_ids": ["实际采用的视觉衣橱候选编号"],
  "reason": "一句很短的内部原因",
  "change_evidence": {{"kind": "none | explicit_outfit_change | action_intent | comfort_adjustment | scene_transition", "source": "stored | occurred_schedule | live_state | autonomous", "timeline_time": "已发生节点时间或空字符串", "quote": "原样证据短句或空字符串"}}
}}
"""
        dynamic = f"""生活日程日期：{context["timeline_date"]}
{
            self.context._outfit_scene_context_text(
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
                current_scene_category=context.get("current_scene_category", ""),
                current_outfit=context.get("current_outfit", ""),
                current_components=context.get("current_components"),
            )
        }
当前穿搭事实：
{context["outfit_fact_context"]}
实时生活状态：
{context["state_context"]}
长期审美偏好：
{context["appearance_context"] or "无"}
角色人设（只提取其中明确的稳定外观事实）：
{context["persona"] or "无"}
视觉衣橱候选：
{context["style_catalog_context"] or "无"}
当前实际时间：{current_time.strftime("%Y-%m-%d %H:%M")}
当前时间范围：{PERIOD_TIME_RANGES.get(target_period, "未知")}
本轮穿搭要求（来源：{context["instruction_source_label"]}）：{
            context["instruction"] or "无"
        }
用户原始穿搭请求（判断是否允许覆盖稳定人设外观，以此为准）：{
            context["source_instruction"] or "无"
        }"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="穿搭现场")

    async def _resolve_outfit_catalog_appearance(
        self,
        reference_ids: list[int],
        *,
        scene_category: str,
        component_only: bool,
        requires_catalog_clothing: bool,
        context: dict,
    ) -> tuple[list[int], dict] | None:
        """选择衣橱候选，并限制局部调整可以替换的服装组成。"""
        user_instruction = _has_user_outfit_instruction(context)
        if component_only:
            appearance = await self.context._style_catalog_reference_appearance(
                reference_ids,
                scene_category="" if user_instruction else scene_category,
            )
            appearance.pop("outfit", None)
            components = normalize_outfit_components(appearance.get("outfit_components"))
            components.pop("main_clothing", None)
            if components:
                appearance["outfit_components"] = serialize_outfit_components(components)
            else:
                appearance.pop("outfit_components", None)
        elif requires_catalog_clothing and not user_instruction:
            selection_kwargs = {"scene_category": scene_category}
            if context.get("wardrobe_conditions") is not None:
                selection_kwargs["conditions"] = context["wardrobe_conditions"]
            resolved_ids = await self.context._style_catalog_resolve_new_outfit_reference_ids(
                reference_ids, **selection_kwargs,
            )
            if resolved_ids != reference_ids:
                logger.debug(
                    "[穿搭更新] 模型未提供完整衣橱编号，已自动采用候选："
                    + ",".join(str(item) for item in resolved_ids)
                )
                reference_ids = resolved_ids
            appearance, issue = await self.context._style_catalog_new_outfit_selection(
                reference_ids, **selection_kwargs,
            )
            if issue:
                context["catalog_selection_issue"] = issue
                context["catalog_selection_scene"] = scene_category
                return None
        else:
            appearance = await self.context._style_catalog_reference_appearance(
                reference_ids,
                scene_category="" if user_instruction else scene_category,
            )
        return reference_ids, appearance

    async def _record_outfit_update_decision(
        self, data, *, date_str: str, target_period: str, decision: str,
        result: dict, context: dict,
    ) -> None:
        """记录已保存的服饰结果，让保持原样与换装都有可追溯的决定。"""
        meta = data.meta
        outcome_parts = [f"风格：{meta.get('style', '')}"]
        for key, label in (
            ("hair_style", "发型名称"), ("hair", "发型细节"),
            ("makeup_style", "妆容名称"), ("makeup", "妆容细节"),
            ("nails_style", "美甲名称"), ("nails", "美甲细节"),
        ):
            if meta.get(key):
                outcome_parts.append(f"{label}：{meta[key]}")
        outcome_parts.extend((
            f"场景：{outfit_scene_category_label(meta.get('outfit_scene_category'))}",
            f"风格池：{outfit_style_pool_label(meta.get('outfit_style_pool'))}",
        ))
        await self.context._save_life_decision_record(
            kind="outfit",
            date=date_str,
            subject=f"{date_str}:{target_period}",
            decision=f"{decision or 'keep'}｜{data.outfit[:160]}",
            reason=self.context._localize_outfit_reason(result.get("reason")),
            evidence=(
                f"当前：{context['current_timeline']}；下一项：{context['next_timeline']}；"
                f"天气：{context['weather']}；本轮要求：{context['instruction'] or '无'}"
            ),
            outcome="；".join(outcome_parts),
        )



    async def _persist_outfit_commit(
        self, day, commit: OutfitCommit, *,
        current_time: datetime.datetime, target_period: str,
    ) -> list[int]:
        """集中保存外观、历史和衣橱引用，沿用归档的版本与可穿检查。"""
        pending = copy.deepcopy(day)
        if commit.outfit_changed:
            pending.outfit_history[target_period] = commit.outfit
        for key, value in commit.meta.items():
            text = str(value or "").strip()
            if text:
                pending.meta[key] = text
            else:
                pending.meta.pop(key, None)
        pending.outfit = commit.outfit
        pending.time_period = target_period
        reference_ids = list(commit.reference_ids)
        lookup_ids = list(dict.fromkeys(commit.previous_reference_ids + reference_ids))
        items = await self.context.archive.get_style_catalog_items(
            ids=lookup_ids, limit=len(lookup_ids)
        ) if lookup_ids else []
        reference_ids = merge_outfit_catalog_references(
            commit.previous_reference_ids, reference_ids,
            {item.id: item.kind for item in items},
            normalize_outfit_components(pending.meta.get("outfit_components")),
            partial=commit.partial_catalog,
        )
        if reference_ids:
            pending.meta["style_catalog_reference_ids"] = ",".join(map(str, reference_ids))
        else:
            pending.meta.pop("style_catalog_reference_ids", None)
        if commit.appearance_changed:
            record_appearance_snapshot(pending, current_time)
        if commit.require_wearable:
            await self.context.archive.save_day(pending, require_wearable=True)
        else:
            await self.context.archive.save_day(pending)
        day.apply_persisted(pending)
        wear_recorder = getattr(self.context.archive, "record_wardrobe_wear", None)
        if commit.appearance_changed and callable(wear_recorder):
            await wear_recorder(
                day,
                event_id=("outfit:" + str(day.meta.get("outfit_fact_confirmed_at"))
                          + ":" + str(day.meta.get("style_catalog_reference_ids"))),
            )
        return reference_ids

    async def _apply_outfit_update_result(
        self,
        result: dict,
        *,
        date_str: str,
        target_period: str,
        current_time: datetime.datetime,
        context: dict,
        provider=None,
        provider_id: str = "",
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
        verified_change_source = self.context._verified_outfit_change_source(result, context)
        generated = normalize_outfit_appearance(result)
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
        scene_category = scene_category_for_place_kind(
            context.get("current_place_kind"), default=scene_category
        )
        stored = normalize_outfit_appearance(
            {**old_meta, "outfit": old_data.outfit}
        )
        current_reference_ids = self.context._style_catalog_reference_ids(
            old_meta.get("style_catalog_reference_ids")
        )
        review = review_outfit_decision(
            decision,
            current_basis=current_basis,
            scene_category=scene_category,
            style_pool=result.get("style_pool"),
            component_review=component_review,
            stored=stored,
            generated=generated,
            has_instruction=bool(context.get("instruction")),
        )
        decision = review.decision
        if review.occurred_change:
            basis_label = {
                "occurred_schedule": "已发生日程",
                "live_state": "实时状态",
            }.get(current_basis, "当前记录")
            logger.debug(
                "[穿搭更新] 已按发生后的生活状态校正穿搭决定："
                f"依据={basis_label}；决定={outfit_decision_label(decision)}"
            )
        main_clothing_review = review.main_clothing_review
        clothing_component_adjusted = review.clothing_component_adjusted
        if review.partial_change:
            logger.debug("[穿搭更新] 已按组成部分审视结果校正穿搭决定：决定=局部调整")
        component_only_partial_change = (
            decision == "partial_change" and main_clothing_review != "adjust"
        )
        reference_ids = (
            self.context._style_catalog_reference_ids(result.get("catalog_reference_ids"))
            if decision != "keep"
            else current_reference_ids
        )
        requires_catalog_clothing = bool(
            decision in {"change", "sleepwear", "outdoor"}
            or (
                decision == "partial_change"
                and (
                    clothing_component_adjusted
                    or (generated["outfit"] and generated["outfit"] != stored["outfit"])
                )
            )
        )
        selection = await self._resolve_outfit_catalog_appearance(
            reference_ids,
            scene_category=scene_category,
            component_only=component_only_partial_change,
            requires_catalog_clothing=requires_catalog_clothing,
            context=context,
        )
        if selection is None:
            return None
        reference_ids, catalog_appearance = selection
        full_change = decision in {"change", "sleepwear", "outdoor"}
        projection = prepare_outfit_components(
            old_meta.get("outfit_components"),
            result.get("outfit_components") if decision != "keep" else {},
            catalog_appearance.get("outfit_components"),
            full_change=full_change,
            component_only=component_only_partial_change,
            scene_category=scene_category,
            catalog_selected=bool(reference_ids and catalog_appearance.get("outfit")),
            generated_outfit=catalog_appearance.get("outfit") or generated["outfit"],
        )
        old_components = projection.stored
        effective_components = projection.effective
        components_changed = projection.changed
        if projection.visible_outfit:
            generated["outfit"] = projection.visible_outfit
        if decision == "keep" and components_changed:
            decision = "partial_change"
            component_review = dict(component_review)
            for key, value in effective_components.items():
                if old_components.get(key) != value:
                    component_review.setdefault(key, "adjust")
        if reference_ids:
            catalog_outfit = catalog_appearance.get("outfit", "")
            if catalog_outfit and not projection.visible_outfit:
                generated["outfit"] = catalog_outfit
            for key in (
                "hair_style", "hair", "makeup_style", "makeup", "nails_style", "nails"
            ):
                generated[key] = generated[key] or catalog_appearance.get(key, "")
            logger.debug(
                "[穿搭更新] 已采用视觉衣橱候选："
                + ",".join(str(item) for item in reference_ids)
            )
        final = select_outfit_appearance(decision, stored, generated)
        if projection.visible_outfit:
            final["outfit"] = projection.visible_outfit
        expanded_group_appearance = bool(
            str(context.get("source_instruction") or "").strip()
            != str(context.get("instruction") or "").strip()
        )
        catalog_has_persona_appearance = any(
            str(catalog_appearance.get(key) or "").strip()
            for key in (
                "hair_style",
                "hair",
                "makeup_style",
                "makeup",
                "nails_style",
                "nails",
            )
        )
        if expanded_group_appearance or catalog_has_persona_appearance:
            audited = await self.context._audit_persona_appearance(
                {
                    "current": {
                        "hair_style": final["hair_style"],
                        "hair": final["hair"],
                        "makeup_style": final["makeup_style"],
                        "makeup": final["makeup"],
                        "nails_style": final["nails_style"],
                        "nails": final["nails"],
                    }
                },
                persona=context.get("persona", ""),
                original_instruction=context.get("source_instruction", ""),
                provider=provider,
                provider_id=provider_id,
                subject="即时换装与衣橱候选合并",
            )
            corrected = audited.get("current", {})
            for key in (
                "hair_style", "hair", "makeup_style", "makeup", "nails_style", "nails"
            ):
                final[key] = corrected.get(key, final[key])
        final["outfit"] = strip_hair_from_outfit(
            final["outfit"],
            final["hair_style"],
            final["hair"],
        )
        if not final["outfit"]:
            return None
        appearance_changed = any(
            final[key] != stored[key] for key in ("outfit", "hair", "makeup", "nails")
        ) or components_changed
        comparable_old_outfit = strip_hair_from_outfit(
            stored["outfit"],
            stored["hair_style"],
            stored["hair"],
        )
        comparable_old_outfit = strip_hair_from_outfit(
            comparable_old_outfit,
            final["hair_style"],
            final["hair"],
        )
        outfit_changed = bool(final["outfit"] and final["outfit"] != comparable_old_outfit)
        user_confirmed = (
            str(old_meta.get("outfit_fact_source") or "").strip() == "user_instruction"
        )
        instruction_source = str(context.get("instruction_source") or "").strip()
        user_instruction = _has_user_outfit_instruction(context)
        evidence = result.get("change_evidence")
        action_intent = (
            decision != "keep"
            and appearance_changed
            and (
                (
                    current_basis == "action_intent"
                    and isinstance(evidence, dict)
                    and evidence.get("kind") == "action_intent"
                    and evidence.get("source") == "autonomous"
                )
                or (
                    not user_instruction
                    and not verified_change_source
                    and reference_ids
                    and catalog_appearance
                )
            )
        )
        # 本轮选择已通过当前衣橱与场景校验，交给执行器执行。
        # 模型的完成叙述和来源标签都不能代替当前执行，也不能回填历史完成时间。
        claims_completed = (
            isinstance(evidence, dict)
            and evidence.get("kind") == "explicit_outfit_change"
        )
        if (
            appearance_changed
            and not user_instruction
            and claims_completed
            and not verified_change_source
            and not action_intent
        ):
            context["outfit_decision_issue"] = "声称完成的换装没有执行证据"
            logger.debug("[穿搭更新] 需要重新决策：声称完成的换装没有执行证据")
            return None
        if (
            user_confirmed
            and not user_instruction
            and decision != "keep"
            and appearance_changed
            and not verified_change_source
            and not action_intent
        ):
            logger.debug("[穿搭更新] 已保留用户确认的当前穿搭：本轮没有已发生换装证据")
            return old_data
        style_pool = resolve_outfit_style_pool(
            scene_category,
            decision=decision,
            requested=result.get("style_pool"),
            current=old_meta.get("outfit_style_pool"),
        )
        timing_issue = future_outfit_timing_issue(
            final["outfit"],
            old_data.timeline,
            current_time=current_time,
            timeline_date=context["timeline_date"],
            source_timeline_time=(
                str(result["change_evidence"].get("timeline_time") or "").strip()
                if not user_instruction
                and isinstance(result.get("change_evidence"), dict)
                and result["change_evidence"].get("source") == "occurred_schedule"
                else ""
            ),
        )
        if timing_issue:
            logger.warning(f"[穿搭更新] 已忽略提前换装结果：{timing_issue}")
            return None
        style_issue = outfit_style_contamination_reason(
            final["style"],
            theme=old_meta.get("theme"),
            mood=old_meta.get("mood"),
            schedule_type=old_meta.get("schedule_type"),
        )
        if style_issue:
            logger.warning(f"[穿搭更新] 已忽略穿搭风格异常结果：{style_issue}")
            return None
        if should_abort and should_abort():
            return None
        if action_intent:
            action_id = "outfit_update:" + uuid.uuid4().hex
            action = LifeActionIntent(
                action_id=action_id,
                action_type="change_outfit",
                target=final["outfit"],
                requested_at=current_time.strftime("%Y-%m-%d %H:%M:%S"),
                payload={
                    **final,
                    "outfit_components": serialize_outfit_components(effective_components),
                    "scene_category": scene_category,
                    "outfit_decision": decision,
                    "style_pool": style_pool,
                    "time_period": target_period,
                    "outing_outfit_reserve": str(catalog_appearance.get("outing_reserve") or "").strip(),
                    "catalog_reference_ids": reference_ids,
                },
                evidence=self.context._localize_outfit_reason(result.get("reason")),
                source="outfit_update",
            )
            outcome = await self.context.record_life_action_receipt(
                old_data, action_id,
                {"status": "simulated", "source": "outfit_update",
                 "evidence": [action.evidence], "occurred_at": action.requested_at},
                now=current_time, planned_action=action,
            )
            if outcome is None or outcome.status != "committed":
                context["outfit_decision_issue"] = (
                    getattr(outcome, "reason", "") or "换装未通过生活执行器校验"
                )
                return None
            updated = await self.context.archive.get_day(date_str)
            if updated is not None:
                await self._record_outfit_update_decision(
                    updated, date_str=date_str, target_period=target_period,
                    decision=decision, result=result, context=context,
                )
            return updated
        meta_updates = {
            "outfit_decision": decision,
            "outfit_scene_category": scene_category,
            "outfit_style_pool": style_pool,
            "outfit_components": serialize_outfit_components(effective_components),
            **{key: value for key, value in final.items() if key != "outfit"},
            "outfit_reason": self.context._localize_outfit_reason(result.get("reason")),
        }
        if reference_ids:
            meta_updates["outing_outfit_reserve"] = str(
                catalog_appearance.get("outing_reserve") or ""
            ).strip()
        if user_instruction:
            meta_updates["outfit_fact_source"] = "user_instruction"
            meta_updates["outfit_fact_confirmed_at"] = current_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            meta_updates["outfit_fact_evidence"] = "用户本轮明确穿搭要求"
        elif decision != "keep" and appearance_changed:
            meta_updates["outfit_fact_source"] = (
                verified_change_source
                or (instruction_source if instruction_source in {"commitment"} else "")
                or "autonomous"
            )
            meta_updates["outfit_fact_confirmed_at"] = current_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            evidence = (
                result.get("change_evidence")
                if isinstance(result.get("change_evidence"), dict)
                else {}
            )
            evidence_text = self.context._compact_outfit_evidence(
                evidence.get("quote") or result.get("reason"), 160
            )
            if evidence_text:
                meta_updates["outfit_fact_evidence"] = evidence_text
            else:
                meta_updates["outfit_fact_evidence"] = ""
        commit = OutfitCommit(
            outfit=final["outfit"], meta=meta_updates,
            outfit_changed=outfit_changed, appearance_changed=appearance_changed,
            reference_ids=reference_ids, previous_reference_ids=current_reference_ids,
            partial_catalog=not full_change and decision != "keep",
            require_wearable=requires_catalog_clothing and not user_instruction,
        )
        try:
            reference_ids = await self._persist_outfit_commit(
                old_data, commit, current_time=current_time, target_period=target_period
            )
        except ValueError as exc:
            if not commit.require_wearable:
                raise
            context["catalog_selection_issue"] = str(exc)
            return None
        if appearance_changed and reference_ids:
            await self.context._mark_style_catalog_references(reference_ids)
        await self._record_outfit_update_decision(
            old_data, date_str=date_str, target_period=target_period,
            decision=decision, result=result, context=context,
        )
        return old_data

    async def update_outfit(
        self,
        date_str,
        target_period,
        current_time: datetime.datetime | None = None,
        instruction: str = "",
        should_abort: Callable[[], bool] | None = None,
        source_instruction: str | None = None,
        instruction_source: str | None = None,
    ):
        current_time = current_time or life_now()
        instruction = str(instruction or "").strip()
        started_at = time.monotonic()
        if should_abort and should_abort():
            return None
        old_data = await self.context.archive.get_day(date_str)
        if not old_data:
            logger.debug(f"[穿搭更新] {date_str} 尚无生活安排，先生成基础日程")
            try:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                target_date = life_now()
            target_hour = PERIOD_HOURS.get(target_period)
            if should_abort and should_abort():
                return None
            generation_instruction = instruction
            if source_instruction and source_instruction != instruction:
                generation_instruction = (
                    f"用户原始穿搭请求：{source_instruction}\n"
                    f"同图整组造型展开：{instruction}"
                )
            return await self.context.generate_daily(
                target_date,
                force=True,
                target_hour=target_hour,
                extra=generation_instruction,
            )

        logger.debug(
            f"[穿搭更新] 开始：日期={date_str}；"
            f"时段={get_time_period_cn(target_period)}"
        )
        async with self.context._gen_lock:
            if should_abort and should_abort():
                return None
            old_data = await self.context.archive.get_day(date_str)
            if not old_data:
                return None

            context = await self._outfit_update_context(
                old_data=old_data,
                date_str=date_str,
                target_period=target_period,
                current_time=current_time,
                instruction=instruction,
                source_instruction=source_instruction,
                instruction_source=instruction_source,
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
                provider_id = self.context._task_provider_id(self.context.config.outfit.provider)
                provider = await self.context._get_provider(provider_id)
                if not provider:
                    return None
                for attempt in range(2):
                    if should_abort and should_abort():
                        return None
                    completion_text = await self.context._call_llm_text(
                        provider,
                        prompt,
                        update_session_id,
                        primary_provider_id=provider_id,
                    )
                    if should_abort and should_abort():
                        return None
                    if not completion_text:
                        return None
                    result = extract_json_from_text(completion_text)
                    if not result:
                        return None
                    context.pop("catalog_selection_issue", None)
                    context.pop("outfit_decision_issue", None)
                    updated = await self._apply_outfit_update_result(
                        result,
                        date_str=date_str,
                        target_period=target_period,
                        current_time=current_time,
                        context=context,
                        provider=provider,
                        provider_id=provider_id,
                        should_abort=should_abort,
                    )
                    if updated is not None:
                        logger.debug(
                            f"[穿搭更新] 完成：日期={date_str}；日程保持不变；"
                            f"耗时={time.monotonic() - started_at:.2f} 秒"
                        )
                    decision_issue = context.get("outfit_decision_issue")
                    if updated is None and decision_issue:
                        if attempt == 0:
                            prompt += (
                                "\n" + json.dumps(
                                    {"validation_result": {"status": "rejected", "reason": decision_issue}},
                                    ensure_ascii=False,
                                )
                            )
                            continue
                        logger.warning(f"[穿搭更新] 换装未执行，保持当前穿着：{decision_issue}")
                        return None
                    issue = context.get("catalog_selection_issue")
                    if updated is None and issue and not _has_user_outfit_instruction(context):
                        callback = getattr(self.context, "wardrobe_shortage_callback", None)
                        scene = context.get("catalog_selection_scene") or context.get(
                            "current_scene_category", ""
                        )
                        if callable(callback) and not await self.context._style_catalog_has_eligible_clothing(
                            scene_category=scene,
                            conditions=context.get("wardrobe_conditions"),
                        ):
                            if should_abort and should_abort():
                                return None
                            if await callback():
                                logger.info(
                                    "[穿搭更新] 当前缺少可用整套搭配，已交给衣橱服务评估补充；"
                                    "入库后重新选装，当前穿着保持到实际换装"
                                )
                                # 记下本轮已处理，衣橱入库后的 selection_key 会触发新一轮选装。
                                return await self.context.archive.get_day(date_str)
                        logger.warning(f"[穿搭更新] 已忽略脱离衣橱的新穿搭：{issue}")
                    if updated is None and issue and attempt == 0:
                        prompt += (
                            f"\n上一轮衣橱选择未通过校验：{issue}。"
                            "请按本次换装用途，从上方详细候选或服装简表中重新选择"
                            "实际采用的完整服装编号，重新返回完整 JSON。"
                        )
                        continue
                    return updated
            except DayRevisionConflict as exc:
                logger.debug(f"[穿搭更新] 模型结果已过期，保留较新的穿搭：{exc}")
            except Exception as e:
                logger.error(f"[穿搭更新] 更新失败：{e}")
            finally:
                await self.context._cleanup_conversation(update_session_id)
            return None
