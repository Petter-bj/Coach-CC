-- Blokk er planmotorens overliggende lag. Den eksisterende training_blocks-
--tabellen beholder ansvar for periode/fase, mens én rad per uke beskriver
--den menneskelesbare progresjonen i blokken.

CREATE TABLE training_block_weeks (
    id INTEGER PRIMARY KEY,
    training_block_id INTEGER NOT NULL
        REFERENCES training_blocks(id) ON DELETE CASCADE,
    week_start TEXT NOT NULL,                  -- mandag, YYYY-MM-DD
    focus TEXT NOT NULL,
    progression_note TEXT,
    planned_volume_note TEXT,
    is_deload INTEGER NOT NULL DEFAULT 0 CHECK (is_deload IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (training_block_id, week_start)
);
CREATE INDEX idx_training_block_weeks_block_start
    ON training_block_weeks(training_block_id, week_start);

-- En økt kan senere knyttes til en reell blokk. Eksempelblokken i dashboardet
--lagres bevisst ikke, slik at den aldri blandes med brukerens faktiske plan.
ALTER TABLE planned_sessions
    ADD COLUMN training_block_id INTEGER
        REFERENCES training_blocks(id) ON DELETE SET NULL;
CREATE INDEX idx_planned_sessions_training_block
    ON planned_sessions(training_block_id, planned_date);

-- Modellen får aldri skrive planlagte økter direkte. Den kan kun opprette en
-- kandidat til endring her; status pending må gjennom eksplisitt brukerklikk
-- før API-et bruker operasjonene mot planned_sessions.
CREATE TABLE weekly_plan_proposals (
    id INTEGER PRIMARY KEY,
    week_start TEXT NOT NULL,
    question TEXT NOT NULL,
    coach_answer TEXT NOT NULL,
    operations_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'applied', 'discarded')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    applied_at TEXT
);
CREATE INDEX idx_weekly_plan_proposals_week_status
    ON weekly_plan_proposals(week_start, status);
