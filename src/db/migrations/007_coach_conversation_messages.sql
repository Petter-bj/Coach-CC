-- Blokkcoachen er en samtale over tid. Historikken er privat og liten, og
-- lagres på samme VPS som resten av brukerens dashboarddata slik at den ikke
-- forsvinner ved navigasjon, refresh eller bytte mellom egne enheter.

CREATE TABLE coach_conversation_messages (
    id INTEGER PRIMARY KEY,
    thread TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_coach_conversation_thread_id
    ON coach_conversation_messages(thread, id);
