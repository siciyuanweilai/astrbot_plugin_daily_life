from __future__ import annotations

from astrbot.api import logger

from ...clock import timestamp as life_timestamp
from ...life.tools import analyze_weather, extract_city_from_persona
from ...models import WeatherInfo


class SpineClimateMixin:
    async def try_update_weather(self, today_str: str) -> None:
        if not self.config.weather.api_key:
            return

        data = await self.archive.get_day(today_str)
        if not data:
            return

        now_ts = life_timestamp()
        weather_info = data.weather_info
        has_rich_info = bool(weather_info.condition or weather_info.temp is not None)
        if has_rich_info and now_ts - data.weather_last_update < 3600:
            return

        city = self.config.weather.default_city
        if not city:
            persona = await self.composer._get_persona()
            city = extract_city_from_persona(persona)
        if not city:
            return

        try:
            logger.debug(f"[天气更新] 正在自动刷新 {city} 天气……")
            weather_data = await self.weather_client.get_weather(city)
            if not isinstance(weather_data, dict) and "失败" in str(weather_data):
                return

            analyzed = analyze_weather(weather_data)
            if analyzed.get("temp") is None:
                return

            data.weather = analyzed["raw"]
            data.weather_info = WeatherInfo.from_value(analyzed)
            data.weather_last_update = now_ts
            await self.archive.save_day(data)
            await self.mark_page_status_changed("weather")
            logger.debug(f"[天气更新] 天气数据已更新：{analyzed['raw']}")
        except Exception as exc:
            logger.warning(f"[天气更新] 更新出错：{exc}")
