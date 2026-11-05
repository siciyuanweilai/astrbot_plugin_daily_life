"""File facts for delivery replies; independent of requested output format."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image


def image_artifact_facts(path: Path) -> dict:
    try:
        with Image.open(path) as image:
            return {
                "format": image.format,
                "frame_count": getattr(image, "n_frames", 1),
                "width": image.width,
                "height": image.height,
            }
    except (OSError, ValueError, Image.DecompressionBombError):
        return {"inspection": "unavailable"}


async def image_delivery_context(
    path: Path, *, request_text: str, media_name: str, guidance: str = ""
) -> dict[str, str]:
    facts = await asyncio.to_thread(image_artifact_facts, path)
    if facts.get("format") == "GIF" and facts.get("frame_count", 0) > 1:
        media_name = "GIF 动图"
    return {
        "media_name": media_name,
        "request_text": request_text,
        "delivery_text": json.dumps(
            {
                "status": "sent",
                "direction": "角色向当前聊天对象发送",
                "artifact": facts,
                "visual_content_verified": False,
                "recipient_playback": "unknown",
            },
            ensure_ascii=False,
        ),
        "guidance": guidance,
    }
