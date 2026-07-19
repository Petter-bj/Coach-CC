"""Detaljkontrakt for gjennomførte økter, inkludert Hevy-styrkeøkter."""

from __future__ import annotations

import sqlite3

from src.api.workout import build_workout_detail
from src.db.connection import configure
from src.db.migrations import migrate


def test_hevy_workout_detail_includes_grouped_exercises_and_sets() -> None:
    conn = sqlite3.connect(":memory:")
    configure(conn)
    migrate(conn)
    try:
        workout_id = conn.execute(
            """
            INSERT INTO workouts (
                source, external_id, started_at_utc, timezone, local_date,
                type, duration_sec, notes
            ) VALUES (
                'hevy', 'hevy-123', '2026-07-18T16:00:00Z', 'Europe/Oslo',
                '2026-07-18', 'strength_training', 3120,
                'Økt: Overkropp A — Kontrollerte reps og god teknikk.'
            )
            """
        ).lastrowid
        session_id = conn.execute(
            "INSERT INTO strength_sessions (workout_id) VALUES (?)",
            (workout_id,),
        ).lastrowid
        conn.executemany(
            """
            INSERT INTO strength_sets (session_id, exercise, set_num, reps, weight_kg, rpe)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (session_id, "Benkpress", 2, 8, 70, 8),
                (session_id, "Benkpress", 1, 8, 70, 7),
                (session_id, "Sidehev", 1, 14, 8, 9),
            ],
        )
        conn.commit()

        detail = build_workout_detail(conn, workout_id)
    finally:
        conn.close()

    assert detail is not None
    assert detail["workout"]["source"] == "hevy"
    assert detail["strength_summary"] == {
        "set_count": 3,
        "exercise_count": 2,
        "volume_kg": 1232.0,
        "session_name": "Overkropp A",
        "exercises": [
            {
                "exercise": "Benkpress",
                "sets": [
                    {"set_num": 1, "reps": 8, "weight_kg": 70.0, "rpe": 7, "e1rm_kg": None, "notes": None},
                    {"set_num": 2, "reps": 8, "weight_kg": 70.0, "rpe": 8, "e1rm_kg": None, "notes": None},
                ],
            },
            {
                "exercise": "Sidehev",
                "sets": [
                    {"set_num": 1, "reps": 14, "weight_kg": 8.0, "rpe": 9, "e1rm_kg": None, "notes": None},
                ],
            },
        ],
    }
