from __future__ import annotations

import asyncio
import datetime
import math
from typing import Any

from astrbot.api import logger

from ..config.options import LifeDomainSettings
from ..models import DayRecord, LifeActionIntent, LifeActionOutcome
from .location_audit import DailyLocationAuditMixin
from .maps import create_map_client, map_provider_label, normalize_map_provider
from .tools import get_week_id

INTERNAL_SIMULATED_ACTION_TYPES = frozenset(
    {
        "rest",
        "meal",
        "move",
        "travel",
        "work",
        "study",
        "groom",
        "cook",
        "order_food",
        "purchase",
        "chore",
        "exercise",
    }
)

_TRAVEL_SPEED_METERS_PER_SECOND = {
    "walking": 1.25,
    "cycling": 4.2,
    "driving": 8.5,
    "transit": 6.0,
}

_ROUTE_MODE_LABELS = {
    "walking": "步行",
    "cycling": "骑行",
    "driving": "驾车",
    "transit": "公交",
}

_ROUTE_SOURCE_LABELS = {
    "amap": "高德地图",
    "tencent": "腾讯地图",
    "baidu": "百度地图",
    "coordinate_estimate": "坐标估算",
    "default_estimate": "默认估算",
}


class LifeDomainService(DailyLocationAuditMixin):
    """协调生活领域记录、动作副作用和上下文预算。"""

    def __init__(
        self,
        settings: LifeDomainSettings,
        archive: Any,
    ):
        self.settings = settings
        self.archive = archive
        self.home_city = ""
        self._coordinates: dict[str, tuple[float, float]] = {}
        self._place_cities: dict[str, str] = {}
        self._geocode_misses: set[str] = set()
        self._home_location: dict[str, Any] | None = None
        self._home_location_retry_after = 0.0
        self._home_location_lock = asyncio.Lock()
        self._residence_boundary_date: str | None = None
        self._detected_residence_change_at = ""
        self.map_provider = normalize_map_provider(settings.map_provider)
        self.map_provider_label = map_provider_label(self.map_provider)
        self._map = create_map_client(settings)

    async def resolve_home_location(self) -> dict[str, Any] | None:
        """使用居住地解析唯一的居住城市和“家”坐标。"""

        if self._home_location is not None:
            return self._home_location
        home_address = str(self.settings.home_address or "").strip()
        if not home_address or not self._map.available:
            return None
        loop = asyncio.get_running_loop()
        if loop.time() < self._home_location_retry_after:
            return None
        async with self._home_location_lock:
            if self._home_location is not None:
                return self._home_location
            if loop.time() < self._home_location_retry_after:
                return None
            geocoded = await self._map.geocode(home_address, city_hint="")
            city = str(
                (geocoded or {}).get("city") or (geocoded or {}).get("province") or ""
            ).strip()
            try:
                coordinate = (
                    float((geocoded or {})["latitude"]),
                    float((geocoded or {})["longitude"]),
                )
            except (KeyError, TypeError, ValueError):
                coordinate = None
            if not city or coordinate is None:
                self._home_location_retry_after = loop.time() + 300.0
                logger.debug(
                    f"[日常生活] {self.map_provider_label}未能解析居住地，"
                    "天气和地点城市暂不可用。"
                )
                return None

            previous_coordinate = None
            getter = getattr(self.archive, "get_recent_places", None)
            if callable(getter):
                for place in await getter(0):
                    if (
                        place.name == "家"
                        and place.latitude is not None
                        and place.longitude is not None
                    ):
                        previous_coordinate = (
                            float(place.latitude),
                            float(place.longitude),
                        )
                        break

            if (
                previous_coordinate is not None
                and coordinate is not None
                and self._haversine(previous_coordinate, coordinate) > 10_000
            ):
                changed_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                resetter = getattr(self.archive, "reset_residence_context", None)
                if callable(resetter):
                    await resetter(
                        changed_at=changed_at,
                        week_id=get_week_id(datetime.datetime.now()),
                    )
                self._residence_boundary_date = changed_at[:10]
                self._detected_residence_change_at = changed_at

            self.home_city = city
            self._map.city = city
            await self._remember_tool_place(
                "家", geocoded or {}, f"{self.map_provider}_home_address"
            )
            self._coordinates[home_address] = coordinate
            self._place_cities[home_address] = self._place_cities.get("家", city)
            self._home_location = {
                "city": city,
                "coordinate": coordinate,
                "formatted_address": str(
                    (geocoded or {}).get("formatted_address") or home_address
                ).strip(),
            }
            logger.debug(f"[日常生活] 已从居住地解析天气城市：{city}")
            return self._home_location

    def set_residence_boundary(self, changed_at: str) -> None:
        """同步配置切换阶段已经写入的居住地边界。"""

        value = str(changed_at or "").strip()
        self._residence_boundary_date = value[:10] if value else ""

    def consume_detected_residence_change(self) -> str:
        """取出地图层检测到的跨居住地变化信号。"""

        changed_at = self._detected_residence_change_at
        self._detected_residence_change_at = ""
        return changed_at

    def invalidate_home_location_cache(self) -> None:
        """清除居住地解析缓存，供切换后的统一刷新重新解析。"""

        home_address = str(self.settings.home_address or "").strip()
        self.home_city = ""
        self._home_location = None
        self._home_location_retry_after = 0.0
        self._geocode_misses.discard(home_address)
        self._coordinates.pop(home_address, None)
        self._coordinates.pop("家", None)
        self._place_cities.pop(home_address, None)
        self._place_cities.pop("家", None)
        self._map.city = ""

    async def residence_boundary_date(self) -> str:
        """返回当前居住地上下文允许读取历史记录的最早日期。"""

        if self._residence_boundary_date is not None:
            return self._residence_boundary_date
        getter = getattr(self.archive, "get_residence_context_boundary", None)
        value = await getter() if callable(getter) else ""
        self._residence_boundary_date = str(value or "").strip()[:10]
        return self._residence_boundary_date

    async def resolve_weather_city(self) -> str:
        """返回居住地解析出的天气城市，不使用其他来源回退。"""

        location = await self.resolve_home_location()
        return str((location or {}).get("city") or "").strip()

    def should_simulate(self, action: LifeActionIntent) -> bool:
        """判断明确计划动作是否允许由虚拟生活时钟产生模拟回执。"""

        return bool(
            self.settings.enabled
            and self.settings.simulate_internal_actions
            and action.action_type in INTERNAL_SIMULATED_ACTION_TYPES
        )

    async def sync_activity_sessions(
        self, day: DayRecord, *, now: datetime.datetime
    ) -> None:
        """把显式计划动作同步为活动会话，不分析活动文案。"""

        if not self.settings.enabled or not self.settings.activity_tracking_enabled:
            return
        import json

        raw = str((day.meta or {}).get("planned_life_actions") or "")
        try:
            values = json.loads(raw) if raw else []
        except (TypeError, ValueError, json.JSONDecodeError):
            values = []
        actions: list[LifeActionIntent] = []
        for value in values if isinstance(values, list) else []:
            action = LifeActionIntent.from_value(value)
            if (
                action.action_id
                and action.timeline_index is not None
                and 0 <= action.timeline_index < len(day.timeline)
            ):
                actions.append(action)
        reconciler = getattr(self.archive, "reconcile_activity_sessions", None)
        if callable(reconciler):
            await reconciler(day.date, {action.action_id for action in actions})
        saver = getattr(self.archive, "upsert_activity_session", None)
        if not callable(saver):
            return
        for action in actions:
            item = day.timeline[action.timeline_index]
            started_at = self._timeline_datetime(day.date, item.time)
            duration_seconds = max(0, action.duration_minutes) * 60
            ended_at = ""
            if item.execution_state in {"completed", "expired", "cancelled", "skipped"}:
                ended_at = item.execution_updated_at or now.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            status = {
                "planned": "planned",
                "active": "active",
                "completed": "completed",
                "expired": "expired",
                "cancelled": "cancelled",
                "skipped": "skipped",
            }.get(item.execution_state, "planned")
            await saver(
                {
                    "action_id": action.action_id,
                    "date": day.date,
                    "activity_type": action.action_type,
                    "title": action.target or item.activity,
                    "status": status,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "last_heartbeat_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration_seconds": duration_seconds,
                    "source": action.source or "daily_plan",
                    "evidence": [item.execution_evidence or action.evidence],
                    "metadata": {
                        "timeline_index": action.timeline_index,
                        "payload": action.payload,
                    },
                }
            )

    @staticmethod
    def _timeline_datetime(date_str: str, time_text: str) -> str:
        text = str(time_text or "").strip()
        if not text:
            return ""
        return f"{date_str} {text}:00" if len(text) == 5 else f"{date_str} {text}"

    async def validate_action(self, action: LifeActionIntent) -> tuple[bool, str]:
        """校验领域前置条件，避免库存被扣成负数。"""

        if not self.settings.enabled:
            return True, ""
        if action.action_type in {"move", "travel"} and self.settings.location_enabled:
            origin = str(action.payload.get("origin") or "").strip()
            destination = str(
                action.payload.get("destination") or action.target or ""
            ).strip()
            if origin and destination and action.duration_minutes > 0:
                route = await self.estimate_route(
                    origin,
                    destination,
                    str(action.payload.get("travel_mode") or "walking"),
                )
                required_minutes = math.ceil(
                    max(0, int(route.get("duration_seconds") or 0)) / 60
                )
                if required_minutes > action.duration_minutes:
                    return (
                        False,
                        f"预留出行时间不足：需要约 {required_minutes} 分钟，计划为 {action.duration_minutes} 分钟",
                    )
        if (
            action.action_type not in {"meal", "cook"}
            or not self.settings.pantry_enabled
        ):
            return True, ""
        ingredients = action.payload.get("ingredients")
        if not isinstance(ingredients, list):
            return True, ""
        getter = getattr(self.archive, "get_pantry_items", None)
        if not callable(getter):
            return True, ""
        stock = {
            str(item.get("name") or "").strip(): float(item.get("quantity") or 0)
            for item in await getter(limit=0)
        }
        required: dict[str, float] = {}
        for item in ingredients:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            try:
                quantity = max(0.0, float(item.get("quantity") or 0))
            except (TypeError, ValueError):
                continue
            if name:
                required[name] = required.get(name, 0.0) + quantity
        missing = [
            name
            for name, quantity in required.items()
            if quantity > stock.get(name, 0.0)
        ]
        if missing:
            return False, "库存不足：" + "、".join(missing[:6])
        return True, ""

    async def apply_action(
        self,
        day: DayRecord,
        action: LifeActionIntent,
        outcome: LifeActionOutcome,
        *,
        receipt_status: str = "confirmed",
    ) -> None:
        """在主动作提交后幂等写入对应领域记录。"""

        if not self.settings.enabled or outcome.status != "committed":
            return
        occurred_at = outcome.committed_at
        evidence = [outcome.evidence] if outcome.evidence else []
        source = (
            "life_action_simulation" if receipt_status == "simulated" else "life_action"
        )
        if action.action_type in {"meal", "cook", "order_food"}:
            await self._apply_meal(
                day, action, occurred_at=occurred_at, evidence=evidence, source=source
            )
        elif action.action_type == "purchase":
            await self._apply_purchase(action, occurred_at=occurred_at, source=source)
        elif action.action_type == "chore":
            await self._apply_chore(
                action, occurred_at=occurred_at, evidence=evidence, source=source
            )
        elif action.action_type == "exercise":
            await self._apply_fitness(
                day, action, occurred_at=occurred_at, evidence=evidence, source=source
            )
        elif action.action_type in {"move", "travel"}:
            await self._apply_route(day, action, occurred_at=occurred_at)

    async def _apply_meal(
        self,
        day: DayRecord,
        action: LifeActionIntent,
        *,
        occurred_at: str,
        evidence: list[str],
        source: str,
    ) -> None:
        if not self.settings.meals_enabled:
            return
        ingredients = action.payload.get("ingredients")
        ingredients = ingredients if isinstance(ingredients, list) else []
        saver = getattr(self.archive, "save_meal_record", None)
        recipe_id = str(action.payload.get("recipe_id") or "").strip()
        save_recipe = getattr(self.archive, "upsert_recipe", None)
        if recipe_id and callable(save_recipe):
            await save_recipe(
                {
                    "id": recipe_id,
                    "name": action.target
                    or str(action.payload.get("name") or "未命名食谱"),
                    "meal_type": str(action.payload.get("meal_type") or ""),
                    "ingredients": ingredients,
                    "tags": action.payload.get("tags")
                    if isinstance(action.payload.get("tags"), list)
                    else [],
                    "source": source,
                }
            )
        if callable(saver):
            await saver(
                {
                    "action_id": action.action_id,
                    "date": day.date,
                    "meal_type": str(action.payload.get("meal_type") or ""),
                    "name": action.target or str(action.payload.get("name") or "用餐"),
                    "recipe_id": recipe_id,
                    "status": "completed",
                    "ingredients": ingredients,
                    "place": str(action.payload.get("place") or ""),
                    "source": source,
                    "evidence": evidence,
                    "occurred_at": occurred_at,
                }
            )
        if not self.settings.pantry_enabled or action.action_type == "order_food":
            return
        adjust = getattr(self.archive, "adjust_pantry_item", None)
        if not callable(adjust):
            return
        for item in ingredients:
            if not isinstance(item, dict):
                continue
            try:
                quantity = max(0.0, float(item.get("quantity") or 0))
            except (TypeError, ValueError):
                continue
            name = str(item.get("name") or "").strip()
            if name and quantity:
                await adjust(
                    name,
                    -quantity,
                    unit=str(item.get("unit") or ""),
                    reason=f"完成餐食：{action.target or '用餐'}",
                    action_id=action.action_id,
                    occurred_at=occurred_at,
                    source=source,
                )

    async def _apply_purchase(
        self, action: LifeActionIntent, *, occurred_at: str, source: str
    ) -> None:
        if not self.settings.pantry_enabled:
            return
        items = action.payload.get("items")
        if not isinstance(items, list):
            return
        adjust = getattr(self.archive, "adjust_pantry_item", None)
        if not callable(adjust):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            try:
                quantity = max(0.0, float(item.get("quantity") or 0))
            except (TypeError, ValueError):
                continue
            if name and quantity:
                await adjust(
                    name,
                    quantity,
                    unit=str(item.get("unit") or ""),
                    minimum_quantity=float(item.get("minimum_quantity") or 0),
                    expires_at=str(item.get("expires_at") or ""),
                    reason="采购入库",
                    action_id=action.action_id,
                    occurred_at=occurred_at,
                    source=source,
                )

    async def _apply_chore(
        self,
        action: LifeActionIntent,
        *,
        occurred_at: str,
        evidence: list[str],
        source: str,
    ) -> None:
        if not self.settings.chores_enabled:
            return
        chore_id = str(action.payload.get("chore_id") or action.action_id)
        name = action.target or str(action.payload.get("name") or "家务")
        cadence_days = max(0, int(action.payload.get("cadence_days") or 0))
        try:
            finished = datetime.datetime.strptime(occurred_at, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            finished = datetime.datetime.now()
        next_due_at = (
            (finished + datetime.timedelta(days=cadence_days)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if cadence_days
            else ""
        )
        upsert = getattr(self.archive, "upsert_chore", None)
        if callable(upsert):
            await upsert(
                {
                    "id": chore_id,
                    "name": name,
                    "cadence_days": cadence_days,
                    "effort": int(action.payload.get("effort") or 1),
                    "last_completed_at": occurred_at,
                    "next_due_at": next_due_at,
                    "enabled": True,
                    "source": source,
                }
            )
        saver = getattr(self.archive, "save_chore_record", None)
        if callable(saver):
            await saver(
                {
                    "action_id": action.action_id,
                    "chore_id": chore_id,
                    "name": name,
                    "status": "completed",
                    "duration_minutes": action.duration_minutes,
                    "evidence": evidence,
                    "occurred_at": occurred_at,
                    "source": source,
                }
            )

    async def _apply_fitness(
        self,
        day: DayRecord,
        action: LifeActionIntent,
        *,
        occurred_at: str,
        evidence: list[str],
        source: str,
    ) -> None:
        if not self.settings.fitness_enabled:
            return
        intensity = max(1, min(5, int(action.payload.get("intensity") or 2)))
        duration = max(0, action.duration_minutes)
        saver = getattr(self.archive, "save_fitness_record", None)
        if callable(saver):
            await saver(
                {
                    "action_id": action.action_id,
                    "date": day.date,
                    "activity": action.target
                    or str(action.payload.get("activity") or "运动"),
                    "duration_minutes": duration,
                    "intensity": intensity,
                    "load_score": round(duration * intensity / 5.0, 2),
                    "status": "completed",
                    "source": source,
                    "evidence": evidence,
                    "occurred_at": occurred_at,
                }
            )

    async def _apply_route(
        self, day: DayRecord, action: LifeActionIntent, *, occurred_at: str
    ) -> None:
        if not self.settings.location_enabled:
            return
        origin = str(
            action.payload.get("origin") or (day.meta or {}).get("previous_place") or ""
        ).strip()
        destination = str(
            action.payload.get("destination") or action.target or ""
        ).strip()
        if not origin or not destination:
            return
        route = await self.estimate_route(
            origin,
            destination,
            str(action.payload.get("travel_mode") or "walking"),
            now=occurred_at,
        )
        if route:
            day.meta["last_route"] = self._compact_route(route)

    @staticmethod
    def _compact_route(route: dict[str, Any]) -> str:
        import json

        return json.dumps(route, ensure_ascii=False, separators=(",", ":"))

    async def estimate_route(
        self,
        origin: str,
        destination: str,
        mode: str = "walking",
        *,
        now: str = "",
    ) -> dict[str, Any]:
        """优先读取缓存，再使用当前地图服务或坐标直线估算。"""

        await self.resolve_home_location()
        current = self._parse_datetime(now) or datetime.datetime.now()
        getter = getattr(self.archive, "get_route", None)
        if callable(getter):
            cached = await getter(origin, destination, mode)
            expires_at = self._parse_datetime((cached or {}).get("expires_at"))
            if (
                cached
                and cached.get("provider")
                in {
                    self.map_provider,
                    "coordinate_estimate",
                    "default_estimate",
                }
                and (expires_at is None or expires_at > current)
            ):
                return cached

        origin_coordinate = await self._place_coordinate(origin)
        destination_coordinate = await self._place_coordinate(destination)
        route = None
        if origin_coordinate and destination_coordinate and self._map.available:
            route = await self._map.route(
                origin_coordinate,
                destination_coordinate,
                mode,
                origin_city=self._place_cities.get(origin, ""),
                destination_city=self._place_cities.get(destination, ""),
            )
        if not route and origin_coordinate and destination_coordinate:
            distance = self._haversine(origin_coordinate, destination_coordinate)
            speed = _TRAVEL_SPEED_METERS_PER_SECOND.get(mode, 1.25)
            route = {
                "distance_meters": round(distance, 1),
                "duration_seconds": max(60, int(distance / speed)),
                "provider": "coordinate_estimate",
                "confidence": 0.55,
            }
        if not route:
            route = {
                "distance_meters": 0,
                "duration_seconds": self.settings.default_travel_minutes * 60,
                "provider": "default_estimate",
                "confidence": 0.2,
            }
        payload = {
            "origin_name": origin,
            "destination_name": destination,
            "travel_mode": mode,
            **route,
            "fetched_at": current.strftime("%Y-%m-%d %H:%M:%S"),
            "expires_at": (current + datetime.timedelta(hours=12)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        saver = getattr(self.archive, "upsert_route", None)
        return await saver(payload) if callable(saver) else payload

    async def _place_coordinate(self, name: str) -> tuple[float, float] | None:
        if name in self._coordinates:
            return self._coordinates[name]
        getter = getattr(self.archive, "get_recent_places", None)
        places = await getter(0) if callable(getter) else []
        matched_place = None
        for place in places:
            coordinate_source = str(place.coordinate_source or "").strip()
            is_manual_coordinate = coordinate_source == "manual" or (
                coordinate_source == "config" or coordinate_source.startswith("config_")
            )
            if (
                place.name == name
                and place.latitude is not None
                and place.longitude is not None
                and not is_manual_coordinate
            ):
                coordinate = float(place.latitude), float(place.longitude)
                self._coordinates[name] = coordinate
                return coordinate
            if place.name == name:
                matched_place = place
        if not self._map.available or name in self._geocode_misses:
            return None
        address = str(getattr(matched_place, "hint", "") or name).strip()
        geocoded = await self._map.geocode(address)
        if not geocoded:
            self._geocode_misses.add(name)
            return None
        coordinate = (
            float(geocoded["latitude"]),
            float(geocoded["longitude"]),
        )
        self._coordinates[name] = coordinate
        self._place_cities[name] = str(
            geocoded.get("citycode")
            or geocoded.get("adcode")
            or geocoded.get("city")
            or self.home_city
        ).strip()
        updater = getattr(self.archive, "update_place_coordinates", None)
        if callable(updater):
            await updater(
                name,
                coordinate[0],
                coordinate[1],
                source=f"{self.map_provider}_geocode",
                updated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        return coordinate

    def map_tools_available(self) -> bool:
        """判断当前地图自然语言工具是否可以加入模型工具集。"""

        return bool(
            self.settings.enabled
            and self.settings.location_enabled
            and bool(self.settings.home_address)
            and self._map.available
        )

    async def tool_place_search(
        self,
        query: str,
        *,
        near: str = "",
        category: str = "",
        radius_meters: int = 3000,
        limit: int = 5,
    ) -> dict[str, Any]:
        """搜索符合自然语言需求的地点，并返回适合模型阅读的结果。"""

        if not self.map_tools_available():
            return self._map_unavailable_result()
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "reason": "缺少要搜索的地点或场所需求。"}
        home_location = await self.resolve_home_location()
        if home_location is None:
            return {
                "ok": False,
                "reason": "居住地无法解析，暂时不能确定地点搜索城市。",
            }
        near = str(near or "").strip()
        center = None
        resolved_near = None
        if near:
            resolved_near = await self._resolve_tool_place(near)
            if not resolved_near:
                return {
                    "ok": False,
                    "reason": f"无法确认搜索中心“{near}”，请补充更明确的地点。",
                    "suggestions": await self._public_place_suggestions(near),
                }
            center = resolved_near["coordinate"]
        places = await self._map.search_places(
            query,
            center=center,
            category=str(category or "").strip(),
            radius_meters=max(100, min(50000, int(radius_meters))),
            limit=max(1, min(10, int(limit))),
        )
        if not places:
            return {
                "ok": False,
                "reason": f"{self.map_provider_label}没有找到符合条件的地点。",
                "query": query,
                "suggestions": await self._public_place_suggestions(query),
            }
        public_places = [self._public_poi(item) for item in places]
        return {
            "ok": True,
            "query": query,
            "search_scope": (
                f"{resolved_near['name']}附近" if resolved_near else self.home_city
            ),
            "summary": f"找到 {len(public_places)} 个符合条件的地点。",
            "map_provider": self.map_provider_label,
            "places": public_places,
        }

    async def tool_route_plan(
        self,
        origin: str,
        destination: str,
        *,
        mode: str = "walking",
    ) -> dict[str, Any]:
        """解析自然语言起终点，并查询单一或多种交通方式。"""

        if not self.map_tools_available():
            return self._map_unavailable_result()
        origin = str(origin or "").strip()
        destination = str(destination or "").strip()
        if not origin or not destination:
            return {"ok": False, "reason": "路线规划需要明确的出发地和目的地。"}
        if await self.resolve_home_location() is None:
            return {
                "ok": False,
                "reason": "居住地无法解析，暂时不能规划路线。",
            }
        resolved_origin, resolved_destination = await asyncio.gather(
            self._resolve_tool_place(origin),
            self._resolve_tool_place(destination),
        )
        if not resolved_origin or not resolved_destination:
            missing = origin if not resolved_origin else destination
            return {
                "ok": False,
                "reason": f"无法确认地点“{missing}”，请补充更明确的名称或地址。",
                "suggestions": await self._public_place_suggestions(missing),
            }
        normalized_mode = str(mode or "walking").strip().lower()
        modes = (
            list(_ROUTE_MODE_LABELS)
            if normalized_mode == "compare"
            else [
                normalized_mode if normalized_mode in _ROUTE_MODE_LABELS else "walking"
            ]
        )
        route_values = await asyncio.gather(
            *(self.estimate_route(origin, destination, item) for item in modes)
        )
        routes = [
            self._public_route(item_mode, route)
            for item_mode, route in zip(modes, route_values, strict=True)
        ]
        traffic = None
        if "driving" in modes:
            traffic = await self._map.traffic_status(resolved_origin["coordinate"])
        recommended = min(
            routes,
            key=lambda item: int(item.get("duration_minutes") or 0),
        )
        return {
            "ok": True,
            "origin": resolved_origin["name"],
            "destination": resolved_destination["name"],
            "routes": routes,
            "recommended": recommended,
            "traffic": traffic or {},
            "map_provider": self.map_provider_label,
        }

    async def tool_place_detail(self, poi_id: str) -> dict[str, Any]:
        """读取先前地点搜索返回的 POI 详情。"""

        if not self.map_tools_available():
            return self._map_unavailable_result()
        poi_id = str(poi_id or "").strip()
        if not poi_id:
            return {"ok": False, "reason": "缺少地点 POI ID。"}
        detail = await self._map.place_detail(poi_id)
        if not detail:
            return {"ok": False, "reason": "没有查到这个地点的详情。"}
        return {
            "ok": True,
            "map_provider": self.map_provider_label,
            "place": self._public_poi(detail, include_details=True),
        }

    async def tool_outing_plan(
        self,
        request: str,
        stops: list[str],
        *,
        start: str,
        mode: str = "walking",
        duration_minutes: int = 120,
        max_stops: int = 3,
    ) -> dict[str, Any]:
        """根据结构化停靠目标组合地点搜索和逐段路线。"""

        if not self.map_tools_available():
            return self._map_unavailable_result()
        start = str(start or "").strip()
        stop_queries = [
            str(item or "").strip()
            for item in (stops if isinstance(stops, list) else [])
            if str(item or "").strip()
        ][: max(1, min(5, int(max_stops)))]
        if not start or not stop_queries:
            return {
                "ok": False,
                "reason": "外出计划需要明确的出发地和至少一个停靠目标。",
            }
        if await self.resolve_home_location() is None:
            return {
                "ok": False,
                "reason": "居住地无法解析，暂时不能安排外出路线。",
            }
        current = await self._resolve_tool_place(start)
        if not current:
            return {
                "ok": False,
                "reason": f"无法确认出发地“{start}”。",
                "suggestions": await self._public_place_suggestions(start),
            }
        normalized_mode = str(mode or "walking").strip().lower()
        if normalized_mode not in _ROUTE_MODE_LABELS:
            normalized_mode = "walking"
        time_budget = max(30, min(1440, int(duration_minutes)))
        plan = []
        warnings = []
        total_travel_minutes = 0
        current_name = start
        for query in stop_queries:
            candidates = await self._map.search_places(
                query,
                center=current["coordinate"],
                radius_meters=8000,
                limit=3,
            )
            candidate = next(
                (
                    item
                    for item in candidates
                    if item.get("coordinate") and item.get("name")
                ),
                None,
            )
            if not candidate:
                warnings.append(f"没有找到停靠目标：{query}")
                continue
            await self._remember_tool_place(
                candidate["name"], candidate, f"{self.map_provider}_poi"
            )
            route = await self.estimate_route(
                current_name,
                str(candidate["name"]),
                normalized_mode,
            )
            travel_minutes = self._route_minutes(route)
            if total_travel_minutes + travel_minutes > time_budget:
                warnings.append(f"前往{candidate['name']}会超出时间预算")
                continue
            total_travel_minutes += travel_minutes
            plan.append(
                {
                    "stop": len(plan) + 1,
                    "goal": query,
                    "place": self._public_poi(candidate),
                    "from": current["name"],
                    "travel": self._public_route(normalized_mode, route),
                }
            )
            current_name = str(candidate["name"])
            current = {
                "name": current_name,
                "coordinate": candidate["coordinate"],
            }
        if not plan:
            return {
                "ok": False,
                "reason": "没有找到能放入当前时间预算的外出地点。",
                "warnings": warnings,
            }
        return {
            "ok": True,
            "request": str(request or "").strip(),
            "start": start,
            "mode": _ROUTE_MODE_LABELS[normalized_mode],
            "duration_budget_minutes": time_budget,
            "travel_minutes": total_travel_minutes,
            "remaining_minutes": max(0, time_budget - total_travel_minutes),
            "map_provider": self.map_provider_label,
            "stops": plan,
            "warnings": warnings,
        }

    async def _resolve_tool_place(self, name: str) -> dict[str, Any] | None:
        name = str(name or "").strip()
        if not name:
            return None
        if name in self._coordinates:
            return {"name": name, "coordinate": self._coordinates[name]}
        tips = await self._map.input_tips(name, limit=5)
        candidate = next((item for item in tips if item.get("coordinate")), None)
        if candidate:
            await self._remember_tool_place(
                name, candidate, f"{self.map_provider}_input_tip"
            )
            return {
                "name": str(candidate.get("name") or name),
                "coordinate": candidate["coordinate"],
            }
        geocoded = await self._map.geocode(name)
        if not geocoded:
            return None
        await self._remember_tool_place(name, geocoded, f"{self.map_provider}_geocode")
        return {
            "name": str(geocoded.get("formatted_address") or name),
            "coordinate": self._coordinates[name],
        }

    async def _remember_tool_place(
        self, name: str, place: dict[str, Any], source: str
    ) -> None:
        coordinate = place.get("coordinate")
        if coordinate is None and {
            "latitude",
            "longitude",
        }.issubset(place):
            coordinate = float(place["latitude"]), float(place["longitude"])
        if not (isinstance(coordinate, tuple) and len(coordinate) == 2):
            return
        latitude, longitude = float(coordinate[0]), float(coordinate[1])
        self._coordinates[name] = latitude, longitude
        self._place_cities[name] = str(
            place.get("citycode")
            or place.get("adcode")
            or place.get("city")
            or self.home_city
        ).strip()
        updater = getattr(self.archive, "update_place_coordinates", None)
        if callable(updater):
            await updater(
                name,
                latitude,
                longitude,
                source=source,
                updated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

    async def _public_place_suggestions(self, query: str) -> list[dict[str, str]]:
        tips = await self._map.input_tips(query, limit=5)
        return [
            {
                "poi_id": str(item.get("poi_id") or ""),
                "name": str(item.get("name") or ""),
                "address": str(item.get("address") or item.get("district") or ""),
            }
            for item in tips
            if item.get("name")
        ]

    @staticmethod
    def _public_poi(
        place: dict[str, Any], *, include_details: bool = False
    ) -> dict[str, Any]:
        result = {
            "poi_id": str(place.get("poi_id") or ""),
            "name": str(place.get("name") or ""),
            "address": str(place.get("address") or ""),
            "category": str(place.get("category") or ""),
            "distance_meters": place.get("distance_meters"),
            "telephone": str(place.get("telephone") or ""),
            "opening_hours": str(place.get("opening_hours") or ""),
            "rating": place.get("rating"),
            "average_cost": place.get("average_cost"),
        }
        if include_details:
            result["photos"] = list(place.get("photos") or [])[:3]
            result["province"] = str(place.get("province") or "")
            result["city"] = str(place.get("city") or "")
            result["district"] = str(place.get("district") or "")
        return {
            key: value
            for key, value in result.items()
            if value is not None and value != ""
        }

    @classmethod
    def _public_route(cls, mode: str, route: dict[str, Any]) -> dict[str, Any]:
        return {
            "mode": mode,
            "mode_label": _ROUTE_MODE_LABELS.get(mode, mode),
            "distance_meters": round(float(route.get("distance_meters") or 0), 1),
            "duration_minutes": cls._route_minutes(route),
            "source": _ROUTE_SOURCE_LABELS.get(
                str(route.get("provider") or ""), "其他估算"
            ),
            "confidence": float(route.get("confidence") or 0),
        }

    @staticmethod
    def _route_minutes(route: dict[str, Any]) -> int:
        return max(1, math.ceil(float(route.get("duration_seconds") or 0) / 60))

    def _map_unavailable_result(self) -> dict[str, Any]:
        return {
            "ok": False,
            "reason": (
                f"{self.map_provider_label}自然语言工具未启用，或尚未配置"
                "居住地和对应的服务端 Key。"
            ),
        }

    @staticmethod
    def _haversine(
        origin: tuple[float, float], destination: tuple[float, float]
    ) -> float:
        lat1, lon1 = map(math.radians, origin)
        lat2, lon2 = map(math.radians, destination)
        delta_lat = lat2 - lat1
        delta_lon = lon2 - lon1
        value = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        return 6_371_000 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))

    @staticmethod
    def _parse_datetime(value: Any) -> datetime.datetime | None:
        text = str(value or "").strip()
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(text, pattern)
            except (TypeError, ValueError):
                continue
        return None

    async def snapshot(self, limit: int = 20) -> dict[str, Any]:
        """返回 Dashboard 和提示上下文共用的领域快照。"""

        if not self.settings.enabled:
            return self._disabled_snapshot()
        archive = self.archive
        getter = getattr(archive, "get_domain_snapshot", None)
        if callable(getter):
            snapshot = await getter(limit=limit)
            return {
                "enabled": True,
                **snapshot,
            }
        sessions = await archive.get_activity_sessions(limit=limit)
        pantry = await archive.get_pantry_items(limit=limit)
        recipes = await archive.get_recipes(limit=limit)
        meals = await archive.get_meal_records(limit=limit)
        chores = await archive.get_chores(limit=limit)
        chore_records = await archive.get_chore_records(limit=limit)
        fitness = await archive.get_fitness_records(limit=limit)
        action_items = await archive.get_conversation_action_items(limit=limit)
        timeline = await archive.get_unified_life_timeline(limit=max(limit, 40))
        return {
            "enabled": True,
            "activity_sessions": sessions,
            "pantry": pantry,
            "recipes": recipes,
            "meals": meals,
            "chores": chores,
            "chore_records": chore_records,
            "fitness": fitness,
            "conversation_actions": action_items,
            "timeline": timeline,
        }

    def _disabled_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "activity_sessions": [],
            "pantry": [],
            "recipes": [],
            "meals": [],
            "chores": [],
            "chore_records": [],
            "fitness": [],
            "conversation_actions": [],
            "timeline": [],
        }

    async def format_context(self) -> str:
        """按稳定优先级和字符预算生成生活领域上下文。"""

        if not self.settings.enabled:
            return ""
        snapshot = await self.snapshot(limit=8)
        blocks: list[str] = []
        if self.home_city:
            blocks.append(
                f"当前居住城市：{self.home_city}（当前地点和天气判断以此为准）"
            )
        action_items = [
            item
            for item in snapshot["conversation_actions"]
            if item.get("status") in {"open", "pending"}
        ]
        if action_items:
            blocks.append(
                "待办行动项："
                + "；".join(
                    f"{item.get('title')}（负责人：{item.get('owner') or '未定'}，截止：{item.get('due_at') or '未定'}）"
                    for item in action_items[:5]
                )
            )
        due_chores = [item for item in snapshot["chores"] if item.get("enabled")]
        if due_chores:
            blocks.append(
                "家务轮换："
                + "；".join(
                    f"{item.get('name')}（下次：{item.get('next_due_at') or '未设'}）"
                    for item in due_chores[:5]
                )
            )
        pantry = snapshot["pantry"]
        if pantry:
            blocks.append(
                "现有库存："
                + "、".join(
                    f"{item.get('name')} {item.get('quantity')}{item.get('unit') or ''}"
                    for item in pantry[:10]
                )
            )
        meals = snapshot["meals"]
        if meals:
            blocks.append(
                "近期饮食："
                + "；".join(
                    f"{item.get('date')} {item.get('name')}" for item in meals[:4]
                )
            )
        fitness = snapshot["fitness"]
        if fitness:
            blocks.append(
                "近期运动："
                + "；".join(
                    f"{item.get('date')} {item.get('activity')} {item.get('duration_minutes')}分钟"
                    for item in fitness[:4]
                )
            )
        if not blocks:
            return ""
        text = "## 生活实况（结构化记录）\n" + "\n".join(
            f"- {block}" for block in blocks
        )
        if not self.settings.context_budget_enabled:
            return text
        limit = max(600, self.settings.context_budget_chars)
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"


__all__ = ["INTERNAL_SIMULATED_ACTION_TYPES", "LifeDomainService"]
