from __future__ import annotations

from typing import Any

from .amap import AmapWebServiceClient
from .baidu_map import BaiduMapWebServiceClient
from .tencent_map import TencentMapWebServiceClient

MAP_PROVIDER_LABELS = {
    "amap": "高德地图",
    "tencent": "腾讯地图",
    "baidu": "百度地图",
}


def normalize_map_provider(value: Any) -> str:
    provider = str(value or "amap").strip().lower()
    return provider if provider in MAP_PROVIDER_LABELS else "amap"


def create_map_client(settings: Any):
    provider = normalize_map_provider(getattr(settings, "map_provider", "amap"))
    if provider == "tencent":
        return TencentMapWebServiceClient(
            getattr(settings, "tencent_map_api_key", ""), city=""
        )
    if provider == "baidu":
        return BaiduMapWebServiceClient(
            getattr(settings, "baidu_map_api_key", ""), city=""
        )
    return AmapWebServiceClient(getattr(settings, "amap_api_key", ""), city="")


def map_provider_label(provider: Any) -> str:
    return MAP_PROVIDER_LABELS[normalize_map_provider(provider)]


__all__ = [
    "MAP_PROVIDER_LABELS",
    "create_map_client",
    "map_provider_label",
    "normalize_map_provider",
]
