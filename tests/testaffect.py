import datetime

from core.life.affect import AffectEngine, AffectiveSnapshot, AffectSignal


def test_affect_signal_requires_structured_evidence():
    assert (
        AffectSignal.from_value(
            {
                "layer": "daily",
                "label": "轻松",
                "valence": 0.7,
                "arousal": 0.4,
                "intensity": 0.8,
            }
        )
        is None
    )


def test_affect_layers_react_at_different_speeds():
    engine = AffectEngine()
    now = datetime.datetime(2026, 8, 1, 12, 0)
    transient = engine.apply(
        None,
        AffectSignal(
            layer="transient",
            label="开心",
            valence=1.0,
            arousal=0.8,
            intensity=1.0,
            evidence_ids=["event:1"],
        ),
        scope="global",
        now=now,
    )
    relationship = engine.apply(
        None,
        AffectSignal(
            layer="relationship",
            label="亲近",
            valence=1.0,
            arousal=0.8,
            intensity=1.0,
            evidence_ids=["reply_effect:1"],
        ),
        scope="relationship:u1",
        now=now,
    )
    assert transient.valence > relationship.valence
    assert transient.intensity > relationship.intensity


def test_affect_decay_uses_half_life():
    engine = AffectEngine()
    start = datetime.datetime(2026, 8, 1, 8, 0)
    state = AffectiveSnapshot(
        layer="transient",
        valence=0.8,
        arousal=0.9,
        intensity=0.85,
        baseline=0.05,
        decay_half_life_minutes=120,
        updated_at=start,
    )
    decayed = engine.decay(state, start + datetime.timedelta(minutes=120))
    assert decayed.valence == 0.4
    assert decayed.arousal == 0.7
    assert round(decayed.intensity, 2) == 0.45


def test_reflection_gate_skips_low_importance_updates():
    gate = AffectEngine.reflection_gate(
        novelty=0.2,
        emotional_intensity=0.2,
        goal_impact=0.1,
        social_impact=0.1,
    )
    assert gate.should_reflect is False
    assert gate.reason_code == "importance_below_threshold"


def test_relationship_updates_are_bounded_and_require_evidence():
    updates = AffectEngine.relationship_updates_from_payload(
        {
            "relationship_updates": [
                {
                    "profile_id": "u1",
                    "familiarity_delta": 0.7,
                    "trust_delta": -0.3,
                    "evidence_ids": ["effect:3"],
                },
                {"profile_id": "u2", "affinity_delta": 0.5},
            ]
        }
    )
    assert len(updates) == 1
    assert updates[0].familiarity_delta == 0.08
    assert updates[0].trust_delta == -0.08


def test_grounded_diary_rejects_unknown_evidence():
    payload = {
        "grounded_diary": {
            "title": "雨天",
            "summary": "我在家安静地整理了照片。",
            "evidence_ids": ["event:unknown"],
            "mood_label": "平静",
        }
    }
    assert (
        AffectEngine.grounded_diary_from_payload(
            payload,
            date="2026-08-01",
            allowed_evidence_ids={"event:1"},
        )
        is None
    )
    diary = AffectEngine.grounded_diary_from_payload(
        payload,
        date="2026-08-01",
        allowed_evidence_ids={"event:unknown"},
    )
    assert diary is not None
    assert diary["evidence_ids"] == ["event:unknown"]
