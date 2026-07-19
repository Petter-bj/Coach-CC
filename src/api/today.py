"""Read-only datakontrakt for dashboardets «I dag»-side.

Denne modulen er bevisst uavhengig av FastAPI. Det gjør at den samme
kontrakten kan testes direkte mot SQLite, og at HTTP-laget ikke får lov til å
inneholde coaching- eller SQL-logikk.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from src.analysis.recovery import recovery_snapshot


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _baseline_metric(snapshot_metric: dict[str, Any], *, source: str = "garmin") -> dict[str, Any]:
    """Normaliser en recovery-metrikk for klienten."""
    return {
        "source": source,
        "value": snapshot_metric.get("value"),
        "baseline": snapshot_metric.get("baseline"),
        "delta": snapshot_metric.get("delta"),
        "status": snapshot_metric.get("status", "unknown"),
    }


def _decode_target_metrics(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _planned_session(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "date": row["planned_date"],
        "type": row["type"],
        "description": row["description"],
        "status": row["status"],
        "target_metrics": _decode_target_metrics(row["target_metrics"]),
        "notes": row["notes"],
        "workout_id": row["workout_id"],
        "source": "plan",
    }


def _week_payload(conn: sqlite3.Connection, target_date: date) -> dict[str, Any]:
    week_start = target_date - timedelta(days=target_date.weekday())
    week_end = week_start + timedelta(days=6)
    rows = conn.execute(
        """
        SELECT id, planned_date, type, description, target_metrics, status,
               notes, workout_id
          FROM planned_sessions
         WHERE planned_date BETWEEN ? AND ?
         ORDER BY planned_date, id
        """,
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchall()
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        session = _planned_session(row)
        by_date.setdefault(session["date"], []).append(session)

    days = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        sessions = by_date.get(day.isoformat(), [])
        # Én planlagt økt per dag er dagens eksisterende datamodell. Skulle
        # flere dukke opp, sender vi alle og lar klienten vise den mest
        # relevante som primærøkt.
        status = sessions[0]["status"] if len(sessions) == 1 else (
            "rest" if not sessions else "multiple"
        )
        days.append({
            "date": day.isoformat(),
            "weekday": day.weekday(),
            "status": status,
            "sessions": sessions,
        })

    completed = [session for sessions in by_date.values() for session in sessions
                 if session["status"] == "completed"]
    return {
        "start": week_start.isoformat(),
        "end": week_end.isoformat(),
        "planned_sessions": len(rows),
        "completed_sessions": len(completed),
        "days": days,
    }


def _garmin_sync_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT MAX(last_successful_sync_at) AS synced_at
          FROM source_stream_state
         WHERE source = 'garmin'
        """
    ).fetchone()
    return row["synced_at"] if row else None


