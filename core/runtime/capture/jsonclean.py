from __future__ import annotations

import json
from typing import Any

from ...life.tools import extract_json_from_text
from ...prompts import cache_friendly_prompt


STRICT_JSON_REPLY_RULE = "最终回复只能是一个 JSON 对象；第一个非空字符必须是 {，最后一个非空字符必须是 }，禁止在 JSON 前后写任何独白、解释、旁白或补充文字。"


def parse_json_object(text: str) -> dict[str, Any] | None:
    payload = extract_json_from_text(text)
    return payload if isinstance(payload, dict) else None


def is_pure_json_object_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw.startswith("{") or not raw.endswith("}"):
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict)


async def call_pure_json(
    composer: Any,
    provider: Any,
    prompt: str,
    session_id: str,
    *,
    primary_provider_id: str = "",
    repair_session_id: str = "",
) -> dict[str, Any] | None:
    text = await composer._call_llm_text(
        provider,
        prompt,
        session_id,
        empty_retries=0,
        primary_provider_id=primary_provider_id,
    )
    if is_pure_json_object_text(text):
        return parse_json_object(text)

    payload = parse_json_object(text)
    if not payload:
        return None

    compact_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    repair_prompt = cache_friendly_prompt(
        f"{STRICT_JSON_REPLY_RULE}\n请把下面内容改写为严格 JSON 对象本体，不要增加、删除或改写字段含义。",
        compact_json,
        dynamic_title="待修复 JSON",
    )
    repaired = await composer._call_llm_text(
        provider,
        repair_prompt,
        repair_session_id or f"{session_id}_json",
        empty_retries=0,
        primary_provider_id=primary_provider_id,
    )
    if is_pure_json_object_text(repaired):
        return parse_json_object(repaired)
    return payload
