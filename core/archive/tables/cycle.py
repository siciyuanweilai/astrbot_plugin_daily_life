WEEKLY_SQL = """
-- 周计划与模板
CREATE TABLE IF NOT EXISTS week_plans (
            week_id TEXT PRIMARY KEY,
            theme TEXT NOT NULL DEFAULT '',
            template_id TEXT NOT NULL DEFAULT '',
            generated INTEGER NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS week_goals (
            week_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            goal TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(week_id, sort_order),
            FOREIGN KEY(week_id) REFERENCES week_plans(week_id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS week_hints (
            week_id TEXT NOT NULL,
            day_key TEXT NOT NULL,
            hint TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(week_id, day_key),
            FOREIGN KEY(week_id) REFERENCES week_plans(week_id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS week_suggestions (
            week_id TEXT NOT NULL,
            day_key TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            suggestion TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(week_id, day_key, sort_order),
            FOREIGN KEY(week_id) REFERENCES week_plans(week_id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS custom_week_templates (
            template_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            emoji TEXT NOT NULL DEFAULT '📅',
            weight REAL NOT NULL DEFAULT 0.1,
            enabled INTEGER NOT NULL DEFAULT 1,
            cooldown_weeks INTEGER NOT NULL DEFAULT 3,
            source TEXT NOT NULL DEFAULT 'custom',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS custom_week_template_goals (
            template_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            goal TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(template_id, sort_order),
            FOREIGN KEY(template_id) REFERENCES custom_week_templates(template_id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS custom_week_template_hints (
            template_id TEXT NOT NULL,
            day_key TEXT NOT NULL,
            hint TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(template_id, day_key),
            FOREIGN KEY(template_id) REFERENCES custom_week_templates(template_id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS custom_week_template_suggestions (
            template_id TEXT NOT NULL,
            day_key TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            suggestion TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(template_id, day_key, sort_order),
            FOREIGN KEY(template_id) REFERENCES custom_week_templates(template_id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS custom_week_template_tags (
            template_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            tag TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(template_id, sort_order),
            FOREIGN KEY(template_id) REFERENCES custom_week_templates(template_id) ON DELETE CASCADE
        );
"""
