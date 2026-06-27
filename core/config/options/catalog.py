from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ...presets import (
    DEFAULT_DAILY_THEMES,
    DEFAULT_MOOD_COLORS,
    DEFAULT_NIGHT_HAIRSTYLES,
    DEFAULT_OUTFIT_STYLES,
    DEFAULT_SCHEDULE_TYPES,
    DEFAULT_SLEEP_STYLES,
    DEFAULT_STYLE_TO_HAIR_MAP,
)


@dataclass(slots=True)
class CatalogSettings:
    daily_themes: list[str] = field(default_factory=lambda: copy.deepcopy(DEFAULT_DAILY_THEMES))
    mood_colors: list[str] = field(default_factory=lambda: copy.deepcopy(DEFAULT_MOOD_COLORS))
    outfit_styles: list[str] = field(default_factory=lambda: copy.deepcopy(DEFAULT_OUTFIT_STYLES))
    sleep_styles: list[str] = field(default_factory=lambda: copy.deepcopy(DEFAULT_SLEEP_STYLES))
    schedule_types: list[str] = field(default_factory=lambda: copy.deepcopy(DEFAULT_SCHEDULE_TYPES))
    night_hairstyles: list[str] = field(default_factory=lambda: copy.deepcopy(DEFAULT_NIGHT_HAIRSTYLES))
    style_to_hair_map: dict[str, list[str]] = field(default_factory=lambda: copy.deepcopy(DEFAULT_STYLE_TO_HAIR_MAP))
