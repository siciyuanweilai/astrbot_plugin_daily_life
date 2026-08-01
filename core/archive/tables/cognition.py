COGNITION_SQL = """
-- 时间化认知、可恢复任务与生活结算
CREATE TABLE IF NOT EXISTS temporal_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            predicate TEXT NOT NULL DEFAULT '',
            object_json TEXT NOT NULL DEFAULT 'null',
            observed_at TEXT NOT NULL DEFAULT '',
            valid_from TEXT NOT NULL DEFAULT '',
            valid_to TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT 'observation',
            source_type TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            supersedes_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(supersedes_id) REFERENCES temporal_facts(id) ON DELETE SET NULL
        );
CREATE TABLE IF NOT EXISTS fact_evidence_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_id INTEGER NOT NULL,
            signal TEXT NOT NULL DEFAULT 'reinforce',
            weight REAL NOT NULL DEFAULT 1.0,
            confidence REAL NOT NULL DEFAULT 1.0,
            summary TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'observation',
            source_id TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL DEFAULT '',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(fact_id) REFERENCES temporal_facts(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'reflection',
            summary TEXT NOT NULL DEFAULT '',
            importance REAL NOT NULL DEFAULT 0,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            assertion_subject TEXT NOT NULL DEFAULT '',
            assertion_predicate TEXT NOT NULL DEFAULT '',
            assertion_object_json TEXT NOT NULL DEFAULT 'null',
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'pending',
            source TEXT NOT NULL DEFAULT 'reflection',
            promoted_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS persona_assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            predicate TEXT NOT NULL DEFAULT '',
            object_json TEXT NOT NULL DEFAULT 'null',
            confidence REAL NOT NULL DEFAULT 1.0,
            source_reflection_id INTEGER,
            valid_from TEXT NOT NULL DEFAULT '',
            valid_to TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(source_reflection_id) REFERENCES reflections(id) ON DELETE SET NULL
        );
CREATE TABLE IF NOT EXISTS durable_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 50,
            available_at TEXT NOT NULL DEFAULT '',
            lease_owner TEXT NOT NULL DEFAULT '',
            lease_expires_at TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            last_error TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT NOT NULL DEFAULT ''
        );
CREATE TABLE IF NOT EXISTS decision_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL DEFAULT '',
            reason_code TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL DEFAULT '',
            score_json TEXT NOT NULL DEFAULT '{}',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            outcome TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS life_action_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id TEXT NOT NULL UNIQUE,
            date TEXT NOT NULL DEFAULT '',
            action_type TEXT NOT NULL DEFAULT '',
            target TEXT NOT NULL DEFAULT '',
            preconditions_json TEXT NOT NULL DEFAULT '{}',
            effects_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'proposed',
            reason TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            started_at TEXT NOT NULL DEFAULT '',
            committed_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS affective_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            layer TEXT NOT NULL DEFAULT 'transient',
            label TEXT NOT NULL DEFAULT '',
            valence REAL NOT NULL DEFAULT 0,
            arousal REAL NOT NULL DEFAULT 0.5,
            intensity REAL NOT NULL DEFAULT 0.5,
            baseline REAL NOT NULL DEFAULT 0.5,
            decay_half_life_minutes REAL NOT NULL DEFAULT 240,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            valid_from TEXT NOT NULL DEFAULT '',
            valid_to TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT 'state',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS grounded_diary_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            mood_label TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'daily_review',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, scope)
        );
"""


COGNITION_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_temporal_facts_one_active
ON temporal_facts(scope, subject, predicate)
WHERE status = 'active' AND valid_to = '';
CREATE INDEX IF NOT EXISTS idx_temporal_facts_as_of
ON temporal_facts(scope, subject, predicate, valid_from, valid_to, id DESC);
CREATE INDEX IF NOT EXISTS idx_fact_evidence_fact
ON fact_evidence_signals(fact_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_reflections_pending
ON reflections(status, importance DESC, id ASC);
CREATE INDEX IF NOT EXISTS idx_persona_assertions_active
ON persona_assertions(scope, status, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_durable_tasks_available
ON durable_tasks(status, available_at, priority DESC, id ASC);
CREATE INDEX IF NOT EXISTS idx_durable_tasks_lease
ON durable_tasks(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_decision_traces_scope
ON decision_traces(scope, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_traces_stage
ON decision_traces(trace_id, stage);
CREATE INDEX IF NOT EXISTS idx_life_action_outcomes_recent
ON life_action_outcomes(date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_affective_states_active
ON affective_states(scope, layer, status, valid_from DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_grounded_diary_recent
ON grounded_diary_entries(date DESC, id DESC);
"""


__all__ = ["COGNITION_INDEX_SQL", "COGNITION_SQL"]
