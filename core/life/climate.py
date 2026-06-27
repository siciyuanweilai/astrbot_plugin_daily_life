from .tools import get_weather_activity_constraint, get_weather_outfit_constraint


class DailyClimateMixin:
    @staticmethod
    def _has_weather_safety_risk(weather_info: dict) -> bool:
        temp = weather_info.get("temp")
        if isinstance(temp, (int, float)) and (temp >= 35 or temp <= 5):
            return True
        condition = str(weather_info.get("condition") or "")
        severe_weather = ("暴雨", "大雨", "雷", "雪", "冰雹")
        return bool(weather_info.get("is_foggy")) or any(token in condition for token in severe_weather)


    def _build_weather_sections(self, weather_info: dict) -> tuple[str, str]:
        weather_section = f"\n天气：{weather_info['raw']}"
        if weather_info["temp"] is not None:
            weather_section += f"\n温度感受：{weather_info.get('temp_desc', '')}（{weather_info['temp']}°C）"
        if self.config.weather.aware_outfit and weather_info["outfit_hint"]:
            weather_section += f"\n穿衣参考：{weather_info['outfit_hint']}"
        if self.config.weather.aware_activity and weather_info["activity_hint"]:
            weather_section += f"\n活动参考：{weather_info['activity_hint']}"

        weather_constraint = get_weather_outfit_constraint(weather_info, self.config.weather.aware_outfit)
        activity_constraint = get_weather_activity_constraint(weather_info, self.config.weather.aware_activity)
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
