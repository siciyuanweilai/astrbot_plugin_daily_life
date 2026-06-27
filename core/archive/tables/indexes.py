INDEX_SQL = """
-- 查询索引
CREATE INDEX IF NOT EXISTS idx_events_recent ON events(id DESC);
CREATE INDEX IF NOT EXISTS idx_places_recent ON places(last_seen DESC, visits DESC);
CREATE INDEX IF NOT EXISTS idx_relationships_recent ON relationships(last_seen DESC, interactions DESC);
CREATE INDEX IF NOT EXISTS idx_relationship_notes_recent ON relationship_notes(profile_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_relationship_points_recent ON relationship_points(profile_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_chat_summaries_recent ON chat_summaries(id DESC, date DESC);
CREATE INDEX IF NOT EXISTS idx_group_environments_recent ON group_environments(id DESC, date DESC);
CREATE INDEX IF NOT EXISTS idx_message_visibility_recent ON message_visibility(id DESC, date DESC);
CREATE INDEX IF NOT EXISTS idx_action_decisions_recent ON action_decisions(id DESC, date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_reviews_recent ON daily_reviews(date DESC);
CREATE INDEX IF NOT EXISTS idx_preferences_rank ON preferences(weight DESC, last_seen DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_life_events_recent ON life_events(date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_life_episodes_recent ON life_episodes(date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_target ON memory_evidence(target_type, target_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_feedback_recent ON behavior_feedback(date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_reply_effects_recent ON reply_effects(updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_memory_corrections_target ON memory_corrections(target_type, target_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_expression_profiles_recent ON expression_profiles(updated_at DESC, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_expression_reviews_recent ON expression_reviews(id DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_patterns_recent ON behavior_patterns(last_seen DESC, confidence DESC, support_count DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_scenes_recent ON behavior_scenes(last_seen DESC, confidence DESC, support_count DESC);
CREATE INDEX IF NOT EXISTS idx_session_mid_summaries_recent ON session_mid_summaries(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_temporary_expression_states_active ON temporary_expression_states(expires_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_focus_slots_active ON focus_slots(expires_at, priority DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_expression_intents_recent ON expression_intents(id DESC);
CREATE INDEX IF NOT EXISTS idx_emoji_assets_available ON emoji_assets(status, used_count ASC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_focus_targets_enabled ON focus_targets(enabled, priority DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_life_terms_recent ON life_terms(last_seen DESC, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_memory_boundaries_enabled ON memory_boundaries(enabled, source_scope, target_scope);
CREATE INDEX IF NOT EXISTS idx_memory_maintenance_recent ON memory_maintenance(date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_custom_catalog_items_order ON custom_catalog_items(category, enabled, sort_order);
CREATE INDEX IF NOT EXISTS idx_custom_hair_styles_order ON custom_hair_styles(enabled, sort_order);
CREATE INDEX IF NOT EXISTS idx_builtin_item_states_lookup ON builtin_item_states(kind, scope, enabled);
CREATE INDEX IF NOT EXISTS idx_commitments_status_date ON commitments(status, trigger_date);
CREATE INDEX IF NOT EXISTS idx_day_commitments_date ON day_commitments(date);
"""
