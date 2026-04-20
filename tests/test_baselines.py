"""Tester baseline-beregning mot :memory:-DB."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from src.analysis.baselines import (
    MIN_SAMPLES,
    WINDOWS,
    compute_baseline,
    refresh_baselines,
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


# ---------------------------------------------------------------------------
# compute_baseline — pure
# ---------------------------------------------------------------------------


def test_compute_baseline_returns_median() -> None:
    b = compute_baseline([70, 72, 74, 76, 78])
    assert b["median"] == 74
    assert b["value"] == 74


def test_compute_baseline_mad_robust_to_outlier() -> None:
    # [70, 72, 74, 76, 78, 200] — outlier 200 burde ikke dra median dramatisk
    b = compute_baseline([70, 72, 74, 76, 78, 200])
    assert 74 <= b["median"] <= 77  # median fortsatt rimelig
    assert b["mad"] < 10  # MAD lav siden fleste verdier er tett


def test_compute_baseline_returns_none_for_too_few() -> None:
    assert compute_baseline([]) is None
    assert compute_baseline([75]) is None


def test_compute_baseline_n_equals_sample_size() -> None:
    b = compute_baseline([70, 75, 80])
    assert b["sample_size"] == 3


# ---------------------------------------------------------------------------
# refresh_baselines — integrasjon mot live schema
# ---------------------------------------------------------------------------


def _seed_rhr(conn: sqlite3.Connection, days: int, values: list[int]) -> None:
    """Seed RHR-data i garmin_daily for siste N dager."""
    today = date.today()
    for i, v in enumerate(values):
        d = (today - timedelta(days=days - 1 - i)).isoformat()
        conn.execute(
            "INSERT INTO garmin_daily (local_date, resting_hr) VALUES (?, ?)",
            (d, v),
        )
    conn.commit()


def test_refresh_flags_insufficient_data(conn: sqlite3.Connection) -> None:
    # Ingen data → alle baselines skal være flagget som insufficient
    refresh_baselines(conn)
    rows = conn.execute(
        "SELECT insufficient_data FROM user_baselines"
    ).fetchall()
    assert all(r[0] == 1 for r in rows)


def test_refresh_with_enough_data_computes_value(conn: sqlite3.Connection) -> None:
    # Seed 30 dager med varierende RHR
    values = [45, 46, 47, 48, 45, 46, 47, 48, 49, 50] * 3  # 30 dager
    _seed_rhr(conn, 30, values)

    refresh_baselines(conn)

    # 30d baseline for resting_hr skal ha verdi
    row = conn.execute(
        "SELECT value, median, mad, sample_size, insufficient_data "
        "FROM user_baselines WHERE metric='resting_hr' AND window_days=30"
    ).fetchone()
    assert row is not None
    assert row["insufficient_data"] == 0
    assert row["value"] is not None
    assert 45 <= row["value"] <= 50
    assert row["sample_size"] == 30


def test_refresh_respects_window_boundaries(conn: sqlite3.Connection) -> None:
    """90d-baseline skal ha flere datapunkter enn 7d."""
    # 50 dager (overlapper både 30d og 90d; 7d får bare siste 7)
    _seed_rhr(conn, 50, [45] * 50)
    refresh_baselines(conn)

    seven = conn.execute(
        "SELECT sample_size FROM user_baselines "
        "WHERE metric='resting_hr' AND window_days=7"
    ).fetchone()["sample_size"]
    thirty = conn.execute(
        "SELECT sample_size FROM user_baselines "
        "WHERE metric='resting_hr' AND window_days=30"
    ).fetchone()["sample_size"]
    ninety = conn.execute(
        "SELECT sample_size FROM user_baselines "
        "WHERE metric='resting_hr' AND window_days=90"
    ).fetchone()["sample_size"]

    assert seven == 7
    assert thirty == 30
    assert ninety == 50


def test_refresh_is_idempotent(conn: sqlite3.Connection) -> None:
    _seed_rhr(conn, 30, [47] * 30)
    n1 = refresh_baselines(conn)
    n2 = refresh_baselines(conn)
    assert n1 == n2  # samme antall rader

    # Antall rader i tabellen skal være konstant (upserted, ikke duplisert)
    count = conn.execute("SELECT COUNT(*) FROM user_baselines").fetchone()[0]
    assert count == len(WINDOWS) * 6  # 3 vinduer × 6 metrikker


def test_min_samples_thresholds() -> None:
    assert MIN_SAMPLES[7] == 4
    assert MIN_SAMPLES[30] == 14
    assert MIN_SAMPLES[90] == 30
