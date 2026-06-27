EXPERIENCE_SQL = """
-- 体验学习与表达记录
CREATE TABLE IF NOT EXISTS life_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'daily',
            source TEXT NOT NULL DEFAULT 'daily',
            impact TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'open',
            protected INTEGER NOT NULL DEFAULT 0,
            correction TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS life_episode_people (
            episode_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            person TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(episode_id, sort_order),
            FOREIGN KEY(episode_id) REFERENCES life_episodes(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS life_episode_places (
            episode_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            place TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(episode_id, sort_order),
            FOREIGN KEY(episode_id) REFERENCES life_episodes(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS memory_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL DEFAULT '',
            target_id TEXT NOT NULL DEFAULT '',
            evidence_type TEXT NOT NULL DEFAULT 'observation',
            source_table TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS behavior_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL DEFAULT '',
            target_type TEXT NOT NULL DEFAULT 'action',
            target_id TEXT NOT NULL DEFAULT '',
            scene TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '',
            score REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat_memory',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS reply_effects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            target_message_id TEXT NOT NULL DEFAULT '',
            reply_text TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL DEFAULT 'pending',
            warmth INTEGER NOT NULL DEFAULT 50,
            continuity INTEGER NOT NULL DEFAULT 50,
            friction INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'proactive_reply',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS memory_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL DEFAULT '',
            target_id TEXT NOT NULL DEFAULT '',
            correction TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            applied INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'chat_memory',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS expression_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            profile_id TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            tone TEXT NOT NULL DEFAULT '',
            habits TEXT NOT NULL DEFAULT '',
            avoid TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            source TEXT NOT NULL DEFAULT 'chat_memory',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, profile_id, label)
        );
CREATE TABLE IF NOT EXISTS expression_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            reply_text TEXT NOT NULL DEFAULT '',
            passed INTEGER NOT NULL DEFAULT 1,
            risk TEXT NOT NULL DEFAULT '',
            suggestion TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'proactive_reply',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS behavior_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            scene TEXT NOT NULL DEFAULT '',
            pattern TEXT NOT NULL DEFAULT '',
            suggested_action TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            support_count INTEGER NOT NULL DEFAULT 1,
            score REAL NOT NULL DEFAULT 0,
            evidence TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat_memory',
            last_seen TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, scene, pattern)
        );
CREATE TABLE IF NOT EXISTS behavior_scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            scene TEXT NOT NULL DEFAULT '',
            cues TEXT NOT NULL DEFAULT '',
            preferred_action TEXT NOT NULL DEFAULT '',
            avoid_action TEXT NOT NULL DEFAULT '',
            outcome_hint TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            support_count INTEGER NOT NULL DEFAULT 1,
            last_seen TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat_memory',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, scene)
        );
CREATE TABLE IF NOT EXISTS session_mid_summaries (
            session_id TEXT PRIMARY KEY,
            scope_label TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL DEFAULT '',
            mood TEXT NOT NULL DEFAULT '',
            participants TEXT NOT NULL DEFAULT '',
            message_count INTEGER NOT NULL DEFAULT 0,
            last_message_id TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat_memory',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS temporary_expression_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            tone TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            intensity INTEGER NOT NULL DEFAULT 50,
            expires_at TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat_memory',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, label)
        );
CREATE TABLE IF NOT EXISTS focus_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            focus_key TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            priority INTEGER NOT NULL DEFAULT 50,
            reason TEXT NOT NULL DEFAULT '',
            last_active_at TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, focus_key)
        );
CREATE TABLE IF NOT EXISTS expression_intents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            reply_text TEXT NOT NULL DEFAULT '',
            emotion TEXT NOT NULL DEFAULT '',
            emotion_category TEXT NOT NULL DEFAULT '',
            emoji_intent TEXT NOT NULL DEFAULT '',
            action_intent TEXT NOT NULL DEFAULT '',
            send_emoji INTEGER NOT NULL DEFAULT 0,
            emoji_id INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'proactive_reply',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS emoji_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            emotions TEXT NOT NULL DEFAULT '',
            source_scope TEXT NOT NULL DEFAULT '',
            source_message_id TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            used_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_hash)
        );
CREATE TABLE IF NOT EXISTS focus_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL DEFAULT 'topic',
            target_id TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            priority INTEGER NOT NULL DEFAULT 50,
            reason TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(target_type, target_id, scope)
        );
CREATE TABLE IF NOT EXISTS life_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL DEFAULT '',
            meaning TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT '',
            scene TEXT NOT NULL DEFAULT '',
            examples TEXT NOT NULL DEFAULT '',
            familiarity INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'chat_memory',
            confidence REAL NOT NULL DEFAULT 1.0,
            last_seen TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(term, scope)
        );
CREATE TABLE IF NOT EXISTS memory_boundaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_scope TEXT NOT NULL DEFAULT '',
            target_scope TEXT NOT NULL DEFAULT '',
            policy TEXT NOT NULL DEFAULT 'ask',
            reason TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_scope, target_scope)
        );
CREATE TABLE IF NOT EXISTS memory_maintenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            merged_count INTEGER NOT NULL DEFAULT 0,
            corrected_count INTEGER NOT NULL DEFAULT 0,
            pruned_count INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
"""
