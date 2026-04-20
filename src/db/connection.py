"""SQLite connection utilities.

Korte transaksjoner, WAL-modus, busy_timeout. Hver sync-funksjon skal åpne
og lukke sin egen connection via `connect()`-context manager.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.paths import DB_PATH, ensure_runtime_dirs


def configure(conn: sqlite3.Connection) -> None:
    """Sett alle PRAGMAs vi baserer oss på."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    # Navngitte parametere + gi Row factory slik at kolonne-tilgang er lesbar
    conn.row_factory = sqlite3.Row


@contextmanager
def connect(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Åpne en kortlevd SQLite-connection med riktige PRAGMAs.

    Args:
        db_path: Overstyrer DB-sti (brukes i tester for :memory:-DB).

    Yields:
        sqlite3.Connection — committer automatisk ved suksess, rullerer
        tilbake ved exception.
    """
    if db_path is None:
        ensure_runtime_dirs()
        db_path = DB_PATH
    conn = sqlite3.connect(str(db_path))
    try:
        configure(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
