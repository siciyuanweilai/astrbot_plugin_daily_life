from __future__ import annotations

import math
from typing import Any

import aiohttp

from astrbot.api import logger

_AMAP_BASE_URL = "https://restapi.amap.com"
_ROUTE_PATHS = {
    "walking": "/v5/direction/walking",
    "cycling": "/v5/direction/bicycling",
    "driving": "/v5/direction/driving",
    "transit": "/v5/direction/transit/integrated",
}


class AmapWebServiceClient:
    """调用高德地图 Web 服务，并把响应转换为生活领域统一格式。"""

    provider_id = "amap"
    provider_label = "高德地图"

    def __init__(self, api_key: str, *, city: str = "", timeout_seconds: int = 8):
        self.api_key = str(api_key or "").strip()
        self.city = str(city or "").strip()
        self.timeout_seconds = max(1, int(timeout_seconds))

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def geocode(
        self,
        address: str,
        *,
        city_hint: str | None = None,
    ) -> dict[str, Any] | None:
        """把地点名称或地址解析为高德 GCJ-02 坐标。"""

        address = str(address or "").strip()
        if not self.available or not address:
            return None
        params = {"address": address}
        resolved_city_hint = self.city if city_hint is None else str(city_hint).strip()
        if resolved_city_hint:
            params["city"] = resolved_city_hint
        payload = await self._request_json("/v3/geocode/geo", params)
        geocodes = payload.get("geocodes") if isinstance(payload, dict) else None
        first = geocodes[0] if isinstance(geocodes, list) and geocodes else None
        if not isinstance(first, dict):
            return None
        coordinate = self._parse_location(first.get("location"))
        if coordinate is None:
            return None
        latitude, longitude = coordinate
        return {
            "latitude": latitude,
            "longitude": longitude,
            "formatted_address": str(first.get("formatted_address") or address),
            "country": str(first.get("country") or ""),
            "province": str(first.get("province") or ""),
            "city": self._text_value(first.get("city")),
            "citycode": self._text_value(first.get("citycode")),
            "adcode": self._text_value(first.get("adcode")),
            "provider": "amap_geocode",
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
        """查询高德路线，并返回统一的距离和时长字段。"""

        normalized_mode = mode if mode in _ROUTE_PATHS else "walking"
        if not self.available:
            return None
        lat1, lon1 = origin
        lat2, lon2 = destination
        params = {
            "origin": f"{lon1:.7f},{lat1:.7f}",
            "destination": f"{lon2:.7f},{lat2:.7f}",
            "show_fields": "cost",
        }
        if normalized_mode == "transit":
            city1 = str(origin_city or self.city).strip()
            city2 = str(destination_city or self.city).strip()
            if not city1 or not city2:
                return None
            params.update({"city1": city1, "city2": city2})
        payload = await self._request_json(_ROUTE_PATHS[normalized_mode], params)
        route = payload.get("route") if isinstance(payload, dict) else None
        if not isinstance(route, dict):
            return None
        candidates = route.get("transits" if normalized_mode == "transit" else "paths")
        first = candidates[0] if isinstance(candidates, list) and candidates else None
        if not isinstance(first, dict):
            return None
        distance = self._route_metric(first, "distance")
        duration = self._route_metric(first, "duration")
        if distance is None or duration is None:
            return None
        return {
            "distance_meters": round(distance, 1),
            "duration_seconds": max(0, int(duration)),
            "provider": "amap",
            "confidence": 0.95,
        }

    async def search_places(
        self,
        query: str,
        *,
        center: tuple[float, float] | None = None,
        category: str = "",
        radius_meters: int = 3000,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """按城市或中心点搜索地点。"""

        query = str(query or "").strip()
        if not self.available or not query:
            return []
        page_size = max(1, min(25, int(limit)))
        params: dict[str, Any] = {
            "keywords": query,
            "page_size": page_size,
            "show_fields": "business,navi,photos",
        }
        category = str(category or "").strip()
        if category:
            params["types"] = category
        if center is not None:
            latitude, longitude = center
            path = "/v5/place/around"
            params.update(
                {
                    "location": f"{longitude:.7f},{latitude:.7f}",
                    "radius": max(100, min(50000, int(radius_meters))),
                    "sortrule": "distance",
                }
            )
        else:
            path = "/v5/place/text"
            if self.city:
                params.update({"region": self.city, "city_limit": "true"})
        payload = await self._request_json(path, params)
        pois = payload.get("pois") if isinstance(payload, dict) else None
        if not isinstance(pois, list):
            return []
        return [
            normalized
            for item in pois[:page_size]
            if isinstance(item, dict)
            and (normalized := self._normalize_poi(item)) is not None
        ]

    async def input_tips(
        self,
        query: str,
        *,
        center: tuple[float, float] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """提供同名地点消歧候选。"""

        query = str(query or "").strip()
        if not self.available or not query:
            return []
        params: dict[str, Any] = {"keywords": query, "datatype": "all"}
        if self.city:
            params.update({"city": self.city, "citylimit": "true"})
        if center is not None:
            latitude, longitude = center
            params["location"] = f"{longitude:.7f},{latitude:.7f}"
        payload = await self._request_json("/v3/assistant/inputtips", params)
        tips = payload.get("tips") if isinstance(payload, dict) else None
        if not isinstance(tips, list):
            return []
        result = []
        for item in tips:
            if not isinstance(item, dict):
                continue
            coordinate = self._parse_location(item.get("location"))
            result.append(
                {
                    "poi_id": self._text_value(item.get("id")),
                    "name": self._text_value(item.get("name")),
                    "address": self._text_value(item.get("address")),
                    "district": self._text_value(item.get("district")),
                    "typecode": self._text_value(item.get("typecode")),
                    "city": self._text_value(item.get("city")),
                    "adcode": self._text_value(item.get("adcode")),
                    "coordinate": coordinate,
                }
            )
            if len(result) >= max(1, min(20, int(limit))):
                break
        return result

    async def place_detail(self, poi_id: str) -> dict[str, Any] | None:
        """读取地点详情。"""

        poi_id = str(poi_id or "").strip()
        if not self.available or not poi_id:
            return None
        payload = await self._request_json(
            "/v5/place/detail",
            {"id": poi_id, "show_fields": "business,navi,photos"},
        )
        pois = payload.get("pois") if isinstance(payload, dict) else None
        first = pois[0] if isinstance(pois, list) and pois else None
        return self._normalize_poi(first) if isinstance(first, dict) else None

    async def traffic_status(
        self,
        center: tuple[float, float],
        *,
        radius_meters: int = 1000,
    ) -> dict[str, Any] | None:
        """查询中心点周边实时交通态势。"""

        if not self.available:
            return None
        latitude, longitude = center
        payload = await self._request_json(
            "/v3/traffic/status/circle",
            {
                "location": f"{longitude:.7f},{latitude:.7f}",
                "radius": max(100, min(5000, int(radius_meters))),
                "level": 5,
                "extensions": "base",
            },
        )
        traffic = payload.get("trafficinfo") if isinstance(payload, dict) else None
        if not isinstance(traffic, dict):
            return None
        evaluation = traffic.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        return {
            "description": self._text_value(traffic.get("description")),
            "status": self._text_value(evaluation.get("status")),
            "evaluation": self._text_value(evaluation.get("description")),
        }

    async def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        request_params = {**params, "key": self.api_key, "output": "JSON"}
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{_AMAP_BASE_URL}{path}", params=request_params
                ) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.debug(f"[日常生活] 高德地图请求失败：{type(exc).__name__}")
            return {}
        if not isinstance(payload, dict):
            return {}
        if str(payload.get("status") or "") != "1":
            info = str(payload.get("info") or "未知错误").strip()
            infocode = str(payload.get("infocode") or "").strip()
            logger.debug(f"[日常生活] 高德地图返回失败：{info}（{infocode}）")
            return {}
        return payload

    @staticmethod
    def _parse_location(value: Any) -> tuple[float, float] | None:
        parts = str(value or "").split(",")
        if len(parts) != 2:
            return None
        try:
            longitude = float(parts[0])
            latitude = float(parts[1])
        except (TypeError, ValueError):
            return None
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None
        return latitude, longitude

    @staticmethod
    def _text_value(value: Any) -> str:
        if isinstance(value, list):
            return str(value[0] if value else "").strip()
        return str(value or "").strip()

    @classmethod
    def _normalize_poi(cls, value: dict[str, Any]) -> dict[str, Any] | None:
        name = cls._text_value(value.get("name"))
        poi_id = cls._text_value(value.get("id"))
        if not name and not poi_id:
            return None
        business = value.get("business")
        business = business if isinstance(business, dict) else {}
        photos = value.get("photos")
        photo_urls = []
        for photo in photos if isinstance(photos, list) else []:
            if not isinstance(photo, dict):
                continue
            url = cls._text_value(photo.get("url"))
            if url:
                photo_urls.append(url)
            if len(photo_urls) >= 3:
                break
        return {
            "poi_id": poi_id,
            "name": name,
            "address": cls._text_value(value.get("address")),
            "category": cls._text_value(value.get("type")),
            "typecode": cls._text_value(value.get("typecode")),
            "province": cls._text_value(value.get("pname")),
            "city": cls._text_value(value.get("cityname")),
            "district": cls._text_value(value.get("adname")),
            "adcode": cls._text_value(value.get("adcode")),
            "distance_meters": cls._non_negative_number(value.get("distance")),
            "telephone": cls._text_value(business.get("tel") or value.get("tel")),
            "opening_hours": cls._text_value(
                business.get("opentime_week") or business.get("opentime_today")
            ),
            "rating": cls._non_negative_number(business.get("rating")),
            "average_cost": cls._non_negative_number(business.get("cost")),
            "photos": photo_urls,
            "coordinate": cls._parse_location(value.get("location")),
        }

    @staticmethod
    def _non_negative_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

    @classmethod
    def _route_metric(cls, route: dict[str, Any], name: str) -> float | None:
        direct = cls._non_negative_number(route.get(name))
        if direct is not None:
            return direct
        cost = route.get("cost")
        return (
            cls._non_negative_number(cost.get(name)) if isinstance(cost, dict) else None
        )


__all__ = ["AmapWebServiceClient"]
