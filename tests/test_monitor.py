"""Tester for bot-heartbeat-monitor (pure logic — ingen ekte Telegram/tmux-kall)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.monitor import (
    DEDUPE_MINUTES,
    Issue,
    has_recent_auth_error,
    mark_alerted,
    should_alert,
)


# ---------------------------------------------------------------------------
# has_recent_auth_error
# ---------------------------------------------------------------------------


def test_detects_401_error() -> None:
    pane = """
← telegram · Pettrrrrr: test
  ⎿  Please run /login · API Error: 401
"""
    assert has_recent_auth_error(pane) is True


def test_detects_authentication_error_json() -> None:
    pane = '{"type":"error","error":{"type":"authentication_error","message":"Invalid"}}'
    assert has_recent_auth_error(pane) is True


def test_detects_invalid_credentials_message() -> None:
    pane = 'Invalid authentication credentials in response'
    assert has_recent_auth_error(pane) is True


def test_no_error_in_healthy_pane() -> None:
    pane = """
  Listening for channel messages from: plugin:telegram@claude-plugins-official
← telegram · Pettrrrrr: hello
⏺ Svarte på hello
"""
    assert has_recent_auth_error(pane) is False


def test_empty_pane_no_error() -> None:
    assert has_recent_auth_error("") is False


# ---------------------------------------------------------------------------
# Dedupe (should_alert / mark_alerted)
# ---------------------------------------------------------------------------


def test_first_alert_always_sends() -> None:
    state = {"last_alerts": {}}
    assert should_alert(state, "process_dead") is True


def test_recent_alert_deduped() -> None:
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=5)).isoformat()
    state = {"last_alerts": {"process_dead": recent}}
    assert should_alert(state, "process_dead", now) is False


def test_old_alert_allows_resend() -> None:
    now = datetime.now(timezone.utc)
    old = (now - timedelta(minutes=DEDUPE_MINUTES + 1)).isoformat()
    state = {"last_alerts": {"process_dead": old}}
    assert should_alert(state, "process_dead", now) is True


def test_different_issue_type_not_deduped() -> None:
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=5)).isoformat()
    state = {"last_alerts": {"process_dead": recent}}
    # Ny issue-type skal alertes uavhengig av recent
    assert should_alert(state, "auth_401", now) is True


def test_mark_alerted_updates_state() -> None:
    state = {"last_alerts": {}}
    now = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)
    mark_alerted(state, "process_dead", now)
    assert state["last_alerts"]["process_dead"] == now.isoformat()


def test_mark_alerted_overwrites_old() -> None:
    now = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=3)).isoformat()
    state = {"last_alerts": {"process_dead": old}}
    mark_alerted(state, "process_dead", now)
    assert state["last_alerts"]["process_dead"] == now.isoformat()


def test_malformed_timestamp_allows_alert() -> None:
    """Hvis timestamp i state er ødelagt, send alert (fail open)."""
    state = {"last_alerts": {"process_dead": "not-a-date"}}
    assert should_alert(state, "process_dead") is True


# ---------------------------------------------------------------------------
# Issue dataclass
# ---------------------------------------------------------------------------


def test_issue_defaults() -> None:
    i = Issue(type="auth_401", message="Auth expired")
    assert i.auto_recoverable is False  # default


def test_issue_auto_recoverable() -> None:
    i = Issue(type="process_dead", message="Dead", auto_recoverable=True)
    assert i.auto_recoverable is True


# ---------------------------------------------------------------------------
# Sync stale-detection (E)
# ---------------------------------------------------------------------------


def test_sync_stale_check_no_db(monkeypatch, tmp_path) -> None:
    """Hvis DB ikke eksisterer (fresh install) returneres None — ikke alert."""
    from src import monitor
    monkeypatch.setattr(monitor, "DB_PATH", tmp_path / "missing.db")
    assert monitor.hours_since_last_successful_sync() is None


def test_sync_stale_check_with_recent_success(monkeypatch, tmp_path) -> None:
    """Sync som kjørte for 1 time siden → ikke stale."""
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from src import monitor

    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE sync_runs (
            source TEXT, stream TEXT, started_at TEXT, finished_at TEXT,
            status TEXT, rows_inserted INT, rows_updated INT, error_message TEXT
        )
    """)
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute(
        "INSERT INTO sync_runs (source, stream, finished_at, status) "
        "VALUES ('garmin', 'daily', ?, 'success')",
        (one_hour_ago,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(monitor, "DB_PATH", db)
    hours = monitor.hours_since_last_successful_sync()
    assert hours is not None
    assert 0.9 < hours < 1.2


def test_sync_stale_check_with_old_success(monkeypatch, tmp_path) -> None:
    """Sync 24t gammel → stale (over 12t-grense)."""
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from src import monitor

    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE sync_runs (
            source TEXT, stream TEXT, started_at TEXT, finished_at TEXT,
            status TEXT, rows_inserted INT, rows_updated INT, error_message TEXT
        )
    """)
    old = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute(
        "INSERT INTO sync_runs (source, stream, finished_at, status) "
        "VALUES ('garmin', 'daily', ?, 'success')",
        (old,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(monitor, "DB_PATH", db)
    hours = monitor.hours_since_last_successful_sync()
    assert hours is not None
    assert hours > monitor.SYNC_STALE_HOURS


def test_sync_stale_check_only_failures(monkeypatch, tmp_path) -> None:
    """Bare error-rader, ingen success → returnerer None."""
    import sqlite3
    from src import monitor

    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE sync_runs (
            source TEXT, stream TEXT, started_at TEXT, finished_at TEXT,
            status TEXT, rows_inserted INT, rows_updated INT, error_message TEXT
        )
    """)
    conn.execute(
        "INSERT INTO sync_runs (status, finished_at) VALUES ('error', '2026-05-09T10:00:00Z')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(monitor, "DB_PATH", db)
    assert monitor.hours_since_last_successful_sync() is None
