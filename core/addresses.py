"""习惯称呼是可选表达偏好，账号身份仍由平台消息确定。"""
from __future__ import annotations

import unicodedata
from typing import Any


ADDRESS_UPDATE_SCHEMA = {
    "profile_id": "本批真实用户档案编号",
    "operation": "set|clear",
    "name": "对方希望或已接受被我使用的称呼；clear时为空",
    "basis": "user_request|accepted_use",
    "source_message_id": "证明对方意愿的真实用户消息编号",
    "evidence": "该用户消息完整原文",
    "address_source_message_id": "set时实际出现该称呼的消息编号",
}
ADDRESS_UPDATE_CONTRACT = (
    "address_updates只记录用户明确表达的称呼偏好，或用户明确接受我已使用的称呼；"
    "普通昵称、关系描述和我单方面起的外号不构成称呼更新。无依据时返回空数组。"
    "撤销称呼用clear，改称呼用set；称呼出现的消息与用户表达意愿的消息分别引用实际编号。"
)


def distinct_address(value: Any, *identity_names: Any) -> str:
    text = str(value or "").strip()
    def normalized(item: Any) -> str:
        return unicodedata.normalize("NFKC", str(item or "").strip()).casefold()
    return text if text and normalized(text) not in {normalized(x) for x in identity_names} else ""


def habitual_address(person: Any) -> str:
    get = person.get if isinstance(person, dict) else lambda key: getattr(person, key, "")
    return distinct_address(get("subjective_name"), *(get(key) for key in ("name", "alias", "id", "user_id", "profile_id", "display_name")))


async def settle_batch_addresses(archive: Any, payload: dict, batch: dict) -> None:
    apply = getattr(archive, "apply_relationship_address", None)
    updates = payload.get("address_updates")
    if not callable(apply) or not isinstance(updates, list):
        return
    for update in updates[:8]:
        if isinstance(update, dict):
            await apply(update, session_id=batch.get("session_id", ""), rows=batch.get("messages", []))
