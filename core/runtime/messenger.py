from __future__ import annotations

from astrbot.api import logger

from ..media.cleanup import MediaFileCleanupMixin
from .director import RuntimeMediaDirectorMixin
from .channel import (
    RuntimeImageMediaMixin,
    RuntimeMediaCommonMixin,
    RuntimeReverseMediaMixin,
    RuntimeVideoMediaMixin,
    RuntimeVoiceMediaMixin,
)
from .voice import VoiceSwitchMixin


class RuntimeMediaMixin(
    RuntimeImageMediaMixin,
    RuntimeReverseMediaMixin,
    RuntimeVideoMediaMixin,
    RuntimeVoiceMediaMixin,
    MediaFileCleanupMixin,
    RuntimeMediaCommonMixin,
    RuntimeMediaDirectorMixin,
    VoiceSwitchMixin,
):
    pass


__all__ = ["RuntimeMediaMixin", "logger"]
