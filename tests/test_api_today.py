"""Tester for read-only kontrakten bak dashboardets «I dag»-side."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, timedelta

import httpx

from src.api.app import create_app
from src.api.today import build_today_payload
from src.db.connection import configure
from src.db.migrations import migrate


def _seed(conn: sqlite3.Connection, target_date: date) -> None:
    day = target_date.isoformat()
    saturday = (target_date - timedelta(days=1)).isoformat()
    monday = (target_date - timedelta(days=target_date.weekday())).isoformat()
    conn.execute(
        """
        INSERT INTO garmin_daily (local_date, resting_hr, training_readiness_score,
                                  training_readiness_level)
        VALUES (?, 47, 84, 'HIGH')
        """,
        (day,),
    )
    conn.execute(
        """
        INSERT INTO garmin_sleep (local_date, duration_sec, sleep_score,
                                  sleep_score_qualifier)
        VALUES (?, 28080, 90, 'GOOD')
        """,
        (day,),
    )
    conn.execute(
        """
        INSERT INTO garmin_hrv (local_date, last_night_avg_ms, status)
        VALUES (?, 73, 'BALANCED')
        """,
        (day,),
    )
    conn.executemany(
        """
        INSERT INTO user_baselines (metric, window_days, value, median, mad,
                                    sample_size, insufficient_data)
        VALUES (?, 30, ?, ?, 1, 30, 0)
        """,
        [
            ("resting_hr", 50, 50),
            ("sleep_score", 85, 85),
            ("hrv_last_night_ms", 66, 66),
            ("training_readiness", 75, 75),
        ],
    )
    conn.execute(
        """
        INSERT INTO source_stream_state (source, stream, last_successful_sync_at)
        VALUES ('garmin', 'daily', '2026-07-19T06:20:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO planned_sessions (planned_date, type, description, status,
                                      target_metrics)
        VALUES (?, 'threshold_run', '6 × 3 min @ terskel', 'planned',
                '{"duration_min": 54}')
        """,
        (day,),
    )
    conn.execute(
        """
        INSERT INTO planned_sessions (planned_date, type, description, status)
        VALUES (?, 'easy_run', 'Rolig langtur', 'completed')
        """,
        (saturday,),
    )
    conn.execute(
        """
        INSERT INTO planned_sessions (planned_date, type, description, status)
        VALUES (?, 'rest', 'Hvile', 'skipped')
        """,
        (monday,),
    )
    conn.commit()


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    configure(conn)
    migrate(conn)
    return conn


def test_build_today_payload_separates_sources_and_baselines() -> None:
    conn = _connection()
    target = date(2026, 7, 19)
    try:
        _seed(conn, target)
        payload = build_today_payload(conn, target)
    finally:
        conn.close()

    assert payload["date"] == "2026-07-19"
    assert payload["sources"]["garmin"] == {
        "last_synced_at": "2026-07-19T06:20:00Z",
        "source": "automatic",
    }
    assert payload["recommendation"]["source"] == "coach_rules"
    assert payload["planned_sessions"][0]["target_metrics"] == {"duration_min": 54}
    assert payload["metrics"]["readiness"]["value"] == 84
    assert payload["metrics"]["readiness"]["delta"] == 9
    assert payload["metrics"]["hrv"]["baseline"] == 66
    assert payload["metrics"]["resting_hr"]["delta"] == -3
    assert payload["week"]["completed_sessions"] == 1
    saturday = next(day for day in payload["week"]["days"] if day["date"] == "2026-07-18")
    assert saturday["status"] == "completed"
    assert payload["reviews"] == []


def test_today_endpoint_requires_configured_bearer_token(tmp_path) -> None:
    db_path = tmp_path / "trening.db"
    conn = sqlite3.connect(db_path)
    configure(conn)
    migrate(conn)
    _seed(conn, date(2026, 7, 19))
    conn.close()

    async def request_today() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(db_path=db_path, api_token="test-token")
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            denied = await client.get("/api/today?day=2026-07-19")
            allowed = await client.get(
                "/api/today?day=2026-07-19",
                headers={"Authorization": "Bearer test-token"},
            )
        return denied, allowed

    denied, allowed = asyncio.run(request_today())

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["metrics"]["sleep"]["duration_sec"] == 28080
