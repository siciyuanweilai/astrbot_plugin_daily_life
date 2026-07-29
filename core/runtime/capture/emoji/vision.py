import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ....life.tools import extract_json_from_text
from ....models import EmojiAssetRecord
from ....models.primitive import optional_bool
from ....prompts import cache_friendly_prompt
from ...markers import LOG_PREFIX


class EmojiVisionMixin:
    @staticmethod
    def _emoji_asset_is_inline_source(path: str) -> bool:
        return str(path or "").startswith(("data:image/", "base64://"))

    def _emoji_asset_readable_source(self, path: str) -> str:
        path = str(path or "").strip()
        if path.startswith("file://"):
            path = self._local_path_from_file_uri(path)
        if not path:
            return ""
        if path.startswith(
            ("http://", "https://")
        ) or self._emoji_asset_is_inline_source(path):
            return path
        return path if Path(path).exists() else ""

    @staticmethod
    async def _call_emoji_vision_provider(
        provider: Any, prompt: str, image: str, session_id: str
    ) -> Any:
        for name, kwargs in (
            (
                "text_chat",
                {"prompt": prompt, "image_urls": [image], "session_id": session_id},
            ),
            (
                "image_chat",
                {"prompt": prompt, "image": image, "session_id": session_id},
            ),
            (
                "vision_chat",
                {"prompt": prompt, "image": image, "session_id": session_id},
            ),
        ):
            method = getattr(provider, name, None)
            if not callable(method):
                continue
            try:
                result = method(**kwargs)
            except (TypeError, NotImplementedError, AttributeError):
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except (TypeError, NotImplementedError, AttributeError):
                continue
            return result
        return None

    async def _describe_emoji_asset_with_vision(
        self,
        asset: EmojiAssetRecord,
        *,
        context_scope: str = "",
        context_message_key: str = "",
        preserve_on_failure: bool = False,
    ) -> None:
        if not asset:
            return
        update_asset = asset.status in {"pending", "reviewing"}
        if not update_asset and not context_scope:
            return
        provider = await self._get_vision_provider()
        if not provider:
            logger.debug(f"{LOG_PREFIX} 表情视觉识别跳过：未配置可用视觉模型")
            return
        if not any(
            callable(getattr(provider, name, None))
            for name in ("text_chat", "image_chat", "vision_chat")
        ):
            logger.debug(f"{LOG_PREFIX} 表情视觉识别跳过：视觉模型不支持图片输入")
            return
        path = str(asset.file_path or "").strip()
        readable_path = self._emoji_asset_readable_source(path)
        if path and not readable_path:
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
                    source_kind=asset.source_kind,
                    asset_type=asset.asset_type,
                    confidence=asset.confidence,
                    sendable=False,
                    rejected_reason="文件不存在",
                    status="missing",
                    used_count=asset.used_count,
                    last_used_at=asset.last_used_at,
                )
            )
            return
        path = readable_path or path
        prompt = cache_friendly_prompt(
            (
                "请从聊天语境理解这张表情或贴纸图片，并判断它是否适合进入可复用表情池。\n"
                "只输出 JSON："
                '{"summary":"2-40字可见内容摘要","is_emoji_asset":true,'
                '"asset_type":"emoji|sticker|reaction|meme|other","label":"短标签",'
                '"description":"一句话用途描述","emotion_category":"neutral|happy|sad|angry",'
                '"emotions":["具体语气或动作标签"],"sendable":true,"confidence":0.0,'
                '"standalone_reaction":true,"context_dependent":false,'
                '"information_dominant":false,'
                '"rejected_reason":"","status":"ready|rejected"}\n'
                "summary 只写可见事实和氛围，不写回复建议。"
                "label 和 emotions 用于后续发送匹配。判断标准是图片本身能否脱离原消息，"
                "作为明确的聊天反应重复发送，而不是图片是否带有情绪。"
                "主要传播事实、新闻、文档、商品、事件现场或长文字的信息图片不是表情；"
                "普通照片只有在人物或动物的表情、动作本身可独立形成聊天反应时才算。"
                "依赖原事件或配套文字才能理解时 context_dependent=true；"
                "主要功能是传递信息时 information_dominant=true；"
                "不确定能否作为表情发送时 sendable=false。"
            )
        )
        session_id = f"daily_life_emoji_vision_{uuid.uuid4().hex[:8]}"
        try:
            result = await self._call_emoji_vision_provider(
                provider, prompt, path, session_id
            )
            if result is None:
                logger.debug(f"{LOG_PREFIX} 表情视觉识别跳过：视觉模型未返回结果")
                return
            payload = extract_json_from_text(self._completion_text(result))
            if not isinstance(payload, dict):
                if not preserve_on_failure:
                    await self._mark_emoji_asset_failed(
                        asset, "视觉模型未返回有效结果"
                    )
                return
            self._apply_visual_context_summary(
                context_scope,
                context_message_key,
                payload,
                asset.file_hash,
            )
            if not update_asset:
                return
            status = self._emoji_asset_review_status(asset, payload)
            rejected_reason = (
                "" if status == "ready" else self._emoji_asset_rejected_reason(payload)
            )
            if status == "ready" and not await self._emoji_can_accept_ready_asset(
                exclude_hash=asset.file_hash
            ):
                status = "rejected"
                rejected_reason = "表情库已满"
            updated = EmojiAssetRecord.from_value(
                {
                    "id": asset.id,
                    "file_hash": asset.file_hash,
                    "file_path": asset.file_path,
                    "label": self._str_payload(payload.get("label")),
                    "description": self._str_payload(payload.get("description")),
                    "emotion_category": self._str_payload(
                        payload.get("emotion_category")
                    ),
                    "emotions": self._list_payload(payload.get("emotions")),
                    "source_scope": asset.source_scope,
                    "source_message_id": asset.source_message_id,
                    "source_url": asset.source_url,
                    "source_kind": (
                        "verified"
                        if status == "ready"
                        and asset.source_kind in {"review", "verified"}
                        else asset.source_kind
                    ),
                    "asset_type": self._str_payload(payload.get("asset_type"))
                    or asset.asset_type,
                    "confidence": self._emoji_asset_confidence(asset, payload),
                    "sendable": status == "ready",
                    "rejected_reason": rejected_reason,
                    "status": status,
                }
            )
            if updated:
                saved = await self.archive.upsert_emoji_asset(updated)
                if saved and saved.status == "ready":
                    await self._prune_extra_ready_emoji_assets()
                logger.debug(
                    f"{LOG_PREFIX} 表情素材识别完成：{updated.label or updated.file_hash}，状态={updated.status}"
                )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 表情视觉识别跳过：{exc}")
            if not preserve_on_failure:
                await self._mark_emoji_asset_failed(asset, str(exc)[:120])
        finally:
            await self.close_text_session(session_id)

    async def _save_plain_image_emoji_candidate(
        self,
        payload: dict[str, Any],
        *,
        image: str,
        fingerprint: str,
        context_scope: str,
        context_message_key: str,
        cache_sources: list[str] | None = None,
    ) -> None:
        if not self._emoji_auto_collect_enabled():
            return
        if optional_bool(payload.get("is_emoji_asset")) is not True:
            return
        image = str(image or "").strip()
        fingerprint = str(fingerprint or "").strip()
        if not image or not fingerprint:
            return
        existing = await self.archive.get_emoji_asset_by_hash(fingerprint)
        if (
            existing
            and str(existing.status or "") in self.EMOJI_ASSET_REJECTED_STATUSES
        ):
            return
        if existing and str(existing.status or "") == "ready":
            return

        candidate = EmojiAssetRecord(
            file_hash=fingerprint,
            file_path=image,
            source_scope=context_scope,
            source_message_id=context_message_key,
            source_url=image if self._emoji_asset_is_remote(image) else "",
            source_kind="review",
            asset_type="image",
            sendable=False,
            status="reviewing",
        )
        status = self._emoji_asset_review_status(candidate, payload)
        if status != "ready":
            logger.debug(
                f"{LOG_PREFIX} 图片未收集为表情：{self._emoji_asset_rejected_reason(payload)}"
            )
            return
        if not await self._emoji_can_accept_ready_asset(exclude_hash=fingerprint):
            logger.debug(f"{LOG_PREFIX} 图片未收集为表情：表情库已满")
            return

        cached_path = None
        cached_source = ""
        for source in self._emoji_asset_cache_candidates(image, cache_sources):
            cached_path = await self._cache_emoji_asset_path(
                {"path": source}, fingerprint
            )
            if cached_path:
                cached_source = source
                break
        if not cached_path:
            logger.debug(
                f"{LOG_PREFIX} 图片未收集为表情：素材缓存失败（未取得可读原图）"
            )
            return
        updated = EmojiAssetRecord.from_value(
            {
                "file_hash": fingerprint,
                "file_path": str(cached_path),
                "label": self._str_payload(payload.get("label")),
                "description": self._str_payload(payload.get("description")),
                "emotion_category": self._str_payload(payload.get("emotion_category")),
                "emotions": self._list_payload(payload.get("emotions")),
                "source_scope": context_scope,
                "source_message_id": context_message_key,
                "source_url": cached_source
                if self._emoji_asset_is_remote(cached_source)
                else "",
                "source_kind": "verified",
                "asset_type": self._str_payload(payload.get("asset_type")) or "image",
                "confidence": self._emoji_asset_confidence(candidate, payload),
                "sendable": True,
                "rejected_reason": "",
                "status": "ready",
            }
        )
        if updated:
            saved = await self.archive.upsert_emoji_asset(updated)
            if saved and saved.status == "ready":
                await self._prune_extra_ready_emoji_assets()
            logger.debug(
                f"{LOG_PREFIX} 图片已收集为表情素材：{updated.label or updated.file_hash}"
            )

    @staticmethod
    def _emoji_asset_cache_candidates(
        primary: str, extra_sources: list[str] | None = None
    ) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        for source in [primary, *(extra_sources or [])]:
            text = str(source or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            candidates.append(text)
        return candidates

    async def _emoji_asset_prepare_vision_source(
        self,
        candidates: list[str],
        fingerprint: str,
    ) -> tuple[str, list[str]]:
        normalized = self._emoji_asset_cache_candidates("", candidates)
        if not normalized:
            return "", []
        fingerprint = str(fingerprint or "").strip()
        if not fingerprint:
            return "", normalized
        for source in normalized:
            cached_path = await self._cache_emoji_asset_path(
                {"path": source}, fingerprint
            )
            if cached_path:
                cached_source = str(cached_path)
                return cached_source, self._emoji_asset_cache_candidates(
                    cached_source, normalized
                )
        return "", normalized

    def _emoji_asset_confidence(
        self, asset: EmojiAssetRecord, payload: dict[str, Any]
    ) -> float:
        value = self._float_payload(
            payload.get("confidence"), 1.0 if asset.source_kind == "trusted" else 0.0
        )
        return max(0.0, min(value, 1.0))

    def _emoji_asset_review_status(
        self, asset: EmojiAssetRecord, payload: dict[str, Any]
    ) -> str:
        raw_status = self._str_payload(payload.get("status"), "ready").lower()
        if raw_status in {"disabled", "failed", "rejected"}:
            return "rejected"
        sendable = optional_bool(payload.get("sendable"))
        is_emoji_asset = optional_bool(payload.get("is_emoji_asset"))
        label = self._str_payload(payload.get("label"))
        emotions = [
            self._str_payload(item)
            for item in self._list_payload(payload.get("emotions"))
            if self._str_payload(item)
        ]
        has_metadata = bool(label and emotions)
        if asset.source_kind in {"review", "verified"}:
            if is_emoji_asset is not True or sendable is not True:
                return "rejected"
            asset_type = self._str_payload(payload.get("asset_type")).lower()
            if asset_type not in self.EMOJI_ASSET_ALLOWED_REACTION_TYPES:
                return "rejected"
            if optional_bool(payload.get("standalone_reaction")) is not True:
                return "rejected"
            if optional_bool(payload.get("context_dependent")) is not False:
                return "rejected"
            if optional_bool(payload.get("information_dominant")) is not False:
                return "rejected"
            if (
                self._emoji_asset_confidence(asset, payload)
                < self.EMOJI_ASSET_MIN_PLAIN_IMAGE_CONFIDENCE
            ):
                return "rejected"
            return "ready" if has_metadata else "rejected"
        if is_emoji_asset is False or sendable is False:
            return "rejected"
        return "ready" if has_metadata else "rejected"

    def _emoji_asset_rejected_reason(self, payload: dict[str, Any]) -> str:
        reason = self._str_payload(payload.get("rejected_reason"))
        if reason:
            return reason[:160]
        if optional_bool(payload.get("is_emoji_asset")) is not True:
            return "不是可复用的聊天表情"
        if optional_bool(payload.get("sendable")) is not True:
            return "不适合直接作为聊天反应发送"
        asset_type = self._str_payload(payload.get("asset_type")).lower()
        if asset_type not in self.EMOJI_ASSET_ALLOWED_REACTION_TYPES:
            return "素材类型不属于表情"
        if optional_bool(payload.get("standalone_reaction")) is not True:
            return "不是可独立使用的聊天反应"
        if optional_bool(payload.get("context_dependent")) is not False:
            return "图片含义依赖原始语境"
        if optional_bool(payload.get("information_dominant")) is not False:
            return "图片主要用于传递信息"
        confidence = self._float_payload(payload.get("confidence"), 0.0)
        if confidence < self.EMOJI_ASSET_MIN_PLAIN_IMAGE_CONFIDENCE:
            return "表情识别置信度不足"
        if not self._str_payload(payload.get("label")) or not self._list_payload(
            payload.get("emotions")
        ):
            return "表情标签信息不完整"
        return "视觉验收未通过"

    async def _describe_visual_context_with_vision(
        self,
        provider: Any,
        path: str,
        context_scope: str,
        context_message_key: str,
        fingerprint: str,
        cache_sources: list[str] | None = None,
    ) -> None:
        path = str(path or "").strip()
        readable_path = self._emoji_asset_readable_source(path)
        if path and not readable_path:
            return
        path = readable_path or path
        prompt = cache_friendly_prompt(
            (
                "请从第一人称生活视角理解这张聊天图片。\n"
                "只给后续聊天上下文看的图片内容短摘要，写可见事实、可见文字和整体氛围。"
                "同时判断它是否适合进入可复用表情池。\n"
                "只输出 JSON："
                '{"summary":"图片内容短摘要","is_emoji_asset":false,'
                '"asset_type":"emoji|sticker|reaction|meme|other","label":"短标签",'
                '"description":"一句话用途描述","emotion_category":"neutral|happy|sad|angry",'
                '"emotions":["具体语气或动作标签"],"sendable":false,"confidence":0.0,'
                '"standalone_reaction":false,"context_dependent":false,'
                '"information_dominant":false,'
                '"rejected_reason":"","status":"ready|rejected"}\n'
                "summary 不写回复建议。判断表情时看图片本身能否脱离原消息，作为明确的聊天反应重复发送，"
                "不能因为图片有情绪就判为表情。主要传播事实、新闻、文档、商品、事件现场或长文字的"
                "信息图片不是表情；普通照片只有在人物或动物的表情、动作本身可独立形成聊天反应时才算。"
                "依赖原事件或配套文字才能理解时 context_dependent=true；"
                "主要功能是传递信息时 information_dominant=true。"
            )
        )
        session_id = f"daily_life_visual_{uuid.uuid4().hex[:8]}"
        try:
            result = await self._call_emoji_vision_provider(
                provider, prompt, path, session_id
            )
            if result is None:
                logger.debug(f"{LOG_PREFIX} 图片上下文识别跳过：视觉模型未返回结果")
                return
            payload = extract_json_from_text(self._completion_text(result))
            if isinstance(payload, dict):
                summary = self._visual_context_summary_from_payload(payload)
                logger.debug(f"{LOG_PREFIX} 图片上下文识别完成：{summary or '已解析'}")
                self._apply_visual_context_summary(
                    context_scope,
                    context_message_key,
                    payload,
                    fingerprint,
                )
                await self._save_plain_image_emoji_candidate(
                    payload,
                    image=path,
                    fingerprint=fingerprint,
                    context_scope=context_scope,
                    context_message_key=context_message_key,
                    cache_sources=cache_sources,
                )
            else:
                logger.debug(f"{LOG_PREFIX} 图片上下文识别跳过：视觉模型未返回有效结果")
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 图片上下文识别跳过：{exc}")
        finally:
            await self.close_text_session(session_id)

    async def _mark_emoji_asset_failed(
        self, asset: EmojiAssetRecord, reason: str = ""
    ) -> None:
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
                source_kind=asset.source_kind,
                asset_type=asset.asset_type,
                confidence=asset.confidence,
                sendable=False,
                rejected_reason=reason,
                status="failed",
                used_count=asset.used_count,
                last_used_at=asset.last_used_at,
            )
        )
