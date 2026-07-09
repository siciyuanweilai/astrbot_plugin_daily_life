import datetime
import json
import random
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain

from ...clock import now as life_now
from ...life.tools import extract_json_from_text
from ...models import EmojiAssetRecord, ExpressionIntentRecord
from ...models.phrasing import emoji_category_tag
from ...prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX


class ProactiveGestureMixin:
    _EMOJI_MIN_CLEAR_SCORE = 1.4
    _EMOJI_CLOSE_SCORE_GAP = 0.75
    _EMOJI_CLOSE_POOL_LIMIT = 16
    _EMOJI_MODEL_CANDIDATE_LIMIT = 8

    def _emoji_sent_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_emoji_sent_state", None)
        if not isinstance(store, dict):
            store = {}
            self._emoji_sent_state = store
        return store

    def _note_emoji_sent(
        self,
        scope: str,
        emoji: Any,
        *,
        source: str = "",
        source_message_id: str = "",
    ) -> None:
        scope = str(scope or "").strip()
        if not scope:
            return
        self._emoji_sent_store()[scope] = {
            "last_asset_key": self._emoji_asset_key(emoji),
            "last_source_message_id": str(source_message_id or "").strip(),
            "source": str(source or "").strip(),
        }

    def _emoji_duplicate_skip_reason(self, scope: str, emoji: Any, *, source_message_id: str = "") -> str:
        source_message_id = str(source_message_id or "").strip()
        if not source_message_id:
            return ""
        item = self._emoji_sent_store().get(str(scope or "").strip())
        if not item or item.get("last_source_message_id") != source_message_id:
            return ""
        asset_key = self._emoji_asset_key(emoji)
        if asset_key and asset_key == item.get("last_asset_key"):
            return "同一轮已发送过这个表情"
        return ""

    @staticmethod
    def _emoji_asset_key(emoji: Any) -> str:
        for attr in ("id", "file_hash", "file_path"):
            value = str(getattr(emoji, attr, "") or "").strip()
            if value and value != "0":
                return f"{attr}:{value}"
        return ""

    async def life_emoji_send(
        self,
        event: Any,
        *,
        intent: str = "",
        emotion: str = "",
        emotion_category: str = "",
        decision_reason: str = "",
    ) -> str:
        scope = self._emoji_scope(event)
        if not scope:
            return "当前会话不可发送表情。"
        intent_payload = {
            "send_emoji": True,
            "emotion": str(emotion or "").strip(),
            "emotion_category": str(emotion_category or "").strip(),
            "emoji_intent": str(intent or "").strip(),
            "action_intent": str(intent or "").strip(),
            "reason": str(decision_reason or intent or "").strip(),
        }
        emoji = await self._select_emoji_asset_for_intent(intent_payload, scope=scope)
        if emoji is None:
            return "没有找到可发送的表情素材。"

        chain = self._emoji_message_chain(emoji)
        items = list(getattr(chain, "chain", None) or getattr(chain, "items", None) or []) if chain else []
        if not items:
            return "表情素材暂时不可发送。"

        message_id = self._event_message_id(event)
        skip_reason = self._emoji_duplicate_skip_reason(scope, emoji, source_message_id=message_id)
        if skip_reason:
            return skip_reason
        if not await self.send_message_if_not_recalled(
            scope,
            chain,
            source_event=event,
            source_message_id=message_id,
        ):
            return "原消息已撤回，已取消表情发送。"

        await self._mark_emoji_used(
            event,
            emoji,
            intent_payload,
            scope=scope,
            reply_text=f"[表情：{getattr(emoji, 'label', '') or getattr(emoji, 'file_hash', '')}]",
            message_id=message_id,
            source="tool",
        )
        note = getattr(self, "note_structured_bot_message", None)
        if callable(note):
            note(
                scope,
                f"[表情：{getattr(emoji, 'label', '') or '已发送'}]",
                source_event=event,
                media="表情",
            )
        label = str(getattr(emoji, "label", "") or "").strip()
        logger.debug(f"{LOG_PREFIX} 已通过表情工具发送素材：{label or getattr(emoji, 'file_hash', '')}")
        return f"表情已发送{f'：{label}' if label else '。'}"

    def _emoji_scope(self, event: Any) -> str:
        getter = getattr(self, "_event_session_id", None)
        scope = getter(event) if callable(getter) else ""
        if not scope:
            scope_getter = getattr(self, "_proactive_scope_key", None)
            scope = scope_getter(event) if callable(scope_getter) else ""
        return str(scope or "").strip()

    async def _mark_emoji_used(
        self,
        event: Any,
        emoji: Any,
        intent: dict[str, Any],
        *,
        scope: str = "",
        reply_text: str = "",
        message_id: str = "",
        source: str = "tool",
    ) -> None:
        self._note_emoji_sent(scope, emoji, source=source, source_message_id=message_id)
        emoji_id = int(getattr(emoji, "id", 0) or 0)
        marker = getattr(self.archive, "mark_emoji_used", None)
        if callable(marker) and emoji_id > 0:
            await marker(emoji_id, life_now().strftime("%Y-%m-%d %H:%M"))
        saver = getattr(self.archive, "save_expression_intent", None)
        if not callable(saver):
            return
        if not message_id:
            message_getter = getattr(self, "_event_message_id", None)
            message_id = message_getter(event) if callable(message_getter) else ""
        await saver(
            ExpressionIntentRecord.from_value(
                {
                    **intent,
                    "scope": scope,
                    "message_id": message_id,
                    "reply_text": reply_text,
                    "send_emoji": True,
                    "emoji_id": emoji_id,
                    "source": source,
                },
                source=source,
            )
        )

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
        emoji = await self._select_emoji_asset_for_intent(intent, scope=target_scope)
        if not emoji:
            return
        skip_reason = self._emoji_duplicate_skip_reason(target_scope, emoji, source_message_id=source_message_id)
        if skip_reason:
            logger.debug(f"{LOG_PREFIX} 闲时回复表情跳过：{skip_reason}")
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
            self._note_emoji_sent(
                target_scope,
                emoji,
                source="proactive_reply",
                source_message_id=source_message_id,
            )
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
                            source_kind=str(getattr(emoji, "source_kind", "") or "trusted"),
                            asset_type=str(getattr(emoji, "asset_type", "") or ""),
                            confidence=float(getattr(emoji, "confidence", 0.0) or 0.0),
                            sendable=False,
                            rejected_reason="文件不存在",
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

    @staticmethod
    def _emoji_terms_from_value(value: Any) -> set[str]:
        if isinstance(value, (list, tuple, set)):
            terms: set[str] = set()
            for item in value:
                terms.update(ProactiveGestureMixin._emoji_terms_from_value(item))
            return terms
        text = str(value or "").strip().lower()
        if not text:
            return set()
        for separator in ("，", ",", "、", "/", "|", "；", ";", "。", ".", "！", "!", "？", "?", "：", " ", "\n", "\t"):
            text = text.replace(separator, " ")
        return {part.strip() for part in text.split(" ") if part.strip()}

    @classmethod
    def _emoji_current_intent_terms(cls, intent: dict[str, Any]) -> set[str]:
        terms: set[str] = set()
        category = emoji_category_tag(intent.get("emotion_category"))
        if category:
            terms.add(category)
        for key in ("emotion", "emoji_intent", "action_intent"):
            terms.update(cls._emoji_terms_from_value(intent.get(key)))
        return terms

    @classmethod
    def _emoji_recent_intent_terms(cls, intent: dict[str, Any]) -> set[str]:
        terms: set[str] = set()
        for item in intent.get("_recent_intents") or []:
            if not isinstance(item, dict):
                continue
            category = emoji_category_tag(item.get("emotion_category"))
            if category:
                terms.add(category)
            for key in ("emotion", "emoji_intent", "action_intent"):
                terms.update(cls._emoji_terms_from_value(item.get(key)))
        return terms

    @classmethod
    def _emoji_intent_terms(cls, intent: dict[str, Any]) -> set[str]:
        return cls._emoji_current_intent_terms(intent) | cls._emoji_recent_intent_terms(intent)

    @classmethod
    def _emoji_asset_terms(cls, asset: Any) -> set[str]:
        terms = cls._emoji_terms_from_value(getattr(asset, "emotions", None))
        terms.update(cls._emoji_terms_from_value(getattr(asset, "label", "")))
        terms.update(cls._emoji_terms_from_value(getattr(asset, "description", "")))
        return terms

    @classmethod
    def _emoji_asset_is_sendable(cls, asset: Any) -> bool:
        if str(getattr(asset, "status", "") or "") != "ready":
            return False
        if getattr(asset, "sendable", True) is False:
            return False
        if not str(getattr(asset, "file_path", "") or "").strip():
            return False
        return bool(cls._emoji_asset_terms(asset))

    @staticmethod
    def _emoji_recency_penalty(last_used_at: Any, now: datetime.datetime) -> float:
        text = str(last_used_at or "").strip()
        if not text:
            return 0.0
        try:
            used_at = datetime.datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            return 0.0
        elapsed = (now - used_at).total_seconds()
        if elapsed < 0:
            return 0.0
        if elapsed <= 30 * 60:
            return 1.0
        if elapsed <= 6 * 60 * 60:
            return 0.35
        return 0.0

    @staticmethod
    def _emoji_term_affinity(target_terms: set[str], asset_terms: set[str]) -> float:
        if not target_terms or not asset_terms:
            return 0.0
        score = float(len(target_terms & asset_terms))
        for target in target_terms - asset_terms:
            if len(target) < 2:
                continue
            for asset in asset_terms:
                if len(asset) >= 2 and (target in asset or asset in target):
                    score += 0.6
                    break
                if ProactiveGestureMixin._emoji_common_run_length(target, asset) >= 2:
                    score += 0.45
                    break
        return score

    @staticmethod
    def _emoji_common_run_length(left: str, right: str) -> int:
        left = str(left or "")
        right = str(right or "")
        if not left or not right:
            return 0
        previous = [0] * (len(right) + 1)
        best = 0
        for left_char in left:
            current = [0]
            for index, right_char in enumerate(right, start=1):
                value = previous[index - 1] + 1 if left_char == right_char else 0
                current.append(value)
                if value > best:
                    best = value
            previous = current
        return best

    def _rank_emoji_assets_for_intent(self, assets: list[Any], intent: dict[str, Any]) -> list[tuple[float, Any]]:
        current_terms = self._emoji_current_intent_terms(intent)
        recent_terms = self._emoji_recent_intent_terms(intent)
        now = life_now()
        ranked: list[tuple[float, Any]] = []
        for asset in assets:
            asset_terms = self._emoji_asset_terms(asset)
            affinity = self._emoji_term_affinity(current_terms, asset_terms)
            affinity += self._emoji_term_affinity(recent_terms, asset_terms) * 0.35
            if (current_terms or recent_terms) and asset_terms and affinity <= 0:
                continue
            used_count = max(0, int(getattr(asset, "used_count", 0) or 0))
            score = 1.0 + affinity * 1.8 - min(used_count, 20) * 0.04
            score -= self._emoji_recency_penalty(getattr(asset, "last_used_at", ""), now)
            if score > 0:
                ranked.append((score, asset))
        ranked.sort(key=lambda item: (item[0], -int(getattr(item[1], "used_count", 0) or 0)), reverse=True)
        return ranked

    @classmethod
    def _close_emoji_candidates(cls, ranked: list[tuple[float, Any]]) -> list[Any]:
        if not ranked:
            return []
        top_score = ranked[0][0]
        min_score = max(cls._EMOJI_MIN_CLEAR_SCORE, top_score - cls._EMOJI_CLOSE_SCORE_GAP)
        return [
            asset
            for score, asset in ranked[: cls._EMOJI_CLOSE_POOL_LIMIT]
            if score >= min_score
        ]

    @classmethod
    def _sample_emoji_candidates_for_model(cls, ranked: list[tuple[float, Any]]) -> list[Any]:
        candidates = cls._close_emoji_candidates(ranked)
        if len(candidates) <= 1:
            return candidates
        count = min(len(candidates), cls._EMOJI_MODEL_CANDIDATE_LIMIT)
        return random.sample(candidates, count)

    async def _select_emoji_asset_for_intent(self, intent: dict[str, Any], *, scope: str = "") -> Any | None:
        assets = await self.archive.get_emoji_assets(
            limit=self._emoji_send_candidate_limit(),
            status="ready",
            sendable_only=True,
        )
        assets = [asset for asset in assets if self._emoji_asset_is_sendable(asset)]
        if not assets:
            return None
        intent = dict(intent)
        await self._attach_recent_expression_intents(intent, scope=scope)
        ranked_assets = self._rank_emoji_assets_for_intent(assets, intent)
        if not ranked_assets:
            return None
        provider = await self._get_proactive_provider()
        if not provider:
            return None
        candidate_assets = self._sample_emoji_candidates_for_model(ranked_assets)
        if not candidate_assets:
            return None
        candidates = [
            {
                "id": item.id,
                "label": str(getattr(item, "label", "") or ""),
                "description": str(getattr(item, "description", "") or ""),
                "emotions": list(getattr(item, "emotions", []) or [])[:6],
                "used_count": int(getattr(item, "used_count", 0) or 0),
                "recently_used": self._emoji_recency_penalty(getattr(item, "last_used_at", ""), life_now()) > 0,
            }
            for item in candidate_assets
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
            return next((item for item in candidate_assets if int(getattr(item, "id", 0) or 0) == emoji_id), None)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时回复表情选择跳过：{exc}")
            return None
        finally:
            await self.composer._cleanup_conversation(session_id)

    async def _attach_recent_expression_intents(self, intent: dict[str, Any], *, scope: str = "") -> None:
        getter = getattr(self.archive, "get_expression_intents", None)
        if not callable(getter):
            return
        try:
            recent = await getter(limit=3, scope=scope)
        except Exception:
            return
        extra: list[dict[str, Any]] = []
        for item in recent:
            extra.append(
                {
                    "emotion": getattr(item, "emotion", ""),
                    "emotion_category": getattr(item, "emotion_category", ""),
                    "emoji_intent": getattr(item, "emoji_intent", ""),
                    "action_intent": getattr(item, "action_intent", ""),
                }
            )
        if extra:
            intent["_recent_intents"] = extra
