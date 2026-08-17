import asyncio
import inspect
from typing import Any

from astrbot.api import logger
from astrbot.core.star.context import Context

from ..archive import LifeArchive
from ..config.options import LifeSettings
from ..prompts import CORE_INTERNAL_SYSTEM_PROMPT
from ..search import SearchService
from ..sources import SavedHistoryReader
from .appearance import AppearanceAuditMixin
from .autonomy import LifeAutonomyMixin
from .daily import DailyMixin
from .inspiration import StyleCatalogMixin
from .invite import InviteMixin
from .outfit import OutfitMixin
from .people import PersonFactMixin
from .reliability import (
    NonRetryableProviderError,
    ProviderCircuit,
    exception_status,
    is_non_retryable_provider_error,
    is_transient_provider_error,
    retry_delay,
)
from .rhythm import LifecycleMixin
from .settlement import LifeActionMixin
from .sourcebook import ReferenceMixin
from .tools import extract_json_from_text, get_time_period
from .weather import WeatherClient
from .weekly import WeekMixin


class LifeBackgroundComposer(
    AppearanceAuditMixin,
    StyleCatalogMixin,
    ReferenceMixin,
    PersonFactMixin,
    LifeAutonomyMixin,
    WeekMixin,
    DailyMixin,
    LifecycleMixin,
    LifeActionMixin,
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
        search_service: SearchService | None = None,
        domain_service=None,
    ):
        self.context = context
        self.config = config
        self.archive = archive
        self.weather_client = weather_client
        self.contact_resolver = contact_resolver
        self.saved_history = SavedHistoryReader(context)
        self.search = search_service or SearchService(context, config.search)
        self.domains = domain_service
        self._reference_name_cache = {}
        self._gen_lock = asyncio.Lock()
        self._preference_maintenance_done = False
        self._provider_circuit = ProviderCircuit()

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
        provider_id = str(
            config.get("provider_settings", {}).get("default_provider_id", "") or ""
        ).strip()
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
                logger.debug(
                    f"[日常生活] 获取默认大语言模型服务提供商失败（{default_id}）：{exc}"
                )

        provider = self.context.get_using_provider()
        provider_id = self._provider_meta_id(provider) or "__default__"
        if provider and provider_id != primary_provider_id:
            return provider, provider_id
        return None, ""

    async def _temporary_provider_for_call(
        self, primary_provider_id: str, reason: str = ""
    ):
        provider, temporary_id = await self._system_default_provider(
            primary_provider_id
        )
        if not provider:
            return None, ""
        suffix = f"：{reason}" if reason else ""
        logger.info(
            f"[日常生活] 指定大语言模型本次调用不可用，临时使用当前默认模型（{temporary_id}）{suffix}"
        )
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
                logger.warning(
                    f"[日常生活] 获取指定大语言模型供应商失败（{provider_id}）：{e}"
                )
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
                recovered = LifeBackgroundComposer._structured_text_from_reasoning(
                    resp.get(key)
                )
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

    async def _call_llm_text(
        self,
        provider,
        prompt: str,
        session_id: str,
        empty_retries: int = 1,
        primary_provider_id: str = "",
        propagate_non_retryable: bool = False,
        timeout_seconds: float | None = None,
    ) -> str:
        primary_provider_id = str(primary_provider_id or "").strip()
        current_provider = provider
        provider_meta_id = self._provider_meta_id(provider)
        current_provider_id = provider_meta_id or primary_provider_id
        request_timeout = max(
            0.01,
            float(
                timeout_seconds
                if timeout_seconds is not None
                else getattr(self.config, "llm_timeout_seconds", 120)
            ),
        )

        async def switch_to_temporary_provider(reason: str) -> bool:
            nonlocal current_provider, current_provider_id
            if not primary_provider_id or current_provider_id != primary_provider_id:
                return False
            temporary_provider, temporary_id = await self._temporary_provider_for_call(
                primary_provider_id, reason
            )
            if not temporary_provider:
                return False
            current_provider = temporary_provider
            current_provider_id = temporary_id
            return True

        if self._provider_circuit.is_open(current_provider_id):
            if not await switch_to_temporary_provider("连续失败熔断"):
                logger.warning(
                    f"[日常生活] 大语言模型服务暂时熔断：提供商={current_provider_id or '默认'}"
                )
                return ""

        attempt = 0
        while attempt <= empty_retries:
            if attempt > 0 and attempt == empty_retries:
                await switch_to_temporary_provider("达到空响应重试上限")
            try:
                resp = await asyncio.wait_for(
                    current_provider.text_chat(
                        prompt,
                        session_id=session_id,
                        system_prompt=CORE_INTERNAL_SYSTEM_PROMPT,
                    ),
                    timeout=request_timeout,
                )
            except Exception as exc:
                err_text = str(exc)
                status = exception_status(exc)
                if status == 401 and await switch_to_temporary_provider("401"):
                    continue
                if is_non_retryable_provider_error(exc):
                    logger.warning(
                        f"[日常生活] 大语言模型请求不可重试：状态={status or '未知'}；"
                        f"提供商={current_provider_id or '默认'}；错误={err_text[:300]}"
                    )
                    if propagate_non_retryable:
                        raise NonRetryableProviderError(
                            err_text,
                            status=status,
                            provider_id=current_provider_id,
                        ) from exc
                    return ""
                transient = is_transient_provider_error(exc)
                if transient:
                    opened = self._provider_circuit.record_failure(current_provider_id)
                else:
                    opened = False
                if attempt < empty_retries:
                    logger.warning(
                        f"[日常生活] 大语言模型调用异常（第 {attempt + 1} 次；"
                        f"{'瞬时故障' if transient else '未知故障'}）：{err_text[:300]}"
                    )
                    await asyncio.sleep(retry_delay(attempt))
                    attempt += 1
                    continue
                if opened:
                    logger.warning(
                        f"[日常生活] 大语言模型服务已短暂熔断：提供商={current_provider_id or '默认'}"
                    )
                if await switch_to_temporary_provider("调用异常"):
                    continue
                logger.warning(
                    f"[日常生活] 大语言模型调用异常（第 {attempt + 1} 次）：{exc}"
                )
                return ""

            text = self._extract_completion_text(resp)
            if text:
                self._provider_circuit.record_success(current_provider_id)
                return text
            if attempt < empty_retries:
                logger.warning("[日常生活] 大语言模型返回为空，准备重试一次")
                attempt += 1
                continue
            if await switch_to_temporary_provider("返回空响应"):
                continue
            return ""
        return ""
