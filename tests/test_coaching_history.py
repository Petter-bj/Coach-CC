"""Tester for `last_top_set` og relaterte history-helpers.

Setter inn syntetiske workouts + strength_sets og verifiserer at CLI-en
identifiserer riktig topp-sett.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.coaching.history import (
    exercise_sessions_count,
    known_exercises,
    last_top_set,
)
from src.db.connection import configure
from src.db.migrations import migrate


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    configure(c)
    migrate(c)
    yield c
    c.close()


def _insert_session(
    conn: sqlite3.Connection,
    started_at: str,
    local_date: str,
    sets: list[tuple[str, int, int, float | None, int | None]],
    source: str = "hevy",
) -> int:
    """Helper: insert workout + session + sets, returnerer workout_id.

    sets: liste av (exercise, set_num, reps, weight_kg, rpe)
    """
    cur = conn.execute(
        """
        INSERT INTO workouts (source, external_id, started_at_utc, timezone,
                              local_date, type)
        VALUES (?, ?, ?, 'Europe/Oslo', ?, 'strength_training')
        """,
        (source, f"ext-{started_at}", started_at, local_date),
    )
    wid = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO strength_sessions (workout_id) VALUES (?)", (wid,),
    )
    sid = cur.lastrowid
    for ex, n, r, w, rpe in sets:
        conn.execute(
            """
            INSERT INTO strength_sets
                (session_id, exercise, set_num, reps, weight_kg, rpe)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sid, ex, n, r, w, rpe),
        )
    conn.commit()
    return wid


def test_last_top_set_picks_heaviest_with_most_reps(conn) -> None:
    _insert_session(
        conn,
        started_at="2026-04-20T10:00:00Z",
        local_date="2026-04-20",
        sets=[
            ("Bench Press", 1, 8, 80.0, 7),
            ("Bench Press", 2, 8, 80.0, 8),
            ("Bench Press", 3, 6, 80.0, 9),  # Samme vekt, færre reps
        ],
    )
    top = last_top_set(conn, "Bench Press")
    assert top is not None
    assert top["reps"] == 8
    assert top["weight_kg"] == 80.0


def test_last_top_set_picks_most_recent_session(conn) -> None:
    _insert_session(
        conn, "2026-04-10T10:00:00Z", "2026-04-10",
        [("Bench Press", 1, 10, 75.0, 8)],
    )
    _insert_session(
        conn, "2026-04-18T10:00:00Z", "2026-04-18",
        [("Bench Press", 1, 6, 80.0, 8)],
    )
    top = last_top_set(conn, "Bench Press")
    assert top["weight_kg"] == 80.0
    assert top["reps"] == 6
    assert top["local_date"] == "2026-04-18"


def test_last_top_set_case_insensitive(conn) -> None:
    _insert_session(
        conn, "2026-04-20T10:00:00Z", "2026-04-20",
        [("Bench Press", 1, 8, 80.0, 7)],
    )
    assert last_top_set(conn, "bench press") is not None
    assert last_top_set(conn, "BENCH PRESS") is not None


def test_last_top_set_none_for_unknown_exercise(conn) -> None:
    _insert_session(
        conn, "2026-04-20T10:00:00Z", "2026-04-20",
        [("Bench Press", 1, 8, 80.0, 7)],
    )
    assert last_top_set(conn, "Deadlift") is None


def test_last_top_set_respects_within_days(conn) -> None:
    # Økt i dag (nå)
    _insert_session(
        conn,
        started_at="2026-04-21T10:00:00Z",  # i dag
        local_date="2026-04-21",
        sets=[("Squat", 1, 8, 100.0, 8)],
    )
    top = last_top_set(conn, "Squat", within_days=7)
    assert top is not None

    # Sett within_days=1 og sett datoen i fremtiden — skal fortsatt være inkludert
    # (date('now', '-1 days') < 2026-04-21)
    top = last_top_set(conn, "Squat", within_days=1)
    assert top is not None


def test_last_top_set_excludes_superseded(conn) -> None:
    wid1 = _insert_session(
        conn, "2026-04-20T10:00:00Z", "2026-04-20",
        [("Pull Up", 1, 10, None, 7)],
        source="strength",
    )
    wid2 = _insert_session(
        conn, "2026-04-20T11:00:00Z", "2026-04-20",
        [("Pull Up", 1, 5, 20.0, 8)],
        source="hevy",
    )
    # Mark strength som superseded av hevy
    conn.execute("UPDATE workouts SET superseded_by = ? WHERE id = ?",
                 (wid2, wid1))
    conn.commit()

    top = last_top_set(conn, "Pull Up")
    # Bør plukke hevy-raden (superseded strength skal ignoreres)
    assert top["source"] == "hevy"
    assert top["weight_kg"] == 20.0


def test_bodyweight_set_returns_none_weight(conn) -> None:
    _insert_session(
        conn, "2026-04-20T10:00:00Z", "2026-04-20",
        [("Push Up", 1, 20, None, 6)],
    )
    top = last_top_set(conn, "Push Up")
    assert top["weight_kg"] is None
    assert top["reps"] == 20


# ---------------------------------------------------------------------------
# exercise_sessions_count
# ---------------------------------------------------------------------------


def test_sessions_count(conn) -> None:
    _insert_session(
        conn, "2026-04-10T10:00:00Z", "2026-04-10",
        [("Bench Press", 1, 8, 80, 7)],
    )
    _insert_session(
        conn, "2026-04-18T10:00:00Z", "2026-04-18",
        [("Bench Press", 1, 6, 80, 8),
         ("Bench Press", 2, 6, 80, 9)],
    )
    assert exercise_sessions_count(conn, "Bench Press") == 2


# ---------------------------------------------------------------------------
# known_exercises
# ---------------------------------------------------------------------------


def test_known_exercises_lists_and_sorts(conn) -> None:
    _insert_session(
        conn, "2026-04-10T10:00:00Z", "2026-04-10",
        [("Bench Press", 1, 8, 80, 7)],
    )
    _insert_session(
        conn, "2026-04-20T10:00:00Z", "2026-04-20",
        [("Squat", 1, 5, 100, 8),
         ("Squat", 2, 5, 100, 8)],
    )
    rows = known_exercises(conn)
    assert len(rows) == 2
    # Nyeste først
    assert rows[0]["exercise"] == "Squat"
    assert rows[0]["last_seen"] == "2026-04-20"
    assert rows[1]["exercise"] == "Bench Press"
