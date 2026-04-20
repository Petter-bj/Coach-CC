-- Initial schema for Trening helsedata-system.
--
-- Konvensjoner:
--   * Alle timestamp-felter lagres som ISO 8601 TEXT (UTC for *_utc, lokal for *_local).
--   * `local_date` er eksplisitt YYYY-MM-DD; aldri beregnet via generated column
--     (SQLite støtter ikke timezone-konvertering).
--   * UNIQUE-constraints på naturlige nøkler sikrer idempotent sync.
--   * Fremmednøkler aktiveres per connection via `PRAGMA foreign_keys=ON` (src/db/connection.py).

-- =========================================================================
-- Migreringstracking
-- =========================================================================
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- =========================================================================
-- Kanonisk øktmodell
-- =========================================================================
-- Hver trenings-økt har én rad i `workouts` uansett kilde. Kilde-spesifikke
-- detaljtabeller peker hit via workout_id.
CREATE TABLE workouts (
    id INTEGER PRIMARY KEY,
    external_id TEXT,                          -- ID i kildesystem (garmin_activity_id, c2_result_id, etc.)
    source TEXT NOT NULL CHECK (source IN ('garmin', 'concept2', 'strength', 'manual')),
    started_at_utc TEXT NOT NULL,              -- ISO 8601
    timezone TEXT NOT NULL DEFAULT 'Europe/Oslo',
    local_date TEXT NOT NULL,                  -- YYYY-MM-DD i timezone
    duration_sec INTEGER,
    type TEXT,                                 -- running, skierg, indoor_rowing, strength_training, ...
    distance_m REAL,
    avg_hr INTEGER,
    calories INTEGER,
    rpe INTEGER CHECK (rpe IS NULL OR (rpe BETWEEN 0 AND 10)),
    session_load REAL,                         -- rpe * duration_min
    superseded_by INTEGER REFERENCES workouts(id) ON DELETE SET NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (source, external_id)
);
CREATE INDEX idx_workouts_local_date ON workouts(local_date);
CREATE INDEX idx_workouts_started_at ON workouts(started_at_utc);

-- FIT-parset tidsserie (per-sekund sample fra Garmin eller Concept2)
CREATE TABLE workout_samples (
    workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    t_offset_sec INTEGER NOT NULL,             -- sekunder fra workout-start
    hr INTEGER,
    pace_sec_per_km REAL,
    speed_m_per_sec REAL,
    cadence INTEGER,                           -- strokes/minute eller steps/minute
    power_w INTEGER,
    distance_m REAL,                           -- kumulativ distanse
    altitude_m REAL,
    vertical_oscillation_mm REAL,
    ground_contact_ms INTEGER,
    stride_length_mm INTEGER,
    PRIMARY KEY (workout_id, t_offset_sec)
);

-- =========================================================================
-- Kilde-spesifikke øktdetaljer
-- =========================================================================
CREATE TABLE garmin_activity_details (
    workout_id INTEGER PRIMARY KEY REFERENCES workouts(id) ON DELETE CASCADE,
    garmin_activity_id INTEGER NOT NULL UNIQUE,
    activity_name TEXT,
    activity_type_key TEXT,                    -- running, indoor_rowing, strength_training
    activity_type_parent_id INTEGER,
    moving_duration_sec REAL,
    elevation_gain_m REAL,
    elevation_loss_m REAL,
    avg_speed_m_per_sec REAL,
    max_speed_m_per_sec REAL,
    max_hr INTEGER,
    start_latitude REAL,
    start_longitude REAL,
    has_polyline INTEGER,                      -- 0/1
    device_id INTEGER,
    fit_file_path TEXT,                        -- relativ sti fra FIT_FILES_DIR
    raw_json TEXT                              -- full JSON for fremtidige felter
);

CREATE TABLE concept2_session_details (
    workout_id INTEGER PRIMARY KEY REFERENCES workouts(id) ON DELETE CASCADE,
    c2_result_id INTEGER NOT NULL UNIQUE,
    type TEXT NOT NULL,                        -- skierg | rower | bikeerg
    time_tenths INTEGER,                       -- total tid i 1/10 sek (Concept2 native)
    workout_type TEXT,                         -- FixedDistance, VariableInterval, etc.
    source TEXT,                               -- ErgData iOS, PM5, manual
    avg_pace_500m_sec REAL,                    -- beregnet
    avg_watts REAL,
    avg_stroke_rate INTEGER,
    stroke_count INTEGER,
    drag_factor INTEGER,
    rest_distance_m INTEGER,
    rest_time_tenths INTEGER,
    verified INTEGER,                          -- 0/1
    fit_file_path TEXT,
    raw_json TEXT
);

CREATE TABLE concept2_intervals (
    id INTEGER PRIMARY KEY,
    session_details_workout_id INTEGER NOT NULL REFERENCES concept2_session_details(workout_id) ON DELETE CASCADE,
    interval_num INTEGER NOT NULL,
    machine TEXT,                              -- skierg, rower
    type TEXT,                                 -- time, distance
    time_tenths INTEGER,
    distance_m INTEGER,
    calories_total INTEGER,
    stroke_rate INTEGER,
    rest_distance_m INTEGER,
    rest_time_tenths INTEGER,
    hr_min INTEGER,
    hr_max INTEGER,
    hr_avg INTEGER,
    UNIQUE (session_details_workout_id, interval_num)
);

