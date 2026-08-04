from __future__ import annotations

import math
from typing import Any

from astrbot.api import logger

from .map_common import non_negative_number
from .map_http import request_map_json

_BAIDU_MAP_BASE_URL = "https://api.map.baidu.com"
_ROUTE_PATHS = {
    "walking": "/directionlite/v1/walking",
    "cycling": "/directionlite/v1/riding",
    "driving": "/directionlite/v1/driving",
    "transit": "/directionlite/v1/transit",
}
_X_PI = math.pi * 3000.0 / 180.0


def bd09_to_gcj02(coordinate: tuple[float, float]) -> tuple[float, float]:
    """把百度 BD-09 经纬度转换为插件统一使用的 GCJ-02。"""

    latitude, longitude = coordinate
    x = longitude - 0.0065
    y = latitude - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    return z * math.sin(theta), z * math.cos(theta)


def gcj02_to_bd09(coordinate: tuple[float, float]) -> tuple[float, float]:
    """把插件统一使用的 GCJ-02 经纬度转换为百度 BD-09。"""

    latitude, longitude = coordinate
    z = math.sqrt(longitude * longitude + latitude * latitude) + 0.00002 * math.sin(
        latitude * _X_PI
    )
    theta = math.atan2(latitude, longitude) + 0.000003 * math.cos(longitude * _X_PI)
    return z * math.sin(theta) + 0.006, z * math.cos(theta) + 0.0065


