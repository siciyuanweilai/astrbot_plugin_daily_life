from __future__ import annotations

from typing import Any

_SUBWAY_MARKERS = (
    "地铁",
    "轨道交通",
    "轻轨",
    "subway",
    "metro",
)
_BUS_MARKERS = (
    "公交",
    "公共汽车",
    "巴士",
    "busline",
    "bus line",
)
_TRANSIT_CONTAINER_KEYS = frozenset(
    {
        "bus",
        "busline",
        "buslines",
        "line",
        "lines",
        "route",
        "routes",
        "segment",
        "segments",
        "step",
        "steps",
        "vehicle",
        "vehicle_info",
    }
)
_TRANSIT_TEXT_KEYS = frozenset(
    {
        "action",
        "instruction",
        "line_name",
        "mode",
        "name",
        "title",
        "type",
        "vehicle",
        "vehicle_type",
    }
)


def transit_route_detail(route: Any) -> str:
    """从地图公共交通方案中提取公交、地铁或混合换乘摘要。"""

    detected: set[str] = set()
    _collect_transit_kinds(route, detected, depth=0, in_transit_container=False)
    if "bus" in detected and "subway" in detected:
        return "公交 + 地铁"
    if "subway" in detected:
        return "地铁"
    if "bus" in detected:
        return "公交"
    return ""


def _collect_transit_kinds(
    value: Any,
    detected: set[str],
    *,
    depth: int,
    in_transit_container: bool,
) -> None:
    if depth > 8 or {"bus", "subway"}.issubset(detected):
        return
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key or "").strip().lower()
            child_in_transit = in_transit_container or key in _TRANSIT_CONTAINER_KEYS
            if key in _TRANSIT_TEXT_KEYS:
                _classify_transit_text(child, detected)
            if child_in_transit or isinstance(child, (dict, list, tuple)):
                _collect_transit_kinds(
                    child,
                    detected,
                    depth=depth + 1,
                    in_transit_container=child_in_transit,
                )
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _collect_transit_kinds(
                child,
                detected,
                depth=depth + 1,
                in_transit_container=in_transit_container,
            )
        return
    if in_transit_container:
        _classify_transit_text(value, detected)


def _classify_transit_text(value: Any, detected: set[str]) -> None:
    text = " ".join(str(value or "").strip().lower().split())
    if not text:
        return
    if any(marker in text for marker in _SUBWAY_MARKERS):
        detected.add("subway")
    if any(marker in text for marker in _BUS_MARKERS) or text in {
        "bus",
        "公交车",
        "公共交通",
    }:
        detected.add("bus")


__all__ = ["transit_route_detail"]
