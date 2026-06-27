from __future__ import annotations

from typing import Any, Awaitable, Callable

JsonRequester = Callable[..., Awaitable[Any]]
LogWriter = Callable[[str], None]
