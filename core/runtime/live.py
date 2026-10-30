from __future__ import annotations

import asyncio
import datetime
from collections.abc import Awaitable, Callable
from functools import cached_property
from typing import Any

from astrbot.api import logger

from ..composition import ComponentCall
from ..labels import page_status_reason_label
from ..media.cleanup import MediaFileCleanupComponent
from ..memos.conduit import MemosComponent
from ..sight import bridge as _part_sight_component
from ..sight.cinematics import NativeSightComponent
from ..sight.identity import SightIdentityComponent
from ..sight.prune import SightCleanupComponent
from ..sources.dispatch import send_message_to_scope
from ..telemetry import ModelCacheMetrics, model_request_identity
from .addressing import ChatAddressingService
from .background import BackgroundTaskScheduler
from .capture.awareness import AwarenessComponent
from .capture.batch import ChatMemoryBatchComponent
from .capture.deposit import ImprintDepositComponent
from .capture.diction import ExpressionLearningComponent
from .capture.emoji import depot as _part_emoji_cache_component
from .capture.emoji import vision as _part_emoji_vision_component
from .capture.emoji.anchor import EmojiBaseComponent
from .capture.emoji.gather import EmojiGatherComponent
from .capture.emoji.sweep import EmojiSweepComponent
from .capture.event import CaptureEventComponent
from .capture.execution import ChatExecutionComponent
from .capture.harvest import ImprintHarvestComponent
from .capture.payload import CapturePayloadComponent
from .capture.persona import CapturePersonaComponent
from .capture.portrait import ImprintPortraitComponent
from .capture.tune.packet import PacketComponent
from .capture.tune.profile import ProfileComponent
from .capture.tune.target import TargetComponent
from .channel import reverse as _part_runtime_reverse_media_component
from .channel import suite as _part_runtime_photo_suite_media_component
from .channel.audio import RuntimeVoiceMediaComponent
from .channel.image import RuntimeImageMediaComponent
from .channel.motion import RuntimeVideoMediaComponent
from .channel.stylist import RuntimeStyleCatalogComponent
from .channel.summary import RuntimeMediaCommonComponent
from .choice.apply import ResponseGateApplyComponent
from .choice.incoming import ResponseGateEventComponent
from .choice.score import ResponseGateScoreComponent
from .choice.sense import ResponseGateSenseComponent
from .choice.state import ResponseGateStateComponent
from .clothier import WardrobeLifeService
from .context import InteractionContext, InteractionContextService
from .continuous import ContinuousLifeService
from .delay import ChatDelayComponent
from .deliberation import ContinuousDecisionService
from .elapsed import ConversationTimingService
from .expression import ExpressionHintService
from .forward import TextForwardService
from .gateway import ModelCallOptions, ModelGateway
from .generation import DailyGenerationResult, DailyGenerationService
from .inject import InjectComponent
from .integration import ExternalActivityService, ShareExpressionService
from .layer.chain import LayerChainComponent
from .layer.prefix import RequestPrefixComponent
from .layer.text import LayerTextComponent
from .locks import operation_lock
from .markers import LOG_PREFIX
from .meaning import MeaningService
from .mirror.chat import SnapshotChatComponent
from .mirror.export import SnapshotExportComponent
from .mirror.pack import SnapshotPackComponent
from .mirror.seed import SnapshotSeedComponent
from .mirror.tempo import SnapshotTempoComponent
from .outbound import OutboundLogService
from .participation import LivingStateService
from .past import RuntimeHistoryComponent
from .proactive.breeze.effect import AirEffectComponent
from .proactive.breeze.meter import AirMeterComponent
from .proactive.decide.aware import ProactiveAwareComponent
from .proactive.decide.flow import ProactiveFlowComponent
from .proactive.decide.ledger import ProactiveStoreComponent
from .proactive.decide.prompt import ProactivePromptComponent
from .proactive.followup import ProactiveFollowupComponent
from .proactive.gesture import ProactiveGestureComponent
from .proactive.lifecycle import ProactiveLifecycleComponent
from .proactive.procontext import ProactiveContextComponent
from .proactive.rendezvous import SharedActivityContactComponent
from .proactive.revisit import ProactiveRevisitComponent
from .proactive.scope import ProactiveScopeComponent
from .proactive.segment import ProactiveSegmentComponent
from .proactive.send import ProactiveSendComponent
from .reaction import ToolReactionService
from .recall import RecallService
from .receipt import RuntimeActionReceiptComponent
from .refresh import RefreshComponent
from .remember import LifeMemoryService
from .reply import SemanticSegmentRuntimeComponent
from .shade.render import HiddenExperienceComponent
from .spine import boot as _part_spine_boot_component
from .spine.adapt import SpineAdaptComponent
from .spine.pulse import SpinePulseComponent
from .spine.rsvp import SpineInviteComponent
from .spine.sky import SpineClimateComponent
from .stage.album import StageAlbumComponent
from .stage.figure import StageFigureComponent
from .stage.frame import StageFrameComponent
from .stage.lens import StageLensComponent
from .stage.reel import StageReelComponent
from .stage.speech import StageVoiceComponent
from .status import StateRefreshService
from .style import ChatStyleRuntimeComponent
from .trail import parse as _part_history_parse_component
from .trail.scene import HistorySceneComponent
from .trail.watch import HistoryWatchComponent
from .trail.write import HistoryWriteComponent
from .turns import ContinuousTurnComponent
from .veil import InjectVeilComponent
from .voice import VoiceSwitchComponent
from .voice.judge import VoiceSwitchJudgeComponent
from .voice.limit import VoiceSwitchGateComponent
from .voice.preface import SilentToolPrefaceComponent
from .voice.trace import VoiceSwitchRecordComponent
from .weave.dock import StructuredDockComponent
from .weave.forge import StructuredForgeComponent
from .weave.glance import StructuredGlanceComponent
from .weave.grain import StructuredBaseComponent
from .weave.route import StructuredRouteComponent


