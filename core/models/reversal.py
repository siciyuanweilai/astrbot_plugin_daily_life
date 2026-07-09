from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_int


@dataclass(slots=True)
class ReversePromptRecord:
    id: int = 0
    scope: str = ""
    prompt: str = ""
    image_path: str = ""
    title: str = ""
    keywords: list[str] = field(default_factory=list)
    ratio: str = ""
    usage: str = ""
    profile: str = ""
    source_prompt: str = ""
    created_at: str = ""

    @staticmethod
    def _text(value: Any, limit: int = 0) -> str:
        text = " ".join(str(value or "").strip().split())
        return text[:limit].strip() if limit > 0 else text

    @classmethod
    def _keywords(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("、", ",").split(",")
        else:
            try:
                raw_items = list(value or [])
            except TypeError:
                raw_items = []
        items: list[str] = []
        for item in raw_items:
            text = cls._text(item, 32)
            if text and text not in items:
                items.append(text)
        return items[:12]

    @classmethod
    def from_value(cls, value: Any) -> "ReversePromptRecord | None":
        if isinstance(value, ReversePromptRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        prompt = cls._text(raw.get("prompt") if isinstance(raw, dict) else value)
        if not prompt:
            return None
        return ReversePromptRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=cls._text(raw.get("scope"), 240),
            prompt=prompt,
            image_path=cls._text(raw.get("image_path") or raw.get("image"), 1000),
            title=cls._text(raw.get("title"), 80),
            keywords=cls._keywords(raw.get("keywords")),
            ratio=cls._text(raw.get("ratio"), 40),
            usage=cls._text(raw.get("usage"), 80),
            profile=cls._text(raw.get("profile"), 80),
            source_prompt=cls._text(raw.get("source_prompt"), 1000),
            created_at=cls._text(raw.get("created_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "prompt": self.prompt,
            "image_path": self.image_path,
            "title": self.title,
            "keywords": list(self.keywords),
            "ratio": self.ratio,
            "usage": self.usage,
            "profile": self.profile,
            "source_prompt": self.source_prompt,
            "created_at": self.created_at,
        }
