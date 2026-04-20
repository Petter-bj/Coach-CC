"""Tester dedupe-reglene mellom Garmin og Concept2."""

from __future__ import annotations

import sqlite3

import pytest

from src.db.connection import configure
from src.db.migrations import migrate
from src.reconcile import _match_score, dedupe_workouts


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    configure(c)
    migrate(c)
    try:
        yield c
    finally:
        c.close()


def _insert_workout(
    conn: sqlite3.Connection,
    source: str,
    type_: str,
    started_utc: str,
    duration_sec: float,
    external_id: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO workouts (external_id, source, started_at_utc, timezone,
                              local_date, duration_sec, type)
        VALUES (?, ?, ?, 'Europe/Oslo', ?, ?, ?)
        """,
        (
            external_id or f"{source}-{type_}-{started_utc}",
            source,
            started_utc,
            started_utc[:10],
            duration_sec,
            type_,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# _match_score — pure heuristikk
# ---------------------------------------------------------------------------


def test_perfect_match_scores_high() -> None:
    g = {
        "type": "indoor_rowing",
        "started_at_utc": "2026-04-18T09:53:00Z",
        "duration_sec": 1500,
    }
    c = {
        "type": "skierg",
        "started_at_utc": "2026-04-18T09:53:00Z",
        "duration_sec": 1500,
    }
    score = _match_score(g, c)
    assert score is not None
    assert score > 0.95


def test_type_mismatch_returns_none() -> None:
    g = {"type": "running", "started_at_utc": "2026-04-18T09:53:00Z", "duration_sec": 1500}
    c = {"type": "skierg", "started_at_utc": "2026-04-18T09:53:00Z", "duration_sec": 1500}
    assert _match_score(g, c) is None


def test_no_overlap_returns_none() -> None:
    """Garmin 09:00-09:25, Concept2 11:00-11:25 — ingen overlapp."""
    g = {"type": "indoor_rowing", "started_at_utc": "2026-04-18T09:00:00Z", "duration_sec": 1500}
    c = {"type": "skierg", "started_at_utc": "2026-04-18T11:00:00Z", "duration_sec": 1500}
    assert _match_score(g, c) is None


def test_duration_within_tolerance() -> None:
    """Real-world: Garmin 56min vs Concept2 25min (0.446 ratio) skal matche."""
    g = {
        "type": "indoor_rowing",
        "started_at_utc": "2026-04-18T09:53:00Z",
        "duration_sec": 3365,
    }
    c = {
        "type": "skierg",
        "started_at_utc": "2026-04-18T09:53:00Z",
        "duration_sec": 1500,
    }
    assert _match_score(g, c) is not None


def test_duration_outside_tolerance_returns_none() -> None:
    """Garmin 3000s vs Concept2 300s (0.1 ratio) — for ulik."""
    g = {"type": "indoor_rowing", "started_at_utc": "2026-04-18T09:53:00Z", "duration_sec": 3000}
    c = {"type": "skierg", "started_at_utc": "2026-04-18T09:53:00Z", "duration_sec": 300}
    assert _match_score(g, c) is None


# ---------------------------------------------------------------------------
# dedupe_workouts — integrasjon
# ---------------------------------------------------------------------------


def test_dedupe_marks_garmin_as_superseded(conn: sqlite3.Connection) -> None:
    c_id = _insert_workout(conn, "concept2", "skierg", "2026-04-18T09:53:00Z", 1500.0)
    g_id = _insert_workout(conn, "garmin", "indoor_rowing", "2026-04-18T09:30:00Z", 3365.0)

    marked = dedupe_workouts(conn)
    assert marked == 1

    row = conn.execute("SELECT superseded_by FROM workouts WHERE id = ?", (g_id,)).fetchone()
    assert row["superseded_by"] == c_id

    # Concept2-raden skal IKKE være superseded
    row = conn.execute("SELECT superseded_by FROM workouts WHERE id = ?", (c_id,)).fetchone()
    assert row["superseded_by"] is None


def test_dedupe_preserves_both_rows(conn: sqlite3.Connection) -> None:
    _insert_workout(conn, "concept2", "skierg", "2026-04-18T09:53:00Z", 1500.0)
    _insert_workout(conn, "garmin", "indoor_rowing", "2026-04-18T09:30:00Z", 3365.0)

    dedupe_workouts(conn)

    count = conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]
    assert count == 2  # begge er fortsatt der


def test_dedupe_is_idempotent(conn: sqlite3.Connection) -> None:
    _insert_workout(conn, "concept2", "skierg", "2026-04-18T09:53:00Z", 1500.0)
    _insert_workout(conn, "garmin", "indoor_rowing", "2026-04-18T09:30:00Z", 3365.0)

    first = dedupe_workouts(conn)
    second = dedupe_workouts(conn)
    assert first == 1
    assert second == 0  # ikke flere å markere


def test_dedupe_ignores_non_matching_types(conn: sqlite3.Connection) -> None:
    _insert_workout(conn, "concept2", "skierg", "2026-04-18T09:53:00Z", 1500.0)
    _insert_workout(conn, "garmin", "running", "2026-04-18T09:30:00Z", 3365.0)

    marked = dedupe_workouts(conn)
    assert marked == 0


def test_dedupe_picks_best_match_when_multiple(conn: sqlite3.Connection) -> None:
    """To Concept2-økter innenfor vinduet — velg den med best score."""
    far_c = _insert_workout(conn, "concept2", "skierg", "2026-04-18T09:58:00Z", 1500.0)
    near_c = _insert_workout(conn, "concept2", "skierg", "2026-04-18T09:35:00Z", 3200.0)
    g_id = _insert_workout(conn, "garmin", "indoor_rowing", "2026-04-18T09:30:00Z", 3365.0)

    dedupe_workouts(conn)

    row = conn.execute("SELECT superseded_by FROM workouts WHERE id = ?", (g_id,)).fetchone()
    # near_c er nærmere både i tid og duration
    assert row["superseded_by"] == near_c


def test_dedupe_skips_already_superseded(conn: sqlite3.Connection) -> None:
    c_id = _insert_workout(conn, "concept2", "skierg", "2026-04-18T09:53:00Z", 1500.0)
    g_id = _insert_workout(conn, "garmin", "indoor_rowing", "2026-04-18T09:30:00Z", 3365.0)
    conn.execute(
        "UPDATE workouts SET superseded_by = ? WHERE id = ?", (c_id, g_id)
    )
    conn.commit()

    marked = dedupe_workouts(conn)
    assert marked == 0  # allerede gjort
