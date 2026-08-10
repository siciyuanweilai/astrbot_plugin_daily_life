import asyncio
import datetime
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from core.archive.schema import SCHEMA_VERSION
from core.config.options import LifeDomainSettings
from core.life.actions import LifeActionMixin
from core.life.amap import AmapWebServiceClient
from core.life.baidu_map import (
    BaiduMapWebServiceClient,
    bd09_to_gcj02,
    gcj02_to_bd09,
)
from core.life.domains import LifeDomainService
from core.life.tencent_map import TencentMapWebServiceClient
from core.life.transit import transit_route_detail
from core.models import (
    CommitmentRecord,
    DayRecord,
    EventRecord,
    LifeState,
    PlaceRecord,
    TimelineItem,
    WeekPlanRecord,
)
from support import LifeArchive


class _ActionHarness(LifeActionMixin):
    def __init__(self, archive, domains):
        self.archive = archive
        self.domains = domains

    async def sync_day_world_facts(self, *_args, **_kwargs):
        return []


class LifeDomainTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(f"{self.tempdir.name}/daily_life.db")
        self.settings = LifeDomainSettings()
        self.domains = LifeDomainService(self.settings, self.archive)
        self.harness = _ActionHarness(self.archive, self.domains)

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.tempdir.cleanup()

    def _location_audit_service(self):
        service = LifeDomainService(
            LifeDomainSettings(
                home_address="测试省测试市测试区测试路1号",
                amap_api_key="test-key",
            ),
            self.archive,
        )
        service.home_city = "测试市"
        service._map.city = "测试市"
        service._home_location = {
            "city": "测试市",
            "coordinate": (23.0, 113.0),
            "formatted_address": "测试省测试市测试区测试路1号",
        }
        return service

    @staticmethod
    def _location_payload(
        *,
        place="测试书店",
        scope="local",
        city="",
        hint="",
        destination_time="09:00",
        mode="walking",
    ):
        return {
            "timeline": [
                {
                    "time": "08:00",
                    "activity": "在家整理随身物品",
                    "status": "平静",
                    "place": "家",
                    "place_kind": "home",
                    "place_scope": "local",
                    "place_city": "",
                    "place_hint": "",
                    "travel_mode": "",
                },
                {
                    "time": destination_time,
                    "activity": f"到{place}看看",
                    "status": "期待",
                    "place": place,
                    "place_kind": "poi",
                    "place_scope": scope,
                    "place_city": city,
                    "place_hint": hint,
                    "travel_mode": mode,
                },
            ],
            "planned_actions": [
                {
                    "action_id": "2026-08-04:travel:1",
                    "action_type": "travel",
                    "target": place,
                    "timeline_index": 1,
                    "duration_minutes": 30,
                    "payload": {},
                }
            ],
            "places": [{"name": place, "type": "place", "hint": hint}],
        }

    async def test_daily_location_audit_verifies_local_poi_and_route(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-local",
                    "name": "测试书店",
                    "address": "测试区测试街2号",
                    "category": "购物;书店",
                    "city": "测试市",
                    "coordinate": (23.01, 113.01),
                }
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 1800,
                "duration_seconds": 1200,
                "provider": "amap",
                "travel_detail": "地铁",
            }
        )

        revised, reason = await service.audit_daily_locations(
            self._location_payload(mode="transit")
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["place_city"], "测试市")
        self.assertEqual(revised["timeline"][1]["travel_minutes"], 20)
        self.assertEqual(revised["timeline"][1]["travel_origin"], "家")
        self.assertEqual(revised["timeline"][1]["travel_provider"], "amap")
        self.assertEqual(revised["timeline"][1]["travel_detail"], "地铁")
        self.assertEqual(revised["places"][1]["name"], "测试书店")
        self.assertEqual(
            revised["planned_actions"][0]["payload"]["route_provider"], "amap"
        )
        self.assertEqual(
            revised["planned_actions"][0]["payload"]["travel_detail"], "地铁"
        )
        service._map.search_places.assert_awaited_once_with(
            "测试书店", city_hint="测试市", limit=5
        )

    async def test_daily_location_preselection_returns_map_confirmed_candidate(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-preselected",
                    "name": "测试书店",
                    "address": "测试区测试街2号",
                    "district": "测试区",
                    "category": "书店",
                    "city": "测试市",
                    "coordinate": (23.01, 113.01),
                }
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 1800,
                "duration_seconds": 900,
                "provider": "amap",
            }
        )

        result = await service.prepare_daily_location_candidates(
            [
                {
                    "purpose": "下午安静阅读",
                    "query": "书店",
                    "place_scope": "local",
                    "travel_mode": "walking",
                    "travel_mode_locked": True,
                }
            ]
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["home_city"], "测试市")
        self.assertEqual(result["candidates"][0]["name"], "测试书店")
        self.assertEqual(result["candidates"][0]["place_hint"], "测试区测试街2号")
        self.assertEqual(result["candidates"][0]["travel_minutes"], 15)
        service._map.search_places.assert_awaited_once()
        service._map.route.assert_awaited_once()

    async def test_daily_location_preselection_avoids_long_hot_weather_walk(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-hot-route",
                    "name": "测试美食广场",
                    "address": "测试区测试街8号",
                    "district": "测试区",
                    "category": "餐饮",
                    "city": "测试市",
                    "coordinate": (23.04, 113.02),
                }
            ]
        )

        async def route(_origin, _destination, mode, **_kwargs):
            values = {
                "walking": (4457, 3600),
                "transit": (5000, 1200),
            }
            distance, duration = values[mode]
            return {
                "distance_meters": distance,
                "duration_seconds": duration,
                "provider": "amap",
            }

        service._map.route = AsyncMock(side_effect=route)

        result = await service.prepare_daily_location_candidates(
            [
                {
                    "purpose": "傍晚吃饭",
                    "query": "美食广场",
                    "place_scope": "local",
                    "travel_mode": "auto",
                }
            ],
            weather_info={"temp": 34, "is_hot": True},
        )

        self.assertEqual(result["candidates"][0]["travel_mode"], "transit")
        self.assertEqual(result["candidates"][0]["travel_minutes"], 20)
        self.assertEqual(
            [call.args[2] for call in service._map.route.await_args_list],
            ["walking", "transit"],
        )

    async def test_daily_location_audit_reuses_preselected_candidate(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            side_effect=AssertionError("预选地点不应再次搜索")
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 1800,
                "duration_seconds": 900,
                "provider": "amap",
            }
        )
        preselected = [
            {
                "poi_id": "poi-preselected",
                "name": "测试书店",
                "address": "测试区测试街2号",
                "district": "测试区",
                "category": "书店",
                "city": "测试市",
                "place_hint": "测试区测试街2号",
                "coordinate": (23.01, 113.01),
            }
        ]

        revised, reason = await service.audit_daily_locations(
            self._location_payload(hint="测试区测试街2号"),
            preselected_places=preselected,
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["place"], "测试书店")
        self.assertEqual(revised["timeline"][1]["travel_minutes"], 15)
        self.assertEqual(revised["location_audit"]["substituted_places"], 0)
        service._map.search_places.assert_not_awaited()

    async def test_daily_location_audit_allows_explicit_cross_city_travel(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-travel",
                    "name": "旅行博物馆",
                    "address": "旅行区远方路8号",
                    "category": "科教文化;博物馆",
                    "city": "旅行市",
                    "coordinate": (24.0, 114.0),
                }
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 150000,
                "duration_seconds": 7200,
                "provider": "amap",
            }
        )
        payload = self._location_payload(
            place="旅行博物馆",
            scope="travel",
            city="旅行市",
            destination_time="11:00",
            mode="transit",
        )
        payload["planned_actions"][0]["duration_minutes"] = 120

        revised, reason = await service.audit_daily_locations(payload)

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["place_scope"], "travel")
        self.assertEqual(revised["timeline"][1]["place_city"], "旅行市")
        service._map.search_places.assert_awaited_once_with(
            "旅行博物馆", city_hint="旅行市", limit=5
        )

    async def test_daily_location_audit_rejects_unannounced_cross_city_poi(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-other-city",
                    "name": "测试书店",
                    "address": "异地路9号",
                    "city": "异地市",
                    "coordinate": (25.0, 115.0),
                }
            ]
        )

        _, reason = await service.audit_daily_locations(self._location_payload())

        self.assertIn("与日程声明的测试市不一致", reason)

    async def test_daily_location_audit_requires_hint_for_ambiguous_poi(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-a",
                    "name": "测试书店",
                    "address": "测试区甲路1号",
                    "city": "测试市",
                    "coordinate": (23.01, 113.01),
                },
                {
                    "poi_id": "poi-b",
                    "name": "测试书店",
                    "address": "测试区乙路2号",
                    "city": "测试市",
                    "coordinate": (23.02, 113.02),
                },
            ]
        )

        _, reason = await service.audit_daily_locations(self._location_payload())

        self.assertIn("存在多个同名候选", reason)
        self.assertIn("place_hint", reason)

    async def test_daily_location_audit_final_fallback_uses_ranked_map_candidate(
        self,
    ):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-other",
                    "name": "另一家测试书店",
                    "address": "测试区测试街9号",
                    "city": "测试市",
                    "coordinate": (23.02, 113.02),
                }
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 1900,
                "duration_seconds": 1200,
                "provider": "amap",
            }
        )

        revised, reason = await service.audit_daily_locations(
            self._location_payload(place="街角测试书店"),
            allow_safe_corrections=True,
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["place"], "另一家测试书店")
        self.assertEqual(revised["timeline"][1]["place_kind"], "poi")
        self.assertEqual(revised["timeline"][1]["place_address"], "测试区测试街9号")
        self.assertEqual(revised["timeline"][1]["activity"], "到另一家测试书店看看")
        self.assertEqual(revised["planned_actions"][0]["target"], "另一家测试书店")
        self.assertEqual(revised["location_audit"]["substituted_places"], 1)
        self.assertEqual(
            revised["location_audit"]["place_substitutions"],
            [{"original": "街角测试书店", "canonical": "另一家测试书店"}],
        )
        self.assertEqual(revised["location_audit"]["downgraded_places"], 0)

    async def test_daily_location_audit_final_fallback_uses_generic_without_candidate(
        self,
    ):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(return_value=[])

        revised, reason = await service.audit_daily_locations(
            self._location_payload(place="街角测试书店"),
            allow_safe_corrections=True,
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["place_kind"], "generic")
        self.assertEqual(revised["timeline"][1]["place_address"], "")
        self.assertIsNone(revised["timeline"][1]["place_latitude"])
        self.assertEqual(revised["timeline"][1]["travel_minutes"], 30)
        self.assertEqual(
            revised["planned_actions"][0]["payload"]["route_provider"],
            "default_estimate",
        )
        self.assertEqual(revised["location_audit"]["downgraded_places"], 1)
        self.assertEqual(
            revised["location_audit"]["downgraded_place_names"],
            ["街角测试书店"],
        )

    async def test_daily_location_audit_uses_hint_to_resolve_duplicate_poi(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-a",
                    "name": "测试书店",
                    "address": "测试区甲路1号",
                    "city": "测试市",
                    "coordinate": (23.01, 113.01),
                },
                {
                    "poi_id": "poi-b",
                    "name": "测试书店",
                    "address": "测试区乙路2号",
                    "city": "测试市",
                    "coordinate": (23.02, 113.02),
                },
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 2200,
                "duration_seconds": 1500,
                "provider": "amap",
            }
        )

        revised, reason = await service.audit_daily_locations(
            self._location_payload(hint="乙路2号")
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["place_address"], "测试区乙路2号")

    async def test_daily_location_audit_rejects_insufficient_route_time(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-far",
                    "name": "测试书店",
                    "address": "测试区远方路1号",
                    "city": "测试市",
                    "coordinate": (23.1, 113.1),
                }
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 20000,
                "duration_seconds": 3600,
                "provider": "amap",
            }
        )

        _, reason = await service.audit_daily_locations(
            self._location_payload(destination_time="08:30")
        )

        self.assertIn("预计需要约 60 分钟", reason)
        self.assertIn("只预留了 30 分钟", reason)

    async def test_daily_location_audit_final_correction_switches_to_fitting_mode(
        self,
    ):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-far",
                    "name": "测试书店",
                    "address": "测试区远方路1号",
                    "city": "测试市",
                    "coordinate": (23.1, 113.1),
                }
            ]
        )

        async def route(_origin, _destination, mode, **_kwargs):
            durations = {
                "walking": 3600,
                "transit": 1200,
                "cycling": 1800,
                "driving": 900,
            }
            return {
                "distance_meters": 20000,
                "duration_seconds": durations[mode],
                "provider": "amap",
            }

        service._map.route = AsyncMock(side_effect=route)

        revised, reason = await service.audit_daily_locations(
            self._location_payload(destination_time="08:30"),
            allow_safe_corrections=True,
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["time"], "08:30")
        self.assertEqual(revised["timeline"][1]["travel_mode"], "transit")
        self.assertEqual(revised["timeline"][1]["travel_minutes"], 20)
        self.assertEqual(
            revised["planned_actions"][0]["payload"]["travel_mode"],
            "transit",
        )

    async def test_daily_location_audit_rechecks_impractical_walk_even_if_it_fits(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-hot-route",
                    "name": "测试书店",
                    "address": "测试区测试街8号",
                    "city": "测试市",
                    "coordinate": (23.04, 113.02),
                }
            ]
        )

        async def route(_origin, _destination, mode, **_kwargs):
            values = {
                "walking": (4457, 3600),
                "transit": (5000, 1200),
            }
            distance, duration = values[mode]
            return {
                "distance_meters": distance,
                "duration_seconds": duration,
                "provider": "amap",
            }

        service._map.route = AsyncMock(side_effect=route)

        revised, reason = await service.audit_daily_locations(
            self._location_payload(destination_time="10:00"),
            allow_safe_corrections=True,
            weather_info={"temp": 34, "is_hot": True},
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["travel_mode"], "transit")
        self.assertEqual(revised["timeline"][1]["travel_minutes"], 20)
        self.assertEqual(revised["timeline"][1]["time"], "10:00")

    async def test_daily_location_audit_preserves_explicit_locked_walk(self):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-walk-purpose",
                    "name": "测试书店",
                    "address": "测试区测试街8号",
                    "city": "测试市",
                    "coordinate": (23.04, 113.02),
                }
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 4457,
                "duration_seconds": 3600,
                "provider": "amap",
            }
        )
        payload = self._location_payload(destination_time="10:00")
        payload["timeline"][1]["travel_mode_locked"] = True

        revised, reason = await service.audit_daily_locations(
            payload,
            allow_safe_corrections=True,
            weather_info={"temp": 34, "is_hot": True},
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["travel_mode"], "walking")
        service._map.route.assert_awaited_once()

    async def test_daily_location_audit_final_correction_shifts_timeline_if_needed(
        self,
    ):
        service = self._location_audit_service()
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-far",
                    "name": "测试书店",
                    "address": "测试区远方路1号",
                    "city": "测试市",
                    "coordinate": (23.1, 113.1),
                }
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 20000,
                "duration_seconds": 3600,
                "provider": "amap",
            }
        )

        revised, reason = await service.audit_daily_locations(
            self._location_payload(destination_time="08:30"),
            allow_safe_corrections=True,
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["time"], "09:00")
        self.assertEqual(revised["timeline"][1]["travel_minutes"], 60)
        self.assertEqual(revised["planned_actions"][0]["duration_minutes"], 60)

    async def test_daily_location_audit_final_correction_normalizes_safe_defaults(
        self,
    ):
        service = self._location_audit_service()
        payload = self._location_payload()
        payload["timeline"][1].update(
            {
                "place_kind": "unknown",
                "place_scope": "unknown",
                "travel_mode": "unknown",
            }
        )
        payload["planned_actions"][0]["timeline_index"] = "invalid"

        revised, reason = await service.audit_daily_locations(
            payload,
            allow_safe_corrections=True,
        )

        self.assertEqual(reason, "")
        self.assertEqual(revised["timeline"][1]["place_kind"], "generic")
        self.assertEqual(revised["timeline"][1]["place_scope"], "local")
        self.assertEqual(revised["timeline"][1]["travel_mode"], "")
        self.assertEqual(revised["planned_actions"], [])

    @staticmethod
    def _day(action):
        return DayRecord(
            date="2026-08-03",
            state=LifeState(energy=80, stress=40, mood_score=60),
            timeline=[
                TimelineItem(
                    time="12:00",
                    activity="完成明确计划的生活动作",
                    status="平稳",
                    execution_state="completed",
                    execution_evidence="时间轴节点已结束",
                    execution_updated_at="2026-08-03 12:30:00",
                )
            ],
            meta={
                "planned_life_actions": json.dumps(
                    [action], ensure_ascii=False, separators=(",", ":")
                )
            },
        )

    async def test_internal_meal_simulation_settles_once_and_consumes_stock(self):
        await self.archive.adjust_pantry_item("大米", 2, unit="份", source="test")
        day = self._day(
            {
                "action_id": "2026-08-03:meal:0",
                "action_type": "cook",
                "target": "番茄鸡蛋饭",
                "timeline_index": 0,
                "duration_minutes": 30,
                "payload": {
                    "meal_type": "午餐",
                    "ingredients": [{"name": "大米", "quantity": 1, "unit": "份"}],
                },
                "source": "daily_plan",
            }
        )

        first = await self.harness.settle_completed_planned_actions(day)
        second = await self.harness.settle_completed_planned_actions(day)

        self.assertEqual(first[0].status, "committed")
        self.assertTrue(second[0].replayed)
        pantry = await self.archive.get_pantry_items()
        self.assertEqual(pantry[0]["quantity"], 1)
        self.assertEqual(len(await self.archive.get_meal_records()), 1)
        recipes = await self.archive.get_recipes()
        self.assertEqual(len(recipes), 1)
        self.assertTrue(recipes[0]["id"].startswith("recipe:auto:"))
        receipts = await self.archive.get_life_action_receipts(
            action_id="2026-08-03:meal:0"
        )
        self.assertEqual(receipts[0].status, "simulated")

    async def test_domain_payload_enums_and_short_item_lists_are_normalized(self):
        chore_day = self._day(
            {
                "action_id": "2026-08-03:chore:text-level",
                "action_type": "chore",
                "target": "整理测试书桌",
                "timeline_index": 0,
                "duration_minutes": 15,
                "payload": {"effort": "light", "cadence_days": "7"},
                "source": "daily_plan",
            }
        )
        await self.harness.settle_completed_planned_actions(chore_day)

        exercise_day = self._day(
            {
                "action_id": "2026-08-03:exercise:text-level",
                "action_type": "exercise",
                "target": "室内舒展",
                "timeline_index": 0,
                "duration_minutes": 20,
                "payload": {"intensity": "moderate"},
                "source": "daily_plan",
            }
        )
        await self.harness.settle_completed_planned_actions(exercise_day)

        purchase_day = self._day(
            {
                "action_id": "2026-08-03:purchase:short-list",
                "action_type": "purchase",
                "target": "补充测试食材",
                "timeline_index": 0,
                "duration_minutes": 10,
                "payload": {"items": ["测试食材"]},
                "source": "daily_plan",
            }
        )
        await self.harness.settle_completed_planned_actions(purchase_day)

        chores = await self.archive.get_chores(limit=0)
        fitness = await self.archive.get_fitness_records(limit=0)
        pantry = await self.archive.get_pantry_items(limit=0)
        self.assertEqual(chores[0]["effort"], 1)
        self.assertEqual(chores[0]["cadence_days"], 7)
        self.assertEqual(fitness[0]["intensity"], 3)
        self.assertEqual(pantry[0]["name"], "测试食材")
        self.assertEqual(pantry[0]["quantity"], 1)

    async def test_replayed_action_retries_missing_domain_write(self):
        day = self._day(
            {
                "action_id": "2026-08-03:chore:retry",
                "action_type": "chore",
                "target": "整理测试资料",
                "timeline_index": 0,
                "duration_minutes": 10,
                "payload": {"effort": 2},
                "source": "daily_plan",
            }
        )
        original_apply_action = self.domains.apply_action
        self.domains.apply_action = AsyncMock(side_effect=RuntimeError("测试写入失败"))

        first = await self.harness.settle_completed_planned_actions(day)

        self.assertEqual(first[0].status, "committed")
        self.assertEqual(await self.archive.get_chore_records(limit=0), [])
        self.domains.apply_action = original_apply_action

        second = await self.harness.settle_completed_planned_actions(day)

        self.assertTrue(second[0].replayed)
        self.assertEqual(len(await self.archive.get_chore_records(limit=0)), 1)

    async def test_domain_initialize_repairs_legacy_records_idempotently(self):
        action_id = "2026-08-03:chore:legacy"
        day = self._day(
            {
                "action_id": action_id,
                "action_type": "chore",
                "target": "收好测试物品",
                "timeline_index": 0,
                "duration_minutes": 12,
                "payload": {"effort": "light"},
                "source": "daily_plan",
            }
        )
        await self.archive.save_day(day, replace=True)
        await self.archive.save_life_action_outcome(
            {
                "action_id": action_id,
                "date": day.date,
                "action_type": "chore",
                "target": "收好测试物品",
                "status": "committed",
                "evidence": ["测试时间轴已完成"],
                "committed_at": "2026-08-03 12:30:00",
            }
        )
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="明天确认测试安排",
                trigger_date="2026-08-04",
                confidence=0.95,
                source="test",
                source_session="private:test",
                source_message="测试会话中的未来安排",
            )
        )

        await self.domains.initialize()
        await self.domains.initialize()

        records = await self.archive.get_chore_records(limit=0)
        actions = await self.archive.get_conversation_action_items(limit=0)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["commitment_id"], commitment.id)
        self.assertEqual(actions[0]["status"], "open")

    async def test_domain_initialize_repairs_legacy_cook_recipe_idempotently(self):
        action_id = "2026-08-03:cook:legacy"
        day = self._day(
            {
                "action_id": action_id,
                "action_type": "cook",
                "target": "测试家常饭",
                "timeline_index": 0,
                "duration_minutes": 30,
                "payload": {
                    "meal_type": "晚餐",
                    "ingredients": [
                        {"name": "测试食材", "quantity": 1, "unit": "份"}
                    ],
                },
                "source": "daily_plan",
            }
        )
        await self.archive.save_day(day, replace=True)
        await self.archive.save_life_action_outcome(
            {
                "action_id": action_id,
                "date": day.date,
                "action_type": "cook",
                "target": "测试家常饭",
                "status": "committed",
                "evidence": ["测试时间轴已完成"],
                "committed_at": "2026-08-03 18:30:00",
            }
        )
        await self.archive.save_meal_record(
            {
                "action_id": action_id,
                "date": day.date,
                "meal_type": "晚餐",
                "name": "测试家常饭",
                "recipe_id": "",
                "status": "completed",
                "ingredients": [
                    {"name": "测试食材", "quantity": 1, "unit": "份"}
                ],
                "source": "life_action_simulation",
                "evidence": ["测试时间轴已完成"],
                "occurred_at": "2026-08-03 18:30:00",
            }
        )

        await self.domains.initialize()
        await self.domains.initialize()

        recipes = await self.archive.get_recipes(limit=0)
        meals = await self.archive.get_meal_records(limit=0)
        self.assertEqual(len(recipes), 1)
        self.assertEqual(meals[0]["recipe_id"], recipes[0]["id"])

    async def test_internal_outfit_change_completes_without_external_receipt(self):
        outfit = "薄荷绿棉质上衣配浅色牛仔短裤"
        day = self._day(
            {
                "action_id": "2026-08-03:change_outfit:0",
                "action_type": "change_outfit",
                "target": outfit,
                "timeline_index": 0,
                "duration_minutes": 10,
                "source": "daily_plan",
            }
        )

        await self.domains.sync_activity_sessions(
            day,
            now=datetime.datetime(2026, 8, 3, 12, 30),
        )
        outcomes = await self.harness.settle_completed_planned_actions(day)
        await self.domains.sync_activity_sessions(
            day,
            now=datetime.datetime(2026, 8, 3, 12, 31),
        )

        self.assertEqual(outcomes[0].status, "committed")
        self.assertEqual(day.timeline[0].execution_state, "completed")
        self.assertEqual(day.outfit, outfit)
        receipts = await self.archive.get_life_action_receipts(
            action_id="2026-08-03:change_outfit:0"
        )
        self.assertEqual(receipts[0].status, "simulated")
        sessions = await self.archive.get_activity_sessions(limit=0)
        self.assertEqual(sessions[0]["status"], "completed")

    async def test_legacy_expired_outfit_change_is_repaired_on_settlement(self):
        action_id = "2026-08-03:change_outfit:legacy"
        outfit = "浅绿色短袖配靛蓝色牛仔短裤"
        detailed_outfit = "浅绿色纯棉短袖上衣，搭配靛蓝色高腰牛仔短裤和白色帆布鞋"
        day = self._day(
            {
                "action_id": action_id,
                "action_type": "change_outfit",
                "target": outfit,
                "timeline_index": 0,
                "duration_minutes": 10,
                "source": "daily_plan",
            }
        )
        day.outfit = detailed_outfit
        day.timeline[0].execution_state = "expired"
        day.timeline[
            0
        ].execution_reason = "计划时间已经结束，但没有收到可验证的执行回执"
        day.meta["life_action_expirations"] = json.dumps(
            {
                action_id: {
                    "action_id": action_id,
                    "action_type": "change_outfit",
                    "status": "expired",
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        await self.domains.sync_activity_sessions(
            day,
            now=datetime.datetime(2026, 8, 3, 12, 30),
        )
        outcomes = await self.harness.settle_completed_planned_actions(
            day,
            now=datetime.datetime(2026, 8, 3, 12, 31),
        )
        await self.domains.sync_activity_sessions(
            day,
            now=datetime.datetime(2026, 8, 3, 12, 32),
        )

        self.assertEqual(outcomes[0].status, "committed")
        self.assertEqual(day.timeline[0].execution_state, "completed")
        self.assertEqual(day.outfit, detailed_outfit)
        self.assertIn(detailed_outfit, day.outfit_history.values())
        expirations = json.loads(day.meta["life_action_expirations"])
        self.assertNotIn(action_id, expirations)
        sessions = await self.archive.get_activity_sessions(limit=0)
        self.assertEqual(sessions[0]["status"], "completed")
        receipts = await self.archive.get_life_action_receipts(action_id=action_id)
        self.assertEqual(receipts[0].status, "simulated")

    async def test_insufficient_pantry_rejects_simulated_meal(self):
        await self.archive.adjust_pantry_item("鸡蛋", 1, unit="个", source="test")
        day = self._day(
            {
                "action_id": "2026-08-03:meal:missing",
                "action_type": "cook",
                "target": "鸡蛋料理",
                "timeline_index": 0,
                "duration_minutes": 20,
                "payload": {
                    "ingredients": [{"name": "鸡蛋", "quantity": 2, "unit": "个"}]
                },
                "source": "daily_plan",
            }
        )

        outcomes = await self.harness.settle_completed_planned_actions(day)

        self.assertEqual(outcomes[0].status, "failed")
        self.assertIn("库存不足", outcomes[0].reason)
        self.assertEqual(day.timeline[0].execution_state, "skipped")
        self.assertEqual(await self.archive.get_meal_records(), [])
        pantry = await self.archive.get_pantry_items()
        self.assertEqual(pantry[0]["quantity"], 1)

    async def test_direct_meal_does_not_require_or_consume_pantry(self):
        day = self._day(
            {
                "action_id": "2026-08-03:meal:direct",
                "action_type": "meal",
                "target": "测试餐食",
                "timeline_index": 0,
                "duration_minutes": 20,
                "payload": {
                    "meal_type": "午餐",
                    "ingredients": [
                        {"name": "测试食材", "quantity": 2, "unit": "份"}
                    ],
                },
                "source": "daily_plan",
            }
        )

        outcomes = await self.harness.settle_completed_planned_actions(day)

        self.assertEqual(outcomes[0].status, "committed")
        self.assertEqual(len(await self.archive.get_meal_records()), 1)
        self.assertEqual(await self.archive.get_pantry_items(), [])
        self.assertEqual(await self.archive.get_recipes(), [])

    async def test_external_photo_action_still_expires_without_receipt(self):
        day = self._day(
            {
                "action_id": "2026-08-03:photo:0",
                "action_type": "photo",
                "target": "发一张照片",
                "timeline_index": 0,
                "duration_minutes": 5,
                "source": "daily_plan",
            }
        )

        outcomes = await self.harness.settle_completed_planned_actions(day)

        self.assertEqual(outcomes[0].status, "expired")
        self.assertEqual(day.timeline[0].execution_state, "expired")
        self.assertEqual(await self.archive.get_life_action_receipts(limit=10), [])

    async def test_activity_session_and_unified_timeline_are_persisted(self):
        day = self._day(
            {
                "action_id": "2026-08-03:exercise:0",
                "action_type": "exercise",
                "target": "室内拉伸",
                "timeline_index": 0,
                "duration_minutes": 15,
                "payload": {"intensity": 2},
                "source": "daily_plan",
            }
        )
        await self.domains.sync_activity_sessions(
            day,
            now=datetime.datetime(2026, 8, 3, 12, 30),
        )
        await self.harness.settle_completed_planned_actions(day)
        await self.domains.sync_activity_sessions(
            day,
            now=datetime.datetime(2026, 8, 3, 12, 31),
        )

        snapshot = await self.domains.snapshot()

        self.assertEqual(snapshot["activity_sessions"][0]["status"], "completed")
        self.assertEqual(snapshot["fitness"][0]["activity"], "室内拉伸")
        self.assertEqual(len(snapshot["timeline"]), 1)
        self.assertEqual(snapshot["timeline"][0]["kind"], "fitness")
        self.assertEqual(snapshot["timeline"][0]["action_id"], "2026-08-03:exercise:0")
        self.assertNotIn("voice", snapshot)

    async def test_skipped_activity_session_keeps_distinct_status(self):
        day = self._day(
            {
                "action_id": "2026-08-03:rest:skipped",
                "action_type": "rest",
                "target": "取消的休息安排",
                "timeline_index": 0,
                "duration_minutes": 15,
                "source": "daily_plan",
            }
        )
        day.timeline[0].execution_state = "skipped"
        day.timeline[0].execution_reason = "临时安排变化"
        day.timeline[0].execution_evidence = "夜间复盘确认跳过"

        await self.domains.sync_activity_sessions(
            day,
            now=datetime.datetime(2026, 8, 3, 23, 59),
        )

        sessions = await self.archive.get_activity_sessions(limit=0)
        self.assertEqual(sessions[0]["status"], "skipped")

    async def test_unsettled_activity_session_remains_in_unified_timeline(self):
        day = self._day(
            {
                "action_id": "2026-08-03:photo:pending",
                "action_type": "photo",
                "target": "整理测试照片",
                "timeline_index": 0,
                "duration_minutes": 10,
                "source": "daily_plan",
            }
        )
        await self.domains.sync_activity_sessions(
            day,
            now=datetime.datetime(2026, 8, 3, 12, 30),
        )

        snapshot = await self.domains.snapshot()

        self.assertEqual(len(snapshot["timeline"]), 1)
        self.assertEqual(snapshot["timeline"][0]["kind"], "activity")
        self.assertEqual(
            snapshot["timeline"][0]["action_id"], "2026-08-03:photo:pending"
        )

    async def test_activity_sync_removes_superseded_unfinished_sessions(self):
        await self.archive.upsert_activity_session(
            {
                "action_id": "2026-08-03:old:planned",
                "date": "2026-08-03",
                "activity_type": "cook",
                "title": "旧计划晚餐",
                "status": "planned",
                "started_at": "2026-08-03 18:30:00",
                "source": "daily_plan",
            }
        )
        await self.archive.upsert_activity_session(
            {
                "action_id": "2026-08-03:old:completed",
                "date": "2026-08-03",
                "activity_type": "meal",
                "title": "已经完成的旧计划",
                "status": "completed",
                "started_at": "2026-08-03 12:00:00",
                "ended_at": "2026-08-03 12:30:00",
                "source": "daily_plan",
            }
        )
        day = self._day(
            {
                "action_id": "2026-08-03:new:planned",
                "action_type": "cook",
                "target": "当前计划晚餐",
                "timeline_index": 0,
                "duration_minutes": 20,
                "source": "daily_plan",
            }
        )

        await self.domains.sync_activity_sessions(
            day,
            now=datetime.datetime(2026, 8, 3, 12, 30),
        )

        sessions = await self.archive.get_activity_sessions(limit=0)
        action_ids = {item["action_id"] for item in sessions}
        self.assertNotIn("2026-08-03:old:planned", action_ids)
        self.assertIn("2026-08-03:old:completed", action_ids)
        self.assertIn("2026-08-03:new:planned", action_ids)

    async def test_domain_snapshot_hides_superseded_simulated_records(self):
        current_action_id = "2026-08-03:meal:current"
        stale_action_id = "2026-08-03:meal:stale"
        day = self._day(
            {
                "action_id": current_action_id,
                "action_type": "meal",
                "target": "当前测试餐食",
                "timeline_index": 0,
                "duration_minutes": 20,
                "source": "daily_plan",
            }
        )
        await self.archive.save_day(day, replace=True)
        for action_id, name in (
            (current_action_id, "当前测试餐食"),
            (stale_action_id, "已替换测试餐食"),
        ):
            await self.archive.upsert_activity_session(
                {
                    "action_id": action_id,
                    "date": day.date,
                    "activity_type": "meal",
                    "title": name,
                    "status": "completed",
                    "started_at": f"{day.date} 12:00:00",
                    "ended_at": f"{day.date} 12:30:00",
                    "source": "daily_plan",
                }
            )
            await self.archive.save_meal_record(
                {
                    "action_id": action_id,
                    "date": day.date,
                    "meal_type": "午餐",
                    "name": name,
                    "status": "completed",
                    "source": "life_action_simulation",
                    "occurred_at": f"{day.date} 12:30:00",
                }
            )
        await self.archive.upsert_chore(
            {
                "id": stale_action_id,
                "name": "已替换测试家务",
                "enabled": True,
                "source": "life_action_simulation",
            }
        )
        await self.archive.save_chore_record(
            {
                "action_id": stale_action_id,
                "chore_id": stale_action_id,
                "name": "已替换测试家务",
                "status": "completed",
                "source": "life_action_simulation",
                "occurred_at": f"{day.date} 13:00:00",
            }
        )
        await self.archive.save_fitness_record(
            {
                "action_id": stale_action_id,
                "date": day.date,
                "activity": "已替换测试运动",
                "duration_minutes": 20,
                "status": "completed",
                "source": "life_action_simulation",
                "occurred_at": f"{day.date} 14:00:00",
            }
        )

        snapshot = await self.domains.snapshot()
        context = await self.domains.format_context()

        self.assertEqual(
            [item["action_id"] for item in snapshot["meals"]],
            [current_action_id],
        )
        self.assertEqual(snapshot["chores"], [])
        self.assertEqual(snapshot["chore_records"], [])
        self.assertEqual(snapshot["fitness"], [])
        self.assertEqual(
            [item["action_id"] for item in snapshot["timeline"]],
            [current_action_id],
        )
        self.assertIn("当前测试餐食", context)
        self.assertNotIn("已替换测试餐食", context)
        self.assertNotIn("已替换测试家务", context)
        self.assertNotIn("已替换测试运动", context)

    async def test_domain_snapshot_keeps_superseded_action_with_real_receipt(self):
        stale_action_id = "2026-08-03:meal:confirmed"
        day = self._day(
            {
                "action_id": "2026-08-03:meal:current",
                "action_type": "meal",
                "target": "当前测试餐食",
                "timeline_index": 0,
                "duration_minutes": 20,
                "source": "daily_plan",
            }
        )
        await self.archive.save_day(day, replace=True)
        await self.archive.upsert_activity_session(
            {
                "action_id": stale_action_id,
                "date": day.date,
                "activity_type": "meal",
                "title": "有真实回执的测试餐食",
                "status": "completed",
                "started_at": f"{day.date} 12:00:00",
                "ended_at": f"{day.date} 12:30:00",
                "source": "daily_plan",
            }
        )
        await self.archive.save_life_action_receipt(
            {
                "receipt_id": "receipt:confirmed:test",
                "action_id": stale_action_id,
                "date": day.date,
                "action_type": "meal",
                "status": "confirmed",
                "source": "test_receipt",
                "occurred_at": f"{day.date} 12:30:00",
            }
        )

        snapshot = await self.domains.snapshot()

        self.assertIn(
            stale_action_id,
            {item["action_id"] for item in snapshot["activity_sessions"]},
        )

    async def test_domain_snapshot_hides_past_unconfirmed_terminal_plans(self):
        expired_action_id = "2026-08-03:photo:expired"
        failed_action_id = "2026-08-03:photo:failed"
        day = DayRecord(
            date="2026-08-03",
            timeline=[
                TimelineItem(time="10:00", activity="未确认的测试照片"),
                TimelineItem(time="11:00", activity="执行失败的测试照片"),
            ],
            meta={
                "planned_life_actions": json.dumps(
                    [
                        {
                            "action_id": expired_action_id,
                            "action_type": "photo",
                            "timeline_index": 0,
                        },
                        {
                            "action_id": failed_action_id,
                            "action_type": "photo",
                            "timeline_index": 1,
                        },
                    ],
                    ensure_ascii=False,
                )
            },
        )
        await self.archive.save_day(day, replace=True)
        for action_id, title, status in (
            (expired_action_id, "未确认的测试照片", "expired"),
            (failed_action_id, "执行失败的测试照片", "failed"),
        ):
            await self.archive.upsert_activity_session(
                {
                    "action_id": action_id,
                    "date": day.date,
                    "activity_type": "photo",
                    "title": title,
                    "status": status,
                    "started_at": f"{day.date} 10:00:00",
                    "ended_at": f"{day.date} 23:59:00",
                    "source": "daily_plan",
                }
            )
        await self.archive.save_life_action_receipt(
            {
                "receipt_id": "receipt:photo:failed",
                "action_id": failed_action_id,
                "date": day.date,
                "action_type": "photo",
                "status": "failed",
                "source": "test_receipt",
                "occurred_at": f"{day.date} 11:05:00",
            }
        )

        with patch(
            "core.archive.domains.life_today",
            return_value=datetime.date(2026, 8, 4),
        ):
            snapshot = await self.domains.snapshot()

        visible_ids = {item["action_id"] for item in snapshot["activity_sessions"]}
        self.assertNotIn(expired_action_id, visible_ids)
        self.assertIn(failed_action_id, visible_ids)

    async def test_empty_current_action_set_hides_old_simulated_records(self):
        day = self._day(
            {
                "action_id": "2026-08-03:temporary",
                "action_type": "meal",
                "target": "临时测试餐食",
                "timeline_index": 0,
            }
        )
        day.meta = {}
        await self.archive.save_day(day, replace=True)
        await self.archive.upsert_activity_session(
            {
                "action_id": "2026-08-03:old:simulated",
                "date": day.date,
                "activity_type": "meal",
                "title": "旧模拟餐食",
                "status": "completed",
                "started_at": f"{day.date} 12:00:00",
                "ended_at": f"{day.date} 12:30:00",
                "source": "daily_plan",
            }
        )
        await self.archive.save_meal_record(
            {
                "action_id": "2026-08-03:old:simulated",
                "date": day.date,
                "name": "旧模拟餐食",
                "status": "completed",
                "source": "life_action_simulation",
                "occurred_at": f"{day.date} 12:30:00",
            }
        )

        snapshot = await self.domains.snapshot()

        self.assertEqual(snapshot["activity_sessions"], [])
        self.assertEqual(snapshot["meals"], [])
        self.assertEqual(snapshot["timeline"], [])

    async def test_action_items_and_context_budget_are_structured(self):
        await self.archive.save_conversation_action_item(
            {
                "commitment_id": 7,
                "title": "整理下次聊天要确认的资料",
                "owner": "共同",
                "due_at": "2026-08-04 20:00",
                "status": "open",
                "source_session": "test-session",
                "source_message": "测试会话内容",
                "evidence": ["模型确认的未来约定"],
            }
        )

        context = await self.domains.format_context()

        self.assertIn("待办行动项", context)
        self.assertIn("负责人：共同", context)
        self.assertLessEqual(len(context), self.settings.context_budget_chars)

    async def test_context_exposes_missing_fitness_without_forcing_activity(self):
        context = await self.domains.format_context()

        self.assertIn("近期运动：尚无已结算记录", context)
        self.assertIn("不要求今天强行安排", context)

    async def test_route_falls_back_without_coordinates(self):
        route = await self.domains.estimate_route("地点甲", "地点乙")

        self.assertEqual(route["provider"], "default_estimate")
        self.assertEqual(
            route["duration_seconds"], self.settings.default_travel_minutes * 60
        )

    async def test_map_tools_report_missing_configuration(self):
        result = await self.domains.tool_place_search("测试地点")

        self.assertFalse(result["ok"])
        self.assertIn("高德地图", result["reason"])
        self.assertIn("居住地", result["reason"])

    async def test_amap_geocode_and_route_response_are_normalized(self):
        client = AmapWebServiceClient("test-key", city="测试市")
        client._request_json = AsyncMock(
            side_effect=[
                {
                    "status": "1",
                    "geocodes": [
                        {
                            "location": "113.123456,23.123456",
                            "formatted_address": "测试省测试市测试地点",
                            "city": "测试市",
                            "citycode": "0000",
                            "adcode": "123400",
                        }
                    ],
                },
                {
                    "status": "1",
                    "route": {
                        "paths": [
                            {
                                "distance": "1280",
                                "cost": {"duration": "960"},
                            }
                        ]
                    },
                },
            ]
        )

        place = await client.geocode("测试地点", city_hint="")
        route = await client.route(
            (23.123456, 113.123456),
            (23.124456, 113.133456),
            "driving",
        )

        self.assertEqual(place["citycode"], "0000")
        self.assertEqual(place["latitude"], 23.123456)
        self.assertNotIn("city", client._request_json.await_args_list[0].args[1])
        self.assertEqual(route["provider"], "amap")
        self.assertEqual(route["distance_meters"], 1280)
        self.assertEqual(route["duration_seconds"], 960)

    async def test_amap_transit_resolves_city_name_to_adcode_and_caches_it(self):
        client = AmapWebServiceClient("test-key", city="测试市")

        async def resolve_city(*_args, **_kwargs):
            await asyncio.sleep(0)
            return {"city": "测试市", "adcode": "123400"}

        client.geocode = AsyncMock(side_effect=resolve_city)
        client._request_json = AsyncMock(
            return_value={
                "route": {
                    "transits": [
                        {
                            "distance": "5600",
                            "cost": {"duration": "1800"},
                            "segments": [
                                {
                                    "bus": {
                                        "buslines": [
                                            {
                                                "name": "测试地铁二号线",
                                                "type": "地铁线路",
                                            }
                                        ]
                                    }
                                },
                                {
                                    "bus": {
                                        "buslines": [
                                            {
                                                "name": "测试公交十五路",
                                                "type": "公交线路",
                                            }
                                        ]
                                    }
                                },
                            ],
                        }
                    ]
                }
            }
        )

        first, second = await asyncio.gather(
            client.route(
                (23.0000, 113.0000),
                (23.1000, 113.1000),
                "transit",
                origin_city="测试市",
                destination_city="测试市",
            ),
            client.route(
                (23.0000, 113.0000),
                (23.1000, 113.1000),
                "transit",
                origin_city="测试市",
                destination_city="测试市",
            ),
        )

        self.assertEqual(first["provider"], "amap")
        self.assertEqual(first["travel_detail"], "公交 + 地铁")
        self.assertEqual(second["duration_seconds"], 1800)
        client.geocode.assert_awaited_once_with("测试市", city_hint="")
        for request in client._request_json.await_args_list:
            self.assertEqual(request.args[0], "/v5/direction/transit/integrated")
            self.assertEqual(request.args[1]["city1"], "123400")
            self.assertEqual(request.args[1]["city2"], "123400")
            self.assertEqual(request.args[1]["show_fields"], "cost,navi")

    def test_transit_route_detail_uses_structured_map_route(self):
        self.assertEqual(
            transit_route_detail(
                {
                    "steps": [
                        {"vehicle": {"type": "SUBWAY", "name": "测试一号线"}},
                        {"vehicle": {"type": "BUS", "name": "测试十路"}},
                    ]
                }
            ),
            "公交 + 地铁",
        )
        self.assertEqual(
            transit_route_detail({"segments": [{"type": "地铁线路"}]}),
            "地铁",
        )
        self.assertEqual(transit_route_detail({"distance": 1000}), "")

    async def test_amap_transit_accepts_adcodes_without_geocoding(self):
        client = AmapWebServiceClient("test-key")
        client.geocode = AsyncMock()
        client._request_json = AsyncMock(
            return_value={
                "route": {
                    "transits": [
                        {
                            "distance": "12000",
                            "cost": {"duration": "2700"},
                        }
                    ]
                }
            }
        )

        route = await client.route(
            (23.0000, 113.0000),
            (24.0000, 114.0000),
            "transit",
            origin_city="123400",
            destination_city="567800",
        )

        self.assertEqual(route["duration_seconds"], 2700)
        client.geocode.assert_not_awaited()
        params = client._request_json.await_args.args[1]
        self.assertEqual(params["city1"], "123400")
        self.assertEqual(params["city2"], "567800")

    async def test_map_clients_use_explicit_city_for_travel_search(self):
        amap = AmapWebServiceClient("test-key", city="居住市")
        amap._request_json = AsyncMock(return_value={"status": "1", "pois": []})
        tencent = TencentMapWebServiceClient("test-key", city="居住市")
        tencent._request_json = AsyncMock(return_value={"data": []})
        baidu = BaiduMapWebServiceClient("test-key", city="居住市")
        baidu._request_json = AsyncMock(return_value={"results": []})

        await amap.search_places("博物馆", city_hint="旅行市")
        await tencent.search_places("博物馆", city_hint="旅行市")
        await baidu.search_places("博物馆", city_hint="旅行市")

        self.assertEqual(amap._request_json.await_args.args[1]["region"], "旅行市")
        self.assertEqual(
            tencent._request_json.await_args.args[1]["boundary"],
            "region(旅行市,1)",
        )
        self.assertEqual(baidu._request_json.await_args.args[1]["region"], "旅行市")
        self.assertEqual(amap.city, "居住市")
        self.assertEqual(tencent.city, "居住市")
        self.assertEqual(baidu.city, "居住市")

    async def test_amap_poi_tips_detail_and_traffic_are_normalized(self):
        client = AmapWebServiceClient("test-key", city="测试市")
        client._request_json = AsyncMock(
            side_effect=[
                {
                    "status": "1",
                    "pois": [
                        {
                            "id": "poi-1",
                            "name": "测试书店",
                            "address": "测试路1号",
                            "type": "购物服务;文化用品店;书店",
                            "location": "113.120000,23.020000",
                            "distance": "350",
                            "business": {
                                "tel": "0000-00000000",
                                "rating": "4.6",
                                "cost": "38",
                                "opentime_week": "10:00-22:00",
                            },
                        }
                    ],
                },
                {
                    "status": "1",
                    "tips": [
                        {
                            "id": "poi-1",
                            "name": "测试书店",
                            "address": "测试路1号",
                            "district": "测试区",
                            "location": "113.120000,23.020000",
                            "adcode": "440600",
                        }
                    ],
                },
                {
                    "status": "1",
                    "pois": [
                        {
                            "id": "poi-1",
                            "name": "测试书店",
                            "address": "测试路1号",
                            "location": "113.120000,23.020000",
                            "photos": [{"url": "https://example.com/place.jpg"}],
                        }
                    ],
                },
                {
                    "status": "1",
                    "trafficinfo": {
                        "description": "周边道路通行正常",
                        "evaluation": {
                            "status": "2",
                            "description": "基本畅通",
                        },
                    },
                },
            ]
        )

        places = await client.search_places("书店", center=(23.0, 113.0), limit=3)
        tips = await client.input_tips("测试书店")
        detail = await client.place_detail("poi-1")
        traffic = await client.traffic_status((23.0, 113.0))

        self.assertEqual(places[0]["poi_id"], "poi-1")
        self.assertEqual(places[0]["distance_meters"], 350)
        self.assertEqual(places[0]["rating"], 4.6)
        self.assertEqual(tips[0]["coordinate"], (23.02, 113.12))
        self.assertEqual(detail["photos"], ["https://example.com/place.jpg"])
        self.assertEqual(traffic["evaluation"], "基本畅通")

    async def test_tencent_map_responses_are_normalized(self):
        client = TencentMapWebServiceClient("test-key", city="测试市")
        client._request_json = AsyncMock(
            side_effect=[
                {
                    "status": 0,
                    "result": {
                        "title": "测试地点",
                        "location": {"lat": 23.123456, "lng": 113.123456},
                        "address_components": {
                            "nation": "中国",
                            "province": "测试省",
                            "city": "测试市",
                        },
                        "ad_info": {"city_code": "0001", "adcode": "440600"},
                    },
                },
                {
                    "status": 0,
                    "result": {"routes": [{"distance": 1280, "duration": 18}]},
                },
                {
                    "status": 0,
                    "data": [
                        {
                            "id": "poi-1",
                            "title": "测试书店",
                            "address": "测试路1号",
                            "category": "购物服务:书店",
                            "location": {"lat": 23.02, "lng": 113.12},
                            "_distance": 350,
                            "ad_info": {
                                "province": "测试省",
                                "city": "测试市",
                                "district": "测试区",
                                "adcode": "440600",
                            },
                        }
                    ],
                },
                {
                    "status": 0,
                    "data": [
                        {
                            "id": "poi-1",
                            "title": "测试书店",
                            "address": "测试路1号",
                            "location": {"lat": 23.02, "lng": 113.12},
                            "province": "测试省",
                            "city": "测试市",
                            "district": "测试区",
                            "adcode": "440600",
                        }
                    ],
                },
                {
                    "status": 0,
                    "data": [
                        {
                            "id": "poi-1",
                            "title": "测试书店",
                            "address": "测试路1号",
                            "location": {"lat": 23.02, "lng": 113.12},
                        }
                    ],
                },
            ]
        )

        place = await client.geocode("测试地点", city_hint="")
        route = await client.route(
            (23.123456, 113.123456),
            (23.124456, 113.133456),
            "driving",
        )
        places = await client.search_places("书店", center=(23.0, 113.0), limit=3)
        tips = await client.input_tips("测试书店")
        detail = await client.place_detail("poi-1")

        self.assertEqual(place["citycode"], "0001")
        self.assertEqual(route["provider"], "tencent")
        self.assertEqual(route["duration_seconds"], 1080)
        self.assertEqual(places[0]["distance_meters"], 350)
        self.assertEqual(tips[0]["city"], "测试市")
        self.assertEqual(detail["poi_id"], "poi-1")
        traffic = await client.traffic_status((23.0, 113.0))
        self.assertFalse(traffic["supported"])
        self.assertEqual(traffic["provider"], "tencent")

    async def test_baidu_map_responses_use_unified_coordinates(self):
        client = BaiduMapWebServiceClient("test-key", city="测试市")
        bd_coordinate = (23.129, 113.134)
        expected_gcj = bd09_to_gcj02(bd_coordinate)
        client._request_json = AsyncMock(
            side_effect=[
                {
                    "status": 0,
                    "result": {
                        "location": {
                            "lat": bd_coordinate[0],
                            "lng": bd_coordinate[1],
                        }
                    },
                },
                {
                    "status": 0,
                    "result": {
                        "formatted_address": "测试省测试市测试地点",
                        "addressComponent": {
                            "country": "中国",
                            "province": "测试省",
                            "city": "测试市",
                            "adcode": "440600",
                        },
                    },
                },
                {
                    "status": 0,
                    "result": {"routes": [{"distance": 1280, "duration": 960}]},
                },
                {
                    "status": 0,
                    "results": [
                        {
                            "uid": "poi-1",
                            "name": "测试书店",
                            "address": "测试路1号",
                            "location": {
                                "lat": bd_coordinate[0],
                                "lng": bd_coordinate[1],
                            },
                            "detail_info": {
                                "tag": "购物;书店",
                                "distance": 350,
                                "overall_rating": 4.6,
                            },
                        }
                    ],
                },
                {
                    "status": 0,
                    "result": [
                        {
                            "uid": "poi-1",
                            "name": "测试书店",
                            "location": {
                                "lat": bd_coordinate[0],
                                "lng": bd_coordinate[1],
                            },
                        }
                    ],
                },
                {
                    "status": 0,
                    "result": {
                        "uid": "poi-1",
                        "name": "测试书店",
                        "location": {
                            "lat": bd_coordinate[0],
                            "lng": bd_coordinate[1],
                        },
                        "detail_info": {
                            "photo_list": [{"photo": "https://example.com/place.jpg"}]
                        },
                    },
                },
            ]
        )

        place = await client.geocode("测试地点", city_hint="")
        route = await client.route(
            expected_gcj,
            (expected_gcj[0] + 0.01, expected_gcj[1] + 0.01),
            "driving",
        )
        places = await client.search_places("书店", center=expected_gcj, limit=3)
        tips = await client.input_tips("测试书店")
        detail = await client.place_detail("poi-1")

        self.assertAlmostEqual(place["latitude"], expected_gcj[0], places=7)
        self.assertAlmostEqual(place["longitude"], expected_gcj[1], places=7)
        self.assertEqual(route["provider"], "baidu")
        self.assertEqual(route["duration_seconds"], 960)
        route_params = client._request_json.await_args_list[2].args[1]
        self.assertEqual(route_params["coord_type"], "bd09ll")
        self.assertEqual(places[0]["distance_meters"], 350)
        self.assertAlmostEqual(places[0]["coordinate"][0], expected_gcj[0], places=7)
        self.assertEqual(tips[0]["poi_id"], "poi-1")
        self.assertEqual(detail["photos"], ["https://example.com/place.jpg"])
        traffic = await client.traffic_status(expected_gcj)
        self.assertFalse(traffic["supported"])
        self.assertEqual(traffic["provider"], "baidu")

    def test_baidu_coordinate_conversion_round_trips(self):
        coordinate = (23.123456, 113.123456)

        restored = bd09_to_gcj02(gcj02_to_bd09(coordinate))

        self.assertAlmostEqual(restored[0], coordinate[0], places=5)
        self.assertAlmostEqual(restored[1], coordinate[1], places=5)

    async def test_natural_language_place_tools_hide_coordinates(self):
        service = LifeDomainService(
            LifeDomainSettings(
                home_address="测试省测试市测试区测试路1号",
                amap_api_key="test-key",
            ),
            self.archive,
        )
        service.resolve_home_location = AsyncMock(
            return_value={"city": "测试市", "coordinate": (23.0, 113.0)}
        )
        service._resolve_tool_place = AsyncMock(
            return_value={"name": "测试中心", "coordinate": (23.0, 113.0)}
        )
        service._map.search_places = AsyncMock(
            return_value=[
                {
                    "poi_id": "poi-1",
                    "name": "测试咖啡店",
                    "address": "测试路2号",
                    "category": "餐饮服务;咖啡厅",
                    "distance_meters": 420,
                    "rating": 4.5,
                    "coordinate": (23.01, 113.01),
                }
            ]
        )
        service._map.place_detail = AsyncMock(
            return_value={
                "poi_id": "poi-1",
                "name": "测试咖啡店",
                "address": "测试路2号",
                "coordinate": (23.01, 113.01),
                "photos": ["https://example.com/coffee.jpg"],
            }
        )

        search = await service.tool_place_search("安静咖啡店", near="测试中心")
        detail = await service.tool_place_detail("poi-1")

        self.assertTrue(search["ok"])
        self.assertNotIn("coordinate", search["places"][0])
        self.assertNotIn("coordinate", detail["place"])
        self.assertEqual(detail["place"]["photos"], ["https://example.com/coffee.jpg"])

    async def test_natural_language_route_tool_compares_modes_and_adds_traffic(self):
        service = LifeDomainService(
            LifeDomainSettings(
                home_address="测试省测试市测试区测试路1号",
                amap_api_key="test-key",
            ),
            self.archive,
        )
        service.resolve_home_location = AsyncMock(
            return_value={"city": "测试市", "coordinate": (23.0, 113.0)}
        )
        service._resolve_tool_place = AsyncMock(
            side_effect=[
                {"name": "测试起点", "coordinate": (23.0, 113.0)},
                {"name": "测试终点", "coordinate": (23.1, 113.1)},
            ]
        )

        async def estimate(_origin, _destination, mode):
            duration = {
                "walking": 3600,
                "cycling": 1200,
                "driving": 900,
                "transit": 1500,
            }[mode]
            return {
                "distance_meters": 5000,
                "duration_seconds": duration,
                "provider": "amap",
                "confidence": 0.95,
            }

        service.estimate_route = AsyncMock(side_effect=estimate)
        service._map.traffic_status = AsyncMock(
            return_value={"description": "通行正常", "evaluation": "畅通"}
        )

        result = await service.tool_route_plan("测试起点", "测试终点", mode="compare")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["routes"]), 4)
        self.assertEqual(result["recommended"]["mode"], "driving")
        self.assertEqual(result["traffic"]["evaluation"], "畅通")

    async def test_natural_language_outing_tool_builds_ordered_stops(self):
        service = LifeDomainService(
            LifeDomainSettings(
                home_address="测试省测试市测试区测试路1号",
                amap_api_key="test-key",
            ),
            self.archive,
        )
        service.resolve_home_location = AsyncMock(
            return_value={"city": "测试市", "coordinate": (23.0, 113.0)}
        )
        service._resolve_tool_place = AsyncMock(
            return_value={"name": "测试起点", "coordinate": (23.0, 113.0)}
        )
        service._map.search_places = AsyncMock(
            side_effect=[
                [
                    {
                        "poi_id": "poi-book",
                        "name": "测试书店",
                        "address": "测试路1号",
                        "coordinate": (23.01, 113.01),
                        "adcode": "440600",
                    }
                ],
                [
                    {
                        "poi_id": "poi-dessert",
                        "name": "测试糖水店",
                        "address": "测试路2号",
                        "coordinate": (23.02, 113.02),
                        "adcode": "440600",
                    }
                ],
            ]
        )
        service.estimate_route = AsyncMock(
            side_effect=[
                {
                    "distance_meters": 800,
                    "duration_seconds": 600,
                    "provider": "amap",
                    "confidence": 0.95,
                },
                {
                    "distance_meters": 900,
                    "duration_seconds": 720,
                    "provider": "amap",
                    "confidence": 0.95,
                },
            ]
        )

        result = await service.tool_outing_plan(
            "逛书店再吃糖水",
            ["独立书店", "广式糖水"],
            start="测试起点",
            duration_minutes=120,
        )

        self.assertTrue(result["ok"])
        self.assertEqual([item["stop"] for item in result["stops"]], [1, 2])
        self.assertEqual(result["travel_minutes"], 22)
        self.assertNotIn("coordinate", result["stops"][0]["place"])

    async def test_domain_service_uses_amap_geocode_and_route_then_caches(self):
        settings = LifeDomainSettings(
            home_address="测试省测试市测试区测试路1号",
            amap_api_key="test-key",
        )
        service = LifeDomainService(settings, self.archive)
        self.assertEqual(service._map.city, "")
        service._map.geocode = AsyncMock(
            side_effect=[
                {
                    "latitude": 23.01,
                    "longitude": 113.10,
                    "city": "测试市",
                    "citycode": "0001",
                },
                {
                    "latitude": 23.02,
                    "longitude": 113.11,
                    "citycode": "0000",
                },
                {
                    "latitude": 23.03,
                    "longitude": 113.13,
                    "citycode": "0000",
                },
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 2500,
                "duration_seconds": 1200,
                "provider": "amap",
                "confidence": 0.95,
            }
        )

        first = await service.estimate_route("地点甲", "地点乙", "walking")
        second = await service.estimate_route("地点甲", "地点乙", "walking")

        self.assertEqual(first["provider"], "amap")
        self.assertEqual(second["provider"], "amap")
        self.assertEqual(service.home_city, "测试市")
        self.assertEqual(service._map.route.await_count, 1)
        places = await self.archive.get_recent_places(0)
        self.assertEqual(
            {item.coordinate_source for item in places},
            {"amap_home_address", "amap_geocode"},
        )

    async def test_home_address_defines_weather_city_and_invalidates_old_routes(self):
        await self.archive.update_place_coordinates(
            "家",
            22.0,
            112.0,
            source="amap_home_address",
            updated_at="2026-08-02 10:00:00",
        )
        await self.archive.upsert_route(
            {
                "origin_name": "家",
                "destination_name": "测试地点",
                "travel_mode": "walking",
                "distance_meters": 100,
                "duration_seconds": 80,
                "provider": "amap",
                "confidence": 0.95,
                "fetched_at": "2026-08-02 10:00:00",
                "expires_at": "2099-08-02 10:00:00",
            }
        )
        service = LifeDomainService(
            LifeDomainSettings(
                home_address="测试省测试市测试区测试路1号",
                amap_api_key="test-key",
            ),
            self.archive,
        )
        service._map.geocode = AsyncMock(
            return_value={
                "latitude": 23.01,
                "longitude": 113.10,
                "formatted_address": "测试省测试市测试区测试路1号",
                "province": "测试省",
                "city": "测试市",
                "citycode": "0001",
            }
        )

        first_city = await service.resolve_weather_city()
        second_city = await service.resolve_weather_city()

        self.assertEqual(first_city, "测试市")
        self.assertEqual(second_city, "测试市")
        self.assertEqual(service._map.city, "测试市")
        self.assertEqual(service._coordinates["家"], (23.01, 113.10))
        self.assertEqual(service._map.geocode.await_count, 1)
        self.assertEqual(
            service._map.geocode.await_args.kwargs,
            {"city_hint": ""},
        )
        self.assertIsNone(await self.archive.get_route("家", "测试地点", "walking"))

    async def test_route_cache_preserves_map_transit_detail(self):
        saved = await self.archive.upsert_route(
            {
                "origin_name": "家",
                "destination_name": "测试广场",
                "travel_mode": "transit",
                "travel_detail": "公交 + 地铁",
                "distance_meters": 5700,
                "duration_seconds": 3300,
                "provider": "amap",
                "confidence": 0.95,
            }
        )

        loaded = await self.archive.get_route("家", "测试广场", "transit")

        self.assertEqual(saved["travel_detail"], "公交 + 地铁")
        self.assertEqual(loaded["travel_detail"], "公交 + 地铁")

    async def test_residence_reset_keeps_history_and_clears_active_location_context(
        self,
    ):
        await self.archive.save_day(
            DayRecord(
                date="2026-08-03",
                weather="旧城市 晴 30°C",
                timeline=[TimelineItem(time="10:00", activity="在旧城市散步")],
            )
        )
        await self.archive.save_week_plan(
            WeekPlanRecord(
                week_id="2026-W31",
                theme="旧城市生活",
                generated=True,
            )
        )
        await self.archive.touch_places(
            "2026-08-03",
            [PlaceRecord(name="旧城市公园", type="park")],
        )
        await self.archive.upsert_route(
            {
                "origin_name": "家",
                "destination_name": "旧城市公园",
                "travel_mode": "walking",
                "distance_meters": 800,
                "duration_seconds": 600,
                "provider": "amap",
                "confidence": 0.95,
            }
        )

        await self.archive.reset_residence_context(
            changed_at="2026-08-04 09:00:00",
            week_id="2026-W31",
        )

        self.assertIsNotNone(await self.archive.get_day("2026-08-03"))
        self.assertEqual(await self.archive.get_recent_places(0), [])
        self.assertIsNone(await self.archive.get_route("家", "旧城市公园", "walking"))
        self.assertIsNone(await self.archive.get_week_plan("2026-W31"))
        self.assertEqual(
            await self.archive.get_residence_context_boundary(),
            "2026-08-04 09:00:00",
        )

    async def test_home_city_does_not_fall_back_without_address(self):
        service = LifeDomainService(
            LifeDomainSettings(amap_api_key="test-key"),
            self.archive,
        )
        service._map.geocode = AsyncMock()

        city = await service.resolve_weather_city()

        self.assertEqual(city, "")
        self.assertFalse(service.map_tools_available())
        service._map.geocode.assert_not_awaited()

    async def test_transit_requires_city(self):
        client = AmapWebServiceClient("test-key")
        client._request_json = AsyncMock()

        route = await client.route((23.0, 113.0), (23.1, 113.1), "transit")

        self.assertIsNone(route)
        client._request_json.assert_not_awaited()

    async def test_unknown_legacy_route_cache_is_replaced(self):
        await self.archive.upsert_route(
            {
                "origin_name": "地点甲",
                "destination_name": "地点乙",
                "travel_mode": "walking",
                "distance_meters": 99,
                "duration_seconds": 99,
                "provider": "legacy",
                "confidence": 1,
                "fetched_at": "2026-08-03 10:00:00",
                "expires_at": "2099-08-03 10:00:00",
            }
        )
        settings = LifeDomainSettings(
            home_address="测试省测试市测试区测试路1号",
            amap_api_key="test-key",
        )
        service = LifeDomainService(settings, self.archive)
        service._map.geocode = AsyncMock(
            side_effect=[
                {
                    "latitude": 23.0,
                    "longitude": 113.0,
                    "city": "测试市",
                    "citycode": "0001",
                },
                {"latitude": 23.0, "longitude": 113.0, "citycode": "0000"},
                {"latitude": 23.01, "longitude": 113.01, "citycode": "0000"},
            ]
        )
        service._map.route = AsyncMock(
            return_value={
                "distance_meters": 1500,
                "duration_seconds": 900,
                "provider": "amap",
                "confidence": 0.95,
            }
        )

        route = await service.estimate_route("地点甲", "地点乙", "walking")

        self.assertEqual(route["provider"], "amap")
        self.assertNotEqual(route["duration_seconds"], 99)

    async def test_historical_manual_coordinate_is_replaced_by_amap(self):
        await self.archive.update_place_coordinates(
            "地点甲",
            1.0,
            2.0,
            source="manual",
            updated_at="2026-08-03 10:00:00",
        )
        service = LifeDomainService(
            LifeDomainSettings(amap_api_key="test-key"),
            self.archive,
        )
        service._map.geocode = AsyncMock(
            return_value={
                "latitude": 23.02,
                "longitude": 113.11,
                "citycode": "0000",
            }
        )

        coordinate = await service._place_coordinate("地点甲")

        self.assertEqual(coordinate, (23.02, 113.11))
        places = await self.archive.get_recent_places(0)
        self.assertEqual(places[0].coordinate_source, "amap_geocode")

    def test_domain_settings_accept_map_provider_configuration(self):
        settings = LifeDomainSettings.from_dict(
            {
                "home_address": "测试省测试市测试区测试路1号",
                "map_provider": "tencent",
                "amap_api_key": "test-key",
                "tencent_map_api_key": "tencent-key",
                "baidu_map_api_key": "baidu-key",
            }
        )

        self.assertEqual(settings.home_address, "测试省测试市测试区测试路1号")
        self.assertEqual(settings.map_provider, "tencent")
        self.assertEqual(settings.amap_api_key, "test-key")
        self.assertEqual(settings.tencent_map_api_key, "tencent-key")
        self.assertEqual(settings.baidu_map_api_key, "baidu-key")

    async def test_domain_service_uses_selected_map_provider(self):
        service = LifeDomainService(
            LifeDomainSettings(
                home_address="测试省测试市测试区测试路1号",
                map_provider="tencent",
                tencent_map_api_key="test-key",
            ),
            self.archive,
        )
        service._map.geocode = AsyncMock(
            return_value={
                "latitude": 23.02,
                "longitude": 113.11,
                "formatted_address": "测试省测试市测试区测试路1号",
                "city": "测试市",
                "adcode": "440600",
            }
        )

        location = await service.resolve_home_location()

        self.assertEqual(service.map_provider, "tencent")
        self.assertEqual(service.map_provider_label, "腾讯地图")
        self.assertEqual(location["city"], "测试市")
        places = await self.archive.get_recent_places(0)
        self.assertEqual(places[0].coordinate_source, "tencent_home_address")

    async def test_v4_database_migrates_without_losing_existing_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/migration.db"
            archive = LifeArchive(path)
            await archive.add_events(
                "2026-08-02",
                [
                    EventRecord(
                        date="2026-08-02",
                        summary="迁移前保留的测试事件",
                    )
                ],
            )
            await archive.aclose()
            connection = sqlite3.connect(path)
            for table in (
                "conversation_action_items",
                "fitness_records",
                "chore_records",
                "chores",
                "meal_records",
                "pantry_movements",
                "pantry_items",
                "recipes",
                "route_cache",
                "activity_sessions",
            ):
                connection.execute(f"DROP TABLE {table}")
            for column in (
                "coordinate_updated_at",
                "coordinate_source",
                "longitude",
                "latitude",
            ):
                connection.execute(f"ALTER TABLE places DROP COLUMN {column}")
            connection.execute(
                "UPDATE meta SET value = '4' WHERE key = 'schema_version'"
            )
            connection.commit()
            connection.close()

            migrated = LifeArchive(path)
            try:
                version = migrated._conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                columns = {
                    row[1]
                    for row in migrated._conn.execute(
                        "PRAGMA table_info(places)"
                    ).fetchall()
                }
                events = await migrated.get_recent_events()
                self.assertEqual(version, str(SCHEMA_VERSION))
                self.assertIn("latitude", columns)
                self.assertEqual(events[0].summary, "迁移前保留的测试事件")
            finally:
                await migrated.aclose()

    async def test_v7_database_restores_activity_terminal_statuses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/migration.db"
            archive = LifeArchive(path)
            day = DayRecord(
                date="2026-08-03",
                timeline=[
                    TimelineItem(
                        time="10:00",
                        activity="过期计划",
                        execution_state="expired",
                    ),
                    TimelineItem(
                        time="11:00",
                        activity="跳过计划",
                        execution_state="skipped",
                    ),
                    TimelineItem(
                        time="12:00",
                        activity="真实失败计划",
                        execution_state="skipped",
                    ),
                ],
                meta={
                    "planned_life_actions": json.dumps(
                        [
                            {
                                "action_id": "legacy-expired",
                                "action_type": "photo",
                                "timeline_index": 0,
                            },
                            {
                                "action_id": "legacy-skipped",
                                "action_type": "rest",
                                "timeline_index": 1,
                            },
                            {
                                "action_id": "legacy-failed",
                                "action_type": "photo",
                                "timeline_index": 2,
                            },
                        ],
                        ensure_ascii=False,
                    )
                },
            )
            await archive.save_day(day, replace=True)
            for index, action_id in enumerate(
                ("legacy-expired", "legacy-skipped", "legacy-failed")
            ):
                await archive.upsert_activity_session(
                    {
                        "action_id": action_id,
                        "date": day.date,
                        "activity_type": "photo" if index != 1 else "rest",
                        "title": f"旧活动 {index}",
                        "status": "failed",
                        "started_at": f"{day.date} 1{index}:00:00",
                        "ended_at": f"{day.date} 23:59:00",
                        "source": "daily_plan",
                        "metadata": {"timeline_index": index},
                    }
                )
            await archive.save_life_action_outcome(
                {
                    "action_id": "legacy-expired",
                    "date": day.date,
                    "action_type": "photo",
                    "status": "expired",
                }
            )
            await archive.save_life_action_receipt(
                {
                    "receipt_id": "receipt:legacy-failed",
                    "action_id": "legacy-failed",
                    "date": day.date,
                    "action_type": "photo",
                    "status": "failed",
                    "source": "test_receipt",
                    "occurred_at": f"{day.date} 12:05:00",
                }
            )
            await archive.aclose()

            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE meta SET value = '7' WHERE key = 'schema_version'"
            )
            connection.commit()
            connection.close()

            migrated = LifeArchive(path)
            try:
                sessions = {
                    item["action_id"]: item["status"]
                    for item in await migrated.get_activity_sessions(limit=0)
                }
                version = migrated._conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                self.assertEqual(version, str(SCHEMA_VERSION))
                self.assertEqual(sessions["legacy-expired"], "expired")
                self.assertEqual(sessions["legacy-skipped"], "skipped")
                self.assertEqual(sessions["legacy-failed"], "failed")
            finally:
                await migrated.aclose()


if __name__ == "__main__":
    unittest.main()
