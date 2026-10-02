from __future__ import annotations

from typing import Any

_TRANSIT_TYPES = {
    "subway": "subway",
    "metro": "subway",
    "地铁线路": "subway",
    "公交线路": "bus",
    "bus": "bus",
}
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
_TRANSIT_TYPE_KEYS = frozenset({"mode", "type", "vehicle_type"})


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
            if key in _TRANSIT_TYPE_KEYS:
                kind = _TRANSIT_TYPES.get(str(child or "").strip().lower())
                if kind:
                    detected.add(kind)
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


__all__ = ["transit_route_detail"]
