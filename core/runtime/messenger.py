from __future__ import annotations

from astrbot.api import logger

from ..media.cleanup import MediaFileCleanupMixin
from .channel import (
    RuntimeImageMediaMixin,
    RuntimeMediaCommonMixin,
    RuntimePhotoSuiteMediaMixin,
    RuntimeReverseMediaMixin,
    RuntimeStyleCatalogMixin,
    RuntimeVideoMediaMixin,
    RuntimeVoiceMediaMixin,
)
from .director import RuntimeMediaDirectorMixin
from .voice import VoiceSwitchMixin


class RuntimeMediaMixin(
    RuntimeImageMediaMixin,
    RuntimePhotoSuiteMediaMixin,
    RuntimeReverseMediaMixin,
    RuntimeStyleCatalogMixin,
    RuntimeVideoMediaMixin,
    RuntimeVoiceMediaMixin,
    MediaFileCleanupMixin,
    RuntimeMediaCommonMixin,
    RuntimeMediaDirectorMixin,
    VoiceSwitchMixin,
):
    pass


__all__ = ["RuntimeMediaMixin", "logger"]
