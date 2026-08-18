from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from ..prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from .routing import (
    AUTO_ROUTE_MODE,
    ROUTE_MODES,
    RouteChoiceContext,
    choose_practical_route,
    default_route_mode,
    route_distance,
    route_minutes,
    route_query_modes,
)
from .tools import extract_json_from_text

_LOCATION_SCOPES = frozenset({"local", "travel"})
_TRAVEL_MODES = ROUTE_MODES | {AUTO_ROUTE_MODE}
_FULL_DAY_LOCATION_LIMIT = 8
_PARTIAL_LOCATION_LIMIT = 4


class DailyLocationPlanningMixin:
    """为日程生成预选可确认的地图地点。"""

    @staticmethod
    def _location_plan_text(value: Any, limit: int = 96) -> str:
        return " ".join(str(value or "").split())[:limit]

    @classmethod
    def _normalize_daily_location_requests(
        cls, values: Any, *, limit: int
    ) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            return []
        requests: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for raw in values:
            if not isinstance(raw, dict):
                continue
            query = cls._location_plan_text(raw.get("query"), 80)
            if not query:
                continue
            scope = cls._location_plan_text(raw.get("place_scope"), 16).lower()
            if not scope:
                scope = "local"
            if scope not in _LOCATION_SCOPES:
                continue
            city = cls._location_plan_text(raw.get("place_city"), 48)
            if scope == "travel" and not city:
                continue
            mode = cls._location_plan_text(raw.get("travel_mode"), 16).lower()
            if mode not in _TRAVEL_MODES:
                mode = AUTO_ROUTE_MODE
            mode_locked = bool(raw.get("travel_mode_locked")) and mode in ROUTE_MODES
            try:
                max_travel_minutes = max(
                    0, min(360, int(raw.get("max_travel_minutes") or 0))
                )
            except (TypeError, ValueError):
                max_travel_minutes = 0
            hint = cls._location_plan_text(raw.get("place_hint"), 96)
            key = (query, scope, city, hint)
            if key in seen:
                continue
            seen.add(key)
            requests.append(
                {
                    "purpose": cls._location_plan_text(raw.get("purpose"), 80),
                    "query": query,
                    "category": cls._location_plan_text(raw.get("category"), 80),
                    "place_scope": scope,
                    "place_city": city,
                    "place_hint": hint,
                    "travel_mode": mode,
                    "travel_mode_locked": mode_locked,
                    "max_travel_minutes": max_travel_minutes,
                }
            )
            if len(requests) >= max(1, limit):
                break
        return requests

    async def prepare_daily_location_candidates(
        self,
        requests: Any,
        *,
        max_candidates: int = _FULL_DAY_LOCATION_LIMIT,
        weather_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """根据活动意图选择可写入日程的地图地点候选。

        候选仅用于本次生成提示，不会在最终日程采用前写入地点或路线记录。
        """

        if not self.map_tools_available():
            return {"available": False, "candidates": [], "warnings": []}
        home = await self.resolve_home_location()
        if not home:
            return {"available": False, "candidates": [], "warnings": []}

        normalized = self._normalize_daily_location_requests(
            requests, limit=max(1, min(_FULL_DAY_LOCATION_LIMIT, int(max_candidates)))
        )
        if not normalized:
            return {
                "available": True,
                "map_provider": self.map_provider_label,
                "home_city": str(home.get("city") or "").strip(),
                "candidates": [],
                "warnings": [],
            }

        home_city = str(home.get("city") or "").strip()
        home_coordinate = home.get("coordinate")
        route_context = RouteChoiceContext.from_values(weather_info)

        async def search(request: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
            scope = request["place_scope"]
            city = request["place_city"] if scope == "travel" else home_city
            query = " ".join(
                value for value in (request["query"], request["place_hint"]) if value
            )
            try:
                places = await self._map.search_places(
                    query,
                    center=home_coordinate if scope == "local" else None,
                    city_hint=city,
                    category=request["category"],
                    radius_meters=12_000,
                    limit=3,
                )
                if not places and scope == "local":
                    places = await self._map.search_places(
                        query,
                        city_hint=city,
                        category=request["category"],
                        limit=3,
                    )
                return request, places if isinstance(places, list) else []
            except Exception as exc:
                logger.debug(
                    f"[日程生成] 地图预选地点查询失败：{request['query']}：{exc}"
                )
                return request, []

        search_results = await asyncio.gather(*(search(item) for item in normalized))
        candidates: list[dict[str, Any]] = []
        warnings: list[str] = []
        used: set[tuple[str, str]] = set()
        for request, values in search_results:
            target_city = (
                request["place_city"]
                if request["place_scope"] == "travel"
                else home_city
            )
            selected = None
            selected_route = None
            for value in values:
                if not isinstance(value, dict):
                    continue
                name = self._location_plan_text(value.get("name"), 96)
                coordinate = value.get("coordinate")
                if (
                    not name
                    or not isinstance(coordinate, (list, tuple))
                    or len(coordinate) != 2
                ):
                    continue
                if not self._cities_match(
                    self._location_plan_text(value.get("city"), 48), target_city
                ):
                    continue
                identity = (str(value.get("poi_id") or "").strip(), name)
                if identity in used:
                    continue
                destination_coordinate = (
                    float(coordinate[0]),
                    float(coordinate[1]),
                )
                choice = await self._select_location_candidate_route(
                    home_coordinate,
                    destination_coordinate,
                    request=request,
                    home_city=home_city,
                    target_city=target_city,
                    context=route_context,
                    place_name=name,
                )
                if choice is None:
                    continue
                selected_mode, route, _ = choice
                selected = value
                selected_route = route if isinstance(route, dict) else {}
                selected_mode_value = selected_mode
                used.add(identity)
                break
            if not selected:
                warnings.append(f"未找到适合“{request['query']}”的可确认地点")
                continue

            address = self._location_plan_text(selected.get("address"), 160)
            district = self._location_plan_text(selected.get("district"), 64)
            place_city = (
                self._location_plan_text(selected.get("city"), 48) or target_city
            )
            route = selected_route or {}
            route_minutes_value = route_minutes(route)
            route_distance_value = route_distance(route)
            candidates.append(
                {
                    "purpose": request["purpose"],
                    "name": self._location_plan_text(selected.get("name"), 96),
                    "address": address,
                    "district": district,
                    "city": place_city,
                    "category": self._location_plan_text(selected.get("category"), 96),
                    "place_kind": "poi",
                    "place_scope": request["place_scope"],
                    "place_city": place_city
                    if request["place_scope"] == "travel"
                    else "",
                    "place_hint": address or district,
                    "travel_mode": selected_mode_value,
                    "travel_mode_locked": request["travel_mode_locked"],
                    "travel_detail": (
                        self._location_plan_text(route.get("travel_detail"), 32)
                        if selected_mode_value == "transit"
                        else ""
                    ),
                    "travel_minutes": route_minutes_value,
                    "travel_distance_meters": round(route_distance_value, 1),
                    "poi_id": str(selected.get("poi_id") or "").strip(),
                    "coordinate": (
                        float(selected["coordinate"][0]),
                        float(selected["coordinate"][1]),
                    ),
                }
            )

        return {
            "available": True,
            "map_provider": self.map_provider_label,
            "home_city": home_city,
            "candidates": candidates,
            "warnings": warnings,
        }

    async def _select_location_candidate_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        *,
        request: dict[str, Any],
        home_city: str,
        target_city: str,
        context: RouteChoiceContext,
        place_name: str,
    ) -> tuple[str, dict[str, Any], int] | None:
        requested_mode = str(request.get("travel_mode") or AUTO_ROUTE_MODE)
        locked = bool(request.get("travel_mode_locked"))
        modes = route_query_modes(requested_mode, locked=locked)

        async def fetch(mode: str) -> tuple[str, dict[str, Any] | None]:
            try:
                route = await self._map.route(
                    origin,
                    destination,
                    mode,
                    origin_city=home_city,
                    destination_city=target_city,
                )
                return mode, route if isinstance(route, dict) else None
            except Exception as exc:
                logger.debug(
                    f"[日程生成] 地图预选路线查询失败：{place_name}；"
                    f"方式={mode}；错误={exc}"
                )
                return mode, None

        values = await asyncio.gather(*(fetch(mode) for mode in modes))
        options = [(mode, route) for mode, route in values if route is not None]
        choice = choose_practical_route(
            options,
            requested_mode=requested_mode,
            locked=locked,
            context=context,
            max_minutes=max(0, int(request.get("max_travel_minutes") or 0)),
        )
        if choice is not None:
            return choice
        if options and int(request.get("max_travel_minutes") or 0) > 0:
            return None

        distance = self._haversine(origin, destination)
        fallback_mode = (
            requested_mode
            if locked and requested_mode in ROUTE_MODES
            else default_route_mode(distance, context)
        )
        return fallback_mode, {}, 0


class DailyLocationGenerationMixin:
    """把短地点意图、地图候选和最终日程生成串成两阶段流程。"""

    @staticmethod
    def _daily_location_request_prompt(context: dict[str, Any]) -> str:
        request_limit = (
            _FULL_DAY_LOCATION_LIMIT
            if context.get("expected_coverage") == "full_day"
            else _PARTIAL_LOCATION_LIMIT
        )
        fixed = f"""在生成完整日程前，只规划可能需要地图确认的具体外出地点需求。

【输出格式】
只返回一个 JSON 对象：
{{
  "requests": [
    {{
      "purpose": "为什么需要这个地点",
      "query": "交给地图搜索的地点名称或场所类型",
      "category": "可选地图分类，没有则空字符串",
      "place_scope": "local | travel",
      "place_city": "明确跨城旅行的目标城市，否则空字符串",
      "place_hint": "可选区县、商圈或地址提示",
      "travel_mode": "auto | walking | cycling | driving | transit",
      "travel_mode_locked": false,
      "max_travel_minutes": 0
    }}
  ]
}}

【规则】
- 按实际活动需要提出地点需求，不要为了控制数量而漏掉彼此不同的必要外出地点；本轮最多处理 {request_limit} 个去重需求，不需要外出时返回空数组。
- 这里只表达活动目的和搜索条件，不生成日程、时间轴、穿搭或生活状态。
- 本地生活使用 local，不填写 place_city；旅行、出差、返乡或跨城目标使用 travel 并填写可靠的目标城市。旅行意图可以由用户指令、承诺、周计划、聊天内容或本轮生活决策共同形成，但没有目标城市时不要凭空猜测城市，应返回空需求或保留为本地游览。
- “在家”“附近散步”“河边走走”“线上聊天”等泛化场景不需要地图 POI，不要为了凑数量提出地点需求。
- query 应便于地图找到真实地点，但不要凭空编造店名；具体名称由地图选择。
- travel_mode 默认使用 auto，由地图结合距离、路线耗时、天气和生活状态选择。只有资料明确指定步行、骑行、驾车或公共交通，或者交通方式本身就是活动目的时，才填写具体方式并将 travel_mode_locked 设为 true。
- “到某地散步、逛街、扫街”描述的是到达后的活动，不等于前往该地点的通勤方式；不要因此锁定 walking。
- max_travel_minutes 为 0 表示没有明确上限。
- JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}"""
        weather = context.get("weather_info") or {}
        week_plan = context.get("week_plan")
        week_theme = str(getattr(week_plan, "theme", "") or "").strip()
        goals = getattr(week_plan, "goals", []) if week_plan else []
        if not isinstance(goals, list):
            goals = []
        world_context = str(context.get("world_context") or "").strip()[:5000]
        recent_chats = str(context.get("recent_chats") or "").strip()[:1600]
        memo = str(context.get("memo_str") or "").strip()[:2400]
        check_time = context.get("check_time")
        current_time = (
            check_time.strftime("%Y-%m-%d %H:%M")
            if hasattr(check_time, "strftime")
            else ""
        )
        dynamic_parts = [
            f"目标日期：{context.get('date_str') or ''}",
            f"当前时间：{current_time}",
            f"天气：{weather.get('raw') or '未知'}",
            f"周主题：{week_theme or '无'}",
            "周目标：" + "、".join(str(item) for item in goals[:4])
            if goals
            else "周目标：无",
            f"今日提示：{context.get('today_hint') or '无'}",
            f"建议活动：{context.get('today_suggested') or '无'}",
            f"用户指令与必要事项：\n{memo}" if memo else "",
            f"近期聊天：\n{recent_chats}" if recent_chats else "",
            f"当前世界资料：\n{world_context}" if world_context else "",
        ]
        dynamic = "\n\n".join(part for part in dynamic_parts if part)
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="日程地点预选资料")

    @staticmethod
    def _daily_location_candidates_context(payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(candidates, list) or not candidates:
            return ""
        lines = [
            "## 地图已确认地点候选",
            f"地图服务：{payload.get('map_provider') or '当前地图服务'}",
            f"居住城市：{payload.get('home_city') or '未提供'}",
            "这些候选是可选的，不要为了使用候选而强制外出；一旦使用具体 POI，必须原样采用其名称和结构化字段，不得另造或改写同类店名。travel_mode 已由地图结合路线、天气和活动意图选择，应作为前往该地点的通勤方式，不要被到达后的散步或逛街活动覆盖。没有合适候选时使用家或 generic 泛化场景。",
        ]
        for index, item in enumerate(candidates[:_FULL_DAY_LOCATION_LIMIT], start=1):
            if not isinstance(item, dict):
                continue
            details = [
                f"名称={item.get('name') or ''}",
                "place_kind=poi",
                f"place_scope={item.get('place_scope') or 'local'}",
                f"place_city={item.get('place_city') or ''}",
                f"place_hint={item.get('place_hint') or ''}",
                f"travel_mode={item.get('travel_mode') or '未指定'}",
            ]
            if item.get("travel_detail"):
                details.append(f"公共交通={item['travel_detail']}")
            if item.get("purpose"):
                details.insert(0, f"用途={item['purpose']}")
            if int(item.get("travel_minutes") or 0) > 0:
                details.append(f"从家参考通勤={int(item['travel_minutes'])}分钟")
            lines.append(f"{index}. " + "；".join(details))
        return "\n".join(lines)

    async def _prepare_daily_location_context(
        self,
        *,
        context: dict[str, Any],
        provider: Any,
        provider_id: str,
        session_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        domains = getattr(self, "domains", None)
        availability = getattr(domains, "map_tools_available", None)
        selector = getattr(domains, "prepare_daily_location_candidates", None)
        if not (callable(availability) and availability() and callable(selector)):
            return "", []
        try:
            configured_location_provider_id = str(
                getattr(
                    getattr(self, "config", None),
                    "location_planning_provider",
                    "",
                )
                or ""
            ).strip()
            location_provider = provider
            if configured_location_provider_id:
                location_provider_id = configured_location_provider_id
                configured_provider = await self._get_provider(location_provider_id)
                if configured_provider is not None:
                    location_provider = configured_provider
            else:
                (
                    default_provider,
                    default_provider_id,
                ) = await self._system_default_provider(provider_id)
                if default_provider is not None:
                    location_provider = default_provider
                    location_provider_id = default_provider_id or provider_id
                else:
                    location_provider_id = provider_id
            prompt = self._daily_location_request_prompt(context)
            completion = await self._call_llm_text(
                location_provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=location_provider_id,
            )
            parsed = extract_json_from_text(completion)
            requests = parsed.get("requests") if isinstance(parsed, dict) else None
            if not isinstance(requests, list) or not requests:
                logger.debug("[日程生成] 地点意图未提出具体 POI，继续生成日程。")
                return "", []
            request_limit = (
                _FULL_DAY_LOCATION_LIMIT
                if context.get("expected_coverage") == "full_day"
                else _PARTIAL_LOCATION_LIMIT
            )
            selected = await selector(
                requests,
                max_candidates=request_limit,
                weather_info=context.get("weather_info"),
            )
            location_context = self._daily_location_candidates_context(selected)
            if location_context:
                logger.debug(
                    f"[日程生成] 地图地点预选完成：候选={len(selected.get('candidates') or [])}；"
                    f"服务={selected.get('map_provider') or '当前地图服务'}"
                )
            else:
                logger.debug("[日程生成] 地图未返回可用地点候选，继续原始生成流程。")
            candidates = (
                selected.get("candidates") if isinstance(selected, dict) else []
            )
            return location_context, candidates if isinstance(candidates, list) else []
        except Exception as exc:
            logger.warning(f"[日程生成] 地图地点预选失败，已继续原始生成流程：{exc}")
            return "", []

    @staticmethod
    def _append_daily_location_context(prompt: str, location_context: str) -> str:
        context = str(location_context or "").strip()
        return f"{prompt}\n\n{context}" if context else prompt


__all__ = ["DailyLocationGenerationMixin", "DailyLocationPlanningMixin"]
