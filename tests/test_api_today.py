"""Tester for read-only kontrakten bak dashboardets «I dag»-side."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, timedelta

import httpx

from src.api.app import create_app
from src.api.today import build_today_payload
from src.api.week import build_day_log, build_week_overview
from src.coaching.deepseek import CoachReply
from src.coaching.reviews import ensure_pending_reviews
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
    workout = conn.execute(
        """
        INSERT INTO workouts (source, external_id, started_at_utc, timezone,
                              local_date, type, duration_sec, avg_hr, distance_m)
        VALUES ('garmin', 'review-workout', ?, 'Europe/Oslo', ?, 'running', 3480, 138, 10400)
        """,
        (f"{saturday}T09:00:00Z", saturday),
    )
    conn.execute(
        """
        INSERT INTO planned_sessions (planned_date, type, description, status, workout_id,
                                      target_metrics)
        VALUES (?, 'easy_run', 'Rolig langtur', 'completed', ?, '{"duration_min": 60}')
        """,
        (saturday, workout.lastrowid),
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
        assert ensure_pending_reviews(conn) == 1
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
    assert payload["recent_workouts"] == [{
        "id": 1,
        "local_date": "2026-07-18",
        "type": "running",
        "duration_sec": 3480,
        "distance_m": 10400,
        "avg_hr": 138,
        "source": "garmin",
    }]
    saturday = next(day for day in payload["week"]["days"] if day["date"] == "2026-07-18")
    assert saturday["status"] == "completed"
    assert payload["reviews"][0]["planned_session"]["date"] == "2026-07-18"
    assert payload["reviews"][0]["actual"]["duration_sec"] == 3480
    assert payload["reviews"][0]["coach"]["source"] == "coach_rules"


def test_week_overview_and_day_log_keep_sources_separate() -> None:
    conn = _connection()
    target = date(2026, 7, 19)
    try:
        _seed(conn, target)
        assert ensure_pending_reviews(conn) == 1
        conn.execute(
            """
            INSERT INTO withings_weight (grpid, measured_at_utc, timezone, local_date,
                                         weight_kg, fat_ratio_pct)
            VALUES (1, '2026-07-18T07:00:00Z', 'Europe/Oslo', '2026-07-18', 75.2, 14.1)
            """
        )
        conn.execute(
            """
            INSERT INTO garmin_daily (local_date, resting_hr, training_readiness_score,
                                      training_readiness_level, steps)
            VALUES ('2026-07-18', 48, 79, 'MODERATE', 10240)
            """
        )
        conn.execute(
            """
            INSERT INTO garmin_hrv (local_date, last_night_avg_ms, status)
            VALUES ('2026-07-18', 68, 'BALANCED')
            """
        )
        conn.execute(
            """
            INSERT INTO yazio_daily (local_date, kcal, protein_g, carbs_g, fat_g, water_ml,
                                     kcal_goal, protein_goal_g)
            VALUES ('2026-07-18', 2750, 178, 310, 81, 2200, 2800, 180)
            """
        )
        conn.commit()
        week = build_week_overview(conn, target)
        day = build_day_log(conn, date(2026, 7, 18))
    finally:
        conn.close()

    saturday = next(item for item in week["days"] if item["date"] == "2026-07-18")
    assert saturday["status"] == "review"
    assert saturday["workouts"][0]["distance_m"] == 10400
    assert saturday["planned_sessions"][0]["description"] == "Rolig langtur"
    assert week["workout_count"] == 1
    assert week["total_duration_sec"] == 3480
    assert week["total_distance_m"] == 10400
    assert week["pending_reviews"] == 1
    assert week["training_days"] == 1
    assert day["workouts"][0]["source"] == "garmin"
    assert day["automatic"]["garmin_daily"]["training_readiness_score"] == 79
    assert day["automatic"]["hrv"] == {
        "last_night_avg_ms": 68,
        "weekly_avg_ms": None,
        "status": "BALANCED",
    }
    assert day["automatic"]["weight"] == {"weight_kg": 75.2, "fat_ratio_pct": 14.1}
    assert day["automatic"]["nutrition"]["protein_g"] == 178
    assert day["coach_reviews"][0]["source"] == "coach_rules"


def test_dashboard_is_served_and_api_accepts_tailscale_identity(tmp_path) -> None:
    db_path = tmp_path / "trening.db"
    conn = sqlite3.connect(db_path)
    configure(conn)
    migrate(conn)
    _seed(conn, date(2026, 7, 19))
    conn.close()

    async def request_today() -> tuple[
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
    ]:
        transport = httpx.ASGITransport(
            app=create_app(db_path=db_path, api_token="test-token")
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            dashboard = await client.get("/")
            denied = await client.get("/api/today?day=2026-07-19")
            allowed = await client.get(
                "/api/today?day=2026-07-19",
                headers={"Authorization": "Bearer test-token"},
            )
            tailscale_allowed = await client.get(
                "/api/today?day=2026-07-19",
                headers={"Tailscale-User-Login": "petter@example.com"},
            )
            week = await client.get(
                "/api/week?start=2026-07-19",
                headers={"Authorization": "Bearer test-token"},
            )
            day_log = await client.get(
                "/api/days/2026-07-18",
                headers={"Authorization": "Bearer test-token"},
            )
            workout_detail = await client.get(
                "/api/workouts/1",
                headers={"Authorization": "Bearer test-token"},
            )
        return dashboard, denied, allowed, tailscale_allowed, week, day_log, workout_detail

    dashboard, denied, allowed, tailscale_allowed, week, day_log, workout_detail = asyncio.run(request_today())

    assert dashboard.status_code == 200
    assert "God morgen, Petter" in dashboard.text
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["metrics"]["sleep"]["duration_sec"] == 28080
    assert tailscale_allowed.status_code == 200
    assert week.status_code == 200
    assert week.json()["start"] == "2026-07-13"
    assert day_log.status_code == 200
    assert day_log.json()["workouts"][0]["local_date"] == "2026-07-18"
    assert workout_detail.status_code == 200
    assert workout_detail.json()["workout"]["distance_m"] == 10400
    assert workout_detail.json()["matched_plan"]["description"] == "Rolig langtur"


def test_confirm_review_persists_optional_note_and_removes_pending_card(tmp_path) -> None:
    db_path = tmp_path / "trening.db"
    conn = sqlite3.connect(db_path)
    configure(conn)
    migrate(conn)
    _seed(conn, date(2026, 7, 19))
    assert ensure_pending_reviews(conn) == 1
    review_id = conn.execute("SELECT id FROM session_reviews").fetchone()["id"]
    conn.commit()
    conn.close()

    async def confirm() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(db_path=db_path, api_token="test-token")
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            denied = await client.post(f"/api/reviews/{review_id}/confirm", json={})
            confirmed = await client.post(
                f"/api/reviews/{review_id}/confirm",
                headers={"Authorization": "Bearer test-token"},
                json={"note": "  Møllefarten var 11,5 km/t.  "},
            )
            stale = await client.post(
                f"/api/reviews/{review_id}/confirm",
                headers={"Authorization": "Bearer test-token"},
                json={},
            )
        return denied, confirmed, stale

    denied, confirmed, stale = asyncio.run(confirm())

    assert denied.status_code == 401
    assert confirmed.status_code == 200
    assert confirmed.json() == {
        "id": review_id,
        "status": "reviewed",
        "user_note": "Møllefarten var 11,5 km/t.",
    }
    assert stale.status_code == 409

    conn = sqlite3.connect(db_path)
    configure(conn)
    review = conn.execute(
        "SELECT status, user_note, reviewed_at FROM session_reviews WHERE id = ?",
        (review_id,),
    ).fetchone()
    conn.close()
    assert review["status"] == "reviewed"
    assert review["user_note"] == "Møllefarten var 11,5 km/t."
    assert review["reviewed_at"] is not None


def test_review_note_reconsiders_same_card_before_it_can_be_confirmed(tmp_path) -> None:
    db_path = tmp_path / "trening.db"
    conn = sqlite3.connect(db_path)
    configure(conn)
    migrate(conn)
    _seed(conn, date(2026, 7, 19))
    assert ensure_pending_reviews(conn) == 1
    review_id = conn.execute("SELECT id FROM session_reviews").fetchone()["id"]
    conn.commit()
    conn.close()

    seen: dict = {}

    def responder(question: str, context: dict) -> CoachReply:
        seen["question"] = question
        seen["context"] = context
        return CoachReply(
            answer="Med 11,5 km/t på mølla var økta fortsatt kontrollert. Belastningen passer planen.",
            model="deepseek-v4-pro",
        )

    async def reconsider_and_confirm() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(
                db_path=db_path,
                api_token="test-token",
                coach_responder=responder,
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            reconsidered = await client.post(
                f"/api/reviews/{review_id}/reconsider",
                headers={"Authorization": "Bearer test-token"},
                json={"note": "Møllefarten var 11,5 km/t."},
            )
            confirmed = await client.post(
                f"/api/reviews/{review_id}/confirm",
                headers={"Authorization": "Bearer test-token"},
                json={},
            )
        return reconsidered, confirmed

    reconsidered, confirmed = asyncio.run(reconsider_and_confirm())

    assert reconsidered.status_code == 200
    assert reconsidered.json() == {
        "review": {
            "id": review_id,
            "status": "pending",
            "user_note": "Møllefarten var 11,5 km/t.",
            "coach_source": "agent",
            "coach_comment": "Med 11,5 km/t på mølla var økta fortsatt kontrollert. Belastningen passer planen.",
        },
        "model": "deepseek-v4-pro",
        "changes_applied": False,
    }
    assert confirmed.status_code == 200
    assert confirmed.json()["user_note"] == "Møllefarten var 11,5 km/t."
    assert "Vurder bare denne økten" in seen["question"]
    review_context = seen["context"]["review_in_progress"]
    assert review_context["user_reported_deviation"] == "Møllefarten var 11,5 km/t."
    assert review_context["actual"]["duration_sec"] == 3480
    assert "id" not in json.dumps(review_context)

    conn = sqlite3.connect(db_path)
    configure(conn)
    review = conn.execute(
        "SELECT status, user_note, coach_source, coach_comment, reviewed_at "
        "FROM session_reviews WHERE id = ?",
        (review_id,),
    ).fetchone()
    conn.close()
    assert review["status"] == "reviewed"
    assert review["user_note"] == "Møllefarten var 11,5 km/t."
    assert review["coach_source"] == "agent"
    assert review["coach_comment"].startswith("Med 11,5 km/t")
    assert review["reviewed_at"] is not None


def test_coach_chat_is_private_read_only_and_receives_curated_context(tmp_path) -> None:
    db_path = tmp_path / "trening.db"
    target = date.today()
    conn = sqlite3.connect(db_path)
    configure(conn)
    migrate(conn)
    _seed(conn, target)
    conn.execute(
        "INSERT INTO goals (title, priority) VALUES ('Raskere 10 km', 'A')"
    )
    conn.execute(
        """
        INSERT INTO garmin_activity_details (
            workout_id, garmin_activity_id, start_latitude, start_longitude, raw_json
        ) VALUES (1, 99, 59.9, 10.7, '{"sensitive": true}')
        """
    )
    conn.commit()
    conn.close()

    seen: dict = {}

    def responder(question: str, context: dict) -> CoachReply:
        seen["question"] = question
        seen["context"] = context
        return CoachReply(answer="Forslag: hold økta rolig. Planen er ikke endret.",
                          model="deepseek-v4-pro")

    async def chat() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(
                db_path=db_path,
                api_token="test-token",
                coach_responder=responder,
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            denied = await client.post("/api/coach/chat", json={"message": "Hva nå?"})
            allowed = await client.post(
                "/api/coach/chat",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "message": "Bør jeg løpe?",
                    "history": [
                        {"role": "user", "content": "Kneet var stivt."},
                        {"role": "assistant", "content": "Ta en rolig dag."},
                    ],
                },
            )
        return denied, allowed

    denied, allowed = asyncio.run(chat())

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json() == {
        "answer": "Forslag: hold økta rolig. Planen er ikke endret.",
        "model": "deepseek-v4-pro",
        "changes_applied": False,
        "injury_proposal": None,
        "messages": [
            {"role": "user", "content": "Bør jeg løpe?"},
            {
                "role": "assistant",
                "content": "Forslag: hold økta rolig. Planen er ikke endret.",
                "model": "deepseek-v4-pro",
            },
        ],
    }
    assert seen["question"] == "Bør jeg løpe?"
    assert seen["context"]["conversation_history"] == [
        {"role": "user", "content": "Kneet var stivt."},
        {"role": "assistant", "content": "Ta en rolig dag."},
    ]
    assert seen["context"]["goals"][0]["title"] == "Raskere 10 km"
    assert seen["context"]["recent_workouts"]
    encoded_context = json.dumps(seen["context"])
    assert "start_latitude" not in encoded_context
    assert "start_longitude" not in encoded_context
    assert "sensitive" not in encoded_context
