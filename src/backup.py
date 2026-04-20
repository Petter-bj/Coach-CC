"""SQLite online backup + rotasjon.

Bruker SQLite sitt innebygde backup-API (`conn.backup`) — kjører trygt
parallelt med aktiv WAL. Ikke `VACUUM INTO` som kan blokkere skrivere.

Rotasjon:
* Daglige backups (YYYY-MM-DD.db): behold siste 14
* Ukentlige backups (YYYY-Www.db, søndag): behold siste 8
* Alt eldre slettes

Integritetscheck: etter backup kjøres `PRAGMA integrity_check`. Ved feil
beholdes forrige backup og en alert logges til DB.

Kjøres av launchd kl 03:00 daglig via:
    uv run python -m src.backup
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from src.db.connection import connect
from src.paths import BACKUPS_DIR, DB_PATH, ensure_runtime_dirs

DAILY_KEEP = 14
WEEKLY_KEEP = 8


def _integrity_ok(db_path: Path) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row is not None and row[0] == "ok"
    finally:
        conn.close()


def _write_backup(src: Path, dest: Path) -> None:
    """Bruk SQLite backup-API for online snapshot."""
    src_conn = sqlite3.connect(str(src))
    dest_conn = sqlite3.connect(str(dest))
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    finally:
        src_conn.close()
        dest_conn.close()


def _log_alert(level: str, message: str) -> None:
    """Log til alerts-tabellen så Claude kan se feilen."""
    with connect() as c:
        c.execute(
            "INSERT INTO alerts (source, level, message) VALUES ('backup', ?, ?)",
            (level, message),
        )


def _rotate(kind: str, keep: int) -> int:
    """Behold de `keep` nyeste .db-filene med prefix `kind-`. Returner antall slettet."""
    pattern = f"{kind}-*.db"
    files = sorted(BACKUPS_DIR.glob(pattern), reverse=True)
    deleted = 0
    for old in files[keep:]:
        old.unlink()
        deleted += 1
    return deleted


def run() -> int:
    """Hovedfunksjon. Returnerer 0 for suksess, 1 for feil."""
    ensure_runtime_dirs()

    if not DB_PATH.exists():
        print(f"[backup] Ingen DB å backupe på {DB_PATH}", file=sys.stderr)
        return 1

    today = date.today()
    daily_name = f"daily-{today.isoformat()}.db"
    daily_path = BACKUPS_DIR / daily_name

    # Skriv backup til temp-fil, sjekk integritet, deretter atomisk rename
    tmp_path = BACKUPS_DIR / f".{daily_name}.tmp"
    try:
        _write_backup(DB_PATH, tmp_path)
    except Exception as e:  # noqa: BLE001
        _log_alert("error", f"Backup feilet: {e}")
        print(f"[backup] FEIL: {e}", file=sys.stderr)
        if tmp_path.exists():
            tmp_path.unlink()
        return 1

    if not _integrity_ok(tmp_path):
        _log_alert("error", f"Backup integrity_check feilet for {daily_name}")
        print(f"[backup] integrity_check feilet", file=sys.stderr)
        tmp_path.unlink()
        return 1

    # OK — rename tmp → endelig fil
    tmp_path.rename(daily_path)
    size_mb = daily_path.stat().st_size / 1024 / 1024
    print(f"[backup] {daily_path.name} skrevet ({size_mb:.1f} MB)")

    # Ukentlig snapshot (søndager)
    if today.weekday() == 6:
        week_num = today.isocalendar().week
        weekly_name = f"weekly-{today.year}-W{week_num:02d}.db"
        weekly_path = BACKUPS_DIR / weekly_name
        shutil.copy2(daily_path, weekly_path)
        print(f"[backup] Kopiert til {weekly_path.name} (søndag)")

    # Rotasjon
    deleted_daily = _rotate("daily", DAILY_KEEP)
    deleted_weekly = _rotate("weekly", WEEKLY_KEEP)
    if deleted_daily or deleted_weekly:
        print(f"[backup] Slettet {deleted_daily} gamle daily, {deleted_weekly} gamle weekly")

    return 0


if __name__ == "__main__":
    sys.exit(run())
