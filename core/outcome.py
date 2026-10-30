from __future__ import annotations


class ToolResultText(str):
    """保留工具可见文本，并携带不受文案变化影响的运行时状态。"""

    def __new__(
        cls, text: str, *, status: str = "ok", media: str = "", facts: dict | None = None
    ) -> "ToolResultText":
        value = super().__new__(cls, str(text or ""))
        value.status = str(status or "")
        value.media = str(media or "")
        value.facts = dict(facts or {})
        return value


__all__ = ["ToolResultText"]
