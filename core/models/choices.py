from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_int


@dataclass(slots=True)
class CatalogItemRecord:
    category: str = ""
    item_id: str = ""
    text: str = ""
    enabled: bool = True
    sort_order: int = 0
    source: str = "custom"

    @staticmethod
    def from_value(value: Any) -> "CatalogItemRecord | None":
        raw = value if isinstance(value, dict) else {}
        category = str(raw.get("category") or "").strip()
        text = str(raw.get("text") or "").strip()
        if not category or not text:
            return None
        return CatalogItemRecord(
            category=category,
            item_id=str(raw.get("item_id") or "").strip(),
            text=text,
            enabled=bool(raw.get("enabled", True)),
            sort_order=optional_int(raw.get("sort_order")) or 0,
            source=str(raw.get("source") or "custom").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "item_id": self.item_id,
            "text": self.text,
            "enabled": self.enabled,
            "sort_order": self.sort_order,
            "source": self.source,
        }


@dataclass(slots=True)
class HairStyleRecord:
    style_id: str = ""
    name: str = ""
    hairstyles: list[str] = field(default_factory=list)
    enabled: bool = True
    sort_order: int = 0
    source: str = "custom"

    @staticmethod
    def from_value(value: Any) -> "HairStyleRecord | None":
        raw = value if isinstance(value, dict) else {}
        name = str(raw.get("name") or "").strip()
        if not name:
            return None
        hairstyles = raw.get("hairstyles", [])
        if isinstance(hairstyles, str):
            hairstyles = [hairstyles]
        elif not isinstance(hairstyles, list):
            hairstyles = []
        options = [str(item).strip() for item in hairstyles if str(item).strip()]
        if not options:
            return None
        return HairStyleRecord(
            style_id=str(raw.get("style_id") or "").strip(),
            name=name,
            hairstyles=options,
            enabled=bool(raw.get("enabled", True)),
            sort_order=optional_int(raw.get("sort_order")) or 0,
            source=str(raw.get("source") or "custom").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "style_id": self.style_id,
            "name": self.name,
            "hairstyles": list(self.hairstyles),
            "enabled": self.enabled,
            "sort_order": self.sort_order,
            "source": self.source,
        }
