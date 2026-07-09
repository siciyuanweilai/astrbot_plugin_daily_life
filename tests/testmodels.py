import unittest

from support import (  # noqa: F401
    ActionDecisionRecord,
    BehaviorFeedbackRecord,
    ChatSummaryRecord,
    CommitmentRecord,
    DailyReviewRecord,
    LifeDecisionRecord,
    EventRecord,
    GroupEnvironmentRecord,
    LifeEpisodeRecord,
    LifeEventRecord,
    LifeTermRecord,
    MemoryBoundaryRecord,
    MemoryEvidenceRecord,
    MessageVisibilityRecord,
    PlaceRecord,
    PreferenceRecord,
    RelationshipNote,
    RelationshipRecord,
    BehaviorPatternRecord,
    BehaviorSceneRecord,
    ExpressionProfileRecord,
    ExpressionReviewRecord,
    FocusSlotRecord,
    MemoryCorrectionRecord,
    ReplyEffectRecord,
    SessionMidSummaryRecord,
    TemporaryExpressionStateRecord,
    ExpressionIntentRecord,
    EmojiAssetRecord,
    PhysiologicalRhythmLogRecord,
)

from core.models.coerce import compact_text


class ModelContractTest(unittest.TestCase):
    def test_compact_text_flattens_list_like_payloads(self):
        self.assertEqual(compact_text(["避免每天宅家一整天不出门"]), "避免每天宅家一整天不出门")
        self.assertEqual(
            compact_text("『天气适合轻量户外活动』；['避免每天宅家一整天不出门']；今天去河边步道散步"),
            "『天气适合轻量户外活动』；避免每天宅家一整天不出门；今天去河边步道散步",
        )

    def test_world_models_use_current_field_names(self):
        self.assertIsNone(PlaceRecord.from_value({"place": "旧地点"}))
        self.assertIsNone(EventRecord.from_value({"content": "旧事件"}))
        self.assertIsNone(EventRecord.from_value({"event": "旧事件"}))
        self.assertIsNone(RelationshipRecord.from_value({"profile_id": "u1"}))
        self.assertEqual(RelationshipNote.from_value({"note": "旧笔记"}).content, "")
        self.assertIsNone(ChatSummaryRecord.from_value({"summary": "旧摘要"}))
        self.assertIsNone(ChatSummaryRecord.from_value({"detail": "旧长摘要"}))
        self.assertIsNone(GroupEnvironmentRecord.from_value({"main_topic": "旧话题"}))
        self.assertIsNone(MessageVisibilityRecord.from_value({"sender_id": "u1"}))
        self.assertIsNone(ActionDecisionRecord.from_value({"decision_reason": "旧原因"}))

        relationship = RelationshipRecord.from_value(
            {
                "id": "u1",
                "name": "阿林",
                "subjective_name": "会让我多看一眼的人",
                "subjective_tags": ["可靠", "偶尔嘴硬"],
                "relationship_story": "熟悉到可以互相吐槽。",
            }
        )
        self.assertEqual(relationship.subjective_name, "会让我多看一眼的人")
        self.assertEqual(relationship.subjective_tags, ["可靠", "偶尔嘴硬"])

        visibility = MessageVisibilityRecord.from_value(
            {
                "session_id": "s1",
                "sender_profile_id": "u1",
                "psychological_freshness": 73,
                "reactivated_from_id": 2,
                "reason": "重新被接话激活",
            }
        )
        self.assertEqual(visibility.psychological_freshness, 73)
        self.assertEqual(visibility.reactivated_from_id, 2)

    def test_lifecycle_models_use_current_field_names(self):
        self.assertIsNone(PreferenceRecord.from_value({"preference": "旧偏好"}))
        self.assertIsNone(PreferenceRecord.from_value({"text": "旧偏好"}))
        self.assertEqual(PreferenceRecord.from_value({"content": "偏好", "kind": "sleep"}).category, "general")
        self.assertEqual(PreferenceRecord.from_value({"content": "偏好", "reason": "旧证据"}).evidence, "")

        self.assertIsNone(LifeEventRecord.from_value({"summary": "旧生活事件"}))
        self.assertIsNone(LifeEventRecord.from_value({"event": "旧生活事件"}))
        event = LifeEventRecord.from_value({"title": "事件", "description": "旧详情", "impact": "旧影响"})
        self.assertEqual(event.detail, "")
        self.assertEqual(event.effect, "")

        review = DailyReviewRecord.from_value(
            {
                "date": "2026-06-17",
                "memories": ["旧记忆"],
                "preferences": [{"content": "旧偏好"}],
                "events": [{"title": "旧事件"}],
            }
        )
        self.assertEqual(review.memory_points, [])
        self.assertEqual(review.preference_points, [])
        self.assertEqual(review.life_events, [])

    def test_experience_models_use_current_field_names(self):
        self.assertIsNone(LifeEpisodeRecord.from_value({"summary": "只有摘要"}))
        legacy_episode = LifeEpisodeRecord.from_value(
            {
                "title": "看见但不理",
                "summary": "扫到群聊但选择观察",
                "people": ["阿林"],
                "places": ["测试群"],
                "confidence": 2,
                "protected": True,
            }
        )
        self.assertEqual(legacy_episode.related_people, [])
        self.assertEqual(legacy_episode.related_places, [])

        episode = LifeEpisodeRecord.from_value(
            {
                "title": "看见但不理",
                "summary": "扫到群聊但选择观察",
                "related_people": ["阿林"],
                "related_places": ["测试群"],
                "confidence": 2,
                "protected": True,
            }
        )
        self.assertEqual(episode.related_people, ["阿林"])
        self.assertEqual(episode.related_places, ["测试群"])
        self.assertEqual(episode.confidence, 1.0)
        self.assertTrue(episode.protected)

        self.assertIsNone(MemoryEvidenceRecord.from_value({"target_type": "relationship", "summary": "缺目标"}))
        evidence = MemoryEvidenceRecord.from_value(
            {"target_type": "relationship", "target_id": "u1", "summary": "来自聊天"}
        )
        self.assertEqual(evidence.evidence_type, "observation")

        self.assertIsNone(BehaviorFeedbackRecord.from_value({"action": "observe"}))
        feedback = BehaviorFeedbackRecord.from_value({"action": "observe", "result": "neutral"})
        self.assertEqual(feedback.result, "neutral")

        term = LifeTermRecord.from_value({"term": "蹲后续", "meaning": "继续围观同一话题"})
        self.assertEqual(term.term, "蹲后续")

        intent = ExpressionIntentRecord.from_value({"emotion": "慵懒治愈", "emotion_category": "neutral"})
        self.assertEqual(intent.emotion, "慵懒治愈")
        self.assertEqual(intent.emotion_category, "neutral")
        self.assertEqual(intent.as_dict()["emotion_category"], "neutral")

        asset = EmojiAssetRecord.from_value(
            {"file_hash": "emoji-1", "emotion_category": "happy", "emotions": ["探头", "happy"]}
        )
        self.assertEqual(asset.emotions, ["category:happy", "探头", "happy"])

        raw_asset = EmojiAssetRecord.from_value({"file_hash": "emoji-2", "emotions": ["happy"]})
        self.assertEqual(raw_asset.emotions, ["happy"])

        self.assertIsNone(EmojiAssetRecord.from_value({"hash": "emoji-old"}))
        self.assertIsNone(EmojiAssetRecord.from_value({"path": "emoji-old.png"}))
        self.assertEqual(
            EmojiAssetRecord.from_value(
                {"file_hash": "emoji-2b", "message_id": "old-message", "url": "https://example.com/old.png"}
            ).as_dict()["source_message_id"],
            "",
        )

        old_category_asset = EmojiAssetRecord.from_value({"file_hash": "emoji-3", "emotion_category": "category:happy"})
        self.assertEqual(old_category_asset.emotions, [])

        old_single_asset = EmojiAssetRecord.from_value({"file_hash": "emoji-4", "emotion": "happy"})
        self.assertEqual(old_single_asset.emotions, [])

        self.assertEqual(
            MemoryEvidenceRecord.from_value(
                {"target_type": "relationship", "target_id": "u1", "summary": "来自聊天", "source": "old_source_table"}
            ).source_table,
            "",
        )

        boundary = MemoryBoundaryRecord.from_value(
            {"source_scope": "群A", "target_scope": "群B", "policy": "deny"}
        )
        self.assertEqual(boundary.policy, "deny")
        self.assertIsNone(
            MemoryBoundaryRecord.from_value(
                {"source_scope": "group:1509160", "target_scope": "group:1509160", "policy": "allow"}
            )
        )
        self.assertIsNone(MemoryBoundaryRecord.from_value({"source": "群A", "target": "群B"}))

    def test_experience_models_reject_legacy_alias_fields(self):
        self.assertEqual(
            ExpressionProfileRecord.from_value(
                {"profile_id": "u1", "features": ["旧习惯"], "taboo": ["旧禁忌"]}
            ).habits,
            [],
        )
        self.assertIsNone(ExpressionReviewRecord.from_value({"text": "旧回复文本"}))
        self.assertIsNone(TemporaryExpressionStateRecord.from_value({"state": "旧状态", "style": "旧风格"}))
        self.assertIsNone(ExpressionIntentRecord.from_value({"expression_intent": "旧表达"}))
        self.assertIsNone(ExpressionIntentRecord.from_value({"emoji_style": "旧表情风格"}))
        self.assertIsNone(ExpressionIntentRecord.from_value({"action": "旧动作"}))
        self.assertEqual(
            BehaviorPatternRecord.from_value({"scene": "场景", "pattern": "模式", "action": "reply"}).suggested_action,
            "",
        )
        self.assertEqual(
            BehaviorPatternRecord.from_value({"scene": "场景", "pattern": "模式", "reason": "旧依据"}).evidence,
            "",
        )
        self.assertIsNone(BehaviorSceneRecord.from_value({"label": "旧场景", "signals": ["旧线索"]}))
        self.assertIsNone(BehaviorSceneRecord.from_value({"scene": "场景", "action": "reply"}))
        self.assertIsNone(BehaviorSceneRecord.from_value({"scene": "场景", "outcome": "旧结果"}))
        self.assertEqual(
            SessionMidSummaryRecord.from_value({"session_id": "s1", "summary": "摘要", "people": ["旧参与者"]}).participants,
            [],
        )
        self.assertEqual(
            SessionMidSummaryRecord.from_value({"session_id": "s1", "summary": "摘要", "label": "旧标签"}).scope_label,
            "",
        )
        self.assertEqual(LifeTermRecord.from_value({"term": "梗", "meaning": "含义", "example": "旧例子"}).examples, [])
        self.assertEqual(ReplyEffectRecord.from_value({"scope": "s1", "result": "positive"}).outcome, "pending")
        self.assertEqual(ReplyEffectRecord.from_value({"scope": "s1", "feedback": "旧反馈"}).evidence, "")
        self.assertEqual(ReplyEffectRecord.from_value({"scope": "s1", "message_id": "m-old"}).target_message_id, "")
        decision = LifeDecisionRecord.from_value(
            {"kind": "daily_plan", "decision": "宅家恢复", "reason": "睡眠债偏高", "confidence": 2}
        )
        self.assertEqual(decision.kind, "daily_plan")
        self.assertEqual(decision.confidence, 1.0)
        self.assertIsNone(MemoryCorrectionRecord.from_value({"target_type": "relationship", "target_id": "u1", "content": "旧修正"}))
        self.assertEqual(
            MemoryCorrectionRecord.from_value(
                {"target_type": "relationship", "target_id": "u1", "correction": "修正", "reason": "旧依据"}
            ).evidence,
            "",
        )
        self.assertIsNone(FocusSlotRecord.from_value({"target_id": "old-focus"}))
        self.assertIsNone(PhysiologicalRhythmLogRecord.from_value({"body_condition": {"label": "旧结构"}}))
        rhythm_log = PhysiologicalRhythmLogRecord.from_value(
            {
                "date": "2026-07-05",
                "source": "state",
                "energy_curve": "上午慢热，傍晚回升",
                "body_label": "轻微疲惫",
                "body_intensity": 36,
                "recovery_actions": ["补水", "早点收尾"],
                "social_battery": 42,
                "attention_state": "低刺激更舒服",
                "lifecycle_kind": "short_term",
            }
        )
        self.assertEqual(rhythm_log.body_label, "轻微疲惫")
        self.assertEqual(rhythm_log.recovery_actions, ["补水", "早点收尾"])
        self.assertEqual(rhythm_log.lifecycle_kind, "short_term")
        self.assertEqual(
            PhysiologicalRhythmLogRecord.from_value({"summary": "稳定态旧枚举", "lifecycle_kind": "stable"}).lifecycle_kind,
            "transient",
        )
        self.assertFalse(
            PhysiologicalRhythmLogRecord.from_value(
                {
                    "summary": "仅保留轻量节律",
                    "optional_cycle_enabled": True,
                }
            ).optional_cycle_enabled
        )

    def test_commitment_model_uses_current_field_names(self):
        self.assertIsNone(CommitmentRecord.from_value({"summary": "旧约定"}))
        self.assertIsNone(CommitmentRecord.from_value({"text": "旧约定"}))
        commitment = CommitmentRecord.from_value({"content": "约定", "date": "2026-06-18", "time": "10:00"})
        self.assertEqual(commitment.trigger_date, "")
        self.assertEqual(commitment.trigger_time, "")


if __name__ == "__main__":
    unittest.main()
