import datetime
import json
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

try:
    from astrbot.core.pipeline.process_stage import follow_up as _astrbot_follow_up
except Exception:
    _astrbot_follow_up = None


class ProactiveGestureMixin:
    _EMOJI_MODEL_CANDIDATE_LIMIT = 8
    _EMOJI_RECENT_SENT_EXCLUSION_LIMIT = 5
    _SEMANTIC_EMOJI_ATTEMPTED_ATTR = "_daily_life_semantic_emoji_attempted"

    def _emoji_config_value(self, name: str, default: Any) -> Any:
        settings = getattr(getattr(self, "config", None), "emoji", None)
        return getattr(settings, name, default)

    def _emoji_auto_send_allowed(self, source: str) -> bool:
        if not bool(self._emoji_config_value("auto_send_enabled", True)):
            return False
        source_field = {
            "regular_reply": "send_on_regular_reply",
            "proactive_reply": "send_on_proactive_reply",
            "private_revisit": "send_on_private_revisit",
            "proactive_commitment": "send_on_commitment",
        }.get(str(source or "").strip())
        return source_field is None or bool(self._emoji_config_value(source_field, True))

    def _emoji_tool_send_allowed(self) -> bool:
        return bool(self._emoji_config_value("tool_send_enabled", True))

    def _emoji_send_cooldown_seconds(self) -> int:
        try:
            return max(0, int(self._emoji_config_value("send_cooldown_seconds", 0)))
        except (TypeError, ValueError):
            return 0

    def _emoji_recent_sent_exclusion_limit(self) -> int:
        try:
            return max(
                0,
                int(
                    self._emoji_config_value(
                        "recent_sent_exclusion_limit",
                        self._EMOJI_RECENT_SENT_EXCLUSION_LIMIT,
                    )
                ),
            )
        except (TypeError, ValueError):
            return self._EMOJI_RECENT_SENT_EXCLUSION_LIMIT

    def _emoji_model_candidate_limit(self) -> int:
        try:
            return max(
                1,
                int(
                    self._emoji_config_value(
                        "semantic_candidate_limit", self._EMOJI_MODEL_CANDIDATE_LIMIT
                    )
                ),
            )
        except (TypeError, ValueError):
            return self._EMOJI_MODEL_CANDIDATE_LIMIT

    @staticmethod
    def _emoji_timestamp(value: Any) -> datetime.datetime | None:
        if isinstance(value, datetime.datetime):
            parsed = value
        else:
            raw = str(value or "").strip()
            if not raw:
                return None
            try:
                parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                try:
                    parsed = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc)

    async def _emoji_send_cooldown_active(self, scope: str) -> bool:
        cooldown = self._emoji_send_cooldown_seconds()
        scope = str(scope or "").strip()
        if cooldown <= 0 or not scope:
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        state = self._emoji_sent_store().get(scope)
        if isinstance(state, dict):
            sent_at = self._emoji_timestamp(state.get("sent_at"))
            if sent_at and (now - sent_at).total_seconds() < cooldown:
                return True
        getter = getattr(getattr(self, "archive", None), "get_expression_intents", None)
        if not callable(getter):
            return False
        try:
            records = await getter(limit=20, scope=scope)
        except Exception:
            return False
        for record in records:
            if not bool(getattr(record, "send_emoji", False)):
                continue
            created_at = self._emoji_timestamp(getattr(record, "created_at", ""))
            if created_at and (now - created_at).total_seconds() < cooldown:
                return True
        return False

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
            "sent_at": datetime.datetime.now(datetime.timezone.utc),
        }

    def _emoji_duplicate_skip_reason(
        self, scope: str, emoji: Any, *, source_message_id: str = ""
    ) -> str:
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

    def _emoji_already_sent_for_source(
        self, scope: str, *, source_message_id: str = ""
    ) -> bool:
        source_message_id = str(source_message_id or "").strip()
        if not source_message_id:
            return False
        item = self._emoji_sent_store().get(str(scope or "").strip())
        return bool(item and item.get("last_source_message_id") == source_message_id)

    @staticmethod
    def _emoji_asset_key(emoji: Any) -> str:
        for attr in ("id", "file_hash", "file_path"):
            value = str(getattr(emoji, attr, "") or "").strip()
            if value and value != "0":
                return f"{attr}:{value}"
        return ""

    @staticmethod
    def _disable_emoji_tool_for_active_turn(event: Any) -> None:
        """成功发送后收束当前 Agent 轮次，避免模型重复调用同一工具。"""

        follow_up = _astrbot_follow_up
        runners = getattr(follow_up, "_ACTIVE_AGENT_RUNNERS", None)
        if not isinstance(runners, dict):
            return
        runner = runners.get(str(getattr(event, "unified_msg_origin", "") or ""))
        if runner is None:
            return
        removed = False
        for attr in ("req",):
            request = getattr(runner, attr, None)
            tool_set = getattr(request, "func_tool", None)
            remover = getattr(tool_set, "remove_tool", None)
            if callable(remover):
                before = len(getattr(tool_set, "tools", ()) or ())
                remover("life_emoji_send")
                removed = removed or len(getattr(tool_set, "tools", ()) or ()) < before
        # skills_like 模式保留了原始工具集和参数工具集，也要一并收束。
        for attr in ("_skill_like_raw_tool_set", "_tool_schema_param_set"):
            tool_set = getattr(runner, attr, None)
            remover = getattr(tool_set, "remove_tool", None)
            if callable(remover):
                before = len(getattr(tool_set, "tools", ()) or ())
                remover("life_emoji_send")
                removed = removed or len(getattr(tool_set, "tools", ()) or ()) < before
        if removed:
            logger.debug(f"{LOG_PREFIX} 表情工具已完成本轮收束，阻止重复调用。")

    async def life_emoji_send(
        self,
        event: Any,
        *,
        intent: str = "",
        emotion: str = "",
        emotion_category: str = "",
        decision_reason: str = "",
    ) -> str:
        def mark_outcome(outcome: str) -> None:
            marker = getattr(self, "mark_tool_outcome", None)
            if callable(marker):
                marker(event, "life_emoji_send", outcome)

        if not self._emoji_tool_send_allowed():
            mark_outcome("fallback")
            return "表情工具已关闭。"
        scope = self._emoji_scope(event)
        if not scope:
            mark_outcome("fallback")
            return "当前会话不可发送表情。"
        message_id = self._event_message_id(event)
        if self._emoji_already_sent_for_source(
            scope, source_message_id=message_id
        ):
            mark_outcome("fallback")
            return "同一轮已发送过这个表情"
        current_message = str(getattr(event, "message_str", "") or "").strip()
        intent_payload = {
            "send_emoji": True,
            "emotion": str(emotion or "").strip(),
            "emotion_category": str(emotion_category or "").strip(),
            "emoji_intent": str(intent or "").strip(),
            "action_intent": str(intent or "").strip(),
            "reason": str(decision_reason or intent or "").strip(),
            "_source_message": current_message,
            "_explicit_request": True,
        }
        if current_message and not any(
            intent_payload.get(key)
            for key in ("emotion", "emotion_category", "emoji_intent", "action_intent")
        ):
            intent_payload["emoji_intent"] = current_message
            intent_payload["action_intent"] = current_message
            intent_payload["reason"] = "根据当前用户消息提取表情意图"
        emoji = await self._select_emoji_asset_for_intent(intent_payload, scope=scope)
        if emoji is None:
            mark_outcome("fallback")
            return "没有找到可发送的表情素材。"

        chain = self._emoji_message_chain(emoji)
        items = (
            list(getattr(chain, "chain", None) or getattr(chain, "items", None) or [])
            if chain
            else []
        )
        if not items:
            mark_outcome("fallback")
            return "表情素材暂时不可发送。"

        skip_reason = self._emoji_duplicate_skip_reason(
            scope, emoji, source_message_id=message_id
        )
        if skip_reason:
            mark_outcome("fallback")
            return skip_reason
        if not await self.send_message_if_not_recalled(
            scope,
            chain,
            source_event=event,
            source_message_id=message_id,
        ):
            mark_outcome("fallback")
            return "原消息已撤回，已取消表情发送。"

        mark_outcome("sent")
        self._disable_emoji_tool_for_active_turn(event)
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
        logger.debug(
            f"{LOG_PREFIX} 已通过表情工具发送素材：{label or getattr(emoji, 'file_hash', '')}"
        )
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

    async def send_semantic_emoji_if_needed(self, event: Any) -> bool:
        """在普通聊天文字成功投递后，按语义裁定附加一张表情。"""
        if (
            event is None
            or not self._emoji_auto_send_allowed("regular_reply")
            or getattr(event, self._SEMANTIC_EMOJI_ATTEMPTED_ATTR, False)
        ):
            return False
        plan_getter = getattr(self, "_semantic_expression_plan_from_event", None)
        plan = plan_getter(event) if callable(plan_getter) else None
        if plan is None or not bool(getattr(plan, "send_emoji", False)):
            return False
        setattr(event, self._SEMANTIC_EMOJI_ATTEMPTED_ATTR, True)

        scope = self._emoji_scope(event)
        if not scope:
            logger.debug(f"{LOG_PREFIX} 普通回复表情跳过：当前会话不可发送表情。")
            return False
        if await self._emoji_send_cooldown_active(scope):
            logger.debug(f"{LOG_PREFIX} 普通回复表情跳过：当前会话仍在冷却期。")
            return False
        source_message_id = self._event_message_id(event)
        previous = self._emoji_sent_store().get(scope)
        if source_message_id and isinstance(previous, dict):
            if previous.get("last_source_message_id") == source_message_id:
                logger.debug(f"{LOG_PREFIX} 普通回复表情跳过：同一轮已经发送过表情。")
                return False
        intent = {
            "send_emoji": True,
            "emotion": str(getattr(plan, "emotion", "") or "").strip(),
            "emotion_category": str(
                getattr(plan, "emotion_category", "") or ""
            ).strip(),
            "emoji_intent": str(getattr(plan, "emoji_intent", "") or "").strip(),
            "action_intent": str(getattr(plan, "emoji_intent", "") or "").strip(),
            "reason": str(getattr(plan, "reason", "") or "").strip(),
        }
        try:
            emoji = await self._select_emoji_asset_for_intent(intent, scope=scope)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 普通回复表情选择失败，保留文字回复：{exc}")
            return False
        if emoji is None:
            logger.debug(f"{LOG_PREFIX} 普通回复表情裁定通过，但没有合适的可发送素材。")
            return False
        chain = self._emoji_message_chain(emoji)
        if not chain:
            logger.debug(f"{LOG_PREFIX} 普通回复表情跳过：候选素材不可发送。")
            return False
        if not await self.send_message_if_not_recalled(
            scope,
            chain,
            source_event=event,
            source_message_id=source_message_id,
        ):
            logger.debug(f"{LOG_PREFIX} 普通回复表情发送取消：原消息已撤回或会话不可达。")
            return False

        try:
            await self._mark_emoji_used(
                event,
                emoji,
                intent,
                scope=scope,
                reply_text=str(getattr(plan, "text", "") or "").strip(),
                message_id=source_message_id,
                source="regular_reply",
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 普通回复表情已发送，但记录使用结果失败：{exc}")
        note = getattr(self, "note_structured_bot_message", None)
        if callable(note):
            note(
                scope,
                f"[表情：{getattr(emoji, 'label', '') or '已发送'}]",
                source_event=event,
                media="表情",
            )
        label = str(getattr(emoji, "label", "") or "").strip()
        logger.debug(f"{LOG_PREFIX} 普通回复已附加表情：{label or getattr(emoji, 'file_hash', '')}")
        return True

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
        if source_event is None:
            source_event = payload.get("_source_event")
        if not source_message_id:
            source_message_id = str(payload.get("_source_message_id") or "").strip()
        if not source_message_id and source_event is not None:
            source_message_id = self._event_message_id(source_event)
        source = str(payload.get("source") or "proactive_reply").strip()
        if not self._emoji_auto_send_allowed(source):
            return
        if await self._emoji_send_cooldown_active(target_scope):
            logger.debug(f"{LOG_PREFIX} {source}表情跳过：当前会话仍在冷却期。")
            return
        if self._emoji_already_sent_for_source(
            target_scope, source_message_id=source_message_id
        ):
            logger.debug(f"{LOG_PREFIX} {source}表情跳过：同一轮已经发送过表情。")
            return
        intent = payload.get("expression_intent")
        if not isinstance(intent, dict) or not self._proactive_bool(
            intent.get("send_emoji")
        ):
            return
        try:
            emoji = await self._select_emoji_asset_for_intent(intent, scope=target_scope)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} {source}表情选择失败，保留文字结果：{exc}")
            return
        if not emoji:
            logger.debug(f"{LOG_PREFIX} {source}表情裁定通过，但没有合适的可发送素材。")
            return
        skip_reason = self._emoji_duplicate_skip_reason(
            target_scope, emoji, source_message_id=source_message_id
        )
        if skip_reason:
            logger.debug(f"{LOG_PREFIX} 闲时回复表情跳过：{skip_reason}")
            return
        chain = self._emoji_message_chain(emoji)
        if not chain:
            logger.debug(f"{LOG_PREFIX} {source}表情跳过：候选素材不可发送。")
            return
        try:
            if not await self.send_message_if_not_recalled(
                target_scope,
                chain,
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                return
            await self._mark_emoji_used(
                source_event,
                emoji,
                intent,
                scope=target_scope,
                reply_text=str(payload.get("reply_text") or "").strip(),
                message_id=source_message_id,
                source=source,
            )
            label = str(getattr(emoji, "label", "") or "").strip()
            logger.debug(f"{LOG_PREFIX} {source}已附加表情：{label or getattr(emoji, 'file_hash', '')}")
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} {source}附加表情发送失败：{exc}")

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
                            source_message_id=str(
                                getattr(emoji, "source_message_id", "") or ""
                            ),
                            source_url=str(getattr(emoji, "source_url", "") or ""),
                            source_kind=str(
                                getattr(emoji, "source_kind", "") or "trusted"
                            ),
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
    def _emoji_asset_is_sendable(asset: Any) -> bool:
        if str(getattr(asset, "status", "") or "") != "ready":
            return False
        if getattr(asset, "sendable", True) is False:
            return False
        if not str(getattr(asset, "file_path", "") or "").strip():
            return False
        return bool(
            str(getattr(asset, "label", "") or "").strip()
            or str(getattr(asset, "description", "") or "").strip()
            or list(getattr(asset, "emotions", []) or [])
        )

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
    def _emoji_semantic_text(asset: Any) -> str:
        return "；".join(
            value
            for value in (
                str(getattr(asset, "label", "") or "").strip(),
                str(getattr(asset, "description", "") or "").strip(),
                "、".join(str(item or "") for item in getattr(asset, "emotions", []) or []),
            )
            if value
        )

    async def _semantic_emoji_candidates(
        self, assets: list[Any], intent: dict[str, Any]
    ) -> list[Any]:
        query = json.dumps(
            {
                "emotion": intent.get("emotion"),
                "emotion_category": emoji_category_tag(intent.get("emotion_category")),
                "emoji_intent": intent.get("emoji_intent"),
                "action_intent": intent.get("action_intent"),
                "reason": intent.get("reason"),
                "source_message": intent.get("_source_message"),
                "recent_intents": intent.get("_recent_intents") or [],
            },
            ensure_ascii=False,
        )
        candidate_limit = self._emoji_model_candidate_limit()
        limit = min(len(assets), candidate_limit * 2)
        ranked = await self.rank_embedding_groups(
            query,
            {
                "emoji_assets": [
                    (
                        f"emoji:{int(getattr(asset, 'id', 0) or index)}",
                        self._emoji_semantic_text(asset),
                        asset,
                    )
                    for index, asset in enumerate(assets)
                ]
            },
            {"emoji_assets": limit},
        )
        semantic = list(ranked.get("emoji_assets") or [])
        now = life_now()
        fresh = [
            asset
            for asset in semantic
            if self._emoji_recency_penalty(getattr(asset, "last_used_at", ""), now)
            < 1.0
        ]
        pool = fresh if fresh else semantic
        indexed = {int(getattr(asset, "id", 0) or 0): index for index, asset in enumerate(pool)}
        pool.sort(
            key=lambda asset: (
                indexed.get(int(getattr(asset, "id", 0) or 0), len(pool)),
                self._emoji_recency_penalty(getattr(asset, "last_used_at", ""), now),
                int(getattr(asset, "used_count", 0) or 0),
                int(getattr(asset, "id", 0) or 0),
            )
        )
        return pool[:candidate_limit]

    async def _recent_sent_emoji_ids(self, scope: str) -> set[int]:
        """读取当前会话最近成功投递的表情，用于避免短期反复复用。"""

        scope = str(scope or "").strip()
        exclusion_limit = self._emoji_recent_sent_exclusion_limit()
        getter = getattr(self.archive, "get_expression_intents", None)
        if exclusion_limit <= 0 or not scope or not callable(getter):
            return set()
        try:
            records = await getter(
                limit=exclusion_limit * 4,
                scope=scope,
            )
        except Exception:
            return set()

        recent_ids: set[int] = set()
        sent_count = 0
        for record in records:
            if not bool(getattr(record, "send_emoji", False)):
                continue
            emoji_id = int(getattr(record, "emoji_id", 0) or 0)
            if emoji_id <= 0:
                continue
            recent_ids.add(emoji_id)
            sent_count += 1
            if sent_count >= exclusion_limit:
                break
        return recent_ids

    async def _select_emoji_asset_for_intent(
        self, intent: dict[str, Any], *, scope: str = ""
    ) -> Any | None:
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
        provider = await self._get_proactive_provider()
        if not provider:
            return None
        candidate_assets = await self._semantic_emoji_candidates(assets, intent)
        if not candidate_assets:
            return None
        recent_sent_ids = await self._recent_sent_emoji_ids(scope)
        if recent_sent_ids and not bool(intent.get("_explicit_request")):
            candidate_assets = [
                item
                for item in candidate_assets
                if int(getattr(item, "id", 0) or 0) not in recent_sent_ids
            ]
            if not candidate_assets:
                logger.debug(
                    f"{LOG_PREFIX} 表情选择跳过：当前会话最近"
                    f"{self._EMOJI_RECENT_SENT_EXCLUSION_LIMIT}次已发送过所有匹配候选。"
                )
                return None
        candidates = [
            {
                "id": item.id,
                "label": str(getattr(item, "label", "") or ""),
                "description": str(getattr(item, "description", "") or ""),
                "emotions": list(getattr(item, "emotions", []) or [])[:6],
                "used_count": int(getattr(item, "used_count", 0) or 0),
                "recently_used": self._emoji_recency_penalty(
                    getattr(item, "last_used_at", ""), life_now()
                )
                > 0,
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
用户当前消息：{intent.get("_source_message") or "无"}

候选表情：
{json.dumps(candidates, ensure_ascii=False)}"""
        session_id = f"daily_life_emoji_pick_{uuid.uuid4().hex[:8]}"
        try:
            provider_id = self.config.proactive.provider
            text = await self.call_text_model(
                provider,
                cache_friendly_prompt(fixed, dynamic),
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            payload = extract_json_from_text(text)
            emoji_id = (
                int(payload.get("emoji_id") or 0) if isinstance(payload, dict) else 0
            )
            if emoji_id <= 0:
                return None
            return next(
                (
                    item
                    for item in candidate_assets
                    if int(getattr(item, "id", 0) or 0) == emoji_id
                ),
                None,
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时回复表情选择跳过：{exc}")
            return None
        finally:
            await self.close_text_session(session_id)

    async def _attach_recent_expression_intents(
        self, intent: dict[str, Any], *, scope: str = ""
    ) -> None:
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
