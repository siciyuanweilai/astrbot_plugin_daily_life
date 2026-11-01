"""后台回复的送达账本：生成稿不入历史，实际送达后在会话锁内提交。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReplyHistory:
    message: Any
    conversation_id: str
    checkpoint_id: str = ""
    texts: list[str] = field(default_factory=list)
    sent_count: int = 0
    committed: bool = False

    def suppress_draft(self) -> None:
        self.message._no_save = True

    def record(self, event: Any, result: Any) -> None:
        self.sent_count += 1
        chain = getattr(result, "chain", ())
        texts = [str(getattr(item, "text", "") or "") for item in chain]
        text = "\n".join(part for part in texts if part)
        if not text:
            voice = getattr(event, "_daily_life_pending_voice_receipt", None)
            if isinstance(voice, dict):
                text = str(voice.get("text", "") or "")
        if text:
            self.texts.append(text)

    async def commit(self, manager: Any, scope: str) -> None:
        """调用方持有 AstrBot 会话锁；固定原分支，不向新切换的分支追加。"""
        if self.committed or not self.texts or not self.conversation_id:
            return
        conversation = await manager.get_conversation(scope, self.conversation_id)
        if conversation is None:
            return
        history = json.loads(conversation.history or "[]")
        position = next((i for i, item in enumerate(history)
                         if item.get("role") == "_checkpoint"
                         and isinstance(item.get("content"), dict)
                         and item["content"].get("id") == self.checkpoint_id), len(history))
        history.insert(position, {"role": "assistant", "content": "\n".join(self.texts)})
        await manager.update_conversation(scope, self.conversation_id, history=history)
        self.committed = True


def capture_reply_history(event: Any, run_context: Any) -> None:
    messages = getattr(run_context, "messages", ())
    if not messages:
        return
    message = messages[-1]
    if (getattr(message, "role", "") != "assistant"
            or getattr(message, "tool_calls", None)
            or not hasattr(message, "_no_save") or message._no_save):
        return
    request = getattr(event, "_daily_life_turn_request", None)
    cid = str(getattr(getattr(request, "conversation", None), "cid", "") or "")
    checkpoint = str(getattr(event, "get_extra", lambda *_: None)("llm_checkpoint_id") or "")
    event._daily_life_reply_history = ReplyHistory(message, cid, checkpoint)


def suppress_reply_draft(event: Any) -> None:
    receipt = getattr(event, "_daily_life_reply_history", None)
    if isinstance(receipt, ReplyHistory):
        receipt.suppress_draft()
