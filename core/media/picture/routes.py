from __future__ import annotations

import hashlib

from ...config.options import ImageGenerationSettings
from ..base import normalize_openai_base_url
from . import gemini, imagine, openai
from .pipe import ImageRoute


def task_reference(route: ImageRoute) -> dict[str, str]:
    """保存原任务的通道身份，凭据只保存不可逆指纹。"""
    return {
        "api_url": normalize_openai_base_url(route.api_url),
        "model": route.model,
        "protocol": route.protocol,
        "credential_id": hashlib.sha256(route.api_key.encode("utf-8")).hexdigest(),
    }


def normalize_image_provider(value: str) -> str:
    provider = str(value or "").strip().lower()
    if provider in {"openai", "gpt", "gpt-image-2", "gpt_image_2"}:
        return "openai"
    if provider in {"gemini", "gemini-image"}:
        return "gemini"
    if provider in {"grok", "grok-image", "grok-imagine-image"}:
        return "grok"
    return ""


def requested_image_provider(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw or raw == "auto":
        return ""
    provider = normalize_image_provider(raw)
    if not provider:
        raise ValueError("图片接口只能指定 auto、gpt/openai、gemini 或 grok")
    return provider


def image_provider_label(provider: str) -> str:
    normalized = normalize_image_provider(provider)
    return {
        "openai": "GPT Image",
        "gemini": "Gemini",
        "grok": "Grok",
    }.get(normalized, "")


def _mode_label(mode: str) -> str:
    return "图生图" if str(mode or "").strip().lower() == "edit" else "文生图"


def channel_label(channel: object, mode: str, index: int) -> str:
    configured = " ".join(str(getattr(channel, "group_name", "") or "").split())
    if configured:
        return configured
    return f"{_mode_label(mode)}接口通道 {index}"


def resolve_channel_name(
    settings: ImageGenerationSettings,
    mode: str,
    value: str,
    *,
    protocol: str = "",
    model: str = "",
) -> str:
    """Resolve a caller-selected channel by its configured display name only."""
    requested = " ".join(str(value or "").strip().split())
    if not requested:
        return ""
    channels = (
        getattr(settings, "edit_channels", []) or []
        if str(mode or "").strip().lower() == "edit"
        else getattr(settings, "text_channels", []) or []
    )
    matches = [
        (channel, channel_label(channel, mode, index))
        for index, channel in enumerate(channels, start=1)
        if channel_label(channel, mode, index) == requested
        and str(getattr(channel, "api_url", "") or "").strip()
        and str(getattr(channel, "api_key", "") or "").strip()
    ]
    mode_label = _mode_label(mode)
    if not matches:
        raise ValueError(f"没有找到名称为“{requested}”的可用{mode_label}通道")
    if len(matches) != 1:
        raise ValueError(f"{mode_label}通道名称“{requested}”重复，无法唯一选择")
    channel, label = matches[0]
    if protocol and not channel_matches_provider(channel, protocol):
        raise ValueError(f"{mode_label}通道“{label}”与指定的图片接口类型不匹配")
    if model and str(getattr(channel, "model", "") or "").strip() != model:
        raise ValueError(f"{mode_label}通道“{label}”与指定的生图模型不匹配")
    return label


def channel_matches_provider(channel: object, provider: str) -> bool:
    """判断配置通道是否属于指定图片提供商。"""
    normalized = normalize_image_provider(provider)
    channel_protocol = str(getattr(channel, "protocol", "") or "").strip().lower()
    if not normalized:
        return True
    if normalized == "openai":
        return channel_protocol == "openai"
    return channel_protocol == normalized


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
        and (not protocol or channel_matches_provider(channel, protocol))
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
    protocol = protocol if protocol in {"gemini", "openai", "grok"} else "gemini"
    default_model = {
        "openai": "gpt-image-2",
        "grok": "grok-imagine-image",
    }.get(protocol, "gemini-3-pro-image-preview")
    route_model = str(model or "").strip() or default_model
    return ImageRoute(
        api_url=api_url,
        api_key=api_key,
        model=route_model,
        label=label,
        protocol=protocol,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        timeout_seconds=timeout_seconds,
        origin={
            "openai": openai.origin,
            "grok": imagine.origin,
        }.get(protocol, gemini.origin)(api_url),
    )
