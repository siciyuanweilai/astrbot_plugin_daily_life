import asyncio
from typing import Any

import aiohttp

from astrbot.api import logger

from ..config.options import WeatherSettings


class WeatherClient:
    def __init__(self, config: WeatherSettings):
        self.config = config
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_weather(
        self, city: str, max_retries: int = 3, retry_delay: float = 2
    ) -> dict[str, Any]:
        url = "https://api.nycnm.cn/api/v2/weather"
        params = {"query": city}
        if self.config.api_key:
            params["apikey"] = self.config.api_key
        last_error = "天气查询失败"
        for attempt in range(max_retries):
            try:
                session = await self._get_session()
                async with session.get(url, params=params) as r:
                    if r.status != 200:
                        last_error = f"网络请求状态码 {r.status}"
                        logger.warning(
                            f"[天气] 网络请求状态码 {r.status}（第 {attempt + 1}/{max_retries} 次）"
                        )
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                        continue
                    try:
                        data = await r.json()
                        if (
                            isinstance(data, dict)
                            and data.get("code") == 200
                            and isinstance(data.get("data"), dict)
                        ):
                            logger.debug(f"[天气] 成功获取 {city} 天气")
                            return {**data, "ok": True, "error": ""}
                        else:
                            message = (
                                str(data.get("message") or "接口返回未知错误")
                                if isinstance(data, dict)
                                else "接口返回非结构化数据"
                            )
                            last_error = message
                            logger.warning(f"[天气] 接口返回错误：{message}")
                    except Exception as e:
                        last_error = f"结构化数据解析失败：{type(e).__name__}"
                        logger.warning(f"[天气] 结构化数据解析失败：{e}")
            except Exception as e:
                last_error = f"请求失败：{type(e).__name__}"
                logger.error(f"[天气] 请求失败：{e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
        return {
            "ok": False,
            "code": 0,
            "message": "天气查询失败",
            "error": last_error,
            "data": {},
        }
