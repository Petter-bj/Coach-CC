-- Blokkcoachen kan diskutere og foreslå periodisering, men et forslag må
-- ligge eksplisitt i databasen før brukeren får lov til å bruke det.

CREATE TABLE block_plan_proposals (
    id INTEGER PRIMARY KEY,
    target_block_id INTEGER REFERENCES training_blocks(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    coach_answer TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'applied', 'discarded')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    applied_at TEXT
);
CREATE INDEX idx_block_plan_proposals_target_status
    ON block_plan_proposals(target_block_id, status);
