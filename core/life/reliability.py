import asyncio
import time

TRANSIENT_PROVIDER_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
NON_RETRYABLE_PROVIDER_STATUSES = frozenset({400, 402, 403, 404, 405, 409, 422})


class NonRetryableProviderError(RuntimeError):
    """表示继续重试也不会恢复的 Provider 请求错误。"""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        provider_id: str = "",
    ) -> None:
        super().__init__(str(message or "大语言模型请求不可重试"))
        self.status = status
        self.provider_id = str(provider_id or "").strip()


def exception_status(exc: BaseException) -> int | None:
    """从常见 Provider 异常对象或短错误文本提取 HTTP 状态。"""
    for name in ("status", "status_code", "http_status", "code"):
        value = getattr(exc, name, None)
        try:
            status = int(value)
        except (TypeError, ValueError):
            continue
        if 100 <= status <= 599:
            return status
    text = str(exc or "")
    for token in text.replace(":", " ").replace("=", " ").split():
        cleaned = token.strip('()[]{};,"')
        if cleaned.isdigit():
            status = int(cleaned)
            if 100 <= status <= 599:
                return status
    return None


def is_transient_provider_error(exc: BaseException) -> bool:
    status = exception_status(exc)
    if status is not None:
        return status in TRANSIENT_PROVIDER_STATUSES
    return isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError))


def is_non_retryable_provider_error(exc: BaseException) -> bool:
    status = exception_status(exc)
    return status in NON_RETRYABLE_PROVIDER_STATUSES


def retry_delay(attempt: int, *, retry_after: float | None = None) -> float:
    if retry_after is not None and retry_after >= 0:
        return min(float(retry_after), 30.0)
    return min(0.6 * (2 ** max(int(attempt), 0)), 8.0)


class ProviderCircuit:
    """进程内短熔断，防止单个 Provider 连续故障拖慢每个生活任务。"""

    def __init__(self, *, threshold: int = 3, cooldown_seconds: float = 30.0):
        self.threshold = max(int(threshold), 1)
        self.cooldown_seconds = max(float(cooldown_seconds), 1.0)
        self._failures: dict[str, int] = {}
        self._opened_until: dict[str, float] = {}

    def is_open(self, provider_id: str) -> bool:
        key = str(provider_id or "").strip()
        until = self._opened_until.get(key, 0.0)
        if until and until <= time.monotonic():
            self._opened_until.pop(key, None)
            self._failures.pop(key, None)
            return False
        return bool(until)

    def record_success(self, provider_id: str) -> None:
        key = str(provider_id or "").strip()
        self._failures.pop(key, None)
        self._opened_until.pop(key, None)

    def record_failure(self, provider_id: str) -> bool:
        key = str(provider_id or "").strip()
        failures = self._failures.get(key, 0) + 1
        self._failures[key] = failures
        if failures >= self.threshold:
            self._opened_until[key] = time.monotonic() + self.cooldown_seconds
            return True
        return False


__all__ = [
    "NON_RETRYABLE_PROVIDER_STATUSES",
    "TRANSIENT_PROVIDER_STATUSES",
    "NonRetryableProviderError",
    "ProviderCircuit",
    "exception_status",
    "is_non_retryable_provider_error",
    "is_transient_provider_error",
    "retry_delay",
]
