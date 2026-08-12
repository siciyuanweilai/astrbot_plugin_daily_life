import asyncio
import copy
import datetime
import inspect
import json
import re
from pathlib import Path

from ..clock import now as life_now
from ..life.tools import (
    get_current_timeline_status,
    get_week_id,
    resolve_daily_hint,
    resolve_daily_suggested,
)
from ..models.coerce import compact_explanation_text
from ..models.coerce import compact_text as _compact_text

CONF_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "_conf_schema.json"
PAGE_WORLD_RECORD_LIMIT = 20
PAGE_MEMO_CAROUSEL_LIMIT = 8


class PageViewMixin:
    async def _page_archive_records(self, method_name: str, **kwargs):
        """读取可选的结构化面板记录，兼容旧测试夹具和旧数据库。"""
        getter = getattr(self.runtime.archive, method_name, None)
        if not callable(getter):
            return []
        try:
            result = await getter(**kwargs)
        except (AttributeError, KeyError, TypeError):
            return []
        return list(result or [])

    @staticmethod
    def _page_records_for_date(
        records: list, target_date: str, *date_fields: str
    ) -> list:
        """仅保留属于当前生活日期的过程型面板记录。"""
        date_text = str(target_date or "").strip()
        if not date_text:
            return []
        fields = date_fields or ("date", "created_at")
        result = []
        for item in records:
            data = item.as_dict() if hasattr(item, "as_dict") else dict(item or {})
            for field in fields:
                record_date = str(data.get(field) or "").strip()
                if not record_date:
                    continue
                if record_date[:10] == date_text:
                    result.append(item)
                break
        return result

    async def _build_page_config(self, saved: bool = False) -> dict:
        relationships = await self._page_reference_relationships()
        schema = await self._page_config_schema()
        return {
            "schema": schema,
            "config": self._page_current_config(),
            "providers": await self._page_provider_options(),
            "relationships": relationships,
            "saved": saved,
        }

    async def _page_reference_relationships(self) -> list[dict[str, str]]:
        records = await self.runtime.archive.get_recent_relationships(200)
        items = await self._page_relationships(records)
        result = []
        for item in items:
            profile_id = str(item.get("id") or "").strip()
            if not profile_id:
                continue
            display_name = str(
                item.get("display_name")
                or item.get("subjective_name")
                or item.get("alias")
                or item.get("name")
                or profile_id
            ).strip()
            result.append(
                {"profile_id": profile_id, "display_name": display_name or profile_id}
            )
        return result

    @staticmethod
    def _load_page_config_schema() -> dict:
        with CONF_SCHEMA_PATH.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("配置结构格式错误")
        return data

    @classmethod
    async def _page_config_schema(cls) -> dict:
        return await asyncio.to_thread(cls._load_page_config_schema)

    def _page_current_config(self) -> dict:
        raw_config = self.runtime.raw_config
        if not isinstance(raw_config, dict):
            raise ValueError("当前配置对象不支持面板读取")
        return copy.deepcopy(dict(raw_config))

    async def _page_provider_options(self) -> list[dict]:
        async def load(method_name: str) -> list:
            getter = getattr(self.context, method_name, None)
            if not callable(getter):
                return []
            try:
                providers = getter()
                if inspect.isawaitable(providers):
                    providers = await providers
                return list(providers or [])
            except Exception:
                return []

        chat_providers, embedding_providers = await asyncio.gather(
            load("get_all_providers"),
            load("get_all_embedding_providers"),
        )
        items = []
        seen: set[tuple[str, str]] = set()
        for kind, providers in (
            ("chat", chat_providers),
            ("embedding", embedding_providers),
        ):
            for provider in providers:
                meta = (
                    provider.meta()
                    if callable(getattr(provider, "meta", None))
                    else None
                )
                provider_id = str(getattr(meta, "id", "") or "").strip()
                provider_key = (kind, provider_id)
                if not provider_id or provider_key in seen:
                    continue
                seen.add(provider_key)
                label_parts = [provider_id]
                model = str(getattr(meta, "model", "") or "").strip()
                provider_type = str(getattr(meta, "type", "") or "").strip()
                if model:
                    label_parts.append(model)
                if provider_type:
                    label_parts.append(provider_type)
                items.append(
                    {
                        "id": provider_id,
                        "label": " · ".join(label_parts),
                        "kind": kind,
                    }
                )
        return items

    async def _build_page_status(self) -> dict:
        now = life_now()
        target_date, extended_night = await self.runtime.resolve_injection_target(now)
        data = await self.runtime.archive.get_day(target_date)
        week_plan = await self.runtime.composer._get_week_plan()
        world, world_context = await self._page_world_snapshot(target_date)
        experience = await self._page_experience_snapshot(
            target_date,
            relationship_records=world_context["relationship_records"],
            summaries=world_context["summaries"],
        )
        runtime_status = await self._page_runtime_snapshot(target_date, data, now)
        rhythm = getattr(self.runtime, "rhythm", None)
        return {
            "now": now.strftime("%Y-%m-%d %H:%M:%S"),
            "status_version": getattr(self.runtime, "page_status_version", 0),
            "target_date": target_date,
            "extended_night": extended_night,
            "memo": runtime_status["memo"],
            "daily_generation": runtime_status["daily_generation"],
            "config": {
                "schedule_time": self.runtime.config.schedule_time,
                "state_enabled": self.runtime.config.state.enabled,
                "diagnostics_enabled": False,
                "scheduler_running": bool(getattr(rhythm, "healthy", False)),
                "scheduler_error": str(getattr(rhythm, "last_error", "") or ""),
            },
            "background_tasks": runtime_status["background_tasks"],
            "semantic_segments": runtime_status["semantic_segments"],
            "domains": runtime_status["domains"],
            "day": self._page_day(data, now, extended_night) if data else None,
            "week_plan": self._page_week_plan(week_plan),
            "world": world,
            "lifecycle": experience["lifecycle"],
            "experience": experience["experience"],
            "observatory": await self._page_observatory(
                target_date,
                world_context["life_decisions"],
                experience["evidence"],
            ),
        }

    async def _page_world_snapshot(self, target_date: str) -> tuple[dict, dict]:
        """读取面板中的人物、地点与当天会话事实。"""

        relationship_records = await self.runtime.archive.get_recent_relationships(PAGE_WORLD_RECORD_LIMIT)  # fmt: skip
        relationships = await self._page_relationships(relationship_records)
        places = await self.runtime.archive.get_recent_places(PAGE_WORLD_RECORD_LIMIT)
        events = self._page_records_for_date(
            await self.runtime.archive.get_recent_events(PAGE_WORLD_RECORD_LIMIT),
            target_date,
            "date",
        )
        summaries = self._page_records_for_date(
            await self.runtime.archive.get_recent_chat_summaries(
                PAGE_WORLD_RECORD_LIMIT
            ),
            target_date,
            "date",
        )
        group_environment_records = self._page_records_for_date(
            await self.runtime.archive.get_recent_group_environments(
                PAGE_WORLD_RECORD_LIMIT
            ),
            target_date,
            "date",
            "created_at",
        )
        group_environments = await self._page_group_environments(
            group_environment_records
        )
        message_visibility_records = await self.runtime.archive.get_message_visibility_records(PAGE_WORLD_RECORD_LIMIT)  # fmt: skip
        message_visibility = self._page_records_for_date(
            message_visibility_records,
            target_date,
            "date",
            "created_at",
        )
        action_decisions = self._page_action_decisions(
            self._page_records_for_date(
                await self._page_raw_action_decisions(),
                target_date,
                "date",
                "created_at",
            ),
            limit=PAGE_WORLD_RECORD_LIMIT,
        )
        life_decisions = await self._page_daily_decision_candidates(target_date)
        return (
            {
                "relationships": relationships,
                "places": [item.as_dict() for item in places],
                "events": [item.as_dict() for item in events],
                "summaries": [item.as_dict() for item in summaries],
                "group_environments": group_environments,
                "message_visibility": [item.as_dict() for item in message_visibility],
                "action_decisions": [item.as_dict() for item in action_decisions],
            },
            {
                "relationship_records": relationship_records,
                "summaries": summaries,
                "life_decisions": life_decisions,
            },
        )

    async def _page_experience_snapshot(
        self,
        target_date: str,
        *,
        relationship_records: list,
        summaries: list,
    ) -> dict:
        """读取面板中的生命周期、记忆与行为体验。"""

        reviews = self._page_records_for_date(
            await self.runtime.archive.get_recent_daily_reviews(7),
            target_date,
            "date",
        )
        preferences = await self.runtime.archive.get_preferences(20)
        life_events = self._page_records_for_date(
            await self.runtime.archive.get_life_events(limit=20),
            target_date,
            "date",
        )
        episodes = self._page_records_for_date(
            await self.runtime.archive.get_life_episodes(limit=20),
            target_date,
            "date",
        )
        emotion_arcs = self._page_records_for_date(
            await self.runtime.archive.get_emotion_arcs(limit=20),
            target_date,
            "date",
        )
        physiological_rhythm_logs = self._page_records_for_date(
            await self.runtime.archive.get_physiological_rhythm_logs(limit=20),
            target_date,
            "date",
        )
        physiological_rhythm_trend = (
            await self.runtime.archive.get_physiological_rhythm_trend(days=1, limit=20)
        )
        focus_targets = await self.runtime.archive.get_focus_targets(limit=20)
        memory_evidence = self._page_records_for_date(
            await self.runtime.archive.get_memory_evidence(limit=30),
            target_date,
            "date",
        )
        evidence = await self._page_memory_evidence(
            memory_evidence,
            relationship_records,
            summaries=summaries,
            episodes=episodes,
            focus_targets=focus_targets,
        )
        feedback = self._page_feedback_records(
            self._page_records_for_date(
                await self.runtime.archive.get_behavior_feedback(limit=20),
                target_date,
                "date",
            )
        )
        expression_profiles = await self.runtime.archive.get_expression_profiles(
            limit=20
        )
        behavior_patterns = await self.runtime.archive.get_behavior_patterns(limit=20)
        mid_summaries = self._page_records_for_date(
            await self.runtime.archive.get_session_mid_summaries(limit=20),
            target_date,
            "created_at",
            "updated_at",
        )
        temporary_expression_states = self._page_records_for_date(
            await self.runtime.archive.get_temporary_expression_states(limit=20),
            target_date,
            "created_at",
            "updated_at",
        )
        life_terms = await self.runtime.archive.get_life_terms(limit=20)
        memory_boundaries = await self.runtime.archive.get_memory_boundaries(
            limit=20, enabled_only=False
        )
        long_term_memories = await self.runtime.archive.list_recent_long_term_memories(
            limit=12
        )
        memory_clusters = await self.runtime.archive.get_memory_episode_clusters(
            limit=8
        )
        memory_entities = await self.runtime.archive.get_memory_entities(limit=12)
        memory_conflicts = await self.runtime.archive.get_memory_conflicts(limit=8)
        temporal_facts = await self._page_archive_records(
            "get_temporal_facts", limit=40
        )
        reflections = self._page_records_for_date(
            await self._page_archive_records("get_reflections", limit=20),
            target_date,
            "created_at",
            "updated_at",
        )
        persona_assertions = await self._page_archive_records(
            "get_persona_assertions", limit=20
        )
        decision_traces = self._page_records_for_date(
            await self._page_archive_records("get_decision_traces", limit=30),
            target_date,
            "created_at",
            "updated_at",
        )
        action_outcomes = self._page_records_for_date(
            await self._page_archive_records("get_life_action_outcomes", limit=30),
            target_date,
            "date",
            "started_at",
            "committed_at",
            "created_at",
            "updated_at",
        )
        action_receipts = self._page_records_for_date(
            await self._page_archive_records("get_life_action_receipts", limit=30),
            target_date,
            "date",
            "occurred_at",
            "created_at",
        )
        affective_states = self._page_records_for_date(
            await self._page_archive_records("get_affective_states", limit=30),
            target_date,
            "valid_from",
            "created_at",
            "updated_at",
        )
        grounded_diary = self._page_records_for_date(
            await self._page_archive_records("get_grounded_diary_entries", limit=20),
            target_date,
            "date",
        )
        durable_tasks = await self._page_archive_records("get_durable_tasks", limit=20)
        health = await self.runtime.archive.get_life_health_report(
            self.runtime.config.storage
        )
        lifecycle = {
            "reviews": [item.as_dict() for item in reviews],
            "reflections": [item.as_dict() for item in reflections],
            "grounded_diary": [item.as_dict() for item in grounded_diary],
            "durable_tasks": [item.as_dict() for item in durable_tasks],
            "preferences": [
                self._page_readable_evidence_record(item.as_dict())
                for item in preferences
            ],
            "life_events": [item.as_dict() for item in life_events],
        }
        experience = {
            "episodes": [item.as_dict() for item in episodes],
            "temporal_facts": [item.as_dict() for item in temporal_facts],
            "persona_assertions": [item.as_dict() for item in persona_assertions],
            "decision_traces": [item.as_dict() for item in decision_traces],
            "action_outcomes": [item.as_dict() for item in action_outcomes],
            "action_receipts": [item.as_dict() for item in action_receipts],
            "affective_states": [item.as_dict() for item in affective_states],
            "emotion_arcs": [
                self._page_readable_evidence_record(item.as_dict())
                for item in emotion_arcs
            ],
            "physiological_rhythm_logs": [
                item.as_dict() for item in physiological_rhythm_logs
            ],
            "physiological_rhythm_trend": physiological_rhythm_trend,
            "evidence": evidence,
            "feedback": [item.as_dict() for item in feedback],
            "expression_profiles": [item.as_dict() for item in expression_profiles],
            "behavior_patterns": [item.as_dict() for item in behavior_patterns],
            "mid_summaries": [item.as_dict() for item in mid_summaries],
            "temporary_expression_states": [
                item.as_dict() for item in temporary_expression_states
            ],
            "focus_targets": [item.as_dict() for item in focus_targets],
            "terms": [
                self._page_readable_evidence_record(item.as_dict())
                for item in life_terms
            ],
            "boundaries": [item.as_dict() for item in memory_boundaries],
            "long_term_memories": [item.as_dict() for item in long_term_memories],
            "memory_clusters": [item.as_dict() for item in memory_clusters],
            "memory_entities": [item.as_dict() for item in memory_entities],
            "memory_conflicts": [item.as_dict() for item in memory_conflicts],
            "health": health,
        }
        return {
            "lifecycle": lifecycle,
            "experience": experience,
            "evidence": evidence,
        }

    async def _page_runtime_snapshot(self, target_date: str, data, now) -> dict:
        """读取面板中的调度器、领域服务和生成运行状态。"""

        domain_service = getattr(self.runtime, "domains", None)
        domain_snapshot = {}
        domain_snapshot_getter = getattr(domain_service, "snapshot", None)
        if callable(domain_snapshot_getter):
            domain_snapshot = await domain_snapshot_getter(limit=20)
            domain_snapshot = await self._page_domain_snapshot(
                domain_snapshot, target_date
            )
        memo_status = await self._page_memo_status(target_date, data, now)
        generation_status = {}
        generation_status_getter = getattr(
            self.runtime, "daily_generation_status", None
        )
        if callable(generation_status_getter):
            generation_status = generation_status_getter(target_date)
        background_scheduler = getattr(self.runtime, "_background_scheduler", None)
        background_tasks = {}
        snapshot = getattr(background_scheduler, "snapshot", None)
        if callable(snapshot):
            try:
                background_tasks = snapshot()
            except Exception:
                background_tasks = {}
        semantic_segments = {}
        semantic_status_getter = getattr(self.runtime, "semantic_segment_status", None)
        if callable(semantic_status_getter):
            semantic_segments = semantic_status_getter()
        return {
            "memo": memo_status,
            "daily_generation": generation_status,
            "background_tasks": background_tasks,
            "semantic_segments": semantic_segments,
            "domains": domain_snapshot,
        }

    @staticmethod
    def _page_compact(value: object, limit: int = 120) -> str:
        return _compact_text(value, limit)

    @staticmethod
    def _page_internal_reference_segments(value: object) -> list[str]:
        separators = {",", "，", ";", "；", "、", "|", "｜", "\n", "\r"}
        segments: list[str] = []
        current: list[str] = []
        for char in str(value or ""):
            if char in separators:
                segment = "".join(current).strip()
                if segment:
                    segments.append(segment)
                current = []
                continue
            current.append(char)
        segment = "".join(current).strip()
        if segment:
            segments.append(segment)
        return segments

    @staticmethod
    def _page_is_evidence_reference(value: object) -> bool:
        raw = str(value or "").strip().removeprefix("#").strip()
        unsigned = raw.removeprefix("-")
        if unsigned.isdigit() and len(unsigned) >= 6:
            return True
        lowered = raw.lower()
        return (
            len(lowered) >= 20
            and "-" in lowered
            and all(
                char.isdigit() or "a" <= char <= "f" or char == "-" for char in lowered
            )
        )

    @classmethod
    def _page_readable_evidence(cls, value: object) -> str:
        body = str(value or "").strip()
        if not body:
            return ""
        segments = cls._page_internal_reference_segments(body)
        references = [
            segment for segment in segments if cls._page_is_evidence_reference(segment)
        ]
        if not references:
            return body
        readable = [
            segment
            for segment in segments
            if not cls._page_is_evidence_reference(segment)
        ]
        if readable:
            return "；".join(readable)
        unique = {
            segment.removeprefix("#").strip()
            for segment in references
            if segment.removeprefix("#").strip()
        }
        return f"来自 {len(unique)} 条聊天消息"

    @classmethod
    def _page_readable_evidence_record(cls, record: dict) -> dict:
        result = dict(record or {})
        if "evidence" in result:
            result["evidence"] = cls._page_readable_evidence(result.get("evidence"))
        return result

    @staticmethod
    def _page_strip_same_day_prefix(value: object, target_date: str) -> str:
        body = str(value or "").strip()
        target_date = str(target_date or "").strip()
        if not (body and target_date):
            return body
        pattern = re.compile(
            rf"(^|[\n；;])\s*{re.escape(target_date)}(?:\s*[|｜:：,，]\s*|\s+)"
        )
        return pattern.sub(lambda match: match.group(1), body).strip()

    @classmethod
    def _page_decision_display(cls, decision: dict, target_date: str) -> dict:
        if not decision:
            return {}
        result = dict(decision)
        for key in ("reason", "evidence", "outcome"):
            result[key] = cls._page_strip_same_day_prefix(result.get(key), target_date)
        result["reason"] = compact_explanation_text(result.get("reason"), 360)
        result["evidence"] = cls._page_readable_evidence(result.get("evidence"))
        return result

    @staticmethod
    def _page_current_decision(target_date: str, life_decisions: list) -> dict:
        for item in life_decisions:
            if (
                getattr(item, "kind", "") == "daily_plan"
                and getattr(item, "date", "") == target_date
            ):
                return item.as_dict()
        for item in life_decisions:
            if getattr(item, "kind", "") == "daily_plan":
                return item.as_dict()
        return {}

    async def _page_daily_decision_candidates(self, target_date: str) -> list:
        current = await self.runtime.archive.get_life_decisions(
            limit=PAGE_WORLD_RECORD_LIMIT,
            kind="daily_plan",
            date=target_date,
        )
        if current:
            return current
        return await self.runtime.archive.get_life_decisions(
            limit=PAGE_WORLD_RECORD_LIMIT,
            kind="daily_plan",
        )

    async def _page_memo_status(
        self, target_date: str, data, now: datetime.datetime
    ) -> dict:
        target_memo = str(getattr(data, "memo", "") or "").strip() if data else ""
        try:
            target_day = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            target_day = now.date()
        if target_memo:
            return self._page_memo_payload(
                self._page_memo_items(target_date, "target", target_memo)
            )

        items = []
        future_days = await self.runtime.archive.get_future_memo_days(
            target_day.strftime("%Y-%m-%d"),
            limit=PAGE_MEMO_CAROUSEL_LIMIT,
        )
        for future_data in future_days:
            items.extend(
                self._page_memo_items(
                    getattr(future_data, "date", ""),
                    "future",
                    getattr(future_data, "memo", ""),
                )
            )
        return self._page_memo_payload(items)

    @staticmethod
    def _page_memo_lines(memo: str) -> list[str]:
        return [line.strip() for line in str(memo or "").splitlines() if line.strip()]

    @classmethod
    def _page_memo_items(cls, date_str: str, scope: str, memo: str) -> list[dict]:
        date_text = str(date_str or "").strip()
        items = []
        for line in cls._page_memo_lines(memo):
            items.append(
                {
                    "date": date_text,
                    "scope": scope,
                    "text": line,
                    "display_text": f"{date_text} {line}"
                    if scope == "future"
                    else line,
                }
            )
        return items

    @staticmethod
    def _page_memo_payload(items: list[dict]) -> dict:
        first = items[0] if items else {}
        return {
            "date": first.get("date", ""),
            "scope": first.get("scope", "none"),
            "text": first.get("text", ""),
            "display_text": first.get("display_text", ""),
            "items": items,
            "total": len(items),
        }

    @staticmethod
    def _page_decision_sources(decision: dict, evidence: list[dict]) -> list[str]:
        decision_id = str((decision or {}).get("id") or "").strip()
        if not decision_id:
            return []
        sources: list[str] = []
        seen = set()
        for item in evidence:
            if str(item.get("source_table") or "").strip() != "life_decisions":
                continue
            if str(item.get("source_id") or "").strip() != decision_id:
                continue
            if str(item.get("target_type") or "").strip() == "life_decision":
                continue
            summary = PageViewMixin._page_compact(item.get("summary"), 80)
            if not summary or summary in seen:
                continue
            seen.add(summary)
            sources.append(summary)
            if len(sources) >= 2:
                break
        return sources

    async def _page_observatory(
        self,
        target_date: str,
        life_decisions: list,
        evidence: list[dict],
    ) -> dict:
        decision = self._page_current_decision(target_date, life_decisions)
        if decision:
            decision = self._page_decision_display(decision, target_date)
            decision["influence_sources"] = self._page_decision_sources(
                decision, evidence
            )
            linked_sources = await self.runtime.archive.get_memory_decision_sources(
                int(decision.get("id") or 0),
                limit=3,
            )
            if linked_sources:
                decision["memory_sources"] = [
                    {
                        **item,
                        "summary": self._page_compact(
                            item.get("influence")
                            or item.get("title")
                            or item.get("content"),
                            80,
                        ),
                    }
                    for item in linked_sources
                ]
        return {
            "today_decision": decision,
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
            if (
                group_id
                and (not group_name or group_name == group_id)
                and callable(resolve_group_name)
            ):
                data["group_name"] = (
                    await resolve_group_name(
                        group_id, target_umo=str(data.get("session_id") or "")
                    )
                    or group_name
                )
            result.append(data)
        return result

    async def _page_domain_snapshot(
        self, snapshot: dict, target_date: str = ""
    ) -> dict:
        """筛选当日生活流水，并补充行动项的可读会话名称。"""

        if not isinstance(snapshot, dict):
            return {}
        result = dict(snapshot)
        date_text = str(target_date or "").strip()
        if date_text:
            for key, fields in (
                ("activity_sessions", ("date", "started_at", "ended_at")),
                ("meals", ("date", "occurred_at")),
                ("chore_records", ("occurred_at",)),
                ("fitness", ("date", "occurred_at")),
                ("timeline", ("occurred_at",)),
            ):
                result[key] = self._page_records_for_date(
                    list(snapshot.get(key) or []), date_text, *fields
                )

        labels: dict[str, str] = {}
        action_items = []
        for item in snapshot.get("conversation_actions") or []:
            if not isinstance(item, dict):
                continue
            data = dict(item)
            status = str(data.get("status") or "").strip().lower()
            if date_text and status not in {"open", "pending", "active"}:
                if not self._page_records_for_date(
                    [data], date_text, "updated_at", "created_at", "due_at"
                ):
                    continue
            source_session = str(data.get("source_session") or "").strip()
            if source_session:
                if source_session not in labels:
                    labels[source_session] = await self._page_session_display_name(
                        source_session
                    )
                data["source_session_label"] = labels[source_session]
            action_items.append(data)
        result["conversation_actions"] = action_items
        return result

    async def _page_session_display_name(self, source_session: str) -> str:
        """把统一会话标识解析为昵称、备注或群名。"""

        scope = str(source_session or "").strip()
        parts = scope.split(":")
        message_type = parts[1].casefold() if len(parts) >= 3 else ""
        target_id = ":".join(parts[2:]).strip() if len(parts) >= 3 else ""
        compact_scope = scope.casefold()
        is_group = "group" in message_type or compact_scope.startswith("group:")
        is_private = (
            "friend" in message_type
            or "private" in message_type
            or compact_scope.startswith("private:")
        )
        fallback = "群聊会话" if is_group else "私聊会话" if is_private else "会话"
        resolver = getattr(self.runtime, "contact_resolver", None)
        if not resolver:
            return fallback

        async def resolved(method_name: str, *args, **kwargs) -> str:
            method = getattr(resolver, method_name, None)
            if not callable(method):
                return ""
            try:
                value = method(*args, **kwargs)
                if inspect.isawaitable(value):
                    value = await value
            except Exception:
                return ""
            text = str(value or "").strip()
            if text in {scope, target_id}:
                return ""
            return text

        if is_group:
            return (
                await resolved("resolve_group_name", target_id, target_umo=scope)
                or fallback
            )
        if is_private:
            return (
                await resolved("get_relationship_alias", scope)
                or await resolved("get_onebot_nickname", scope)
                or fallback
            )
        return fallback

    @staticmethod
    def _page_action_decisions(
        decisions: list, limit: int = PAGE_WORLD_RECORD_LIMIT
    ) -> list:
        items = []
        coalesced = set()
        for item in decisions:
            reason = compact_explanation_text(getattr(item, "reason", ""))
            if not reason:
                continue
            if reason != getattr(item, "reason", ""):
                try:
                    item.reason = reason
                except (AttributeError, TypeError):
                    pass
            key = (
                str(getattr(item, "action", "") or "").strip(),
                str(getattr(item, "scene_type", "") or "").strip(),
            )
            if key in {
                ("proactive_proposal_wait", "私聊回访/proposal"),
                ("proactive_proposal_wait", "闲时回复/proposal"),
            }:
                scope = (
                    str(getattr(item, "session_id", "") or "").strip()
                    or str(getattr(item, "sender_profile_id", "") or "").strip()
                    or str(getattr(item, "group_id", "") or "").strip()
                    or str(getattr(item, "sender_name", "") or "").strip()
                )
                scoped_key = (scope, *key)
                if scoped_key in coalesced:
                    continue
                coalesced.add(scoped_key)
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

    async def _page_memory_evidence(
        self,
        evidence: list,
        relationships: list,
        *,
        summaries: list | None = None,
        episodes: list | None = None,
        focus_targets: list | None = None,
    ) -> list[dict]:
        relationship_names = {
            str(item.id or "").strip(): str(
                item.name or item.alias or item.id or ""
            ).strip()
            for item in relationships
            if str(item.id or "").strip()
        }
        result = []
        target_labels: dict[tuple[str, str], str] = {}
        for item in summaries or []:
            item_id = str(getattr(item, "id", "") or "").strip()
            label = self._page_compact(
                getattr(item, "brief", "") or getattr(item, "long_summary", ""),
                40,
            )
            if item_id and label:
                target_labels[("chat_summary", item_id)] = label
        for item in episodes or []:
            item_id = str(getattr(item, "id", "") or "").strip()
            label = self._page_compact(getattr(item, "title", ""), 40)
            if item_id and label:
                target_labels[("life_episode", item_id)] = label
        for item in focus_targets or []:
            item_id = str(getattr(item, "id", "") or "").strip()
            target_id = str(getattr(item, "target_id", "") or "").strip()
            label = self._page_compact(
                getattr(item, "label", "") or target_id,
                40,
            )
            if label:
                for target_type in ("focus", "focus_target"):
                    if item_id:
                        target_labels[(target_type, item_id)] = label
                    if target_id:
                        target_labels[(target_type, target_id)] = label
        get_relationship = getattr(self.runtime.archive, "get_relationship", None)
        for item in evidence:
            data = item.as_dict()
            target_type = str(data.get("target_type") or "").strip().lower()
            target_id = str(data.get("target_id") or "").strip()
            data["summary"] = self._page_readable_evidence(data.get("summary"))
            if target_type == "relationship" and target_id:
                target_label = relationship_names.get(target_id, "")
                if not target_label and callable(get_relationship):
                    relationship = await get_relationship(target_id)
                    if relationship:
                        target_label = str(
                            relationship.name
                            or relationship.alias
                            or relationship.id
                            or ""
                        ).strip()
                        relationship_names[target_id] = target_label
                if target_label and target_label != target_id:
                    data["target_label"] = target_label
            elif target_id:
                target_label = target_labels.get((target_type, target_id), "")
                if target_label and target_label != target_id:
                    data["target_label"] = target_label
            result.append(data)
        return result

    def _page_day(self, data, now: datetime.datetime, extended_night: bool) -> dict:
        current, next_item = get_current_timeline_status(data.timeline, now, data.date)
        if extended_night:
            current = None
        meta = dict(data.meta)
        if meta.get("outfit_reason"):
            meta["outfit_reason"] = compact_explanation_text(
                meta.get("outfit_reason"), 360
            )
        return {
            "date": data.date,
            "outfit": data.outfit,
            "weather": data.weather,
            "weather_info": data.weather_info.as_dict(),
            "meta": meta,
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
