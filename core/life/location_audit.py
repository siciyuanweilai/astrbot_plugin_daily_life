from __future__ import annotations

import asyncio
import copy
import math
from typing import Any

from astrbot.api import logger

_PLACE_KINDS = frozenset({"home", "poi", "generic", "transit", "online", "none"})
_PLACE_SCOPES = frozenset({"local", "travel"})
_TRAVEL_MODES = frozenset({"walking", "cycling", "driving", "transit"})
_TRAVEL_SPEEDS = {
    "walking": 1.25,
    "cycling": 4.2,
    "driving": 8.5,
    "transit": 6.0,
}
_ROUTE_FALLBACK_MODES = ("transit", "cycling", "driving", "walking")


class DailyLocationAuditMixin:
    async def audit_daily_locations(
        self,
        payload: dict[str, Any],
        *,
        allow_safe_corrections: bool = False,
    ) -> tuple[dict[str, Any], str]:
        """在日程保存前核验结构化地点和连续路线。

        Args:
            payload: 已通过基础结构校验的日程结果。
            allow_safe_corrections: 是否自动收敛地点、路线和时间轴中的可修正问题。

        Returns:
            写入地图标准字段后的日程，以及校验失败原因。原因为空表示通过。
        """
        if not isinstance(payload, dict):
            return payload, ""
        if not (
            self.settings.enabled
            and self.settings.location_enabled
            and self.settings.home_address
            and self._map.available
        ):
            return payload, ""

        home_location = await self.resolve_home_location()
        if not home_location:
            logger.warning("[日程生成] 居住地暂时无法解析，本次跳过地图地点校正。")
            return payload, ""

        revised = copy.deepcopy(payload)
        timeline = revised.get("timeline")
        if not isinstance(timeline, list):
            return payload, ""

        home_city = str(home_location.get("city") or "").strip()
        home_coordinate = home_location.get("coordinate")
        home_address = str(home_location.get("formatted_address") or "").strip()
        entries: list[dict[str, Any]] = []
        search_requests: dict[tuple[str, str], None] = {}

        for index, item in enumerate(timeline):
            if not isinstance(item, dict):
                return payload, f"timeline[{index}] 不是有效对象，无法校正地点"
            place_kind = str(item.get("place_kind") or "").strip().lower()
            place_scope = str(item.get("place_scope") or "").strip().lower()
            place = str(item.get("place") or "").strip()
            place_city = str(item.get("place_city") or "").strip()
            place_hint = str(item.get("place_hint") or "").strip()
            travel_mode = str(item.get("travel_mode") or "").strip().lower()
            if place_kind not in _PLACE_KINDS:
                if not allow_safe_corrections:
                    return (
                        payload,
                        f"timeline[{index}].place_kind 必须明确填写为受支持的地点类型",
                    )
                place_kind = "home" if place == "家" else ("generic" if place else "none")
                item["place_kind"] = place_kind
                logger.warning(
                    f"[日程生成] timeline[{index}].place_kind 无效，地图校正已按"
                    f"地点内容校正为 {place_kind}。"
                )
            if place_scope not in _PLACE_SCOPES:
                if not allow_safe_corrections:
                    return (
                        payload,
                        f"timeline[{index}].place_scope 必须填写 local 或 travel",
                    )
                place_scope = "travel" if place_city else "local"
                item["place_scope"] = place_scope
                logger.warning(
                    f"[日程生成] timeline[{index}].place_scope 无效，地图校正已"
                    f"校正为 {place_scope}。"
                )
            if travel_mode and travel_mode not in _TRAVEL_MODES:
                if not allow_safe_corrections:
                    return payload, f"timeline[{index}].travel_mode 不是受支持的交通方式"
                travel_mode = ""
                item["travel_mode"] = ""
                logger.warning(
                    f"[日程生成] timeline[{index}].travel_mode 无效，地图校正将"
                    "根据实际路线重新选择。"
                )
            if place_scope == "travel" and not place_city:
                if not allow_safe_corrections:
                    return payload, f"timeline[{index}] 是跨城安排，但没有填写目标城市"
                place_scope = "local"
                item["place_scope"] = "local"
                logger.warning(
                    f"[日程生成] timeline[{index}] 未提供跨城目标城市，地图校正"
                    "按居住城市内活动处理。"
                )
            if place_kind in {"poi", "generic"} and not place:
                if not allow_safe_corrections:
                    return payload, f"timeline[{index}] 缺少明确的地点名称"
                place_kind = "none"
                item["place_kind"] = "none"
                logger.warning(
                    f"[日程生成] timeline[{index}] 缺少地点名称，地图校正已改为"
                    "无地点节点。"
                )
            if place_kind == "home" and place_scope == "travel":
                if not allow_safe_corrections:
                    return payload, f"timeline[{index}] 不能把居住地标记为跨城地点"
                place_scope = "local"
                place_city = ""
                item["place_scope"] = "local"
                item["place_city"] = ""

            target_city = place_city if place_scope == "travel" else home_city
            query = " ".join(value for value in (place, place_hint) if value)
            entry = {
                "index": index,
                "item": item,
                "kind": place_kind,
                "scope": place_scope,
                "place": place,
                "hint": place_hint,
                "city": target_city,
                "mode": travel_mode,
                "query": query,
                "coordinate": None,
            }
            entries.append(entry)
            if place_kind == "poi":
                search_requests[(query, target_city)] = None

        search_keys = list(search_requests)
        search_values = await asyncio.gather(
            *(
                self._map.search_places(query, city_hint=city, limit=5)
                for query, city in search_keys
            )
        )
        search_results = dict(zip(search_keys, search_values, strict=True))

        canonical_places: list[dict[str, Any]] = []
        seen_places: set[str] = set()
        verified_count = 0
        substituted_places: list[dict[str, str]] = []
        downgraded_places: list[str] = []
        for entry in entries:
            item = entry["item"]
            kind = entry["kind"]
            place = entry["place"]
            target_city = entry["city"]
            if kind == "home":
                item.update(
                    {
                        "place": "家",
                        "place_kind": "home",
                        "place_scope": "local",
                        "place_city": home_city,
                        "place_hint": "",
                        "place_address": home_address,
                        "place_latitude": float(home_coordinate[0]),
                        "place_longitude": float(home_coordinate[1]),
                        "place_coordinate_source": f"{self.map_provider}_home_address",
                    }
                )
                entry.update(
                    {
                        "place": "家",
                        "city": home_city,
                        "coordinate": home_coordinate,
                    }
                )
                verified_count += 1
                if "家" not in seen_places:
                    seen_places.add("家")
                    canonical_places.append(
                        {
                            "name": "家",
                            "type": "home",
                            "hint": home_address,
                            "latitude": float(home_coordinate[0]),
                            "longitude": float(home_coordinate[1]),
                            "coordinate_source": f"{self.map_provider}_home_address",
                        }
                    )
                continue

            if kind == "poi":
                candidates = [
                    candidate
                    for candidate in search_results.get(
                        (entry["query"], target_city), []
                    )
                    if isinstance(candidate, dict)
                    and candidate.get("name")
                    and candidate.get("coordinate")
                ]
                exact_candidates = [
                    candidate
                    for candidate in candidates
                    if str(candidate.get("name") or "").strip() == place
                ]
                if not exact_candidates:
                    same_city_candidates = [
                        candidate
                        for candidate in candidates
                        if self._cities_match(
                            str(candidate.get("city") or "").strip(), target_city
                        )
                    ]
                    suggestions = "、".join(
                        str(candidate.get("name") or "").strip()
                        for candidate in same_city_candidates[:3]
                    )
                    suffix = f"；地图候选为：{suggestions}" if suggestions else ""
                    issue = (
                        f"地图未能在{target_city}确认地点“{place}”的精确名称{suffix}。"
                        "请改用地图中的完整名称；若本来只是泛化场景，请改为 generic。"
                    )
                    if not allow_safe_corrections:
                        return payload, issue
                    if same_city_candidates:
                        candidate = same_city_candidates[0]
                        canonical_name = str(candidate.get("name") or "").strip()
                        substituted_places.append(
                            {"original": place, "canonical": canonical_name}
                        )
                        item["place"] = canonical_name
                        entry["place"] = canonical_name
                        logger.warning(
                            f"[日程生成] 地点“{place}”未能精确确认，地图校正改用"
                            f"同城地图候选“{canonical_name}”。"
                        )
                        place = canonical_name
                        candidate_values = [candidate]
                    else:
                        self._downgrade_unverified_place(
                            item,
                            entry,
                            canonical_places=canonical_places,
                            seen_places=seen_places,
                        )
                        downgraded_places.append(place)
                        logger.warning(
                            f"[日程生成] {issue} 已由地图校正按泛化场景保留，"
                            "不写入导航坐标。"
                        )
                        continue
                else:
                    unique_candidates = {
                        (
                            str(candidate.get("poi_id") or ""),
                            str(candidate.get("address") or ""),
                        ): candidate
                        for candidate in exact_candidates
                    }
                    candidate_values = list(unique_candidates.values())
                if len(candidate_values) > 1:
                    hint = entry["hint"]
                    matched_candidates = [
                        value
                        for value in candidate_values
                        if hint
                        and (
                            hint in str(value.get("address") or "")
                            or hint in str(value.get("district") or "")
                        )
                    ]
                    if len(matched_candidates) == 1:
                        candidate = matched_candidates[0]
                    else:
                        suggestions = "；".join(
                            f"{value.get('name')}（{value.get('address') or value.get('district') or '地址未知'}）"
                            for value in candidate_values[:3]
                        )
                        issue = (
                            f"地点“{place}”在{target_city}存在多个同名候选：{suggestions}。"
                            "请在 place_hint 中补充能够唯一定位的区县、商圈或地址。"
                        )
                        if not allow_safe_corrections:
                            return payload, issue
                        candidate = candidate_values[0]
                        logger.warning(
                            f"[日程生成] {issue} 地图校正采用排序第一的候选地址。"
                        )
                else:
                    candidate = candidate_values[0]
                candidate_city = str(candidate.get("city") or "").strip()
                same_city = self._cities_match(candidate_city, target_city)
                if target_city and not same_city:
                    issue = (
                        f"地点“{place}”解析到{candidate_city or '未知城市'}，"
                        f"与日程声明的{target_city}不一致"
                    )
                    if not allow_safe_corrections:
                        return payload, issue
                    self._downgrade_unverified_place(
                        item,
                        entry,
                        canonical_places=canonical_places,
                        seen_places=seen_places,
                    )
                    downgraded_places.append(place)
                    logger.warning(
                        f"[日程生成] {issue}，已由地图校正按日程声明城市的"
                        "泛化场景保留，不采用异地坐标。"
                    )
                    continue
                coordinate = candidate["coordinate"]
                address = str(candidate.get("address") or "").strip()
                item.update(
                    {
                        "place": place,
                        "place_kind": "poi",
                        "place_scope": entry["scope"],
                        "place_city": candidate_city or target_city,
                        "place_address": address,
                        "place_latitude": float(coordinate[0]),
                        "place_longitude": float(coordinate[1]),
                        "place_coordinate_source": f"{self.map_provider}_poi",
                    }
                )
                entry.update(
                    {
                        "city": candidate_city or target_city,
                        "coordinate": coordinate,
                    }
                )
                verified_count += 1
                if place not in seen_places:
                    seen_places.add(place)
                    canonical_places.append(
                        {
                            "name": place,
                            "type": str(candidate.get("category") or "poi").strip(),
                            "hint": address or entry["hint"],
                            "latitude": float(coordinate[0]),
                            "longitude": float(coordinate[1]),
                            "coordinate_source": f"{self.map_provider}_poi",
                        }
                    )
                continue

            item.update(
                {
                    "place_kind": kind,
                    "place_scope": entry["scope"],
                    "place_city": target_city if entry["scope"] == "travel" else "",
                    "place_address": "",
                    "place_latitude": None,
                    "place_longitude": None,
                    "place_coordinate_source": "",
                }
            )
            if kind in {"transit", "online", "none"}:
                item["place"] = ""
                entry["place"] = ""
            elif place not in seen_places:
                seen_places.add(place)
                canonical_places.append(
                    {
                        "name": place,
                        "type": "generic",
                        "hint": entry["hint"],
                    }
                )

        unwrapped_minutes: list[int | None] = []
        last_unwrapped = None
        day_offset = 0
        for entry in entries:
            parts = str(entry["item"].get("time") or "").split(":", 1)
            try:
                minutes = int(parts[0]) * 60 + int(parts[1])
            except (IndexError, TypeError, ValueError):
                minutes = None
            if minutes is None or not 0 <= minutes < 24 * 60:
                unwrapped_minutes.append(None)
                continue
            candidate = minutes + day_offset
            if last_unwrapped is not None and candidate < last_unwrapped:
                day_offset += 24 * 60
                candidate = minutes + day_offset
            unwrapped_minutes.append(candidate)
            last_unwrapped = candidate

        transitions: list[dict[str, Any]] = []
        previous = None
        for entry, minutes in zip(entries, unwrapped_minutes, strict=True):
            if entry["kind"] not in {"home", "poi"} or not entry["coordinate"]:
                continue
            if previous is not None:
                origin_coordinate = previous["coordinate"]
                destination_coordinate = entry["coordinate"]
                lat1, lon1 = origin_coordinate
                lat2, lon2 = destination_coordinate
                radius = 6_371_000.0
                phi1 = math.radians(lat1)
                phi2 = math.radians(lat2)
                delta_phi = math.radians(lat2 - lat1)
                delta_lambda = math.radians(lon2 - lon1)
                haversine = (
                    math.sin(delta_phi / 2) ** 2
                    + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
                )
                distance = (
                    radius
                    * 2
                    * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
                )
                if distance > 50:
                    if not entry["mode"]:
                        if not allow_safe_corrections:
                            return (
                                payload,
                                f"从“{previous['place']}”前往“{entry['place']}”缺少 travel_mode",
                            )
                        entry["mode"] = "walking"
                        entry["item"]["travel_mode"] = "walking"
                        logger.warning(
                            f"[日程生成] 从“{previous['place']}”前往“{entry['place']}”"
                            "缺少交通方式，地图校正先按步行查询并自动选择可行路线。"
                        )
                    transitions.append(
                        {
                            "origin": previous,
                            "destination": entry,
                            "origin_minutes": previous["minutes"],
                            "destination_minutes": minutes,
                            "straight_distance": distance,
                        }
                    )
            previous = {**entry, "minutes": minutes}

        route_values = await asyncio.gather(
            *(
                self._map.route(
                    transition["origin"]["coordinate"],
                    transition["destination"]["coordinate"],
                    transition["destination"]["mode"],
                    origin_city=transition["origin"]["city"],
                    destination_city=transition["destination"]["city"],
                )
                for transition in transitions
            )
        )
        route_by_index: dict[int, dict[str, Any]] = {}
        for transition, route in zip(transitions, route_values, strict=True):
            mode = transition["destination"]["mode"]
            route = self._normalize_route_result(
                route,
                mode=mode,
                straight_distance=transition["straight_distance"],
            )
            required_minutes = self._route_minutes(route)
            origin_index = transition["origin"]["index"]
            destination_index = transition["destination"]["index"]
            origin_minutes = unwrapped_minutes[origin_index]
            destination_minutes = unwrapped_minutes[destination_index]
            if origin_minutes is not None and destination_minutes is not None:
                available_minutes = destination_minutes - origin_minutes
                if required_minutes > available_minutes:
                    if not allow_safe_corrections:
                        return (
                            payload,
                            f"从“{transition['origin']['place']}”到“{transition['destination']['place']}”"
                            f"预计需要约 {required_minutes} 分钟，但时间轴只预留了 {available_minutes} 分钟",
                        )
                    mode, route, required_minutes = await self._select_route_for_window(
                        transition,
                        requested_mode=mode,
                        requested_route=route,
                        available_minutes=available_minutes,
                    )
                    if required_minutes > available_minutes:
                        shift_minutes = required_minutes - available_minutes
                        self._shift_timeline(
                            entries,
                            unwrapped_minutes,
                            start_index=destination_index,
                            shift_minutes=shift_minutes,
                        )
                        logger.warning(
                            f"[日程生成] 从“{transition['origin']['place']}”到"
                            f"“{transition['destination']['place']}”最快仍需约 "
                            f"{required_minutes} 分钟，已将当前及后续日程顺延 "
                            f"{shift_minutes} 分钟。"
                        )
                    elif mode != transition["destination"]["mode"]:
                        logger.warning(
                            f"[日程生成] 从“{transition['origin']['place']}”到"
                            f"“{transition['destination']['place']}”原交通方式无法按时到达，"
                            f"已改用 {self._travel_mode_label(mode)}，预计约 "
                            f"{required_minutes} 分钟。"
                        )
                    transition["destination"]["mode"] = mode
                    transition["destination"]["item"]["travel_mode"] = mode
            destination_item = transition["destination"]["item"]
            destination_item["travel_minutes"] = required_minutes
            destination_item["travel_distance_meters"] = round(
                float(route.get("distance_meters") or 0), 1
            )
            destination_item["travel_origin"] = transition["origin"]["place"]
            destination_item["travel_provider"] = str(
                route.get("provider") or ""
            ).strip()
            route_by_index[transition["destination"]["index"]] = {
                **route,
                "duration_minutes": required_minutes,
                "origin": transition["origin"]["place"],
                "destination": transition["destination"]["place"],
                "mode": mode,
            }

        planned_actions = revised.get("planned_actions")
        discarded_actions: set[int] = set()
        for action in planned_actions if isinstance(planned_actions, list) else []:
            if not isinstance(action, dict) or str(
                action.get("action_type") or ""
            ).strip().lower() not in {"move", "travel"}:
                continue
            try:
                timeline_index = int(action.get("timeline_index"))
            except (TypeError, ValueError):
                if not allow_safe_corrections:
                    return payload, "move/travel 动作缺少有效的 timeline_index"
                discarded_actions.add(id(action))
                logger.warning(
                    "[日程生成] move/travel 动作缺少有效时间轴索引，地图校正已"
                    "丢弃该可选动作。"
                )
                continue
            try:
                planned_minutes = int(action.get("duration_minutes") or 0)
            except (TypeError, ValueError):
                planned_minutes = 0
            route = route_by_index.get(timeline_index)
            if (
                not route
                and 0 <= timeline_index < len(entries)
                and entries[timeline_index]["kind"] == "generic"
                and entries[timeline_index]["place"]
            ):
                destination = entries[timeline_index]
                origin = next(
                    (
                        candidate
                        for candidate in reversed(entries[:timeline_index])
                        if candidate["kind"] in {"home", "poi", "generic"}
                        and candidate["place"]
                    ),
                    None,
                )
                mode = str(
                    destination["mode"]
                    or (
                        action.get("payload", {}).get("travel_mode")
                        if isinstance(action.get("payload"), dict)
                        else ""
                    )
                    or "walking"
                ).strip()
                required_minutes = max(
                    1,
                    planned_minutes,
                    int(self.settings.default_travel_minutes),
                )
                destination["item"]["travel_mode"] = mode
                destination["item"]["travel_minutes"] = required_minutes
                destination["item"]["travel_distance_meters"] = 0.0
                destination["item"]["travel_origin"] = str(
                    (origin or {}).get("place") or ""
                )
                destination["item"]["travel_provider"] = "default_estimate"
                route = {
                    "distance_meters": 0.0,
                    "duration_minutes": required_minutes,
                    "provider": "default_estimate",
                    "origin": str((origin or {}).get("place") or ""),
                    "destination": destination["place"],
                    "mode": mode,
                }
                route_by_index[timeline_index] = route
            if not route:
                if not allow_safe_corrections:
                    return (
                        payload,
                        f"timeline[{timeline_index}] 的 move/travel 动作没有对应的可验证路线",
                    )
                discarded_actions.add(id(action))
                logger.warning(
                    f"[日程生成] timeline[{timeline_index}] 的 move/travel 动作"
                    "没有实际位移路线，地图校正已丢弃该可选动作。"
                )
                continue
            if planned_minutes and route["duration_minutes"] > planned_minutes:
                if not allow_safe_corrections:
                    return (
                        payload,
                        f"前往“{route['destination']}”预计需要约 {route['duration_minutes']} 分钟，"
                        f"但 travel 动作只预留了 {planned_minutes} 分钟",
                    )
                logger.warning(
                    f"[日程生成] 前往“{route['destination']}”的动作时长由 "
                    f"{planned_minutes} 分钟校正为 {route['duration_minutes']} 分钟。"
                )
            action["target"] = route["destination"]
            action["duration_minutes"] = max(planned_minutes, route["duration_minutes"])
            action["payload"] = {
                **(
                    action.get("payload")
                    if isinstance(action.get("payload"), dict)
                    else {}
                ),
                "origin": route["origin"],
                "destination": route["destination"],
                "travel_mode": route["mode"],
                "distance_meters": route.get("distance_meters"),
                "route_provider": route.get("provider"),
            }
        if discarded_actions and isinstance(planned_actions, list):
            revised["planned_actions"] = [
                action
                for action in planned_actions
                if id(action) not in discarded_actions
            ]

        revised["places"] = canonical_places
        self._replace_place_references(revised, substituted_places)
        revised["location_audit"] = {
            "map_provider": self.map_provider_label,
            "home_city": home_city,
            "verified_places": verified_count,
            "checked_routes": len(transitions),
            "substituted_places": len(substituted_places),
            "place_substitutions": substituted_places,
            "downgraded_places": len(downgraded_places),
            "downgraded_place_names": downgraded_places,
        }
        logger.debug(
            f"[日程生成] 地图地点校正通过：地点={verified_count}；路线={len(transitions)}；"
            f"地图替代={len(substituted_places)}；泛化降级={len(downgraded_places)}；"
            f"服务={self.map_provider_label}"
        )
        return revised, ""

    async def _select_route_for_window(
        self,
        transition: dict[str, Any],
        *,
        requested_mode: str,
        requested_route: dict[str, Any],
        available_minutes: int,
    ) -> tuple[str, dict[str, Any], int]:
        """选择能落入时间窗的路线，找不到时返回耗时最短的方案。"""

        modes = [requested_mode]
        modes.extend(
            mode
            for mode in _ROUTE_FALLBACK_MODES
            if mode != requested_mode
        )
        alternative_modes = modes[1:]
        alternative_values = await asyncio.gather(
            *(
                self._map.route(
                    transition["origin"]["coordinate"],
                    transition["destination"]["coordinate"],
                    mode,
                    origin_city=transition["origin"]["city"],
                    destination_city=transition["destination"]["city"],
                )
                for mode in alternative_modes
            )
        )
        options = [(requested_mode, requested_route)]
        options.extend(
            (
                mode,
                self._normalize_route_result(
                    value,
                    mode=mode,
                    straight_distance=transition["straight_distance"],
                ),
            )
            for mode, value in zip(
                alternative_modes,
                alternative_values,
                strict=True,
            )
        )
        measured = [
            (mode, route, self._route_minutes(route)) for mode, route in options
        ]
        fitting = [
            option for option in measured if option[2] <= available_minutes
        ]
        return fitting[0] if fitting else min(measured, key=lambda option: option[2])

    @staticmethod
    def _normalize_route_result(
        route: Any,
        *,
        mode: str,
        straight_distance: float,
    ) -> dict[str, Any]:
        """把地图失败结果转成保守的坐标耗时估算。"""

        if isinstance(route, dict):
            return route
        return {
            "distance_meters": round(straight_distance, 1),
            "duration_seconds": max(
                60,
                int(straight_distance / _TRAVEL_SPEEDS[mode]),
            ),
            "provider": "coordinate_estimate",
        }

    @staticmethod
    def _route_minutes(route: dict[str, Any]) -> int:
        """读取统一路线结果中的分钟数。"""

        return max(1, math.ceil(float(route.get("duration_seconds") or 0) / 60))

    @staticmethod
    def _shift_timeline(
        entries: list[dict[str, Any]],
        unwrapped_minutes: list[int | None],
        *,
        start_index: int,
        shift_minutes: int,
    ) -> None:
        """顺延一个节点及其后续时间，保持时间轴相对间隔。"""

        if shift_minutes <= 0:
            return
        for index in range(start_index, len(entries)):
            minutes = unwrapped_minutes[index]
            if minutes is None:
                continue
            shifted = minutes + shift_minutes
            unwrapped_minutes[index] = shifted
            clock_minutes = shifted % (24 * 60)
            entries[index]["item"]["time"] = (
                f"{clock_minutes // 60:02d}:{clock_minutes % 60:02d}"
            )

    @staticmethod
    def _travel_mode_label(mode: str) -> str:
        """返回交通方式的中文名称。"""

        return {
            "walking": "步行",
            "cycling": "骑行",
            "driving": "驾车",
            "transit": "公交",
        }.get(mode, mode)

    @staticmethod
    def _cities_match(candidate_city: str, target_city: str) -> bool:
        """判断地图返回城市是否与目标城市一致。"""

        return bool(
            not candidate_city
            or not target_city
            or candidate_city == target_city
            or (
                min(len(candidate_city), len(target_city)) >= 2
                and (
                    candidate_city in target_city or target_city in candidate_city
                )
            )
        )

    @classmethod
    def _replace_place_references(
        cls,
        value: Any,
        substitutions: list[dict[str, str]],
    ) -> Any:
        """把日程各结构中的临时地点名同步为地图标准名称。"""

        if isinstance(value, dict):
            for key, item in value.items():
                value[key] = cls._replace_place_references(item, substitutions)
            return value
        if isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = cls._replace_place_references(item, substitutions)
            return value
        if not isinstance(value, str):
            return value
        result = value
        for substitution in substitutions:
            original = substitution["original"]
            canonical = substitution["canonical"]
            if original and original != canonical:
                result = result.replace(original, canonical)
        return result

    @staticmethod
    def _downgrade_unverified_place(
        item: dict[str, Any],
        entry: dict[str, Any],
        *,
        canonical_places: list[dict[str, Any]],
        seen_places: set[str],
    ) -> None:
        """把无法可靠定位的精确地点转成不参与导航的泛化场景。"""

        place = entry["place"]
        entry["kind"] = "generic"
        entry["coordinate"] = None
        item.update(
            {
                "place_kind": "generic",
                "place_scope": entry["scope"],
                "place_city": entry["city"]
                if entry["scope"] == "travel"
                else "",
                "place_address": "",
                "place_latitude": None,
                "place_longitude": None,
                "place_coordinate_source": "",
            }
        )
        if place in seen_places:
            return
        seen_places.add(place)
        canonical_places.append(
            {
                "name": place,
                "type": "generic",
                "hint": entry["hint"] or "地图未确认具体地点",
            }
        )


__all__ = ["DailyLocationAuditMixin"]
