import json
import uuid

from astrbot.api import logger

from .tools import (
    extract_json_from_text,
    get_weather_activity_constraint,
    get_weather_outfit_constraint,
)


_CONDITION_FLAGS = ("is_rainy", "is_sunny", "is_cloudy", "is_foggy", "is_severe")


class DailyClimateMixin:
    async def _classify_weather_condition(self, weather_data: object) -> object:
        if not isinstance(weather_data, dict) or weather_data.get("ok") is not True:
            return weather_data
        data = weather_data.get("data")
        weather = data.get("weather") if isinstance(data, dict) else None
        if not isinstance(weather, dict) or all(
            isinstance(weather.get(field), bool) for field in _CONDITION_FLAGS
        ):
            return weather_data
        condition = str(weather.get("condition") or "").strip()
        if not condition:
            return weather_data
        cache = self.__dict__.setdefault("_weather_condition_cache", {})
        flags = cache.get(condition)
        if flags is None:
            provider_id = self._task_provider_id()
            provider = await self._get_provider(provider_id)
            if provider is None:
                return weather_data
            session_id = f"daily_life_weather_{uuid.uuid4().hex[:8]}"
            try:
                answer = await self._call_llm_text(
                    provider,
                    "根据天气现象判断语义，只返回 JSON 对象，字段 "
                    "is_rainy、is_sunny、is_cloudy、is_foggy、is_severe 均须为布尔值。"
                    "is_severe 只表示对出行安全有显著影响的恶劣天气。"
                    "不确定时一律返回 false。待判断的天气现象："
                    + json.dumps(condition, ensure_ascii=False),
                    session_id,
                    empty_retries=0,
                    primary_provider_id=provider_id,
                    timeout_seconds=20,
                )
                parsed = extract_json_from_text(answer)
                if not isinstance(parsed, dict) or not all(
                    type(parsed.get(field)) is bool for field in _CONDITION_FLAGS
                ):
                    return weather_data
                flags = {field: parsed[field] for field in _CONDITION_FLAGS}
                if len(cache) >= 32:
                    cache.pop(next(iter(cache)))
                cache[condition] = flags
            except Exception as exc:
                logger.warning(f"[天气] 天气现象分类失败：{exc}")
                return weather_data
            finally:
                await self._cleanup_conversation(session_id)
        flags = {
            field: weather[field] if isinstance(weather.get(field), bool) else flags[field]
            for field in _CONDITION_FLAGS
        }
        return {
            **weather_data,
            "data": {**data, "weather": {**weather, **flags}},
        }

    @staticmethod
    def _has_weather_safety_risk(weather_info: dict) -> bool:
        temp = weather_info.get("temp")
        if isinstance(temp, (int, float)) and (temp >= 35 or temp <= 5):
            return True
        return bool(weather_info.get("is_foggy") or weather_info.get("is_severe"))

    def _build_weather_sections(self, weather_info: dict) -> tuple[str, str]:
        weather_section = f"\n天气：{weather_info['raw']}"
        if weather_info["temp"] is not None:
            weather_section += f"\n温度感受：{weather_info.get('temp_desc', '')}（{weather_info['temp']}°C）"
        if self.config.weather.aware_outfit and weather_info["outfit_hint"]:
            weather_section += f"\n穿衣参考：{weather_info['outfit_hint']}"
        if self.config.weather.aware_activity and weather_info["activity_hint"]:
            weather_section += f"\n活动参考：{weather_info['activity_hint']}"

        weather_constraint = get_weather_outfit_constraint(
            weather_info, self.config.weather.aware_outfit
        )
        activity_constraint = get_weather_activity_constraint(
            weather_info, self.config.weather.aware_activity
        )
        constraint_section = ""
        if weather_constraint or activity_constraint:
            if self._has_weather_safety_risk(weather_info):
                constraint_section = "\n\n【天气安全约束 - 必须遵守】"
                if weather_constraint:
                    constraint_section += f"\n穿搭安全：{weather_constraint}"
                if activity_constraint:
                    constraint_section += f"\n活动安全：{activity_constraint}"
            else:
                constraint_section = "\n\n【天气软参考】\n以下内容只作为生活决策参考，不覆盖 life_decision 或用户指令。"
                if weather_constraint:
                    constraint_section += f"\n穿搭参考：{weather_constraint}"
                if activity_constraint:
                    constraint_section += f"\n活动参考：{activity_constraint}"
        return weather_section, constraint_section


__all__ = ["DailyClimateMixin"]
