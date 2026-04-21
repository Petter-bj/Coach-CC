"""Tester for coaching-filosofi-regler (ren funksjoner, ingen DB)."""

from __future__ import annotations

import pytest

from src.coaching.philosophy import (
    next_set_for_exercise,
    readiness_advice,
    running_ruling,
    strength_running_conflict,
)


# ---------------------------------------------------------------------------
# Double progression
# ---------------------------------------------------------------------------


def test_progression_reps_below_max_pushes_reps() -> None:
    rec = next_set_for_exercise(
        last_top_set={"reps": 8, "weight_kg": 80},
        rep_min=6, rep_max=10, increment_kg=2.5,
    )
    assert rec.action == "add_reps"
    assert rec.target_weight_kg == 80
    assert rec.target_reps == 9


def test_progression_reps_at_max_adds_weight() -> None:
    rec = next_set_for_exercise(
        last_top_set={"reps": 10, "weight_kg": 80},
        rep_min=6, rep_max=10, increment_kg=2.5,
    )
    assert rec.action == "add_weight"
    assert rec.target_weight_kg == 82.5
    assert rec.target_reps == 6


def test_progression_reps_above_max_still_adds_weight() -> None:
    # Hvis brukeren har bommet på rep_max (tatt 11 reps), øk vekten uansett
    rec = next_set_for_exercise(
        last_top_set={"reps": 11, "weight_kg": 80},
        rep_min=6, rep_max=10, increment_kg=2.5,
    )
    assert rec.action == "add_weight"
    assert rec.target_weight_kg == 82.5


def test_progression_bodyweight_pushes_reps_uncapped() -> None:
    rec = next_set_for_exercise(
        last_top_set={"reps": 15, "weight_kg": None},
        rep_min=6, rep_max=10, increment_kg=2.5,
    )
    assert rec.action == "add_reps"
    assert rec.target_weight_kg is None
    assert rec.target_reps == 16  # Ingen tak


def test_progression_no_history_returns_no_data() -> None:
    rec = next_set_for_exercise(
        last_top_set=None,
        rep_min=6, rep_max=10, increment_kg=2.5,
    )
    assert rec.action == "no_data"
    assert rec.target_weight_kg is None
    assert "baseline" in rec.reasoning.lower()


def test_progression_increment_applied_correctly_with_half_kg() -> None:
    rec = next_set_for_exercise(
        last_top_set={"reps": 10, "weight_kg": 77.5},
        rep_min=6, rep_max=10, increment_kg=1.0,
    )
    assert rec.target_weight_kg == 78.5


def test_progression_reasoning_mentions_previous_weight() -> None:
    rec = next_set_for_exercise(
        last_top_set={"reps": 8, "weight_kg": 80},
        rep_min=6, rep_max=10, increment_kg=2.5,
    )
    assert "80" in rec.reasoning
    assert "9" in rec.reasoning


# ---------------------------------------------------------------------------
# Injury hard-stops
# ---------------------------------------------------------------------------


def test_running_ruling_no_injuries_allows() -> None:
    r = running_ruling([])
    assert r.allow is True


def test_running_ruling_shin_splints_blocks() -> None:
    r = running_ruling([{
        "body_part": "Shin",
        "severity": 2,
        "started_at": "2026-04-15",
    }])
    assert r.allow is False
    assert "shin splints" in r.reason.lower()
    assert "cross-train" in r.alternative.lower()


def test_running_ruling_norwegian_keyword_matches() -> None:
    r = running_ruling([{
        "body_part": "Legghinne",
        "severity": 1,
        "started_at": "2026-04-18",
    }])
    assert r.allow is False


def test_running_ruling_keyword_in_notes_also_matches() -> None:
    r = running_ruling([{
        "body_part": "Nedre legg",
        "severity": 2,
        "notes": "Mistenker legghinnebetennelse",
    }])
    assert r.allow is False


def test_running_ruling_unrelated_injury_allows() -> None:
    r = running_ruling([{
        "body_part": "Skulder",
        "severity": 1,
        "started_at": "2026-04-10",
    }])
    assert r.allow is True


# ---------------------------------------------------------------------------
# Readiness-mapping
# ---------------------------------------------------------------------------


def test_readiness_doesnt_gate_strength_unless_extreme() -> None:
    # Readiness 30 skal ikke utløse advarsel for styrke
    assert readiness_advice("strength", 30) is None
    assert readiness_advice("strength", 50) is None
    # Men under 25 er "kanskje syk"-signal
    warn = readiness_advice("strength", 20)
    assert warn is not None
    assert warn["severity"] == "warning"


def test_readiness_gates_cardio_at_40() -> None:
    warn = readiness_advice("cardio", 35)
    assert warn["severity"] == "recommend_easy"
    warn2 = readiness_advice("cardio", 55)
    assert warn2["severity"] == "caution"
    assert readiness_advice("cardio", 75) is None


def test_readiness_none_returns_none() -> None:
    assert readiness_advice("strength", None) is None
    assert readiness_advice("cardio", None) is None


# ---------------------------------------------------------------------------
# Strength vs running conflict
# ---------------------------------------------------------------------------


def test_race_week_cardio_priority_reduces_strength() -> None:
    r = strength_running_conflict("cardio", is_race_week=True)
    assert r is not None
    assert r["action"] == "reduce"
    assert "60%" in r["guidance"]


def test_race_week_strength_priority_keeps_strength() -> None:
    r = strength_running_conflict("strength", is_race_week=True)
    assert r is None


def test_recent_legs_flag_before_running_under_cardio_priority() -> None:
    r = strength_running_conflict(
        "cardio", same_muscle_group_hours_ago=24,
    )
    assert r is not None
    assert r["action"] == "flag"


def test_no_conflict_when_fresh() -> None:
    r = strength_running_conflict("cardio")
    assert r is None
