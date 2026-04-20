"""Abstract base class for data sources.

Hver kilde (Garmin, Withings, Concept2, Yazio) har én eller flere strømmer
(daily, hrv, sleep, activities, weight, ...). Strømmer synkes uavhengig —
hvis Garmin HRV feiler, skal Garmin daily fortsatt gå gjennom.

Konvensjoner:
* `Source.streams` lister alle strømmene denne kilden eier.
* `fetch_stream(conn, stream)` henter og skriver data for én strøm.
* Retry/backoff og state-tracking håndteres her; konkrete kilder bare
  implementerer fetch-logikken og kaster `RetryableError` / `FatalError`
  ved behov.
* `sync(conn)` iterer over alle strømmer og returnerer resultat per strøm.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Feiltyper
# ---------------------------------------------------------------------------


class SourceError(Exception):
    """Base for alle source-feil."""


class RetryableError(SourceError):
    """Midlertidig feil — nettverk, 5xx, timeout. Backoff og prøv igjen."""


class FatalError(SourceError):
    """Permanent feil — auth brutt, ugyldig credentials, schema-mismatch.

    Backoff gjelder fortsatt (forhindrer spam), men alert opprettes raskere.
    """


# ---------------------------------------------------------------------------
# Resultat-typer
# ---------------------------------------------------------------------------


@dataclass
class StreamResult:
    source: str
    stream: str
    status: str  # running | success | error | skipped
    rows_inserted: int = 0
    rows_updated: int = 0
    error_message: str | None = None
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------------------
# Retry-policy
# ---------------------------------------------------------------------------

MAX_RETRY_HOURS = 24
ALERT_THRESHOLD_FAILURES = 3


def _backoff_hours(consecutive_failures: int) -> int:
    """Exponential backoff capped at 24h.

    Args:
        consecutive_failures: antall sammenhengende feil (1 etter første feil).

    Returns:
        Timer til neste retry skal tillates.
    """
    if consecutive_failures <= 0:
        return 0
    return min(2 ** (consecutive_failures - 1), MAX_RETRY_HOURS)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    """UTC → ISO 8601 uten mikrosekunder."""
    return ts.replace(microsecond=0).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# State-helpers
# ---------------------------------------------------------------------------


def get_stream_state(conn: sqlite3.Connection, source: str, stream: str) -> dict:
    """Hent gjeldende state-rad (eller defaults hvis ikke finnes)."""
    row = conn.execute(
        """
        SELECT last_successful_upper_bound, last_successful_sync_at,
               last_error_at, last_error_message, consecutive_failures,
               next_retry_at
          FROM source_stream_state
         WHERE source = ? AND stream = ?
        """,
        (source, stream),
    ).fetchone()
    if row is None:
        return {
            "last_successful_upper_bound": None,
            "last_successful_sync_at": None,
            "last_error_at": None,
            "last_error_message": None,
            "consecutive_failures": 0,
            "next_retry_at": None,
        }
    return dict(row)


def upsert_stream_state(
    conn: sqlite3.Connection,
    source: str,
    stream: str,
    **fields,
) -> None:
    """Skriv state-rad (opprett eller oppdater ved unik (source, stream))."""
    existing = conn.execute(
        "SELECT 1 FROM source_stream_state WHERE source = ? AND stream = ?",
        (source, stream),
    ).fetchone()
    if existing:
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE source_stream_state SET {cols} WHERE source = ? AND stream = ?",
            (*fields.values(), source, stream),
        )
    else:
        cols = ", ".join(["source", "stream", *fields.keys()])
        placeholders = ", ".join(["?"] * (len(fields) + 2))
        conn.execute(
            f"INSERT INTO source_stream_state ({cols}) VALUES ({placeholders})",
            (source, stream, *fields.values()),
        )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


@dataclass
class Source(ABC):
    """Abstract source. Concrete implementations define `name`, `streams`,
    `backfill_days` and implement `fetch_stream`."""

    name: str = field(init=False)
    streams: list[str] = field(init=False)
    backfill_days: dict[str, int] = field(init=False)
    timeout_sec: int = 600

    @abstractmethod
    def fetch_stream(
        self,
        conn: sqlite3.Connection,
        stream: str,
        since_date: str,
    ) -> tuple[int, int]:
        """Hent og skriv data for én strøm.

        Args:
            conn: åpen SQLite-connection.
            stream: strøm-navn (fra self.streams).
            since_date: YYYY-MM-DD — start av backfill-vindu.

        Returns:
            (rows_inserted, rows_updated).

        Raises:
            RetryableError eller FatalError ved feil.
        """

    # -----------------------------------------------------------------
    # Public orchestration
    # -----------------------------------------------------------------

    def should_run(self, conn: sqlite3.Connection, stream: str) -> bool:
        """Sjekk om backoff-vinduet tillater et forsøk nå."""
        state = get_stream_state(conn, self.name, stream)
        next_retry = state.get("next_retry_at")
        if not next_retry:
            return True
        try:
            nrt = datetime.fromisoformat(next_retry.replace("Z", "+00:00"))
        except ValueError:
            return True
        return _now_utc() >= nrt

    def since_date_for(self, conn: sqlite3.Connection, stream: str) -> str:
        """Beregn YYYY-MM-DD hvor backfill skal starte.

        cursor - backfill_days, eller today - backfill_days hvis ingen cursor.
        """
        state = get_stream_state(conn, self.name, stream)
        window = self.backfill_days.get(stream, 30)
        cursor = state.get("last_successful_upper_bound")
        if cursor:
            try:
                anchor = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            except ValueError:
                anchor = _now_utc()
        else:
            anchor = _now_utc()
        return (anchor - timedelta(days=window)).date().isoformat()

    def sync(self, conn: sqlite3.Connection) -> list[StreamResult]:
        """Kjør alle strømmer. Returnerer resultat per strøm."""
        results: list[StreamResult] = []
        for stream in self.streams:
            if not self.should_run(conn, stream):
                results.append(
                    StreamResult(
                        source=self.name,
                        stream=stream,
                        status="skipped",
                        started_at=_iso(_now_utc()),
                        finished_at=_iso(_now_utc()),
                        error_message="backoff window not elapsed",
                    )
                )
                continue
            results.append(self._run_stream(conn, stream))
        return results

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _run_stream(self, conn: sqlite3.Connection, stream: str) -> StreamResult:
        start = _now_utc()
        start_iso = _iso(start)

        # Registrer sync_run i "running"-status
        cur = conn.execute(
            """
            INSERT INTO sync_runs (source, stream, started_at, status)
            VALUES (?, ?, ?, 'running')
            """,
            (self.name, stream, start_iso),
        )
        run_id = cur.lastrowid
        conn.commit()

        since = self.since_date_for(conn, stream)
        try:
            ins, upd = self.fetch_stream(conn, stream, since)
        except (RetryableError, FatalError, Exception) as e:  # noqa: BLE001
            return self._handle_failure(conn, run_id, stream, start_iso, e)

        return self._handle_success(conn, run_id, stream, start_iso, ins, upd)

    def _handle_success(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        stream: str,
        start_iso: str,
        rows_inserted: int,
        rows_updated: int,
    ) -> StreamResult:
        end = _now_utc()
        end_iso = _iso(end)
        conn.execute(
            """
            UPDATE sync_runs
               SET finished_at = ?, status = 'success',
                   rows_inserted = ?, rows_updated = ?
             WHERE id = ?
            """,
            (end_iso, rows_inserted, rows_updated, run_id),
        )
        upsert_stream_state(
            conn,
            self.name,
            stream,
            last_successful_upper_bound=end_iso,
            last_successful_sync_at=end_iso,
            last_error_at=None,
            last_error_message=None,
            consecutive_failures=0,
            next_retry_at=None,
        )
        conn.commit()
        return StreamResult(
            source=self.name,
            stream=stream,
            status="success",
            rows_inserted=rows_inserted,
            rows_updated=rows_updated,
            started_at=start_iso,
            finished_at=end_iso,
        )

    def _handle_failure(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        stream: str,
        start_iso: str,
        exc: BaseException,
    ) -> StreamResult:
        end = _now_utc()
        end_iso = _iso(end)
        msg = f"{type(exc).__name__}: {exc}"

        state = get_stream_state(conn, self.name, stream)
        failures = (state.get("consecutive_failures") or 0) + 1
        backoff_h = _backoff_hours(failures)
        next_retry = _iso(end + timedelta(hours=backoff_h))

        conn.execute(
            """
            UPDATE sync_runs
               SET finished_at = ?, status = 'error', error_message = ?
             WHERE id = ?
            """,
            (end_iso, msg, run_id),
        )
        upsert_stream_state(
            conn,
            self.name,
            stream,
            last_error_at=end_iso,
            last_error_message=msg,
            consecutive_failures=failures,
            next_retry_at=next_retry,
        )

        # Lag alert etter 3 sammenhengende feil — og for FatalError på første
        level = "error" if isinstance(exc, FatalError) else "warning"
        if failures >= ALERT_THRESHOLD_FAILURES or isinstance(exc, FatalError):
            conn.execute(
                """
                INSERT INTO alerts (source, level, message)
                VALUES (?, ?, ?)
                """,
                (
                    self.name,
                    level,
                    f"{self.name}/{stream}: {msg} (failures={failures})",
                ),
            )

        conn.commit()
        return StreamResult(
            source=self.name,
            stream=stream,
            status="error",
            error_message=msg,
            started_at=start_iso,
            finished_at=end_iso,
        )
