"""Tester for den lille, personlige styrkekonteksten modellen får."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from src.coaching.preferences import set_exercise_prefs, set_pref
from src.coaching.strength_context import build_strength_context
from src.db.connection import configure
from src.db.migrations import migrate


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    configure(connection)
    migrate(connection)
    yield connection
    connection.close()


def _insert_strength_workout(conn: sqlite3.Connection) -> None:
    day = date.today() - timedelta(days=1)
    workout_id = conn.execute(
        """
        INSERT INTO workouts (source, external_id, started_at_utc, timezone, local_date, type)
        VALUES ('hevy', 'hevy-strength-context', ?, 'Europe/Oslo', ?, 'strength_training')
        """,
        (f"{day.isoformat()}T10:00:00Z", day.isoformat()),
    ).lastrowid
    session_id = conn.execute(
        "INSERT INTO strength_sessions (workout_id) VALUES (?)", (workout_id,)
    ).lastrowid
    conn.execute(
        """
        INSERT INTO strength_sets (session_id, exercise, set_num, reps, weight_kg, rpe)
        VALUES (?, 'Barbell Bench Press', 1, 8, 82.5, 8)
        """,
        (session_id,),
    )
    conn.commit()


def test_strength_context_is_uninitialized_with_only_seed_defaults(conn) -> None:
    context = build_strength_context(conn)

    assert context["initialized"] is False
    assert context["profile"]["training_priority"] == "cardio"
    assert context["profile"]["strength_rep_min_default"] == "6"
    assert context["exercise_overrides"] == []
    assert context["recent_exercise_history"] == []
    assert context["program_structure"]["principle"] == "stable_template_family"
    assert context["generation_rules"]["when_uninitialized"].startswith("You may propose")


def test_strength_context_combines_profile_overrides_and_recent_history(conn) -> None:
    set_pref(conn, "strength_goal", "styrke og muskelmasse")
    set_pref(conn, "strength_sessions_per_week", "2")
    set_pref(conn, "strength_preferred_exercises", "Barbell Bench Press, Front Squat")
    set_exercise_prefs(
        conn,
        "Barbell Bench Press",
        rep_min=6,
        rep_max=8,
        increment_kg=2.5,
        exercise_type="compound",
        notes="Prioritert hovedløft.",
    )
    _insert_strength_workout(conn)

    context = build_strength_context(conn)

    assert context["initialized"] is True
    assert context["profile"]["strength_goal"] == "styrke og muskelmasse"
    assert context["profile"]["strength_sessions_per_week"] == "2"
    assert context["exercise_overrides"] == [{
        "exercise": "Barbell Bench Press",
        "rep_min": 6,
        "rep_max": 8,
        "increment_kg": 2.5,
        "exercise_type": "compound",
        "notes": "Prioritert hovedløft.",
    }]
    history = context["recent_exercise_history"]
    assert history[0]["exercise"] == "Barbell Bench Press"
    assert history[0]["last_top_set"]["weight_kg"] == 82.5
    assert history[0]["last_top_set"]["reps"] == 8
