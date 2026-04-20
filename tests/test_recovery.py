"""Tests for recovery-snapshot og anbefalings-regler."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from src.analysis.recovery import (
    ACR_RISK,
    ACR_SWEET_HIGH,
    ACR_SWEET_LOW,
    MIN_CHRONIC_DAYS,
    compute_load,
    recovery_snapshot,
)
from src.db.connection import configure
from src.db.migrations import migrate


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    configure(c)
    migrate(c)
    try:
        yield c
    finally:
        c.close()


def _add_workout(
    conn: sqlite3.Connection,
    day_offset: int,
    duration_min: int,
    *,
    rpe: int | None = None,
    session_load: float | None = None,
    type_: str = "running",
) -> int:
    d = (date.today() - timedelta(days=day_offset)).isoformat()
    cur = conn.execute(
        """
        INSERT INTO workouts (source, started_at_utc, timezone, local_date,
                              duration_sec, type, rpe, session_load)
        VALUES ('garmin', ?, 'Europe/Oslo', ?, ?, ?, ?, ?)
        """,
        (f"{d}T10:00:00Z", d, duration_min * 60, type_, rpe, session_load),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# compute_load
# ---------------------------------------------------------------------------


def test_compute_load_returns_none_when_no_workouts(conn: sqlite3.Connection) -> None:
    load = compute_load(conn, date.today())
    assert load.acr is None
    assert load.acr_zone == "insufficient"
    assert load.workouts_counted == 0


def test_compute_load_flags_insufficient_when_less_than_14_days(
    conn: sqlite3.Connection
) -> None:
    # 5 workouts spread over 5 days — not enough for chronic
    for i in range(5):
        _add_workout(conn, i, 30, rpe=6)
    load = compute_load(conn, date.today())
    assert load.acr is None
    assert load.acr_zone == "insufficient"
    assert load.workouts_counted == 5


def test_compute_load_computes_acr_when_enough_history(
    conn: sqlite3.Connection
) -> None:
    # 20 workouts spread over 20 days → nok til ACR
    for i in range(20):
        _add_workout(conn, i, 30, rpe=6)
    load = compute_load(conn, date.today())
    assert load.acr is not None
    # Flat volum → ACR ≈ 1.0 (sweet spot)
    assert 0.8 <= load.acr <= 1.3


def test_compute_load_detects_risk_zone(conn: sqlite3.Connection) -> None:
    # 14 dager historikk med lav acute, men spike siste 7
    for i in range(7, 21):  # dag 7-20 tilbake: baseline ~30 min × rpe 5
        _add_workout(conn, i, 30, rpe=5)
    # Siste 7 dager: DOBBELT volum
    for i in range(7):
        _add_workout(conn, i, 90, rpe=8)
    load = compute_load(conn, date.today())
    assert load.acr is not None
    assert load.acr > ACR_RISK
    assert load.acr_zone == "risk"


def test_compute_load_uses_session_load_when_available(
    conn: sqlite3.Connection
) -> None:
    # 14 workouts med eksplisitt session_load
    for i in range(14):
        _add_workout(conn, i, 30, session_load=150)
    load = compute_load(conn, date.today())
    assert load.workouts_without_rpe == 0
    assert load.acr is not None


def test_compute_load_falls_back_to_duration(conn: sqlite3.Connection) -> None:
    # 14 dager uten RPE — skal fortsatt kunne beregne, men flagget
    for i in range(14):
        _add_workout(conn, i, 30, rpe=None, session_load=None)
    load = compute_load(conn, date.today())
    assert load.workouts_without_rpe == 14
    assert load.acr is not None


# ---------------------------------------------------------------------------
# recovery_snapshot — regel-basert anbefaling
# ---------------------------------------------------------------------------


def _add_wellness(conn: sqlite3.Connection, illness: bool = False, **kwargs) -> None:
    today = date.today().isoformat()
    conn.execute(
        """
        INSERT INTO wellness_daily (local_date, sleep_quality, muscle_soreness,
                                    motivation, energy, illness_flag)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (today, kwargs.get("sleep", 7), kwargs.get("soreness", 3),
         kwargs.get("motivation", 7), kwargs.get("energy", 7),
         1 if illness else 0),
    )
    conn.commit()


def test_rest_recommendation_when_illness(conn: sqlite3.Connection) -> None:
    _add_wellness(conn, illness=True)
    snap = recovery_snapshot(conn)
    assert snap["recommendation"] == "rest"
    assert any("syk" in r.lower() for r in snap["rationale"])


def test_rest_recommendation_when_severe_injury(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO injuries (body_part, severity, started_at, status)
        VALUES ('lower_back', 3, ?, 'active')
        """, (date.today().isoformat(),)
    )
    conn.commit()
    snap = recovery_snapshot(conn)
    assert snap["recommendation"] == "rest"


def test_light_recommendation_when_acr_in_risk_zone(conn: sqlite3.Connection) -> None:
    # Byg ACR > 1.5
    for i in range(7, 21):
        _add_workout(conn, i, 30, rpe=4)
    for i in range(7):
        _add_workout(conn, i, 60, rpe=8)
    snap = recovery_snapshot(conn)
    assert snap["recommendation"] == "light"
    assert any("ACR" in r for r in snap["rationale"])


def test_normal_recommendation_baseline_case(conn: sqlite3.Connection) -> None:
    """Flat historikk + ingen skade/sykdom → normal."""
    for i in range(15):
        _add_workout(conn, i, 45, rpe=6)
    snap = recovery_snapshot(conn)
    # Flatt volum gir ACR i sweet spot → normal
    assert snap["recommendation"] == "normal"


def test_snapshot_includes_all_baseline_sections(conn: sqlite3.Connection) -> None:
    snap = recovery_snapshot(conn)
    assert "load" in snap
    assert "hrv" in snap
    assert "sleep_score" in snap
    assert "resting_hr" in snap
    assert "readiness" in snap
    assert "active_injuries" in snap
    assert "active_contexts" in snap
    assert "rationale" in snap


def test_snapshot_preserves_rationale_list(conn: sqlite3.Connection) -> None:
    _add_wellness(conn, illness=True)
    snap = recovery_snapshot(conn)
    assert isinstance(snap["rationale"], list)
    assert len(snap["rationale"]) >= 1