def _latest_garmin_rows(
    conn: sqlite3.Connection, target_date: date
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    as_of = target_date.isoformat()
    daily = _row_dict(conn.execute(
        "SELECT * FROM garmin_daily WHERE local_date <= ? "
        "ORDER BY local_date DESC LIMIT 1", (as_of,)
    ).fetchone())
    sleep = _row_dict(conn.execute(
        "SELECT * FROM garmin_sleep WHERE local_date <= ? "
        "ORDER BY local_date DESC LIMIT 1", (as_of,)
    ).fetchone())
    hrv = _row_dict(conn.execute(
        "SELECT * FROM garmin_hrv WHERE local_date <= ? "
        "ORDER BY local_date DESC LIMIT 1", (as_of,)
    ).fetchone())
    return daily, sleep, hrv


def _pending_reviews(conn: sqlite3.Connection, target_date: date) -> list[dict[str, Any]]:
    """Returner ubekreftede, nylig automatisk matchede økter."""
    cutoff = (target_date - timedelta(days=7)).isoformat()
    rows = conn.execute(
        """
        SELECT r.id AS review_id, r.status AS review_status, r.coach_source,
               r.coach_comment, r.created_at,
               p.id AS planned_session_id, p.planned_date, p.type,
               p.description, p.target_metrics,
               w.id AS workout_id, w.source AS workout_source,
               w.duration_sec, w.avg_hr, w.distance_m
          FROM session_reviews r
          JOIN planned_sessions p ON p.id = r.planned_session_id
          JOIN workouts w ON w.id = r.workout_id
         WHERE r.status = 'pending'
           AND p.planned_date BETWEEN ? AND ?
         ORDER BY p.planned_date DESC, r.id DESC
        """,
        (cutoff, target_date.isoformat()),
    ).fetchall()
    reviews = []
    for row in rows:
        reviews.append({
            "id": row["review_id"],
            "status": row["review_status"],
            "source": "automatic",
            "created_at": row["created_at"],
            "planned_session": {
                "id": row["planned_session_id"],
                "date": row["planned_date"],
                "type": row["type"],
                "description": row["description"],
                "target_metrics": _decode_target_metrics(row["target_metrics"]),
            },
            "actual": {
                "workout_id": row["workout_id"],
                "source": row["workout_source"],
                "duration_sec": row["duration_sec"],
                "avg_hr": row["avg_hr"],
                "distance_m": row["distance_m"],
            },
            "coach": {
                "source": row["coach_source"],
                "comment": row["coach_comment"],
            },
        })
    return reviews


def build_today_payload(conn: sqlite3.Connection, target_date: date | None = None) -> dict[str, Any]:
    """Bygg API-responsen for én dags dashboard, uten å skrive til databasen."""
    target_date = target_date or date.today()
    snapshot = recovery_snapshot(conn, target_date)
    daily, sleep, hrv = _latest_garmin_rows(conn, target_date)
    planned_rows = conn.execute(
        """
        SELECT id, planned_date, type, description, target_metrics, status,
               notes, workout_id
          FROM planned_sessions
         WHERE planned_date = ?
         ORDER BY id
        """,
        (target_date.isoformat(),),
    ).fetchall()

    readiness = _baseline_metric(snapshot["readiness"]["vs_baseline"])
    readiness.update({
        "label": "training_readiness",
        "local_date": daily["local_date"] if daily else None,
        "level": snapshot["readiness"].get("garmin_level"),
    })
    sleep_score = _baseline_metric(snapshot["sleep_score"])
    sleep_score.update({
        "label": "sleep_score",
        "local_date": sleep["local_date"] if sleep else None,
        "duration_sec": sleep.get("duration_sec") if sleep else None,
        "qualifier": sleep.get("sleep_score_qualifier") if sleep else None,
    })
    hrv_metric = _baseline_metric(snapshot["hrv"])
    hrv_metric.update({
        "label": "hrv_last_night_ms",
        "local_date": hrv["local_date"] if hrv else None,
        "unit": "ms",
    })
    rhr_metric = _baseline_metric(snapshot["resting_hr"])
    rhr_metric.update({
        "label": "resting_hr",
        "local_date": daily["local_date"] if daily else None,
        "unit": "bpm",
    })

    return {
        "date": target_date.isoformat(),
        "sources": {
            "garmin": {
                "last_synced_at": _garmin_sync_at(conn),
                "source": "automatic",
            },
        },
        # Foreløpig er anbefalingen den eksisterende, deterministiske
        # recovery-regelmotoren. LLM-en skal senere foreslå tekst og verktøy-
        # kall oppå denne konteksten, ikke erstatte grunnlaget.
        "recommendation": {
            "source": "coach_rules",
            "kind": snapshot["recommendation"],
            "rationale": snapshot["rationale"],
        },
        "planned_sessions": [_planned_session(row) for row in planned_rows],
        "metrics": {
            "readiness": readiness,
            "sleep": sleep_score,
            "hrv": hrv_metric,
            "resting_hr": rhr_metric,
        },
        "week": _week_payload(conn, target_date),
        "reviews": _pending_reviews(conn, target_date),
    }
