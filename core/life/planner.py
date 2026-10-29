from __future__ import annotations

import asyncio
import copy
import inspect
import time
from functools import cached_property
from types import MethodType
from typing import Any

from astrbot.api import logger
from astrbot.core.star.context import Context

from ..archive import LifeArchive
from ..composition import ComponentCall
from ..config.options import LifeSettings
from ..prompts import CORE_INTERNAL_SYSTEM_PROMPT
from ..search import SearchService
from ..sources import SavedHistoryReader
from ..telemetry import (
    ModelCacheMetrics,
    cache_prefix_version,
    model_request_identity,
    model_task_identity,
)
from .appearance import AppearanceAuditComponent
from .assembly import DailyAssemblyComponent
from .autonomy import LifeAutonomyComponent
from .briefing import DailyBriefingComponent
from .climate import DailyClimateComponent
from .contract import DailyContractComponent
from .draft import DailyDraftComponent
from .engine import DailyEngineComponent
from .inspiration import StyleCatalogComponent
from .invite import InviteComponent
from .locator import DailyLocationGenerationComponent
from .outfit import OutfitComponent
from .people import PersonFactComponent
from .record import DailyRecordComponent
from .reliability import (
    NonRetryableProviderError,
    ProviderCircuit,
    exception_status,
    is_non_retryable_provider_error,
    is_transient_provider_error,
    retry_delay,
)
from .rhythm import LifecycleComponent
from .settlement import LifeActionComponent
from .sourcebook import ReferenceComponent
from .tools import build_time_context, extract_json_from_text
from .weather import WeatherClient
from .weekly import WeekComponent


