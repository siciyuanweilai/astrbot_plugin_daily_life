from __future__ import annotations

from pathlib import Path
from typing import Any

from .picture import GeminiImageService
from .video import GrokVideoService
from .volcengine import VolcengineVoiceService


class LifeMediaService:
    def __init__(self, config: Any, data_path: Path):
        data_dir = Path(data_path).expanduser().resolve().parent
        self.image = GeminiImageService(config.image_generation, data_dir)
        self.video = GrokVideoService(config.video_generation, data_dir)
        self.voice = VolcengineVoiceService(config.voice_generation, data_dir)

    async def close(self) -> None:
        await self.image.close()
        await self.voice.close()
