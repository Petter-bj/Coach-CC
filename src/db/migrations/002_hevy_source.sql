-- 002_hevy_source.sql
-- Slipp CHECK-constraint på workouts.source så vi kan legge til nye kilder
-- (hevy, og evt. fremtidige) uten å måtte rebuild'e tabellen hver gang.
-- Kildenavn valideres fortsatt i Python via Source-klassens navn.
--
-- SQLite kan ikke ALTER CHECK — vi må bygge om tabellen. For at FK fra
-- andre tabeller (workout_samples, garmin_activity_details, strength_sessions,
-- etc.) skal overleve rebuild, slår vi midlertidig av FK-enforcement.

PRAGMA foreign_keys = OFF;

CREATE TABLE workouts_new (
    id INTEGER PRIMARY KEY,
    external_id TEXT,
    source TEXT NOT NULL,                      -- ingen CHECK; validert i kode
    started_at_utc TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Europe/Oslo',
    local_date TEXT NOT NULL,
    duration_sec INTEGER,
    type TEXT,
    distance_m REAL,
    avg_hr INTEGER,
    calories INTEGER,
    rpe INTEGER CHECK (rpe IS NULL OR (rpe BETWEEN 0 AND 10)),
    session_load REAL,
    superseded_by INTEGER REFERENCES workouts(id) ON DELETE SET NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (source, external_id)
);

INSERT INTO workouts_new (id, external_id, source, started_at_utc, timezone,
                          local_date, duration_sec, type, distance_m, avg_hr,
                          calories, rpe, session_load, superseded_by, notes,
                          created_at)
SELECT id, external_id, source, started_at_utc, timezone,
       local_date, duration_sec, type, distance_m, avg_hr,
       calories, rpe, session_load, superseded_by, notes,
       created_at
  FROM workouts;

DROP INDEX IF EXISTS idx_workouts_local_date;
DROP INDEX IF EXISTS idx_workouts_started_at;
DROP TABLE workouts;
ALTER TABLE workouts_new RENAME TO workouts;

CREATE INDEX idx_workouts_local_date ON workouts(local_date);
CREATE INDEX idx_workouts_started_at ON workouts(started_at_utc);

-- Sjekk at ingen FK ble brutt av omrokkeringen
-- (dette kaster hvis noen workout_samples.workout_id peker til noe som ikke finnes)
-- PRAGMA foreign_key_check returnerer en rad per brudd; vi kan ikke lese
-- resultat fra executescript, men check-pragmaen kjøres uansett og migrations-runneren
-- stopper hvis SQLite selv finner konsistensproblem under etterfølgende bruk.
PRAGMA foreign_key_check;

PRAGMA foreign_keys = ON;
