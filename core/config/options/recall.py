from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .parse import as_bool, as_float, as_int, as_str


@dataclass(slots=True)
class CommitmentSettings:
    provider: str = ""
    min_confidence: float = 0.7

    @staticmethod
    def from_dict(data: Any) -> "CommitmentSettings":
        if not isinstance(data, dict):
            return CommitmentSettings()
        return CommitmentSettings(
            provider=as_str(data.get("provider", "")).strip(),
            min_confidence=as_float(data.get("min_confidence", 0.7), 0.7, 0.0),
        )


@dataclass(slots=True)
class MemorySettings:
    provider: str = ""
    min_message_length: int = 8
    max_generation_items: int = 8
    max_injection_items: int = 5

    @staticmethod
    def from_dict(data: Any) -> "MemorySettings":
        if not isinstance(data, dict):
            return MemorySettings()
        return MemorySettings(
            provider=as_str(data.get("provider", "")).strip(),
            min_message_length=as_int(data.get("min_message_length", 8), 8, 1, 200),
            max_generation_items=as_int(data.get("max_generation_items", 8), 8, 0, 30),
            max_injection_items=as_int(data.get("max_injection_items", 5), 5, 0, 20),
        )


@dataclass(slots=True)
class MemOSSettings:
    enabled: bool = False
    base_url: str = "https://memos.memtensor.cn/api/openmem/v1"
    api_key: str = ""
    timeout_seconds: float = 15.0
    injection_timeout_seconds: float = 0.8
    memory_limit_number: int = 5
    preference_limit_number: int = 4
    max_context_items: int = 6
    max_context_chars: int = 700
    include_preference: bool = True
    sync_selected_memory: bool = False
    sync_corrections: bool = True

    @staticmethod
    def from_dict(data: Any) -> "MemOSSettings":
        if not isinstance(data, dict):
            return MemOSSettings()
        return MemOSSettings(
            enabled=as_bool(data.get("enabled", False), False),
            base_url=as_str(
                data.get("base_url", "https://memos.memtensor.cn/api/openmem/v1"),
                "https://memos.memtensor.cn/api/openmem/v1",
            ).strip().rstrip("/")
            or "https://memos.memtensor.cn/api/openmem/v1",
            api_key=as_str(data.get("api_key", "")).strip(),
            timeout_seconds=as_float(data.get("timeout_seconds", 15.0), 15.0, 0.5, 30.0),
            injection_timeout_seconds=as_float(data.get("injection_timeout_seconds", 0.8), 0.8, 0.2, 5.0),
            memory_limit_number=as_int(data.get("memory_limit_number", 5), 5, 1, 20),
            preference_limit_number=as_int(data.get("preference_limit_number", 4), 4, 0, 20),
            max_context_items=as_int(data.get("max_context_items", 6), 6, 1, 20),
            max_context_chars=as_int(data.get("max_context_chars", 700), 700, 120, 3000),
            include_preference=as_bool(data.get("include_preference", True), True),
            sync_selected_memory=as_bool(data.get("sync_selected_memory", False), False),
            sync_corrections=as_bool(data.get("sync_corrections", True), True),
        )
