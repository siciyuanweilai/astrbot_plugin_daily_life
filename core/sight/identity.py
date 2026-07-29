from __future__ import annotations

import time
from typing import Any

from .clip import SightClip
from .probe import payload_from_item


SIGHT_UPLOAD_ID_TTL_SECONDS = 180
SIGHT_COMPLETED_KEY_TTL_SECONDS = 2 * 60 * 60


class SightIdentityMixin:
    def _init_sight_identity(self) -> None:
        self._sight_upload_ids: dict[tuple[str, str, str, str], tuple[str, float]] = {}
        self._sight_completed_keys: dict[str, float] = {}

    @staticmethod
    def _sight_raw_message(source: Any) -> Any:
        message_obj = getattr(source, "message_obj", None)
        return getattr(message_obj, "raw_message", None) or getattr(
            source, "raw_message", None
        )

    def note_sight_group_upload_event(self, event: Any) -> bool:
        now = time.monotonic()
        upload_ids = getattr(self, "_sight_upload_ids", None)
        if not isinstance(upload_ids, dict):
            upload_ids = {}
            self._sight_upload_ids = upload_ids
        for key, (_, timestamp) in list(upload_ids.items()):
            if now - timestamp > SIGHT_UPLOAD_ID_TTL_SECONDS:
                upload_ids.pop(key, None)
        remembered = False
        for source in self._event_sources(event):
            raw = self._sight_raw_message(source)
            if (
                not isinstance(raw, dict)
                or str(raw.get("notice_type") or "").strip().lower() != "group_upload"
            ):
                continue
            file_data = raw.get("file")
            if not isinstance(file_data, dict):
                continue
            platform_id = str(file_data.get("id") or "").strip()
            name = str(file_data.get("name") or "").strip()
            size = str(file_data.get("size") or "").strip()
            group_id = str(raw.get("group_id") or "").strip()
            user_id = str(raw.get("user_id") or "").strip()
            if not platform_id or not name or not size or not group_id:
                continue
            upload_ids[(group_id, user_id, name, size)] = (platform_id, now)
            remembered = True
        return remembered

    def _sight_apply_group_upload_id(
        self, event: Any, clips: list[SightClip]
    ) -> list[SightClip]:
        upload_ids = getattr(self, "_sight_upload_ids", None)
        if not isinstance(upload_ids, dict) or not clips:
            return clips
        group_id = self._event_group_meta(event)[0]
        user_id = self._safe_event_call(event, "get_sender_id")
        if not group_id or not user_id:
            return clips
        now = time.monotonic()
        for clip in clips:
            size = str(clip.metadata.get("file_size") or "").strip()
            key = (group_id, user_id, str(clip.name or "").strip(), size)
            value = upload_ids.get(key)
            if not value or now - value[1] > SIGHT_UPLOAD_ID_TTL_SECONDS:
                continue
            clip.metadata["platform_id"] = value[0]
        return clips

    def _sight_enrich_clips_from_raw_message(
        self, event: Any, clips: list[SightClip]
    ) -> list[SightClip]:
        payloads: list[dict[str, str]] = []
        for source in self._event_sources(event):
            raw = self._sight_raw_message(source)
            if not isinstance(raw, dict):
                continue
            items = raw.get("message")
            if not isinstance(items, list):
                continue
            for item in items:
                if "file" not in self._event_component_kind(item):
                    continue
                payload = payload_from_item(item)
                if payload:
                    payloads.append(payload)
        for clip in clips:
            for payload in payloads:
                raw_name = str(
                    payload.get("name")
                    or payload.get("file_name")
                    or payload.get("filename")
                    or payload.get("file")
                    or ""
                ).strip()
                if clip.name and raw_name and clip.name != raw_name:
                    continue
                if not clip.name and raw_name:
                    clip.name = raw_name
                file_size = str(
                    payload.get("file_size") or payload.get("size") or ""
                ).strip()
                if file_size:
                    clip.metadata["file_size"] = file_size
                if not clip.file_id:
                    clip.file_id = str(
                        payload.get("file_id") or payload.get("fileid") or ""
                    ).strip()
                if not clip.source:
                    clip.source = str(
                        payload.get("url") or payload.get("path") or ""
                    ).strip()
                break
        return clips

    def _sight_completed_key_is_fresh(self, key: str) -> bool:
        now = time.monotonic()
        values = getattr(self, "_sight_completed_keys", None)
        if not isinstance(values, dict):
            values = {}
            self._sight_completed_keys = values
        ttl = max(
            60.0,
            float(self._sight_cache_ttl_seconds() or SIGHT_COMPLETED_KEY_TTL_SECONDS),
        )
        for item, marked_at in list(values.items()):
            if now - marked_at >= ttl:
                values.pop(item, None)
        normalized = str(key or "").strip()
        return bool(normalized and normalized in values)

    def _mark_sight_completed_key(self, key: str) -> None:
        normalized = str(key or "").strip()
        if not normalized:
            return
        values = getattr(self, "_sight_completed_keys", None)
        if not isinstance(values, dict):
            values = {}
            self._sight_completed_keys = values
        values[normalized] = time.monotonic()
        maximum = max(8, self._sight_cache_max_items())
        if len(values) > maximum:
            stale = sorted(values.items(), key=lambda item: item[1])[
                : len(values) - maximum
            ]
            for item, _ in stale:
                values.pop(item, None)