class LifeBackgroundComposer:
    def __init__(
        self,
        context: Context,
        config: LifeSettings,
        archive: LifeArchive,
        weather_client: WeatherClient,
        contact_resolver=None,
        search_service: SearchService | None = None,
        domain_service=None,
        wardrobe_shortage_callback=None,
    ):
        self.context = context
        self.config = config
        self.archive = archive
        self.weather_client = weather_client
        self.contact_resolver = contact_resolver
        self.saved_history = SavedHistoryReader(context)
        self.search = search_service or SearchService(context, config.search)
        self.domains = domain_service
        self.wardrobe_shortage_callback = wardrobe_shortage_callback
        self._reference_name_cache = {}
        self._gen_lock = asyncio.Lock()
        self._preference_maintenance_done = False
        self._provider_circuit = ProviderCircuit()
        self.model_cache_metrics = ModelCacheMetrics()

    def _get_curr_period(self, target_dt=None) -> str:
        schedule_time = getattr(self.config, "schedule_time", "07:00")
        return build_time_context(target_dt, schedule_time).period

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
        self,
        primary_provider_id: str,
        reason: str = "",
        *,
        call_id: str = "",
    ):
        provider, temporary_id = await self._system_default_provider(
            primary_provider_id
        )
        if not provider:
            return None, ""
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
            except Exception:
                # 指定模型不可用时由调用层选择当前默认模型；不把供应商正文写入日志。
                pass
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

    @staticmethod
    def _reasoning_alias_value(completion: object) -> str | None:
        """读取 OpenAI 兼容服务偶尔使用的非标准推理字段。"""

        choices = getattr(completion, "choices", None)
        if not choices:
            return None
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            message = getattr(choice, "delta", None)
        if message is None:
            return None

        field_seen = False
        fields_set = getattr(message, "model_fields_set", None)
        for key in ("reasoning", "thinking"):
            if isinstance(fields_set, set) and key not in fields_set:
                continue
            if not isinstance(fields_set, set) and not hasattr(message, key):
                continue
            field_seen = True
            value = getattr(message, key, None)
            if value is not None and str(value).strip():
                return str(value)
        return "" if field_seen else None

    @staticmethod
    def _isolated_reasoning_alias_provider(provider: object) -> object:
        """仅为本次内部调用复制 provider，不修改 AstrBot 共享实例。"""

        if getattr(provider, "reasoning_key", None) != "reasoning_content":
            return provider
        original = getattr(provider, "_extract_reasoning_content", None)
        if not callable(original):
            return provider
        try:
            isolated = copy.copy(provider)
        except Exception:
            return provider
        if isolated is provider:
            return provider

        def compatible(_self_provider, completion):
            primary = original(completion)
            if primary is not None and str(primary).strip():
                return primary
            alias = LifeBackgroundComposer._reasoning_alias_value(completion)
            return primary if alias is None else alias

        try:
            isolated._extract_reasoning_content = MethodType(compatible, isolated)
        except (AttributeError, TypeError):
            return provider
        return isolated

    async def _call_llm_text(
        self,
        provider,
        prompt: str,
        session_id: str,
        empty_retries: int = 1,
        primary_provider_id: str = "",
        propagate_non_retryable: bool = False,
        timeout_seconds: float | None = None,
        system_prompt: str | None = None,
    ) -> str:
        primary_provider_id = str(primary_provider_id or "").strip()
        call_id = " ".join(str(session_id or "内部任务").split())[:120]
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
                primary_provider_id,
                reason,
                call_id=call_id,
            )
            if not temporary_provider:
                return False
            current_provider = temporary_provider
            current_provider_id = temporary_id
            return True

        if self._provider_circuit.is_open(current_provider_id):
            if not await switch_to_temporary_provider("连续失败熔断"):
                return ""

        attempt = 0
        request_number = 0
        purpose, stage = model_task_identity(session_id)
        prefix_version = cache_prefix_version(prompt, has_dynamic_section=True)
        while attempt <= empty_retries:
            if attempt > 0 and attempt == empty_retries:
                await switch_to_temporary_provider("达到空响应重试上限")
            started = time.perf_counter()
            metric_identity = model_request_identity(
                current_provider, current_provider_id
            )
            try:
                request_provider = self._isolated_reasoning_alias_provider(
                    current_provider
                )
                resp = await asyncio.wait_for(
                    request_provider.text_chat(
                        prompt,
                        session_id=session_id,
                        system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                        if system_prompt is None
                        else system_prompt,
                    ),
                    timeout=request_timeout,
                )
            except asyncio.CancelledError:
                self.model_cache_metrics.record(
                    None,
                    kind="internal",
                    model=metric_identity,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    failed=True,
                    cancelled=True,
                    prefix_version=prefix_version,
                    purpose=purpose,
                    stage=stage,
                    attempt=request_number,
                )
                raise
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.model_cache_metrics.record(
                    None,
                    kind="internal",
                    model=metric_identity,
                    elapsed_ms=elapsed_ms,
                    failed=True,
                    prefix_version=prefix_version,
                    purpose=purpose,
                    stage=stage,
                    attempt=request_number,
                )
                request_number += 1
                err_text = str(exc).strip() or type(exc).__name__
                if isinstance(exc, TimeoutError):
                    err_text = (
                        f"TimeoutError：模型请求超时（上限 {request_timeout:g} 秒）"
                    )
                status = exception_status(exc)
                non_retryable = is_non_retryable_provider_error(exc)
                if status == 401 and await switch_to_temporary_provider("401"):
                    continue
                if non_retryable:
                    if propagate_non_retryable:
                        raise NonRetryableProviderError(
                            err_text,
                            status=status,
                            provider_id=current_provider_id,
                        ) from exc
                    return ""
                transient = is_transient_provider_error(exc)
                if transient:
                    self._provider_circuit.record_failure(current_provider_id)
                if attempt < empty_retries:
                    await asyncio.sleep(retry_delay(attempt))
                    attempt += 1
                    continue
                if await switch_to_temporary_provider("调用异常"):
                    continue
                return ""

            text = self._extract_completion_text(resp)
            self.model_cache_metrics.record(
                resp,
                kind="internal",
                model=metric_identity,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                failed=not bool(text),
                prefix_version=prefix_version,
                purpose=purpose,
                stage=stage,
                attempt=request_number,
            )
            request_number += 1
            if text:
                self._provider_circuit.record_success(current_provider_id)
                return text
            if attempt < empty_retries:
                attempt += 1
                continue
            if await switch_to_temporary_provider("返回空响应"):
                continue
            return ""
        return ""

    _audit_persona_appearance = ComponentCall(
        "_part_appearance_audit_component",
        AppearanceAuditComponent,
        "_audit_persona_appearance",
    )

    _style_catalog_reference_ids = staticmethod(
        StyleCatalogComponent._style_catalog_reference_ids
    )

    _style_catalog_list = staticmethod(StyleCatalogComponent._style_catalog_list)

    _style_catalog_description = staticmethod(
        StyleCatalogComponent._style_catalog_description
    )

    _style_catalog_attributes = staticmethod(
        StyleCatalogComponent._style_catalog_attributes
    )

    _style_catalog_scene_role = classmethod(
        StyleCatalogComponent._style_catalog_scene_role.__func__
    )

    _style_catalog_component_profiles = classmethod(
        StyleCatalogComponent._style_catalog_component_profiles.__func__
    )

    _style_catalog_scene_compatible = classmethod(
        StyleCatalogComponent._style_catalog_scene_compatible.__func__
    )

    _style_catalog_component_state = staticmethod(
        StyleCatalogComponent._style_catalog_component_state
    )

    _style_catalog_scene_components = classmethod(
        StyleCatalogComponent._style_catalog_scene_components.__func__
    )

    _style_catalog_item_line = classmethod(
        StyleCatalogComponent._style_catalog_item_line.__func__
    )

    _style_catalog_has_clothing_candidates = ComponentCall(
        "_part_style_catalog_component",
        StyleCatalogComponent,
        "_style_catalog_has_clothing_candidates",
    )

    _style_catalog_has_eligible_clothing = ComponentCall(
        "_part_style_catalog_component",
        StyleCatalogComponent,
        "_style_catalog_has_eligible_clothing",
    )

    _style_catalog_resolve_new_outfit_reference_ids = ComponentCall(
        "_part_style_catalog_component",
        StyleCatalogComponent,
        "_style_catalog_resolve_new_outfit_reference_ids",
    )

    _style_catalog_new_outfit_selection = ComponentCall(
        "_part_style_catalog_component",
        StyleCatalogComponent,
        "_style_catalog_new_outfit_selection",
    )

    _style_catalog_context = ComponentCall(
        "_part_style_catalog_component", StyleCatalogComponent, "_style_catalog_context"
    )

    _style_catalog_reference_appearance = ComponentCall(
        "_part_style_catalog_component",
        StyleCatalogComponent,
        "_style_catalog_reference_appearance",
    )

    _mark_style_catalog_references = ComponentCall(
        "_part_style_catalog_component",
        StyleCatalogComponent,
        "_mark_style_catalog_references",
    )

    _history_response_messages = staticmethod(
        ReferenceComponent._history_response_messages
    )

    _history_batch_progress = staticmethod(ReferenceComponent._history_batch_progress)

    _history_next_cursor = staticmethod(ReferenceComponent._history_next_cursor)

    _get_persona = ComponentCall(
        "_part_reference_component", ReferenceComponent, "_get_persona"
    )

    _extract_persona_prompt = staticmethod(ReferenceComponent._extract_persona_prompt)

    _cleanup_conversation = ComponentCall(
        "_part_reference_component", ReferenceComponent, "_cleanup_conversation"
    )

    _resolve_reference_user_profile = ComponentCall(
        "_part_reference_component",
        ReferenceComponent,
        "_resolve_reference_user_profile",
    )

    _format_reference_profile = staticmethod(
        ReferenceComponent._format_reference_profile
    )

    _extract_reference_persona = staticmethod(
        ReferenceComponent._extract_reference_persona
    )

    _split_persona_clauses = staticmethod(ReferenceComponent._split_persona_clauses)

    _clean_reference_name_candidate = staticmethod(
        ReferenceComponent._clean_reference_name_candidate
    )

    _get_recent_chats = ComponentCall(
        "_part_reference_component", ReferenceComponent, "_get_recent_chats"
    )

    _build_person_fact_context = ComponentCall(
        "_part_person_fact_component", PersonFactComponent, "_build_person_fact_context"
    )

    _audit_person_payload = ComponentCall(
        "_part_person_fact_component", PersonFactComponent, "_audit_person_payload"
    )

    _counter_line = staticmethod(LifeAutonomyComponent._counter_line)

    _build_recent_pattern_context = ComponentCall(
        "_part_life_autonomy_component",
        LifeAutonomyComponent,
        "_build_recent_pattern_context",
    )

    _build_autonomous_life_context = ComponentCall(
        "_part_life_autonomy_component",
        LifeAutonomyComponent,
        "_build_autonomous_life_context",
    )

    _save_life_decision_record = ComponentCall(
        "_part_life_autonomy_component",
        LifeAutonomyComponent,
        "_save_life_decision_record",
    )

    _daily_decision_text = staticmethod(LifeAutonomyComponent._daily_decision_text)

    _daily_decision_outcome = staticmethod(
        LifeAutonomyComponent._daily_decision_outcome
    )

    _get_week_plan = ComponentCall(
        "_part_week_component", WeekComponent, "_get_week_plan"
    )

    _get_week_progress = ComponentCall(
        "_part_week_component", WeekComponent, "_get_week_progress"
    )

    _ensure_week_plan = ComponentCall(
        "_part_week_component", WeekComponent, "_ensure_week_plan"
    )

    generate_week_plan = ComponentCall(
        "_part_week_component", WeekComponent, "generate_week_plan"
    )

    _set_validation_issue = ComponentCall(
        "_part_daily_contract_component",
        DailyContractComponent,
        "_set_validation_issue",
    )

    _validate_daily_payload = ComponentCall(
        "_part_daily_contract_component",
        DailyContractComponent,
        "_validate_daily_payload",
    )

    _cooking_payload_issue = staticmethod(DailyContractComponent._cooking_payload_issue)

    _action_timeline_index = staticmethod(DailyContractComponent._action_timeline_index)

    _daytime_outfit_transition_issue = classmethod(
        DailyContractComponent._daytime_outfit_transition_issue.__func__
    )

    _repeat_text = staticmethod(DailyContractComponent._repeat_text)

    _repeat_bigrams = staticmethod(DailyContractComponent._repeat_bigrams)

    _repeat_similarity = classmethod(DailyContractComponent._repeat_similarity.__func__)

    _repeat_sequence_similarity = classmethod(
        DailyContractComponent._repeat_sequence_similarity.__func__
    )

    _day_repeat_profile = classmethod(
        DailyContractComponent._day_repeat_profile.__func__
    )

    _decision_novelty_text = staticmethod(DailyContractComponent._decision_novelty_text)

    _repeat_generation_issue = ComponentCall(
        "_part_daily_contract_component",
        DailyContractComponent,
        "_repeat_generation_issue",
    )

    _validate_outfit_style = staticmethod(DailyContractComponent._validate_outfit_style)

    _timeline_minutes = staticmethod(DailyContractComponent._timeline_minutes)

    _minutes_text = staticmethod(DailyContractComponent._minutes_text)

    _timeline_item_duration = staticmethod(
        DailyContractComponent._timeline_item_duration
    )

    _full_day_coverage_profile = staticmethod(
        DailyContractComponent._full_day_coverage_profile
    )

    _full_day_coverage_thresholds = staticmethod(
        DailyContractComponent._full_day_coverage_thresholds
    )

    _full_day_coverage_ok = classmethod(
        DailyContractComponent._full_day_coverage_ok.__func__
    )

    _coverage_contract = staticmethod(DailyContractComponent._coverage_contract)

    _contract_json_text = staticmethod(DailyContractComponent._contract_json_text)

    _build_contract_prompt = ComponentCall(
        "_part_daily_contract_component",
        DailyContractComponent,
        "_build_contract_prompt",
    )

    _build_repair_prompt = ComponentCall(
        "_part_daily_contract_component", DailyContractComponent, "_build_repair_prompt"
    )

    _repair_strategy_text = staticmethod(DailyContractComponent._repair_strategy_text)

    _timeline_prompt_persona_section = staticmethod(
        DailyDraftComponent._timeline_prompt_persona_section
    )

    _timeline_prompt_week_section = staticmethod(
        DailyDraftComponent._timeline_prompt_week_section
    )

    _timeline_prompt_memo_section = staticmethod(
        DailyDraftComponent._timeline_prompt_memo_section
    )

    _timeline_prompt_repeat_section = staticmethod(
        DailyDraftComponent._timeline_prompt_repeat_section
    )

    _build_timeline_prompt = ComponentCall(
        "_part_daily_draft_component", DailyDraftComponent, "_build_timeline_prompt"
    )

    _format_commitment_prompt = staticmethod(
        DailyBriefingComponent._format_commitment_prompt
    )

    _current_residence_boundary_date = ComponentCall(
        "_part_daily_briefing_component",
        DailyBriefingComponent,
        "_current_residence_boundary_date",
    )

    _is_before_residence_boundary = ComponentCall(
        "_part_daily_briefing_component",
        DailyBriefingComponent,
        "_is_before_residence_boundary",
    )

    _build_life_inertia_context = ComponentCall(
        "_part_daily_briefing_component",
        DailyBriefingComponent,
        "_build_life_inertia_context",
    )

    _build_previous_life_context = ComponentCall(
        "_part_daily_briefing_component",
        DailyBriefingComponent,
        "_build_previous_life_context",
    )

    _build_history_schedule_summary = ComponentCall(
        "_part_daily_briefing_component",
        DailyBriefingComponent,
        "_build_history_schedule_summary",
    )

    _collect_recent_chat_context = ComponentCall(
        "_part_daily_briefing_component",
        DailyBriefingComponent,
        "_collect_recent_chat_context",
    )

    _build_world_context = ComponentCall(
        "_part_daily_briefing_component", DailyBriefingComponent, "_build_world_context"
    )

    _classify_weather_condition = ComponentCall(
        "_part_daily_climate_component",
        DailyClimateComponent,
        "_classify_weather_condition",
    )

    _has_weather_safety_risk = staticmethod(
        DailyClimateComponent._has_weather_safety_risk
    )

    _build_weather_sections = ComponentCall(
        "_part_daily_climate_component",
        DailyClimateComponent,
        "_build_weather_sections",
    )

    _day_from_generation = ComponentCall(
        "_part_daily_assembly_component", DailyAssemblyComponent, "_day_from_generation"
    )

    _filter_places_by_day_evidence = staticmethod(
        DailyAssemblyComponent._filter_places_by_day_evidence
    )

    _meta_text = staticmethod(DailyAssemblyComponent._meta_text)

    _mood_color_text = classmethod(DailyAssemblyComponent._mood_color_text.__func__)

    _meta_from_generation = ComponentCall(
        "_part_daily_assembly_component",
        DailyAssemblyComponent,
        "_meta_from_generation",
    )

    _persist_generated_day = ComponentCall(
        "_part_daily_record_component", DailyRecordComponent, "_persist_generated_day"
    )

    _daily_location_request_prompt = staticmethod(
        DailyLocationGenerationComponent._daily_location_request_prompt
    )

    _daily_location_candidates_context = staticmethod(
        DailyLocationGenerationComponent._daily_location_candidates_context
    )

    _prepare_daily_location_context = ComponentCall(
        "_part_daily_location_generation_component",
        DailyLocationGenerationComponent,
        "_prepare_daily_location_context",
    )

    _append_daily_location_context = staticmethod(
        DailyLocationGenerationComponent._append_daily_location_context
    )

    _normalize_extra = staticmethod(DailyEngineComponent._normalize_extra)

    _build_daily_replacement_context = staticmethod(
        DailyEngineComponent._build_daily_replacement_context
    )

    generate_daily = ComponentCall(
        "_part_daily_engine_component", DailyEngineComponent, "generate_daily"
    )

    _meta_float = staticmethod(LifecycleComponent._meta_float)

    maintain_preference_consistency = ComponentCall(
        "_part_lifecycle_component",
        LifecycleComponent,
        "maintain_preference_consistency",
    )

    _compute_sleep_continuity = ComponentCall(
        "_part_lifecycle_component", LifecycleComponent, "_compute_sleep_continuity"
    )

    _apply_lifecycle_to_day = ComponentCall(
        "_part_lifecycle_component", LifecycleComponent, "_apply_lifecycle_to_day"
    )

    _build_lifecycle_context = ComponentCall(
        "_part_lifecycle_component", LifecycleComponent, "_build_lifecycle_context"
    )

    _format_physiological_rhythm_trend_context = staticmethod(
        LifecycleComponent._format_physiological_rhythm_trend_context
    )

    compose_daily_review = ComponentCall(
        "_part_lifecycle_component", LifecycleComponent, "compose_daily_review"
    )

    learn_preferences_from_payload = ComponentCall(
        "_part_lifecycle_component",
        LifecycleComponent,
        "learn_preferences_from_payload",
    )

    persist_life_events_from_payload = ComponentCall(
        "_part_lifecycle_component",
        LifecycleComponent,
        "persist_life_events_from_payload",
    )

    _load_action_settlements = staticmethod(
        LifeActionComponent._load_action_settlements
    )

    _validate_action_contract = staticmethod(
        LifeActionComponent._validate_action_contract
    )

    _action_condition_value = staticmethod(LifeActionComponent._action_condition_value)

    _action_condition_matches = staticmethod(
        LifeActionComponent._action_condition_matches
    )

    _validate_action_preconditions = classmethod(
        LifeActionComponent._validate_action_preconditions.__func__
    )

    _resolve_action_effects = staticmethod(LifeActionComponent._resolve_action_effects)

    _validate_action_effects = staticmethod(
        LifeActionComponent._validate_action_effects
    )

    _commit_action_effects = staticmethod(LifeActionComponent._commit_action_effects)

    _planned_outfit_action_is_superseded = staticmethod(
        LifeActionComponent._planned_outfit_action_is_superseded
    )

    settle_life_action = ComponentCall(
        "_part_life_action_component", LifeActionComponent, "settle_life_action"
    )

    sync_day_world_facts = ComponentCall(
        "_part_life_action_component", LifeActionComponent, "sync_day_world_facts"
    )

    _save_action_receipt = ComponentCall(
        "_part_life_action_component", LifeActionComponent, "_save_action_receipt"
    )

    _receipt_evidence_text = staticmethod(LifeActionComponent._receipt_evidence_text)

    record_life_action_receipt = ComponentCall(
        "_part_life_action_component", LifeActionComponent, "record_life_action_receipt"
    )

    record_matching_life_action_receipt = ComponentCall(
        "_part_life_action_component",
        LifeActionComponent,
        "record_matching_life_action_receipt",
    )

    settle_and_persist_life_action = ComponentCall(
        "_part_life_action_component",
        LifeActionComponent,
        "settle_and_persist_life_action",
    )

    settle_completed_planned_actions = ComponentCall(
        "_part_life_action_component",
        LifeActionComponent,
        "settle_completed_planned_actions",
    )

    extract_schedule_anchors = ComponentCall(
        "_part_life_action_component", LifeActionComponent, "extract_schedule_anchors"
    )

    refine_upcoming_anchors = ComponentCall(
        "_part_life_action_component", LifeActionComponent, "refine_upcoming_anchors"
    )

    replan_future_anchors = ComponentCall(
        "_part_life_action_component", LifeActionComponent, "replan_future_anchors"
    )

    evaluate_reflection_threshold = ComponentCall(
        "_part_life_action_component",
        LifeActionComponent,
        "evaluate_reflection_threshold",
    )

    _TIMELINE_AUDIT_ROUTE_FIELDS = InviteComponent._TIMELINE_AUDIT_ROUTE_FIELDS

    _TIMELINE_AUDIT_PLACE_FIELDS = InviteComponent._TIMELINE_AUDIT_PLACE_FIELDS

    _serialized_current_places = staticmethod(
        InviteComponent._serialized_current_places
    )

    _reusable_location_candidates = staticmethod(
        InviteComponent._reusable_location_candidates
    )

    _split_timeline_at = staticmethod(InviteComponent._split_timeline_at)

    _copy_timeline_item = staticmethod(InviteComponent._copy_timeline_item)

    _apply_timeline_edits = classmethod(InviteComponent._apply_timeline_edits.__func__)

    _restore_protected_timeline = classmethod(
        InviteComponent._restore_protected_timeline.__func__
    )

    _route_anchor_from_past_timeline = classmethod(
        InviteComponent._route_anchor_from_past_timeline.__func__
    )

    _merge_audited_places = classmethod(InviteComponent._merge_audited_places.__func__)

    handle_invite = ComponentCall(
        "_part_invite_component", InviteComponent, "handle_invite"
    )

    reconcile_commitment_with_timeline = ComponentCall(
        "_part_invite_component", InviteComponent, "reconcile_commitment_with_timeline"
    )

    _replace_enum_token = staticmethod(OutfitComponent._replace_enum_token)

    _localize_outfit_reason = classmethod(
        OutfitComponent._localize_outfit_reason.__func__
    )

    _compact_outfit_evidence = staticmethod(OutfitComponent._compact_outfit_evidence)

    _outfit_fact_context_text = classmethod(
        OutfitComponent._outfit_fact_context_text.__func__
    )

    _state_outfit_evidence_text = classmethod(
        OutfitComponent._state_outfit_evidence_text.__func__
    )

    _verified_outfit_change_source = classmethod(
        OutfitComponent._verified_outfit_change_source.__func__
    )

    _timeline_item_text = staticmethod(OutfitComponent._timeline_item_text)

    _timeline_context_text = classmethod(
        OutfitComponent._timeline_context_text.__func__
    )

    _state_context_text = staticmethod(OutfitComponent._state_context_text)

    _outfit_scene_context_text = staticmethod(
        OutfitComponent._outfit_scene_context_text
    )

    update_outfit = ComponentCall(
        "_part_outfit_component", OutfitComponent, "update_outfit"
    )

    @cached_property
    def _part_appearance_audit_component(self):
        return AppearanceAuditComponent(self)

    @cached_property
    def _part_style_catalog_component(self):
        return StyleCatalogComponent(self)

    @cached_property
    def _part_reference_component(self):
        return ReferenceComponent(self)

    @cached_property
    def _part_person_fact_component(self):
        return PersonFactComponent(self)

    @cached_property
    def _part_life_autonomy_component(self):
        return LifeAutonomyComponent(self)

    @cached_property
    def _part_week_component(self):
        return WeekComponent(self)

    @cached_property
    def _part_daily_contract_component(self):
        return DailyContractComponent(self)

    @cached_property
    def _part_daily_draft_component(self):
        return DailyDraftComponent(self)

    @cached_property
    def _part_daily_briefing_component(self):
        return DailyBriefingComponent(self)

    @cached_property
    def _part_daily_climate_component(self):
        return DailyClimateComponent(self)

    @cached_property
    def _part_daily_assembly_component(self):
        return DailyAssemblyComponent(self)

    @cached_property
    def _part_daily_record_component(self):
        return DailyRecordComponent(self)

    @cached_property
    def _part_daily_location_generation_component(self):
        return DailyLocationGenerationComponent(self)

    @cached_property
    def _part_daily_engine_component(self):
        return DailyEngineComponent(self)

    @cached_property
    def _part_lifecycle_component(self):
        return LifecycleComponent(self)

    @cached_property
    def _part_life_action_component(self):
        return LifeActionComponent(self)

    @cached_property
    def _part_invite_component(self):
        return InviteComponent(self)

    @cached_property
    def _part_outfit_component(self):
        return OutfitComponent(self)
