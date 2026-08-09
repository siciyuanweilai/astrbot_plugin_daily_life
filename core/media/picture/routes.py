from __future__ import annotations

from ...config.options import ImageGenerationSettings
from . import gemini, openai
from .pipe import ImageRoute


def normalize_image_provider(value: str) -> str:
    provider = str(value or "").strip().lower()
    if provider in {"openai", "gpt", "gpt-image-2", "gpt_image_2"}:
        return "openai"
    if provider in {"gemini", "gemini-image"}:
        return "gemini"
    return ""


def requested_image_provider(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw or raw == "auto":
        return ""
    provider = normalize_image_provider(raw)
    if not provider:
        raise ValueError("图片接口只能指定 auto、gpt 或 gemini")
    return provider


def image_provider_label(provider: str) -> str:
    normalized = normalize_image_provider(provider)
    return (
        "GPT" if normalized == "openai" else "Gemini" if normalized == "gemini" else ""
    )


def has_channel(
    settings: ImageGenerationSettings,
    mode: str = "text",
    protocol: str = "",
    model: str = "",
) -> bool:
    protocol = normalize_image_provider(protocol)
    model = str(model or "").strip()
    channels = (
        getattr(settings, "edit_channels", []) or []
        if str(mode or "").strip().lower() == "edit"
        else getattr(settings, "text_channels", []) or []
    )
    return any(
        str(getattr(channel, "api_url", "") or "").strip()
        and str(getattr(channel, "api_key", "") or "").strip()
        and (
            not protocol
            or str(getattr(channel, "protocol", "") or "").lower() == protocol
        )
        and (not model or str(getattr(channel, "model", "") or "").strip() == model)
        for channel in channels
    )


def make_route(
    api_url: str,
    api_key: str,
    model: str,
    label: str,
    protocol: str,
    resolution: str,
    aspect_ratio: str,
    timeout_seconds: int,
) -> ImageRoute:
    protocol = str(protocol or "gemini").strip().lower()
    protocol = protocol if protocol in {"gemini", "openai"} else "gemini"
    default_model = (
        "gpt-image-2" if protocol == "openai" else "gemini-3-pro-image-preview"
    )
    return ImageRoute(
        api_url=api_url,
        api_key=api_key,
        model=str(model or "").strip() or default_model,
        label=label,
        protocol=protocol,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        timeout_seconds=timeout_seconds,
        origin=(
            openai.origin(api_url) if protocol == "openai" else gemini.origin(api_url)
        ),
    )