class DailyLifeRuntime:
    """日常生活引擎的运行时服务。

    入口装饰器保留在入口文件；这里负责状态、存储、定时任务、提示词注入和工具动作。
    """

    def _model_cache_meter(self) -> ModelCacheMetrics:
        meter = getattr(self, "_model_cache_metrics", None)
        if not isinstance(meter, ModelCacheMetrics):
            meter = ModelCacheMetrics()
            self._model_cache_metrics = meter
        return meter

    def _init_background_tasks(self) -> None:
        self._background_scheduler = BackgroundTaskScheduler()

    def _background_scheduler_for_runtime(self) -> BackgroundTaskScheduler:
        scheduler = getattr(self, "_background_scheduler", None)
        if scheduler is None:
            scheduler = self._background_scheduler = BackgroundTaskScheduler()
        return scheduler

    async def _cancel_background_tasks(self) -> None:
        await self._background_scheduler_for_runtime().cancel_all()

    def _schedule_background_task(
        self,
        coro: Awaitable,
        label: str = "",
        key: str = "",
        category: str = "",
        on_cancel: Callable[[], None] | None = None,
    ) -> bool:
        return self._background_scheduler_for_runtime().schedule(
            coro,
            label=label,
            key=key,
            category=category,
            lease_factory=self.runtime_service_lease,
            on_cancel=on_cancel,
        )

    @cached_property
    def daily_generation(self) -> DailyGenerationService:
        return DailyGenerationService(
            archive=lambda: self.archive,
            composer=lambda: self.composer,
            persona=lambda: self.get_persona_text(),
            notify_status=self.mark_page_status_changed,
            failed_dates=lambda: getattr(self, "failed_dates", None),
            lease_factory=self.runtime_service_lease,
        )

    async def run_daily_generation(
        self,
        *,
        date: datetime.datetime,
        source: str,
        force: bool,
        extra: str = "",
        target_hour: int | None = None,
        delete_existing: bool = False,
        use_web: bool = False,
        search_keyword: str = "今日生活",
        search_prompt: str = "",
        search_category: str = "今日生活背景",
        reject_if_busy: bool = False,
    ) -> DailyGenerationResult:
        return await self.daily_generation.run_daily_generation(
            date=date,
            source=source,
            force=force,
            extra=extra,
            target_hour=target_hour,
            delete_existing=delete_existing,
            use_web=use_web,
            search_keyword=search_keyword,
            search_prompt=search_prompt,
            search_category=search_category,
            reject_if_busy=reject_if_busy,
        )

    def daily_generation_status(self, date_str: str = "") -> dict[str, Any]:
        return self.daily_generation.daily_generation_status(date_str)

    @cached_property
    def wardrobe_life(self) -> WardrobeLifeService:
        return WardrobeLifeService(
            archive=lambda: self.archive,
            config=lambda: self.config,
            now=lambda: self._runtime_now(),
            in_quiet_hours=lambda now: self._state_refresh_in_quiet_hours(now),
            resolve_target=lambda now: self.resolve_injection_target(now),
            schedule_background=lambda *args, **kwargs: self._schedule_background_task(
                *args, **kwargs
            ),
            persona=lambda: self.get_persona_text(),
            text_provider=lambda provider_id: self.get_text_provider(provider_id),
            call_text_model=lambda *args, **kwargs: self.call_text_model(
                *args, **kwargs
            ),
            close_text_session=lambda session_id: self.close_text_session(session_id),
            notify_status=lambda reason: self.mark_page_status_changed(reason),
            session_id=self._event_session_id,
            message_id=self._event_message_id,
            image_service=lambda: self.media.image,
            style_item_ids=lambda value: self._style_item_ids(value),
            creative_prompt=lambda *args, **kwargs: self._creative_style_prompt(
                *args, **kwargs
            ),
            edit_image=lambda *args, **kwargs: self._edit_life_image_with_policy_retry(
                *args, **kwargs
            ),
            generate_image=lambda *args, **kwargs: (
                self._generate_life_image_with_policy_retry(*args, **kwargs)
            ),
            learn_style=lambda *args, **kwargs: self._learn_style_catalog_image(
                *args, **kwargs
            ),
            review_outfit=lambda: self.check_autonomous_life_update(),
        )

    async def check_wardrobe_life(self) -> None:
        await self.wardrobe_life.check_wardrobe_life()

    async def queue_wardrobe_generation(
        self, event: Any, *, requirement: str, generation_mode: str, count: int
    ) -> str:
        return await self.wardrobe_life.queue_wardrobe_generation(
            event, requirement=requirement, generation_mode=generation_mode, count=count
        )

    async def manage_wardrobe(
        self,
        event: Any,
        *,
        operation: str,
        item_ids: list[int],
        reason: str = "",
        duration_minutes: int = 30,
    ) -> str:
        return await self.wardrobe_life.manage_wardrobe(
            event,
            operation=operation,
            item_ids=item_ids,
            reason=reason,
            duration_minutes=duration_minutes,
        )

    @cached_property
    def continuous_decision(self) -> ContinuousDecisionService:
        from ..life.itinerary import itinerary_wardrobe_context

        return ContinuousDecisionService(
            archive=lambda: self.archive,
            config=lambda: self.config,
            persona=lambda: self.composer._get_persona(),
            recent_chats=lambda persona: self.composer._collect_recent_chat_context(
                persona
            ),
            scope_messages=lambda scope: self._read_recent_context_messages(scope),
            text_provider=lambda provider_id: self.get_text_provider(provider_id),
            call_text_model=lambda *args, **kwargs: self.call_text_model(
                *args, **kwargs
            ),
            close_text_session=lambda session_id: self.close_text_session(session_id),
            wardrobe_context=lambda day, now: itinerary_wardrobe_context(
                self.composer, day.date, now
            ),
        )

    @cached_property
    def continuous_life(self) -> ContinuousLifeService:
        from ..life.actionchain import prepare_new_actions

        return ContinuousLifeService(
            archive=lambda: self.archive,
            config=lambda: self.config,
            now=lambda: self._runtime_now(),
            resolve_target=lambda now: self.resolve_injection_target(now),
            message_id=self._event_message_id,
            session_id=self._event_session_id,
            notify_status=lambda reason: self.mark_page_status_changed(reason),
            decide=lambda day, world, now: self.continuous_decision.choose(
                day, world, now
            ),
            validate_action=lambda action, **kwargs: self.domains.validate_action(
                action, **kwargs
            ),
            prepare_actions=lambda day, steps: prepare_new_actions(
                self.composer, day, steps
            ),
            outfit_superseded=lambda *args: (
                self.composer._planned_outfit_action_is_superseded(*args)
            ),
            settle_action=lambda *args, **kwargs: self.composer.settle_life_action(
                *args, **kwargs
            ),
            save_receipt=lambda *args, **kwargs: self.composer._save_action_receipt(
                *args, **kwargs
            ),
            settle_and_persist=lambda *args, **kwargs: (
                self.composer.settle_and_persist_life_action(*args, **kwargs)
            ),
            load_settlements=lambda day: self.composer._load_action_settlements(day),
            record_receipt=lambda *args, **kwargs: (
                self.composer.record_life_action_receipt(*args, **kwargs)
            ),
            validate_preconditions=lambda *args: (
                self.composer._validate_action_preconditions(*args)
            ),
            validate_contract=lambda *args: self.composer._validate_action_contract(
                *args
            ),
            action_lock=lambda key: operation_lock(self, key),
        )

    @cached_property
    def bili_browsing(self):
        from ..browsing.surfing import BiliBrowseService

        return BiliBrowseService(self)

    async def check_bili_browsing(self) -> None:
        await self.bili_browsing.tick()

    async def note_bili_browse_incoming(self, event: Any) -> None:
        if callable(getattr(getattr(self, "archive", None), "_run_db", None)):
            await self.bili_browsing.incoming(event)

    async def record_bili_browse_delivery(self, scope: str, text: str, *, at: str = "") -> bool:
        if not callable(getattr(getattr(self, "archive", None), "_run_db", None)):
            return True
        try:
            from ..life.body import instant

            occurred_at = instant(at)
            await self.bili_browsing.delivered_text(
                scope, text, at=occurred_at.isoformat(timespec="seconds") if occurred_at else "", defer=True
            )
            return True
        except Exception:
            logger.warning("[日常生活] 浏览分享回执未能保存，保留原发送结果", exc_info=True)
            return False

    async def note_bili_browse_delivered(self, event: Any) -> None:
        result = getattr(event, "get_result", lambda: None)()
        text = "\n".join(str(getattr(item, "text", "") or "") for item in (getattr(result, "chain", None) or []))
        await self.record_bili_browse_delivery(self._event_session_id(event), text)

    @cached_property
    def memory_life(self):
        from .memorylife import MemoryLifeService
        return MemoryLifeService(self)

    async def check_continuous_life(self) -> None:
        await self.continuous_life.check_continuous_life()
        self._schedule_background_task(self.memory_life.tick(), label="记忆关联与阅读", key="memory_life", category="maintenance")

    async def note_continuous_chat_exchange(self, event: Any) -> None:
        await self.continuous_life.note_continuous_chat_exchange(event)

    async def request_conversation_action(self, event: Any, detail: str) -> str:
        result = await self.continuous_life.request_conversation_action(event, detail)
        self._schedule_background_task(
            self.continuous_life.check_continuous_life(),
            label="对话生活行动", key="conversation_action", category="life",
        )
        return result

    async def sync_continuous_kernel_state(
        self,
        day,
        *,
        now: datetime.datetime | None = None,
        event_kind: str = "",
        source_id: str = "",
        summary: str = "",
        evidence_ids: list[str] | None = None,
    ) -> None:
        await self.continuous_life.sync_continuous_kernel_state(
            day,
            now=now,
            event_kind=event_kind,
            source_id=source_id,
            summary=summary,
            evidence_ids=evidence_ids,
        )

    @cached_property
    def state_refresh(self) -> StateRefreshService:
        return StateRefreshService(
            config=lambda: self.config.state,
            archive=lambda: self.archive,
            compute_sleep=lambda previous, day: self.composer._compute_sleep_continuity(
                previous, day
            ),
            replan=lambda day, replacements, **kwargs: (
                self.composer.replan_future_anchors(day, replacements, **kwargs)
            ),
            sync_world_facts=lambda day, **kwargs: self.composer.sync_day_world_facts(
                day, **kwargs
            ),
            text_provider=lambda provider_id: self.get_text_provider(provider_id),
            call_text_model=lambda *args, **kwargs: self.call_text_model(
                *args, **kwargs
            ),
            close_text_session=lambda session_id: self.close_text_session(session_id),
            event_was_recalled=lambda event, **kwargs: self.event_was_recalled(
                event, **kwargs
            ),
            sync_kernel=lambda day, **kwargs: self.sync_continuous_kernel_state(
                day, **kwargs
            ),
            notify_status=lambda reason: self.mark_page_status_changed(reason),
        )

    async def refresh_state_for_day(
        self,
        date_str: str,
        data=None,
        now: datetime.datetime | None = None,
        source: str = "idle",
        detail: str = "",
        force: bool = False,
        source_event: Any = None,
        notify_page: bool = True,
    ):
        return await self.state_refresh.refresh_state_for_day(
            date_str,
            data=data,
            now=now,
            source=source,
            detail=detail,
            force=force,
            source_event=source_event,
            notify_page=notify_page,
        )

    @cached_property
    def expression_hints(self) -> ExpressionHintService:
        return ExpressionHintService(
            config=lambda: self.config,
            voice_cadence=lambda event: self._voice_switch_cadence_snapshot(event),
            voice_allowed=lambda scope: self._voice_allowed_for_scope(scope),
            image_reference=lambda event: self._resolve_life_image_reference(event),
            media_cadence=lambda event: self._hidden_media_cadence_hint(event),
        )

    def build_hidden_expression_channel_hint(self, event: Any = None) -> str:
        return self.expression_hints.build_hidden_expression_channel_hint(event)

    def _voice_expression_channel_enabled(self, event: Any = None) -> bool:
        return self.expression_hints._voice_expression_channel_enabled(event)

    @cached_property
    def interaction_context(self) -> InteractionContextService:
        return InteractionContextService(
            archive=lambda: self.archive,
            session_id=lambda event: self._event_session_id(event),
            profile_id=lambda event: self._event_profile_id(event),
            message_id=lambda event: self._event_message_id(event),
            is_group=lambda event: self._event_is_group_message(event),
            turn_messages=lambda event: self.continuous_turn_messages(event),
        )

    async def apply_interaction_turn_decision(
        self, event: Any, value: Any, *, now: datetime.datetime | None = None
    ) -> dict[str, Any] | None:
        return await self.interaction_context.apply_turn_decision(event, value, now=now)

    async def resolve_interaction_context(
        self,
        *,
        event: Any = None,
        target_scope: str = "",
        profile_id: str = "",
        snapshot: dict[str, Any] | None = None,
        now: datetime.datetime | None = None,
    ) -> InteractionContext:
        return await self.interaction_context.resolve(
            event=event,
            target_scope=target_scope,
            profile_id=profile_id,
            snapshot=snapshot,
            now=now,
        )

    @cached_property
    def text_forward(self) -> TextForwardService:
        return TextForwardService(
            context=self.context,
            keep_default_send=lambda event, text: (
                self._chat_style_should_keep_default_send(event, text)
            ),
            log_outbound=lambda chain, **kwargs: self.log_outbound_message_async(
                chain, **kwargs
            ),
        )

    def capture_t2i_source_before_send(self, event: Any) -> bool:
        return self.text_forward.capture_before_send(event)

    def note_t2i_image_sent(self, event: Any) -> bool:
        return self.text_forward.note_image_sent(event)

    async def forward_t2i_text(self, event: Any, *, index: int = 1) -> str:
        return await self.text_forward.forward_text(event, index=index)

    def _evict_scope_services(self, scopes: set[str]) -> None:
        timing = self.__dict__.get("conversation_timing")
        if timing is not None:
            timing.evict_scopes(scopes)
        forward = self.__dict__.get("text_forward")
        if forward is not None:
            forward.evict_scopes(scopes)
        recall = self.__dict__.get("recall")
        if recall is not None:
            recall.evict_scopes(scopes)
        addressing = self.__dict__.get("addressing")
        if addressing is not None:
            addressing.evict_scopes(scopes)
        reactions = self.__dict__.get("tool_reactions")
        if reactions is not None:
            reactions.evict_scopes(scopes)

    @cached_property
    def tool_reactions(self) -> ToolReactionService:
        return ToolReactionService(
            message_id=self._event_message_id,
            session_id=self._event_session_id,
            event_sources=self._event_sources,
            was_recalled=self._event_message_was_recalled,
            background_request=lambda event, name: self._background_tool_request(
                event, name
            ),
        )

    async def note_tool_reaction_start(
        self, event: Any, tool: Any, tool_args: dict | None = None
    ) -> bool:
        return await self.tool_reactions.note_tool_reaction_start(
            event, tool, tool_args
        )

    async def note_tool_reaction_result(
        self,
        event: Any,
        tool: Any,
        tool_args: dict | None = None,
        tool_result: Any = None,
    ) -> bool:
        return await self.tool_reactions.note_tool_reaction_result(
            event, tool, tool_args, tool_result
        )

    async def note_tool_reaction_agent_done(
        self, event: Any, response: Any = None
    ) -> bool:
        return await self.tool_reactions.note_tool_reaction_agent_done(event, response)

    def suppress_media_agent_error(self, event: Any, response: Any = None) -> bool:
        return self.tool_reactions.suppress_media_agent_error(event, response)

    async def note_tool_reaction_message_sent(self, event: Any) -> bool:
        return await self.tool_reactions.note_tool_reaction_message_sent(event)

    async def finish_tool_reaction(
        self, event: Any, tool_name: str, *, success: bool
    ) -> bool:
        return await self.tool_reactions.finish_tool_reaction(
            event, tool_name, success=success
        )

    def cancel_tool_reaction(self, event: Any, tool_name: str) -> None:
        self.tool_reactions.cancel_tool_reaction(event, tool_name)

    @cached_property
    def addressing(self) -> ChatAddressingService:
        return ChatAddressingService(
            is_group=self._event_is_group_message,
            message_id=self._event_message_id,
            session_id=self._event_session_id,
            is_directed=self._event_is_directed,
            safe_event_call=self._safe_event_call,
            find_message=self._structured_find_message,
            scope_messages=self._structured_scope_messages,
            extract_reply=self._structured_extract_reply,
            keep_default_send=lambda event, text: (
                self._chat_style_should_keep_default_send(event, text)
            ),
            sequence_attr=self._STRUCTURED_EVENT_SEQUENCE_ATTR,
            pending_segments_attr=self._CHAT_STYLE_PENDING_SEGMENTS_ATTR,
        )

    def decorate_group_addressing_chain(
        self,
        chain: Any,
        *,
        target_scope: str = "",
        source_event: Any = None,
        source_message_id: str = "",
        segment_index: int = 0,
        source: str = "chat",
    ) -> bool:
        return self.addressing.decorate_group_addressing_chain(
            chain,
            target_scope=target_scope,
            source_event=source_event,
            source_message_id=source_message_id,
            segment_index=segment_index,
            source=source,
        )

    def apply_group_addressing_before_send(self, event: Any) -> bool:
        return self.addressing.apply_group_addressing_before_send(event)

    @cached_property
    def recall(self) -> RecallService:
        return RecallService(
            event_sources=self._event_sources,
            session_id=self._event_session_id,
            message_id=self._event_message_id,
            remove_context=(
                self.remove_recalled_structured_message,
                self.remove_recalled_proactive_candidate,
                self.remove_recalled_sight_context,
            ),
            send_scope=lambda scope, chain, **kwargs: send_message_to_scope(
                self.context, scope, chain, **kwargs
            ),
            log_outbound=lambda chain, **kwargs: self.log_outbound_message_async(
                chain, **kwargs
            ),
            log_limit=self._MESSAGE_DEDUP_LIMIT,
        )

    def note_recalled_message(self, event: Any) -> bool:
        return self.recall.note_recalled_message(event)

    def _message_was_recalled(self, scope: str, message_id: str) -> bool:
        return self.recall.message_was_recalled(scope, message_id)

    def _event_message_was_recalled(self, event: Any) -> bool:
        return self._message_was_recalled(
            self._event_session_id(event), self._event_message_id(event)
        )

    def event_was_recalled(self, event: Any, *, log_skip: bool = False) -> bool:
        return self.recall.event_was_recalled(event, log_skip=log_skip)

    def source_was_recalled(
        self, scope: str, *, source_event: Any = None, source_message_id: str = ""
    ) -> bool:
        return self.recall.source_was_recalled(
            scope, source_event=source_event, source_message_id=source_message_id
        )

    def can_send_for_source(
        self, scope: str, *, source_event: Any = None, source_message_id: str = ""
    ) -> bool:
        return self.recall.can_send_for_source(
            scope, source_event=source_event, source_message_id=source_message_id
        )

    async def send_message_if_not_recalled(
        self,
        scope: str,
        chain: Any,
        *,
        source_event: Any = None,
        source_message_id: str = "",
        raise_delivery_errors: bool = False,
        log_outbound: bool = True,
        prefer_scope_send: bool = False,
    ) -> bool:
        return await self.recall.send_message_if_not_recalled(
            scope,
            chain,
            source_event=source_event,
            source_message_id=source_message_id,
            raise_delivery_errors=raise_delivery_errors,
            log_outbound=log_outbound,
            prefer_scope_send=prefer_scope_send,
        )

    def suppress_recalled_event_result(self, event: Any) -> bool:
        return self.recall.suppress_recalled_event_result(event)

    @cached_property
    def share_expression(self) -> ShareExpressionService:
        return ShareExpressionService(
            chat_style_config=lambda: self.config.chat_style,
            voice_config=lambda: self.config.voice_generation,
            share_style=lambda **kwargs: self.get_share_chat_style(**kwargs),
            text_is_structural=self._chat_style_text_is_structural,
            clean_punctuation=self._semantic_segment_clean_punctuation,
            parse_payload=lambda raw: self._semantic_segment_parse_payload(raw),
            text_provider=lambda provider_id: self.get_text_provider(provider_id),
            call_text_model=lambda *args, **kwargs: self.call_text_model(
                *args, **kwargs
            ),
        )

    async def prepare_share_expression(
        self, text: str, *, scene: str = "", requirements: dict | None = None
    ) -> dict:
        return await self.share_expression.prepare_share_expression(
            text, scene=scene, requirements=requirements
        )

    def clean_share_punctuation(self, text: str, *, scene: str = "") -> str:
        return self.share_expression.clean_share_punctuation(text, scene=scene)

    @cached_property
    def share_contact_gate(self):
        from .sharegate import ShareContactGate

        return ShareContactGate(self)

    async def plan_share_voice(self, text: str, target_umo: str = "") -> dict:
        import json
        from dataclasses import asdict

        from .voice.modality import VoiceExpressionPlanner

        context = await self.get_share_context(target_umo)
        facts = {
            key: context[key]
            for key in (
                "current_facts",
                "subject",
                "share_guidance",
                "conversation_timing",
            )
            if key in context
        }
        plan = await VoiceExpressionPlanner(self).plan(
            text,
            scope=target_umo,
            expression_context="这是主动分享；没有用户要求必须语音。"
            + json.dumps(facts, ensure_ascii=False)[:4000],
        )
        return asdict(plan)

    async def search_share_memories(
        self, query: str, target_umo: str = "", *, limit: int = 4
    ) -> list:
        from .sharememory import search_share_memories

        return await search_share_memories(self, query, target_umo, limit)

    async def coordinate_share_contact(self, target_umo: str, **kwargs) -> dict:
        return await self.share_contact_gate.coordinate(target_umo, **kwargs)

    @cached_property
    def external_activity(self) -> ExternalActivityService:
        return ExternalActivityService(
            archive=lambda: self.archive,
            resolve_target=lambda now: self.resolve_injection_target(now),
            note_structured_bot=self.note_structured_bot_message,
            capture_proactive_reply=self.capture_proactive_chat_memory_reply,
            mark_proactive_sent=self._mark_proactive_reply_sent,
            note_proactive_bot=self.note_proactive_bot_reply,
            schedule_memos=self.schedule_memos_selected_items,
        )

    async def record_external_activity_receipt(self, receipt: dict) -> bool:
        recorded = await self.external_activity.record_external_activity_receipt(receipt)
        if recorded:
            return await self.record_bili_browse_delivery(
                str(receipt.get("target_umo") or ""), str(receipt.get("text") or receipt.get("content") or ""),
                at=str(receipt.get("occurred_at") or ""),
            )
        return recorded

    async def record_public_activity(self, receipt: dict) -> bool:
        recorded = await self.external_activity.record_public_activity(receipt)
        if recorded:
            scope = ("qzone_broadcast" if receipt.get("scene") == "qzone_post"
                     else "qzone_thread:" + str(receipt.get("post_id") or receipt.get("event_id") or ""))
            return await self.record_bili_browse_delivery(
                scope, str(receipt.get("text") or receipt.get("content") or ""), at=str(receipt.get("occurred_at") or "")
            )
        return recorded

    async def bili_browse_share_context(self, scope: str) -> str:
        if not callable(getattr(getattr(self, "archive", None), "_run_db", None)):
            return ""
        return await self.bili_browsing.share_prompt(scope)

    async def record_external_activity(
        self,
        target_umo: str,
        content: str,
        *,
        image_description: str = "",
        image_sent: bool = False,
        media_kind: str = "",
        reason: str = "外部主动活动",
        sync_memory: bool = False,
    ) -> bool:
        return await self.external_activity.record_external_activity(
            target_umo,
            content,
            image_description=image_description,
            image_sent=image_sent,
            media_kind=media_kind,
            reason=reason,
            sync_memory=sync_memory,
        )

    def stop_recalled_event_before_history(self, event: Any) -> bool:
        return self.recall.stop_recalled_event_before_history(event)

    @cached_property
    def life_memory(self) -> LifeMemoryService:
        return LifeMemoryService(
            archive=lambda: self.archive,
            group_meta=self._event_group_meta,
            session_id=self._event_session_id,
            message_text=self._event_message_text,
            semantic_search=lambda *args, **kwargs: (
                self.meaning.search_long_term_memories(*args, **kwargs)
            ),
            recent_messages=lambda scope: self._read_recent_context_messages(
                scope, limit=4
            ),
        )

    async def search_life_memory(
        self,
        query: str,
        *,
        event: Any = None,
        mode: str = "search",
        category: str = "",
        limit: int = 5,
    ) -> list[Any]:
        return await self.life_memory.search(
            query, event=event, mode=mode, category=category, limit=limit
        )

    async def life_memory_search(
        self,
        event: Any,
        query: str = "",
        mode: str = "search",
        category: str = "",
        limit: int = 5,
        memory_id: int = 0,
        offset: int = 0,
    ) -> str:
        return await self.life_memory.search_text(
            event,
            query=query,
            mode=mode,
            category=category,
            limit=limit,
            memory_id=memory_id,
            offset=offset,
        )

    async def build_heuristic_memory_context(
        self, event: Any, message: str = "", limit: int = 4
    ) -> str:
        memory = getattr(getattr(self, "config", None), "memory", None)
        if memory is not None:
            if not memory.enabled:
                return ""
            limit = min(limit, memory.max_injection_items)
        return await self.life_memory.build_context(event, message, limit)

    @cached_property
    def living_state(self) -> LivingStateService:
        return LivingStateService(
            archive=lambda: self.archive,
            session_id=self._event_session_id,
            message_id=self._event_message_id,
            event_was_recalled=self.event_was_recalled,
            message_was_recalled=self._message_was_recalled,
            notify_status=lambda reason: self.mark_page_status_changed(reason),
        )

    async def living_context_for_scope(self, scope: str) -> str:
        return await self.living_state.living_context_for_scope(scope)

    async def manage_shared_activity(
        self,
        event: Any,
        *,
        action: str,
        activity_id: str = "",
        detail: str = "",
        progress: str = "",
        evidence: str = "",
        revision: int = 0,
    ) -> str:
        return await self.living_state.manage_shared_activity(
            event,
            action=action,
            activity_id=activity_id,
            detail=detail,
            progress=progress,
            evidence=evidence,
            revision=revision,
        )

    async def manage_life_object(
        self,
        event: Any,
        *,
        action: str = "list",
        object_id: str = "",
        title: str = "",
        state: str = "",
        location: str = "",
        progress: str = "",
        content: str = "",
        evidence: str = "",
        revision: int = 0,
        owner: str = "",
        quantity: float = -1,
        unit: str = "",
        available_at: str = "",
    ) -> str:
        return await self.living_state.manage_life_object(
            event,
            action=action,
            object_id=object_id,
            title=title,
            state=state,
            location=location,
            progress=progress,
            content=content,
            evidence=evidence,
            revision=revision,
            owner=owner,
            quantity=quantity,
            unit=unit,
            available_at=available_at,
        )

    async def correct_memory_from_event(
        self, event: Any, *, memory_id: int, correction: str, evidence: str
    ) -> str:
        return await self.living_state.correct_memory_from_event(
            event, memory_id=memory_id, correction=correction, evidence=evidence
        )

    async def note_memory_corrections(self, corrections: list, batch: dict) -> None:
        await self.living_state.note_memory_corrections(corrections, batch)

    @cached_property
    def meaning(self) -> MeaningService:
        return MeaningService(
            embedding_providers=lambda: self.context.get_all_embedding_providers(),
            memory_config=lambda: self.config.memory,
            archive=lambda: self.archive,
            text_provider=lambda provider_id: self.get_text_provider(provider_id),
            call_text_model=lambda *args, **kwargs: self.call_text_model(
                *args, **kwargs
            ),
            close_text_session=lambda session: self.close_text_session(session),
            schedule_background=lambda *args, **kwargs: self._schedule_background_task(
                *args, **kwargs
            ),
        )

    async def rank_semantic_groups(
        self,
        query: str,
        groups: dict[str, list[tuple[str, str, Any]]],
        limits: dict[str, int],
        *,
        optional_groups: set[str] | None = None,
    ) -> dict[str, list[Any]]:
        return await self.meaning.rank_semantic_groups(query, groups, limits, optional_groups=optional_groups)

    async def rank_embedding_groups(
        self,
        query: str,
        groups: dict[str, list[tuple[str, str, Any]]],
        limits: dict[str, int],
    ) -> dict[str, list[Any]]:
        return await self.meaning.rank_embedding_groups(query, groups, limits)

    @cached_property
    def conversation_timing(self) -> ConversationTimingService:
        return ConversationTimingService(
            archive=lambda: getattr(self, "archive", None),
            is_self_message=self._proactive_is_self_message,
            session_id=self._event_session_id,
            is_group=lambda event: bool(self._safe_event_call(event, "get_group_id")),
        )

    def note_conversation_incoming(self, event: Any) -> None:
        self.conversation_timing.note_conversation_incoming(event)

    async def record_conversation_arrival(self, event: Any) -> None:
        await self.conversation_timing.record_conversation_arrival(event)

    def clear_conversation_timing(self) -> None:
        self.conversation_timing.clear_conversation_timing()

    def note_conversation_sent(
        self, scope: str, sent_at: float, source_event: Any = None
    ) -> None:
        self.conversation_timing.note_conversation_sent(scope, sent_at, source_event)

    async def record_conversation_delivery(
        self, scope: str, sent_at: float, source_event: Any = None
    ) -> None:
        await self.conversation_timing.record_conversation_delivery(
            scope, sent_at, source_event
        )

    async def build_conversation_timing_context(self, event: Any) -> str:
        return await self.conversation_timing.build_conversation_timing_context(event)

    @cached_property
    def outbound_log(self) -> OutboundLogService:
        return OutboundLogService(
            context=getattr(self, "context", None),
            timing=self.conversation_timing,
            delivered=lambda scope, text: self.record_bili_browse_delivery(scope, text),
            schedule=lambda coro, **kwargs: self._schedule_background_task(
                coro, **kwargs
            ),
        )

    async def prepare_outbound_bot_name(
        self, *, scope: str = "", source_event: Any = None
    ) -> str:
        return await self.outbound_log.prepare_outbound_bot_name(
            scope=scope, source_event=source_event
        )

    def log_outbound_message(
        self,
        chain: Any,
        *,
        scope: str = "",
        source_event: Any = None,
        source: str = "",
        bot_name: str = "",
        sent_at: float | None = None,
        persist_receipt: bool = True,
    ) -> bool:
        return self.outbound_log.log_outbound_message(
            chain,
            scope=scope,
            source_event=source_event,
            source=source,
            bot_name=bot_name,
            sent_at=sent_at,
            persist_receipt=persist_receipt,
        )

    async def log_outbound_message_async(self, chain: Any, **kwargs: Any) -> bool:
        return await self.outbound_log.log_outbound_message_async(chain, **kwargs)

    async def log_outbound_result_async(
        self, event: Any, *, source: str = "reply"
    ) -> bool:
        return await self.outbound_log.log_outbound_result_async(event, source=source)

    def log_outbound_result(
        self, event: Any, *, source: str = "reply", bot_name: str = ""
    ) -> bool:
        return self.outbound_log.log_outbound_result(
            event, source=source, bot_name=bot_name
        )

    def note_chat_model_usage(
        self,
        response: Any,
        *,
        elapsed_ms: float | None = None,
        failed: bool = False,
        model_identity: str = "",
        prefix_version: str = "",
    ) -> None:
        if getattr(response, "is_chunk", False):
            return
        raw = getattr(response, "raw_completion", None)
        model = (
            raw.get("model", "") if isinstance(raw, dict) else getattr(raw, "model", "")
        )
        self._model_cache_meter().record(
            response,
            kind="chat",
            model=model_identity or str(model or ""),
            elapsed_ms=elapsed_ms,
            failed=failed,
            prefix_version=prefix_version,
        )

    async def capture_chat_model_identity(self, event: Any) -> str:
        """读取当前请求的配置身份，不等待模型或平台连接。"""
        context = getattr(self, "context", None)
        if context is None:
            return ""
        try:
            async with asyncio.timeout(0.1):
                getter = getattr(event, "get_extra", None)
                selected = getter("selected_provider") if callable(getter) else None
                if selected:
                    provider = context.get_provider_by_id(selected)
                else:
                    provider = await context.get_using_provider_async(
                        umo=getattr(event, "unified_msg_origin", None)
                    )
                return model_request_identity(provider)
        except (TimeoutError, AttributeError, TypeError, ValueError, KeyError):
            return ""

    def model_cache_status(self) -> dict[str, Any]:
        return self._model_cache_meter().snapshot()

    async def get_text_provider(self, provider_id: str = ""):
        gateway = getattr(self, "model_gateway", None) or ModelGateway(self.composer)
        return await gateway.provider(provider_id)

    async def create_voice_call_invite(
        self,
        event: Any,
        *,
        greeting: str = "",
    ) -> str:
        manager = getattr(self, "voice_call", None)
        if manager is None:
            raise RuntimeError("实时语音通话服务尚未初始化")
        return await manager.create_invite(
            event,
            greeting=greeting,
        )

    async def create_video_call_invite(
        self,
        event: Any,
        *,
        greeting: str = "",
    ) -> str:
        manager = getattr(getattr(self, "voice_call", None), "rtc", None)
        if manager is None:
            raise RuntimeError("AI 音视频通话服务尚未初始化")
        return await manager.create_invite(
            event,
            greeting=greeting,
            transport="rtc",
        )

    def get_text_provider_candidates(self, provider_id: str = ""):
        gateway = getattr(self, "model_gateway", None) or ModelGateway(self.composer)
        return gateway.provider_candidates(provider_id)

    def note_runtime_scope_activity(self, event: Any) -> None:
        self.scope_state.note_event(event)

    async def call_text_model(
        self,
        provider,
        prompt: str,
        session_id: str,
        *,
        empty_retries: int = 1,
        primary_provider_id: str = "",
        propagate_non_retryable: bool = False,
        timeout_seconds: float | None = None,
        system_prompt: str | None = None,
    ) -> str:
        gateway = getattr(self, "model_gateway", None) or ModelGateway(self.composer)
        return await gateway.call(
            provider,
            prompt,
            session_id,
            ModelCallOptions(
                empty_retries=empty_retries,
                primary_provider_id=primary_provider_id,
                propagate_non_retryable=propagate_non_retryable,
                timeout_seconds=timeout_seconds,
                system_prompt=system_prompt,
            ),
        )

    async def close_text_session(self, session_id: str) -> None:
        gateway = getattr(self, "model_gateway", None) or ModelGateway(self.composer)
        await gateway.close(session_id)

    async def get_persona_text(self, scope: str = ""):
        gateway = getattr(self, "model_gateway", None) or ModelGateway(self.composer)
        return await gateway.persona(scope)

    @property
    def page_status_version(self) -> int:
        return self._page_status_version

    async def mark_page_status_changed(self, reason: str = "") -> int:
        cache = getattr(self, "_injection_snapshot_cache", None)
        if isinstance(cache, dict):
            cache.clear()
        async with self._page_status_changed:
            self._page_status_version += 1
            version = self._page_status_version
            self._page_status_changed.notify_all()
        if reason:
            logger.debug(
                f"{LOG_PREFIX} 面板状态已更新：版本：{version}，原因：{page_status_reason_label(reason)}"
            )
        return version

    async def wait_page_status_changed(
        self, since: int = 0, timeout: float = 25.0
    ) -> int:
        since = max(int(since or 0), 0)
        timeout = max(1.0, min(float(timeout or 25.0), 55.0))
        async with self._page_status_changed:
            if self._page_status_version > since:
                return self._page_status_version
            try:
                await asyncio.wait_for(
                    self._page_status_changed.wait_for(
                        lambda: self._page_status_version > since
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                pass
            return self._page_status_version

    _MEMOS_CONTEXT_CACHE_TTL_SECONDS = MemosComponent._MEMOS_CONTEXT_CACHE_TTL_SECONDS

    _MEMOS_CONTEXT_CACHE_LIMIT = MemosComponent._MEMOS_CONTEXT_CACHE_LIMIT

    _create_memos_service = ComponentCall(
        "_part_memos_component", MemosComponent, "_create_memos_service"
    )

    close_memos_service = ComponentCall(
        "_part_memos_component", MemosComponent, "close_memos_service"
    )

    _memos_service = ComponentCall(
        "_part_memos_component", MemosComponent, "_memos_service"
    )

    build_memos_hidden_context = ComponentCall(
        "_part_memos_component", MemosComponent, "build_memos_hidden_context"
    )

    _memos_context_cache_key = staticmethod(MemosComponent._memos_context_cache_key)

    sync_memos_selected_memory = ComponentCall(
        "_part_memos_component", MemosComponent, "sync_memos_selected_memory"
    )

    schedule_memos_selected_memory = ComponentCall(
        "_part_memos_component", MemosComponent, "schedule_memos_selected_memory"
    )

    _memos_items_from_payload = ComponentCall(
        "_part_memos_component", MemosComponent, "_memos_items_from_payload"
    )

    sync_memos_selected_items = ComponentCall(
        "_part_memos_component", MemosComponent, "sync_memos_selected_items"
    )

    schedule_memos_selected_items = ComponentCall(
        "_part_memos_component", MemosComponent, "schedule_memos_selected_items"
    )

    sync_memos_memo = ComponentCall(
        "_part_memos_component", MemosComponent, "sync_memos_memo"
    )

    schedule_memos_memo = ComponentCall(
        "_part_memos_component", MemosComponent, "schedule_memos_memo"
    )

    sync_memos_correction = ComponentCall(
        "_part_memos_component", MemosComponent, "sync_memos_correction"
    )

    schedule_memos_correction = ComponentCall(
        "_part_memos_component", MemosComponent, "schedule_memos_correction"
    )

    _BATCH_READABLE_FIELDS = ChatMemoryBatchComponent._BATCH_READABLE_FIELDS

    _BATCH_REFERENCE_SEPARATORS = ChatMemoryBatchComponent._BATCH_REFERENCE_SEPARATORS

    _validate_chat_memory_payload = staticmethod(
        ChatMemoryBatchComponent._validate_chat_memory_payload
    )

    _init_chat_memory_batcher = ComponentCall(
        "_part_chat_memory_batch_component",
        ChatMemoryBatchComponent,
        "_init_chat_memory_batcher",
    )

    _start_chat_memory_batcher = ComponentCall(
        "_part_chat_memory_batch_component",
        ChatMemoryBatchComponent,
        "_start_chat_memory_batcher",
    )

    _shutdown_chat_memory_batcher = ComponentCall(
        "_part_chat_memory_batch_component",
        ChatMemoryBatchComponent,
        "_shutdown_chat_memory_batcher",
    )

    capture_chat_memory_message = ComponentCall(
        "_part_chat_memory_batch_component",
        ChatMemoryBatchComponent,
        "capture_chat_memory_message",
    )

    capture_chat_memory_bot_reply = ComponentCall(
        "_part_chat_memory_batch_component",
        ChatMemoryBatchComponent,
        "capture_chat_memory_bot_reply",
    )

    capture_proactive_chat_memory_reply = ComponentCall(
        "_part_chat_memory_batch_component",
        ChatMemoryBatchComponent,
        "capture_proactive_chat_memory_reply",
    )

    _chat_memory_elapsed_seconds = staticmethod(
        ChatMemoryBatchComponent._chat_memory_elapsed_seconds
    )

    process_due_chat_memory_batches = ComponentCall(
        "_part_chat_memory_batch_component",
        ChatMemoryBatchComponent,
        "process_due_chat_memory_batches",
    )

    _split_batch_reference_segments = classmethod(
        ChatMemoryBatchComponent._split_batch_reference_segments.__func__
    )

    _batch_reference_ids = staticmethod(ChatMemoryBatchComponent._batch_reference_ids)

    _sanitize_batch_readable_text = classmethod(
        ChatMemoryBatchComponent._sanitize_batch_readable_text.__func__
    )

    _sanitize_batch_readable_fields = classmethod(
        ChatMemoryBatchComponent._sanitize_batch_readable_fields.__func__
    )

    _chat_execution_context = ComponentCall(
        "_part_chat_execution_component",
        ChatExecutionComponent,
        "_chat_execution_context",
    )

    _save_batch_execution_updates = ComponentCall(
        "_part_chat_execution_component",
        ChatExecutionComponent,
        "_save_batch_execution_updates",
    )

    _batch_expression_scope = staticmethod(
        ExpressionLearningComponent._batch_expression_scope
    )

    _batch_expression_context = ComponentCall(
        "_part_expression_learning_component",
        ExpressionLearningComponent,
        "_batch_expression_context",
    )

    _normalize_batch_expression_learning = ComponentCall(
        "_part_expression_learning_component",
        ExpressionLearningComponent,
        "_normalize_batch_expression_learning",
    )

    _save_batch_expression_learning = ComponentCall(
        "_part_expression_learning_component",
        ExpressionLearningComponent,
        "_save_batch_expression_learning",
    )

    _event_sources = staticmethod(CaptureEventComponent._event_sources)

    _event_session_id = staticmethod(CaptureEventComponent._event_session_id)

    _safe_event_call = staticmethod(CaptureEventComponent._safe_event_call)

    _safe_event_attr = staticmethod(CaptureEventComponent._safe_event_attr)

    _event_is_group_message = classmethod(
        CaptureEventComponent._event_is_group_message.__func__
    )

    _event_platform_user = classmethod(
        CaptureEventComponent._event_platform_user.__func__
    )

    _event_group_meta = classmethod(CaptureEventComponent._event_group_meta.__func__)

    _event_message_id = classmethod(CaptureEventComponent._event_message_id.__func__)

    _event_is_platform_directed = classmethod(
        CaptureEventComponent._event_is_platform_directed.__func__
    )

    _event_ascii_token_char = staticmethod(
        CaptureEventComponent._event_ascii_token_char
    )

    _event_text_contains_bot_alias = classmethod(
        CaptureEventComponent._event_text_contains_bot_alias.__func__
    )

    _event_mentions_bot_alias = ComponentCall(
        "_part_capture_event_component",
        CaptureEventComponent,
        "_event_mentions_bot_alias",
    )

    _event_is_directed = ComponentCall(
        "_part_capture_event_component", CaptureEventComponent, "_event_is_directed"
    )

    _event_has_command_handler = classmethod(
        CaptureEventComponent._event_has_command_handler.__func__
    )

    _event_has_quote = classmethod(CaptureEventComponent._event_has_quote.__func__)

    _event_message_items = classmethod(
        CaptureEventComponent._event_message_items.__func__
    )

    _event_message_component_facts = classmethod(
        CaptureEventComponent._event_message_component_facts.__func__
    )

    _event_component_kind = staticmethod(CaptureEventComponent._event_component_kind)

    _completion_text = staticmethod(CaptureEventComponent._completion_text)

    _message_media_payload = classmethod(
        CaptureEventComponent._message_media_payload.__func__
    )

    _media_fingerprint = ComponentCall(
        "_part_capture_event_component", CaptureEventComponent, "_media_fingerprint"
    )

    _event_quote_context = classmethod(
        CaptureEventComponent._event_quote_context.__func__
    )

    _event_profile_id = ComponentCall(
        "_part_capture_event_component", CaptureEventComponent, "_event_profile_id"
    )

    _voice_allowed_for_scope = ComponentCall(
        "_part_capture_event_component",
        CaptureEventComponent,
        "_voice_allowed_for_scope",
    )

    _relationship_contact_payload = ComponentCall(
        "_part_capture_payload_component",
        CapturePayloadComponent,
        "_relationship_contact_payload",
    )

    _list_payload = staticmethod(CapturePayloadComponent._list_payload)

    _str_payload = staticmethod(CapturePayloadComponent._str_payload)

    _contains_chinese_text = staticmethod(
        CapturePayloadComponent._contains_chinese_text
    )

    _chinese_text_payload = classmethod(
        CapturePayloadComponent._chinese_text_payload.__func__
    )

    _int_payload = staticmethod(CapturePayloadComponent._int_payload)

    _float_payload = staticmethod(CapturePayloadComponent._float_payload)

    _bool_payload = staticmethod(CapturePayloadComponent._bool_payload)

    _score_payload = classmethod(CapturePayloadComponent._score_payload.__func__)

    _compact_log = staticmethod(CapturePayloadComponent._compact_log)

    _PERSONA_HINT_CACHE_LIMIT = CapturePersonaComponent._PERSONA_HINT_CACHE_LIMIT

    _current_persona_text = ComponentCall(
        "_part_capture_persona_component",
        CapturePersonaComponent,
        "_current_persona_text",
    )

    _current_role_label = ComponentCall(
        "_part_capture_persona_component",
        CapturePersonaComponent,
        "_current_role_label",
    )

    _persona_hint_cache_text = staticmethod(
        CapturePersonaComponent._persona_hint_cache_text
    )

    _extract_speaker_persona_hint = ComponentCall(
        "_part_capture_persona_component",
        CapturePersonaComponent,
        "_extract_speaker_persona_hint",
    )

    _MEMORY_PAYLOAD_CALIBRATION_KEYS = PacketComponent._MEMORY_PAYLOAD_CALIBRATION_KEYS

    _calibrate_chat_memory_payload = ComponentCall(
        "_part_packet_component", PacketComponent, "_calibrate_chat_memory_payload"
    )

    _MEMORY_TARGET_CALIBRATION_KEYS = TargetComponent._MEMORY_TARGET_CALIBRATION_KEYS

    _calibrate_memory_target_payload = ComponentCall(
        "_part_target_component", TargetComponent, "_calibrate_memory_target_payload"
    )

    _calibrate_relationship_profile = ComponentCall(
        "_part_profile_component", ProfileComponent, "_calibrate_relationship_profile"
    )

    _MEANINGFUL_VISIBILITY_LEVELS = AwarenessComponent._MEANINGFUL_VISIBILITY_LEVELS

    _MEANINGFUL_DECISION_ACTIONS = AwarenessComponent._MEANINGFUL_DECISION_ACTIONS

    _event_context_meta = ComponentCall(
        "_part_awareness_component", AwarenessComponent, "_event_context_meta"
    )

    _save_memory_awareness_records = ComponentCall(
        "_part_awareness_component",
        AwarenessComponent,
        "_save_memory_awareness_records",
    )

    _append_memory_decision_log = ComponentCall(
        "_part_awareness_component", AwarenessComponent, "_append_memory_decision_log"
    )

    _save_long_term_memory = ComponentCall(
        "_part_imprint_deposit_component",
        ImprintDepositComponent,
        "_save_long_term_memory",
    )

    _save_experience_evidence = ComponentCall(
        "_part_imprint_deposit_component",
        ImprintDepositComponent,
        "_save_experience_evidence",
    )

    _save_experience_payload = ComponentCall(
        "_part_imprint_deposit_component",
        ImprintDepositComponent,
        "_save_experience_payload",
    )

    _dict_payloads = ComponentCall(
        "_part_imprint_deposit_component", ImprintDepositComponent, "_dict_payloads"
    )

    _save_expression_profile_payloads = ComponentCall(
        "_part_imprint_deposit_component",
        ImprintDepositComponent,
        "_save_expression_profile_payloads",
    )

    _life_adjustment_default_expiry = staticmethod(
        ImprintDepositComponent._life_adjustment_default_expiry
    )

    _apply_memory_correction = ComponentCall(
        "_part_imprint_portrait_component",
        ImprintPortraitComponent,
        "_apply_memory_correction",
    )

    _save_memory_targets = ComponentCall(
        "_part_imprint_portrait_component",
        ImprintPortraitComponent,
        "_save_memory_targets",
    )

    _get_memory_provider = ComponentCall(
        "_part_imprint_harvest_component",
        ImprintHarvestComponent,
        "_get_memory_provider",
    )

    _save_chat_memory_preferences = ComponentCall(
        "_part_imprint_harvest_component",
        ImprintHarvestComponent,
        "_save_chat_memory_preferences",
    )

    _schedule_chat_memory_memos = ComponentCall(
        "_part_imprint_harvest_component",
        ImprintHarvestComponent,
        "_schedule_chat_memory_memos",
    )

    remember_interaction = ComponentCall(
        "_part_imprint_harvest_component",
        ImprintHarvestComponent,
        "remember_interaction",
    )

    EMOJI_ASSET_DIR_NAME = EmojiBaseComponent.EMOJI_ASSET_DIR_NAME

    EMOJI_ASSET_DEFAULT_MAX_BYTES = EmojiBaseComponent.EMOJI_ASSET_DEFAULT_MAX_BYTES

    EMOJI_ASSET_DEFAULT_MAX_READY = EmojiBaseComponent.EMOJI_ASSET_DEFAULT_MAX_READY

    EMOJI_ASSET_DEFAULT_ORPHAN_GRACE_SECONDS = (
        EmojiBaseComponent.EMOJI_ASSET_DEFAULT_ORPHAN_GRACE_SECONDS
    )

    VISUAL_CONTEXT_SUMMARY_MAX_CHARS = (
        EmojiBaseComponent.VISUAL_CONTEXT_SUMMARY_MAX_CHARS
    )

    EMOJI_ASSET_SUFFIXES = EmojiBaseComponent.EMOJI_ASSET_SUFFIXES

    EMOJI_ASSET_COLLECTIBLE_KINDS = EmojiBaseComponent.EMOJI_ASSET_COLLECTIBLE_KINDS

    EMOJI_ASSET_REVIEWABLE_KINDS = EmojiBaseComponent.EMOJI_ASSET_REVIEWABLE_KINDS

    EMOJI_ASSET_REJECTED_STATUSES = EmojiBaseComponent.EMOJI_ASSET_REJECTED_STATUSES

    EMOJI_ASSET_MIN_PLAIN_IMAGE_CONFIDENCE = (
        EmojiBaseComponent.EMOJI_ASSET_MIN_PLAIN_IMAGE_CONFIDENCE
    )

    EMOJI_ASSET_ALLOWED_REACTION_TYPES = (
        EmojiBaseComponent.EMOJI_ASSET_ALLOWED_REACTION_TYPES
    )

    EMOJI_ASSET_INACTIVE_STATUSES = EmojiBaseComponent.EMOJI_ASSET_INACTIVE_STATUSES

    _emoji_auto_collect_enabled = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_emoji_auto_collect_enabled"
    )

    _emoji_max_bytes = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_emoji_max_bytes"
    )

    _emoji_max_ready_count = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_emoji_max_ready_count"
    )

    _emoji_replace_when_full = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_emoji_replace_when_full"
    )

    _emoji_send_candidate_limit = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_emoji_send_candidate_limit"
    )

    _emoji_review_batch_size = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_emoji_review_batch_size"
    )

    _emoji_inactive_record_keep_days = ComponentCall(
        "_part_emoji_base_component",
        EmojiBaseComponent,
        "_emoji_inactive_record_keep_days",
    )

    _emoji_orphan_grace_seconds = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_emoji_orphan_grace_seconds"
    )

    _emoji_asset_size_allowed = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_emoji_asset_size_allowed"
    )

    _emoji_can_accept_ready_asset = ComponentCall(
        "_part_emoji_base_component",
        EmojiBaseComponent,
        "_emoji_can_accept_ready_asset",
    )

    _emoji_asset_cache_dir = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_emoji_asset_cache_dir"
    )

    _emoji_asset_source = classmethod(EmojiBaseComponent._emoji_asset_source.__func__)

    _emoji_asset_component_kind = staticmethod(
        EmojiBaseComponent._emoji_asset_component_kind
    )

    _emoji_asset_meta_values = classmethod(
        EmojiBaseComponent._emoji_asset_meta_values.__func__
    )

    _emoji_asset_is_image_item = classmethod(
        EmojiBaseComponent._emoji_asset_is_image_item.__func__
    )

    _emoji_asset_is_collectible_item = classmethod(
        EmojiBaseComponent._emoji_asset_is_collectible_item.__func__
    )

    _emoji_asset_source_kind = classmethod(
        EmojiBaseComponent._emoji_asset_source_kind.__func__
    )

    _emoji_asset_type_from_item = classmethod(
        EmojiBaseComponent._emoji_asset_type_from_item.__func__
    )

    _visual_context_summary_cache = ComponentCall(
        "_part_emoji_base_component",
        EmojiBaseComponent,
        "_visual_context_summary_cache",
    )

    _remember_visual_context_summary = ComponentCall(
        "_part_emoji_base_component",
        EmojiBaseComponent,
        "_remember_visual_context_summary",
    )

    _emoji_asset_suffix = classmethod(EmojiBaseComponent._emoji_asset_suffix.__func__)

    _emoji_asset_filename = staticmethod(EmojiBaseComponent._emoji_asset_filename)

    _emoji_asset_is_remote = staticmethod(EmojiBaseComponent._emoji_asset_is_remote)

    _resolve_cached_emoji_path = ComponentCall(
        "_part_emoji_base_component", EmojiBaseComponent, "_resolve_cached_emoji_path"
    )

    _apply_visual_context_summary = ComponentCall(
        "_part_emoji_base_component",
        EmojiBaseComponent,
        "_apply_visual_context_summary",
    )

    _apply_visual_context_summary_text = ComponentCall(
        "_part_emoji_base_component",
        EmojiBaseComponent,
        "_apply_visual_context_summary_text",
    )

    _visual_context_summary_from_payload = ComponentCall(
        "_part_emoji_base_component",
        EmojiBaseComponent,
        "_visual_context_summary_from_payload",
    )

    _compact_visual_context_summary = classmethod(
        EmojiBaseComponent._compact_visual_context_summary.__func__
    )

    _cache_emoji_asset_file = ComponentCall(
        "_part_emoji_cache_component",
        _part_emoji_cache_component.EmojiCacheComponent,
        "_cache_emoji_asset_file",
    )

    _cache_emoji_asset_path = ComponentCall(
        "_part_emoji_cache_component",
        _part_emoji_cache_component.EmojiCacheComponent,
        "_cache_emoji_asset_path",
    )

    cleanup_emoji_asset_cache = ComponentCall(
        "_part_emoji_cache_component",
        _part_emoji_cache_component.EmojiCacheComponent,
        "cleanup_emoji_asset_cache",
    )

    _copy_emoji_asset = ComponentCall(
        "_part_emoji_cache_component",
        _part_emoji_cache_component.EmojiCacheComponent,
        "_copy_emoji_asset",
    )

    _emoji_asset_temporary_path = staticmethod(
        _part_emoji_cache_component.EmojiCacheComponent._emoji_asset_temporary_path
    )

    @staticmethod
    async def _remove_emoji_asset_temporary_path(
        path: _part_emoji_cache_component.Path | None,
    ) -> None:
        return await _part_emoji_cache_component.EmojiCacheComponent._remove_emoji_asset_temporary_path(
            path
        )

    _write_emoji_asset_bytes = classmethod(
        _part_emoji_cache_component.EmojiCacheComponent._write_emoji_asset_bytes.__func__
    )

    _local_path_from_file_uri = staticmethod(
        _part_emoji_cache_component.EmojiCacheComponent._local_path_from_file_uri
    )

    _decode_emoji_asset_data = ComponentCall(
        "_part_emoji_cache_component",
        _part_emoji_cache_component.EmojiCacheComponent,
        "_decode_emoji_asset_data",
    )

    _download_emoji_asset = ComponentCall(
        "_part_emoji_cache_component",
        _part_emoji_cache_component.EmojiCacheComponent,
        "_download_emoji_asset",
    )

    maintain_emoji_assets = ComponentCall(
        "_part_emoji_sweep_component", EmojiSweepComponent, "maintain_emoji_assets"
    )

    _prune_extra_ready_emoji_assets = ComponentCall(
        "_part_emoji_sweep_component",
        EmojiSweepComponent,
        "_prune_extra_ready_emoji_assets",
    )

    _emoji_asset_is_inline_source = staticmethod(
        _part_emoji_vision_component.EmojiVisionComponent._emoji_asset_is_inline_source
    )

    _emoji_asset_readable_source = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_emoji_asset_readable_source",
    )

    @staticmethod
    async def _call_vision_provider(
        provider: _part_emoji_vision_component.Any,
        prompt: str,
        image: str,
        session_id: str,
    ) -> _part_emoji_vision_component.Any:
        return await _part_emoji_vision_component.EmojiVisionComponent._call_vision_provider(
            provider, prompt, image, session_id
        )

    _call_vision_payload = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_call_vision_payload",
    )

    _call_prepared_vision_payload = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_call_prepared_vision_payload",
    )

    _describe_emoji_asset_with_vision = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_describe_emoji_asset_with_vision",
    )

    _classify_plain_image_emoji_candidate = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_classify_plain_image_emoji_candidate",
    )
    _plain_image_emoji_review_cached = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_plain_image_emoji_review_cached",
    )
    _visual_context_cache_key = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_visual_context_cache_key",
    )
    _close_visual_context_flights = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_close_visual_context_flights",
    )
    _save_plain_image_emoji_candidate = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_save_plain_image_emoji_candidate",
    )

    _emoji_asset_cache_candidates = staticmethod(
        _part_emoji_vision_component.EmojiVisionComponent._emoji_asset_cache_candidates
    )

    _emoji_asset_prepare_vision_source = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_emoji_asset_prepare_vision_source",
    )

    _emoji_asset_confidence = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_emoji_asset_confidence",
    )

    _emoji_asset_review_status = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_emoji_asset_review_status",
    )

    _emoji_asset_rejected_reason = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_emoji_asset_rejected_reason",
    )

    _describe_visual_context_with_vision = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_describe_visual_context_with_vision",
    )

    _mark_emoji_asset_failed = ComponentCall(
        "_part_emoji_vision_component",
        _part_emoji_vision_component.EmojiVisionComponent,
        "_mark_emoji_asset_failed",
    )

    _PREPARED_VISUAL_MEDIA_ATTR = EmojiGatherComponent._PREPARED_VISUAL_MEDIA_ATTR

    _VISUAL_CONTEXT_FUTURE_ATTR = EmojiGatherComponent._VISUAL_CONTEXT_FUTURE_ATTR

    _GIF_CONTEXT_FUTURE_ATTR = EmojiGatherComponent._GIF_CONTEXT_FUTURE_ATTR

    _VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS = (
        EmojiGatherComponent._VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS
    )

    _GIF_VISION_BRIDGE_TIMEOUT_SECONDS = (
        EmojiGatherComponent._GIF_VISION_BRIDGE_TIMEOUT_SECONDS
    )

    _visual_media_sources = staticmethod(EmojiGatherComponent._visual_media_sources)

    _visual_media_has_durable_source = staticmethod(
        EmojiGatherComponent._visual_media_has_durable_source
    )

    prepare_visual_media_from_event = ComponentCall(
        "_part_emoji_gather_component",
        EmojiGatherComponent,
        "prepare_visual_media_from_event",
    )

    _append_gif_visual_summary = staticmethod(
        EmojiGatherComponent._append_gif_visual_summary
    )

    bridge_animated_visual_for_llm_request = ComponentCall(
        "_part_emoji_gather_component",
        EmojiGatherComponent,
        "bridge_animated_visual_for_llm_request",
    )

    maybe_collect_emoji_assets_from_event = ComponentCall(
        "_part_emoji_gather_component",
        EmojiGatherComponent,
        "maybe_collect_emoji_assets_from_event",
    )

    schedule_visual_context_from_event = ComponentCall(
        "_part_emoji_gather_component",
        EmojiGatherComponent,
        "schedule_visual_context_from_event",
    )

    _STRUCTURED_CONTEXT_LIMIT = StructuredBaseComponent._STRUCTURED_CONTEXT_LIMIT

    _STRUCTURED_EVENT_SEQUENCE_ATTR = (
        StructuredBaseComponent._STRUCTURED_EVENT_SEQUENCE_ATTR
    )

    _structured_text = staticmethod(StructuredBaseComponent._structured_text)

    _structured_first_text = staticmethod(
        StructuredBaseComponent._structured_first_text
    )

    _structured_component_kind = staticmethod(
        StructuredBaseComponent._structured_component_kind
    )

    _structured_component_data = staticmethod(
        StructuredBaseComponent._structured_component_data
    )

    _structured_sender_card_from_event = ComponentCall(
        "_part_structured_base_component",
        StructuredBaseComponent,
        "_structured_sender_card_from_event",
    )

    _structured_self_id = ComponentCall(
        "_part_structured_base_component",
        StructuredBaseComponent,
        "_structured_self_id",
    )

    _structured_media_outline = staticmethod(
        StructuredBaseComponent._structured_media_outline
    )

    _structured_content_with_media = classmethod(
        StructuredBaseComponent._structured_content_with_media.__func__
    )

    remove_recalled_structured_message = ComponentCall(
        "_part_structured_dock_component",
        StructuredDockComponent,
        "remove_recalled_structured_message",
    )

    _structured_scope_messages = ComponentCall(
        "_part_structured_dock_component",
        StructuredDockComponent,
        "_structured_scope_messages",
    )

    _structured_upsert_message = ComponentCall(
        "_part_structured_dock_component",
        StructuredDockComponent,
        "_structured_upsert_message",
    )

    _structured_extract_at_targets = ComponentCall(
        "_part_structured_route_component",
        StructuredRouteComponent,
        "_structured_extract_at_targets",
    )

    _structured_extract_reply = ComponentCall(
        "_part_structured_route_component",
        StructuredRouteComponent,
        "_structured_extract_reply",
    )

    _structured_find_message = ComponentCall(
        "_part_structured_route_component",
        StructuredRouteComponent,
        "_structured_find_message",
    )

    _structured_message_for_event = ComponentCall(
        "_part_structured_route_component",
        StructuredRouteComponent,
        "_structured_message_for_event",
    )

    _structured_talking_to_bot = staticmethod(
        StructuredRouteComponent._structured_talking_to_bot
    )

    _structured_talking_to_other_user = staticmethod(
        StructuredRouteComponent._structured_talking_to_other_user
    )

    _structured_resolve_talking_to = ComponentCall(
        "_part_structured_route_component",
        StructuredRouteComponent,
        "_structured_resolve_talking_to",
    )

    note_structured_incoming_message = ComponentCall(
        "_part_structured_forge_component",
        StructuredForgeComponent,
        "note_structured_incoming_message",
    )

    note_structured_bot_message = ComponentCall(
        "_part_structured_forge_component",
        StructuredForgeComponent,
        "note_structured_bot_message",
    )

    update_structured_message_visual_summary = ComponentCall(
        "_part_structured_forge_component",
        StructuredForgeComponent,
        "update_structured_message_visual_summary",
    )

    update_structured_message_video_summary = ComponentCall(
        "_part_structured_forge_component",
        StructuredForgeComponent,
        "update_structured_message_video_summary",
    )

    _structured_result_text = ComponentCall(
        "_part_structured_glance_component",
        StructuredGlanceComponent,
        "_structured_result_text",
    )

    mark_structured_pending_bot_text = ComponentCall(
        "_part_structured_glance_component",
        StructuredGlanceComponent,
        "mark_structured_pending_bot_text",
    )

    note_structured_sent_result = ComponentCall(
        "_part_structured_glance_component",
        StructuredGlanceComponent,
        "note_structured_sent_result",
    )

    format_structured_message_context = ComponentCall(
        "_part_structured_glance_component",
        StructuredGlanceComponent,
        "format_structured_message_context",
    )

    structured_recent_history_messages = ComponentCall(
        "_part_structured_glance_component",
        StructuredGlanceComponent,
        "structured_recent_history_messages",
    )

    _init_sight = ComponentCall(
        "_part_sight_component", _part_sight_component.SightComponent, "_init_sight"
    )

    _sight_vault_for_runtime = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_vault_for_runtime",
    )

    _sight_cache_ttl_seconds = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_cache_ttl_seconds",
    )

    _sight_cache_max_items = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_cache_max_items",
    )

    _sight_reader_for_runtime = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_reader_for_runtime",
    )

    _sight_reader_signature = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_reader_signature",
    )

    _sight_brief_for_runtime = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_brief_for_runtime",
    )

    _sight_flight_for_runtime = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_flight_for_runtime",
    )

    _sight_prepare_flight_for_runtime = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_prepare_flight_for_runtime",
    )

    _sight_note_for_runtime = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_note_for_runtime",
    )

    _compose_sight_note_with_timeout = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_compose_sight_note_with_timeout",
    )

    remove_recalled_sight_context = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "remove_recalled_sight_context",
    )

    _sight_cache_dir = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_cache_dir",
    )

    _sight_prepare_cache_path = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_prepare_cache_path",
    )

    _save_sight_prepare_cache = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_save_sight_prepare_cache",
    )

    _load_sight_prepare_cache = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_load_sight_prepare_cache",
    )

    _clear_sight_prepare_cache = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_clear_sight_prepare_cache",
    )

    _bili_cookie_jar_for_runtime = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_bili_cookie_jar_for_runtime",
    )

    bili_login = ComponentCall(
        "_part_sight_component", _part_sight_component.SightComponent, "bili_login"
    )

    bili_logout = ComponentCall(
        "_part_sight_component", _part_sight_component.SightComponent, "bili_logout"
    )

    bili_status = ComponentCall(
        "_part_sight_component", _part_sight_component.SightComponent, "bili_status"
    )

    _sight_clips_from_event = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_clips_from_event",
    )

    _sight_clips_from_event_async = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_clips_from_event_async",
    )

    _sight_quote_clips_from_event = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_quote_clips_from_event",
    )

    _sight_quote_video_clips_from_event = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_quote_video_clips_from_event",
    )

    _sight_clip_source_is_usable = staticmethod(
        _part_sight_component.SightComponent._sight_clip_source_is_usable
    )

    _sight_clip_can_resolve = staticmethod(
        _part_sight_component.SightComponent._sight_clip_can_resolve
    )

    event_has_sight_video = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "event_has_sight_video",
    )

    schedule_video_context_from_event = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "schedule_video_context_from_event",
    )

    schedule_bili_summary_from_event = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "schedule_bili_summary_from_event",
    )

    _collect_sight_context_background = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_collect_sight_context_background",
    )

    _send_bili_summary_background = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_send_bili_summary_background",
    )

    _bili_summary_log_subject = staticmethod(
        _part_sight_component.SightComponent._bili_summary_log_subject
    )

    _cached_sight_note_markdown = staticmethod(
        _part_sight_component.SightComponent._cached_sight_note_markdown
    )

    _record_sight_delivery_metrics = staticmethod(
        _part_sight_component.SightComponent._record_sight_delivery_metrics
    )

    _log_sight_professional_metrics = staticmethod(
        _part_sight_component.SightComponent._log_sight_professional_metrics
    )

    _sight_note_digest = classmethod(
        _part_sight_component.SightComponent._sight_note_digest.__func__
    )

    _sight_note_digest_line = staticmethod(
        _part_sight_component.SightComponent._sight_note_digest_line
    )

    _sight_compact_text = staticmethod(
        _part_sight_component.SightComponent._sight_compact_text
    )

    _cache_sight_note_markdown = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_cache_sight_note_markdown",
    )

    _send_bili_summary_failure = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_send_bili_summary_failure",
    )

    life_video_understand = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "life_video_understand",
    )

    life_video_note = ComponentCall(
        "_part_sight_component", _part_sight_component.SightComponent, "life_video_note"
    )

    _lock_video_note_source = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_lock_video_note_source",
    )

    _life_video_note_inline = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_life_video_note_inline",
    )

    _understand_sight_clip = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_understand_sight_clip",
    )

    _understand_sight_clip_with_timeout = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_understand_sight_clip_with_timeout",
    )

    _complete_cached_sight_brief = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_complete_cached_sight_brief",
    )

    _sight_total_timeout_seconds = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_total_timeout_seconds",
    )

    _sight_finalize_timeout_seconds = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_finalize_timeout_seconds",
    )

    _cached_sight_insight_for_clip = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_cached_sight_insight_for_clip",
    )

    _sight_insight_for_clip = staticmethod(
        _part_sight_component.SightComponent._sight_insight_for_clip
    )

    _merged_sight_metadata = staticmethod(
        _part_sight_component.SightComponent._merged_sight_metadata
    )

    _understand_sight_clip_once = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_understand_sight_clip_once",
    )

    _prepare_sight_clip_material = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_prepare_sight_clip_material",
    )

    _finalize_prepared_sight_clip = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_finalize_prepared_sight_clip",
    )

    _resume_sight_summary_after_timeout = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_resume_sight_summary_after_timeout",
    )

    _finalize_sight_insight = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_finalize_sight_insight",
    )

    _log_sight_fusion = staticmethod(
        _part_sight_component.SightComponent._log_sight_fusion
    )

    _describe_sight_frames = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_describe_sight_frames",
    )

    _describe_sight_frames_with_provider = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_describe_sight_frames_with_provider",
    )

    _cleanup_sight_vision_session = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_cleanup_sight_vision_session",
    )

    @staticmethod
    async def _call_sight_vision_batch_provider(
        provider: _part_sight_component.Any,
        prompt: str,
        images: list[str],
        session_id: str,
    ) -> _part_sight_component.Any:
        return await _part_sight_component.SightComponent._call_sight_vision_batch_provider(
            provider, prompt, images, session_id
        )

    _sight_frame_parts = staticmethod(
        _part_sight_component.SightComponent._sight_frame_parts
    )

    @staticmethod
    async def _call_sight_vision_provider(
        provider: _part_sight_component.Any, prompt: str, image: str, session_id: str
    ) -> _part_sight_component.Any:
        return await _part_sight_component.SightComponent._call_sight_vision_provider(
            provider, prompt, image, session_id
        )

    _sight_video_clips_from_component = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_video_clips_from_component",
    )

    _sight_video_clip_from_component = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_video_clip_from_component",
    )

    _sight_nested_components = staticmethod(
        _part_sight_component.SightComponent._sight_nested_components
    )

    _resolve_sight_clip_source = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_resolve_sight_clip_source",
    )

    _resolve_sight_file_source = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_resolve_sight_file_source",
    )

    _sight_source_note = staticmethod(
        _part_sight_component.SightComponent._sight_source_note
    )

    _apply_sight_insight_to_structured = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_apply_sight_insight_to_structured",
    )

    _sight_plain_result = staticmethod(
        _part_sight_component.SightComponent._sight_plain_result
    )

    _send_sight_note = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_send_sight_note",
    )

    _mark_sight_note_sent = staticmethod(
        _part_sight_component.SightComponent._mark_sight_note_sent
    )

    _sight_note_was_sent = staticmethod(
        _part_sight_component.SightComponent._sight_note_was_sent
    )

    suppress_sight_note_followup = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "suppress_sight_note_followup",
    )

    _sight_note_chain = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_note_chain",
    )

    _render_sight_note_image = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_render_sight_note_image",
    )

    _sight_recent_for_event = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_sight_recent_for_event",
    )

    format_recent_sight_context = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "format_recent_sight_context",
    )

    _append_video_input_anchor = ComponentCall(
        "_part_sight_component",
        _part_sight_component.SightComponent,
        "_append_video_input_anchor",
    )

    _native_sight_route = ComponentCall(
        "_part_native_sight_component", NativeSightComponent, "_native_sight_route"
    )

    _native_video_prompt = staticmethod(NativeSightComponent._native_video_prompt)

    _understand_native_video = ComponentCall(
        "_part_native_sight_component", NativeSightComponent, "_understand_native_video"
    )

    _submit_native_video_tool = ComponentCall(
        "_part_native_sight_component",
        NativeSightComponent,
        "_submit_native_video_tool",
    )

    maintain_sight_cache = ComponentCall(
        "_part_sight_cleanup_component", SightCleanupComponent, "maintain_sight_cache"
    )

    _prune_cache_tree = staticmethod(SightCleanupComponent._prune_cache_tree)

    _prune_cache_files = staticmethod(SightCleanupComponent._prune_cache_files)

    _prune_empty_cache_dirs = staticmethod(
        SightCleanupComponent._prune_empty_cache_dirs
    )

    _init_sight_identity = ComponentCall(
        "_part_sight_identity_component", SightIdentityComponent, "_init_sight_identity"
    )

    _sight_raw_message = staticmethod(SightIdentityComponent._sight_raw_message)

    note_sight_group_upload_event = ComponentCall(
        "_part_sight_identity_component",
        SightIdentityComponent,
        "note_sight_group_upload_event",
    )

    _sight_apply_group_upload_id = ComponentCall(
        "_part_sight_identity_component",
        SightIdentityComponent,
        "_sight_apply_group_upload_id",
    )

    _sight_enrich_clips_from_raw_message = ComponentCall(
        "_part_sight_identity_component",
        SightIdentityComponent,
        "_sight_enrich_clips_from_raw_message",
    )

    _sight_completed_result_key = ComponentCall(
        "_part_sight_identity_component",
        SightIdentityComponent,
        "_sight_completed_result_key",
    )

    _mark_sight_completed_key = ComponentCall(
        "_part_sight_identity_component",
        SightIdentityComponent,
        "_mark_sight_completed_key",
    )

    _CONTINUOUS_TURN_SCOPE_ATTR = ContinuousTurnComponent._CONTINUOUS_TURN_SCOPE_ATTR

    _CONTINUOUS_TURN_PARTICIPANT_ATTR = (
        ContinuousTurnComponent._CONTINUOUS_TURN_PARTICIPANT_ATTR
    )

    _CONTINUOUS_TURN_REVISION_ATTR = (
        ContinuousTurnComponent._CONTINUOUS_TURN_REVISION_ATTR
    )

    _CONTINUOUS_TURN_MESSAGES_ATTR = (
        ContinuousTurnComponent._CONTINUOUS_TURN_MESSAGES_ATTR
    )

    _CONTINUOUS_TURN_DEADLINE_ATTR = (
        ContinuousTurnComponent._CONTINUOUS_TURN_DEADLINE_ATTR
    )

    _CONTINUOUS_TURN_STOPPED_ATTR = (
        ContinuousTurnComponent._CONTINUOUS_TURN_STOPPED_ATTR
    )

    _CONTINUOUS_TURN_WAIT_ATTR = ContinuousTurnComponent._CONTINUOUS_TURN_WAIT_ATTR

    _CONTINUOUS_TURN_FOLLOW_UP_ATTR = (
        ContinuousTurnComponent._CONTINUOUS_TURN_FOLLOW_UP_ATTR
    )

    _CONTINUOUS_TURN_IMAGES_ATTR = ContinuousTurnComponent._CONTINUOUS_TURN_IMAGES_ATTR

    _CONTINUOUS_TURN_RESTART_ATTR = (
        ContinuousTurnComponent._CONTINUOUS_TURN_RESTART_ATTR
    )

    _MEDIA_CHAT_TOKEN_ATTR = ContinuousTurnComponent._MEDIA_CHAT_TOKEN_ATTR

    _CONTINUOUS_TURN_MAX_MESSAGES = (
        ContinuousTurnComponent._CONTINUOUS_TURN_MAX_MESSAGES
    )

    _CONTINUOUS_TURN_MAX_CHARS = ContinuousTurnComponent._CONTINUOUS_TURN_MAX_CHARS

    _CONTINUOUS_TURN_ACTIVE_SECONDS = (
        ContinuousTurnComponent._CONTINUOUS_TURN_ACTIVE_SECONDS
    )

    _CONTINUOUS_TURN_CADENCE_MULTIPLIER = (
        ContinuousTurnComponent._CONTINUOUS_TURN_CADENCE_MULTIPLIER
    )

    _CONTINUOUS_TURN_MAX_TAIL_RATIO = (
        ContinuousTurnComponent._CONTINUOUS_TURN_MAX_TAIL_RATIO
    )

    _init_continuous_turn_state = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "_init_continuous_turn_state",
    )

    note_continuous_turn_media_ready = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "note_continuous_turn_media_ready",
    )

    _continuous_turn_restart_for_image = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "_continuous_turn_restart_for_image",
    )

    _continuous_turn_identity = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "_continuous_turn_identity",
    )

    _continuous_turn_message_id = staticmethod(
        ContinuousTurnComponent._continuous_turn_message_id
    )

    continuous_turn_intentional_wait_seconds = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "continuous_turn_intentional_wait_seconds",
    )

    _continuous_turn_batch = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "_continuous_turn_batch",
    )

    _continuous_turn_trim_messages = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "_continuous_turn_trim_messages",
    )

    note_continuous_turn_incoming = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "note_continuous_turn_incoming",
    )

    _continuous_turn_event_identity = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "_continuous_turn_event_identity",
    )

    continuous_turn_event_is_current = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "continuous_turn_event_is_current",
    )

    media_request_is_current_turn = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "media_request_is_current_turn",
    )

    _continuous_turn_stop_event = staticmethod(
        ContinuousTurnComponent._continuous_turn_stop_event
    )

    stop_stale_continuous_turn_event = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "stop_stale_continuous_turn_event",
    )

    settle_continuous_turn = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "settle_continuous_turn",
    )

    continuous_turn_messages = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "continuous_turn_messages",
    )

    continuous_turn_message_count = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "continuous_turn_message_count",
    )

    continuous_turn_event_is_inflight_follow_up = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "continuous_turn_event_is_inflight_follow_up",
    )

    continuous_turn_semantic_enabled_for_event = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "continuous_turn_semantic_enabled_for_event",
    )

    wait_continuous_turn_after_semantic = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "wait_continuous_turn_after_semantic",
    )

    prepare_continuous_turn_llm_request = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "prepare_continuous_turn_llm_request",
    )

    complete_continuous_turn = ComponentCall(
        "_part_continuous_turn_component",
        ContinuousTurnComponent,
        "complete_continuous_turn",
    )

    _RESPONSE_GATE_CONTINUATION_SECONDS = (
        ResponseGateStateComponent._RESPONSE_GATE_CONTINUATION_SECONDS
    )

    _RESPONSE_GATE_CONTINUATION_TURNS = (
        ResponseGateStateComponent._RESPONSE_GATE_CONTINUATION_TURNS
    )

    _MESSAGE_DEDUP_LIMIT = ResponseGateStateComponent._MESSAGE_DEDUP_LIMIT

    _init_response_gate_state = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_init_response_gate_state",
    )

    _response_gate_wait_state = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_wait_state",
    )

    _response_gate_mark_wait = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_mark_wait",
    )

    _response_gate_clear_wait = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_clear_wait",
    )

    _response_gate_mark_continuation = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_mark_continuation",
    )

    _response_gate_continuation_reason = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_continuation_reason",
    )

    _response_gate_current_state = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_current_state",
    )

    _response_gate_record_seen = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_record_seen",
    )

    _response_gate_record_reply = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_record_reply",
    )

    _response_gate_record_no_reply = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_record_no_reply",
    )

    _response_gate_in_backoff = ComponentCall(
        "_part_response_gate_state_component",
        ResponseGateStateComponent,
        "_response_gate_in_backoff",
    )

    _response_gate_attention_delta = ComponentCall(
        "_part_response_gate_sense_component",
        ResponseGateSenseComponent,
        "_response_gate_attention_delta",
    )

    _response_gate_presence_delta = ComponentCall(
        "_part_response_gate_sense_component",
        ResponseGateSenseComponent,
        "_response_gate_presence_delta",
    )

    _response_gate_batch_delta = ComponentCall(
        "_part_response_gate_sense_component",
        ResponseGateSenseComponent,
        "_response_gate_batch_delta",
    )

    _response_gate_feedback_delta = ComponentCall(
        "_part_response_gate_sense_component",
        ResponseGateSenseComponent,
        "_response_gate_feedback_delta",
    )

    _response_gate_experience_delta = ComponentCall(
        "_part_response_gate_sense_component",
        ResponseGateSenseComponent,
        "_response_gate_experience_delta",
    )

    _response_gate_note_attention = ComponentCall(
        "_part_response_gate_sense_component",
        ResponseGateSenseComponent,
        "_response_gate_note_attention",
    )

    _response_gate_should_skip = ComponentCall(
        "_part_response_gate_event_component",
        ResponseGateEventComponent,
        "_response_gate_should_skip",
    )

    _response_gate_force_reply_reason = ComponentCall(
        "_part_response_gate_event_component",
        ResponseGateEventComponent,
        "_response_gate_force_reply_reason",
    )

    mark_alias_directed_event_as_wake = ComponentCall(
        "_part_response_gate_event_component",
        ResponseGateEventComponent,
        "mark_alias_directed_event_as_wake",
    )

    _response_gate_scope_key = ComponentCall(
        "_part_response_gate_event_component",
        ResponseGateEventComponent,
        "_response_gate_scope_key",
    )

    _response_gate_visible_text = ComponentCall(
        "_part_response_gate_event_component",
        ResponseGateEventComponent,
        "_response_gate_visible_text",
    )

    _response_gate_has_media = ComponentCall(
        "_part_response_gate_event_component",
        ResponseGateEventComponent,
        "_response_gate_has_media",
    )

    _suppress_default_llm = staticmethod(
        ResponseGateEventComponent._suppress_default_llm
    )

    _response_gate_band_delta = staticmethod(
        ResponseGateScoreComponent._response_gate_band_delta
    )

    _response_gate_body_delta = staticmethod(
        ResponseGateScoreComponent._response_gate_body_delta
    )

    _response_gate_sleep_delta = staticmethod(
        ResponseGateScoreComponent._response_gate_sleep_delta
    )

    _response_gate_group_state_delta = staticmethod(
        ResponseGateScoreComponent._response_gate_group_state_delta
    )

    _response_gate_score = ComponentCall(
        "_part_response_gate_score_component",
        ResponseGateScoreComponent,
        "_response_gate_score",
    )

    _response_gate_state_delta = ComponentCall(
        "_part_response_gate_score_component",
        ResponseGateScoreComponent,
        "_response_gate_state_delta",
    )

    _response_gate_relationship_delta = ComponentCall(
        "_part_response_gate_score_component",
        ResponseGateScoreComponent,
        "_response_gate_relationship_delta",
    )

    _response_gate_profile_ids = ComponentCall(
        "_part_response_gate_score_component",
        ResponseGateScoreComponent,
        "_response_gate_profile_ids",
    )

    _response_gate_relationship_signal_count = staticmethod(
        ResponseGateScoreComponent._response_gate_relationship_signal_count
    )

    _response_gate_days_since = staticmethod(
        ResponseGateScoreComponent._response_gate_days_since
    )

    _response_gate_int = staticmethod(ResponseGateScoreComponent._response_gate_int)

    _response_gate_rhythm_dict = staticmethod(
        ResponseGateScoreComponent._response_gate_rhythm_dict
    )

    _response_gate_reply = staticmethod(ResponseGateScoreComponent._response_gate_reply)

    apply_response_gate_for_event = ComponentCall(
        "_part_response_gate_apply_component",
        ResponseGateApplyComponent,
        "apply_response_gate_for_event",
    )

    evaluate_response_gate = ComponentCall(
        "_part_response_gate_apply_component",
        ResponseGateApplyComponent,
        "evaluate_response_gate",
    )

    apply_rest_timing = ComponentCall(
        "_part_refresh_component", RefreshComponent, "apply_rest_timing"
    )

    _meta_datetime = staticmethod(RefreshComponent._meta_datetime)

    _state_stability_signature = staticmethod(
        RefreshComponent._state_stability_signature
    )

    _pending_commitment_outfit = staticmethod(
        RefreshComponent._pending_commitment_outfit
    )

    _schedule_outfit_scene = staticmethod(RefreshComponent._schedule_outfit_scene)

    _schedule_outfit_scene_sync_needed = classmethod(
        RefreshComponent._schedule_outfit_scene_sync_needed.__func__
    )

    _synchronize_outfit_with_schedule = classmethod(
        RefreshComponent._synchronize_outfit_with_schedule.__func__
    )

    _state_refresh_in_quiet_hours = ComponentCall(
        "_part_refresh_component", RefreshComponent, "_state_refresh_in_quiet_hours"
    )

    _run_autonomous_life_check = ComponentCall(
        "_part_refresh_component", RefreshComponent, "_run_autonomous_life_check"
    )

    check_autonomous_life_update = ComponentCall(
        "_part_refresh_component", RefreshComponent, "check_autonomous_life_update"
    )

    check_period_transition = ComponentCall(
        "_part_refresh_component", RefreshComponent, "check_period_transition"
    )

    _schedule_context_state_refresh = ComponentCall(
        "_part_refresh_component", RefreshComponent, "_schedule_context_state_refresh"
    )

    _MEDIA_DELIVERY_LEASE_SECONDS = (
        RuntimeActionReceiptComponent._MEDIA_DELIVERY_LEASE_SECONDS
    )

    _share_asset_key = staticmethod(RuntimeActionReceiptComponent._share_asset_key)

    get_share_image_task = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "get_share_image_task",
    )

    get_share_video_task = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "get_share_video_task",
    )

    generate_share_image_task = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "generate_share_image_task",
    )

    generate_share_video_task = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "generate_share_video_task",
    )

    record_share_delivery = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "record_share_delivery",
    )

    resume_share_image_generation = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "resume_share_image_generation",
    )

    resume_share_video_generation = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "resume_share_video_generation",
    )

    track_image_generation = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "track_image_generation",
    )

    pending_image_generation_result = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "pending_image_generation_result",
    )

    resume_durable_image_generation = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "resume_durable_image_generation",
    )

    record_current_life_action_receipt = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "record_current_life_action_receipt",
    )

    stage_durable_media_delivery = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "stage_durable_media_delivery",
    )

    finalize_durable_media_delivery = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "finalize_durable_media_delivery",
    )

    direct_media_was_delivered = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "direct_media_was_delivered",
    )

    resume_durable_media_delivery = ComponentCall(
        "_part_runtime_action_receipt_component",
        RuntimeActionReceiptComponent,
        "resume_durable_media_delivery",
    )

    _CHAT_STYLE_PENDING_SEGMENTS_ATTR = (
        ChatStyleRuntimeComponent._CHAT_STYLE_PENDING_SEGMENTS_ATTR
    )

    _CHAT_STYLE_STRONG_BREAKS = ChatStyleRuntimeComponent._CHAT_STYLE_STRONG_BREAKS

    _CHAT_STYLE_SOFT_BREAKS = ChatStyleRuntimeComponent._CHAT_STYLE_SOFT_BREAKS

    _CHAT_STYLE_URL_PREFIXES = ChatStyleRuntimeComponent._CHAT_STYLE_URL_PREFIXES

    _CHAT_STYLE_URL_TRAILING_BREAKS = (
        ChatStyleRuntimeComponent._CHAT_STYLE_URL_TRAILING_BREAKS
    )

    _CHAT_STYLE_QUOTE_PAIRS = ChatStyleRuntimeComponent._CHAT_STYLE_QUOTE_PAIRS

    _CHAT_STYLE_PROTECTED_INLINE_MARKDOWN = (
        ChatStyleRuntimeComponent._CHAT_STYLE_PROTECTED_INLINE_MARKDOWN
    )

    _CHAT_STYLE_INLINE_MARKDOWN_PATTERNS = (
        ChatStyleRuntimeComponent._CHAT_STYLE_INLINE_MARKDOWN_PATTERNS
    )

    _chat_style_enabled = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "_chat_style_enabled",
    )

    _chat_style_astrbot_send_config = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "_chat_style_astrbot_send_config",
    )

    _chat_style_int_config = staticmethod(
        ChatStyleRuntimeComponent._chat_style_int_config
    )

    _chat_style_should_keep_default_send = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "_chat_style_should_keep_default_send",
    )

    _chat_style_context = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "_chat_style_context",
    )

    _chat_style_limit_for_event = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "_chat_style_limit_for_event",
    )

    build_chat_style_injection_context = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "build_chat_style_injection_context",
    )

    _chat_style_text_is_structural = staticmethod(
        ChatStyleRuntimeComponent._chat_style_text_is_structural
    )

    _chat_style_clean_inline_markdown = classmethod(
        ChatStyleRuntimeComponent._chat_style_clean_inline_markdown.__func__
    )

    _chat_style_plain_fence_marker = staticmethod(
        ChatStyleRuntimeComponent._chat_style_plain_fence_marker
    )

    _chat_style_fenced_block_is_prose = staticmethod(
        ChatStyleRuntimeComponent._chat_style_fenced_block_is_prose
    )

    _chat_style_unwrap_t2i_prose_fences = classmethod(
        ChatStyleRuntimeComponent._chat_style_unwrap_t2i_prose_fences.__func__
    )

    apply_chat_plain_text_cleanup_before_send = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "apply_chat_plain_text_cleanup_before_send",
    )

    _replace_text_result = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "_replace_text_result",
    )

    _chat_style_text_component_text = staticmethod(
        ChatStyleRuntimeComponent._chat_style_text_component_text
    )

    _chat_style_copy_text_component = staticmethod(
        ChatStyleRuntimeComponent._chat_style_copy_text_component
    )

    _chat_style_result_is_text_only = classmethod(
        ChatStyleRuntimeComponent._chat_style_result_is_text_only.__func__
    )

    _chat_style_compact_text = classmethod(
        ChatStyleRuntimeComponent._chat_style_compact_text.__func__
    )

    _chat_style_char_typing_weight = staticmethod(
        ChatStyleRuntimeComponent._chat_style_char_typing_weight
    )

    _chat_style_typing_weight = classmethod(
        ChatStyleRuntimeComponent._chat_style_typing_weight.__func__
    )

    _chat_style_initial_typing_delay_seconds = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "_chat_style_initial_typing_delay_seconds",
    )

    _chat_style_protected_span_end = classmethod(
        ChatStyleRuntimeComponent._chat_style_protected_span_end.__func__
    )

    _chat_style_split_units = classmethod(
        ChatStyleRuntimeComponent._chat_style_split_units.__func__
    )

    _chat_style_merge_tail_segments = staticmethod(
        ChatStyleRuntimeComponent._chat_style_merge_tail_segments
    )

    _chat_style_units_from_lines = classmethod(
        ChatStyleRuntimeComponent._chat_style_units_from_lines.__func__
    )

    _chat_style_single_segment_plan = classmethod(
        ChatStyleRuntimeComponent._chat_style_single_segment_plan.__func__
    )

    _chat_style_segment_source = classmethod(
        ChatStyleRuntimeComponent._chat_style_segment_source.__func__
    )

    _chat_style_segment_controls = classmethod(
        ChatStyleRuntimeComponent._chat_style_segment_controls.__func__
    )

    _chat_style_collect_segment_plans = classmethod(
        ChatStyleRuntimeComponent._chat_style_collect_segment_plans.__func__
    )

    _chat_style_refine_segment_plans = classmethod(
        ChatStyleRuntimeComponent._chat_style_refine_segment_plans.__func__
    )

    _plan_chat_style_natural_segments = classmethod(
        ChatStyleRuntimeComponent._plan_chat_style_natural_segments.__func__
    )

    _chat_style_segment_reason = classmethod(
        ChatStyleRuntimeComponent._chat_style_segment_reason.__func__
    )

    _chat_style_pending_segments = classmethod(
        ChatStyleRuntimeComponent._chat_style_pending_segments.__func__
    )

    _chat_style_pending_trace = staticmethod(
        ChatStyleRuntimeComponent._chat_style_pending_trace
    )

    _split_chat_style_natural_segments = classmethod(
        ChatStyleRuntimeComponent._split_chat_style_natural_segments.__func__
    )

    _replace_text_result_with_segments = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "_replace_text_result_with_segments",
    )

    send_chat_style_segments_if_needed = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "send_chat_style_segments_if_needed",
    )

    apply_chat_style_before_send = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "apply_chat_style_before_send",
    )

    log_chat_style_trace = ComponentCall(
        "_part_chat_style_runtime_component",
        ChatStyleRuntimeComponent,
        "log_chat_style_trace",
    )


    _CHAT_STYLE_MAX_DELAY_SECONDS = ChatDelayComponent._CHAT_STYLE_MAX_DELAY_SECONDS

    _chat_style_natural_segment_delay_seconds = ComponentCall(
        "_part_chat_delay_component",
        ChatDelayComponent,
        "_chat_style_natural_segment_delay_seconds",
    )

    _chat_style_segment_delay_seconds = ComponentCall(
        "_part_chat_delay_component",
        ChatDelayComponent,
        "_chat_style_segment_delay_seconds",
    )

    _SEMANTIC_SEGMENT_PENDING_ATTR = (
        SemanticSegmentRuntimeComponent._SEMANTIC_SEGMENT_PENDING_ATTR
    )

    _SEMANTIC_SEGMENT_PLAN_ATTR = (
        SemanticSegmentRuntimeComponent._SEMANTIC_SEGMENT_PLAN_ATTR
    )

    _SEMANTIC_SEGMENT_SCOPE_ATTR = (
        SemanticSegmentRuntimeComponent._SEMANTIC_SEGMENT_SCOPE_ATTR
    )

    _SEMANTIC_SEGMENT_ATTEMPTED_ATTR = (
        SemanticSegmentRuntimeComponent._SEMANTIC_SEGMENT_ATTEMPTED_ATTR
    )

    _semantic_segment_init_state = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "_semantic_segment_init_state",
    )

    _semantic_segment_enabled = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "_semantic_segment_enabled",
    )

    _semantic_segment_scope_from_event = staticmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_scope_from_event
    )

    _semantic_segment_revision = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "_semantic_segment_revision",
    )

    _semantic_segment_epoch = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "_semantic_segment_epoch",
    )

    note_semantic_segment_incoming_message = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "note_semantic_segment_incoming_message",
    )

    _note_chat_pacing_effect = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "_note_chat_pacing_effect",
    )

    _semantic_expression_plan_from_event = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "_semantic_expression_plan_from_event",
    )

    _semantic_segment_text = staticmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_text
    )

    _semantic_segment_copy = staticmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_copy
    )

    _semantic_segment_parse_payload = staticmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_parse_payload
    )

    _semantic_segment_normalize_source_text = staticmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_normalize_source_text
    )

    _semantic_segment_source_lines = staticmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_source_lines
    )

    _semantic_segment_replace_parts = staticmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_replace_parts
    )

    _semantic_segment_limit_delivery_count = classmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_limit_delivery_count.__func__
    )

    _semantic_segment_respect_source_lines = classmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_respect_source_lines.__func__
    )

    _semantic_segment_clean_punctuation = staticmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_clean_punctuation
    )

    _semantic_segment_clean_plan_punctuation = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "_semantic_segment_clean_plan_punctuation",
    )

    _semantic_segment_trace = staticmethod(
        SemanticSegmentRuntimeComponent._semantic_segment_trace
    )

    apply_semantic_segment_before_send = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "apply_semantic_segment_before_send",
    )

    send_semantic_segments_if_needed = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "send_semantic_segments_if_needed",
    )

    plan_semantic_segments_for_text = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "plan_semantic_segments_for_text",
    )

    send_semantic_segments = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "send_semantic_segments",
    )

    send_share_expression = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "send_share_expression",
    )

    send_background_text = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "send_background_text",
    )

    semantic_segment_status = ComponentCall(
        "_part_semantic_segment_runtime_component",
        SemanticSegmentRuntimeComponent,
        "semantic_segment_status",
    )

    _life_injection_now = ComponentCall(
        "_part_inject_component", InjectComponent, "_life_injection_now"
    )

    _get_time_status = ComponentCall(
        "_part_snapshot_tempo_component", SnapshotTempoComponent, "_get_time_status"
    )

    is_internal_llm_session = staticmethod(
        SnapshotTempoComponent.is_internal_llm_session
    )

    resolve_injection_target = ComponentCall(
        "_part_snapshot_tempo_component",
        SnapshotTempoComponent,
        "resolve_injection_target",
    )

    _share_current_facts = staticmethod(SnapshotExportComponent._share_current_facts)

    _life_context_meta_line = staticmethod(
        SnapshotExportComponent._life_context_meta_line
    )

    _life_context_weather = staticmethod(SnapshotExportComponent._life_context_weather)

    _life_context_subject = staticmethod(SnapshotExportComponent._life_context_subject)

    _life_context_people_keys = staticmethod(
        SnapshotExportComponent._life_context_people_keys
    )

    _life_context_filter_people_records = classmethod(
        SnapshotExportComponent._life_context_filter_people_records.__func__
    )

    _share_record_value = staticmethod(SnapshotExportComponent._share_record_value)

    _share_text = classmethod(SnapshotExportComponent._share_text.__func__)

    _share_exact_scope = classmethod(
        SnapshotExportComponent._share_exact_scope.__func__
    )

    _share_scope_context = classmethod(
        SnapshotExportComponent._share_scope_context.__func__
    )

    _share_episode_payload = classmethod(
        SnapshotExportComponent._share_episode_payload.__func__
    )

    _share_expression_payload = classmethod(
        SnapshotExportComponent._share_expression_payload.__func__
    )

    _share_behavior_payload = classmethod(
        SnapshotExportComponent._share_behavior_payload.__func__
    )

    _share_terms_payload = classmethod(
        SnapshotExportComponent._share_terms_payload.__func__
    )

    _share_interaction_payload = classmethod(
        SnapshotExportComponent._share_interaction_payload.__func__
    )

    get_share_chat_style = ComponentCall(
        "_part_snapshot_export_component",
        SnapshotExportComponent,
        "get_share_chat_style",
    )

    get_share_context = ComponentCall(
        "_part_snapshot_export_component", SnapshotExportComponent, "get_share_context"
    )

    ensure_startup_day_data = ComponentCall(
        "_part_snapshot_seed_component",
        SnapshotSeedComponent,
        "ensure_startup_day_data",
    )

    ensure_injection_day_data = ComponentCall(
        "_part_snapshot_seed_component",
        SnapshotSeedComponent,
        "ensure_injection_day_data",
    )

    maybe_update_injection_outfit = ComponentCall(
        "_part_snapshot_seed_component",
        SnapshotSeedComponent,
        "maybe_update_injection_outfit",
    )

    _refresh_state_for_chat_background = ComponentCall(
        "_part_snapshot_chat_component",
        SnapshotChatComponent,
        "_refresh_state_for_chat_background",
    )

    _collect_emoji_context_background = ComponentCall(
        "_part_snapshot_chat_component",
        SnapshotChatComponent,
        "_collect_emoji_context_background",
    )

    _INJECTION_SNAPSHOT_CACHE_TTL_SECONDS = (
        SnapshotPackComponent._INJECTION_SNAPSHOT_CACHE_TTL_SECONDS
    )

    _INJECTION_SNAPSHOT_CACHE_LIMIT = (
        SnapshotPackComponent._INJECTION_SNAPSHOT_CACHE_LIMIT
    )

    _cached_injection_snapshot = classmethod(
        SnapshotPackComponent._cached_injection_snapshot.__func__
    )

    _close_injection_snapshot_flight = ComponentCall(
        "_part_snapshot_pack_component",
        SnapshotPackComponent,
        "_close_injection_snapshot_flight",
    )

    _gather_life_context_snapshot = ComponentCall(
        "_part_snapshot_pack_component",
        SnapshotPackComponent,
        "_gather_life_context_snapshot",
    )

    _hidden_text = staticmethod(InjectVeilComponent._hidden_text)

    _hidden_lines = staticmethod(HiddenExperienceComponent._hidden_lines)

    _hidden_section = staticmethod(HiddenExperienceComponent._hidden_section)

    _format_hidden_experience_context = ComponentCall(
        "_part_hidden_experience_component",
        HiddenExperienceComponent,
        "_format_hidden_experience_context",
    )

    build_hidden_chat_style_hint = ComponentCall(
        "_part_layer_text_component", LayerTextComponent, "build_hidden_chat_style_hint"
    )

    _format_hidden_schedule_window = ComponentCall(
        "_part_layer_text_component",
        LayerTextComponent,
        "_format_hidden_schedule_window",
    )

    build_hidden_activity_hint = ComponentCall(
        "_part_layer_text_component", LayerTextComponent, "build_hidden_activity_hint"
    )

    build_hidden_life_context = ComponentCall(
        "_part_layer_text_component", LayerTextComponent, "build_hidden_life_context"
    )

    build_missing_life_context = ComponentCall(
        "_part_layer_text_component", LayerTextComponent, "build_missing_life_context"
    )

    _CHAT_MEMORY_RANK_BUDGET_SECONDS = (
        LayerChainComponent._CHAT_MEMORY_RANK_BUDGET_SECONDS
    )

    _EXPERIENCE_RELEVANCE_FIELDS = LayerChainComponent._EXPERIENCE_RELEVANCE_FIELDS

    _build_person_fact_injection_context = ComponentCall(
        "_part_layer_chain_component",
        LayerChainComponent,
        "_build_person_fact_injection_context",
    )

    _semantic_snapshot_id = staticmethod(LayerChainComponent._semantic_snapshot_id)

    _event_message_text = ComponentCall(
        "_part_layer_chain_component", LayerChainComponent, "_event_message_text"
    )

    schedule_emoji_capture_from_event = ComponentCall(
        "_part_layer_chain_component",
        LayerChainComponent,
        "schedule_emoji_capture_from_event",
    )

    schedule_pending_chat_state_refresh = ComponentCall(
        "_part_layer_chain_component",
        LayerChainComponent,
        "schedule_pending_chat_state_refresh",
    )

    _format_cognition_context = staticmethod(
        LayerChainComponent._format_cognition_context
    )

    inject_life_context = ComponentCall(
        "_part_layer_chain_component", LayerChainComponent, "inject_life_context"
    )

    _apply_life_request_context = ComponentCall(
        "_part_request_prefix_component",
        RequestPrefixComponent,
        "_apply_life_request_context",
    )

    _FOLLOW_UP_ACTIONS = ProactiveFollowupComponent._FOLLOW_UP_ACTIONS

    _validate_proactive_commitment_decision = staticmethod(
        ProactiveFollowupComponent._validate_proactive_commitment_decision
    )

    _follow_up_execute_at = staticmethod(
        ProactiveFollowupComponent._follow_up_execute_at
    )

    _commitment_requests_photo = classmethod(
        ProactiveFollowupComponent._commitment_requests_photo.__func__
    )

    _commitment_requests_video = classmethod(
        ProactiveFollowupComponent._commitment_requests_video.__func__
    )

    _photo_commitment_owner_allowed = classmethod(
        ProactiveFollowupComponent._photo_commitment_owner_allowed.__func__
    )

    _photo_commitment_execute_at = staticmethod(
        ProactiveFollowupComponent._photo_commitment_execute_at
    )

    schedule_commitment_photo = ComponentCall(
        "_part_proactive_followup_component",
        ProactiveFollowupComponent,
        "schedule_commitment_photo",
    )

    reconcile_commitment_photo_tasks = ComponentCall(
        "_part_proactive_followup_component",
        ProactiveFollowupComponent,
        "reconcile_commitment_photo_tasks",
    )

    schedule_commitment_video = ComponentCall(
        "_part_proactive_followup_component",
        ProactiveFollowupComponent,
        "schedule_commitment_video",
    )

    reconcile_commitment_video_tasks = ComponentCall(
        "_part_proactive_followup_component",
        ProactiveFollowupComponent,
        "reconcile_commitment_video_tasks",
    )

    run_commitment_photo_task = ComponentCall(
        "_part_proactive_followup_component",
        ProactiveFollowupComponent,
        "run_commitment_photo_task",
    )

    run_commitment_video_task = ComponentCall(
        "_part_proactive_followup_component",
        ProactiveFollowupComponent,
        "run_commitment_video_task",
    )

    schedule_proactive_commitment = ComponentCall(
        "_part_proactive_followup_component",
        ProactiveFollowupComponent,
        "schedule_proactive_commitment",
    )

    reconcile_commitment_contact_tasks = ComponentCall(
        "_part_proactive_followup_component", ProactiveFollowupComponent,
        "reconcile_commitment_contact_tasks",
    )

    run_commitment_contact_review_task = ComponentCall(
        "_part_proactive_followup_component", ProactiveFollowupComponent,
        "run_commitment_contact_review_task",
    )

    _proactive_commitment_relationship = ComponentCall(
        "_part_proactive_followup_component",
        ProactiveFollowupComponent,
        "_proactive_commitment_relationship",
    )

    run_proactive_commitment_task = ComponentCall(
        "_part_proactive_followup_component",
        ProactiveFollowupComponent,
        "run_proactive_commitment_task",
    )

    reconcile_scheduled_invite_contacts = ComponentCall(
        "_part_shared_activity_contact_component",
        SharedActivityContactComponent,
        "reconcile_scheduled_invite_contacts",
    )

    _shared_activity_timeline = staticmethod(
        SharedActivityContactComponent._shared_activity_timeline
    )

    schedule_invite_contact = ComponentCall(
        "_part_shared_activity_contact_component",
        SharedActivityContactComponent,
        "schedule_invite_contact",
    )

    _shared_activity_candidates = staticmethod(
        SharedActivityContactComponent._shared_activity_candidates
    )

    _run_shared_activity_contact = ComponentCall(
        "_part_shared_activity_contact_component",
        SharedActivityContactComponent,
        "_run_shared_activity_contact",
    )

    _PROACTIVE_UTILITY_FIELDS = ProactiveLifecycleComponent._PROACTIVE_UTILITY_FIELDS

    _PROACTIVE_UTILITY_THRESHOLD = (
        ProactiveLifecycleComponent._PROACTIVE_UTILITY_THRESHOLD
    )

    _PROACTIVE_LIFECYCLE_TRANSITIONS = (
        ProactiveLifecycleComponent._PROACTIVE_LIFECYCLE_TRANSITIONS
    )

    _proactive_social_sessions = ComponentCall(
        "_part_proactive_lifecycle_component",
        ProactiveLifecycleComponent,
        "_proactive_social_sessions",
    )

    _proactive_lifecycle_snapshot = ComponentCall(
        "_part_proactive_lifecycle_component",
        ProactiveLifecycleComponent,
        "_proactive_lifecycle_snapshot",
    )

    _transition_proactive_lifecycle = ComponentCall(
        "_part_proactive_lifecycle_component",
        ProactiveLifecycleComponent,
        "_transition_proactive_lifecycle",
    )

    _proactive_score = staticmethod(ProactiveLifecycleComponent._proactive_score)

    _normalize_proactive_utility = ComponentCall(
        "_part_proactive_lifecycle_component",
        ProactiveLifecycleComponent,
        "_normalize_proactive_utility",
    )

    _AIR_BACKOFF_BASE_SECONDS = AirMeterComponent._AIR_BACKOFF_BASE_SECONDS

    _AIR_BACKOFF_CAP_SECONDS = AirMeterComponent._AIR_BACKOFF_CAP_SECONDS

    _AIR_BACKOFF_START_COUNT = AirMeterComponent._AIR_BACKOFF_START_COUNT

    _AIR_BYPASS_PENDING_COUNT = AirMeterComponent._AIR_BYPASS_PENDING_COUNT

    _AIR_WAIT_SECONDS = AirMeterComponent._AIR_WAIT_SECONDS

    _AIR_MAX_RECENT_MESSAGES = AirMeterComponent._AIR_MAX_RECENT_MESSAGES

    _get_proactive_air_state = ComponentCall(
        "_part_air_meter_component", AirMeterComponent, "_get_proactive_air_state"
    )

    _reset_proactive_air_state = ComponentCall(
        "_part_air_meter_component", AirMeterComponent, "_reset_proactive_air_state"
    )

    _format_proactive_air_state = ComponentCall(
        "_part_air_meter_component", AirMeterComponent, "_format_proactive_air_state"
    )

    _air_backoff_seconds = ComponentCall(
        "_part_air_meter_component", AirMeterComponent, "_air_backoff_seconds"
    )

    _candidate_pending_count = ComponentCall(
        "_part_air_meter_component", AirMeterComponent, "_candidate_pending_count"
    )

    _proactive_air_delay_remaining = ComponentCall(
        "_part_air_meter_component", AirMeterComponent, "_proactive_air_delay_remaining"
    )

    _proactive_target_payload = staticmethod(
        AirMeterComponent._proactive_target_payload
    )

    _proactive_readiness_check = ComponentCall(
        "_part_air_meter_component", AirMeterComponent, "_proactive_readiness_check"
    )

    _update_proactive_air_after_decision = ComponentCall(
        "_part_air_meter_component",
        AirMeterComponent,
        "_update_proactive_air_after_decision",
    )

    _recent_messages_for_candidate = ComponentCall(
        "_part_air_meter_component", AirMeterComponent, "_recent_messages_for_candidate"
    )

    _format_candidate_recent_messages = ComponentCall(
        "_part_air_meter_component",
        AirMeterComponent,
        "_format_candidate_recent_messages",
    )

    _AIR_FEEDBACK_WINDOW_SECONDS = AirEffectComponent._AIR_FEEDBACK_WINDOW_SECONDS

    _proactive_feedback_watches = ComponentCall(
        "_part_air_effect_component", AirEffectComponent, "_proactive_feedback_watches"
    )

    _track_proactive_reply_effect = ComponentCall(
        "_part_air_effect_component",
        AirEffectComponent,
        "_track_proactive_reply_effect",
    )

    note_regular_reply_effect = ComponentCall(
        "_part_air_effect_component", AirEffectComponent, "note_regular_reply_effect"
    )

    _save_pending_reply_effect = ComponentCall(
        "_part_air_effect_component", AirEffectComponent, "_save_pending_reply_effect"
    )

    _settle_stale_reply_effects = ComponentCall(
        "_part_air_effect_component", AirEffectComponent, "_settle_stale_reply_effects"
    )

    _reply_effect_source_labels = staticmethod(
        AirEffectComponent._reply_effect_source_labels
    )

    _observe_proactive_reply_effect = ComponentCall(
        "_part_air_effect_component",
        AirEffectComponent,
        "_observe_proactive_reply_effect",
    )

    _private_revisit_event = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_private_revisit_event",
    )

    _clamp_float = staticmethod(ProactiveContextComponent._clamp_float)

    _proactive_bool = staticmethod(ProactiveContextComponent._proactive_bool)

    _proactive_reply_text = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_proactive_reply_text",
    )

    _proactive_expression_limit_for_scope = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_proactive_expression_limit_for_scope",
    )

    _format_proactive_expression_style = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_format_proactive_expression_style",
    )

    _expression_review_passed = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_expression_review_passed",
    )

    _current_proactive_persona = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_current_proactive_persona",
    )

    _format_proactive_persona_context = staticmethod(
        ProactiveContextComponent._format_proactive_persona_context
    )

    _read_recent_context_messages = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_read_recent_context_messages",
    )

    _context_message_timestamp = staticmethod(
        ProactiveContextComponent._context_message_timestamp
    )

    _merge_recent_context_messages = classmethod(
        ProactiveContextComponent._merge_recent_context_messages.__func__
    )

    _audit_proactive_continuity = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_audit_proactive_continuity",
    )

    _format_context_message_label = staticmethod(
        ProactiveContextComponent._format_context_message_label
    )

    _format_context_message_content = staticmethod(
        ProactiveContextComponent._format_context_message_content
    )

    _format_recent_context_messages = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_format_recent_context_messages",
    )

    _build_recent_context_for_proactive = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_build_recent_context_for_proactive",
    )

    _format_expression_profiles_for_proactive = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_format_expression_profiles_for_proactive",
    )


    _format_reply_effects_for_proactive = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_format_reply_effects_for_proactive",
    )

    _format_expression_reviews_for_proactive = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_format_expression_reviews_for_proactive",
    )

    _format_behavior_scenes_for_proactive = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_format_behavior_scenes_for_proactive",
    )




    _format_life_terms_for_proactive = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_format_life_terms_for_proactive",
    )

    _relationship_friend_target_scope = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_relationship_friend_target_scope",
    )

    _resolve_private_target_umo = ComponentCall(
        "_part_proactive_context_component",
        ProactiveContextComponent,
        "_resolve_private_target_umo",
    )

    _EMOJI_MODEL_CANDIDATE_LIMIT = (
        ProactiveGestureComponent._EMOJI_MODEL_CANDIDATE_LIMIT
    )

    _EMOJI_RECENT_SENT_EXCLUSION_LIMIT = (
        ProactiveGestureComponent._EMOJI_RECENT_SENT_EXCLUSION_LIMIT
    )

    _SEMANTIC_EMOJI_ATTEMPTED_ATTR = (
        ProactiveGestureComponent._SEMANTIC_EMOJI_ATTEMPTED_ATTR
    )

    _emoji_timestamp = staticmethod(ProactiveGestureComponent._emoji_timestamp)

    _emoji_asset_key = staticmethod(ProactiveGestureComponent._emoji_asset_key)

    _disable_emoji_tool_for_active_turn = staticmethod(
        ProactiveGestureComponent._disable_emoji_tool_for_active_turn
    )

    life_emoji_send = ComponentCall(
        "_part_proactive_gesture_component",
        ProactiveGestureComponent,
        "life_emoji_send",
    )

    send_semantic_emoji_if_needed = ComponentCall(
        "_part_proactive_gesture_component",
        ProactiveGestureComponent,
        "send_semantic_emoji_if_needed",
    )

    _send_proactive_emoji_if_needed = ComponentCall(
        "_part_proactive_gesture_component",
        ProactiveGestureComponent,
        "_send_proactive_emoji_if_needed",
    )

    _emoji_asset_is_sendable = staticmethod(
        ProactiveGestureComponent._emoji_asset_is_sendable
    )

    _emoji_recency_penalty = staticmethod(
        ProactiveGestureComponent._emoji_recency_penalty
    )

    _emoji_semantic_text = staticmethod(ProactiveGestureComponent._emoji_semantic_text)

    _send_segmented_proactive_message = ComponentCall(
        "_part_proactive_segment_component",
        ProactiveSegmentComponent,
        "_send_segmented_proactive_message",
    )

    _chat_style_limit_for_scope = ComponentCall(
        "_part_proactive_segment_component",
        ProactiveSegmentComponent,
        "_chat_style_limit_for_scope",
    )

    _apply_proactive_send_timing = ComponentCall(
        "_part_proactive_segment_component",
        ProactiveSegmentComponent,
        "_apply_proactive_send_timing",
    )

    remove_recalled_proactive_candidate = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "remove_recalled_proactive_candidate",
    )

    _record_virtual_life_metric = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_record_virtual_life_metric",
    )

    _cancel_proactive_idle_tasks = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_cancel_proactive_idle_tasks",
    )

    _schedule_proactive_idle_evaluation = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_schedule_proactive_idle_evaluation",
    )

    _proactive_chat_enabled = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_chat_enabled",
    )

    _proactive_idle_enabled = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_idle_enabled",
    )

    _prune_disabled_proactive_candidates = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_prune_disabled_proactive_candidates",
    )

    _get_proactive_provider = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_get_proactive_provider",
    )

    _proactive_scope_key = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_scope_key",
    )

    _proactive_is_self_message = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_is_self_message",
    )

    _proactive_allowed_for_event = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_allowed_for_event",
    )

    _proactive_idle_seconds = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_idle_seconds",
    )

    _refresh_private_proactive_timing = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_refresh_private_proactive_timing",
    )

    _proactive_observe_hold_seconds = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_observe_hold_seconds",
    )

    _proactive_observe_hold_remaining = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_observe_hold_remaining",
    )

    _proactive_cooldown_remaining = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_cooldown_remaining",
    )

    _mark_proactive_reply_sent = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_mark_proactive_reply_sent",
    )

    note_proactive_activity = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "note_proactive_activity",
    )

    note_conversation_turn_decision = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "note_conversation_turn_decision",
    )

    note_proactive_bot_reply = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "note_proactive_bot_reply",
    )

    _proactive_candidate_event = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_candidate_event",
    )

    _proactive_current_day = ComponentCall(
        "_part_proactive_scope_component",
        ProactiveScopeComponent,
        "_proactive_current_day",
    )

    _snapshot_proactive_send_event = ComponentCall(
        "_part_proactive_send_component",
        ProactiveSendComponent,
        "_snapshot_proactive_send_event",
    )

    _proactive_send_is_current = ComponentCall(
        "_part_proactive_send_component",
        ProactiveSendComponent,
        "_proactive_send_is_current",
    )

    _send_proactive_voice_call_invite = ComponentCall(
        "_part_proactive_send_component",
        ProactiveSendComponent,
        "_send_proactive_voice_call_invite",
    )

    _proactive_source_label = staticmethod(
        ProactiveSendComponent._proactive_source_label
    )

    _send_proactive_message = ComponentCall(
        "_part_proactive_send_component",
        ProactiveSendComponent,
        "_send_proactive_message",
    )

    _save_proactive_expression_records = ComponentCall(
        "_part_proactive_send_component",
        ProactiveSendComponent,
        "_save_proactive_expression_records",
    )

    _proactive_voice_capability = ComponentCall(
        "_part_proactive_send_component",
        ProactiveSendComponent,
        "_proactive_voice_capability",
    )

    _record_message_chain = staticmethod(ProactiveSendComponent._record_message_chain)

    image_message_chain = staticmethod(ProactiveSendComponent.image_message_chain)

    images_message_chain = staticmethod(ProactiveSendComponent.images_message_chain)

    video_message_chain = staticmethod(ProactiveSendComponent.video_message_chain)

    video_file_message_chain = staticmethod(
        ProactiveSendComponent.video_file_message_chain
    )

    _build_recent_group_awareness_for_proactive = ComponentCall(
        "_part_proactive_aware_component",
        ProactiveAwareComponent,
        "_build_recent_group_awareness_for_proactive",
    )

    _proactive_relationship_context = staticmethod(
        ProactivePromptComponent._proactive_relationship_context
    )

    _proactive_summary_lines = staticmethod(
        ProactivePromptComponent._proactive_summary_lines
    )


    _proactive_readiness_text = staticmethod(
        ProactivePromptComponent._proactive_readiness_text
    )

    _build_proactive_prompt = ComponentCall(
        "_part_proactive_prompt_component",
        ProactivePromptComponent,
        "_build_proactive_prompt",
    )

    _advance_proactive_decision_trace = ComponentCall(
        "_part_proactive_store_component",
        ProactiveStoreComponent,
        "_advance_proactive_decision_trace",
    )

    _save_proactive_decision = ComponentCall(
        "_part_proactive_store_component",
        ProactiveStoreComponent,
        "_save_proactive_decision",
    )

    _proactive_skip = staticmethod(ProactiveFlowComponent._proactive_skip)

    _split_voice_call_invite_message = staticmethod(
        ProactiveFlowComponent._split_voice_call_invite_message
    )

    _commit_proactive_decision = ComponentCall(
        "_part_proactive_flow_component",
        ProactiveFlowComponent,
        "_commit_proactive_decision",
    )

    evaluate_proactive_reply = ComponentCall(
        "_part_proactive_flow_component",
        ProactiveFlowComponent,
        "evaluate_proactive_reply",
    )

    evaluate_idle_proactive_candidates = ComponentCall(
        "_part_proactive_flow_component",
        ProactiveFlowComponent,
        "evaluate_idle_proactive_candidates",
    )

    _evaluate_idle_candidate = ComponentCall(
        "_part_proactive_flow_component",
        ProactiveFlowComponent,
        "_evaluate_idle_candidate",
    )

    _private_revisit_memos_query = staticmethod(
        ProactiveRevisitComponent._private_revisit_memos_query
    )

    _private_revisit_evidence_scope = staticmethod(
        ProactiveRevisitComponent._private_revisit_evidence_scope
    )

    _format_private_revisit_evidence_scope = staticmethod(
        ProactiveRevisitComponent._format_private_revisit_evidence_scope
    )

    _private_revisit_relationship_snapshot = staticmethod(
        ProactiveRevisitComponent._private_revisit_relationship_snapshot
    )

    evaluate_private_revisit_candidates = ComponentCall(
        "_part_proactive_revisit_component",
        ProactiveRevisitComponent,
        "evaluate_private_revisit_candidates",
    )

    _CURRENT_APPEARANCE_PROMPT_MARKER = (
        RuntimeImageMediaComponent._CURRENT_APPEARANCE_PROMPT_MARKER
    )

    _HISTORICAL_APPEARANCE_PROMPT_MARKER = (
        RuntimeImageMediaComponent._HISTORICAL_APPEARANCE_PROMPT_MARKER
    )

    _DIRECT_IMAGE_TOOLS = RuntimeImageMediaComponent._DIRECT_IMAGE_TOOLS

    _apply_historical_appearance_snapshot = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_apply_historical_appearance_snapshot",
    )

    _active_agent_runner = classmethod(
        RuntimeImageMediaComponent._active_agent_runner.__func__
    )

    _direct_image_tool_already_sent = classmethod(
        RuntimeImageMediaComponent._direct_image_tool_already_sent.__func__
    )

    _disable_direct_image_tools_for_active_turn = classmethod(
        RuntimeImageMediaComponent._disable_direct_image_tools_for_active_turn.__func__
    )

    _duplicate_direct_image_result = classmethod(
        RuntimeImageMediaComponent._duplicate_direct_image_result.__func__
    )

    _outfit_change_scene_fallback = staticmethod(
        RuntimeImageMediaComponent._outfit_change_scene_fallback
    )

    _align_current_appearance_scene_prompt = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_align_current_appearance_scene_prompt",
    )

    _current_character_identity_profiles = staticmethod(
        RuntimeImageMediaComponent._current_character_identity_profiles
    )

    _resolve_life_identity_profiles = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_resolve_life_identity_profiles",
    )

    _current_life_appearance_snapshot = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_current_life_appearance_snapshot",
    )

    _historical_life_appearance_snapshot = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_historical_life_appearance_snapshot",
    )

    _apply_current_appearance_snapshot = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_apply_current_appearance_snapshot",
    )

    _friend_daily_look_key = staticmethod(
        RuntimeImageMediaComponent._friend_daily_look_key
    )

    _normalize_friend_look = staticmethod(
        RuntimeImageMediaComponent._normalize_friend_look
    )

    _friend_look_compatible = staticmethod(
        RuntimeImageMediaComponent._friend_look_compatible
    )

    _friend_look_default_decision = staticmethod(
        RuntimeImageMediaComponent._friend_look_default_decision
    )

    _load_friend_daily_looks = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_load_friend_daily_looks",
    )

    _prepare_friend_daily_look = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_prepare_friend_daily_look",
    )

    _friend_look_should_persist = staticmethod(
        RuntimeImageMediaComponent._friend_look_should_persist
    )

    _friend_look_parameters_result = staticmethod(
        RuntimeImageMediaComponent._friend_look_parameters_result
    )

    _friend_look_required_parameters = staticmethod(
        RuntimeImageMediaComponent._friend_look_required_parameters
    )

    _log_friend_daily_look = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_log_friend_daily_look",
    )

    _remember_friend_daily_look = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_remember_friend_daily_look",
    )

    _friend_look_prompt = staticmethod(RuntimeImageMediaComponent._friend_look_prompt)

    _image_delivery_result = staticmethod(
        RuntimeImageMediaComponent._image_delivery_result
    )

    _image_prompt_aspect_ratio = staticmethod(
        RuntimeImageMediaComponent._image_prompt_aspect_ratio
    )

    _image_prompt_resolution = staticmethod(
        RuntimeImageMediaComponent._image_prompt_resolution
    )

    _image_generation_error_needs_rewrite = staticmethod(
        RuntimeImageMediaComponent._image_generation_error_needs_rewrite
    )

    _image_policy_rejection_detail = staticmethod(
        RuntimeImageMediaComponent._image_policy_rejection_detail
    )

    _generate_life_image_with_policy_retry = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_generate_life_image_with_policy_retry",
    )

    _edit_life_image_with_policy_retry = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_edit_life_image_with_policy_retry",
    )

    note_media_source_event = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "note_media_source_event",
    )

    _image_prompt_detail_score = staticmethod(
        RuntimeImageMediaComponent._image_prompt_detail_score
    )

    _normalize_image_subject_route = staticmethod(
        RuntimeImageMediaComponent._normalize_image_subject_route
    )

    _normalize_image_participants = staticmethod(
        RuntimeImageMediaComponent._normalize_image_participants
    )

    friend_reference_injection_context = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "friend_reference_injection_context",
    )

    _event_current_image_request_text = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_event_current_image_request_text",
    )

    _event_image_prompt_text = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_event_image_prompt_text",
    )

    _resolve_image_prompt = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_resolve_image_prompt",
    )

    _image_generation_mode_label = staticmethod(
        RuntimeImageMediaComponent._image_generation_mode_label
    )

    _life_character_reference_image = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "_life_character_reference_image",
    )

    generate_life_image_asset = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "generate_life_image_asset",
    )

    life_image_generate = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "life_image_generate",
    )

    hold_life_image_final_text = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "hold_life_image_final_text",
    )

    edit_life_image = ComponentCall(
        "_part_runtime_image_media_component",
        RuntimeImageMediaComponent,
        "edit_life_image",
    )

    _photo_suite_single_shot_prompt = staticmethod(
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._photo_suite_single_shot_prompt
    )

    _disable_photo_suite_tool_for_active_turn = classmethod(
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._disable_photo_suite_tool_for_active_turn.__func__
    )

    _photo_suite_count = staticmethod(
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._photo_suite_count
    )

    _photo_suite_planning_timeout_seconds = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_planning_timeout_seconds",
    )

    _photo_suite_provider_label = staticmethod(
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._photo_suite_provider_label
    )

    _photo_suite_retry_indexes = staticmethod(
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._photo_suite_retry_indexes
    )

    _photo_suite_now = staticmethod(
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._photo_suite_now
    )

    _photo_suite_requests = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_requests",
    )

    _photo_suite_last_tasks = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_last_tasks",
    )

    _photo_suite_generation_gate = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_generation_gate",
    )

    _photo_suite_register_request = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_register_request",
    )

    _photo_suite_request_from_event = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_request_from_event",
    )

    _photo_suite_finish_request = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_finish_request",
    )

    hold_life_photo_suite_final_text = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "hold_life_photo_suite_final_text",
    )

    suppress_photo_suite_agent_error = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "suppress_photo_suite_agent_error",
    )

    _photo_suite_root = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_root",
    )

    life_photo_suite_generate = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "life_photo_suite_generate",
    )

    _photo_suite_prepare_generation = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_prepare_generation",
    )

    _photo_suite_run_generation_slots = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_run_generation_slots",
    )

    _photo_suite_available_shots = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_available_shots",
    )

    _photo_suite_finalize_manifest = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_finalize_manifest",
    )

    _photo_suite_report_delivery = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_report_delivery",
    )

    _photo_suite_handle_generation_failure = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_handle_generation_failure",
    )

    _photo_suite_generate_background = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_generate_background",
    )

    _photo_suite_generate_background_tracked = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_generate_background_tracked",
    )

    _photo_suite_generate_slot = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_generate_slot",
    )

    _photo_suite_generate_asset = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_generate_asset",
    )

    _photo_suite_plan = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_plan",
    )

    _photo_suite_parse_plan = staticmethod(
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._photo_suite_parse_plan
    )

    _photo_suite_fallback_plan = staticmethod(
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._photo_suite_fallback_plan
    )

    _photo_suite_stabilize_reference = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_stabilize_reference",
    )

    @staticmethod
    async def _photo_suite_copy_generated(
        source: _part_runtime_photo_suite_media_component.Path,
        task_dir: _part_runtime_photo_suite_media_component.Path,
        index: int,
    ) -> _part_runtime_photo_suite_media_component.Path:
        return await _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._photo_suite_copy_generated(
            source, task_dir, index
        )

    _photo_suite_send_images = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_send_images",
    )

    _photo_suite_send_followup = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_send_followup",
    )

    _photo_suite_followup_text = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_followup_text",
    )

    _photo_suite_latest_manifest = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_latest_manifest",
    )

    @staticmethod
    async def _photo_suite_read_manifest(
        path: _part_runtime_photo_suite_media_component.Path,
    ) -> dict[str, _part_runtime_photo_suite_media_component.Any] | None:
        return await _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent._photo_suite_read_manifest(
            path
        )

    _photo_suite_write_manifest = ComponentCall(
        "_part_runtime_photo_suite_media_component",
        _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent,
        "_photo_suite_write_manifest",
    )

    _reverse_prompt_clean = staticmethod(
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent._reverse_prompt_clean
    )

    _reverse_prompt_list = classmethod(
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent._reverse_prompt_list.__func__
    )

    _reverse_prompt_analysis_item_text = classmethod(
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent._reverse_prompt_analysis_item_text.__func__
    )

    _reverse_prompt_analysis_payload = classmethod(
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent._reverse_prompt_analysis_payload.__func__
    )

    _reverse_prompt_analysis_text = classmethod(
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent._reverse_prompt_analysis_text.__func__
    )

    _reverse_prompt_text_from_payload = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_reverse_prompt_text_from_payload",
    )

    _reverse_prompt_payload_from_text = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_reverse_prompt_payload_from_text",
    )

    _reverse_prompt_json_source = staticmethod(
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent._reverse_prompt_json_source
    )

    _reverse_prompt_json_candidates = staticmethod(
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent._reverse_prompt_json_candidates
    )

    _reverse_prompt_profile_instruction = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_reverse_prompt_profile_instruction",
    )

    _remember_reverse_prompt_for_scope = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_remember_reverse_prompt_for_scope",
    )

    _save_reverse_prompt_for_scope = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_save_reverse_prompt_for_scope",
    )

    _last_reverse_prompt_for_scope = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_last_reverse_prompt_for_scope",
    )

    _last_reverse_prompt_record_for_scope = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_last_reverse_prompt_record_for_scope",
    )

    _cached_reverse_prompt_for_scope = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_cached_reverse_prompt_for_scope",
    )

    _format_reverse_prompt_result = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_format_reverse_prompt_result",
    )

    _last_reverse_reference_for_scope = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_last_reverse_reference_for_scope",
    )

    _reverse_reference_cache_dir = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_reverse_reference_cache_dir",
    )

    _reverse_reference_bytes = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_reverse_reference_bytes",
    )

    _persist_reverse_reference_image = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_persist_reverse_reference_image",
    )

    _reverse_prompt_contract = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_reverse_prompt_contract",
    )

    _reverse_prompt_call_vision = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "_reverse_prompt_call_vision",
    )

    @staticmethod
    async def _reverse_prompt_call_provider(
        provider: _part_runtime_reverse_media_component.Any,
        prompt: str,
        image: str,
        session_id: str,
    ) -> _part_runtime_reverse_media_component.Any:
        return await _part_runtime_reverse_media_component.RuntimeReverseMediaComponent._reverse_prompt_call_provider(
            provider, prompt, image, session_id
        )

    life_image_reverse_prompt = ComponentCall(
        "_part_runtime_reverse_media_component",
        _part_runtime_reverse_media_component.RuntimeReverseMediaComponent,
        "life_image_reverse_prompt",
    )

    _requested_style_kinds = staticmethod(
        RuntimeStyleCatalogComponent._requested_style_kinds
    )

    _style_text = staticmethod(RuntimeStyleCatalogComponent._style_text)

    _style_list = classmethod(RuntimeStyleCatalogComponent._style_list.__func__)

    _style_item_ids = staticmethod(RuntimeStyleCatalogComponent._style_item_ids)

    _style_attributes = classmethod(
        RuntimeStyleCatalogComponent._style_attributes.__func__
    )

    _style_analysis_item = classmethod(
        RuntimeStyleCatalogComponent._style_analysis_item.__func__
    )

    _prepare_style_catalog_image_bytes = staticmethod(
        RuntimeStyleCatalogComponent._prepare_style_catalog_image_bytes
    )

    _style_perceptual_hash = staticmethod(
        RuntimeStyleCatalogComponent._style_perceptual_hash
    )

    _style_hash_distance = staticmethod(
        RuntimeStyleCatalogComponent._style_hash_distance
    )

    _learn_style_catalog_image = ComponentCall(
        "_part_runtime_style_catalog_component",
        RuntimeStyleCatalogComponent,
        "_learn_style_catalog_image",
    )

    _creative_style_prompt = ComponentCall(
        "_part_runtime_style_catalog_component",
        RuntimeStyleCatalogComponent,
        "_creative_style_prompt",
    )

    life_style_generate = ComponentCall(
        "_part_runtime_style_catalog_component",
        RuntimeStyleCatalogComponent,
        "life_style_generate",
    )

    review_style_catalog_item = ComponentCall(
        "_part_runtime_style_catalog_component",
        RuntimeStyleCatalogComponent,
        "review_style_catalog_item",
    )

    _style_catalog_result_text = staticmethod(
        RuntimeStyleCatalogComponent._style_catalog_result_text
    )

    life_style_learn = ComponentCall(
        "_part_runtime_style_catalog_component",
        RuntimeStyleCatalogComponent,
        "life_style_learn",
    )

    life_style_catalog_list = ComponentCall(
        "_part_runtime_style_catalog_component",
        RuntimeStyleCatalogComponent,
        "life_style_catalog_list",
    )

    life_style_feedback = ComponentCall(
        "_part_runtime_style_catalog_component",
        RuntimeStyleCatalogComponent,
        "life_style_feedback",
    )

    _VIDEO_REFERENCE_SOURCE_ATTR = (
        RuntimeVideoMediaComponent._VIDEO_REFERENCE_SOURCE_ATTR
    )

    note_life_video_reference_source = ComponentCall(
        "_part_runtime_video_media_component",
        RuntimeVideoMediaComponent,
        "note_life_video_reference_source",
    )

    generate_life_video_asset = ComponentCall(
        "_part_runtime_video_media_component",
        RuntimeVideoMediaComponent,
        "generate_life_video_asset",
    )

    life_video_generate = ComponentCall(
        "_part_runtime_video_media_component",
        RuntimeVideoMediaComponent,
        "life_video_generate",
    )

    _resolve_life_video_commitment_future = staticmethod(
        RuntimeVideoMediaComponent._resolve_life_video_commitment_future
    )

    _life_video_failure_fallback_text = staticmethod(
        RuntimeVideoMediaComponent._life_video_failure_fallback_text
    )

    hold_life_video_final_text = ComponentCall(
        "_part_runtime_video_media_component",
        RuntimeVideoMediaComponent,
        "hold_life_video_final_text",
    )

    _parse_life_video_reply_text = staticmethod(
        RuntimeVideoMediaComponent._parse_life_video_reply_text
    )

    life_voice_generate = ComponentCall(
        "_part_runtime_voice_media_component",
        RuntimeVoiceMediaComponent,
        "life_voice_generate",
    )

    _media_keep_days = staticmethod(MediaFileCleanupComponent._media_keep_days)

    maintain_plugin_file_cache = ComponentCall(
        "_part_media_file_cleanup_component",
        MediaFileCleanupComponent,
        "maintain_plugin_file_cache",
    )

    _prune_media_cache_tree = staticmethod(
        MediaFileCleanupComponent._prune_media_cache_tree
    )

    _prune_media_cache_files = staticmethod(
        MediaFileCleanupComponent._prune_media_cache_files
    )

    _prune_media_cache_dirs = staticmethod(
        MediaFileCleanupComponent._prune_media_cache_dirs
    )

    _MEDIA_CADENCE_TTL_SECONDS = RuntimeMediaCommonComponent._MEDIA_CADENCE_TTL_SECONDS

    _background_tool_request = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_background_tool_request",
    )

    _background_tool_existing_result = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_background_tool_existing_result",
    )

    _submit_background_tool = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_submit_background_tool",
    )

    hold_background_tool_final_text = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "hold_background_tool_final_text",
    )

    _release_media_chat_turn = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_release_media_chat_turn",
    )

    _snapshot_media_event = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_snapshot_media_event",
    )

    _parse_delivered_media_reply = staticmethod(
        RuntimeMediaCommonComponent._parse_delivered_media_reply
    )

    _send_background_failure_reply = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_send_background_failure_reply",
    )

    _send_delivered_media_followup = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_send_delivered_media_followup",
    )

    _localized_error_name = staticmethod(
        RuntimeMediaCommonComponent._localized_error_name
    )

    _media_elapsed_text = staticmethod(RuntimeMediaCommonComponent._media_elapsed_text)

    _media_size_text = staticmethod(RuntimeMediaCommonComponent._media_size_text)

    _media_result_summary = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_media_result_summary",
    )

    _media_error_summary = staticmethod(
        RuntimeMediaCommonComponent._media_error_summary
    )

    _media_tool_failure_text = staticmethod(
        RuntimeMediaCommonComponent._media_tool_failure_text
    )

    note_life_media_sent = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "note_life_media_sent",
    )

    _hidden_media_cadence_hint = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_hidden_media_cadence_hint",
    )

    _media_payload_from_item_async = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_media_payload_from_item_async",
    )

    _image_reference_from_items_async = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_image_reference_from_items_async",
    )

    _resolve_life_image_reference_async = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_resolve_life_image_reference_async",
    )

    _life_image_reference_is_usable = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_life_image_reference_is_usable",
    )

    _resolve_life_image_reference = ComponentCall(
        "_part_runtime_media_common_component",
        RuntimeMediaCommonComponent,
        "_resolve_life_image_reference",
    )

    _media_director_current_day = ComponentCall(
        "_part_stage_frame_component",
        StageFrameComponent,
        "_media_director_current_day",
    )

    _media_director_context_text = ComponentCall(
        "_part_stage_frame_component",
        StageFrameComponent,
        "_media_director_context_text",
    )

    _media_director_compact_text = staticmethod(
        StageFrameComponent._media_director_compact_text
    )

    _media_director_format_emotion_arcs = classmethod(
        StageFrameComponent._media_director_format_emotion_arcs.__func__
    )

    _media_director_emotion_context_text = ComponentCall(
        "_part_stage_frame_component",
        StageFrameComponent,
        "_media_director_emotion_context_text",
    )

    _media_director_recent_context_text = ComponentCall(
        "_part_stage_frame_component",
        StageFrameComponent,
        "_media_director_recent_context_text",
    )

    _media_director_message_label = staticmethod(
        StageFrameComponent._media_director_message_label
    )

    _media_director_message_content = staticmethod(
        StageFrameComponent._media_director_message_content
    )

    _media_director_format_recent_messages = staticmethod(
        StageFrameComponent._media_director_format_recent_messages
    )

    _character_appearance_prompt = staticmethod(
        StageFigureComponent._character_appearance_prompt
    )

    _character_appearance_fingerprint = staticmethod(
        StageFigureComponent._character_appearance_fingerprint
    )

    _remember_character_appearance_profile = ComponentCall(
        "_part_stage_figure_component",
        StageFigureComponent,
        "_remember_character_appearance_profile",
    )

    _character_appearance_context = ComponentCall(
        "_part_stage_figure_component",
        StageFigureComponent,
        "_character_appearance_context",
    )

    _character_appearance_profile = ComponentCall(
        "_part_stage_figure_component",
        StageFigureComponent,
        "_character_appearance_profile",
    )

    _close_character_appearance_tasks = ComponentCall(
        "_part_stage_figure_component",
        StageFigureComponent,
        "_close_character_appearance_tasks",
    )

    _media_image_director_provider_id = ComponentCall(
        "_part_stage_lens_component",
        StageLensComponent,
        "_media_image_director_provider_id",
    )

    _media_director_text_call = ComponentCall(
        "_part_stage_lens_component", StageLensComponent, "_media_director_text_call"
    )

    _media_director_call = ComponentCall(
        "_part_stage_lens_component", StageLensComponent, "_media_director_call"
    )

    _IMAGE_IDENTITY_CHARACTER = StageReelComponent._IMAGE_IDENTITY_CHARACTER

    _IMAGE_IDENTITY_NONE = StageReelComponent._IMAGE_IDENTITY_NONE

    _IMAGE_IDENTITY_UNKNOWN = StageReelComponent._IMAGE_IDENTITY_UNKNOWN

    _IMAGE_IDENTITY_ROUTES = StageReelComponent._IMAGE_IDENTITY_ROUTES

    _IMAGE_SUBJECT_KINDS = StageReelComponent._IMAGE_SUBJECT_KINDS

    _IMAGE_PERSON_SUBJECT_KINDS = StageReelComponent._IMAGE_PERSON_SUBJECT_KINDS

    _media_image_subject_kind = staticmethod(
        StageReelComponent._media_image_subject_kind
    )

    _validate_media_image_route_payload = classmethod(
        StageReelComponent._validate_media_image_route_payload.__func__
    )

    _media_image_preserve_user_request = staticmethod(
        StageReelComponent._media_image_preserve_user_request
    )

    _media_image_identity_route = staticmethod(
        StageReelComponent._media_image_identity_route
    )

    _media_image_contains_character = staticmethod(
        StageReelComponent._media_image_contains_character
    )

    _media_image_needs_character_reference = staticmethod(
        StageReelComponent._media_image_needs_character_reference
    )

    _strip_media_code_fence = staticmethod(StageReelComponent._strip_media_code_fence)

    _media_rewrite_prompt_from_response = ComponentCall(
        "_part_stage_reel_component",
        StageReelComponent,
        "_media_rewrite_prompt_from_response",
    )

    _direct_life_image_payload = ComponentCall(
        "_part_stage_reel_component", StageReelComponent, "_direct_life_image_payload"
    )

    _rewrite_life_image_prompt_for_policy_retry = ComponentCall(
        "_part_stage_reel_component",
        StageReelComponent,
        "_rewrite_life_image_prompt_for_policy_retry",
    )

    _direct_life_video_prompt = ComponentCall(
        "_part_stage_voice_component", StageVoiceComponent, "_direct_life_video_prompt"
    )

    _direct_life_video_plan = ComponentCall(
        "_part_stage_voice_component", StageVoiceComponent, "_direct_life_video_plan"
    )

    _remember_life_image_for_scope = ComponentCall(
        "_part_stage_album_component",
        StageAlbumComponent,
        "_remember_life_image_for_scope",
    )

    _last_generated_life_image_path = ComponentCall(
        "_part_stage_album_component",
        StageAlbumComponent,
        "_last_generated_life_image_path",
    )

    _forget_last_generated_life_image_path = ComponentCall(
        "_part_stage_album_component",
        StageAlbumComponent,
        "_forget_last_generated_life_image_path",
    )

    apply_voice_switch_before_send = ComponentCall(
        "_part_voice_switch_component",
        VoiceSwitchComponent,
        "apply_voice_switch_before_send",
    )

    note_voice_switch_message_sent = ComponentCall(
        "_part_voice_switch_component",
        VoiceSwitchComponent,
        "note_voice_switch_message_sent",
    )

    _VOICE_SWITCH_CADENCE_TTL_SECONDS = (
        VoiceSwitchGateComponent._VOICE_SWITCH_CADENCE_TTL_SECONDS
    )

    _VOICE_SWITCH_COOLDOWN_SECONDS = (
        VoiceSwitchGateComponent._VOICE_SWITCH_COOLDOWN_SECONDS
    )

    _VOICE_SWITCH_CHAIN_WINDOW_SECONDS = (
        VoiceSwitchGateComponent._VOICE_SWITCH_CHAIN_WINDOW_SECONDS
    )

    _VOICE_SWITCH_MAX_CONSECUTIVE_VOICE = (
        VoiceSwitchGateComponent._VOICE_SWITCH_MAX_CONSECUTIVE_VOICE
    )

    _VOICE_SWITCH_CHAIN_LIMIT_CHOICES = (
        VoiceSwitchGateComponent._VOICE_SWITCH_CHAIN_LIMIT_CHOICES
    )

    _VOICE_SWITCH_TEXT_TURNS_AFTER_VOICE = (
        VoiceSwitchGateComponent._VOICE_SWITCH_TEXT_TURNS_AFTER_VOICE
    )

    _VOICE_SWITCH_LONG_TEXT_LIMIT = (
        VoiceSwitchGateComponent._VOICE_SWITCH_LONG_TEXT_LIMIT
    )

    _VOICE_SWITCH_LONG_LINE_LIMIT = (
        VoiceSwitchGateComponent._VOICE_SWITCH_LONG_LINE_LIMIT
    )

    _VOICE_SWITCH_MAX_SHORT_LINES = (
        VoiceSwitchGateComponent._VOICE_SWITCH_MAX_SHORT_LINES
    )

    _VOICE_SWITCH_MIN_CONFIDENCE = VoiceSwitchGateComponent._VOICE_SWITCH_MIN_CONFIDENCE

    _voice_switch_scope_key = ComponentCall(
        "_part_voice_switch_gate_component",
        VoiceSwitchGateComponent,
        "_voice_switch_scope_key",
    )

    _voice_switch_round_store = ComponentCall(
        "_part_voice_switch_gate_component",
        VoiceSwitchGateComponent,
        "_voice_switch_round_store",
    )

    _prune_voice_switch_rounds = ComponentCall(
        "_part_voice_switch_gate_component",
        VoiceSwitchGateComponent,
        "_prune_voice_switch_rounds",
    )

    _voice_switch_cadence_snapshot = ComponentCall(
        "_part_voice_switch_gate_component",
        VoiceSwitchGateComponent,
        "_voice_switch_cadence_snapshot",
    )

    _mark_voice_switch_channel = ComponentCall(
        "_part_voice_switch_gate_component",
        VoiceSwitchGateComponent,
        "_mark_voice_switch_channel",
    )

    _voice_switch_auto_gate = ComponentCall(
        "_part_voice_switch_gate_component",
        VoiceSwitchGateComponent,
        "_voice_switch_auto_gate",
    )

    _voice_switch_text_too_dense_for_voice = ComponentCall(
        "_part_voice_switch_gate_component",
        VoiceSwitchGateComponent,
        "_voice_switch_text_too_dense_for_voice",
    )

    mark_voice_switch_available = ComponentCall(
        "_part_voice_switch_gate_component",
        VoiceSwitchGateComponent,
        "mark_voice_switch_available",
    )

    mark_voice_switch_used = ComponentCall(
        "_part_voice_switch_gate_component",
        VoiceSwitchGateComponent,
        "mark_voice_switch_used",
    )

    _tool_name = classmethod(SilentToolPrefaceComponent._tool_name.__func__)

    _copy_result_chain = staticmethod(SilentToolPrefaceComponent._copy_result_chain)

    _normalize_visible_text = staticmethod(
        SilentToolPrefaceComponent._normalize_visible_text
    )

    _same_visible_text = classmethod(
        SilentToolPrefaceComponent._same_visible_text.__func__
    )

    _plain_send_message_tool_text = staticmethod(
        SilentToolPrefaceComponent._plain_send_message_tool_text
    )

    _plain_tool_preface_text = staticmethod(
        SilentToolPrefaceComponent._plain_tool_preface_text
    )

    handle_llm_tool_start = ComponentCall(
        "_part_silent_tool_preface_component",
        SilentToolPrefaceComponent,
        "handle_llm_tool_start",
    )

    should_skip_duplicate_send_message = ComponentCall(
        "_part_silent_tool_preface_component",
        SilentToolPrefaceComponent,
        "should_skip_duplicate_send_message",
    )

    handle_llm_tool_respond = ComponentCall(
        "_part_silent_tool_preface_component",
        SilentToolPrefaceComponent,
        "handle_llm_tool_respond",
    )

    mark_tool_outcome = ComponentCall(
        "_part_silent_tool_preface_component",
        SilentToolPrefaceComponent,
        "mark_tool_outcome",
    )

    note_tool_final_response = ComponentCall(
        "_part_silent_tool_preface_component",
        SilentToolPrefaceComponent,
        "note_tool_final_response",
    )

    suppress_final_silent_tool_result = ComponentCall(
        "_part_silent_tool_preface_component",
        SilentToolPrefaceComponent,
        "suppress_final_silent_tool_result",
    )

    _follow_up_module = staticmethod(SilentToolPrefaceComponent._follow_up_module)

    _voice_switch_reply_text_from_event = staticmethod(
        SilentToolPrefaceComponent._voice_switch_reply_text_from_event
    )

    suppress_intermediate_tool_result = ComponentCall(
        "_part_silent_tool_preface_component",
        SilentToolPrefaceComponent,
        "suppress_intermediate_tool_result",
    )

    _is_active_agent_intermediate_result = ComponentCall(
        "_part_silent_tool_preface_component",
        SilentToolPrefaceComponent,
        "_is_active_agent_intermediate_result",
    )

    _coerce_tool_names = classmethod(
        SilentToolPrefaceComponent._coerce_tool_names.__func__
    )

    _is_llm_result_object = staticmethod(
        SilentToolPrefaceComponent._is_llm_result_object
    )

    _runner_is_done = staticmethod(SilentToolPrefaceComponent._runner_is_done)

    _VOICE_SWITCH_MIN_NATURAL_SCORE = (
        VoiceSwitchJudgeComponent._VOICE_SWITCH_MIN_NATURAL_SCORE
    )

    _judge_voice_switch_channel = ComponentCall(
        "_part_voice_switch_judge_component",
        VoiceSwitchJudgeComponent,
        "_judge_voice_switch_channel",
    )

    _voice_switch_text_decision = staticmethod(
        VoiceSwitchJudgeComponent._voice_switch_text_decision
    )

    _voice_switch_structural_text_reason = ComponentCall(
        "_part_voice_switch_judge_component",
        VoiceSwitchJudgeComponent,
        "_voice_switch_structural_text_reason",
    )

    _voice_switch_has_path_shape = staticmethod(
        VoiceSwitchJudgeComponent._voice_switch_has_path_shape
    )

    _voice_switch_has_list_shape = staticmethod(
        VoiceSwitchJudgeComponent._voice_switch_has_list_shape
    )

    _voice_switch_has_dense_foreign_shape = staticmethod(
        VoiceSwitchJudgeComponent._voice_switch_has_dense_foreign_shape
    )

    _voice_switch_has_dense_number_shape = staticmethod(
        VoiceSwitchJudgeComponent._voice_switch_has_dense_number_shape
    )

    _voice_record_component = staticmethod(
        VoiceSwitchRecordComponent._voice_record_component
    )

    _strip_voice_record_caption = staticmethod(
        VoiceSwitchRecordComponent._strip_voice_record_caption
    )

    _replace_result_with_voice = ComponentCall(
        "_part_voice_switch_record_component",
        VoiceSwitchRecordComponent,
        "_replace_result_with_voice",
    )

    note_voice_switch_text_result = ComponentCall(
        "_part_voice_switch_record_component",
        VoiceSwitchRecordComponent,
        "note_voice_switch_text_result",
    )

    _voice_expression_log_title = staticmethod(
        VoiceSwitchRecordComponent._voice_expression_log_title
    )

    _note_voice_expression_decision = ComponentCall(
        "_part_voice_switch_record_component",
        VoiceSwitchRecordComponent,
        "_note_voice_expression_decision",
    )

    _OBSERVED_USER_HISTORY_MARKER = (
        RuntimeHistoryComponent._OBSERVED_USER_HISTORY_MARKER
    )

    _WEEKDAY_NAMES = RuntimeHistoryComponent._WEEKDAY_NAMES

    _astrbot_now_for_scope = ComponentCall(
        "_part_runtime_history_component",
        RuntimeHistoryComponent,
        "_astrbot_now_for_scope",
    )

    _history_text_from_value = staticmethod(
        _part_history_parse_component.HistoryParseComponent._history_text_from_value
    )

    _history_text_from_message_item = classmethod(
        _part_history_parse_component.HistoryParseComponent._history_text_from_message_item.__func__
    )

    _history_text_from_message_items = classmethod(
        _part_history_parse_component.HistoryParseComponent._history_text_from_message_items.__func__
    )

    _history_primary_text = classmethod(
        _part_history_parse_component.HistoryParseComponent._history_primary_text.__func__
    )

    _event_user_history_text = ComponentCall(
        "_part_history_parse_component",
        _part_history_parse_component.HistoryParseComponent,
        "_event_user_history_text",
    )

    _conversation_history_list = staticmethod(
        _part_history_parse_component.HistoryParseComponent._conversation_history_list
    )

    _history_item_matches = staticmethod(
        _part_history_parse_component.HistoryParseComponent._history_item_matches
    )

    _history_user_item_matches = staticmethod(
        _part_history_parse_component.HistoryParseComponent._history_user_item_matches
    )

    _history_content_has_media = staticmethod(
        _part_history_parse_component.HistoryParseComponent._history_content_has_media
    )

    _history_message_parts_for_user = staticmethod(
        _part_history_parse_component.HistoryParseComponent._history_message_parts_for_user
    )

    @staticmethod
    async def _history_display_url(path: str) -> str:
        return await _part_history_parse_component.HistoryParseComponent._history_display_url(
            path
        )

    _platform_media_part_from_history_payload = ComponentCall(
        "_part_history_parse_component",
        _part_history_parse_component.HistoryParseComponent,
        "_platform_media_part_from_history_payload",
    )

    @classmethod
    async def _history_media_parts_from_event(
        cls, event: _part_history_parse_component.Any
    ) -> list[dict[str, _part_history_parse_component.Any]]:
        return await _part_history_parse_component.HistoryParseComponent._history_media_parts_from_event.__func__(
            cls, event
        )

    _history_media_type = staticmethod(
        _part_history_parse_component.HistoryParseComponent._history_media_type
    )

    @staticmethod
    async def _history_media_payload(
        item: _part_history_parse_component.Any, media_type: str
    ) -> dict[str, _part_history_parse_component.Any]:
        return await _part_history_parse_component.HistoryParseComponent._history_media_payload(
            item, media_type
        )

    _ASTRBOT_WEEKDAY_NAMES = HistorySceneComponent._ASTRBOT_WEEKDAY_NAMES

    _astrbot_config = ComponentCall(
        "_part_history_scene_component", HistorySceneComponent, "_astrbot_config"
    )

    inject_astrbot_history_reminder = ComponentCall(
        "_part_history_scene_component",
        HistorySceneComponent,
        "inject_astrbot_history_reminder",
    )

    _user_history_item_from_event = ComponentCall(
        "_part_history_scene_component",
        HistorySceneComponent,
        "_user_history_item_from_event",
    )

    _event_user_history_media_label = ComponentCall(
        "_part_history_watch_component",
        HistoryWatchComponent,
        "_event_user_history_media_label",
    )

    record_observed_private_user_message = ComponentCall(
        "_part_history_watch_component",
        HistoryWatchComponent,
        "record_observed_private_user_message",
    )

    _append_assistant_history = ComponentCall(
        "_part_history_write_component",
        HistoryWriteComponent,
        "_append_assistant_history",
    )

    _append_user_history = ComponentCall(
        "_part_history_write_component", HistoryWriteComponent, "_append_user_history"
    )

    _append_platform_user_history = ComponentCall(
        "_part_history_write_component",
        HistoryWriteComponent,
        "_append_platform_user_history",
    )

    _append_turn_history = ComponentCall(
        "_part_history_write_component", HistoryWriteComponent, "_append_turn_history"
    )

    _append_proactive_send_history = ComponentCall(
        "_part_history_write_component",
        HistoryWriteComponent,
        "_append_proactive_send_history",
    )

    config = _part_spine_boot_component.SpineBootComponent.config

    media = _part_spine_boot_component.SpineBootComponent.media

    memos = _part_spine_boot_component.SpineBootComponent.memos

    contact_resolver = _part_spine_boot_component.SpineBootComponent.contact_resolver

    weather_client = _part_spine_boot_component.SpineBootComponent.weather_client

    search = _part_spine_boot_component.SpineBootComponent.search

    domains = _part_spine_boot_component.SpineBootComponent.domains

    composer = _part_spine_boot_component.SpineBootComponent.composer

    model_gateway = _part_spine_boot_component.SpineBootComponent.model_gateway

    rhythm = _part_spine_boot_component.SpineBootComponent.rhythm

    __init__ = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "initialize_runtime",
    )

    initialize = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "initialize",
    )

    _platform_instances = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_platform_instances",
    )

    _platform_type = staticmethod(
        _part_spine_boot_component.SpineBootComponent._platform_type
    )

    _platform_client = staticmethod(
        _part_spine_boot_component.SpineBootComponent._platform_client
    )

    _is_platform_ready = classmethod(
        _part_spine_boot_component.SpineBootComponent._is_platform_ready.__func__
    )

    _manager_has_configured_platform = staticmethod(
        _part_spine_boot_component.SpineBootComponent._manager_has_configured_platform
    )

    wait_for_platform_ready = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "wait_for_platform_ready",
    )

    _runtime_data_path = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_runtime_data_path",
    )

    _build_runtime_services = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_build_runtime_services",
    )

    _install_runtime_services = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_install_runtime_services",
    )

    _current_runtime_services = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_current_runtime_services",
    )

    _bind_runtime = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_bind_runtime",
    )

    _leased_rhythm_callback = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_leased_rhythm_callback",
    )

    _schedule_durable_task_pump = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_schedule_durable_task_pump",
    )

    _durable_task_key = staticmethod(
        _part_spine_boot_component.SpineBootComponent._durable_task_key
    )

    _durable_retry_at = staticmethod(
        _part_spine_boot_component.SpineBootComponent._durable_retry_at
    )

    _run_durable_tasks_once = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_run_durable_tasks_once",
    )

    _run_durable_lane = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_run_durable_lane",
    )

    _run_durable_handler = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_run_durable_handler",
    )

    _drain_durable_tasks = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_drain_durable_tasks",
    )

    _run_durable_task_worker = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_run_durable_task_worker",
    )

    _build_rhythm = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_build_rhythm",
    )

    _runtime_service_condition = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_runtime_service_condition",
    )

    _runtime_service_lease_depth = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_runtime_service_lease_depth",
    )

    _runtime_service_lease_owner = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_runtime_service_lease_owner",
    )

    _runtime_service_binding = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_runtime_service_binding",
    )

    _retire_runtime_services = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_retire_runtime_services",
    )

    runtime_service_lease = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "runtime_service_lease",
    )

    _begin_runtime_service_swap = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_begin_runtime_service_swap",
    )

    _end_runtime_service_swap = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_end_runtime_service_swap",
    )

    @staticmethod
    async def _close_runtime_services(
        services: _part_spine_boot_component.RuntimeServices,
    ) -> None:
        return (
            await _part_spine_boot_component.SpineBootComponent._close_runtime_services(
                services
            )
        )

    @staticmethod
    async def _close_runtime_component(
        service: _part_spine_boot_component.Any,
        label: str,
        *,
        method_names: tuple[str, ...] = ("close",),
        thread_sync: bool = False,
    ) -> None:
        return await _part_spine_boot_component.SpineBootComponent._close_runtime_component(
            service, label, method_names=method_names, thread_sync=thread_sync
        )

    _log_boot_summary = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "_log_boot_summary",
    )

    begin_shutdown = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "begin_shutdown",
    )

    terminate = ComponentCall(
        "_part_spine_boot_component",
        _part_spine_boot_component.SpineBootComponent,
        "terminate",
    )

    run_daily_refresh = ComponentCall(
        "_part_spine_pulse_component", SpinePulseComponent, "run_daily_refresh"
    )

    run_nightly_review = ComponentCall(
        "_part_spine_pulse_component", SpinePulseComponent, "run_nightly_review"
    )

    run_memory_maintenance = ComponentCall(
        "_part_spine_pulse_component", SpinePulseComponent, "run_memory_maintenance"
    )

    run_weekly_refresh = ComponentCall(
        "_part_spine_pulse_component", SpinePulseComponent, "run_weekly_refresh"
    )

    run_private_revisit_check = ComponentCall(
        "_part_spine_pulse_component", SpinePulseComponent, "run_private_revisit_check"
    )

    run_proactive_idle_check = ComponentCall(
        "_part_spine_pulse_component", SpinePulseComponent, "run_proactive_idle_check"
    )

    _get_curr_period = ComponentCall(
        "_part_spine_adapt_component", SpineAdaptComponent, "_get_curr_period"
    )

    _runtime_now = staticmethod(SpineAdaptComponent._runtime_now)

    _persist_schedule_time = ComponentCall(
        "_part_spine_adapt_component", SpineAdaptComponent, "_persist_schedule_time"
    )

    _rhythm_running = staticmethod(SpineAdaptComponent._rhythm_running)

    _residence_address = staticmethod(SpineAdaptComponent._residence_address)

    _prepare_residence_change = ComponentCall(
        "_part_spine_adapt_component", SpineAdaptComponent, "_prepare_residence_change"
    )

    _refresh_after_residence_change = ComponentCall(
        "_part_spine_adapt_component",
        SpineAdaptComponent,
        "_refresh_after_residence_change",
    )

    apply_config = ComponentCall(
        "_part_spine_adapt_component", SpineAdaptComponent, "apply_config"
    )

    _resolve_command_target_date = ComponentCall(
        "_part_spine_adapt_component",
        SpineAdaptComponent,
        "_resolve_command_target_date",
    )

    _target_datetime_for_command = staticmethod(
        SpineAdaptComponent._target_datetime_for_command
    )

    try_update_weather = ComponentCall(
        "_part_spine_climate_component", SpineClimateComponent, "try_update_weather"
    )

    sync_outfit_after_invite = ComponentCall(
        "_part_spine_invite_component", SpineInviteComponent, "sync_outfit_after_invite"
    )

    schedule_invite_outfit_sync = ComponentCall(
        "_part_spine_invite_component",
        SpineInviteComponent,
        "schedule_invite_outfit_sync",
    )

    _commitment_reconcile_markers = staticmethod(
        SpineInviteComponent._commitment_reconcile_markers
    )

    _commitment_reconcile_signature = staticmethod(
        SpineInviteComponent._commitment_reconcile_signature
    )

    _commitment_reconcile_marker_value = staticmethod(
        SpineInviteComponent._commitment_reconcile_marker_value
    )

    _store_commitment_reconcile_marker = classmethod(
        SpineInviteComponent._store_commitment_reconcile_marker.__func__
    )

    _outfit_instruction_is_due = staticmethod(
        SpineInviteComponent._outfit_instruction_is_due
    )

    apply_commitment_to_current_day = ComponentCall(
        "_part_spine_invite_component",
        SpineInviteComponent,
        "apply_commitment_to_current_day",
    )

    reconcile_due_commitments_for_day = ComponentCall(
        "_part_spine_invite_component",
        SpineInviteComponent,
        "reconcile_due_commitments_for_day",
    )

    accept_user_invite = ComponentCall(
        "_part_spine_invite_component", SpineInviteComponent, "accept_user_invite"
    )

    add_memo_for_tomorrow = ComponentCall(
        "_part_spine_invite_component", SpineInviteComponent, "add_memo_for_tomorrow"
    )

    @cached_property
    def _part_memos_component(self):
        return MemosComponent(self)

    @cached_property
    def _part_chat_memory_batch_component(self):
        return ChatMemoryBatchComponent(self)

    @cached_property
    def _part_chat_execution_component(self):
        return ChatExecutionComponent(self)

    @cached_property
    def _part_expression_learning_component(self):
        return ExpressionLearningComponent(self)

    @cached_property
    def _part_capture_event_component(self):
        return CaptureEventComponent(self)

    @cached_property
    def _part_capture_payload_component(self):
        return CapturePayloadComponent(self)


    @cached_property
    def _part_capture_persona_component(self):
        return CapturePersonaComponent(self)

    @cached_property
    def _part_packet_component(self):
        return PacketComponent(self)

    @cached_property
    def _part_target_component(self):
        return TargetComponent(self)

    @cached_property
    def _part_profile_component(self):
        return ProfileComponent(self)

    @cached_property
    def _part_awareness_component(self):
        return AwarenessComponent(self)

    @cached_property
    def _part_imprint_deposit_component(self):
        return ImprintDepositComponent(self)

    @cached_property
    def _part_imprint_portrait_component(self):
        return ImprintPortraitComponent(self)

    @cached_property
    def _part_imprint_harvest_component(self):
        return ImprintHarvestComponent(self)

    @cached_property
    def _part_emoji_base_component(self):
        return EmojiBaseComponent(self)

    @cached_property
    def _part_emoji_cache_component(self):
        return _part_emoji_cache_component.EmojiCacheComponent(self)

    @cached_property
    def _part_emoji_sweep_component(self):
        return EmojiSweepComponent(self)

    @cached_property
    def _part_emoji_vision_component(self):
        return _part_emoji_vision_component.EmojiVisionComponent(self)

    @cached_property
    def _part_emoji_gather_component(self):
        return EmojiGatherComponent(self)

    @cached_property
    def _part_structured_base_component(self):
        return StructuredBaseComponent(self)

    @cached_property
    def _part_structured_dock_component(self):
        return StructuredDockComponent(self)

    @cached_property
    def _part_structured_route_component(self):
        return StructuredRouteComponent(self)

    @cached_property
    def _part_structured_forge_component(self):
        return StructuredForgeComponent(self)

    @cached_property
    def _part_structured_glance_component(self):
        return StructuredGlanceComponent(self)

    @cached_property
    def _part_sight_component(self):
        return _part_sight_component.SightComponent(self)

    @cached_property
    def _part_native_sight_component(self):
        return NativeSightComponent(self)

    @cached_property
    def _part_sight_cleanup_component(self):
        return SightCleanupComponent(self)

    @cached_property
    def _part_sight_identity_component(self):
        return SightIdentityComponent(self)

    @cached_property
    def _part_continuous_turn_component(self):
        return ContinuousTurnComponent(self)

    @cached_property
    def _part_response_gate_state_component(self):
        return ResponseGateStateComponent(self)

    @cached_property
    def _part_response_gate_sense_component(self):
        return ResponseGateSenseComponent(self)

    @cached_property
    def _part_response_gate_event_component(self):
        return ResponseGateEventComponent(self)

    @cached_property
    def _part_response_gate_score_component(self):
        return ResponseGateScoreComponent(self)

    @cached_property
    def _part_response_gate_apply_component(self):
        return ResponseGateApplyComponent(self)

    @cached_property
    def _part_refresh_component(self):
        return RefreshComponent(self)

    @cached_property
    def _part_runtime_action_receipt_component(self):
        return RuntimeActionReceiptComponent(self)

    @cached_property
    def _part_chat_style_runtime_component(self):
        return ChatStyleRuntimeComponent(self)

    @cached_property
    def _part_chat_delay_component(self):
        return ChatDelayComponent(self)

    @cached_property
    def _part_semantic_segment_runtime_component(self):
        return SemanticSegmentRuntimeComponent(self)

    @cached_property
    def _part_inject_component(self):
        return InjectComponent(self)

    @cached_property
    def _part_snapshot_tempo_component(self):
        return SnapshotTempoComponent(self)

    @cached_property
    def _part_snapshot_export_component(self):
        return SnapshotExportComponent(self)

    @cached_property
    def _part_snapshot_seed_component(self):
        return SnapshotSeedComponent(self)

    @cached_property
    def _part_snapshot_chat_component(self):
        return SnapshotChatComponent(self)

    @cached_property
    def _part_snapshot_pack_component(self):
        return SnapshotPackComponent(self)

    @cached_property
    def _part_hidden_experience_component(self):
        return HiddenExperienceComponent(self)

    @cached_property
    def _part_layer_text_component(self):
        return LayerTextComponent(self)

    @cached_property
    def _part_layer_chain_component(self):
        return LayerChainComponent(self)

    @cached_property
    def _part_request_prefix_component(self):
        return RequestPrefixComponent(self)

    @cached_property
    def _part_proactive_followup_component(self):
        return ProactiveFollowupComponent(self)

    @cached_property
    def _part_shared_activity_contact_component(self):
        return SharedActivityContactComponent(self)

    @cached_property
    def _part_proactive_lifecycle_component(self):
        return ProactiveLifecycleComponent(self)

    @cached_property
    def _part_air_meter_component(self):
        return AirMeterComponent(self)

    @cached_property
    def _part_air_effect_component(self):
        return AirEffectComponent(self)

    @cached_property
    def _part_proactive_context_component(self):
        return ProactiveContextComponent(self)

    @cached_property
    def _part_proactive_gesture_component(self):
        return ProactiveGestureComponent(self)

    @cached_property
    def _part_proactive_segment_component(self):
        return ProactiveSegmentComponent(self)

    @cached_property
    def _part_proactive_scope_component(self):
        return ProactiveScopeComponent(self)

    @cached_property
    def _part_proactive_send_component(self):
        return ProactiveSendComponent(self)

    @cached_property
    def _part_proactive_aware_component(self):
        return ProactiveAwareComponent(self)

    @cached_property
    def _part_proactive_prompt_component(self):
        return ProactivePromptComponent(self)

    @cached_property
    def _part_proactive_store_component(self):
        return ProactiveStoreComponent(self)

    @cached_property
    def _part_proactive_flow_component(self):
        return ProactiveFlowComponent(self)

    @cached_property
    def _part_proactive_revisit_component(self):
        return ProactiveRevisitComponent(self)

    @cached_property
    def _part_runtime_image_media_component(self):
        return RuntimeImageMediaComponent(self)

    @cached_property
    def _part_runtime_photo_suite_media_component(self):
        return (
            _part_runtime_photo_suite_media_component.RuntimePhotoSuiteMediaComponent(
                self
            )
        )

    @cached_property
    def _part_runtime_reverse_media_component(self):
        return _part_runtime_reverse_media_component.RuntimeReverseMediaComponent(self)

    @cached_property
    def _part_runtime_style_catalog_component(self):
        return RuntimeStyleCatalogComponent(self)

    @cached_property
    def _part_runtime_video_media_component(self):
        return RuntimeVideoMediaComponent(self)

    @cached_property
    def _part_runtime_voice_media_component(self):
        return RuntimeVoiceMediaComponent(self)

    @cached_property
    def _part_media_file_cleanup_component(self):
        return MediaFileCleanupComponent(self)

    @cached_property
    def _part_runtime_media_common_component(self):
        return RuntimeMediaCommonComponent(self)

    @cached_property
    def _part_stage_frame_component(self):
        return StageFrameComponent(self)

    @cached_property
    def _part_stage_figure_component(self):
        return StageFigureComponent(self)

    @cached_property
    def _part_stage_lens_component(self):
        return StageLensComponent(self)

    @cached_property
    def _part_stage_reel_component(self):
        return StageReelComponent(self)

    @cached_property
    def _part_stage_voice_component(self):
        return StageVoiceComponent(self)

    @cached_property
    def _part_stage_album_component(self):
        return StageAlbumComponent(self)

    @cached_property
    def _part_voice_switch_component(self):
        return VoiceSwitchComponent(self)

    @cached_property
    def _part_voice_switch_gate_component(self):
        return VoiceSwitchGateComponent(self)

    @cached_property
    def _part_silent_tool_preface_component(self):
        return SilentToolPrefaceComponent(self)

    @cached_property
    def _part_voice_switch_judge_component(self):
        return VoiceSwitchJudgeComponent(self)

    @cached_property
    def _part_voice_switch_record_component(self):
        return VoiceSwitchRecordComponent(self)

    @cached_property
    def _part_runtime_history_component(self):
        return RuntimeHistoryComponent(self)

    @cached_property
    def _part_history_parse_component(self):
        return _part_history_parse_component.HistoryParseComponent(self)

    @cached_property
    def _part_history_scene_component(self):
        return HistorySceneComponent(self)

    @cached_property
    def _part_history_watch_component(self):
        return HistoryWatchComponent(self)

    @cached_property
    def _part_history_write_component(self):
        return HistoryWriteComponent(self)

    @cached_property
    def _part_spine_boot_component(self):
        return _part_spine_boot_component.SpineBootComponent(self)

    @cached_property
    def _part_spine_pulse_component(self):
        return SpinePulseComponent(self)

    @cached_property
    def _part_spine_adapt_component(self):
        return SpineAdaptComponent(self)

    @cached_property
    def _part_spine_climate_component(self):
        return SpineClimateComponent(self)

    @cached_property
    def _part_spine_invite_component(self):
        return SpineInviteComponent(self)
