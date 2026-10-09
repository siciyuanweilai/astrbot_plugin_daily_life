from __future__ import annotations

from collections import OrderedDict
from typing import Any


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def cache_usage(response: Any) -> dict[str, int | None]:
    """读取真实用量；接口未报告命中数时保持未知，不伪装成零命中。"""
    raw = _field(response, "raw_completion") or response
    usage = _field(raw, "usage") or _field(raw, "usage_metadata")
    total = None
    input_field = ""
    cached = None
    for name in ("prompt_tokens", "input_tokens", "prompt_token_count"):
        total = _count(_field(usage, name))
        if total is not None:
            input_field = name
            break
    for name in ("prompt_cache_hit_tokens", "cache_read_input_tokens", "cached_content_token_count"):
        cached = _count(_field(usage, name))
        if cached is not None:
            break
    if cached is None:
        details = _field(usage, "prompt_tokens_details") or _field(usage, "input_tokens_details")
        cached = _count(_field(details, "cached_tokens"))
    if input_field == "input_tokens" and _field(usage, "cache_read_input_tokens") is not None and total is not None:
        # 这类 API 的 input_tokens 不包含缓存读取和缓存写入。
        total += (cached or 0) + (_count(_field(usage, "cache_creation_input_tokens")) or 0)
    normalized = _field(response, "usage")
    other = _count(_field(normalized, "input_other"))
    normalized_cached = _count(_field(normalized, "input_cached"))
    if total is None and other is not None and normalized_cached is not None:
        total = other + normalized_cached
    if cached is None and normalized_cached is not None and normalized_cached > 0:
        cached = normalized_cached
    if cached is not None and total is not None:
        cached = min(cached, total)
    return {"input_tokens": total, "cached_tokens": cached}


class ModelCacheMetrics:
    """仅保留有上限的用量聚合，不保存提示词、消息、凭据或会话编号。"""

    def __init__(self, limit: int = 32):
        self.limit = max(1, limit)
        self.entries: OrderedDict[tuple[str, str], dict[str, int]] = OrderedDict()

    def record(self, response: Any, *, kind: str, model: str = "") -> dict[str, int | None]:
        usage = cache_usage(response)
        if not model:
            raw = _field(response, "raw_completion") or response
            model = str(_field(raw, "model") or "")
        key = (str(kind or "internal")[:20], str(model or "当前模型")[:120])
        item = self.entries.setdefault(key, {
            "requests": 0, "reported_requests": 0, "unreported_requests": 0,
            "input_tokens": 0, "cached_tokens": 0, "requests_with_hits": 0,
        })
        self.entries.move_to_end(key)
        while len(self.entries) > self.limit:
            self.entries.popitem(last=False)
        item["requests"] += 1
        if usage["input_tokens"] is not None and usage["cached_tokens"] is not None:
            item["reported_requests"] += 1
            item["input_tokens"] += usage["input_tokens"]
            item["cached_tokens"] += usage["cached_tokens"]
            item["requests_with_hits"] += int(usage["cached_tokens"] > 0)
        else:
            item["unreported_requests"] += 1
        return usage

    def snapshot(self) -> dict[str, Any]:
        rows = []
        for (kind, model), values in self.entries.items():
            total = values["input_tokens"]
            rows.append({
                "kind": kind, "model": model, **values,
                "cached_token_ratio": round(values["cached_tokens"] / total, 4) if total else None,
            })
        return {"since": "当前插件运行期", "models": rows}
