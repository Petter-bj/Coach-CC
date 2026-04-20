"""Tests for Source base-class: state-tracking, retry, backoff, alerts."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.db.connection import configure
from src.db.migrations import migrate
from src.sources.base import (
    ALERT_THRESHOLD_FAILURES,
    FatalError,
    RetryableError,
    Source,
    StreamResult,
    _backoff_hours,
    get_stream_state,
    upsert_stream_state,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    configure(c)
    migrate(c)
    try:
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Backoff math
# ---------------------------------------------------------------------------


def test_backoff_zero_on_no_failures() -> None:
    assert _backoff_hours(0) == 0


def test_backoff_exponential_growth() -> None:
    assert _backoff_hours(1) == 1
    assert _backoff_hours(2) == 2
    assert _backoff_hours(3) == 4
    assert _backoff_hours(4) == 8
    assert _backoff_hours(5) == 16


def test_backoff_caps_at_24h() -> None:
    assert _backoff_hours(6) == 24
    assert _backoff_hours(10) == 24
    assert _backoff_hours(100) == 24


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def test_get_stream_state_defaults_when_missing(conn: sqlite3.Connection) -> None:
    s = get_stream_state(conn, "garmin", "daily")
    assert s["consecutive_failures"] == 0
    assert s["last_successful_upper_bound"] is None


def test_upsert_stream_state_insert_then_update(conn: sqlite3.Connection) -> None:
    upsert_stream_state(conn, "garmin", "daily", consecutive_failures=1)
    s = get_stream_state(conn, "garmin", "daily")
    assert s["consecutive_failures"] == 1

    upsert_stream_state(conn, "garmin", "daily", consecutive_failures=2)
    s = get_stream_state(conn, "garmin", "daily")
    assert s["consecutive_failures"] == 2


# ---------------------------------------------------------------------------
# Dummy source used by sync-level tests
# ---------------------------------------------------------------------------


class DummySource(Source):
    """Test-subklasse med kontrollerbar oppførsel."""

    def __init__(self, behaviors: dict[str, object]) -> None:
        self.name = "dummy"
        self.streams = list(behaviors.keys())
        self.backfill_days = {k: 7 for k in behaviors}
        self._behaviors = behaviors  # stream → exception-klasse ELLER (ins, upd)

    def fetch_stream(self, conn, stream, since_date):
        behavior = self._behaviors[stream]
        if isinstance(behavior, type) and issubclass(behavior, Exception):
            raise behavior(f"simulated {behavior.__name__} for {stream}")
        return behavior  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Sync-orkestrering
# ---------------------------------------------------------------------------


def test_successful_sync_updates_state(conn: sqlite3.Connection) -> None:
    src = DummySource({"daily": (10, 0)})
    [r] = src.sync(conn)

    assert r.status == "success"
    assert r.rows_inserted == 10
    s = get_stream_state(conn, "dummy", "daily")
    assert s["consecutive_failures"] == 0
    assert s["last_successful_upper_bound"] is not None
    assert s["next_retry_at"] is None


def test_successful_sync_records_sync_run(conn: sqlite3.Connection) -> None:
    src = DummySource({"daily": (3, 1)})
    src.sync(conn)
    row = conn.execute(
        "SELECT status, rows_inserted, rows_updated, error_message "
        "FROM sync_runs WHERE source='dummy' AND stream='daily'"
    ).fetchone()
    assert row["status"] == "success"
    assert row["rows_inserted"] == 3
    assert row["rows_updated"] == 1
    assert row["error_message"] is None


def test_retryable_error_sets_backoff_and_no_alert(conn: sqlite3.Connection) -> None:
    src = DummySource({"daily": RetryableError})
    [r] = src.sync(conn)

    assert r.status == "error"
    s = get_stream_state(conn, "dummy", "daily")
    assert s["consecutive_failures"] == 1
    assert s["next_retry_at"] is not None

    alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    assert alerts == 0  # under terskel, ingen alert ennå


def test_three_consecutive_failures_creates_alert(conn: sqlite3.Connection) -> None:
    src = DummySource({"daily": RetryableError})

    # First 2 failures — no alert
    for _ in range(ALERT_THRESHOLD_FAILURES - 1):
        # Clear next_retry_at to allow immediate retry in test
        upsert_stream_state(conn, "dummy", "daily", next_retry_at=None)
        src.sync(conn)

    alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    assert alerts == 0

    # Third failure triggers alert
    upsert_stream_state(conn, "dummy", "daily", next_retry_at=None)
    src.sync(conn)

    alerts = conn.execute("SELECT COUNT(*), level FROM alerts").fetchone()
    assert alerts[0] == 1
    assert alerts[1] == "warning"  # RetryableError


def test_fatal_error_creates_alert_immediately(conn: sqlite3.Connection) -> None:
    src = DummySource({"daily": FatalError})
    src.sync(conn)

    row = conn.execute("SELECT level, message FROM alerts").fetchone()
    assert row["level"] == "error"
    assert "FatalError" in row["message"]


def test_skip_when_backoff_window_not_elapsed(conn: sqlite3.Connection) -> None:
    src = DummySource({"daily": (0, 0)})
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    upsert_stream_state(conn, "dummy", "daily", next_retry_at=future, consecutive_failures=2)

    [r] = src.sync(conn)
    assert r.status == "skipped"
    assert "backoff" in (r.error_message or "").lower()


def test_success_after_failure_resets_state(conn: sqlite3.Connection) -> None:
    # Første run: feil
    src_bad = DummySource({"daily": RetryableError})
    upsert_stream_state(conn, "dummy", "daily", next_retry_at=None)
    src_bad.sync(conn)
    assert get_stream_state(conn, "dummy", "daily")["consecutive_failures"] == 1

    # Andre run: suksess
    src_good = DummySource({"daily": (5, 0)})
    upsert_stream_state(conn, "dummy", "daily", next_retry_at=None)
    [r] = src_good.sync(conn)

    assert r.status == "success"
    s = get_stream_state(conn, "dummy", "daily")
    assert s["consecutive_failures"] == 0
    assert s["next_retry_at"] is None
    assert s["last_error_message"] is None


def test_streams_sync_independently(conn: sqlite3.Connection) -> None:
    """Hvis ett stream feiler, skal de andre fortsatt gå gjennom."""
    src = DummySource({
        "daily": (10, 0),
        "sleep": RetryableError,
        "hrv": (3, 0),
    })
    results = src.sync(conn)
    by_stream = {r.stream: r for r in results}

    assert by_stream["daily"].status == "success"
    assert by_stream["sleep"].status == "error"
    assert by_stream["hrv"].status == "success"


def test_since_date_uses_backfill_window(conn: sqlite3.Connection) -> None:
    src = DummySource({"daily": (0, 0)})
    src.backfill_days["daily"] = 14

    # Ingen cursor — since skal være today - 14
    from datetime import date
    expected = (date.today() - timedelta(days=14)).isoformat()
    assert src.since_date_for(conn, "daily") == expected
