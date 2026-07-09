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
CREATE TABLE IF NOT EXISTS emotion_arcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            valence INTEGER NOT NULL DEFAULT 0,
            arousal INTEGER NOT NULL DEFAULT 50,
            intensity INTEGER NOT NULL DEFAULT 50,
            stability INTEGER NOT NULL DEFAULT 50,
            trigger TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            influence TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT 'state',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS physiological_rhythm_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'state',
            energy_curve TEXT NOT NULL DEFAULT '',
            body_label TEXT NOT NULL DEFAULT '',
            body_intensity INTEGER NOT NULL DEFAULT 0,
            body_source TEXT NOT NULL DEFAULT '',
            body_expires_at TEXT NOT NULL DEFAULT '',
            recovery_actions TEXT NOT NULL DEFAULT '',
            social_battery INTEGER NOT NULL DEFAULT 50,
            attention_state TEXT NOT NULL DEFAULT '',
            optional_cycle_enabled INTEGER NOT NULL DEFAULT 0,
            optional_cycle_label TEXT NOT NULL DEFAULT '',
            optional_cycle_intensity INTEGER NOT NULL DEFAULT 0,
            optional_cycle_source TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            lifecycle_kind TEXT NOT NULL DEFAULT 'transient',
            weight REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
CREATE TABLE IF NOT EXISTS life_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'daily_plan',
            subject TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            source TEXT NOT NULL DEFAULT 'autonomous_life',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            source_kind TEXT NOT NULL DEFAULT 'trusted',
            asset_type TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            sendable INTEGER NOT NULL DEFAULT 0,
            rejected_reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            used_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_hash)
        );
CREATE TABLE IF NOT EXISTS video_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            file_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT 'current',
            text TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            transcript TEXT NOT NULL DEFAULT '',
            transcript_source TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            note_source TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '',
            source_note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ready',
            error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            expires_at REAL NOT NULL DEFAULT 0,
            UNIQUE(cache_key)
        );
CREATE TABLE IF NOT EXISTS video_insight_details (
            insight_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            detail_text TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(insight_id, sort_order),
            FOREIGN KEY(insight_id) REFERENCES video_insights(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS video_insight_frames (
            insight_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            frame_text TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(insight_id, sort_order),
            FOREIGN KEY(insight_id) REFERENCES video_insights(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS reverse_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            prompt TEXT NOT NULL DEFAULT '',
            image_path TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            keywords TEXT NOT NULL DEFAULT '',
            ratio TEXT NOT NULL DEFAULT '',
            usage TEXT NOT NULL DEFAULT '',
            profile TEXT NOT NULL DEFAULT '',
            source_prompt TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
CREATE TABLE IF NOT EXISTS long_term_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'general',
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            source_table TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            weight REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active',
            expires_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, category, content, source_table, source_id)
        );
CREATE TABLE IF NOT EXISTS memory_episode_clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'life',
            first_date TEXT NOT NULL DEFAULT '',
            last_date TEXT NOT NULL DEFAULT '',
            memory_count INTEGER NOT NULL DEFAULT 0,
            weight REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT 'memory_quality',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, title, category)
        );
CREATE TABLE IF NOT EXISTS memory_episode_cluster_items (
            cluster_id INTEGER NOT NULL,
            memory_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(cluster_id, memory_id),
            FOREIGN KEY(cluster_id) REFERENCES memory_episode_clusters(id) ON DELETE CASCADE,
            FOREIGN KEY(memory_id) REFERENCES long_term_memories(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS memory_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            entity_type TEXT NOT NULL DEFAULT 'topic',
            name TEXT NOT NULL DEFAULT '',
            aliases TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL DEFAULT '',
            last_seen TEXT NOT NULL DEFAULT '',
            mention_count INTEGER NOT NULL DEFAULT 1,
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, entity_type, name)
        );
CREATE TABLE IF NOT EXISTS memory_entity_links (
            entity_id INTEGER NOT NULL,
            memory_id INTEGER NOT NULL,
            relation TEXT NOT NULL DEFAULT 'mentions',
            weight REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(entity_id, memory_id, relation),
            FOREIGN KEY(entity_id) REFERENCES memory_entities(id) ON DELETE CASCADE,
            FOREIGN KEY(memory_id) REFERENCES long_term_memories(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS memory_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT '',
            memory_id INTEGER NOT NULL DEFAULT 0,
            related_memory_id INTEGER NOT NULL DEFAULT 0,
            conflict_type TEXT NOT NULL DEFAULT 'tension',
            summary TEXT NOT NULL DEFAULT '',
            resolution TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, memory_id, related_memory_id, conflict_type)
        );
CREATE TABLE IF NOT EXISTS memory_decision_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL DEFAULT 0,
            memory_id INTEGER NOT NULL DEFAULT 0,
            influence TEXT NOT NULL DEFAULT '',
            weight REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(decision_id, memory_id),
            FOREIGN KEY(decision_id) REFERENCES life_decisions(id) ON DELETE CASCADE,
            FOREIGN KEY(memory_id) REFERENCES long_term_memories(id) ON DELETE CASCADE
        );
CREATE VIRTUAL TABLE IF NOT EXISTS long_term_memories_fts
            USING fts5(title, content, category, tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS long_term_memories_ai
            AFTER INSERT ON long_term_memories
            WHEN new.status = 'active'
            BEGIN
                INSERT INTO long_term_memories_fts(rowid, title, content, category)
                VALUES (new.id, new.title, new.content, new.category);
            END;
CREATE TRIGGER IF NOT EXISTS long_term_memories_ad
            AFTER DELETE ON long_term_memories
            BEGIN
                INSERT INTO long_term_memories_fts(long_term_memories_fts, rowid, title, content, category)
                VALUES ('delete', old.id, old.title, old.content, old.category);
            END;
CREATE TRIGGER IF NOT EXISTS long_term_memories_au
            AFTER UPDATE OF title, content, category, status ON long_term_memories
            BEGIN
                INSERT INTO long_term_memories_fts(long_term_memories_fts, rowid, title, content, category)
                VALUES ('delete', old.id, old.title, old.content, old.category);
                INSERT INTO long_term_memories_fts(rowid, title, content, category)
                SELECT new.id, new.title, new.content, new.category
                WHERE new.status = 'active';
            END;
"""
