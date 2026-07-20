"""Lesekontrakter for Uke-siden og én valgt dagslogg."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any


def _decode_metrics(value: str | None) -> dict[str, Any] | None:
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
        "target_metrics": _decode_metrics(row["target_metrics"]),
        "notes": row["notes"],
        "source": "plan",
    }


def _workout(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source": row["source"],
        "started_at_utc": row["started_at_utc"],
        "local_date": row["local_date"],
        "type": row["type"],
        "duration_sec": row["duration_sec"],
        "distance_m": row["distance_m"],
        "avg_hr": row["avg_hr"],
        "max_hr": row["max_hr"],
        "calories": row["calories"],
        "elevation_gain_m": row["elevation_gain_m"],
        "rpe": row["rpe"],
        "session_load": row["session_load"],
        "notes": row["notes"],
    }


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _block_context(
    conn: sqlite3.Connection,
    *,
    week_start: date,
    week_end: date,
) -> dict[str, Any] | None:
    """Returner den strategiske blokken som dekker den valgte kalenderuken."""
    row = conn.execute(
        """
        SELECT b.id, b.name, b.phase, b.start_date, b.end_date,
               bw.focus, bw.progression_note, bw.planned_volume_note, bw.is_deload
          FROM training_blocks AS b
          LEFT JOIN training_block_weeks AS bw
            ON bw.training_block_id = b.id
           AND bw.week_start = ?
         WHERE b.start_date <= ?
           AND b.end_date >= ?
         ORDER BY b.start_date DESC
         LIMIT 1
        """,
        (week_start.isoformat(), week_end.isoformat(), week_start.isoformat()),
    ).fetchone()
    if row is None:
        return None

    block_start = date.fromisoformat(row["start_date"])
    block_end = date.fromisoformat(row["end_date"])
    return {
        "id": row["id"],
        "name": row["name"],
        "phase": row["phase"],
        "week_number": ((week_start - block_start).days // 7) + 1,
        "total_weeks": ((block_end - block_start).days // 7) + 1,
        "focus": row["focus"] or "Planlegg ukens konkrete økter",
        "progression_note": row["progression_note"],
        "planned_volume_note": row["planned_volume_note"],
        "is_deload": bool(row["is_deload"]),
    }


def build_week_overview(
    conn: sqlite3.Connection,
    week_of: date | None = None,
) -> dict[str, Any]:
    """Returner en ukes kompakte status uten rå helsedata."""
    week_start = _week_start(week_of or date.today())
    week_end = week_start + timedelta(days=6)
    start, end = week_start.isoformat(), week_end.isoformat()

    planned_rows = conn.execute(
        """
        SELECT id, planned_date, type, description, status, target_metrics, notes
          FROM planned_sessions
         WHERE planned_date BETWEEN ? AND ?
         ORDER BY planned_date, id
        """,
        (start, end),
    ).fetchall()
    workouts_rows = conn.execute(
        """
        SELECT w.id, w.source, w.started_at_utc, w.local_date, w.type, w.duration_sec,
               w.distance_m, w.avg_hr, w.calories, w.rpe, w.session_load, w.notes,
               d.max_hr, d.elevation_gain_m
          FROM workouts AS w
          LEFT JOIN garmin_activity_details AS d ON d.workout_id = w.id
         WHERE w.superseded_by IS NULL
           AND w.local_date BETWEEN ? AND ?
         ORDER BY w.local_date, w.started_at_utc
        """,
        (start, end),
    ).fetchall()
    pending_review_dates = {
        row["planned_date"]
        for row in conn.execute(
            """
            SELECT p.planned_date
              FROM session_reviews r
              JOIN planned_sessions p ON p.id = r.planned_session_id
             WHERE r.status = 'pending'
               AND p.planned_date BETWEEN ? AND ?
            """,
            (start, end),
        ).fetchall()
    }

    planned_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in planned_rows:
        session = _planned_session(row)
        planned_by_date.setdefault(session["date"], []).append(session)
    workouts_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in workouts_rows:
        workout = _workout(row)
        workouts_by_date.setdefault(workout["local_date"], []).append(workout)

    days: list[dict[str, Any]] = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        local_date = day.isoformat()
        planned = planned_by_date.get(local_date, [])
        workouts = workouts_by_date.get(local_date, [])
        if local_date in pending_review_dates:
            status = "review"
        elif workouts or any(item["status"] == "completed" for item in planned):
            status = "completed"
        elif any(item["status"] in {"planned", "modified"} for item in planned):
            status = "planned"
        elif planned:
            status = "rest"
        else:
            status = "empty"
        days.append({
            "date": local_date,
            "weekday": day.weekday(),
            "status": status,
            "planned_sessions": planned,
            "workouts": workouts,
        })

    return {
        "start": start,
        "end": end,
        "block_context": _block_context(conn, week_start=week_start, week_end=week_end),
        "days": days,
        "completed_days": sum(day["status"] == "completed" for day in days),
        "training_days": sum(bool(day["workouts"]) for day in days),
        "planned_sessions": len(planned_rows),
        "workout_count": len(workouts_rows),
        "total_duration_sec": sum(row["duration_sec"] or 0 for row in workouts_rows),
        "total_distance_m": sum(row["distance_m"] or 0 for row in workouts_rows),
        "pending_reviews": len(pending_review_dates),
    }


def build_day_log(conn: sqlite3.Connection, local_day: date) -> dict[str, Any]:
    """Bygg en eksplisitt kilde-merket dagslogg for valgt kalenderdag."""
    day = local_day.isoformat()
    planned_rows = conn.execute(
        """
        SELECT id, planned_date, type, description, status, target_metrics, notes
          FROM planned_sessions
         WHERE planned_date = ?
         ORDER BY id
        """,
        (day,),
    ).fetchall()
    workout_rows = conn.execute(
        """
        SELECT w.id, w.source, w.started_at_utc, w.local_date, w.type, w.duration_sec,
               w.distance_m, w.avg_hr, w.calories, w.rpe, w.session_load, w.notes,
               d.max_hr, d.elevation_gain_m
          FROM workouts AS w
          LEFT JOIN garmin_activity_details AS d ON d.workout_id = w.id
         WHERE w.superseded_by IS NULL
           AND w.local_date = ?
         ORDER BY w.started_at_utc
        """,
        (day,),
    ).fetchall()
    review_rows = conn.execute(
        """
        SELECT r.status, r.coach_source, r.coach_comment, r.user_note,
               r.reviewed_at, p.description
          FROM session_reviews r
          JOIN planned_sessions p ON p.id = r.planned_session_id
         WHERE p.planned_date = ?
         ORDER BY r.id
        """,
        (day,),
    ).fetchall()
    daily = conn.execute(
        """
        SELECT resting_hr, training_readiness_score, training_readiness_level,
               acute_load, recovery_time_hours, steps, total_calories,
               active_calories, intensity_minutes_moderate,
               intensity_minutes_vigorous
          FROM garmin_daily WHERE local_date = ?
        """,
        (day,),
    ).fetchone()
    sleep = conn.execute(
        """
        SELECT duration_sec, sleep_score, sleep_score_qualifier
          FROM garmin_sleep WHERE local_date = ?
        """,
        (day,),
    ).fetchone()
    hrv = conn.execute(
        """
        SELECT last_night_avg_ms, weekly_avg_ms, status
          FROM garmin_hrv WHERE local_date = ?
        """,
        (day,),
    ).fetchone()
    weight = conn.execute(
        """
        SELECT weight_kg, fat_ratio_pct
          FROM withings_weight
         WHERE local_date = ?
         ORDER BY measured_at_utc DESC LIMIT 1
        """,
        (day,),
    ).fetchone()
    nutrition = conn.execute(
        """
        SELECT kcal, protein_g, carbs_g, fat_g, water_ml, kcal_goal,
               protein_goal_g, carbs_goal_g, fat_goal_g
          FROM yazio_daily WHERE local_date = ?
        """,
        (day,),
    ).fetchone()

    return {
        "date": day,
        "planned_sessions": [_planned_session(row) for row in planned_rows],
        "workouts": [_workout(row) for row in workout_rows],
        "automatic": {
            "garmin_daily": dict(daily) if daily else None,
            "sleep": dict(sleep) if sleep else None,
            "hrv": dict(hrv) if hrv else None,
            "weight": dict(weight) if weight else None,
            "nutrition": dict(nutrition) if nutrition else None,
        },
        "coach_reviews": [
            {
                "source": row["coach_source"],
                "status": row["status"],
                "comment": row["coach_comment"],
                "user_note": row["user_note"],
                "reviewed_at": row["reviewed_at"],
                "session_description": row["description"],
            }
            for row in review_rows
        ],
    }