-- =========================================================================
-- Styrke-staging-flyt
-- =========================================================================
CREATE TABLE strength_sessions_pending (
    id INTEGER PRIMARY KEY,
    received_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    screenshot_path TEXT,                      -- relativ til CACHES
    started_at_local TEXT,                     -- YYYY-MM-DDTHH:MM (fra bildet eller brukerens input)
    parsed_json TEXT NOT NULL,                 -- full strukturert parse fra Claude
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'rejected')),
    telegram_message_id TEXT,
    resolved_at_utc TEXT
);

CREATE TABLE strength_sessions (
    id INTEGER PRIMARY KEY,
    workout_id INTEGER NOT NULL UNIQUE REFERENCES workouts(id) ON DELETE CASCADE,
    confirmed_from_pending_id INTEGER REFERENCES strength_sessions_pending(id) ON DELETE SET NULL
);

CREATE TABLE strength_sets (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES strength_sessions(id) ON DELETE CASCADE,
    exercise TEXT NOT NULL,
    set_num INTEGER NOT NULL,
    reps INTEGER NOT NULL CHECK (reps > 0),
    weight_kg REAL,
    rpe INTEGER CHECK (rpe IS NULL OR (rpe BETWEEN 0 AND 10)),
    e1rm_kg REAL,                              -- Epley: weight * (1 + reps/30)
    notes TEXT,
    UNIQUE (session_id, set_num, exercise)
);
CREATE INDEX idx_strength_sets_exercise ON strength_sets(exercise);

