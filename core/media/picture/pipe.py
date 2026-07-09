from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ImageRoute:
    api_url: str
    api_key: str
    model: str
    label: str
    protocol: str
    resolution: str
    aspect_ratio: str
    timeout_seconds: int
    origin: str


@dataclass(slots=True)
class ImageRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any] | None = None
    form: Any = None
