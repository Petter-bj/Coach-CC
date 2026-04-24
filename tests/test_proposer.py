"""Tester for weekly plan proposer — pure logic mot in-memory DB."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from src.coaching.proposer import (
    ProposedSession,
    ProposedWeek,
    propose_week,
    shin_status,
    weekly_running_volume_km,
)
from src.db.connection import configure
from src.db.migrations import migrate


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    configure(c)
    migrate(c)
    # Set up HRmax pref for consistent HR-target computation
    c.execute(
        "INSERT INTO user_preferences (key, value) VALUES ('hr_max_garmin', '197')"
    )
    c.commit()
    yield c
    c.close()


def _insert_block(conn, phase: str = "base") -> None:
    today = date.today()
    conn.execute(
        """
        INSERT INTO training_blocks (name, phase, start_date, end_date)
        VALUES ('Rebuild base', ?, ?, ?)
        """,
        (phase, (today - timedelta(days=5)).isoformat(),
         (today + timedelta(days=25)).isoformat()),
    )
    conn.commit()


def _insert_run(conn, days_ago: int, distance_m: int) -> None:
    d = date.today() - timedelta(days=days_ago)
    conn.execute(
        """
        INSERT INTO workouts (source, external_id, started_at_utc, timezone,
                              local_date, type, distance_m, duration_sec)
        VALUES ('garmin', ?, ?, 'Europe/Oslo', ?, 'running', ?, ?)
        """,
        (f"test-{days_ago}", f"{d.isoformat()}T10:00:00Z", d.isoformat(),
         distance_m, int(distance_m * 0.4)),
    )
    conn.commit()


def _add_injury(conn, body_part: str, severity: int, status: str = "active") -> None:
    conn.execute(
        """
        INSERT INTO injuries (body_part, severity, status, started_at)
        VALUES (?, ?, ?, date('now'))
        """,
        (body_part, severity, status),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# State-lesere
# ---------------------------------------------------------------------------


def test_weekly_running_volume_sums_last_7d(conn) -> None:
    _insert_run(conn, 1, 5000)  # 5 km
    _insert_run(conn, 3, 7000)  # 7 km
    _insert_run(conn, 10, 8000)  # 8 km — out of 7d window
    km = weekly_running_volume_km(conn, days_back=7)
    assert km == 12.0


def test_weekly_running_volume_empty(conn) -> None:
    assert weekly_running_volume_km(conn) == 0.0


def test_shin_status_clear_when_no_injuries(conn) -> None:
    assert shin_status(conn) == "clear"


def test_shin_status_mild_severity_1(conn) -> None:
    _add_injury(conn, "Shin", 1)
    assert shin_status(conn) == "active_mild"


def test_shin_status_moderate_severity_2(conn) -> None:
    _add_injury(conn, "Legghinne", 2)
    assert shin_status(conn) == "active_moderate"


def test_shin_status_ignores_non_shin_injuries(conn) -> None:
    _add_injury(conn, "Skulder", 3)
    assert shin_status(conn) == "clear"


def test_shin_status_picks_max_severity(conn) -> None:
    _add_injury(conn, "Shin", 1)
    _add_injury(conn, "Legghinne", 2)
    assert shin_status(conn) == "active_moderate"


def test_shin_status_ignores_resolved(conn) -> None:
    _add_injury(conn, "Shin", 2, status="resolved")
    assert shin_status(conn) == "clear"


# ---------------------------------------------------------------------------
# propose_week — variant-valg
# ---------------------------------------------------------------------------


def test_propose_green_variant_when_no_issues(conn) -> None:
    _insert_block(conn, "base")
    _insert_run(conn, 2, 5000)
    _insert_run(conn, 5, 7000)
    monday = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
    p = propose_week(conn, monday)
    assert p.variant == "A_green"
    assert len(p.sessions) == 7
    assert p.phase == "base"


def test_propose_yellow_variant_with_mild_shin(conn) -> None:
    _insert_block(conn, "base")
    _add_injury(conn, "Shin", 1)
    monday = date.today() + timedelta(days=7)
    p = propose_week(conn, monday)
    assert p.variant == "B_yellow"
    # Skal ikke inneholde z3_run i gul-varianten
    assert not any(s.type == "z3_run" for s in p.sessions)


def test_propose_red_variant_with_severe_shin(conn) -> None:
    _insert_block(conn, "base")
    _add_injury(conn, "Shin", 3)
    monday = date.today() + timedelta(days=7)
    p = propose_week(conn, monday)
    assert p.variant == "C_red"
    # Ingen løp-økter i rød-varianten
    run_types = ("easy_run", "z3_run", "long_run")
    assert not any(s.type in run_types for s in p.sessions)


def test_propose_moderate_shin_triggers_red(conn) -> None:
    """Severity 2+ er hard-stop på løping (jf. running_ruling)."""
    _insert_block(conn, "base")
    _add_injury(conn, "Shin", 2)
    monday = date.today() + timedelta(days=7)
    p = propose_week(conn, monday)
    assert p.variant == "C_red"


# ---------------------------------------------------------------------------
# propose_week — sessions
# ---------------------------------------------------------------------------


def test_green_week_has_expected_session_types(conn) -> None:
    _insert_block(conn, "base")
    _insert_run(conn, 2, 5000)
    monday = date.today() + timedelta(days=7)
    p = propose_week(conn, monday)

    types = [s.type for s in p.sessions]
    assert "upper_1_push" in types
    assert "lower" in types
    assert "upper_2_pull" in types
    assert "long_run" in types


def test_session_dates_monotonic(conn) -> None:
    _insert_block(conn, "base")
    monday = date.today() + timedelta(days=7)
    p = propose_week(conn, monday)
    dates = [s.planned_date for s in p.sessions]
    assert dates == sorted(dates)
    assert dates[0] == monday.isoformat()


def test_hr_targets_use_synced_hr_max(conn) -> None:
    _insert_block(conn, "base")
    monday = date.today() + timedelta(days=7)
    p = propose_week(conn, monday)
    # Finn long_run — Z2, skal ha HR-target ca 72-82% av 197 = 141-161
    long_runs = [s for s in p.sessions if s.type == "long_run"]
    assert long_runs
    low, high = long_runs[0].hr_target
    assert 140 <= low <= 145
    assert 159 <= high <= 164


def test_reasoning_includes_volume(conn) -> None:
    _insert_block(conn, "base")
    _insert_run(conn, 2, 10000)
    monday = date.today() + timedelta(days=7)
    p = propose_week(conn, monday)
    reasoning_text = " ".join(p.reasoning).lower()
    assert "løp" in reasoning_text
    assert "variant" in reasoning_text


def test_estimated_run_km_reasonable(conn) -> None:
    _insert_block(conn, "base")
    _insert_run(conn, 2, 5000)
    _insert_run(conn, 5, 7000)
    monday = date.today() + timedelta(days=7)
    p = propose_week(conn, monday)
    # Grønn variant skal ha minst 2-3 løp, samlet 10+ km
    assert p.estimated_run_km >= 10.0
    assert p.estimated_run_km <= 40.0  # sane upper bound


def test_no_active_block_defaults_to_base(conn) -> None:
    monday = date.today() + timedelta(days=7)
    p = propose_week(conn, monday)
    assert p.phase == "base"  # fallback
    assert len(p.sessions) == 7
