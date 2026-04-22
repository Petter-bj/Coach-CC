"""Tester for blokk-fase-lesing og phase_guidance-modulering."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from src.coaching.philosophy import phase_guidance
from src.coaching.preferences import current_phase, get_active_block
from src.db.connection import configure
from src.db.migrations import migrate


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    configure(c)
    migrate(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# get_active_block / current_phase
# ---------------------------------------------------------------------------


def _insert_block(
    conn: sqlite3.Connection, *, phase: str, start: str, end: str, name: str = "Test",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO training_blocks (name, phase, start_date, end_date)
        VALUES (?, ?, ?, ?)
        """,
        (name, phase, start, end),
    )
    conn.commit()
    return cur.lastrowid


def test_no_active_block_returns_none(conn) -> None:
    assert get_active_block(conn) is None
    assert current_phase(conn) is None


def test_active_block_covers_today(conn) -> None:
    today = date.today()
    _insert_block(
        conn, phase="base",
        start=(today - timedelta(days=5)).isoformat(),
        end=(today + timedelta(days=20)).isoformat(),
    )
    b = get_active_block(conn)
    assert b is not None
    assert b.phase == "base"
    assert current_phase(conn) == "base"


def test_past_block_not_returned(conn) -> None:
    today = date.today()
    _insert_block(
        conn, phase="base",
        start=(today - timedelta(days=90)).isoformat(),
        end=(today - timedelta(days=60)).isoformat(),
    )
    assert get_active_block(conn) is None


def test_future_block_not_returned(conn) -> None:
    today = date.today()
    _insert_block(
        conn, phase="build",
        start=(today + timedelta(days=10)).isoformat(),
        end=(today + timedelta(days=40)).isoformat(),
    )
    assert get_active_block(conn) is None


def test_most_recent_active_block_wins(conn) -> None:
    today = date.today()
    _insert_block(
        conn, name="old base",
        phase="base",
        start=(today - timedelta(days=60)).isoformat(),
        end=(today + timedelta(days=10)).isoformat(),
    )
    _insert_block(
        conn, name="new build",
        phase="build",
        start=(today - timedelta(days=3)).isoformat(),
        end=(today + timedelta(days=25)).isoformat(),
    )
    b = get_active_block(conn)
    assert b.phase == "build"
    assert b.name == "new build"


# ---------------------------------------------------------------------------
# phase_guidance
# ---------------------------------------------------------------------------


def test_base_phase_bakken_correct_allows_run_z3() -> None:
    """Bakken-korrekt: sub-threshold (Z3) løping fra dag 1 i base.

    Z5-løping venter fortsatt til build (tissue stress). Injury-gating
    håndteres separat via running_ruling() som er severity-aware.
    """
    g = phase_guidance("base")
    assert g.phase == "base"
    assert g.should_recommend_run_z3 is True  # Z3 OK i base per Bakken
    assert g.should_recommend_run_hard_intervals is False  # Z5 venter til build
    assert g.run_intensity_cap_zone == "Z3"
    assert g.allow_long_runs_over_16km is False
    assert g.volume_ramp_pct_per_week_max == 0.10


def test_base_phase_default_conservative_cross_training() -> None:
    """Default er konservativ — Bakken-pyramidal (passer høy-volum base).

    Men notes skal dokumentere at dette er volum-tier-avhengig:
    ved lavt volum (< 55 km/uke) skal caller oppgradere hard_intervals
    til True (polarisert hybrid).
    """
    g = phase_guidance("base")
    # Default: Z3 YES, hard NO, cap Z3
    assert g.should_recommend_cross_training_z3 is True
    assert g.should_recommend_cross_training_hard_intervals is False
    assert g.cross_training_intensity_cap_zone == "Z3"
    # Notes skal nevne volum-tier som overstyrings-trigger
    joined = " ".join(g.notes).lower()
    assert "sub-threshold" in joined or "sub threshold" in joined
    assert "polarisert" in joined or "polarized" in joined
    assert "volum-tier" in joined or "volum tier" in joined


def test_base_phase_allows_neuromuscular_and_progression() -> None:
    """Base er ikke bare Z2 monotoni — strides og progressive runs er OK."""
    g = phase_guidance("base")
    assert g.allow_neuromuscular_work is True
    assert g.allow_progression_runs is True
    joined = " ".join(g.notes).lower()
    assert "strides" in joined
    assert "progressive" in joined


def test_build_phase_allows_run_z3_and_hard() -> None:
    g = phase_guidance("build")
    assert g.should_recommend_run_z3 is True
    assert g.should_recommend_run_hard_intervals is True
    assert g.run_intensity_cap_zone == "Z5"
    assert g.allow_long_runs_over_16km is True


def test_peak_phase_minimal_strength_no_ramp() -> None:
    g = phase_guidance("peak")
    assert g.strength_modulation == "minimal"
    assert g.volume_ramp_pct_per_week_max == 0.0


def test_taper_phase_ramps_down() -> None:
    g = phase_guidance("taper")
    assert g.volume_ramp_pct_per_week_max < 0
    assert g.strength_modulation == "minimal"
    assert g.allow_long_runs_over_16km is False


def test_recovery_phase_caps_at_z2_no_hard() -> None:
    g = phase_guidance("recovery")
    assert g.run_intensity_cap_zone == "Z2"
    assert g.should_recommend_run_hard_intervals is False
    assert g.should_recommend_run_z3 is False
    assert g.allow_neuromuscular_work is False
    # Recovery kapper også cross-training — ulikt base
    assert g.should_recommend_cross_training_hard_intervals is False


def test_taper_allows_strides_but_not_progression() -> None:
    """Taper beholder neural sharpness via strides, men ingen nye stimuli."""
    g = phase_guidance("taper")
    assert g.allow_neuromuscular_work is True
    assert g.allow_progression_runs is False


def test_base_allows_both_run_z3_and_cross_training_z3() -> None:
    """Bakken: Z3 er definerende stimulus i base — både løp og cross-training.

    Løp-Z3 gates bare ved aktiv severity 2+ skade via running_ruling(),
    ikke ved phase boundary.
    """
    g = phase_guidance("base")
    assert g.should_recommend_run_z3 is True
    assert g.should_recommend_cross_training_z3 is True


def test_unknown_phase_defaults_to_base_conservative() -> None:
    g = phase_guidance(None)
    assert g.phase == "base"
    g2 = phase_guidance("something_unexpected")
    assert g2.phase == "base"
