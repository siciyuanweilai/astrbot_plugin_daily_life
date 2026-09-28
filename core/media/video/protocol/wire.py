from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

JsonRequester = Callable[..., Awaitable[Any]]
LogWriter = Callable[[str], None]
