-- Persistente, brukerbekreftede vurderinger av automatisk matchede økter.
--
-- planned_sessions.status='completed' betyr bare at Garmin/annen kilde har
-- matchet økten. session_reviews.status beskriver den separate review-flyten:
-- pending → reviewed. Én vurdering per planlagte økt gjør synken idempotent.

CREATE TABLE session_reviews (
    id INTEGER PRIMARY KEY,
    planned_session_id INTEGER NOT NULL UNIQUE
        REFERENCES planned_sessions(id) ON DELETE CASCADE,
    workout_id INTEGER NOT NULL
        REFERENCES workouts(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'reviewed')),
    coach_source TEXT NOT NULL DEFAULT 'coach_rules'
        CHECK (coach_source IN ('coach_rules', 'agent')),
    coach_comment TEXT NOT NULL,
    user_note TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    reviewed_at TEXT
);

CREATE INDEX idx_session_reviews_pending
    ON session_reviews(status, created_at)
    WHERE status = 'pending';
