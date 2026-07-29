WEEKLY_SQL = """
-- Weekly plans
CREATE TABLE IF NOT EXISTS week_plans (
            week_id TEXT PRIMARY KEY,
            theme TEXT NOT NULL DEFAULT '',
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
"""
