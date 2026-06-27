from __future__ import annotations

import uuid
from typing import Any

from ...life.tools import extract_json_from_text


def clean_director_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").replace("```json", "").replace("```", "").split())
    return text.strip(" ：:，,。")[:limit]


class StageLensMixin:
    async def _media_director_call(self, prompt: str) -> dict[str, Any]:
        provider = await self.composer._get_provider("")
        session_id = f"daily_life_media_{uuid.uuid4().hex[:8]}"
        text = await self.composer._call_llm_text(provider, prompt, session_id, empty_retries=0)
        if not text:
            return {}
        data = extract_json_from_text(text)
        return data if isinstance(data, dict) else {}
