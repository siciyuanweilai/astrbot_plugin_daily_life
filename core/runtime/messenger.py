from __future__ import annotations

from astrbot.api import logger

from .director import RuntimeMediaDirectorMixin
from .channel import (
    RuntimeImageMediaMixin,
    RuntimeMediaCommonMixin,
    RuntimeVideoMediaMixin,
    RuntimeVoiceMediaMixin,
)
from .voice import VoiceSwitchMixin


class RuntimeMediaMixin(
    RuntimeImageMediaMixin,
    RuntimeVideoMediaMixin,
    RuntimeVoiceMediaMixin,
    RuntimeMediaCommonMixin,
    RuntimeMediaDirectorMixin,
    VoiceSwitchMixin,
):
    pass


__all__ = ["RuntimeMediaMixin", "logger"]
