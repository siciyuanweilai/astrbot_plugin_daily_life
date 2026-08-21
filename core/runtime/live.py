import asyncio
from typing import Any

from astrbot.api import logger

from ..labels import page_status_reason_label
from ..memos import MemosMixin
from ..sight import SightMixin
from .addressing import ChatAddressingMixin
from .background import BackgroundTaskMixin
from .capture import CaptureMixin
from .context import InteractionContextMixin
from .forward import TextForwardMixin
from .gate import ResponseGateMixin
from .gateway import ModelCallOptions, ModelGateway
from .generation import DailyGenerationMixin
from .inject import InjectMixin
from .integration import ExternalIntegrationMixin
from .markers import LOG_PREFIX
from .meaning import MeaningRuntimeMixin
from .messenger import RuntimeMediaMixin
from .outbound import OutboundLogMixin
from .past import RuntimeHistoryMixin
from .proactive import ProactiveMixin
from .reaction import ToolReactionMixin
from .recall import RecallMixin
from .receipt import RuntimeActionReceiptMixin
from .refresh import RefreshMixin
from .remember import RuntimeMemoryMixin
from .reply import SemanticSegmentRuntimeMixin
from .spine import SpineMixin
from .status import StatusMixin
from .structured import StructuredContextMixin
from .style import ChatStyleRuntimeMixin
from .turns import ContinuousTurnMixin


class DailyLifeRuntime(
    MeaningRuntimeMixin,
    ExternalIntegrationMixin,
    MemosMixin,
    StatusMixin,
    CaptureMixin,
    InteractionContextMixin,
    StructuredContextMixin,
    RecallMixin,
    ToolReactionMixin,
    SightMixin,
    ContinuousTurnMixin,
    ResponseGateMixin,
    RefreshMixin,
    RuntimeActionReceiptMixin,
    ChatAddressingMixin,
    TextForwardMixin,
    ChatStyleRuntimeMixin,
    SemanticSegmentRuntimeMixin,
    InjectMixin,
    ProactiveMixin,
    RuntimeMediaMixin,
    OutboundLogMixin,
    RuntimeMemoryMixin,
    RuntimeHistoryMixin,
    BackgroundTaskMixin,
    DailyGenerationMixin,
    SpineMixin,
):
    """日常生活引擎的运行时服务。

    入口装饰器保留在入口文件；这里负责状态、存储、定时任务、提示词注入和工具动作。
    """

    async def get_text_provider(self, provider_id: str = ""):
        gateway = getattr(self, "model_gateway", None) or ModelGateway(self.composer)
        return await gateway.provider(provider_id)

    async def create_voice_call_invite(self, event: Any, *, greeting: str = "") -> str:
        manager = getattr(self, "voice_call", None)
        if manager is None:
            raise RuntimeError("实时语音通话服务尚未初始化")
        return await manager.create_invite(event, greeting=greeting)

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
