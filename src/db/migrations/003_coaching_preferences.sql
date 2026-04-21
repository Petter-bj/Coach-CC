-- 003_coaching_preferences.sql
-- Legger til to KV-tabeller for coaching-konfigurasjon:
--
-- user_preferences       — globale settings (training_priority, default rep-window, ...)
-- exercise_preferences   — per-øvelse-overstyringer (rep-window, increment, type)
--
-- Dobbel progresjon er grunnregelen: topp-sett når rep_max → øk vekt +increment_kg,
-- reset til rep_min. Under rep_max → samme vekt, push +1 rep.

CREATE TABLE user_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Seed defaults. Brukeren kan overstyre via `src.cli.prefs set`.
INSERT INTO user_preferences (key, value) VALUES
    ('training_priority', 'cardio'),
    ('strength_rep_min_default', '6'),
    ('strength_rep_max_default', '10'),
    ('strength_increment_kg_default', '2.5');

-- Per-øvelse-overstyringer. NULL-felt = bruk defaults fra user_preferences.
-- exercise_lower er nøkkel for case-insensitive lookup mot strength_sets.exercise.
CREATE TABLE exercise_preferences (
    exercise_lower TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    rep_min INTEGER,
    rep_max INTEGER,
    increment_kg REAL,
    exercise_type TEXT CHECK (exercise_type IS NULL
                              OR exercise_type IN ('compound', 'isolation')),
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
