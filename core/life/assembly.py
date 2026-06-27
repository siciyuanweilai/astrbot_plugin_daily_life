from ..models import (
    DayRecord,
    EventRecord,
    LifeState,
    PlaceRecord,
    TimelineItem,
    WeatherInfo,
)
from .condition import normalize_state, state_log_entry
from .surroundings import normalize_event_items


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
        raw_places = result.get("places", [])
        places = [
            place
            for place in (PlaceRecord.from_value(item) for item in (raw_places if isinstance(raw_places, list) else []))
            if place is not None
        ]
        events = [
            event
            for event in (
                EventRecord.from_value(item, date=date_str, source="daily")
                for item in normalize_event_items(date_str, result.get("new_events", []), source="daily")
            )
            if event is not None
        ]
        state = LifeState.from_value(normalize_state(result.get("state"), source="daily"))
        outfit = str(result.get("outfit", "")).strip()
        return DayRecord(
            date=date_str,
            state=state,
            outfit=outfit,
            timeline=[TimelineItem.from_value(item) for item in result.get("timeline", [])],
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
    def _meta_text(value: object, limit: int = 80) -> str:
        text = str(value or "").strip()
        return " ".join(text.split())[:limit]


    @classmethod
    def _mood_color_text(cls, value: object) -> str:
        text = cls._meta_text(value)
        return text if "·" in text else ""


    def _meta_from_generation(self, result: dict) -> dict[str, str]:
        decision = result.get("life_decision") if isinstance(result.get("life_decision"), dict) else {}
        sleep = decision.get("sleep") if isinstance(decision.get("sleep"), dict) else {}
        outfit = decision.get("outfit") if isinstance(decision.get("outfit"), dict) else {}
        plan = decision.get("day_plan") if isinstance(decision.get("day_plan"), dict) else {}
        state = result.get("state") if isinstance(result.get("state"), dict) else {}

        pairs = {
            "theme": decision.get("theme") or plan.get("theme"),
            "mood": self._mood_color_text(decision.get("mood")),
            "style": outfit.get("style"),
            "hair": outfit.get("hair"),
            "life_mode": decision.get("life_mode") or plan.get("life_mode"),
            "plan_outfit_decision": outfit.get("decision"),
            "outfit_decision": outfit.get("decision"),
            "outfit_scene_category": outfit.get("scene_category"),
            "outfit_style_pool": outfit.get("style_pool"),
            "outfit_reason": self._localize_outfit_reason(outfit.get("reason")),
            "sleep_mode": sleep.get("mode"),
            "schedule_type": plan.get("schedule_type") or plan.get("type"),
            "schedule_intent": plan.get("schedule_intent"),
            "energy_bias": plan.get("energy_bias"),
            "social_bias": plan.get("social_bias"),
        }
        meta = {}
        for key, value in pairs.items():
            text = self._meta_text(value)
            if text:
                meta[key] = text
        return meta



__all__ = ["DailyAssemblyMixin"]
