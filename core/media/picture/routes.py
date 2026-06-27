from __future__ import annotations

from ...config.options import ImageGenerationSettings
from . import gemini, openai
from .pipe import ImageRoute


def has_channel(settings: ImageGenerationSettings) -> bool:
    return any(
        str(getattr(channel, "api_url", "") or "").strip()
        and str(getattr(channel, "api_key", "") or "").strip()
        for channel in (getattr(settings, "channels", []) or [])
    )


def make_route(
    api_url: str,
    api_key: str,
    model: str,
    label: str,
    protocol: str = "gemini",
    resolution: str = "4K",
    aspect_ratio: str = "1:1",
    timeout_seconds: int = 120,
) -> ImageRoute:
    protocol = str(protocol or "gemini").strip().lower()
    protocol = protocol if protocol in {"gemini", "openai"} else "gemini"
    default_model = "gpt-image-2" if protocol == "openai" else "gemini-3-pro-image-preview"
    return ImageRoute(
        api_url=api_url,
        api_key=api_key,
        model=str(model or "").strip() or default_model,
        label=label,
        protocol=protocol,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        timeout_seconds=timeout_seconds,
        origin=(openai.origin(api_url) if protocol == "openai" else gemini.origin(api_url)),
    )
