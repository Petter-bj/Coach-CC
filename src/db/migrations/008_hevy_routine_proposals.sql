-- Hevy-rutiner (maler) opprettes først etter et eksplisitt klikk i dashboardet.
CREATE TABLE hevy_routine_proposals (
    id INTEGER PRIMARY KEY,
    week_start TEXT NOT NULL,
    question TEXT NOT NULL,
    coach_answer TEXT NOT NULL,
    routine_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'applied', 'discarded')),
    hevy_routine_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    applied_at TEXT
);
CREATE INDEX idx_hevy_routine_proposals_week_status
    ON hevy_routine_proposals(week_start, status);
