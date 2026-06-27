from __future__ import annotations

from typing import Any

from .calibrate import CaptureCalibrationMixin
from .persona import CapturePersonaMixin


class CaptureRelationMixin(CapturePersonaMixin, CaptureCalibrationMixin):
    def _subjective_impression_data(self, payload: dict) -> dict[str, Any]:
        impression = payload.get("subjective_impression")
        impression = impression if isinstance(impression, dict) else {}
        return {
            "subjective_name": self._str_payload(impression.get("subjective_name")),
            "subjective_tags": [
                self._str_payload(item)
                for item in self._list_payload(impression.get("tags") or impression.get("subjective_tags"))
                if self._str_payload(item)
            ][:8],
            "relationship_story": self._str_payload(impression.get("relationship_story")),
            "note": self._str_payload(impression.get("impression_delta")),
        }

    async def _save_subjective_impression(
        self,
        payload: dict,
        meta: dict[str, str],
        *,
        note_fallback: str = "",
        source: str = "chat_impression",
        persona_hint: str = "",
    ) -> None:
        visibility = payload.get("visibility") if isinstance(payload.get("visibility"), dict) else {}
        level = self._str_payload(visibility.get("level")).lower()
        if level in {"unseen", "missed"}:
            return

        impression_data = self._subjective_impression_data(payload)
        note = impression_data["note"] or note_fallback
        if not any([note, impression_data["subjective_name"], impression_data["subjective_tags"], impression_data["relationship_story"]]):
            return

        await self.archive.touch_relationship(
            meta["sender_profile_id"],
            name=meta["sender_name"],
            note=note,
            date_str=meta["date"],
            source=source,
            platform=meta["platform"],
            user_id=meta["user_id"],
            alias=meta["sender_name"],
            persona_hint=persona_hint,
            subjective_name=impression_data["subjective_name"],
            subjective_tags=impression_data["subjective_tags"],
            relationship_story=impression_data["relationship_story"],
            **self._relationship_contact_payload(meta),
        )
        await self._calibrate_relationship_profile(meta["sender_profile_id"], persona_hint, meta["date"])
