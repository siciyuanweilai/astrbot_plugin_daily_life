import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ....config.options.basis import EmojiSettings


class EmojiBaseMixin:
    EMOJI_ASSET_DIR_NAME = "emoji"
    EMOJI_ASSET_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
    EMOJI_ASSET_DEFAULT_MAX_READY = 128
    EMOJI_ASSET_DEFAULT_ORPHAN_GRACE_SECONDS = 24 * 60 * 60
    VISUAL_CONTEXT_SUMMARY_MAX_CHARS = 180
    EMOJI_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    EMOJI_ASSET_COLLECTIBLE_KINDS = {
        "emoji",
        "face",
        "mface",
        "sticker",
        "custom_emoji",
        "customemoji",
        "market_face",
        "marketface",
        "wechat_emoji",
        "wechatemoji",
        "is_emoji",
        "is_sticker",
    }
    EMOJI_ASSET_REVIEWABLE_KINDS = {
        "emoji_image",
        "sticker_image",
        "mface_image",
        "market_face_image",
        "marketface_image",
    }
    EMOJI_ASSET_REJECTED_STATUSES = {"failed", "disabled", "rejected"}
    EMOJI_ASSET_MIN_REVIEW_CONFIDENCE = 0.7
    EMOJI_ASSET_INACTIVE_STATUSES = {"pending", "reviewing", "failed", "rejected", "missing"}

    def _emoji_settings(self) -> EmojiSettings:
        settings = getattr(getattr(self, "config", None), "emoji", None)
        return settings if isinstance(settings, EmojiSettings) else EmojiSettings()

    def _emoji_auto_collect_enabled(self) -> bool:
        return bool(self._emoji_settings().collect_chat_emojis)

    def _emoji_max_bytes(self) -> int:
        max_mb = max(1.0, min(float(self._emoji_settings().max_size_mb), 20.0))
        return max(1, int(max_mb * 1024 * 1024))

    def _emoji_max_ready_count(self) -> int:
        return max(1, int(self._emoji_settings().max_ready))

    def _emoji_replace_when_full(self) -> bool:
        return bool(self._emoji_settings().replace_when_full)

    def _emoji_send_candidate_limit(self) -> int:
        return max(1, int(self._emoji_settings().send_candidate_limit))

    def _emoji_review_batch_size(self) -> int:
        return max(1, int(self._emoji_settings().review_batch_size))

    def _emoji_inactive_record_keep_days(self) -> int:
        return max(1, int(self._emoji_settings().inactive_record_keep_days))

    def _emoji_orphan_grace_seconds(self) -> int:
        return max(1, int(self._emoji_settings().orphan_cache_grace_hours)) * 60 * 60

    def _emoji_asset_size_allowed(self, size_bytes: int) -> bool:
        max_bytes = self._emoji_max_bytes()
        return int(size_bytes or 0) <= max_bytes

    async def _emoji_ready_asset_count(self, *, exclude_hash: str = "") -> int:
        getter = getattr(getattr(self, "archive", None), "get_emoji_assets", None)
        if not callable(getter):
            return 0
        assets = await getter(limit=0, status="ready", sendable_only=True)
        exclude_hash = str(exclude_hash or "").strip()
        return sum(1 for item in assets if str(getattr(item, "file_hash", "") or "").strip() != exclude_hash)

    async def _emoji_can_accept_ready_asset(self, *, exclude_hash: str = "") -> bool:
        return self._emoji_replace_when_full() or await self._emoji_ready_asset_count(exclude_hash=exclude_hash) < self._emoji_max_ready_count()

    async def _get_vision_provider(self):
        provider_id = self.config.vision.provider
        return await self.composer._get_provider(provider_id)

    def _emoji_asset_cache_dir(self, *, create: bool = True) -> Path | None:
        data_path = getattr(self, "data_path", None)
        if not data_path:
            return None
        cache_dir = Path(data_path).expanduser().resolve().parent / self.EMOJI_ASSET_DIR_NAME
        if create:
            cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    @classmethod
    def _emoji_asset_source(cls, payload: dict[str, str]) -> str:
        return (payload.get("path") or payload.get("file") or payload.get("url") or payload.get("image") or "").strip()

    @staticmethod
    def _emoji_asset_component_kind(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("type") or item.get("kind") or "").strip().lower()
        explicit = str(getattr(item, "type", "") or getattr(item, "kind", "") or "").strip().lower()
        return explicit or item.__class__.__name__.strip().lower()

    @classmethod
    def _emoji_asset_meta_values(cls, item: Any) -> set[str]:
        values: set[str] = set()

        def add_value(value: Any) -> None:
            text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
            if not text:
                return
            values.add(text)
            compact = text.replace("_", "")
            values.add(compact)
            for suffix in ("component", "message", "segment", "part"):
                if compact.endswith(suffix) and len(compact) > len(suffix):
                    values.add(compact[: -len(suffix)])

        def add_mapping(mapping: dict[str, Any]) -> None:
            for key in (
                "type",
                "kind",
                "sub_type",
                "subType",
                "message_type",
                "messageType",
                "media_type",
                "mediaType",
                "raw_type",
                "rawType",
                "original_type",
                "originalType",
                "segment_type",
                "segmentType",
                "element_type",
                "elementType",
                "media_kind",
                "mediaKind",
            ):
                add_value(mapping.get(key))
            for key in ("is_emoji", "isEmoji", "is_sticker", "isSticker"):
                if mapping.get(key) is True:
                    add_value(key)

        if isinstance(item, dict):
            add_mapping(item)
            data = item.get("data")
            if isinstance(data, dict):
                add_mapping(data)
        else:
            for key in ("type", "kind", "sub_type", "subType", "message_type", "messageType", "media_type", "mediaType"):
                add_value(getattr(item, key, ""))
            data = getattr(item, "data", None)
            if isinstance(data, dict):
                add_mapping(data)
            add_value(item.__class__.__name__)
        return values

    @classmethod
    def _emoji_asset_is_image_item(cls, item: Any) -> bool:
        return bool(cls._emoji_asset_meta_values(item) & {"image", "picture"})

    @classmethod
    def _emoji_asset_is_collectible_item(cls, item: Any) -> bool:
        return cls._emoji_asset_source_kind(item) == "trusted"

    @classmethod
    def _emoji_asset_source_kind(cls, item: Any) -> str:
        values = cls._emoji_asset_meta_values(item)
        if values & cls.EMOJI_ASSET_COLLECTIBLE_KINDS:
            return "trusted"
        if values & cls.EMOJI_ASSET_REVIEWABLE_KINDS:
            return "review"
        if values & {"image", "picture"}:
            return "plain_image"
        return ""

    @classmethod
    def _emoji_asset_type_from_item(cls, item: Any) -> str:
        values = cls._emoji_asset_meta_values(item)
        for kind in sorted(cls.EMOJI_ASSET_COLLECTIBLE_KINDS | cls.EMOJI_ASSET_REVIEWABLE_KINDS):
            if kind in values:
                return kind
        if values & {"image", "picture"}:
            return "image"
        return cls._emoji_asset_component_kind(item)

    def _visual_context_summary_cache(self) -> dict[str, str]:
        cache = getattr(self, "_visual_context_summaries", None)
        if not isinstance(cache, dict):
            cache = {}
            self._visual_context_summaries = cache
        return cache

    @classmethod
    def _emoji_asset_suffix(cls, source: str, data: bytes = b"", content_type: str = "") -> str:
        lower_type = str(content_type or "").split(";", 1)[0].strip().lower()
        type_suffixes = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
        }
        if lower_type in type_suffixes:
            return type_suffixes[lower_type]
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if data.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if len(data) >= 12 and data[8:12] == b"WEBP":
            return ".webp"
        parsed = urlparse(source)
        suffix_source = parsed.path if parsed.scheme else source
        suffix = Path(suffix_source).suffix.lower()
        return suffix if suffix in cls.EMOJI_ASSET_SUFFIXES else ".png"

    @staticmethod
    def _emoji_asset_filename(fingerprint: str, suffix: str) -> str:
        safe_hash = "".join(ch for ch in str(fingerprint or "") if ch.isalnum())[:80] or uuid.uuid4().hex
        safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        return f"{safe_hash}{safe_suffix.lower()}"

    @staticmethod
    def _emoji_asset_is_remote(path_text: str) -> bool:
        return str(path_text or "").startswith(("http://", "https://"))

    def _resolve_cached_emoji_path(self, path_text: str, cache_dir: Path) -> Path | None:
        if not path_text or self._emoji_asset_is_remote(path_text):
            return None
        try:
            resolved = Path(path_text).expanduser().resolve()
            resolved.relative_to(cache_dir)
        except (OSError, RuntimeError, ValueError):
            return None
        return resolved

    def _apply_visual_context_summary(
        self,
        context_scope: str,
        context_message_key: str,
        payload: dict[str, Any],
        fingerprint: str = "",
    ) -> None:
        summary = self._visual_context_summary_from_payload(payload)
        if not summary:
            return
        if fingerprint:
            self._visual_context_summary_cache()[fingerprint] = summary
        self._apply_visual_context_summary_text(context_scope, context_message_key, summary)

    def _apply_visual_context_summary_text(
        self,
        context_scope: str,
        context_message_key: str,
        summary: str,
    ) -> None:
        updater = getattr(self, "update_structured_message_visual_summary", None)
        if callable(updater):
            updater(context_scope, context_message_key, summary)

    def _visual_context_summary_from_payload(self, payload: dict[str, Any]) -> str:
        summary = self._str_payload(payload.get("summary"))
        if not summary:
            summary = self._str_payload(payload.get("caption"))
        if not summary:
            summary = self._str_payload(payload.get("description"))
        return self._compact_visual_context_summary(summary)

    @classmethod
    def _compact_visual_context_summary(cls, summary: str) -> str:
        text = " ".join(str(summary or "").split())
        limit = max(int(cls.VISUAL_CONTEXT_SUMMARY_MAX_CHARS), 40)
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}..."
