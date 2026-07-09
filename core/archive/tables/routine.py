DAILY_SQL = """
-- 每日生活状态
CREATE TABLE IF NOT EXISTS days (
            date TEXT PRIMARY KEY,
            outfit TEXT NOT NULL DEFAULT '',
            weather TEXT NOT NULL DEFAULT '',
            time_period TEXT NOT NULL DEFAULT '',
            memo TEXT NOT NULL DEFAULT '',
            weather_last_update INTEGER NOT NULL DEFAULT 0,
            weather_temp REAL,
            weather_condition TEXT NOT NULL DEFAULT '',
            weather_temp_desc TEXT NOT NULL DEFAULT '',
            weather_outfit_hint TEXT NOT NULL DEFAULT '',
            weather_activity_hint TEXT NOT NULL DEFAULT '',
            weather_is_hot INTEGER NOT NULL DEFAULT 0,
            weather_is_warm INTEGER NOT NULL DEFAULT 0,
            weather_is_cool INTEGER NOT NULL DEFAULT 0,
            weather_is_cold INTEGER NOT NULL DEFAULT 0,
            weather_is_rainy INTEGER NOT NULL DEFAULT 0,
            weather_is_sunny INTEGER NOT NULL DEFAULT 0,
            weather_is_cloudy INTEGER NOT NULL DEFAULT 0,
            weather_is_foggy INTEGER NOT NULL DEFAULT 0,
            meta_theme TEXT NOT NULL DEFAULT '',
            meta_mood TEXT NOT NULL DEFAULT '',
            meta_style TEXT NOT NULL DEFAULT '',
            meta_hair TEXT NOT NULL DEFAULT ''
        );
CREATE TABLE IF NOT EXISTS timelines (
            date TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            time TEXT NOT NULL DEFAULT '',
            activity TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(date, sort_order),
            FOREIGN KEY(date) REFERENCES days(date) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS outfit_history (
            date TEXT NOT NULL,
            period TEXT NOT NULL,
            outfit TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(date, period),
            FOREIGN KEY(date) REFERENCES days(date) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS day_meta (
            date TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(date, key),
            FOREIGN KEY(date) REFERENCES days(date) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS states (
            date TEXT PRIMARY KEY,
            energy INTEGER,
            mood TEXT NOT NULL DEFAULT '',
            mood_score INTEGER,
            busyness INTEGER,
            social INTEGER,
            stress INTEGER,
            focus INTEGER,
            sleepiness INTEGER,
            outgoing INTEGER,
            emotional_stability INTEGER,
            interaction_capacity INTEGER,
            boredom INTEGER,
            fishing INTEGER,
            attention_openness INTEGER,
            watch_state TEXT NOT NULL DEFAULT '',
            interrupt_level TEXT NOT NULL DEFAULT '',
            interrupt_reason TEXT NOT NULL DEFAULT '',
            sleep_quality INTEGER,
            sleep_depth TEXT NOT NULL DEFAULT '',
            sleep_summary TEXT NOT NULL DEFAULT '',
            physiological_rhythm TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(date) REFERENCES days(date) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS state_logs (
            date TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            entry TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(date, sort_order),
            FOREIGN KEY(date) REFERENCES days(date) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS day_places (
            date TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL DEFAULT 'place',
            hint TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(date, sort_order),
            FOREIGN KEY(date) REFERENCES days(date) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS day_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            place TEXT NOT NULL DEFAULT '',
            importance TEXT NOT NULL DEFAULT 'normal',
            source TEXT NOT NULL DEFAULT 'daily',
            FOREIGN KEY(date) REFERENCES days(date) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS day_event_people (
            day_event_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            person TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(day_event_id, sort_order),
            FOREIGN KEY(day_event_id) REFERENCES day_events(id) ON DELETE CASCADE
        );
"""
