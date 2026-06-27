import unittest

from support import (  # noqa: F401
    ActionDecisionRecord,
    BehaviorFeedbackRecord,
    CatalogItemRecord,
    ChatSummaryRecord,
    CommitmentRecord,
    DailyReviewRecord,
    EventRecord,
    GroupEnvironmentRecord,
    HairStyleRecord,
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
    WeekTemplateRecord,
    ExpressionIntentRecord,
)


class ModelContractTest(unittest.TestCase):
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
        episode = LifeEpisodeRecord.from_value(
            {
                "title": "看见但不理",
                "summary": "扫到群聊但选择观察",
                "people": ["阿林"],
                "places": ["测试群"],
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

        boundary = MemoryBoundaryRecord.from_value(
            {"source_scope": "群A", "target_scope": "群B", "policy": "deny"}
        )
        self.assertEqual(boundary.policy, "deny")
        self.assertIsNone(
            MemoryBoundaryRecord.from_value(
                {"source_scope": "group:1509160", "target_scope": "group:1509160", "policy": "allow"}
            )
        )

    def test_commitment_and_catalog_models_use_current_field_names(self):
        self.assertIsNone(CommitmentRecord.from_value({"summary": "旧约定"}))
        self.assertIsNone(CommitmentRecord.from_value({"text": "旧约定"}))
        commitment = CommitmentRecord.from_value({"content": "约定", "date": "2026-06-18", "time": "10:00"})
        self.assertEqual(commitment.trigger_date, "")
        self.assertEqual(commitment.trigger_time, "")

        self.assertIsNone(CatalogItemRecord.from_value({"category": "daily_themes", "value": "旧素材"}))
        item = CatalogItemRecord.from_value({"category": "daily_themes", "text": "素材", "id": "old-id"})
        self.assertEqual(item.item_id, "")

        self.assertIsNone(HairStyleRecord.from_value({"style": "旧发型", "hairstyles": ["低马尾"]}))
        style = HairStyleRecord.from_value({"name": "发型", "hairstyles": ["低马尾"], "id": "old-id"})
        self.assertEqual(style.style_id, "")

    def test_week_template_uses_template_id_field(self):
        self.assertIsNone(WeekTemplateRecord.from_value({"id": "old_week", "name": "旧模板"}))
        template = WeekTemplateRecord.from_value({"template_id": "new_week", "name": "新模板"})
        self.assertEqual(template.template_id, "new_week")


if __name__ == "__main__":
    unittest.main()
