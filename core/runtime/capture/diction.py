from __future__ import annotations

from typing import Any

from ...models import ExpressionProfileRecord, ExpressionReviewRecord


class ExpressionLearningMixin:
    """在已有后台记忆批次中提炼有消息证据的表达习惯。"""

    @staticmethod
    def _batch_expression_scope(batch: dict[str, Any]) -> str:
        rows = batch.get("messages") or []
        last = rows[-1] if rows else {}
        return str(last.get("group_id") or batch.get("session_id") or "").strip()

    async def _batch_expression_context(self, batch: dict[str, Any]) -> dict[str, Any]:
        scope = self._batch_expression_scope(batch)
        participants = {
            str(row.get("sender_profile_id") or "")
            for row in batch.get("messages", [])
            if row.get("role", "user") == "user"
        }
        profiles = await self.archive.get_expression_profiles(limit=12, scope=scope)
        effects = await self.archive.get_reply_effects(
            limit=12, scope=str(batch.get("session_id") or "")
        )
        return {
            "current_expression_profiles": [
                item.as_dict()
                for item in profiles
                if item.scope == scope and item.profile_id in participants
            ],
            "recent_reply_effects": [
                item.as_dict()
                for item in effects
                if item.scope == batch.get("session_id")
                and item.outcome in {"positive", "negative"}
                and item.evidence
            ],
        }

    def _normalize_batch_expression_learning(
        self, payload: dict[str, Any], batch: dict[str, Any]
    ) -> None:
        rows = batch.get("messages") or []
        by_id = {
            str(row.get("message_id") or row.get("id") or ""): (index, row)
            for index, row in enumerate(rows)
        }
        effects = {
            str(item.get("id")): item
            for item in batch.get("recent_reply_effects", [])
            if isinstance(item, dict)
            and item.get("scope") == batch.get("session_id")
            and item.get("outcome") in {"positive", "negative"}
            and item.get("evidence")
        }
        scope = self._batch_expression_scope(batch)

        def references(raw: dict[str, Any], field: str) -> list[str]:
            value = raw.get(field)
            if not isinstance(value, list):
                return []
            return list(dict.fromkeys(str(item).strip() for item in value if item))[:12]

        def user_evidence(ids: list[str], profile_id: str) -> bool:
            return bool(ids) and all(
                item in by_id
                and by_id[item][1].get("role", "user") == "user"
                and by_id[item][1].get("sender_profile_id") == profile_id
                and not by_id[item][1].get("is_quoted")
                and str(by_id[item][1].get("message_text") or "").strip()
                for item in ids
            )

        profiles = []
        seen = set()
        for raw in self._dict_payloads(payload.get("expression_profiles"))[:4]:
            record = ExpressionProfileRecord.from_value(raw)
            if (
                not record
                or not scope
                or not record.profile_id
                or not record.label
                or not (record.tone or record.habits or record.avoid)
                or not record.evidence
                or isinstance(raw.get("confidence"), bool)
                or not isinstance(raw.get("confidence"), (int, float))
                or not 0.78 <= raw["confidence"] <= 1.0
            ):
                continue
            user_ids = references(raw, "source_message_ids")
            if not user_evidence(user_ids, record.profile_id):
                continue
            basis = str(raw.get("basis") or "")
            if basis == "repeated":
                reply_ids = references(raw, "reply_message_ids")
                if any(
                    item not in by_id or by_id[item][1].get("role") != "assistant"
                    for item in reply_ids
                ):
                    continue
                # 每次回应只计一次后续反馈，不能用同一轮的连发消息凑次数。
                paired = {
                    max(
                        (
                            by_id[item][0]
                            for item in reply_ids
                            if by_id[item][0] < by_id[user][0]
                        ),
                        default=-1,
                    )
                    for user in user_ids
                } - {-1}
                effect_ids = references(raw, "reply_effect_ids")
                valid_effects = [
                    effects[item] for item in effect_ids if item in effects
                ]
                if len(valid_effects) != len(effect_ids):
                    continue
                if rows and rows[-1].get("is_group"):
                    valid_effects = [
                        item
                        for item in valid_effects
                        if user_evidence(
                            [str(item.get("target_message_id") or "")],
                            record.profile_id,
                        )
                    ]
                effect_targets = {
                    str(item.get("target_message_id") or item["id"])
                    for item in valid_effects
                }
                if max(len(paired), len(effect_targets)) < 3:
                    continue
            elif basis != "explicit":
                continue
            key = (record.profile_id, record.label)
            if key in seen:
                continue
            seen.add(key)
            profiles.append(
                {
                    **raw,
                    **record.as_dict(),
                    "scope": scope,
                    "source": "chat_expression",
                    "source_message_ids": user_ids,
                }
            )
        payload["expression_profiles"] = profiles

        reviews = []
        for raw in self._dict_payloads(payload.get("expression_reviews"))[:3]:
            reply_id = str(raw.get("reply_message_id") or "")
            user_ids = references(raw, "source_message_ids")
            profile_id = str(raw.get("profile_id") or "")
            if (
                reply_id not in by_id
                or by_id[reply_id][1].get("role") != "assistant"
                or not user_evidence(user_ids, profile_id)
                or any(by_id[item][0] <= by_id[reply_id][0] for item in user_ids)
                or not isinstance(raw.get("passed"), bool)
                or not str(raw.get("reason") or "").strip()
            ):
                continue
            record = ExpressionReviewRecord.from_value(
                {
                    **raw,
                    "scope": str(batch.get("session_id") or ""),
                    "reply_text": by_id[reply_id][1].get("message_text") or "",
                    "source": "chat_expression",
                }
            )
            if record:
                reviews.append(
                    {**raw, **record.as_dict(), "source_message_ids": user_ids}
                )
        payload["expression_reviews"] = reviews

    async def _save_batch_expression_learning(
        self, payload: dict[str, Any], batch: dict[str, Any], meta: dict[str, str]
    ) -> None:
        saved = {"expression_profiles": []}
        await self._save_expression_profile_payloads(
            payload, meta, self._batch_expression_scope(batch), saved
        )
        by_id = {
            str(row.get("message_id") or row.get("id") or ""): row
            for row in batch.get("messages", [])
        }
        for profile, raw in zip(
            saved["expression_profiles"], payload.get("expression_profiles", [])
        ):
            existing = await self.archive.get_memory_evidence(
                target_type="expression_profile", target_id=str(profile.id), limit=30
            )
            known = {(item.source_table, item.source_id) for item in existing}
            for message_id in raw.get("source_message_ids", []):
                row = by_id[str(message_id)]
                source_id = str(row["id"])
                if ("chat_memory_messages", source_id) in known:
                    continue
                await self._save_experience_evidence(
                    "expression_profile",
                    str(profile.id),
                    profile.evidence,
                    {**meta, "message_id": str(message_id)},
                    evidence_type="expression_feedback",
                    source_table="chat_memory_messages",
                    source_id=source_id,
                    confidence=profile.confidence,
                )
        for raw in payload.get("expression_reviews", []):
            await self.archive.save_expression_review(
                ExpressionReviewRecord.from_value(raw)
            )
        # 表达学习独立于摘要是否值得保存；后续通用记忆入库不重复处理。
        payload["expression_profiles"] = []
        payload["expression_reviews"] = []
