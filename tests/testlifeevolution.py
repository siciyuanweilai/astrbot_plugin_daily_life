import datetime
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from core.life.evolution import LifeEvolutionService


@dataclass
class Record:
    id: int


class FakeArchive:
    def __init__(self):
        self.states = []
        self.reflections = []
        self.diaries = []
        self.traces = []
        self.points = []

    async def get_affective_states(self, **kwargs):
        return [
            item
            for item in self.states
            if (not kwargs.get("scope") or item.scope == kwargs["scope"])
            and (not kwargs.get("layer") or item.layer == kwargs["layer"])
        ]

    async def save_affective_state(self, item):
        self.states.append(item)
        return item

    async def get_relationship(self, profile_id):
        return object() if profile_id == "u1" else None

    async def add_relationship_point(self, profile_id, content, **kwargs):
        self.points.append((profile_id, content, kwargs))

    async def save_reflection(self, item):
        self.reflections.append(item)
        return item

    async def promote_reflections(self, **kwargs):
        return []

    async def save_grounded_diary_entry(self, item):
        self.diaries.append(item)
        return item

    async def save_decision_trace(self, item):
        self.traces.append(item)
        return item


class LifeEvolutionServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_review_settlement_requires_known_evidence(self):
        archive = FakeArchive()
        service = LifeEvolutionService(archive)
        payload = {
            "affect_updates": [
                {
                    "layer": "daily",
                    "label": "满足",
                    "valence": 0.8,
                    "arousal": 0.4,
                    "intensity": 0.8,
                    "evidence_ids": ["event:1"],
                },
                {
                    "layer": "transient",
                    "label": "虚构",
                    "valence": 1.0,
                    "arousal": 1.0,
                    "intensity": 1.0,
                    "evidence_ids": ["event:99"],
                },
            ],
            "relationship_updates": [
                {
                    "profile_id": "u1",
                    "trust_delta": 0.04,
                    "evidence_ids": ["reply_effect:4"],
                    "reason": "今天的交流更自然。",
                }
            ],
            "reflection_score": {
                "novelty": 0.9,
                "emotional_intensity": 0.8,
                "goal_impact": 0.8,
                "social_impact": 0.9,
            },
            "reflection": {
                "summary": "高价值复盘",
                "evidence_ids": ["event:1", "reply_effect:4"],
                "assertion": {
                    "subject": "我",
                    "predicate": "喜欢",
                    "object": "雨天整理照片",
                },
            },
            "grounded_diary": {
                "title": "雨天",
                "summary": "我今天在家整理了照片，也聊得很放松。",
                "evidence_ids": ["event:1", "reply_effect:4"],
                "mood_label": "满足",
            },
        }

        result = await service.settle_review(
            payload,
            date="2026-08-01",
            events=[Record(1)],
            decisions=[],
            feedback=[],
            reply_effects=[Record(4)],
            now=datetime.datetime(2026, 8, 1, 23, 30),
        )

        self.assertEqual(result["affective_states"], 1)
        self.assertEqual(result["relationship_updates"], 1)
        self.assertTrue(result["reflection_saved"])
        self.assertTrue(result["diary_saved"])
        self.assertEqual(len(archive.states), 2)
        self.assertEqual(len(archive.reflections), 1)
        self.assertEqual(len(archive.diaries), 1)
        self.assertEqual(archive.traces[-1]["stage"], "committed")

    async def test_low_value_review_does_not_create_reflection_or_diary(self):
        archive = FakeArchive()
        service = LifeEvolutionService(archive)
        result = await service.settle_review(
            {
                "reflection_score": {
                    "novelty": 0.1,
                    "emotional_intensity": 0.1,
                    "goal_impact": 0.1,
                    "social_impact": 0.1,
                },
                "reflection": {
                    "summary": "普通的一天",
                    "evidence_ids": ["event:1"],
                },
                "grounded_diary": {
                    "summary": "没有依据的描述",
                    "evidence_ids": ["event:99"],
                },
            },
            date="2026-08-01",
            events=[Record(1)],
            decisions=[],
            feedback=[],
            reply_effects=[],
        )
        self.assertFalse(result["reflection_saved"])
        self.assertFalse(result["diary_saved"])
        self.assertEqual(archive.reflections, [])
        self.assertEqual(archive.diaries, [])

    async def test_review_uses_life_clock_when_now_is_omitted(self):
        archive = FakeArchive()
        service = LifeEvolutionService(archive)
        expected = datetime.datetime(2026, 8, 1, 23, 45)

        with patch("core.life.evolution.life_now", return_value=expected) as clock:
            await service.settle_review(
                {},
                date="2026-08-01",
                events=[],
                decisions=[],
                feedback=[],
                reply_effects=[],
            )

        clock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
