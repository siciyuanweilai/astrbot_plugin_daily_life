from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from astrbot.api import logger

from ..facts import (
    PathPattern,
    PersonFact,
    PersonFactContext,
    allowed_string_fields,
    apply_string_replacements,
    build_person_fact_audit_prompt,
    merge_person_facts,
    person_fact_context_from_relationships,
)
from .tools import extract_json_from_text


DAILY_PERSON_TEXT_PATHS: tuple[PathPattern, ...] = (
    ("timeline", "*", "activity"),
    ("timeline", "*", "status"),
    ("new_events", "*", "summary"),
    ("new_events", "*", "people", "*"),
    ("decision_summary", "decision"),
    ("decision_summary", "reason"),
    ("decision_summary", "continuity"),
    ("decision_summary", "novelty"),
    ("decision_summary", "memory_used", "*"),
    ("decision_summary", "avoid_repeat", "*"),
)

WEEK_PERSON_TEXT_PATHS: tuple[PathPattern, ...] = (
    ("theme",),
    ("goals", "*"),
    ("daily_hints", "*"),
    ("suggested_activities", "*", "*"),
)

INVITE_PERSON_TEXT_PATHS: tuple[PathPattern, ...] = (
    ("reason",),
    ("response_stance",),
    ("response_tone",),
    ("alternative_time",),
    ("impact",),
    ("new_future_timeline", "*", "activity"),
    ("new_future_timeline", "*", "status"),
    ("life_events", "*", "title"),
    ("life_events", "*", "detail"),
    ("life_events", "*", "effect"),
)

PROACTIVE_PERSON_TEXT_PATHS: tuple[PathPattern, ...] = (
    ("reason",),
    ("target_topic",),
    ("reply_text",),
    ("expression_intent", "reason"),
)

COMMITMENT_PERSON_TEXT_PATHS: tuple[PathPattern, ...] = (
    ("content",),
    ("people", "*"),
)

MEDIA_PERSON_TEXT_PATHS: tuple[PathPattern, ...] = (
    ("shots", "*", "title"),
    ("shots", "*", "prompt"),
)


@dataclass(frozen=True, slots=True)
class PersonAuditResult:
    payload: dict[str, Any]
    status: str
    reason: str = ""
    replacements: int = 0

    @property
    def unresolved(self) -> bool:
        return self.status == "unresolved"


class PersonFactMixin:
    async def _build_person_fact_context(
        self,
        *,
        persona: str = "",
        explicit_instruction: str = "",
        relationships: list[Any] | None = None,
    ) -> PersonFactContext:
        persona = str(persona or "").strip() or await self._get_persona()
        if relationships is None:
            relationships = await self.archive.get_recent_relationships(12)

        base = person_fact_context_from_relationships(
            persona=persona,
            explicit_instruction=explicit_instruction,
            relationships=relationships,
        )
        facts = list(base.facts)
        unverified_people = list(base.unverified_people)

        def add_persona_fact(name: str, statement: str) -> None:
            name = str(name or "").strip()
            statement = str(statement or "").strip()
            if not name:
                return
            if not statement:
                if name not in unverified_people:
                    unverified_people.append(name)
                return
            matched_index = next(
                (
                    index
                    for index, fact in enumerate(facts)
                    if name.casefold()
                    in {candidate.casefold() for candidate in fact.names()}
                ),
                None,
            )
            if matched_index is not None:
                previous = facts[matched_index]
                aliases = tuple(
                    dict.fromkeys(
                        item
                        for item in (*previous.names(), name)
                        if item != previous.name
                    )
                )
                facts[matched_index] = PersonFact(
                    name=previous.name,
                    aliases=aliases,
                    statement=statement,
                    source="当前角色人设",
                    priority=300,
                )
            else:
                facts.append(
                    PersonFact(
                        name=name,
                        statement=statement,
                        source="当前角色人设",
                        priority=300,
                    )
                )
            unverified_people[:] = [
                item for item in unverified_people if item.casefold() != name.casefold()
            ]

        image_settings = getattr(self.config, "image_generation", None)
        for profile in getattr(image_settings, "friend_reference_profiles", []) or []:
            if not isinstance(profile, dict):
                continue
            name = str(
                profile.get("display_name") or profile.get("profile_id") or ""
            ).strip()
            add_persona_fact(name, self._extract_reference_persona(persona, name))

        for user_id in getattr(self.config, "reference_users", []) or []:
            profile = await self._resolve_reference_user_profile(
                str(user_id or ""), persona=persona
            )
            add_persona_fact(profile.get("name"), profile.get("persona"))

        return PersonFactContext(
            persona=persona,
            explicit_instruction=str(explicit_instruction or "").strip(),
            facts=merge_person_facts(facts),
            unverified_people=tuple(unverified_people[:12]),
        )

    async def _audit_person_payload(
        self,
        payload: dict[str, Any],
        *,
        context: PersonFactContext,
        patterns: Sequence[PathPattern],
        provider: Any,
        provider_id: str = "",
        subject: str,
    ) -> PersonAuditResult:
        if not context.has_external_facts:
            return PersonAuditResult(payload, "skipped")
        if not allowed_string_fields(payload, patterns):
            return PersonAuditResult(payload, "skipped")

        prompt = build_person_fact_audit_prompt(
            context, payload, patterns, subject=subject
        )
        session_id = f"daily_life_person_audit_{uuid.uuid4().hex[:8]}"
        try:
            text = await asyncio.wait_for(
                self._call_llm_text(
                    provider,
                    prompt,
                    session_id,
                    empty_retries=0,
                    primary_provider_id=provider_id,
                ),
                timeout=15.0,
            )
            audit = extract_json_from_text(text)
            if not isinstance(audit, dict) or not isinstance(audit.get("valid"), bool):
                logger.debug("[人物事实审计] 未形成有效结果，保留原生成内容。")
                return PersonAuditResult(payload, "failed", "审计结果无效")
            reason = str(audit.get("reason") or "").strip()[:240]
            if audit["valid"]:
                logger.debug("[人物事实审计] 通过。")
                return PersonAuditResult(payload, "passed", reason)
            revised, count = apply_string_replacements(
                payload, audit.get("replacements"), patterns
            )
            if count <= 0:
                logger.warning("[人物事实审计] 发现冲突，但没有可安全应用的修订。")
                return PersonAuditResult(
                    payload,
                    "unresolved",
                    reason or "人物身份或称谓与明确资料冲突",
                )
            logger.info(f"[人物事实审计] 已修正冲突字段：{count} 项")
            return PersonAuditResult(revised, "revised", reason, count)
        except Exception as exc:
            logger.debug(f"[人物事实审计] 执行失败，保留原生成内容：{exc}")
            return PersonAuditResult(payload, "failed", str(exc)[:240])
        finally:
            await self._cleanup_conversation(session_id)


__all__ = [
    "DAILY_PERSON_TEXT_PATHS",
    "COMMITMENT_PERSON_TEXT_PATHS",
    "INVITE_PERSON_TEXT_PATHS",
    "MEDIA_PERSON_TEXT_PATHS",
    "PersonAuditResult",
    "PersonFactMixin",
    "PROACTIVE_PERSON_TEXT_PATHS",
    "WEEK_PERSON_TEXT_PATHS",
]
