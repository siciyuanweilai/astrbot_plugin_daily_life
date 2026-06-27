import datetime
from typing import Any

import aiohttp
from astrbot.api import logger

from ..config.options import WebInspirationSettings


class WebInspirationSearch:
    """把联网搜索结果整理成生成时可参考的灵感片段。"""

    def __init__(self, context: Any, settings: WebInspirationSettings):
        self.context = context
        self.settings = settings

    async def search(
        self,
        keyword: str,
        prompt_template: str,
        *,
        category: str = "",
        persona: str = "",
        today: str = "",
    ) -> str:
        keyword = self._compact(keyword)
        if not self.settings.enabled or not keyword:
            return ""

        provider, keys = self._web_search_config()
        if not keys:
            logger.debug("[日常生活] 联网灵感已开启，但框架网页搜索密钥未配置")
            return ""

        query = self._format_query(
            prompt_template,
            keyword=keyword,
            category=category,
            persona=persona,
            today=today,
        )
        if not query:
            return ""

        for index, key in enumerate(keys, start=1):
            try:
                summary = await self._request(provider, key, query)
            except Exception as exc:
                logger.warning(f"[日常生活] 联网灵感搜索请求失败（第 {index} 个密钥）：{exc}")
                continue
            if summary:
                logger.info(f"[日常生活] 已获取联网灵感参考：{query}")
                return (
                    "## 🌐 联网灵感参考（只作灵感，不是硬性规则）\n"
                    f"- 查询：{query}\n"
                    f"- 摘要：{summary}"
                )

        logger.warning("[日常生活] 联网灵感搜索未获取到可用结果")
        return ""

    def _web_search_config(self) -> tuple[str, list[str]]:
        provider = "tavily"
        keys: list[str] = []
        try:
            getter = getattr(self.context, "get_config", None)
            config = getter() if callable(getter) else getattr(self.context, "_config", None)
            provider_settings = config.get("provider_settings", {}) if hasattr(config, "get") else {}
            provider = str(provider_settings.get("websearch_provider") or provider).strip().lower()
            key_name = "websearch_brave_key" if provider == "brave" else "websearch_tavily_key"
            keys = self._key_list(provider_settings.get(key_name))
        except Exception as exc:
            logger.debug(f"[日常生活] 读取框架网页搜索设置失败：{exc}")
        return ("brave" if provider == "brave" else "tavily"), keys

    @staticmethod
    def _key_list(value: object) -> list[str]:
        if isinstance(value, str):
            raw = [value]
        else:
            try:
                raw = list(value or [])
            except TypeError:
                raw = [value]
        result = []
        seen = set()
        for item in raw:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    @classmethod
    def _format_query(
        cls,
        template: str,
        *,
        keyword: str,
        category: str,
        persona: str,
        today: str,
    ) -> str:
        values = {
            "keyword": keyword,
            "category": category,
            "persona": cls._compact(persona)[:180],
            "today": cls._compact(today)[:180],
            "date": datetime.datetime.now().strftime("%Y年%m月%d日"),
        }
        try:
            query = str(template or "{keyword}").format_map(_SafeFormatMap(values))
        except Exception:
            query = f"{template or ''} {keyword}"
        return cls._compact(query)[:220]

    async def _request(self, provider: str, key: str, query: str) -> str:
        timeout = aiohttp.ClientTimeout(total=self.settings.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if provider == "brave":
                return await self._request_brave(session, key, query)
            return await self._request_tavily(session, key, query)

    async def _request_brave(self, session: aiohttp.ClientSession, key: str, query: str) -> str:
        async with session.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": key},
            params={"q": query, "count": str(self.settings.max_results)},
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"联网搜索服务返回状态 {response.status}")
            data = await response.json()
        results = (data.get("web") or {}).get("results") or []
        texts = []
        for item in results[: self.settings.max_results]:
            if not isinstance(item, dict):
                continue
            title = self._compact(item.get("title"))
            desc = self._compact(item.get("description"))
            snippets = item.get("extra_snippets")
            snippet_text = self._compact(" ".join(str(value or "") for value in snippets)) if isinstance(snippets, list) else ""
            line = " · ".join(part for part in (title, desc, snippet_text) if part)
            if line:
                texts.append(line)
        return self._compact("；".join(texts))[:900]

    async def _request_tavily(self, session: aiohttp.ClientSession, key: str, query: str) -> str:
        async with session.post(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json"},
            json={
                "api_key": key,
                "query": query,
                "search_depth": "basic",
                "include_answer": True,
                "max_results": self.settings.max_results,
            },
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"联网搜索服务返回状态 {response.status}")
            data = await response.json()
        answer = self._compact(data.get("answer"))
        if answer:
            return answer[:900]
        results = data.get("results") or []
        texts = []
        for item in results[: self.settings.max_results]:
            if not isinstance(item, dict):
                continue
            title = self._compact(item.get("title"))
            content = self._compact(item.get("content"))
            line = " · ".join(part for part in (title, content) if part)
            if line:
                texts.append(line)
        return self._compact("；".join(texts))[:900]

    @staticmethod
    def _compact(value: object) -> str:
        return " ".join(str(value or "").strip().split())


class _SafeFormatMap(dict):
    def __missing__(self, key: str) -> str:
        return ""