-- =========================================================================
-- Garmin daglige aggregater
-- =========================================================================
CREATE TABLE garmin_daily (
    local_date TEXT PRIMARY KEY,
    resting_hr INTEGER,
    body_battery_min INTEGER,
    body_battery_max INTEGER,
    training_readiness_score INTEGER,
    training_readiness_level TEXT,
    acute_load INTEGER,
    recovery_time_hours INTEGER,
    vo2max REAL,
    spo2_avg REAL,
    spo2_lowest REAL,
    stress_avg INTEGER,
    stress_max INTEGER,
    steps INTEGER,
    step_goal INTEGER,
    distance_m INTEGER,
    total_calories INTEGER,
    active_calories INTEGER,
    bmr_calories INTEGER,
    intensity_minutes_moderate INTEGER,
    intensity_minutes_vigorous INTEGER,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE garmin_sleep (
    local_date TEXT PRIMARY KEY,
    sleep_start_utc TEXT,
    sleep_end_utc TEXT,
    duration_sec INTEGER,
    deep_sec INTEGER,
    light_sec INTEGER,
    rem_sec INTEGER,
    awake_sec INTEGER,
    nap_sec INTEGER,
    sleep_score INTEGER,
    sleep_score_qualifier TEXT,                -- GOOD, FAIR, POOR, EXCELLENT
    avg_respiration REAL,
    lowest_respiration REAL,
    sleep_from_device INTEGER,                 -- 0/1
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE garmin_hrv (
    local_date TEXT PRIMARY KEY,
    last_night_avg_ms INTEGER,                 -- hrvSummary.lastNightAvg
    last_night_5min_high_ms INTEGER,
    weekly_avg_ms INTEGER,
    baseline_low_upper INTEGER,
    baseline_balanced_low INTEGER,
    baseline_balanced_upper INTEGER,
    status TEXT,                               -- NONE | BALANCED | UNBALANCED | LOW | POOR
    feedback_phrase TEXT,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- =========================================================================
-- Withings — vekt og kroppssammensetning
-- =========================================================================
CREATE TABLE withings_weight (
    grpid INTEGER PRIMARY KEY,                 -- Withings' egen måle-gruppe-id
    measured_at_utc TEXT NOT NULL,             -- ISO 8601
    timezone TEXT NOT NULL,
    local_date TEXT NOT NULL,
    weight_kg REAL,
    fat_ratio_pct REAL,
    fat_mass_kg REAL,
    fat_free_mass_kg REAL,
    muscle_mass_kg REAL,
    bone_mass_kg REAL,
    hydration_kg REAL,
    deviceid TEXT,
    model TEXT,                                -- Body Smart, etc.
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_withings_weight_local_date ON withings_weight(local_date);

-- =========================================================================
-- Yazio — kosthold
-- =========================================================================
CREATE TABLE yazio_daily (
    local_date TEXT PRIMARY KEY,
    kcal REAL,
    protein_g REAL,
    carbs_g REAL,
    fat_g REAL,
    steps INTEGER,                             -- Yazio tracker dette også
    water_ml INTEGER,
    kcal_goal REAL,
    protein_goal_g REAL,
    carbs_goal_g REAL,
    fat_goal_g REAL,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE yazio_meals (
    local_date TEXT NOT NULL,
    meal TEXT NOT NULL CHECK (meal IN ('breakfast', 'lunch', 'dinner', 'snack')),
    kcal REAL,
    protein_g REAL,
    carbs_g REAL,
    fat_g REAL,
    energy_goal_kcal REAL,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (local_date, meal)
);

CREATE TABLE yazio_consumed_items (
    id TEXT PRIMARY KEY,                       -- Yazio UUID
    local_date TEXT NOT NULL,
    daytime TEXT,                              -- breakfast | lunch | dinner | snack
    type TEXT,                                 -- product | simple | recipe
    product_id TEXT,
    amount REAL,
    serving TEXT,
    serving_quantity REAL,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_yazio_consumed_local_date ON yazio_consumed_items(local_date);

-- =========================================================================
-- Coaching-kontekst
-- =========================================================================
CREATE TABLE wellness_daily (
    local_date TEXT PRIMARY KEY,
    sleep_quality INTEGER CHECK (sleep_quality IS NULL OR (sleep_quality BETWEEN 1 AND 10)),
    muscle_soreness INTEGER CHECK (muscle_soreness IS NULL OR (muscle_soreness BETWEEN 1 AND 10)),
    motivation INTEGER CHECK (motivation IS NULL OR (motivation BETWEEN 1 AND 10)),
    energy INTEGER CHECK (energy IS NULL OR (energy BETWEEN 1 AND 10)),
    illness_flag INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE goals (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    target_date TEXT,                          -- YYYY-MM-DD
    metric TEXT,                               -- e.g. 10k_time_sec, bench_1rm_kg, weight_kg
    target_value REAL,
    priority TEXT CHECK (priority IS NULL OR priority IN ('A', 'B', 'C')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'achieved', 'abandoned', 'on_hold')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    resolved_at TEXT
);

CREATE TABLE training_blocks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('base', 'build', 'peak', 'taper', 'recovery')),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    primary_goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE user_baselines (
    metric TEXT NOT NULL,                      -- resting_hr, sleep_score, weight_kg, ...
    window_days INTEGER NOT NULL CHECK (window_days IN (7, 14, 30, 90)),
    value REAL,
    median REAL,
    mad REAL,                                  -- median absolute deviation (for outlier-deteksjon)
    sample_size INTEGER,
    computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    insufficient_data INTEGER NOT NULL DEFAULT 0,  -- 0 hvis nok data, 1 ellers
    PRIMARY KEY (metric, window_days)
);

CREATE TABLE intake_log (
    id INTEGER PRIMARY KEY,
    logged_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    timezone TEXT NOT NULL DEFAULT 'Europe/Oslo',
    local_date TEXT NOT NULL,
    alcohol_units REAL,                        -- 1 enhet ≈ 12g ren alkohol
    caffeine_mg INTEGER,
    notes TEXT
);
CREATE INDEX idx_intake_local_date ON intake_log(local_date);

CREATE TABLE injuries (
    id INTEGER PRIMARY KEY,
    body_part TEXT NOT NULL,
    severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 3),  -- 1=niggle, 3=alvorlig
    started_at TEXT NOT NULL,                  -- YYYY-MM-DD
    resolved_at TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'healing', 'resolved')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE planned_sessions (
    id INTEGER PRIMARY KEY,
    planned_date TEXT NOT NULL,                -- YYYY-MM-DD
    type TEXT,                                 -- run_easy, intervals, skierg, strength_upper, ...
    description TEXT,
    target_metrics TEXT,                       -- JSON
    workout_id INTEGER REFERENCES workouts(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'completed', 'skipped', 'modified')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_planned_sessions_date ON planned_sessions(planned_date);

CREATE TABLE context_log (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL CHECK (category IN ('travel', 'illness', 'stress', 'life_event', 'other')),
    starts_on TEXT NOT NULL,                   -- YYYY-MM-DD
    ends_on TEXT,                              -- NULL = pågående
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- =========================================================================
-- Sync-state og drift
-- =========================================================================
CREATE TABLE source_stream_state (
    source TEXT NOT NULL,                      -- garmin, withings, concept2, yazio
    stream TEXT NOT NULL,                      -- daily, hrv, sleep, activities, weight, ...
    last_successful_upper_bound TEXT,          -- ISO timestamp eller YYYY-MM-DD
    last_successful_sync_at TEXT,
    last_error_at TEXT,
    last_error_message TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    PRIMARY KEY (source, stream)
);

CREATE TABLE sync_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    stream TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'error', 'skipped')),
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_updated INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
CREATE INDEX idx_sync_runs_source_time ON sync_runs(source, started_at);

CREATE TABLE alerts (
    id INTEGER PRIMARY KEY,
    source TEXT,
    level TEXT NOT NULL CHECK (level IN ('info', 'warning', 'error')),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    acknowledged_at TEXT
);
CREATE INDEX idx_alerts_unacknowledged ON alerts(created_at) WHERE acknowledged_at IS NULL;
