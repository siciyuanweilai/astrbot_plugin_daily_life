import json
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain

from ...clock import now as life_now
from ...life.tools import extract_json_from_text
from ...models import EmojiAssetRecord
from ...prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX


class ProactiveGestureMixin:
    async def _send_proactive_emoji_if_needed(
        self,
        target_scope: str,
        payload: dict[str, Any] | None,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> None:
        if not isinstance(payload, dict):
            return
        intent = payload.get("expression_intent")
        if not isinstance(intent, dict) or not self._proactive_bool(intent.get("send_emoji")):
            return
        emoji = await self._select_emoji_asset_for_intent(intent)
        if not emoji:
            return
        chain = self._emoji_message_chain(emoji)
        if not chain:
            return
        try:
            if not await self.send_message_if_not_recalled(
                target_scope,
                chain,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                return
            marker = getattr(self.archive, "mark_emoji_used", None)
            if callable(marker):
                await marker(emoji.id, life_now().strftime("%Y-%m-%d %H:%M"))
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时回复附加表情发送失败：{exc}")

    def _emoji_message_chain(self, emoji: Any) -> Any | None:
        path = str(getattr(emoji, "file_path", "") or "").strip()
        if not path:
            return None
        chain = MessageChain()
        if path.startswith(("http://", "https://")):
            method = getattr(chain, "url_image", None)
            if callable(method):
                return method(path)
            return None
        if not Path(path).exists():
            marker = getattr(self.archive, "upsert_emoji_asset", None)
            if callable(marker):
                self._schedule_background_task(
                    marker(
                        EmojiAssetRecord(
                            id=int(getattr(emoji, "id", 0) or 0),
                            file_hash=str(getattr(emoji, "file_hash", "") or ""),
                            file_path=path,
                            label=str(getattr(emoji, "label", "") or ""),
                            description=str(getattr(emoji, "description", "") or ""),
                            emotions=list(getattr(emoji, "emotions", []) or []),
                            source_scope=str(getattr(emoji, "source_scope", "") or ""),
                            source_message_id=str(getattr(emoji, "source_message_id", "") or ""),
                            source_url=str(getattr(emoji, "source_url", "") or ""),
                            status="missing",
                            used_count=int(getattr(emoji, "used_count", 0) or 0),
                            last_used_at=str(getattr(emoji, "last_used_at", "") or ""),
                        )
                    ),
                    label="表情素材缺失标记",
                    key=f"emoji_asset_missing:{getattr(emoji, 'id', 0) or path}",
                )
            return None
        method = getattr(chain, "file_image", None)
        if callable(method):
            return method(path)
        return None

    async def _select_emoji_asset_for_intent(self, intent: dict[str, Any]) -> Any | None:
        assets = await self.archive.get_emoji_assets(limit=12, status="ready")
        if not assets:
            return None
        provider = await self._get_proactive_provider()
        if not provider:
            return None
        candidates = [
            {
                "id": item.id,
                "label": str(getattr(item, "label", "") or ""),
                "description": str(getattr(item, "description", "") or ""),
                "emotions": list(getattr(item, "emotions", []) or [])[:6],
                "used_count": int(getattr(item, "used_count", 0) or 0),
            }
            for item in assets
        ]
        fixed = f"""我想判断是否有一个表情适合当前闲时回复的表达意图。

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON：{{"emoji_id": 0, "reason": "选择理由；没有合适表情就写0"}}

规则：
- 只有表情比纯文字更自然、更轻盈时才选择。
- 不要为了发表情而发表情；没有合适候选就 emoji_id=0。
- 选择必须基于候选描述和当前表达意图，不要靠固定关键词。"""
        dynamic = f"""表达意图：
情绪：{intent.get("emotion") or "无"}
表情意图：{intent.get("emoji_intent") or "无"}
动作意图：{intent.get("action_intent") or "无"}
理由：{intent.get("reason") or "无"}

候选表情：
{json.dumps(candidates, ensure_ascii=False)}"""
        session_id = f"daily_life_emoji_pick_{uuid.uuid4().hex[:8]}"
        try:
            provider_id = self.config.proactive.provider
            text = await self.composer._call_llm_text(
                provider,
                cache_friendly_prompt(fixed, dynamic),
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            payload = extract_json_from_text(text)
            emoji_id = int(payload.get("emoji_id") or 0) if isinstance(payload, dict) else 0
            if emoji_id <= 0:
                return None
            return next((item for item in assets if int(getattr(item, "id", 0) or 0) == emoji_id), None)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时回复表情选择跳过：{exc}")
            return None
        finally:
            await self.composer._cleanup_conversation(session_id)
