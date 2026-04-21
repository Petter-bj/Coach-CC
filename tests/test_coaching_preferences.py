"""Tester for preferences-laget (DB-backet KV + per-øvelse-overrides)."""

from __future__ import annotations

import sqlite3

import pytest

from src.coaching.preferences import (
    get_exercise_prefs,
    get_pref,
    list_exercise_prefs,
    list_prefs,
    set_exercise_prefs,
    set_pref,
    training_priority,
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


# ---------------------------------------------------------------------------
# user_preferences (globale KV)
# ---------------------------------------------------------------------------


def test_seed_defaults_exist_after_migration(conn) -> None:
    assert get_pref(conn, "training_priority") == "cardio"
    assert get_pref(conn, "strength_rep_min_default") == "6"
    assert get_pref(conn, "strength_rep_max_default") == "10"
    assert get_pref(conn, "strength_increment_kg_default") == "2.5"


def test_set_pref_overwrites(conn) -> None:
    set_pref(conn, "training_priority", "strength")
    assert get_pref(conn, "training_priority") == "strength"


def test_set_pref_new_key(conn) -> None:
    set_pref(conn, "custom_key", "custom_value")
    assert get_pref(conn, "custom_key") == "custom_value"


def test_list_prefs_includes_seeds(conn) -> None:
    prefs = list_prefs(conn)
    assert "training_priority" in prefs
    assert prefs["training_priority"] == "cardio"


def test_training_priority_helper(conn) -> None:
    assert training_priority(conn) == "cardio"
    set_pref(conn, "training_priority", "balanced")
    assert training_priority(conn) == "balanced"


# ---------------------------------------------------------------------------
# exercise_preferences — default fallback
# ---------------------------------------------------------------------------


def test_get_exercise_prefs_fallback_to_global_defaults(conn) -> None:
    p = get_exercise_prefs(conn, "Bench Press")
    assert p.is_default is True
    assert p.rep_min == 6
    assert p.rep_max == 10
    assert p.increment_kg == 2.5
    assert p.exercise_type is None


def test_get_exercise_prefs_fallback_reflects_updated_globals(conn) -> None:
    set_pref(conn, "strength_rep_min_default", "5")
    set_pref(conn, "strength_increment_kg_default", "1.25")
    p = get_exercise_prefs(conn, "Lateral Raise")
    assert p.rep_min == 5
    assert p.increment_kg == 1.25


# ---------------------------------------------------------------------------
# exercise_preferences — override
# ---------------------------------------------------------------------------


def test_set_exercise_prefs_creates_override(conn) -> None:
    set_exercise_prefs(
        conn, "Bench Press",
        rep_min=5, rep_max=8, increment_kg=2.5, exercise_type="compound",
    )
    p = get_exercise_prefs(conn, "Bench Press")
    assert p.is_default is False
    assert p.rep_min == 5
    assert p.rep_max == 8
    assert p.exercise_type == "compound"


def test_set_exercise_prefs_case_insensitive(conn) -> None:
    set_exercise_prefs(conn, "Bench Press", rep_min=5, rep_max=8)
    p1 = get_exercise_prefs(conn, "bench press")
    p2 = get_exercise_prefs(conn, "BENCH PRESS")
    assert p1.rep_min == p2.rep_min == 5


def test_set_exercise_prefs_partial_update_preserves_others(conn) -> None:
    set_exercise_prefs(
        conn, "Squat",
        rep_min=4, rep_max=6, increment_kg=5.0, exercise_type="compound",
    )
    # Bare oppdater increment
    set_exercise_prefs(conn, "Squat", increment_kg=2.5)
    p = get_exercise_prefs(conn, "Squat")
    assert p.rep_min == 4  # Uendret
    assert p.rep_max == 6  # Uendret
    assert p.increment_kg == 2.5
    assert p.exercise_type == "compound"  # Uendret


def test_null_field_in_override_falls_back_to_default(conn) -> None:
    """Hvis en override har rep_min=NULL, skal get_exercise_prefs bruke global default."""
    conn.execute(
        """
        INSERT INTO exercise_preferences
            (exercise_lower, display_name, rep_min, rep_max, increment_kg, exercise_type)
        VALUES ('plank', 'Plank', NULL, NULL, NULL, 'isolation')
        """
    )
    conn.commit()

    p = get_exercise_prefs(conn, "Plank")
    assert p.is_default is False  # Raden finnes
    assert p.rep_min == 6  # Men NULL → global default
    assert p.rep_max == 10
    assert p.exercise_type == "isolation"  # Satt eksplisitt


def test_list_exercise_prefs_returns_only_overrides(conn) -> None:
    assert list_exercise_prefs(conn) == []
    set_exercise_prefs(conn, "Bench Press", rep_min=5, rep_max=8)
    set_exercise_prefs(conn, "Squat", rep_min=4, rep_max=6)
    rows = list_exercise_prefs(conn)
    assert len(rows) == 2
    assert {r.display_name for r in rows} == {"Bench Press", "Squat"}


def test_invalid_exercise_type_rejected_at_db_level(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO exercise_preferences
                (exercise_lower, display_name, exercise_type)
            VALUES ('test', 'Test', 'invalid_type')
            """
        )
        conn.commit()
