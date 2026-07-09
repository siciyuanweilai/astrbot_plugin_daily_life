import asyncio

import aiohttp
from astrbot.api import logger

from ..config.options import WeatherSettings


class WeatherClient:
    def __init__(self, config: WeatherSettings):
        self.config = config
        self._session = None
    
    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed: await self._session.close()
    
    async def get_weather(self, city, max_retries=3, retry_delay=2):
        url = "https://api.nycnm.cn/api/v2/weather"
        params = {"query": city}
        if self.config.api_key: params["apikey"] = self.config.api_key
        for attempt in range(max_retries):
            try:
                session = await self._get_session()
                async with session.get(url, params=params) as r:
                    if r.status != 200:
                        logger.warning(f"[天气] 网络请求状态码 {r.status}（第 {attempt + 1}/{max_retries} 次）")
                        if attempt < max_retries - 1: await asyncio.sleep(retry_delay)
                        continue
                    try:
                        data = await r.json()
                        if data.get("code") == 200 and "data" in data: 
                            logger.debug(f"[天气] 成功获取 {city} 天气")
                            return data
                        else:
                            logger.warning(f"[天气] 接口返回错误：{data.get('message')}")
                    except Exception as e:
                        logger.warning(f"[天气] 结构化数据解析失败：{e}")
            except Exception as e:
                logger.error(f"[天气] 请求失败：{e}")
                if attempt < max_retries - 1: await asyncio.sleep(retry_delay)
        return "天气查询失败"
