from __future__ import annotations

from typing import Any


class StageAlbumMixin:
    def _remember_life_image_for_scope(self, scope: str, path: Any) -> None:
        scope = str(scope or "").strip()
        path_text = str(path or "").strip()
        if not scope or not path_text:
            return
        cache = getattr(self, "_life_media_last_images", None)
        if not isinstance(cache, dict):
            cache = {}
            self._life_media_last_images = cache
        cache[scope] = path_text

    def _last_generated_life_image_path(self, scope: str = "") -> str:
        cache = getattr(self, "_life_media_last_images", None)
        if not isinstance(cache, dict):
            return ""
        return str(cache.get(str(scope or "").strip()) or "").strip()
