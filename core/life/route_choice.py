from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

ROUTE_MODES = frozenset({"walking", "cycling", "driving", "transit"})
AUTO_ROUTE_MODE = "auto"


@dataclass(frozen=True, slots=True)
class RouteChoiceContext:
    """交通方式选择所需的天气与身体状态。"""

    temperature: float | None = None
    is_hot: bool = False
    is_rainy: bool = False
    is_foggy: bool = False
    energy: float | None = None

    @classmethod
    def from_values(
        cls,
        weather_info: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> RouteChoiceContext:
        weather = weather_info if isinstance(weather_info, dict) else {}
        state = state if isinstance(state, dict) else {}
        return cls(
            temperature=_optional_number(weather.get("temp")),
            is_hot=bool(weather.get("is_hot")),
            is_rainy=bool(weather.get("is_rainy")),
            is_foggy=bool(weather.get("is_foggy")),
            energy=_optional_number(state.get("energy")),
        )


def route_query_modes(requested_mode: str, *, locked: bool = False) -> tuple[str, ...]:
    """返回需要向地图查询的交通方式，避免无意义地请求所有路线。"""

    requested = str(requested_mode or "").strip().lower()
    if locked and requested in ROUTE_MODES:
        return (requested,)

    modes: list[str] = []
    if requested in ROUTE_MODES:
        modes.append(requested)
    for mode in ("walking", "transit"):
        if mode not in modes:
            modes.append(mode)
    if requested in {"cycling", "driving"} and requested not in modes:
        modes.insert(0, requested)
    return tuple(modes)


def default_route_mode(
    distance_meters: float,
    context: RouteChoiceContext,
) -> str:
    """在没有明确交通偏好时按实际负担选择初始查询方式。"""

    distance_limit, _ = walking_comfort_limits(context)
    return "walking" if max(0.0, distance_meters) <= distance_limit else "transit"


def walking_comfort_limits(context: RouteChoiceContext) -> tuple[float, int]:
    """返回当前条件下较自然的步行距离和时间上限。"""

    distance_limit = 2600.0
    minute_limit = 35
    if context.is_hot or (
        context.temperature is not None and context.temperature >= 30
    ):
        distance_limit = 1800.0
        minute_limit = 25
    if (
        context.is_rainy
        or context.is_foggy
        or (context.temperature is not None and context.temperature >= 35)
        or (context.energy is not None and context.energy < 35)
    ):
        distance_limit = 1200.0
        minute_limit = 20
    return distance_limit, minute_limit


def route_needs_comparison(
    mode: str,
    route: dict[str, Any],
    context: RouteChoiceContext,
) -> bool:
    """判断当前路线是否应与其他方式比较，而非只验证能否到达。"""

    normalized = str(mode or "").strip().lower()
    if normalized not in ROUTE_MODES:
        return True
    if normalized == "walking":
        distance_limit, minute_limit = walking_comfort_limits(context)
        return (
            route_distance(route) > distance_limit
            or route_minutes(route) > minute_limit
        )
    if normalized == "cycling" and (context.is_rainy or context.is_foggy):
        return True
    return False


def choose_practical_route(
    options: Iterable[tuple[str, dict[str, Any]]],
    *,
    requested_mode: str = AUTO_ROUTE_MODE,
    locked: bool = False,
    context: RouteChoiceContext | None = None,
    available_minutes: int | None = None,
    max_minutes: int | None = None,
) -> tuple[str, dict[str, Any], int] | None:
    """综合路线、天气、体力和时间窗选择实际可用的交通方式。"""

    route_context = context or RouteChoiceContext()
    measured = [
        (str(mode or "").strip().lower(), route, route_minutes(route))
        for mode, route in options
        if str(mode or "").strip().lower() in ROUTE_MODES
        and isinstance(route, dict)
        and route_minutes(route) > 0
    ]
    if not measured:
        return None

    requested = str(requested_mode or "").strip().lower()
    if locked and requested in ROUTE_MODES:
        explicit = next((item for item in measured if item[0] == requested), None)
        if explicit is not None:
            return explicit

    candidates = measured
    if max_minutes is not None and max_minutes > 0:
        within_limit = [item for item in candidates if item[2] <= max_minutes]
        if within_limit:
            candidates = within_limit
        else:
            return None
    if available_minutes is not None:
        available = max(0, available_minutes)
        fitting = [item for item in candidates if item[2] <= available]
        if fitting:
            candidates = fitting
        else:
            return min(candidates, key=lambda item: item[2])

    return min(
        candidates,
        key=lambda item: _route_score(
            item[0],
            item[1],
            item[2],
            requested_mode=requested,
            context=route_context,
        ),
    )


def route_minutes(route: dict[str, Any]) -> int:
    """读取统一地图路线中的向上取整分钟数。"""

    try:
        seconds = max(0.0, float(route.get("duration_seconds") or 0))
    except (TypeError, ValueError):
        return 0
    return math.ceil(seconds / 60) if seconds > 0 else 0


def route_distance(route: dict[str, Any]) -> float:
    """读取统一地图路线中的非负距离。"""

    try:
        return max(0.0, float(route.get("distance_meters") or 0))
    except (TypeError, ValueError):
        return 0.0


def _route_score(
    mode: str,
    route: dict[str, Any],
    minutes: int,
    *,
    requested_mode: str,
    context: RouteChoiceContext,
) -> float:
    distance = route_distance(route)
    score = float(minutes)
    if mode == "walking":
        distance_limit, minute_limit = walking_comfort_limits(context)
        score += max(0.0, distance - distance_limit) / 80.0
        score += max(0, minutes - minute_limit) * 1.6
        if context.is_rainy or context.is_foggy:
            score += 28.0
        elif context.is_hot and minutes > 15:
            score += 14.0
    elif mode == "transit":
        score += 7.0
        if distance < 1200:
            score += 20.0
    elif mode == "cycling":
        score += 10.0
        if context.is_hot:
            score += 10.0
        if context.is_rainy or context.is_foggy:
            score += 40.0
    elif mode == "driving":
        # 没有车辆事实时不能因为更快就默认假设可以驾车。
        score += 18.0
        if distance < 5000:
            score += 18.0

    if requested_mode == mode:
        score -= 3.0
    return score


def _optional_number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "AUTO_ROUTE_MODE",
    "ROUTE_MODES",
    "RouteChoiceContext",
    "choose_practical_route",
    "default_route_mode",
    "route_distance",
    "route_minutes",
    "route_needs_comparison",
    "route_query_modes",
    "walking_comfort_limits",
]
