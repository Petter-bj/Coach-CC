"""Tests for schema migrations.

Kjører migreringene på :memory:-DB og verifiserer at:
* Alle forventede tabeller eksisterer
* WAL + foreign_keys-PRAGMAs funker
* Re-kjøring er idempotent (andre kall legger til 0 versjoner)
* Etter data-insertion gir en second migrate() ingen datatap
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.db.connection import configure
from src.db.migrations import migrate


EXPECTED_TABLES = {
    "schema_migrations",
    "workouts",
    "workout_samples",
    "garmin_activity_details",
    "concept2_session_details",
    "concept2_intervals",
    "strength_sessions_pending",
    "strength_sessions",
    "strength_sets",
    "garmin_daily",
    "garmin_sleep",
    "garmin_hrv",
    "withings_weight",
    "yazio_daily",
    "yazio_meals",
    "yazio_consumed_items",
    "wellness_daily",
    "goals",
    "training_blocks",
    "user_baselines",
    "intake_log",
    "injuries",
    "planned_sessions",
    "context_log",
    "source_stream_state",
    "sync_runs",
    "alerts",
    "user_preferences",
    "exercise_preferences",
}


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    configure(c)
    try:
        yield c
    finally:
        c.close()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def test_migrate_creates_all_tables(conn: sqlite3.Connection) -> None:
    applied = migrate(conn)
    assert applied == [1, 2, 3]
    assert _table_names(conn) == EXPECTED_TABLES


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    first = migrate(conn)
    second = migrate(conn)
    assert first == [1, 2, 3]
    assert second == []


def test_migrate_preserves_data(conn: sqlite3.Connection) -> None:
    """Insert en rad, kjør migrate() igjen → raden skal fortsatt være der."""
    migrate(conn)
    conn.execute(
        """
        INSERT INTO workouts (source, started_at_utc, local_date, type)
        VALUES ('garmin', '2026-04-19T09:16:58Z', '2026-04-19', 'running')
        """
    )
    conn.commit()

    # Re-run migrate
    migrate(conn)

    rows = conn.execute("SELECT source, type FROM workouts").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "garmin"
    assert rows[0][1] == "running"


def test_foreign_keys_enforced(conn: sqlite3.Connection) -> None:
    migrate(conn)
    # Forsøk å sette inn workout_sample uten workout → skal feile
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO workout_samples (workout_id, t_offset_sec, hr) VALUES (999, 0, 150)"
        )
        conn.commit()


def test_cascade_delete_workout_removes_samples(conn: sqlite3.Connection) -> None:
    migrate(conn)
    cur = conn.execute(
        """
        INSERT INTO workouts (source, started_at_utc, local_date, type)
        VALUES ('garmin', '2026-04-19T09:16:58Z', '2026-04-19', 'running')
        """
    )
    wid = cur.lastrowid
    conn.execute(
        "INSERT INTO workout_samples (workout_id, t_offset_sec, hr) VALUES (?, 0, 150)",
        (wid,),
    )
    assert conn.execute("SELECT COUNT(*) FROM workout_samples").fetchone()[0] == 1

    conn.execute("DELETE FROM workouts WHERE id = ?", (wid,))
    assert conn.execute("SELECT COUNT(*) FROM workout_samples").fetchone()[0] == 0


def test_unique_constraint_on_workouts_source_external_id(conn: sqlite3.Connection) -> None:
    migrate(conn)
    conn.execute(
        """
        INSERT INTO workouts (source, external_id, started_at_utc, local_date, type)
        VALUES ('garmin', '12345', '2026-04-19T09:00:00Z', '2026-04-19', 'running')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO workouts (source, external_id, started_at_utc, local_date, type)
            VALUES ('garmin', '12345', '2026-04-19T10:00:00Z', '2026-04-19', 'running')
            """
        )


def test_wal_mode_active(tmp_path: Path) -> None:
    """Real file, not :memory: — WAL requires file-backed DB."""
    from src.db.connection import connect
    db = tmp_path / "test.db"
    with connect(db) as c:
        migrate(c)
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


def test_schema_migrations_records_version(conn: sqlite3.Connection) -> None:
    migrate(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert [r[0] for r in rows] == [1, 2, 3]


def test_check_constraint_rpe_range(conn: sqlite3.Connection) -> None:
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO workouts (source, started_at_utc, local_date, type, rpe)
            VALUES ('garmin', '2026-04-19T09:00:00Z', '2026-04-19', 'running', 15)
            """
        )


def test_check_constraint_yazio_meal_enum(conn: sqlite3.Connection) -> None:
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO yazio_meals (local_date, meal, kcal)
            VALUES ('2026-04-20', 'supper', 500)
            """
        )
