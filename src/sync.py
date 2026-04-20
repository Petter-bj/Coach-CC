"""Sync entrypoint — kjøres av launchd hver time.

Ansvar:
1. Hold prosesslås (fcntl.flock) — hindrer at to parallelle launchd-runs
   overlapper.
2. Sjekk ledig diskplass før kjøring.
3. Kjør migreringer.
4. Iterer over registrerte kilder og la hver kjøre alle sine strømmer.
5. Skriv strukturert log-linje per resultat.

Brukes via:
    uv run python -m src.sync
    uv run python -m src.sync --source garmin --backfill-since 2026-04-13
"""

from __future__ import annotations

import argparse
import fcntl
import shutil
import sys
from contextlib import contextmanager
from typing import Iterator

from src.db.connection import connect
from src.db.migrations import migrate
from src.paths import APP_SUPPORT, SYNC_LOCK, ensure_runtime_dirs
from src.sources.base import Source
from src.sources.concept2 import Concept2Source
from src.sources.garmin import GarminSource
from src.sources.withings import WithingsSource

MIN_FREE_DISK_MB = 500


class LockBusy(Exception):
    pass


@contextmanager
def process_lock() -> Iterator[None]:
    """Tar eksklusiv fil-lås på SYNC_LOCK. Kaster LockBusy hvis opptatt."""
    ensure_runtime_dirs()
    fh = open(SYNC_LOCK, "w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise LockBusy("sync.lock held — another sync is running") from e
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def check_disk_space(min_mb: int = MIN_FREE_DISK_MB) -> None:
    """Aborter hvis mindre enn min_mb ledig på APP_SUPPORT-stien."""
    usage = shutil.disk_usage(APP_SUPPORT)
    free_mb = usage.free // (1024 * 1024)
    if free_mb < min_mb:
        raise RuntimeError(
            f"Kun {free_mb} MB ledig på {APP_SUPPORT} — minimum er {min_mb} MB"
        )


# Registry fylles på etter hvert som kilder implementeres.
SOURCES: list[type[Source]] = [GarminSource, WithingsSource, Concept2Source]


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trening health data sync")
    parser.add_argument("--source", help="Kjør kun denne kilden (f.eks. garmin)")
    parser.add_argument(
        "--skip-lock", action="store_true",
        help="Hopp over prosesslåsen (kun for debugging)",
    )
    args = parser.parse_args(argv)

    try:
        if args.skip_lock:
            _run_sync(args)
        else:
            with process_lock():
                _run_sync(args)
    except LockBusy as e:
        print(f"[sync] {e} — exiting quietly", file=sys.stderr)
        return 0  # ikke en feil; launchd trigger bare igjen neste time
    except Exception as e:  # noqa: BLE001
        print(f"[sync] FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


def _run_sync(args) -> None:
    check_disk_space()
    with connect() as conn:
        migrate(conn)

        sources = [cls() for cls in SOURCES]
        if args.source:
            sources = [s for s in sources if s.name == args.source]
            if not sources:
                raise RuntimeError(f"Ukjent kilde: {args.source}")

        for source in sources:
            results = source.sync(conn)
            for r in results:
                print(
                    f"[{r.source}/{r.stream}] {r.status:8} "
                    f"ins={r.rows_inserted} upd={r.rows_updated} "
                    f"{(r.error_message or '')}"
                )


if __name__ == "__main__":
    sys.exit(run())
