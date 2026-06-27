import copy
import datetime
import inspect
import json
from pathlib import Path

from ..clock import now as life_now
from ..life.tools import (
    get_current_timeline_status,
    get_week_id,
    resolve_daily_hint,
    resolve_daily_suggested,
)


CONF_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "_conf_schema.json"


class PageViewMixin:
    async def _build_page_config(self, saved: bool = False) -> dict:
        return {
            "schema": self._page_config_schema(),
            "config": self._page_current_config(),
            "providers": await self._page_provider_options(),
            "saved": saved,
        }

    @staticmethod
    def _page_config_schema() -> dict:
        with CONF_SCHEMA_PATH.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("配置结构格式错误")
        return data

    def _page_current_config(self) -> dict:
        raw_config = self.runtime.raw_config
        if not isinstance(raw_config, dict):
            raise ValueError("当前配置对象不支持面板读取")
        return copy.deepcopy(dict(raw_config))

    async def _page_provider_options(self) -> list[dict]:
        get_all_providers = getattr(self.context, "get_all_providers", None)
        if not callable(get_all_providers):
            return []
        providers = get_all_providers()
        if inspect.isawaitable(providers):
            providers = await providers
        items = []
        for provider in providers or []:
            meta = provider.meta() if callable(getattr(provider, "meta", None)) else None
            provider_id = str(getattr(meta, "id", "") or "").strip()
            if not provider_id:
                continue
            label_parts = [provider_id]
            model = str(getattr(meta, "model", "") or "").strip()
            provider_type = str(getattr(meta, "type", "") or "").strip()
            if model:
                label_parts.append(model)
            if provider_type:
                label_parts.append(provider_type)
            items.append({"id": provider_id, "label": " · ".join(label_parts)})
        return items

    async def _build_page_status(self) -> dict:
        now = life_now()
        target_date, extended_night = await self.runtime.resolve_injection_target(now)
        data = await self.runtime.archive.get_day(target_date)
        week_plan = await self.runtime.composer._get_week_plan()
        relationship_records = await self.runtime.archive.get_recent_relationships(8)
        relationships = await self._page_relationships(relationship_records)
        places = await self.runtime.archive.get_recent_places(12)
        events = await self.runtime.archive.get_recent_events(12)
        summaries = await self.runtime.archive.get_recent_chat_summaries(8)
        group_environments = await self._page_group_environments(
            await self.runtime.archive.get_recent_group_environments(8)
        )
        message_visibility = await self.runtime.archive.get_message_visibility_records(20)
        action_decisions = self._page_action_decisions(await self._page_raw_action_decisions(), limit=8)
        reviews = await self.runtime.archive.get_recent_daily_reviews(7)
        preferences = await self.runtime.archive.get_preferences(20)
        life_events = await self.runtime.archive.get_life_events(limit=20)
        episodes = await self.runtime.archive.get_life_episodes(limit=20)
        evidence = await self._page_memory_evidence(
            await self.runtime.archive.get_memory_evidence(limit=30),
            relationship_records,
        )
        feedback = self._page_feedback_records(await self.runtime.archive.get_behavior_feedback(limit=20))
        expression_profiles = await self.runtime.archive.get_expression_profiles(limit=20)
        behavior_patterns = await self.runtime.archive.get_behavior_patterns(limit=20)
        mid_summaries = await self.runtime.archive.get_session_mid_summaries(limit=20)
        temporary_expression_states = await self.runtime.archive.get_temporary_expression_states(limit=20)
        focus_targets = await self.runtime.archive.get_focus_targets(limit=20, enabled_only=False, include_expired=True)
        life_terms = await self.runtime.archive.get_life_terms(limit=20)
        memory_boundaries = await self.runtime.archive.get_memory_boundaries(limit=20, enabled_only=False)
        health = await self.runtime.archive.get_life_health_report(self.runtime.config.storage)
        templates = await self._page_templates()
        catalog = await self._page_catalog()

        return {
            "now": now.strftime("%Y-%m-%d %H:%M:%S"),
            "status_version": getattr(self.runtime, "page_status_version", 0),
            "target_date": target_date,
            "extended_night": extended_night,
            "config": {
                "schedule_time": self.runtime.config.schedule_time,
                "week_plan_time": self.runtime.config.week_plan_time,
                "state_enabled": self.runtime.config.state.enabled,
            },
            "day": self._page_day(data, now, extended_night) if data else None,
            "week_plan": self._page_week_plan(week_plan),
            "world": {
                "relationships": relationships,
                "places": [item.as_dict() for item in places],
                "events": [item.as_dict() for item in events],
                "summaries": [item.as_dict() for item in summaries],
                "group_environments": group_environments,
                "message_visibility": [item.as_dict() for item in message_visibility],
                "action_decisions": [item.as_dict() for item in action_decisions],
            },
            "lifecycle": {
                "reviews": [item.as_dict() for item in reviews],
                "preferences": [item.as_dict() for item in preferences],
                "life_events": [item.as_dict() for item in life_events],
            },
            "experience": {
                "episodes": [item.as_dict() for item in episodes],
                "evidence": evidence,
                "feedback": [item.as_dict() for item in feedback],
                "expression_profiles": [item.as_dict() for item in expression_profiles],
                "behavior_patterns": [item.as_dict() for item in behavior_patterns],
                "mid_summaries": [item.as_dict() for item in mid_summaries],
                "temporary_expression_states": [item.as_dict() for item in temporary_expression_states],
                "focus_targets": [item.as_dict() for item in focus_targets],
                "terms": [item.as_dict() for item in life_terms],
                "boundaries": [item.as_dict() for item in memory_boundaries],
                "health": health,
            },
            "templates": templates,
            "catalog": catalog,
        }

    @staticmethod
    def _page_unique_records(records: list, keys: tuple[str, ...]) -> list:
        result = []
        seen = set()
        for item in records:
            data = item.as_dict() if hasattr(item, "as_dict") else dict(item or {})
            marker = tuple(str(data.get(key) or "").strip() for key in keys)
            if marker in seen:
                continue
            seen.add(marker)
            result.append(item)
        return result

    @staticmethod
    def _page_feedback_records(records: list) -> list:
        result = []
        seen = set()
        for item in records:
            data = item.as_dict() if hasattr(item, "as_dict") else dict(item or {})
            marker = (
                str(data.get("scene") or "").strip(),
                str(data.get("action") or "").strip(),
                str(data.get("feedback") or "").strip(),
                str(data.get("result") or "").strip(),
            )
            if marker in seen:
                continue
            seen.add(marker)
            result.append(item)
        return result

    async def _page_group_environments(self, environments: list) -> list[dict]:
        resolver = getattr(self.runtime, "contact_resolver", None)
        resolve_group_name = getattr(resolver, "resolve_group_name", None)
        result = []
        for item in environments:
            data = item.as_dict()
            group_id = str(data.get("group_id") or "").strip()
            group_name = str(data.get("group_name") or "").strip()
            if group_id and (not group_name or group_name == group_id) and callable(resolve_group_name):
                data["group_name"] = await resolve_group_name(group_id, target_umo=str(data.get("session_id") or "")) or group_name
            result.append(data)
        return result

    @staticmethod
    def _page_action_decisions(decisions: list, limit: int = 8) -> list:
        items = []
        for item in decisions:
            reason = str(getattr(item, "reason", "") or "").strip()
            if not reason:
                continue
            items.append(item)
            if limit > 0 and len(items) >= limit:
                break
        return items

    async def _page_raw_action_decisions(self) -> list:
        return await self.runtime.archive.get_action_decision_records(80)

    async def _page_relationships(self, relationships: list) -> list[dict]:
        resolver = getattr(self.runtime, "contact_resolver", None)
        result = []
        for item in relationships:
            data = item.as_dict()
            display_name = await self._resolve_relationship_display_name(data, resolver)
            if display_name:
                data["display_name"] = display_name
            result.append(data)
        return result

    async def _resolve_relationship_display_name(self, data: dict, resolver) -> str:
        current_name = str(data.get("name") or "").strip()
        current_alias = str(data.get("alias") or "").strip()
        if current_alias and not self._is_generic_page_name(current_alias):
            return current_alias
        if current_name and not self._is_generic_page_name(current_name):
            return current_name
        if not resolver:
            return ""

        candidates = []
        for key in ("user_id", "id"):
            value = str(data.get(key) or "").strip()
            if value:
                candidates.append(value)
        for contact in data.get("contacts") or []:
            if not isinstance(contact, dict):
                continue
            for key in ("user_id", "target_scope", "profile_id"):
                value = str(contact.get(key) or "").strip()
                if value:
                    candidates.append(value)

        for candidate in dict.fromkeys(candidates):
            alias = ""
            get_alias = getattr(resolver, "get_relationship_alias", None)
            if callable(get_alias):
                alias = str(get_alias(candidate) or "").strip()
            if alias and not self._is_generic_page_name(alias):
                return alias
            get_nickname = getattr(resolver, "get_onebot_nickname", None)
            if callable(get_nickname):
                nickname = await get_nickname(candidate)
                if nickname and not self._is_generic_page_name(nickname):
                    return nickname
        return ""

    @staticmethod
    def _is_generic_page_name(name: str) -> bool:
        return str(name or "").strip() in {"用户", "对方", "未知", "未知用户"}

    async def _page_memory_evidence(self, evidence: list, relationships: list) -> list[dict]:
        relationship_names = {
            str(item.id or "").strip(): str(item.name or item.alias or item.id or "").strip()
            for item in relationships
            if str(item.id or "").strip()
        }
        result = []
        get_relationship = getattr(self.runtime.archive, "get_relationship", None)
        for item in evidence:
            data = item.as_dict()
            target_type = str(data.get("target_type") or "").strip().lower()
            target_id = str(data.get("target_id") or "").strip()
            if target_type == "relationship" and target_id:
                target_label = relationship_names.get(target_id, "")
                if not target_label and callable(get_relationship):
                    relationship = await get_relationship(target_id)
                    if relationship:
                        target_label = str(relationship.name or relationship.alias or relationship.id or "").strip()
                        relationship_names[target_id] = target_label
                if target_label and target_label != target_id:
                    data["target_label"] = target_label
            result.append(data)
        return result

    def _page_day(self, data, now: datetime.datetime, extended_night: bool) -> dict:
        current, next_item = get_current_timeline_status(data.timeline, now, data.date)
        if extended_night:
            current = None
        return {
            "date": data.date,
            "outfit": data.outfit,
            "weather": data.weather,
            "weather_info": data.weather_info.as_dict(),
            "memo": data.memo,
            "meta": dict(data.meta),
            "state": data.state.as_dict() if data.state else {},
            "timeline": [item.as_dict() for item in data.timeline],
            "places": [item.as_dict() for item in data.places],
            "new_events": [item.as_dict() for item in data.new_events],
            "outfit_history": dict(data.outfit_history),
            "state_log": list(data.state_log),
            "current": current.as_dict() if current else None,
            "next": next_item.as_dict() if next_item else None,
            "extended_night": extended_night,
        }

    def _page_week_plan(self, plan) -> dict:
        if not plan:
            return {}
        now = life_now()
        data = plan.as_dict()
        data["week_id"] = plan.week_id or get_week_id()
        data["today_hint"] = resolve_daily_hint(plan, now, default="")
        data["today_suggested"] = resolve_daily_suggested(plan, now, default="")
        return data
