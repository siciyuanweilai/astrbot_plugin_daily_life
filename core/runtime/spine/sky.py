from __future__ import annotations

from astrbot.api import logger

from ...clock import timestamp as life_timestamp
from ...life.tools import analyze_weather
from ...models import WeatherInfo


_AUTO_WEATHER_REFRESH_SECONDS = 3600
_MANUAL_WEATHER_REFRESH_SECONDS = 30


class SpineClimateMixin:
    async def try_update_weather(self, today_str: str, *, force: bool = False) -> bool:
        if not self.config.weather.api_key:
            return False

        data = await self.archive.get_day(today_str)
        if not data:
            return False

        now_ts = life_timestamp()
        weather_info = data.weather_info
        has_rich_info = bool(weather_info.condition or weather_info.temp is not None)
        refresh_seconds = (
            _MANUAL_WEATHER_REFRESH_SECONDS
            if force
            else _AUTO_WEATHER_REFRESH_SECONDS
        )
        if (
            has_rich_info
            and data.weather_last_update
            and now_ts - data.weather_last_update < refresh_seconds
        ):
            return False

        try:
            city_resolver = getattr(
                getattr(self, "domains", None),
                "resolve_weather_city",
                None,
            )
            city = await city_resolver() if callable(city_resolver) else ""
            if not city:
                return False
            action = "手动" if force else "自动"
            logger.debug(
                f"[天气更新] 正在通过柠柚接口{action}刷新 {city} 天气……"
            )
            weather_data = await self.weather_client.get_weather(city)
            if not isinstance(weather_data, dict) and "失败" in str(weather_data):
                return False

            analyzed = analyze_weather(weather_data)
            if analyzed.get("temp") is None:
                return False

            data.weather = analyzed["raw"]
            data.weather_info = WeatherInfo.from_value(analyzed)
            data.weather_last_update = now_ts
            await self.archive.save_day(data)
            await self.mark_page_status_changed("weather")
            logger.debug(f"[天气更新] 天气数据已更新：{analyzed['raw']}")
            return True
        except Exception as exc:
            logger.warning(f"[天气更新] 更新出错：{exc}")
            return False
