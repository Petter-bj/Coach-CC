"""Tester for strength-log-flow: schema-validering, PR-sjekk, insert."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest
from pydantic import ValidationError

from src.cli.strength import _epley, _insert_session, _pr_warnings, _local_to_utc
from src.db.connection import configure
from src.db.migrations import migrate
from src.schemas import StrengthExercise, StrengthSession, StrengthSet


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    configure(c)
    migrate(c)
    try:
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Epley
# ---------------------------------------------------------------------------


def test_epley_standard_case() -> None:
    # 80kg × 5 reps = 80 × (1 + 5/30) = 93.33
    assert _epley(80, 5) == 93.33


def test_epley_returns_none_for_zero_or_missing() -> None:
    assert _epley(None, 5) is None
    assert _epley(0, 5) is None
    assert _epley(100, 0) is None


# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------


def test_local_to_utc_summer_time() -> None:
    # Oslo i sommertid: UTC+2
    utc = _local_to_utc("2026-04-20T17:00")
    assert utc == "2026-04-20T15:00:00Z"


def test_local_to_utc_winter_time() -> None:
    # Oslo i vintertid: UTC+1
    utc = _local_to_utc("2026-01-15T17:00")
    assert utc == "2026-01-15T16:00:00Z"


# ---------------------------------------------------------------------------
# Schema-validering
# ---------------------------------------------------------------------------


def test_valid_session_parses() -> None:
    session = StrengthSession.model_validate({
        "started_at_local": "2026-04-20T17:00",
        "session_name": "Push",
        "exercises": [{
            "name": "Bench press",
            "sets": [{"reps": 8, "weight_kg": 80}]
        }]
    })
    assert session.total_sets() == 1
    assert session.local_date() == "2026-04-20"


def test_space_separator_in_datetime_accepted() -> None:
    """'2026-04-20 17:00' skal aksepteres (vanlig blanding)."""
    session = StrengthSession.model_validate({
        "started_at_local": "2026-04-20 17:00",
        "exercises": [{"name": "Squat", "sets": [{"reps": 5, "weight_kg": 100}]}]
    })
    assert session.started_at_local == "2026-04-20T17:00"


def test_invalid_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        StrengthSession.model_validate({
            "started_at_local": "neste tirsdag",
            "exercises": [{"name": "x", "sets": [{"reps": 5}]}]
        })


def test_empty_exercises_rejected() -> None:
    with pytest.raises(ValidationError):
        StrengthSession.model_validate({
            "started_at_local": "2026-04-20T17:00",
            "exercises": []
        })


def test_reps_range_enforced() -> None:
    with pytest.raises(ValidationError):
        StrengthSet.model_validate({"reps": 0})
    with pytest.raises(ValidationError):
        StrengthSet.model_validate({"reps": 200})


def test_rpe_range_enforced() -> None:
    with pytest.raises(ValidationError):
        StrengthSet.model_validate({"reps": 5, "rpe": 11})


def test_weight_without_reps_raises() -> None:
    with pytest.raises(ValidationError):
        StrengthSet.model_validate({"weight_kg": 80})  # reps mangler


# ---------------------------------------------------------------------------
# PR-sjekk
# ---------------------------------------------------------------------------


def _seed_existing_pr(conn: sqlite3.Connection, exercise: str, e1rm: float) -> None:
    """Seed en eksisterende strength_set med gitt e1RM."""
    conn.execute(
        """
        INSERT INTO workouts (source, external_id, started_at_utc, timezone,
                              local_date, type)
        VALUES ('strength', 'seed_1', '2026-01-01T12:00:00Z', 'Europe/Oslo',
                '2026-01-01', 'strength_training')
        """
    )
    wid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO strength_sessions (workout_id) VALUES (?)", (wid,)
    )
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Reverse Epley for å få vekt+reps som gir ønsket e1rm
    # e1rm = weight × (1 + reps/30). Med reps=5: weight = e1rm / (1 + 5/30)
    reps = 5
    weight = e1rm / (1 + reps / 30)
    conn.execute(
        """
        INSERT INTO strength_sets (session_id, exercise, set_num, reps,
                                    weight_kg, e1rm_kg)
        VALUES (?, ?, 1, ?, ?, ?)
        """,
        (sid, exercise, reps, round(weight, 1), e1rm),
    )
    conn.commit()


def test_pr_warning_triggered_above_threshold(conn: sqlite3.Connection) -> None:
    _seed_existing_pr(conn, "Squat", 100.0)

    session = StrengthSession.model_validate({
        "started_at_local": "2026-04-20T17:00",
        "exercises": [{
            "name": "Squat",
            "sets": [{"reps": 3, "weight_kg": 150}]  # e1RM = 165 = 1.65× PR
        }]
    })
    warnings = _pr_warnings(conn, session)
    assert len(warnings) == 1
    assert warnings[0]["exercise"] == "Squat"
    assert warnings[0]["factor"] >= 1.4


def test_pr_no_warning_for_incremental_progress(conn: sqlite3.Connection) -> None:
    _seed_existing_pr(conn, "Bench press", 100.0)

    # 5% økning — ikke nok til advarsel (krever 40%)
    session = StrengthSession.model_validate({
        "started_at_local": "2026-04-20T17:00",
        "exercises": [{
            "name": "Bench press",
            "sets": [{"reps": 3, "weight_kg": 95}]  # e1RM ~ 104.5
        }]
    })
    warnings = _pr_warnings(conn, session)
    assert warnings == []


def test_pr_case_insensitive_exercise_match(conn: sqlite3.Connection) -> None:
    _seed_existing_pr(conn, "Bench Press", 100.0)
    # Bruker lowercase
    session = StrengthSession.model_validate({
        "started_at_local": "2026-04-20T17:00",
        "exercises": [{
            "name": "bench press",
            "sets": [{"reps": 2, "weight_kg": 180}]  # enorm PR
        }]
    })
    warnings = _pr_warnings(conn, session)
    assert len(warnings) == 1


def test_pr_no_warning_for_new_exercise(conn: sqlite3.Connection) -> None:
    """Øvelse vi aldri har logget før skal ikke trigge advarsel."""
    session = StrengthSession.model_validate({
        "started_at_local": "2026-04-20T17:00",
        "exercises": [{
            "name": "Totally New Exercise",
            "sets": [{"reps": 5, "weight_kg": 500}]
        }]
    })
    warnings = _pr_warnings(conn, session)
    assert warnings == []


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


def test_insert_session_creates_all_rows(conn: sqlite3.Connection) -> None:
    session = StrengthSession.model_validate({
        "started_at_local": "2026-04-20T17:00",
        "session_name": "Push",
        "exercises": [
            {"name": "Bench", "sets": [
                {"reps": 8, "weight_kg": 70},
                {"reps": 6, "weight_kg": 75, "rpe": 8},
            ]},
            {"name": "Dip", "sets": [{"reps": 10}]},  # bodyweight
        ]
    })
    wid, sid, n = _insert_session(conn, session, None)
    assert n == 3

    count = conn.execute("SELECT COUNT(*) FROM strength_sets WHERE session_id=?", (sid,)).fetchone()[0]
    assert count == 3

    # e1RM beregnet for vektøvelser
    e1rm = conn.execute(
        "SELECT e1rm_kg FROM strength_sets WHERE session_id=? AND exercise='Bench' AND set_num=2",
        (sid,)
    ).fetchone()[0]
    assert e1rm is not None  # 75 × (1 + 6/30) = 90
    assert 89 < e1rm < 91

    # bodyweight-dip har e1rm=None
    dip_e1rm = conn.execute(
        "SELECT e1rm_kg FROM strength_sets WHERE session_id=? AND exercise='Dip'",
        (sid,)
    ).fetchone()[0]
    assert dip_e1rm is None


def test_insert_is_idempotent_via_external_id(conn: sqlite3.Connection) -> None:
    """Re-kjør samme økt → overwriter, duplikater ikke."""
    session = StrengthSession.model_validate({
        "started_at_local": "2026-04-20T17:00",
        "session_name": "Push",
        "exercises": [{"name": "Bench", "sets": [{"reps": 8, "weight_kg": 70}]}]
    })
    _insert_session(conn, session, None)
    _insert_session(conn, session, None)  # samme igjen

    workouts = conn.execute(
        "SELECT COUNT(*) FROM workouts WHERE source='strength'"
    ).fetchone()[0]
    assert workouts == 1
