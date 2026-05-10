"""Tester for reconcile_planned_sessions — type-matching + apply-logikk."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from src.coaching.reconcile_plan import (
    _match_workout_types,
    reconcile_planned_sessions,
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


def _add_planned(conn, days_ago: int, type_: str, status: str = "planned") -> int:
    d = date.today() - timedelta(days=days_ago)
    cur = conn.execute(
        """
        INSERT INTO planned_sessions (planned_date, type, description, status)
        VALUES (?, ?, '', ?)
        """,
        (d.isoformat(), type_, status),
    )
    conn.commit()
    return cur.lastrowid


def _add_workout(conn, days_ago: int, type_: str, source: str = "garmin") -> int:
    d = date.today() - timedelta(days=days_ago)
    cur = conn.execute(
        """
        INSERT INTO workouts (source, external_id, started_at_utc, timezone,
                              local_date, type, distance_m)
        VALUES (?, ?, ?, 'Europe/Oslo', ?, ?, 5000)
        """,
        (source, f"test-{days_ago}-{type_}",
         f"{d.isoformat()}T10:00:00Z", d.isoformat(), type_),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# _match_workout_types
# ---------------------------------------------------------------------------


def test_match_easy_run() -> None:
    assert "running" in _match_workout_types("easy_run")


def test_match_z3_run_variants() -> None:
    assert "running" in _match_workout_types("z3_run")
    assert "running" in _match_workout_types("run_z3")
    assert "running" in _match_workout_types("long_run_easy")


def test_match_skierg_includes_both_types() -> None:
    types = _match_workout_types("easy_skierg")
    assert "indoor_rowing" in types
    assert "skierg" in types


def test_match_skierg_z3_variants() -> None:
    types = _match_workout_types("skierg_z3")
    assert "indoor_rowing" in types


def test_match_strength_variants() -> None:
    assert "strength_training" in _match_workout_types("upper_1_push")
    assert "strength_training" in _match_workout_types("upper_2_pull_plus_prehab")
    assert "strength_training" in _match_workout_types("lower")
    assert "strength_training" in _match_workout_types("upper_strength")


def test_match_lower_plus_skierg_hybrid() -> None:
    """Hybrid-dag: lower + hard skierg matcher BÅDE strength + indoor_rowing."""
    types = _match_workout_types("lower_plus_hard_skierg")
    assert "strength_training" in types
    assert "indoor_rowing" in types


def test_match_no_match_for_rest() -> None:
    assert _match_workout_types("rest") is None
    assert _match_workout_types("prehab") is None


def test_match_empty() -> None:
    assert _match_workout_types("") is None
    assert _match_workout_types(None) is None


# ---------------------------------------------------------------------------
# reconcile_planned_sessions
# ---------------------------------------------------------------------------


def test_reconcile_matches_run(conn) -> None:
    plan_id = _add_planned(conn, 3, "easy_run")
    workout_id = _add_workout(conn, 3, "running")
    result = reconcile_planned_sessions(conn, since_days_ago=7, apply=True)
    assert result.matched == 1
    assert result.matches == [(plan_id, workout_id, "easy_run")]

    # Verifiser status oppdatert
    row = conn.execute(
        "SELECT status, workout_id FROM planned_sessions WHERE id = ?",
        (plan_id,),
    ).fetchone()
    assert row["status"] == "completed"
    assert row["workout_id"] == workout_id


def test_reconcile_dry_run_does_not_apply(conn) -> None:
    plan_id = _add_planned(conn, 3, "easy_run")
    _add_workout(conn, 3, "running")
    result = reconcile_planned_sessions(conn, since_days_ago=7, apply=False)
    assert result.matched == 1

    row = conn.execute(
        "SELECT status FROM planned_sessions WHERE id = ?", (plan_id,)
    ).fetchone()
    assert row["status"] == "planned"  # ikke endret


def test_reconcile_skips_already_completed(conn) -> None:
    _add_planned(conn, 3, "easy_run", status="completed")
    _add_workout(conn, 3, "running")
    result = reconcile_planned_sessions(conn, since_days_ago=7)
    assert result.already_completed == 1
    assert result.matched == 0


def test_reconcile_unmatched_when_no_workout(conn) -> None:
    _add_planned(conn, 3, "easy_run")
    result = reconcile_planned_sessions(conn, since_days_ago=7)
    assert result.unmatched == 1
    assert result.matched == 0


def test_reconcile_skips_unknown_type(conn) -> None:
    _add_planned(conn, 3, "rest")
    _add_workout(conn, 3, "running")
    result = reconcile_planned_sessions(conn, since_days_ago=7)
    assert result.no_type_map == 1
    assert result.matched == 0


def test_reconcile_excludes_superseded_workouts(conn) -> None:
    """Superseded workouts skal ikke matche planlagte økter."""
    plan_id = _add_planned(conn, 3, "easy_skierg")
    # Opprett indoor_rowing som er superseded, og en concept2 skierg som vinner
    rowing_id = _add_workout(conn, 3, "indoor_rowing", source="garmin")
    skierg_id = _add_workout(conn, 3, "skierg", source="concept2")
    # Markér rowing som superseded av skierg
    conn.execute(
        "UPDATE workouts SET superseded_by = ? WHERE id = ?",
        (skierg_id, rowing_id),
    )
    conn.commit()

    result = reconcile_planned_sessions(conn, since_days_ago=7)
    assert result.matched == 1
    # Skal ha matchet til den ikke-superseded skierg-økta, ikke rowing
    assert result.matches[0][1] == skierg_id


def test_reconcile_respects_since_days_window(conn) -> None:
    """Eldre planned_sessions enn since-grensen skal ignoreres."""
    _add_planned(conn, 60, "easy_run")
    _add_workout(conn, 60, "running")
    result = reconcile_planned_sessions(conn, since_days_ago=30)
    # Ikke i vinduet, examined er 0
    assert result.rows_examined == 0


def test_reconcile_skips_skipped_status(conn) -> None:
    """Hvis brukeren har eksplisitt markert som skipped, ikke overstyr."""
    _add_planned(conn, 3, "easy_run", status="skipped")
    _add_workout(conn, 3, "running")
    result = reconcile_planned_sessions(conn, since_days_ago=7)
    assert result.matched == 0


def test_reconcile_skierg_matches_concept2_skierg_type(conn) -> None:
    plan_id = _add_planned(conn, 3, "skierg_z3")
    skierg_id = _add_workout(conn, 3, "skierg", source="concept2")
    result = reconcile_planned_sessions(conn, since_days_ago=7)
    assert result.matched == 1
    assert result.matches[0][1] == skierg_id
