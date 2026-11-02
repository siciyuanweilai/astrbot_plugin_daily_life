from __future__ import annotations

from typing import Any

from ...models import MemoryCorrectionRecord
from ...addresses import habitual_address

from typing import Protocol


class ImprintPortraitComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _calibrate_memory_target_payload: Any
    _calibrate_relationship_profile: Any
    _int_payload: Any
    _list_payload: Any
    _relationship_contact_payload: Any
    _str_payload: Any
    archive: Any


class ImprintPortraitComponent:
    def __init__(self, context: ImprintPortraitComponentContext) -> None:
        self.context = context

    async def _apply_memory_correction(
        self,
        correction: MemoryCorrectionRecord,
        meta: dict[str, str],
    ) -> bool:
        target_type = self.context._str_payload(correction.target_type)
        target_id = self.context._str_payload(correction.target_id)
        if not (target_type and target_id and correction.correction):
            return False
        if target_type == "life_episode":
            episode_id = self.context._int_payload(target_id)
            return bool(
                episode_id
                and await self.context.archive.correct_life_episode(
                    episode_id, correction.correction
                )
            )
        if target_type == "relationship":
            await self.context.archive.revise_relationship_profile(
                target_id,
                date_str=meta.get("date", ""),
                source="记忆纠错",
                note=correction.correction,
                relationship_points=[correction.correction],
            )
            return True
        return False

    async def _save_memory_targets(
        self, payload: dict, meta: dict[str, str]
    ) -> list[dict[str, Any]]:
        targets = self.context._list_payload(payload.get("memory_targets"))
        if not targets:
            return []
        saved_targets: list[dict[str, Any]] = []
        for target in targets[:8]:
            if not isinstance(target, dict):
                continue
            target_persona_hint = self.context._str_payload(target.get("persona_hint"))
            if target_persona_hint:
                target = await self.context._calibrate_memory_target_payload(
                    target, meta, target_persona_hint
                )
                target_persona_hint = (
                    self.context._str_payload(target.get("persona_hint")) or target_persona_hint
                )
            raw_profile_id = self.context._str_payload(target.get("profile_id"))
            if not raw_profile_id or raw_profile_id != meta.get("sender_profile_id"):
                continue
            profile_id = raw_profile_id
            # 人物身份取已核实的本批发送者；模型只维护主观关系描述。
            name = self.context._str_payload(meta.get("sender_name")) or profile_id
            platform = meta["platform"]
            user_id = meta["user_id"]
            existing = await self.context.archive.get_relationship(profile_id)
            if name in {profile_id, user_id}:
                if existing is not None:
                    name = existing.name or profile_id
            contact_payload = self.context._relationship_contact_payload(meta)
            note = self.context._str_payload(target.get("note"))
            subjective_name = habitual_address(existing)
            relationship_story = self.context._str_payload(target.get("relationship_story"))
            subjective_tags = [
                self.context._str_payload(item)
                for item in self.context._list_payload(
                    target.get("subjective_tags") or target.get("tags")
                )
                if self.context._str_payload(item)
            ]
            points = [
                self.context._str_payload(item)
                for item in self.context._list_payload(target.get("points"))
                if self.context._str_payload(item)
            ]
            if not any(
                [note, points, relationship_story, subjective_tags]
            ):
                continue
            await self.context.archive.touch_relationship(
                profile_id,
                name=name or profile_id,
                note=note,
                date_str=meta["date"],
                source=self.context._str_payload(target.get("source"), "chat_memory_target")
                or "chat_memory_target",
                platform=platform,
                user_id=user_id,
                alias=name,
                observed_name=self.context._str_payload(meta.get("sender_name")),
                persona_hint=self.context._str_payload(target.get("persona_hint")),
                subjective_tags=subjective_tags[:8],
                relationship_story=relationship_story,
                **contact_payload,
            )
            for point in points[:6]:
                await self.context.archive.add_relationship_point(
                    profile_id,
                    point,
                    date_str=meta["date"],
                    source="chat_memory_target",
                )
            # 关系点在这里才真正写入，必须在最后统一校准一次，避免追加内容绕过审计。
            await self.context._calibrate_relationship_profile(
                profile_id, target_persona_hint, meta["date"]
            )
            saved_targets.append(
                {
                    **target,
                    "profile_id": profile_id,
                    "name": name or profile_id,
                    "alias": name,
                    "subjective_name": subjective_name,
                    "points": points[:6],
                    "subjective_tags": subjective_tags[:8],
                    "relationship_story": relationship_story,
                    "note": note,
                }
            )
        return saved_targets
