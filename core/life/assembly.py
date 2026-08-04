import json

from ..models import (
    LIFE_ACTION_TYPES,
    DayRecord,
    EventRecord,
    LifeActionIntent,
    LifeState,
    PlaceRecord,
    TimelineItem,
    WeatherInfo,
)
from .appearance import strip_hair_from_outfit
from .condition import normalize_state, state_log_entry
from .surroundings import normalize_event_items
from .wardrobe import (
    normalize_outfit_decision,
    normalize_outfit_scene_category,
    resolve_outfit_style_pool,
)


class DailyAssemblyMixin:
    def _day_from_generation(
        self,
        result: dict,
        *,
        date_str: str,
        period: str,
        weather_str: str,
        weather_info: dict,
        meta: dict[str, str],
        memo: str,
    ) -> DayRecord:
        timeline = [
            TimelineItem.from_value(item) for item in result.get("timeline", [])
        ]
        events = [
            event
            for event in (
                EventRecord.from_value(item, date=date_str, source="daily")
                for item in normalize_event_items(
                    date_str, result.get("new_events", []), source="daily"
                )
            )
            if event is not None
        ]
        raw_places = result.get("places", [])
        raw_place_records = [
            place
            for place in (
                PlaceRecord.from_value(item)
                for item in (raw_places if isinstance(raw_places, list) else [])
            )
            if place is not None
        ]
        places = self._filter_places_by_day_evidence(
            raw_place_records, timeline, events
        )
        state = LifeState.from_value(
            normalize_state(result.get("state"), source="daily")
        )
        outfit = strip_hair_from_outfit(
            result.get("outfit", ""),
            meta.get("hair_style", ""),
            meta.get("hair", ""),
        )
        planned_actions = []
        raw_actions = result.get("planned_actions")
        for raw_action in raw_actions if isinstance(raw_actions, list) else []:
            action = LifeActionIntent.from_value(raw_action)
            if (
                action.action_id
                and action.action_type in LIFE_ACTION_TYPES
                and action.timeline_index is not None
                and 0 <= action.timeline_index < len(timeline)
            ):
                planned_actions.append(action.as_dict())
        if planned_actions:
            meta["planned_life_actions"] = json.dumps(
                planned_actions,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return DayRecord(
            date=date_str,
            state=state,
            outfit=outfit,
            timeline=timeline,
            places=places,
            new_events=events,
            weather=weather_str,
            weather_info=WeatherInfo.from_value(
                {
                    "raw": weather_str,
                    "temp": weather_info.get("temp"),
                    "temp_desc": weather_info.get("temp_desc", ""),
                    "condition": weather_info.get("condition", ""),
                    "outfit_hint": weather_info.get("outfit_hint", ""),
                    "activity_hint": weather_info.get("activity_hint", ""),
                    "is_hot": weather_info.get("is_hot"),
                    "is_warm": weather_info.get("is_warm"),
                    "is_cool": weather_info.get("is_cool"),
                    "is_cold": weather_info.get("is_cold"),
                    "is_rainy": weather_info.get("is_rainy"),
                    "is_sunny": weather_info.get("is_sunny"),
                    "is_cloudy": weather_info.get("is_cloudy"),
                    "is_foggy": weather_info.get("is_foggy"),
                }
            ),
            time_period=period,
            meta=meta,
            outfit_history={period: outfit},
            memo=memo,
            state_log=[state_log_entry(state)],
        )

    @staticmethod
    def _filter_places_by_day_evidence(
        places: list[PlaceRecord],
        timeline: list[TimelineItem],
        events: list[EventRecord],
    ) -> list[PlaceRecord]:
        evidence_parts: list[str] = []
        for item in timeline:
            evidence_parts.extend([item.activity, item.status, item.place])
        for event in events:
            evidence_parts.extend([event.summary, event.place])

        evidence = "\n".join(text for text in evidence_parts if text)
        if not evidence:
            return []

        filtered: list[PlaceRecord] = []
        seen: set[str] = set()
        for place in places:
            name = place.name.strip()
            if not name or name in seen or name not in evidence:
                continue
            seen.add(name)
            filtered.append(place)
        return filtered

    @staticmethod
    def _meta_text(value: object, limit: int = 80) -> str:
        text = str(value or "").strip()
        return " ".join(text.split())[:limit]

    @classmethod
    def _mood_color_text(cls, value: object) -> str:
        text = cls._meta_text(value)
        return text if "·" in text else ""

    def _meta_from_generation(self, result: dict) -> dict[str, str]:
        decision = (
            result.get("life_decision")
            if isinstance(result.get("life_decision"), dict)
            else {}
        )
        sleep = decision.get("sleep") if isinstance(decision.get("sleep"), dict) else {}
        outfit = (
            decision.get("outfit") if isinstance(decision.get("outfit"), dict) else {}
        )
        plan = (
            decision.get("day_plan")
            if isinstance(decision.get("day_plan"), dict)
            else {}
        )
        summary = (
            result.get("decision_summary")
            if isinstance(result.get("decision_summary"), dict)
            else {}
        )
        decision_value = outfit.get("decision")
        scene_value = outfit.get("scene_category")
        style_pool_value = outfit.get("style_pool")
        outfit_decision = (
            normalize_outfit_decision(decision_value) if decision_value else ""
        )
        outfit_scene_category = (
            normalize_outfit_scene_category(scene_value) if scene_value else ""
        )
        outfit_style_pool = (
            resolve_outfit_style_pool(
                outfit_scene_category,
                decision=outfit_decision,
                requested=style_pool_value,
            )
            if outfit_decision or outfit_scene_category or style_pool_value
            else ""
        )

        pairs = {
            "theme": decision.get("theme") or plan.get("theme"),
            "mood": self._mood_color_text(decision.get("mood")),
            "style": outfit.get("style"),
            "hair_style": outfit.get("hair_style"),
            "hair": outfit.get("hair"),
            "life_mode": decision.get("life_mode") or plan.get("life_mode"),
            "plan_outfit_decision": outfit_decision,
            "outfit_decision": outfit_decision,
            "outfit_scene_category": outfit_scene_category,
            "outfit_style_pool": outfit_style_pool,
            "outfit_reason": self._localize_outfit_reason(outfit.get("reason")),
            "sleep_mode": sleep.get("mode"),
            "schedule_type": plan.get("schedule_type") or plan.get("type"),
            "schedule_intent": plan.get("schedule_intent"),
            "energy_bias": plan.get("energy_bias"),
            "social_bias": plan.get("social_bias"),
            "decision_summary": summary.get("decision"),
            "decision_reason": summary.get("reason"),
        }
        meta = {}
        for key, value in pairs.items():
            text = self._meta_text(value)
            if text:
                meta[key] = text
        location_audit = result.get("location_audit")
        if isinstance(location_audit, dict):
            meta["location_audit_provider"] = self._meta_text(
                location_audit.get("map_provider")
            )
            meta["location_audit_city"] = self._meta_text(
                location_audit.get("home_city")
            )
            meta["location_audit_places"] = str(
                max(0, int(location_audit.get("verified_places") or 0))
            )
            meta["location_audit_routes"] = str(
                max(0, int(location_audit.get("checked_routes") or 0))
            )
        return meta


__all__ = ["DailyAssemblyMixin"]
