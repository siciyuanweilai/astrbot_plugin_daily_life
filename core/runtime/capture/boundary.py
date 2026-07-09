from __future__ import annotations

from collections.abc import Iterable


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_items(values: Iterable[object] | None, limit: int = 4) -> list[str]:
    items: list[str] = []
    for value in values or []:
        text = _clean_text(value)
        if text:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def format_speaker_boundary(
    *,
    current_role_label: str = "",
    speaker_name: str = "",
    persona_hint: str = "",
    saved_persona: str = "",
    saved_tags: Iterable[object] | None = None,
    saved_story: str = "",
) -> str:
    role_label = _clean_text(current_role_label)
    speaker = _clean_text(speaker_name) or "消息发送者"
    current_persona = _clean_text(persona_hint)
    stored_persona = _clean_text(saved_persona)
    tags = _clean_items(saved_tags)
    story = _clean_text(saved_story)
    basis = "当前人设线索" if current_persona else ("已保存人设线索" if stored_persona else "证据不足")
    address_policy = "按称谓依据自然称呼" if basis != "证据不足" else "使用中性称呼"
    memory_scope = "背景参考，不覆盖当前人设线索" if current_persona else "背景参考，不单独决定性别或亲密称谓"
    self_line = f"- 当前角色：{role_label or '我'}"
    return "\n".join(
        [
            self_line,
            f"- 消息发送者：{speaker}",
            "- 记录视角：当前角色第一人称",
            f"- 当前人设线索：{current_persona or '无'}",
            f"- 已保存人设线索：{stored_persona or '无'}",
            f"- 已保存关系短标签：{'、'.join(tags) if tags else '无'}",
            f"- 已保存关系叙事：{story or '无'}",
            f"- 称谓依据：{basis}",
            f"- 旧记忆作用：{memory_scope}",
            f"- 称呼策略：{address_policy}",
        ]
    )
