import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ....life.tools import extract_json_from_text
from ....models import EmojiAssetRecord
from ....prompts import cache_friendly_prompt
from ...markers import LOG_PREFIX


class EmojiVisionMixin:

    async def _describe_emoji_asset_with_vision(
        self,
        asset: EmojiAssetRecord,
        *,
        context_scope: str = "",
        context_message_key: str = "",
    ) -> None:
        if not asset:
            return
        update_asset = asset.status == "pending"
        if not update_asset and not context_scope:
            return
        provider = await self._get_vision_provider()
        if not provider:
            return
        vision_call = getattr(provider, "image_chat", None) or getattr(provider, "vision_chat", None)
        if not callable(vision_call):
            return
        path = str(asset.file_path or "").strip()
        if path and not path.startswith(("http://", "https://")) and not Path(path).exists():
            await self.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    id=asset.id,
                    file_hash=asset.file_hash,
                    file_path=asset.file_path,
                    label=asset.label,
                    description=asset.description,
                    emotions=asset.emotions,
                    source_scope=asset.source_scope,
                    source_message_id=asset.source_message_id,
                    source_url=asset.source_url,
                    status="missing",
                    used_count=asset.used_count,
                    last_used_at=asset.last_used_at,
                )
            )
            return
        prompt = cache_friendly_prompt(
            (
                "请从第一人称生活视角理解这张聊天图片。\n"
                "同时给出两类信息：\n"
                "- summary：给后续聊天上下文看的图片内容短摘要，只写可见事实和氛围，12-40 字；不要加入猜测、隐私推断或回复建议。\n"
                "- label/description/emotions：作为可复用表情或图片素材的表达用途。\n"
                '只输出 JSON：{"summary":"图片内容短摘要","label":"短标签","description":"一句描述","emotions":["情绪词"],"status":"ready"}'
            )
        )
        session_id = f"daily_life_emoji_vision_{uuid.uuid4().hex[:8]}"
        try:
            result = vision_call(prompt=prompt, image=path, session_id=session_id)
            if hasattr(result, "__await__"):
                result = await result
            payload = extract_json_from_text(self._completion_text(result))
            if not isinstance(payload, dict):
                await self._mark_emoji_asset_failed(asset, "视觉模型未返回有效结构")
                return
            self._apply_visual_context_summary(
                context_scope,
                context_message_key,
                payload,
                asset.file_hash,
            )
            if not update_asset:
                return
            status = self._str_payload(payload.get("status"), "ready") or "ready"
            if status not in {"ready", "disabled", "failed"}:
                status = "ready"
            updated = EmojiAssetRecord(
                id=asset.id,
                file_hash=asset.file_hash,
                file_path=asset.file_path,
                label=self._str_payload(payload.get("label")),
                description=self._str_payload(payload.get("description")),
                emotions=[
                    self._str_payload(item)
                    for item in self._list_payload(payload.get("emotions"))
                    if self._str_payload(item)
                ][:8],
                source_scope=asset.source_scope,
                source_message_id=asset.source_message_id,
                source_url=asset.source_url,
                status=status,
            )
            await self.archive.upsert_emoji_asset(updated)
        except Exception as e:
            logger.debug(f"{LOG_PREFIX} 表情视觉识别跳过：{e}")
            await self._mark_emoji_asset_failed(asset, str(e)[:120])
        finally:
            await self.composer._cleanup_conversation(session_id)

    async def _describe_visual_context_with_vision(
        self,
        vision_call,
        path: str,
        context_scope: str,
        context_message_key: str,
        fingerprint: str,
    ) -> None:
        path = str(path or "").strip()
        if path and not path.startswith(("http://", "https://")) and not Path(path).exists():
            return
        prompt = cache_friendly_prompt(
            (
                "请从第一人称生活视角理解这张聊天图片。\n"
                "只给后续聊天上下文看的图片内容短摘要，写可见事实、可见文字和整体氛围；"
                "不要加入猜测、隐私推断、回复建议或长期记忆判断。\n"
                '只输出 JSON：{"summary":"图片内容短摘要"}'
            )
        )
        session_id = f"daily_life_visual_{uuid.uuid4().hex[:8]}"
        try:
            result = vision_call(prompt=prompt, image=path, session_id=session_id)
            if hasattr(result, "__await__"):
                result = await result
            payload = extract_json_from_text(self._completion_text(result))
            if isinstance(payload, dict):
                self._apply_visual_context_summary(
                    context_scope,
                    context_message_key,
                    payload,
                    fingerprint,
                )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 图片上下文识别跳过：{exc}")
        finally:
            await self.composer._cleanup_conversation(session_id)

    async def _mark_emoji_asset_failed(self, asset: EmojiAssetRecord, reason: str = "") -> None:
        await self.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=asset.id,
                file_hash=asset.file_hash,
                file_path=asset.file_path,
                label=asset.label,
                description=reason or asset.description,
                emotions=asset.emotions,
                source_scope=asset.source_scope,
                source_message_id=asset.source_message_id,
                source_url=asset.source_url,
                status="failed",
                used_count=asset.used_count,
                last_used_at=asset.last_used_at,
            )
        )
