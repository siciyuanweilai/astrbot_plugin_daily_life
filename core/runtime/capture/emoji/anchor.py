import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class EmojiBaseMixin:
    EMOJI_ASSET_DIR_NAME = "emoji_assets"
    EMOJI_ASSET_MAX_BYTES = 5 * 1024 * 1024
    EMOJI_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    EMOJI_ASSET_MAX_READY = 200
    EMOJI_ASSET_ORPHAN_GRACE_SECONDS = 24 * 60 * 60

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
    def _emoji_asset_is_image_item(cls, item: Any) -> bool:
        kind = cls._emoji_asset_component_kind(item)
        return "image" in kind or "picture" in kind

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
        return " ".join(summary.split())[:96]
