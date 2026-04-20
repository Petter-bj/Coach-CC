"""Migrations-runner.

Migreringer ligger som `NNN_<name>.sql` i src/db/migrations/ og kjøres
i numerisk rekkefølge. Versjoner registreres i `schema_migrations`.

Kjør via:
    from src.db.migrations import migrate
    from src.db.connection import connect
    with connect() as conn:
        migrate(conn)
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterator

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
FILENAME_RE = re.compile(r"^(\d{3})_(.+)\.sql$")


def _discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> Iterator[tuple[int, str, Path]]:
    """Yield (version, name, path) for hver .sql-fil, sortert etter version."""
    files = []
    for path in migrations_dir.iterdir():
        m = FILENAME_RE.match(path.name)
        if not m:
            continue
        version = int(m.group(1))
        name = m.group(2)
        files.append((version, name, path))
    files.sort(key=lambda t: t[0])
    yield from files


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Returner sett av allerede anvendte versjoner."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not row:
        return set()
    cursor = conn.execute("SELECT version FROM schema_migrations")
    return {r[0] for r in cursor.fetchall()}


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
    """Kjør alle ukjørte migreringer i transaksjonene de tilhører.

    Returns:
        Liste med versjoner som ble anvendt (tomt hvis ingen nye).
    """
    applied = _applied_versions(conn)
    newly_applied: list[int] = []

    for version, name, path in _discover_migrations(migrations_dir):
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        # executescript committer automatisk i mange SQLite-versjoner;
        # kjør som én batch og registrer versjonen etterpå.
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            (version,),
        )
        newly_applied.append(version)

    return newly_applied
