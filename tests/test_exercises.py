"""Tester exercise-navn-lookup med aliases."""

from __future__ import annotations

from src.analysis.exercises import _normalize, list_muscles, lookup


def test_normalize_strips_case_and_punctuation() -> None:
    assert _normalize("Bench Press.") == "bench press"
    assert _normalize("  Benkpress  ") == "benkpress"
    assert _normalize("Lying (Leg) Curl,") == "lying leg curl"


def test_lookup_english_name() -> None:
    r = lookup("Bench Press")
    assert r["canonical"] == "bench_press"
    assert r["primary"] == "bryst"
    assert "triceps" in r["secondary"]
    assert r["unknown"] is False


def test_lookup_norwegian_alias() -> None:
    r = lookup("benkpress")
    assert r["canonical"] == "bench_press"
    assert r["primary"] == "bryst"


def test_lookup_alias_case_insensitive() -> None:
    assert lookup("KNEBØY")["primary"] == "quadriceps"
    assert lookup("knebøy")["primary"] == "quadriceps"


def test_lookup_unknown_exercise() -> None:
    r = lookup("Snatch Grip Behind The Neck Press")
    assert r["unknown"] is True
    assert r["primary"] is None


def test_lookup_deadlift_variants() -> None:
    assert lookup("Deadlift")["canonical"] == "deadlift"
    assert lookup("markløft")["canonical"] == "deadlift"
    assert lookup("RDL")["canonical"] == "romanian_deadlift"
    assert lookup("rumensk markløft")["canonical"] == "romanian_deadlift"


def test_list_muscles_contains_core_groups() -> None:
    muscles = list_muscles()
    for core in ("bryst", "rygg", "skuldre", "biceps", "triceps",
                 "quadriceps", "hamstrings", "glutes", "calves", "abs"):
        assert core in muscles