class BaiduMapWebServiceClient:
    """调用百度地图 Web 服务，并统一输出 GCJ-02 坐标。"""

    provider_id = "baidu"
    provider_label = "百度地图"

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
        params: dict[str, Any] = {
            "address": address,
            "ret_coordtype": "bd09ll",
        }
        if resolved_city:
            params["city"] = resolved_city
        payload = await self._request_json("/geocoding/v3/", params)
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            return None
        bd_coordinate = self._parse_location(result.get("location"))
        if bd_coordinate is None:
            return None
        reverse = await self._request_json(
            "/reverse_geocoding/v3/",
            {
                "location": f"{bd_coordinate[0]:.7f},{bd_coordinate[1]:.7f}",
                "coordtype": "bd09ll",
                "extensions_poi": 0,
            },
        )
        reverse_result = reverse.get("result") if isinstance(reverse, dict) else None
        reverse_result = reverse_result if isinstance(reverse_result, dict) else {}
        components = reverse_result.get("addressComponent")
        components = components if isinstance(components, dict) else {}
        latitude, longitude = bd09_to_gcj02(bd_coordinate)
        return {
            "latitude": latitude,
            "longitude": longitude,
            "formatted_address": str(
                reverse_result.get("formatted_address") or address
            ).strip(),
            "country": self._text_value(components.get("country")),
            "province": self._text_value(components.get("province")),
            "city": self._text_value(components.get("city")),
            "citycode": self._text_value(components.get("city_level")),
            "adcode": self._text_value(components.get("adcode")),
            "provider": "baidu_geocode",
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
        bd_origin = gcj02_to_bd09(origin)
        bd_destination = gcj02_to_bd09(destination)
        params: dict[str, Any] = {
            "origin": f"{bd_origin[0]:.7f},{bd_origin[1]:.7f}",
            "destination": f"{bd_destination[0]:.7f},{bd_destination[1]:.7f}",
            "coord_type": "bd09ll",
        }
        if normalized_mode == "transit":
            params["tactics_incity"] = 0
        payload = await self._request_json(_ROUTE_PATHS[normalized_mode], params)
        result = payload.get("result") if isinstance(payload, dict) else None
        routes = result.get("routes") if isinstance(result, dict) else None
        first = routes[0] if isinstance(routes, list) and routes else None
        if not isinstance(first, dict):
            return None
        distance = non_negative_number(first.get("distance"))
        duration = non_negative_number(first.get("duration"))
        if distance is None or duration is None:
            return None
        return {
            "distance_meters": round(distance, 1),
            "duration_seconds": max(0, int(duration)),
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
        search_query = " ".join(
            item for item in (query, str(category or "").strip()) if item
        )
        params: dict[str, Any] = {
            "query": search_query,
            "scope": 2,
            "page_size": page_size,
            "page_num": 0,
        }
        if center is not None:
            bd_center = gcj02_to_bd09(center)
            params.update(
                {
                    "location": f"{bd_center[0]:.7f},{bd_center[1]:.7f}",
                    "radius": max(100, min(50000, int(radius_meters))),
                    "radius_limit": "true",
                }
            )
        else:
            params.update(
                {
                    "region": str(city_hint or self.city).strip() or "全国",
                    "city_limit": "true",
                }
            )
        payload = await self._request_json("/place/v2/search", params)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return []
        return [
            normalized
            for item in results[:page_size]
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
        del center
        query = str(query or "").strip()
        if not self.available or not query:
            return []
        payload = await self._request_json(
            "/place/v2/suggestion",
            {
                "query": query,
                "region": str(city_hint or self.city).strip() or "全国",
                "city_limit": "true",
            },
        )
        results = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(results, list) and isinstance(payload, dict):
            results = payload.get("results")
        if not isinstance(results, list):
            return []
        normalized = []
        for item in results:
            if not isinstance(item, dict):
                continue
            value = self._normalize_poi(item)
            if value:
                normalized.append(value)
            if len(normalized) >= max(1, min(20, int(limit))):
                break
        return normalized

    async def place_detail(self, poi_id: str) -> dict[str, Any] | None:
        poi_id = str(poi_id or "").strip()
        if not self.available or not poi_id:
            return None
        payload = await self._request_json(
            "/place/v2/detail", {"uid": poi_id, "scope": 2}
        )
        result = payload.get("result") if isinstance(payload, dict) else None
        return self._normalize_poi(result) if isinstance(result, dict) else None

    async def traffic_status(
        self, center: tuple[float, float], *, radius_meters: int = 1000
    ) -> dict[str, Any]:
        del center, radius_meters
        return {
            "supported": False,
            "provider": self.provider_id,
            "reason": "百度地图当前通道未提供实时路况查询。",
        }

    async def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        request_params = {**params, "ak": self.api_key, "output": "json"}
        payload = await request_map_json(
            f"{_BAIDU_MAP_BASE_URL}{path}",
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
            logger.debug(f"[日常生活] 百度地图返回失败：{message}")
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
        name = cls._text_value(value.get("name"))
        poi_id = cls._text_value(value.get("uid"))
        if not name and not poi_id:
            return None
        detail = value.get("detail_info")
        detail = detail if isinstance(detail, dict) else {}
        bd_coordinate = cls._parse_location(value.get("location"))
        coordinate = bd09_to_gcj02(bd_coordinate) if bd_coordinate else None
        photos = detail.get("photo_list")
        photo_urls = []
        for photo in photos if isinstance(photos, list) else []:
            if not isinstance(photo, dict):
                continue
            url = cls._text_value(photo.get("photo"))
            if url:
                photo_urls.append(url)
            if len(photo_urls) >= 3:
                break
        return {
            "poi_id": poi_id,
            "name": name,
            "address": cls._text_value(value.get("address")),
            "category": cls._text_value(detail.get("tag")),
            "typecode": cls._text_value(detail.get("type")),
            "province": cls._text_value(value.get("province")),
            "city": cls._text_value(value.get("city")),
            "district": cls._text_value(value.get("area") or value.get("district")),
            "adcode": cls._text_value(value.get("adcode")),
            "distance_meters": non_negative_number(detail.get("distance")),
            "telephone": cls._text_value(value.get("telephone")),
            "opening_hours": cls._text_value(detail.get("shop_hours")),
            "rating": non_negative_number(detail.get("overall_rating")),
            "average_cost": non_negative_number(detail.get("price")),
            "photos": photo_urls,
            "coordinate": coordinate,
        }


__all__ = [
    "BaiduMapWebServiceClient",
    "bd09_to_gcj02",
    "gcj02_to_bd09",
]
