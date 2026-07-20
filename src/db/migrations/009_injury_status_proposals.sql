-- Brukeroppgitte skadestatus-endringer må godkjennes før de endrer coaching-gates.
CREATE TABLE injury_status_proposals (
    id INTEGER PRIMARY KEY,
    target_injury_id INTEGER REFERENCES injuries(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    coach_answer TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'applied', 'discarded')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    applied_at TEXT
);
CREATE INDEX idx_injury_status_proposals_status
    ON injury_status_proposals(status, created_at);
