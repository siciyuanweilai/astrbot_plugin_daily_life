import asyncio

from astrbot.api import logger

from ..clock import now as life_now
from ..labels import page_status_reason_label
from ..memos import MemosMixin
from ..sight import SightMixin
from .capture import CaptureMixin
from .past import RuntimeHistoryMixin
from .markers import LOG_PREFIX
from .remember import RuntimeMemoryMixin
from .messenger import RuntimeMediaMixin
from .inject import InjectMixin
from .proactive import ProactiveMixin
from .status import StatusMixin
from .structured import StructuredContextMixin
from .gate import ResponseGateMixin
from .recall import RecallMixin
from .refresh import RefreshMixin
from .style import ChatStyleRuntimeMixin
from .addressing import ChatAddressingMixin
from .background import BackgroundTaskMixin
from .spine import SpineMixin

class DailyLifeRuntime(
    MemosMixin,
    StatusMixin,
    CaptureMixin,
    StructuredContextMixin,
    RecallMixin,
    SightMixin,
    ResponseGateMixin,
    RefreshMixin,
    ChatAddressingMixin,
    ChatStyleRuntimeMixin,
    InjectMixin,
    ProactiveMixin,
    RuntimeMediaMixin,
    RuntimeMemoryMixin,
    RuntimeHistoryMixin,
    BackgroundTaskMixin,
    SpineMixin,
):
    """日常生活引擎的运行时服务。

    入口装饰器保留在入口文件；这里负责状态、存储、定时任务、提示词注入和工具动作。
    """

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
            logger.debug(f"{LOG_PREFIX} 面板状态已更新：版本：{version}，原因：{page_status_reason_label(reason)}")
        return version

    async def wait_page_status_changed(self, since: int = 0, timeout: float = 25.0) -> int:
        since = max(int(since or 0), 0)
        timeout = max(1.0, min(float(timeout or 25.0), 55.0))
        async with self._page_status_changed:
            if self._page_status_version > since:
                return self._page_status_version
            try:
                await asyncio.wait_for(
                    self._page_status_changed.wait_for(lambda: self._page_status_version > since),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                pass
            return self._page_status_version
