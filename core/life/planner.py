import asyncio
import inspect
from typing import Any

from astrbot.api import logger
from astrbot.core.star.context import Context

from ..archive import LifeArchive
from ..config.options import LifeSettings
from ..sources import SavedHistoryReader, WebInspirationSearch
from .daily import DailyMixin
from .autonomy import LifeAutonomyMixin
from .invite import InviteMixin
from .rhythm import LifecycleMixin
from .outfit import OutfitMixin
from ..prompts import CORE_INTERNAL_SYSTEM_PROMPT, CORE_REASONING_PERSPECTIVE_SECTION
from .reference import ReferenceMixin
from .tools import extract_json_from_text, get_time_period
from .weather import WeatherClient
from .weekly import WeekMixin


class LifeBackgroundComposer(
    ReferenceMixin,
    LifeAutonomyMixin,
    WeekMixin,
    DailyMixin,
    LifecycleMixin,
    InviteMixin,
    OutfitMixin,
):
    def __init__(
        self,
        context: Context,
        config: LifeSettings,
        archive: LifeArchive,
        weather_client: WeatherClient,
        contact_resolver=None,
    ):
        self.context = context
        self.config = config
        self.archive = archive
        self.weather_client = weather_client
        self.contact_resolver = contact_resolver
        self.saved_history = SavedHistoryReader(context)
        self.web_inspiration = WebInspirationSearch(context, config.web_inspiration)
        self._reference_name_cache = {}
        self._gen_lock = asyncio.Lock()

    def _get_curr_period(self, target_dt=None) -> str:
        return get_time_period(target_dt)

    async def _provider_by_id(self, provider_id: str):
        provider_id = str(provider_id or "").strip()
        if not provider_id:
            return None
        getter = getattr(self.context, "get_provider_by_id", None)
        if not callable(getter):
            return None
        provider = getter(provider_id)
        if inspect.isawaitable(provider):
            provider = await provider
        return provider

    @staticmethod
    def _provider_meta_id(provider: Any) -> str:
        meta_getter = getattr(provider, "meta", None)
        if not callable(meta_getter):
            return ""
        try:
            meta = meta_getter()
        except Exception:
            return ""
        return str(getattr(meta, "id", "") or "").strip()

    def _system_default_provider_id(self) -> str:
        config_getter = getattr(self.context, "get_config", None)
        if not callable(config_getter):
            return ""
        try:
            config = config_getter()
        except Exception as exc:
            logger.debug(f"[日常生活] 读取默认大语言模型服务提供商失败：{exc}")
            return ""
        if not isinstance(config, dict):
            return ""
        provider_id = str(config.get("provider_settings", {}).get("default_provider_id", "") or "").strip()
        if provider_id:
            return provider_id
        for item in config.get("provider", []) or []:
            if not isinstance(item, dict) or not item.get("enable", False):
                continue
            provider_type = str(item.get("provider_type", "chat") or "chat")
            if "chat" in provider_type:
                return str(item.get("id") or "").strip()
        return ""

    async def _system_default_provider(self, primary_provider_id: str = ""):
        default_id = self._system_default_provider_id()
        if default_id and default_id != primary_provider_id:
            try:
                provider = await self._provider_by_id(default_id)
                if provider:
                    return provider, default_id
            except Exception as exc:
                logger.debug(f"[日常生活] 获取默认大语言模型服务提供商失败（{default_id}）：{exc}")

        provider = self.context.get_using_provider()
        provider_id = self._provider_meta_id(provider) or "__default__"
        if provider and provider_id != primary_provider_id:
            return provider, provider_id
        return None, ""

    async def _temporary_provider_for_call(self, primary_provider_id: str, reason: str = ""):
        provider, temporary_id = await self._system_default_provider(primary_provider_id)
        if not provider:
            return None, ""
        suffix = f"：{reason}" if reason else ""
        logger.info(f"[日常生活] 指定大语言模型本次调用不可用，临时使用当前默认模型（{temporary_id}）{suffix}")
        return provider, temporary_id

    def _generation_provider_id(self) -> str:
        return str(self.config.llm_provider or "").strip()

    def _task_provider_id(self, provider_id: str = "") -> str:
        return str(provider_id or "").strip()

    async def _get_provider(self, provider_id: str = ""):
        provider = None
        provider_id = str(provider_id or "").strip()
        if provider_id:
            try:
                provider = await self._provider_by_id(provider_id)
            except Exception as e:
                logger.warning(f"[日常生活] 获取指定大语言模型供应商失败（{provider_id}）：{e}")
        return provider or self.context.get_using_provider()

    @staticmethod
    def _extract_completion_text(resp: object) -> str:
        if resp is None:
            return ""
        for key in ("completion_text", "completion", "text", "content"):
            value = getattr(resp, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if isinstance(resp, dict):
            for key in ("completion_text", "completion", "text", "content"):
                value = resp.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = getattr(resp, key, None)
            recovered = LifeBackgroundComposer._structured_text_from_reasoning(value)
            if recovered:
                return recovered
        if isinstance(resp, dict):
            for key in ("reasoning_content", "reasoning", "thinking"):
                recovered = LifeBackgroundComposer._structured_text_from_reasoning(resp.get(key))
                if recovered:
                    return recovered
        return ""

    @staticmethod
    def _structured_text_from_reasoning(value: object) -> str:
        text = str(value or "").strip() if isinstance(value, str) else ""
        if not text or not isinstance(extract_json_from_text(text), dict):
            return ""
        start = text.find("{")
        return text[start:].strip() if start >= 0 else ""

    @staticmethod
    def _with_reasoning_perspective_rule(prompt: str) -> str:
        prompt_text = str(prompt or "")
        if prompt_text.startswith(CORE_REASONING_PERSPECTIVE_SECTION):
            return prompt_text
        return f"{CORE_REASONING_PERSPECTIVE_SECTION}\n\n{prompt_text.lstrip()}"

    async def _call_llm_text(
        self,
        provider,
        prompt: str,
        session_id: str,
        empty_retries: int = 1,
        primary_provider_id: str = "",
    ) -> str:
        primary_provider_id = str(primary_provider_id or "").strip()
        current_provider = provider
        provider_meta_id = self._provider_meta_id(provider)
        current_provider_id = provider_meta_id or primary_provider_id
        prompt = self._with_reasoning_perspective_rule(prompt)

        async def switch_to_temporary_provider(reason: str) -> bool:
            nonlocal current_provider, current_provider_id
            if not primary_provider_id or current_provider_id != primary_provider_id:
                return False
            temporary_provider, temporary_id = await self._temporary_provider_for_call(primary_provider_id, reason)
            if not temporary_provider:
                return False
            current_provider = temporary_provider
            current_provider_id = temporary_id
            return True

        attempt = 0
        while attempt <= empty_retries:
            if attempt > 0 and attempt == empty_retries:
                await switch_to_temporary_provider("达到空响应重试上限")
            try:
                resp = await current_provider.text_chat(
                    prompt,
                    session_id=session_id,
                    system_prompt=CORE_INTERNAL_SYSTEM_PROMPT,
                )
            except Exception as exc:
                err_text = str(exc)
                if "401" in err_text and await switch_to_temporary_provider("401"):
                    continue
                if attempt < empty_retries:
                    logger.warning(f"[日常生活] 大语言模型调用异常（第 {attempt + 1} 次）：{exc}")
                    attempt += 1
                    continue
                if await switch_to_temporary_provider("调用异常"):
                    continue
                logger.warning(f"[日常生活] 大语言模型调用异常（第 {attempt + 1} 次）：{exc}")
                return ""

            text = self._extract_completion_text(resp)
            if text:
                return text
            if attempt < empty_retries:
                logger.warning("[日常生活] 大语言模型返回为空，准备重试一次")
                attempt += 1
                continue
            if await switch_to_temporary_provider("返回空响应"):
                continue
            return ""
        return ""
