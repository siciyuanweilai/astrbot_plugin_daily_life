from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ...life.tools import extract_json_from_text
from ...prompts import cache_friendly_prompt

STRICT_JSON_REPLY_RULE = "最终回复只能是一个 JSON 对象；第一个非空字符必须是 {，最后一个非空字符必须是 }，禁止在 JSON 前后写任何独白、解释、旁白或补充文字。"
MAX_JSON_REPAIR_CHARS = 20_000


class JsonContractError(ValueError):
    """模型输出无法满足调用方声明的 JSON 契约。"""


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
    model_gateway: Any,
    provider: Any,
    prompt: str,
    session_id: str,
    *,
    primary_provider_id: str = "",
    repair_session_id: str = "",
    propagate_non_retryable: bool = False,
    strict: bool = False,
    validator: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    fallback: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    def fallback_value() -> dict[str, Any] | None:
        value = fallback() if callable(fallback) else fallback
        return dict(value) if isinstance(value, dict) else None

    def validate(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if validator is None:
            return dict(value)
        try:
            normalized = validator(dict(value))
        except Exception as exc:
            raise JsonContractError(f"JSON 字段契约校验失败：{exc}") from exc
        return dict(normalized) if isinstance(normalized, dict) else None

    call_options = {
        "empty_retries": 0,
        "primary_provider_id": primary_provider_id,
    }
    if propagate_non_retryable:
        call_options["propagate_non_retryable"] = True
    text = await model_gateway.call_text_model(
        provider,
        prompt,
        session_id,
        **call_options,
    )
    if is_pure_json_object_text(text):
        payload = validate(json.loads(str(text).strip()))
        if payload is not None:
            return payload
        if strict:
            return fallback_value()

    payload = None if strict else parse_json_object(text)
    raw = str(text or "").strip()
    if not payload and not raw:
        return fallback_value()

    repair_source = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if payload
        else raw[:MAX_JSON_REPAIR_CHARS]
    )
    repair_instruction = (
        "请把下面内容改写为严格 JSON 对象本体，不要增加、删除或改写字段含义。"
        if payload
        else "请只修复下面模型输出的 JSON 语法和包裹格式，保留现有字段和值，不补造缺失事实。"
    )
    repair_prompt = cache_friendly_prompt(
        f"{STRICT_JSON_REPLY_RULE}\n{repair_instruction}",
        repair_source,
        dynamic_title="待修复 JSON",
    )
    repaired = await model_gateway.call_text_model(
        provider,
        repair_prompt,
        repair_session_id or session_id,
        **call_options,
    )
    repaired_payload = None
    if is_pure_json_object_text(repaired):
        repaired_payload = validate(json.loads(str(repaired).strip()))
    if isinstance(repaired_payload, dict):
        return repaired_payload
    if strict:
        return fallback_value()
    return payload
