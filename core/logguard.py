from typing import Any

from astrbot.api import logger

_SDK_QUIET_LEVELS = {
    "openai": {"DEBUG", "INFO"},
    "anthropic": {"DEBUG", "INFO"},
    "google.genai": {"DEBUG", "INFO"},
    "google.generativeai": {"DEBUG", "INFO"},
    "httpcore": {"DEBUG", "INFO"},
    "httpx": {"DEBUG"},
}


class ProviderLogGuard:
    """Filter verbose model logs through AstrBot's existing output handlers."""

    def __init__(self) -> None:
        self._handlers: list[Any] = []
        self._filter = self._allow_record

    def install(self) -> None:
        if self._handlers:
            return
        current = logger
        visited: set[int] = set()
        # The API logger may route to a plugin logger. Ancestor handlers also
        # carry framework Provider and SDK logs, even without plugin propagation.
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            for handler in getattr(current, "handlers", ()):
                if any(handler is existing for existing in self._handlers):
                    continue
                handler.addFilter(self._filter)
                self._handlers.append(handler)
            current = getattr(current, "parent", None)

    def close(self) -> None:
        for handler in self._handlers:
            handler.removeFilter(self._filter)
        self._handlers.clear()

    def _allow_record(self, record: Any) -> bool:
        name = str(getattr(record, "name", "") or "")
        level = str(getattr(record, "levelname", "") or "").upper()
        for namespace, quiet_levels in _SDK_QUIET_LEVELS.items():
            if name == namespace or name.startswith(namespace + "."):
                return level not in quiet_levels
        if level != "DEBUG":
            return True
        source = str(getattr(record, "pathname", "") or "").replace("\\", "/")
        if "/core/provider/sources/" not in source:
            return True
        message = record.getMessage().lstrip().lower()
        return not message.startswith(("completion:", "response:"))
