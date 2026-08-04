from __future__ import annotations

from typing import Any

from astrbot.api import logger

from .map_common import non_negative_number
from .map_http import request_map_json

_TENCENT_MAP_BASE_URL = "https://apis.map.qq.com"
_ROUTE_PATHS = {
    "walking": "/ws/direction/v1/walking/",
    "cycling": "/ws/direction/v1/bicycling/",
    "driving": "/ws/direction/v1/driving/",
    "transit": "/ws/direction/v1/transit/",
}


class TencentMapWebServiceClient:
    """调用腾讯地图 WebService，并转换为生活领域统一格式。"""

    provider_id = "tencent"
    provider_label = "腾讯地图"

    def __init__(self, api_key: str, *, city: str = "", timeout_seconds: int = 8):
        self.api_key = str(api_key or "").strip()
        self.city = str(city or "").strip()
        self.timeout_seconds = max(1, int(timeout_seconds))

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def geocode(
        self, address: str, *, city_hint: str | None = None
    ) -> dict[str, Any] | None:
        address = str(address or "").strip()
        if not self.available or not address:
            return None
        resolved_city = self.city if city_hint is None else str(city_hint).strip()
        query = f"{resolved_city}{address}" if resolved_city else address
        payload = await self._request_json("/ws/geocoder/v1/", {"address": query})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            return None
        coordinate = self._parse_location(result.get("location"))
        if coordinate is None:
            return None
        components = result.get("address_components")
        components = components if isinstance(components, dict) else {}
        ad_info = result.get("ad_info")
        ad_info = ad_info if isinstance(ad_info, dict) else {}
        latitude, longitude = coordinate
        return {
            "latitude": latitude,
            "longitude": longitude,
            "formatted_address": str(result.get("title") or query).strip(),
            "country": self._text_value(components.get("nation")),
            "province": self._text_value(components.get("province")),
            "city": self._text_value(components.get("city")),
            "citycode": self._text_value(ad_info.get("city_code")),
            "adcode": self._text_value(ad_info.get("adcode")),
            "provider": "tencent_geocode",
        }

    async def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        *,
        origin_city: str = "",
        destination_city: str = "",
    ) -> dict[str, Any] | None:
        del origin_city, destination_city
        normalized_mode = mode if mode in _ROUTE_PATHS else "walking"
        if not self.available:
            return None
        lat1, lon1 = origin
        lat2, lon2 = destination
        payload = await self._request_json(
            _ROUTE_PATHS[normalized_mode],
            {
                "from": f"{lat1:.7f},{lon1:.7f}",
                "to": f"{lat2:.7f},{lon2:.7f}",
            },
        )
        result = payload.get("result") if isinstance(payload, dict) else None
        routes = result.get("routes") if isinstance(result, dict) else None
        first = routes[0] if isinstance(routes, list) and routes else None
        if not isinstance(first, dict):
            return None
        distance = non_negative_number(first.get("distance"))
        duration_minutes = non_negative_number(first.get("duration"))
        if distance is None or duration_minutes is None:
            return None
        return {
            "distance_meters": round(distance, 1),
            "duration_seconds": max(0, int(duration_minutes * 60)),
            "provider": self.provider_id,
            "confidence": 0.95,
        }

    async def search_places(
        self,
        query: str,
        *,
        center: tuple[float, float] | None = None,
        city_hint: str = "",
        category: str = "",
        radius_meters: int = 3000,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not self.available or not query:
            return []
        page_size = max(1, min(20, int(limit)))
        if center is not None:
            latitude, longitude = center
            boundary = (
                f"nearby({latitude:.7f},{longitude:.7f},"
                f"{max(100, min(50000, int(radius_meters)))})"
            )
        else:
            boundary = f"region({str(city_hint or self.city).strip() or '全国'},1)"
        params: dict[str, Any] = {
            "keyword": query,
            "boundary": boundary,
            "page_size": page_size,
            "page_index": 1,
        }
        category = str(category or "").strip()
        if category:
            params["filter"] = f"category={category}"
        payload = await self._request_json("/ws/place/v1/search", params)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        return [
            normalized
            for item in data[:page_size]
            if isinstance(item, dict)
            and (normalized := self._normalize_poi(item)) is not None
        ]

    async def input_tips(
        self,
        query: str,
        *,
        center: tuple[float, float] | None = None,
        city_hint: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not self.available or not query:
            return []
        params: dict[str, Any] = {
            "keyword": query,
            "region": str(city_hint or self.city).strip() or "全国",
            "region_fix": 0,
            "policy": 1,
        }
        if center is not None:
            latitude, longitude = center
            params["location"] = f"{latitude:.7f},{longitude:.7f}"
        payload = await self._request_json("/ws/place/v1/suggestion", params)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_poi(item)
            if normalized:
                result.append(normalized)
            if len(result) >= max(1, min(20, int(limit))):
                break
        return result

    async def place_detail(self, poi_id: str) -> dict[str, Any] | None:
        poi_id = str(poi_id or "").strip()
        if not self.available or not poi_id:
            return None
        payload = await self._request_json("/ws/place/v1/detail", {"id": poi_id})
        data = payload.get("data") if isinstance(payload, dict) else None
        detail = data[0] if isinstance(data, list) and data else None
        return self._normalize_poi(detail) if isinstance(detail, dict) else None

    async def traffic_status(
        self, center: tuple[float, float], *, radius_meters: int = 1000
    ) -> dict[str, Any]:
        del center, radius_meters
        return {
            "supported": False,
            "provider": self.provider_id,
            "reason": "腾讯地图当前通道未提供实时路况查询。",
        }

    async def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        request_params = {**params, "key": self.api_key, "output": "json"}
        payload = await request_map_json(
            f"{_TENCENT_MAP_BASE_URL}{path}",
            request_params,
            timeout_seconds=self.timeout_seconds,
            provider_label=self.provider_label,
            endpoint_label=path,
        )
        try:
            status = int(payload.get("status"))
        except (TypeError, ValueError):
            status = -1
        if status != 0:
            message = str(payload.get("message") or "未知错误").strip()
            logger.debug(f"[日常生活] 腾讯地图返回失败：{message}")
            return {}
        return payload

    @staticmethod
    def _parse_location(value: Any) -> tuple[float, float] | None:
        if not isinstance(value, dict):
            return None
        try:
            latitude = float(value.get("lat"))
            longitude = float(value.get("lng"))
        except (TypeError, ValueError):
            return None
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None
        return latitude, longitude

    @staticmethod
    def _text_value(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _normalize_poi(cls, value: dict[str, Any]) -> dict[str, Any] | None:
        name = cls._text_value(value.get("title") or value.get("name"))
        poi_id = cls._text_value(value.get("id"))
        if not name and not poi_id:
            return None
        ad_info = value.get("ad_info")
        ad_info = ad_info if isinstance(ad_info, dict) else {}
        return {
            "poi_id": poi_id,
            "name": name,
            "address": cls._text_value(value.get("address")),
            "category": cls._text_value(value.get("category")),
            "typecode": cls._text_value(value.get("type")),
            "province": cls._text_value(
                ad_info.get("province") or value.get("province")
            ),
            "city": cls._text_value(ad_info.get("city") or value.get("city")),
            "district": cls._text_value(
                ad_info.get("district") or value.get("district")
            ),
            "adcode": cls._text_value(ad_info.get("adcode") or value.get("adcode")),
            "distance_meters": non_negative_number(
                value.get("_distance") or value.get("distance")
            ),
            "telephone": cls._text_value(value.get("tel")),
            "opening_hours": "",
            "rating": None,
            "average_cost": None,
            "photos": [],
            "coordinate": cls._parse_location(value.get("location")),
        }


__all__ = ["TencentMapWebServiceClient"]
