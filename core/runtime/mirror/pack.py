from __future__ import annotations

import asyncio
from typing import Any

from ...clock import now as life_now


class SnapshotPackMixin:
    async def _gather_life_context_snapshot(
        self,
        event: Any = None,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        cache = getattr(self, "_injection_snapshot_cache", None)
        if cache is None:
            self._injection_snapshot_cache = {}
            cache = self._injection_snapshot_cache

        max_summaries = self.config.memory.max_injection_items
        experience_scope = ""
        session_id = ""
        group_id = ""
        if event is not None:
            session_id = self._event_session_id(event)
            group_id, _ = self._event_group_meta(event)
            experience_scope = group_id or session_id

        now_ts = life_now().timestamp()
        cache_key = f"{max_summaries}:{experience_scope}"
        if use_cache:
            cached = cache.get(cache_key)
            if isinstance(cached, dict) and now_ts - float(cached.get("ts", 0.0) or 0.0) <= 8.0:
                return dict(cached.get("data") or {})

        await self._settle_stale_reply_effects()

        base_tasks: list[Any] = [
            self.archive.get_recent_relationships(8),
            self.archive.get_recent_places(10),
            self.archive.get_recent_events(10),
            self.archive.get_recent_chat_summaries(max_summaries),
            self.archive.get_recent_group_environments(3),
            self.archive.get_recent_action_decisions(3),
            self.archive.get_recent_message_visibility(3),
            self.archive.get_life_episodes(limit=3),
            self.archive.get_focus_targets(limit=4),
            self.archive.get_behavior_feedback(limit=3),
            self.archive.get_reply_effects(limit=4, scope=experience_scope),
            self.archive.get_memory_corrections(limit=3, unapplied_only=True),
            self.archive.get_expression_profiles(limit=4),
            self.archive.get_expression_reviews(limit=3, scope=experience_scope),
            self.archive.get_behavior_patterns(limit=4),
            self.archive.get_behavior_scenes(limit=4, scope=experience_scope),
            self.archive.get_session_mid_summaries(limit=3, session_id=session_id),
            self.archive.get_temporary_expression_states(limit=3, scope=experience_scope),
            self.archive.get_focus_slots(limit=4, scope=experience_scope),
            self.archive.get_expression_intents(limit=3, scope=experience_scope),
            self.archive.get_life_terms(limit=6, scope=experience_scope),
            self.archive.get_memory_boundaries(limit=4),
        ]

        results = await asyncio.gather(*base_tasks)
        snapshot = {
            "relationships": results[0],
            "places": results[1],
            "events": results[2],
            "summaries": results[3],
            "experience_scope": experience_scope,
        }

        experience_keys = (
            "environments",
            "decisions",
            "visibility",
            "episodes",
            "focus_targets",
            "feedback",
            "reply_effects",
            "memory_corrections",
            "expression_profiles",
            "expression_reviews",
            "behavior_patterns",
            "behavior_scenes",
            "mid_summaries",
            "temporary_expression_states",
            "focus_slots",
            "expression_intents",
            "terms",
            "boundaries",
        )
        snapshot.update(dict(zip(experience_keys, results[4:])))

        cache[cache_key] = {"ts": now_ts, "data": snapshot}
        return dict(snapshot)
