from __future__ import annotations

import asyncio
import copy
import math
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .route_choice import (
    ROUTE_MODES,
    RouteChoiceContext,
    choose_practical_route,
    default_route_mode,
    route_needs_comparison,
    route_query_modes,
)

_PLACE_KINDS = frozenset({"home", "poi", "generic", "transit", "online", "none"})
_PLACE_SCOPES = frozenset({"local", "travel"})
_TRAVEL_MODES = ROUTE_MODES
_TRAVEL_SPEEDS = {
    "walking": 1.25,
    "cycling": 4.2,
    "driving": 8.5,
    "transit": 6.0,
}


@dataclass(slots=True)
class _LocationAuditState:
    canonical_places: list[dict[str, Any]] = field(default_factory=list)
    seen_places: set[str] = field(default_factory=set)
    substituted_places: list[dict[str, str]] = field(default_factory=list)
    downgraded_places: list[str] = field(default_factory=list)
    verified_count: int = 0


class DailyLocationAuditMixin:
    async def audit_daily_locations(
        self,
        payload: dict[str, Any],
        *,
        allow_safe_corrections: bool = False,
        preselected_places: list[dict[str, Any]] | None = None,
        weather_info: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """在日程保存前核验结构化地点和连续路线。

        Args:
            payload: 已通过基础结构校验的日程结果。
            allow_safe_corrections: 是否自动收敛地点、路线和时间轴中的可修正问题。
            preselected_places: 本轮生成前已由地图确认的候选地点；匹配时直接复用其坐标。
            weather_info: 当前日程对应的结构化天气，用于判断步行和骑行是否合理。

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
        route_context = RouteChoiceContext.from_values(
            weather_info,
            revised.get("state"),
        )
        preselected_places = [
            item
            for item in (
                preselected_places if isinstance(preselected_places, list) else []
            )
            if isinstance(item, dict)
        ]
        timeline = revised.get("timeline")
        if not isinstance(timeline, list):
            return payload, ""

        home_city = str(home_location.get("city") or "").strip()
        home_coordinate = home_location.get("coordinate")
        home_address = str(home_location.get("formatted_address") or "").strip()
        entries, search_requests, issue = self._normalize_location_entries(
            timeline,
            home_city=home_city,
            allow_safe_corrections=allow_safe_corrections,
        )
        if issue:
            return payload, issue

        search_results = await self._location_search_results(
            entries,
            search_requests,
            preselected_places,
        )
        audit_state = _LocationAuditState()
        issue = self._canonicalize_location_entries(
            entries,
            home_city=home_city,
            home_coordinate=home_coordinate,
            home_address=home_address,
            preselected_places=preselected_places,
            search_results=search_results,
            allow_safe_corrections=allow_safe_corrections,
            state=audit_state,
        )
        if issue:
            return payload, issue

        transitions, route_by_index, issue = await self._audit_location_routes(
            entries,
            allow_safe_corrections=allow_safe_corrections,
            context=route_context,
        )
        if issue:
            return payload, issue

        issue = self._synchronize_travel_actions(
            revised,
            entries=entries,
            route_by_index=route_by_index,
            allow_safe_corrections=allow_safe_corrections,
        )
        if issue:
            return payload, issue

        revised["places"] = audit_state.canonical_places
        self._replace_place_references(revised, audit_state.substituted_places)
        revised["location_audit"] = {
            "map_provider": self.map_provider_label,
            "home_city": home_city,
            "verified_places": audit_state.verified_count,
            "checked_routes": len(transitions),
            "substituted_places": len(audit_state.substituted_places),
            "place_substitutions": audit_state.substituted_places,
            "downgraded_places": len(audit_state.downgraded_places),
            "downgraded_place_names": audit_state.downgraded_places,
        }
        logger.debug(
            f"[日程生成] 地图地点校正通过：地点={audit_state.verified_count}；"
            f"路线={len(transitions)}；地图替代={len(audit_state.substituted_places)}；"
            f"泛化降级={len(audit_state.downgraded_places)}；"
            f"服务={self.map_provider_label}"
        )
        return revised, ""

    async def _location_search_results(
        self,
        entries: list[dict[str, Any]],
        search_requests: dict[tuple[str, str], None],
        preselected_places: list[dict[str, Any]],
    ) -> dict[tuple[str, str], Any]:
        """只查询没有被预选地点覆盖的 POI。"""

        search_keys = [
            key
            for key in search_requests
            if not any(
                entry["kind"] == "poi"
                and entry["query"] == key[0]
                and entry["city"] == key[1]
                and any(
                    self._preselected_place_matches(candidate, entry, key[1])
                    for candidate in preselected_places
                )
                for entry in entries
            )
        ]
        search_values = await asyncio.gather(
            *(
                self._map.search_places(query, city_hint=city, limit=5)
                for query, city in search_keys
            )
        )
        return dict(zip(search_keys, search_values, strict=True))

    def _canonicalize_location_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        home_city: str,
        home_coordinate: tuple[float, float],
        home_address: str,
        preselected_places: list[dict[str, Any]],
        search_results: dict[tuple[str, str], Any],
        allow_safe_corrections: bool,
        state: _LocationAuditState,
    ) -> str:
        """把归一化节点写成稳定的地图地点字段。"""

        for entry in entries:
            kind = entry["kind"]
            if kind == "home":
                self._apply_home_location_entry(
                    entry,
                    home_city=home_city,
                    home_coordinate=home_coordinate,
                    home_address=home_address,
                    state=state,
                )
                continue
            if kind == "poi":
                issue = self._canonicalize_poi_entry(
                    entry,
                    preselected_places=preselected_places,
                    search_results=search_results,
                    allow_safe_corrections=allow_safe_corrections,
                    state=state,
                )
                if issue:
                    return issue
                continue
            self._apply_non_poi_location_entry(entry, state=state)
        return ""

    def _apply_home_location_entry(
        self,
        entry: dict[str, Any],
        *,
        home_city: str,
        home_coordinate: tuple[float, float],
        home_address: str,
        state: _LocationAuditState,
    ) -> None:
        item = entry["item"]
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
        state.verified_count += 1
        if "家" in state.seen_places:
            return
        state.seen_places.add("家")
        state.canonical_places.append(
            {
                "name": "家",
                "type": "home",
                "hint": home_address,
                "latitude": float(home_coordinate[0]),
                "longitude": float(home_coordinate[1]),
                "coordinate_source": f"{self.map_provider}_home_address",
            }
        )

    def _canonicalize_poi_entry(
        self,
        entry: dict[str, Any],
        *,
        preselected_places: list[dict[str, Any]],
        search_results: dict[tuple[str, str], Any],
        allow_safe_corrections: bool,
        state: _LocationAuditState,
    ) -> str:
        candidate, issue, downgraded = self._resolve_poi_candidate(
            entry,
            preselected_places=preselected_places,
            search_results=search_results,
            allow_safe_corrections=allow_safe_corrections,
            state=state,
        )
        if issue or downgraded:
            return issue
        self._apply_verified_poi_entry(entry, candidate, state=state)
        return ""

    def _resolve_poi_candidate(
        self,
        entry: dict[str, Any],
        *,
        preselected_places: list[dict[str, Any]],
        search_results: dict[tuple[str, str], Any],
        allow_safe_corrections: bool,
        state: _LocationAuditState,
    ) -> tuple[dict[str, Any], str, bool]:
        item = entry["item"]
        place = entry["place"]
        target_city = entry["city"]
        preselected = [
            candidate
            for candidate in preselected_places
            if self._preselected_place_matches(candidate, entry, target_city)
        ]
        candidates = preselected or [
            candidate
            for candidate in search_results.get((entry["query"], target_city), [])
            if isinstance(candidate, dict)
            and candidate.get("name")
            and candidate.get("coordinate")
        ]
        exact_candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("name") or "").strip() == place
        ]
        if exact_candidates:
            unique_candidates = {
                (
                    str(candidate.get("poi_id") or ""),
                    str(candidate.get("address") or ""),
                ): candidate
                for candidate in exact_candidates
            }
            candidate_values = list(unique_candidates.values())
        else:
            candidate_values, issue, downgraded = self._fallback_poi_candidates(
                entry,
                candidates=candidates,
                allow_safe_corrections=allow_safe_corrections,
                state=state,
            )
            if issue or downgraded:
                return {}, issue, downgraded

        candidate, issue = self._disambiguate_poi_candidate(
            entry,
            candidate_values,
            allow_safe_corrections=allow_safe_corrections,
        )
        if issue:
            return {}, issue, False
        candidate_city = str(candidate.get("city") or "").strip()
        if target_city and not self._cities_match(candidate_city, target_city):
            issue = (
                f"地点“{entry['place']}”解析到{candidate_city or '未知城市'}，"
                f"与日程声明的{target_city}不一致"
            )
            if not allow_safe_corrections:
                return {}, issue, False
            self._downgrade_unverified_place(
                item,
                entry,
                canonical_places=state.canonical_places,
                seen_places=state.seen_places,
            )
            state.downgraded_places.append(entry["place"])
            logger.warning(
                f"[日程生成] {issue}，已由地图校正按日程声明城市的"
                "泛化场景保留，不采用异地坐标。"
            )
            return {}, "", True
        return candidate, "", False

    def _fallback_poi_candidates(
        self,
        entry: dict[str, Any],
        *,
        candidates: list[dict[str, Any]],
        allow_safe_corrections: bool,
        state: _LocationAuditState,
    ) -> tuple[list[dict[str, Any]], str, bool]:
        place = entry["place"]
        target_city = entry["city"]
        same_city_candidates = [
            candidate
            for candidate in candidates
            if self._cities_match(str(candidate.get("city") or "").strip(), target_city)
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
            return [], issue, False
        if same_city_candidates:
            candidate = same_city_candidates[0]
            canonical_name = str(candidate.get("name") or "").strip()
            state.substituted_places.append(
                {"original": place, "canonical": canonical_name}
            )
            entry["item"]["place"] = canonical_name
            entry["place"] = canonical_name
            logger.warning(
                f"[日程生成] 地点“{place}”未能精确确认，地图校正改用"
                f"同城地图候选“{canonical_name}”。"
            )
            return [candidate], "", False
        self._downgrade_unverified_place(
            entry["item"],
            entry,
            canonical_places=state.canonical_places,
            seen_places=state.seen_places,
        )
        state.downgraded_places.append(place)
        logger.warning(
            f"[日程生成] {issue} 已由地图校正按泛化场景保留，不写入导航坐标。"
        )
        return [], "", True

    @staticmethod
    def _disambiguate_poi_candidate(
        entry: dict[str, Any],
        candidate_values: list[dict[str, Any]],
        *,
        allow_safe_corrections: bool,
    ) -> tuple[dict[str, Any], str]:
        if len(candidate_values) == 1:
            return candidate_values[0], ""
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
            return matched_candidates[0], ""
        suggestions = "；".join(
            f"{value.get('name')}（{value.get('address') or value.get('district') or '地址未知'}）"
            for value in candidate_values[:3]
        )
        issue = (
            f"地点“{entry['place']}”在{entry['city']}存在多个同名候选：{suggestions}。"
            "请在 place_hint 中补充能够唯一定位的区县、商圈或地址。"
        )
        if not allow_safe_corrections:
            return {}, issue
        logger.warning(f"[日程生成] {issue} 地图校正采用排序第一的候选地址。")
        return candidate_values[0], ""

    def _apply_verified_poi_entry(
        self,
        entry: dict[str, Any],
        candidate: dict[str, Any],
        *,
        state: _LocationAuditState,
    ) -> None:
        item = entry["item"]
        place = entry["place"]
        target_city = entry["city"]
        candidate_city = str(candidate.get("city") or "").strip()
        coordinate = candidate["coordinate"]
        address = str(candidate.get("address") or "").strip()
        candidate_mode = str(candidate.get("travel_mode") or "").strip().lower()
        if candidate_mode in _TRAVEL_MODES:
            entry["mode"] = candidate_mode
            entry["mode_locked"] = bool(candidate.get("travel_mode_locked"))
            item["travel_mode"] = candidate_mode
            item["travel_detail"] = (
                str(candidate.get("travel_detail") or "").strip()
                if candidate_mode == "transit"
                else ""
            )
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
        state.verified_count += 1
        if place in state.seen_places:
            return
        state.seen_places.add(place)
        state.canonical_places.append(
            {
                "name": place,
                "type": str(candidate.get("category") or "poi").strip(),
                "hint": address or entry["hint"],
                "latitude": float(coordinate[0]),
                "longitude": float(coordinate[1]),
                "coordinate_source": f"{self.map_provider}_poi",
            }
        )

    @staticmethod
    def _apply_non_poi_location_entry(
        entry: dict[str, Any], *, state: _LocationAuditState
    ) -> None:
        item = entry["item"]
        kind = entry["kind"]
        place = entry["place"]
        target_city = entry["city"]
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
        elif place not in state.seen_places:
            state.seen_places.add(place)
            state.canonical_places.append(
                {
                    "name": place,
                    "type": "generic",
                    "hint": entry["hint"],
                }
            )

    @staticmethod
    def _normalize_location_entries(
        timeline: list[Any],
        *,
        home_city: str,
        allow_safe_corrections: bool,
    ) -> tuple[list[dict[str, Any]], dict[tuple[str, str], None], str]:
        """归一化时间轴地点字段并整理地图搜索请求。

        Args:
            timeline: 日程时间轴原始节点。
            home_city: 居住城市。
            allow_safe_corrections: 是否允许自动修正安全问题。

        Returns:
            地点节点、去重后的搜索请求和失败原因。
        """

        entries: list[dict[str, Any]] = []
        search_requests: dict[tuple[str, str], None] = {}
        for index, item in enumerate(timeline):
            if not isinstance(item, dict):
                return [], {}, f"timeline[{index}] 不是有效对象，无法校正地点"
            place_kind = str(item.get("place_kind") or "").strip().lower()
            place_scope = str(item.get("place_scope") or "").strip().lower()
            place = str(item.get("place") or "").strip()
            place_city = str(item.get("place_city") or "").strip()
            place_hint = str(item.get("place_hint") or "").strip()
            travel_mode = str(item.get("travel_mode") or "").strip().lower()
            if place_kind not in _PLACE_KINDS:
                if not allow_safe_corrections:
                    return (
                        [],
                        {},
                        f"timeline[{index}].place_kind 必须明确填写为受支持的地点类型",
                    )
                place_kind = (
                    "home" if place == "家" else ("generic" if place else "none")
                )
                item["place_kind"] = place_kind
                logger.warning(
                    f"[日程生成] timeline[{index}].place_kind 无效，地图校正已按"
                    f"地点内容校正为 {place_kind}。"
                )
            if place_scope not in _PLACE_SCOPES:
                if not allow_safe_corrections:
                    return (
                        [],
                        {},
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
                    return [], {}, f"timeline[{index}].travel_mode 不是受支持的交通方式"
                travel_mode = ""
                item["travel_mode"] = ""
                logger.warning(
                    f"[日程生成] timeline[{index}].travel_mode 无效，地图校正将"
                    "根据实际路线重新选择。"
                )
            if place_scope == "travel" and not place_city:
                if not allow_safe_corrections:
                    return [], {}, f"timeline[{index}] 是跨城安排，但没有填写目标城市"
                place_scope = "local"
                item["place_scope"] = "local"
                logger.warning(
                    f"[日程生成] timeline[{index}] 未提供跨城目标城市，地图校正"
                    "按居住城市内活动处理。"
                )
            if place_kind in {"poi", "generic"} and not place:
                if not allow_safe_corrections:
                    return [], {}, f"timeline[{index}] 缺少明确的地点名称"
                place_kind = "none"
                item["place_kind"] = "none"
                logger.warning(
                    f"[日程生成] timeline[{index}] 缺少地点名称，地图校正已改为"
                    "无地点节点。"
                )
            if place_kind == "home" and place_scope == "travel":
                if not allow_safe_corrections:
                    return [], {}, f"timeline[{index}] 不能把居住地标记为跨城地点"
                place_scope = "local"
                place_city = ""
                item["place_scope"] = "local"
                item["place_city"] = ""

            target_city = place_city if place_scope == "travel" else home_city
            query = " ".join(value for value in (place, place_hint) if value)
            entries.append(
                {
                    "index": index,
                    "item": item,
                    "kind": place_kind,
                    "scope": place_scope,
                    "place": place,
                    "hint": place_hint,
                    "city": target_city,
                    "mode": travel_mode,
                    "mode_locked": bool(item.get("travel_mode_locked")),
                    "query": query,
                    "coordinate": None,
                }
            )
            if place_kind == "poi":
                search_requests[(query, target_city)] = None
        return entries, search_requests, ""

    async def _audit_location_routes(
        self,
        entries: list[dict[str, Any]],
        *,
        allow_safe_corrections: bool,
        context: RouteChoiceContext,
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], str]:
        """核验连续地点之间的交通方式、耗时和时间窗。

        Args:
            entries: 已完成地图地点解析的时间轴节点。
            allow_safe_corrections: 是否允许自动调整路线和时间轴。
            context: 交通方式判断所需的天气与身体状态。

        Returns:
            地点转换列表、按时间轴索引组织的路线和失败原因。
        """

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
                lat1, lon1 = previous["coordinate"]
                lat2, lon2 = entry["coordinate"]
                phi1 = math.radians(lat1)
                phi2 = math.radians(lat2)
                delta_phi = math.radians(lat2 - lat1)
                delta_lambda = math.radians(lon2 - lon1)
                haversine = (
                    math.sin(delta_phi / 2) ** 2
                    + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
                )
                distance = (
                    6_371_000.0
                    * 2
                    * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
                )
                if distance > 50:
                    if not entry["mode"]:
                        if not allow_safe_corrections:
                            return (
                                [],
                                {},
                                f"从“{previous['place']}”前往“{entry['place']}”缺少 travel_mode",
                            )
                        entry["mode"] = default_route_mode(distance, context)
                        entry["item"]["travel_mode"] = entry["mode"]
                        logger.warning(
                            f"[日程生成] 从“{previous['place']}”前往“{entry['place']}”"
                            "缺少交通方式，地图校正将按距离、天气、体力和路线耗时"
                            "自动选择。"
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
            available_minutes = (
                destination_minutes - origin_minutes
                if origin_minutes is not None and destination_minutes is not None
                else None
            )
            needs_time_correction = bool(
                available_minutes is not None and required_minutes > available_minutes
            )
            needs_practical_comparison = bool(
                allow_safe_corrections
                and not transition["destination"].get("mode_locked")
                and route_needs_comparison(mode, route, context)
            )
            if needs_time_correction or needs_practical_comparison:
                if not allow_safe_corrections:
                    return (
                        [],
                        {},
                        f"从“{transition['origin']['place']}”到“{transition['destination']['place']}”"
                        f"预计需要约 {required_minutes} 分钟，但时间轴只预留了 {available_minutes} 分钟",
                    )
                original_mode = mode
                choice = await self._select_route_for_window(
                    transition,
                    requested_mode=mode,
                    requested_route=route,
                    available_minutes=available_minutes,
                    locked=bool(transition["destination"].get("mode_locked")),
                    context=context,
                )
                if choice is not None:
                    mode, route, required_minutes = choice
                if (
                    available_minutes is not None
                    and required_minutes > available_minutes
                ):
                    shift_minutes = required_minutes - available_minutes
                    self._shift_timeline(
                        entries,
                        unwrapped_minutes,
                        start_index=destination_index,
                        shift_minutes=shift_minutes,
                    )
                    logger.warning(
                        f"[日程生成] 从“{transition['origin']['place']}”到"
                        f"“{transition['destination']['place']}”当前可用路线仍需约 "
                        f"{required_minutes} 分钟，已将当前及后续日程顺延 "
                        f"{shift_minutes} 分钟。"
                    )
                elif mode != original_mode:
                    logger.warning(
                        f"[日程生成] 从“{transition['origin']['place']}”到"
                        f"“{transition['destination']['place']}”综合距离、天气、体力和"
                        f"时间安排后，已改用 {self._travel_mode_label(mode)}，预计约 "
                        f"{required_minutes} 分钟。"
                    )
                transition["destination"]["mode"] = mode
                transition["destination"]["item"]["travel_mode"] = mode
            destination_item = transition["destination"]["item"]
            destination_item["travel_detail"] = (
                str(route.get("travel_detail") or "").strip()
                if mode == "transit"
                else ""
            )
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
        return transitions, route_by_index, ""

    def _preselected_place_matches(
        self,
        candidate: dict[str, Any],
        entry: dict[str, Any],
        target_city: str,
    ) -> bool:
        """判断预选 POI 是否就是时间轴声明的地点。"""

        if not isinstance(candidate, dict) or not candidate.get("coordinate"):
            return False
        if (
            str(candidate.get("name") or "").strip()
            != str(entry.get("place") or "").strip()
        ):
            return False
        if not self._cities_match(
            str(candidate.get("city") or "").strip(), target_city
        ):
            return False
        hint = str(entry.get("hint") or "").strip()
        return bool(
            not hint
            or hint in str(candidate.get("address") or "")
            or hint in str(candidate.get("district") or "")
            or hint == str(candidate.get("place_hint") or "").strip()
        )

    def _synchronize_travel_actions(
        self,
        revised: dict[str, Any],
        *,
        entries: list[dict[str, Any]],
        route_by_index: dict[int, dict[str, Any]],
        allow_safe_corrections: bool,
    ) -> str:
        """让移动动作与地图核验后的路线保持一致。

        Args:
            revised: 正在修订的日程结果。
            entries: 已归一化的时间轴地点节点。
            route_by_index: 按时间轴索引组织的路线。
            allow_safe_corrections: 是否允许丢弃或修正无效动作。

        Returns:
            校验失败原因，为空表示处理完成。
        """

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
                    return "move/travel 动作缺少有效的 timeline_index"
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
                destination["item"]["travel_detail"] = ""
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
                    return f"timeline[{timeline_index}] 的 move/travel 动作没有对应的可验证路线"
                discarded_actions.add(id(action))
                logger.warning(
                    f"[日程生成] timeline[{timeline_index}] 的 move/travel 动作"
                    "没有实际位移路线，地图校正已丢弃该可选动作。"
                )
                continue
            if planned_minutes and route["duration_minutes"] > planned_minutes:
                if not allow_safe_corrections:
                    return (
                        f"前往“{route['destination']}”预计需要约 {route['duration_minutes']} 分钟，"
                        f"但 travel 动作只预留了 {planned_minutes} 分钟"
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
                "travel_detail": route.get("travel_detail") or "",
                "distance_meters": route.get("distance_meters"),
                "route_provider": route.get("provider"),
            }
        if discarded_actions and isinstance(planned_actions, list):
            revised["planned_actions"] = [
                action
                for action in planned_actions
                if id(action) not in discarded_actions
            ]
        return ""

    async def _select_route_for_window(
        self,
        transition: dict[str, Any],
        *,
        requested_mode: str,
        requested_route: dict[str, Any],
        available_minutes: int | None,
        locked: bool,
        context: RouteChoiceContext,
    ) -> tuple[str, dict[str, Any], int] | None:
        """选择兼顾时间窗与出行负担的地图路线。"""

        modes = route_query_modes(requested_mode, locked=locked)
        alternative_modes = [mode for mode in modes if mode != requested_mode]
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
            (mode, value)
            for mode, value in zip(
                alternative_modes,
                alternative_values,
                strict=True,
            )
            if isinstance(value, dict)
        )
        return choose_practical_route(
            options,
            requested_mode=requested_mode,
            locked=locked,
            context=context,
            available_minutes=available_minutes,
        )

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
                and (candidate_city in target_city or target_city in candidate_city)
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
                "place_city": entry["city"] if entry["scope"] == "travel" else "",
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
