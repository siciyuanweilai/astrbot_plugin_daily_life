from typing import Any

from ...models import MemoryCorrectionRecord


class ImprintPortraitMixin:
    async def _apply_memory_correction(
        self,
        correction: MemoryCorrectionRecord,
        meta: dict[str, str],
    ) -> bool:
        target_type = self._str_payload(correction.target_type)
        target_id = self._str_payload(correction.target_id)
        if not (target_type and target_id and correction.correction):
            return False
        if target_type == "life_episode":
            episode_id = self._int_payload(target_id)
            return bool(episode_id and await self.archive.correct_life_episode(episode_id, correction.correction))
        if target_type == "relationship":
            await self.archive.revise_relationship_profile(
                target_id,
                date_str=meta.get("date", ""),
                source="记忆纠错",
                note=correction.correction,
                relationship_points=[correction.correction],
            )
            return True
        return False

    async def _save_memory_targets(self, payload: dict, meta: dict[str, str]) -> list[dict[str, Any]]:
        targets = self._list_payload(payload.get("memory_targets"))
        if not targets:
            return []
        saved_targets: list[dict[str, Any]] = []
        for target in targets[:8]:
            if not isinstance(target, dict):
                continue
            target_persona_hint = self._str_payload(target.get("persona_hint"))
            if target_persona_hint:
                target = await self._calibrate_memory_target_payload(target, meta, target_persona_hint)
                target_persona_hint = self._str_payload(target.get("persona_hint")) or target_persona_hint
            name = self._str_payload(target.get("name") or target.get("alias"))
            raw_profile_id = self._str_payload(target.get("profile_id"))
            if not raw_profile_id and name:
                raw_profile_id = f"name:{name}"
            if not raw_profile_id:
                continue
            profile_id = raw_profile_id
            if profile_id == meta["sender_profile_id"]:
                name = name or meta["sender_name"]
                platform = meta["platform"]
                user_id = meta["user_id"]
                contact_payload = self._relationship_contact_payload(meta)
            else:
                platform = ""
                user_id = ""
                contact_payload = {}
            note = self._str_payload(target.get("note"))
            subjective_name = self._str_payload(target.get("subjective_name"))
            relationship_story = self._str_payload(target.get("relationship_story"))
            subjective_tags = [
                self._str_payload(item)
                for item in self._list_payload(target.get("subjective_tags") or target.get("tags"))
                if self._str_payload(item)
            ]
            points = [
                self._str_payload(item)
                for item in self._list_payload(target.get("points"))
                if self._str_payload(item)
            ]
            if not any([note, points, subjective_name, relationship_story, subjective_tags]):
                continue
            await self.archive.touch_relationship(
                profile_id,
                name=name or profile_id,
                note=note,
                date_str=meta["date"],
                source=self._str_payload(target.get("source"), "chat_memory_target") or "chat_memory_target",
                platform=platform,
                user_id=user_id,
                alias=self._str_payload(target.get("alias")) or name,
                persona_hint=self._str_payload(target.get("persona_hint")),
                subjective_name=subjective_name,
                subjective_tags=subjective_tags[:8],
                relationship_story=relationship_story,
                **contact_payload,
            )
            await self._calibrate_relationship_profile(profile_id, target_persona_hint, meta["date"])
            for point in points[:6]:
                await self.archive.add_relationship_point(
                    profile_id,
                    point,
                    date_str=meta["date"],
                    source="chat_memory_target",
                )
            saved_targets.append(
                {
                    **target,
                    "profile_id": profile_id,
                    "name": name or profile_id,
                    "points": points[:6],
                    "subjective_tags": subjective_tags[:8],
                    "relationship_story": relationship_story,
                    "note": note,
                }
            )
        return saved_targets
