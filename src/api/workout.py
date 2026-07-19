"""Lesekontrakt for én planlagt eller gjennomført treningsøkt.

Detaljvisningen sender bare ut avledede sammendrag. FIT-samples, GPS-posisjon
og leverandørenes rå-JSON blir liggende i det private datalaget.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _json_object(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _hevy_session_name(notes: str | None) -> str | None:
    """Hent den korte Hevy-tittelen fra det normaliserte øktnotatet.

    Hevy sender tittelen sammen med eventuell beskrivelse. Sync-laget beholder
    begge deler i ``workouts.notes`` for bakoverkompatibilitet, mens API-et
    eksponerer tittelen separat til detaljarket.
    """
    if not notes or not notes.startswith("Økt: "):
        return None
    return notes.removeprefix("Økt: ").split(" — ", 1)[0].strip() or None


def build_workout_detail(
    conn: sqlite3.Connection, workout_id: int,
) -> dict[str, Any] | None:
    """Returner én økts sikre, kompakte detaljsammendrag."""
    row = conn.execute(
        """
        SELECT w.id, w.source, w.started_at_utc, w.local_date, w.type,
               w.duration_sec, w.distance_m, w.avg_hr, w.calories, w.rpe,
               w.session_load, w.notes,
               g.activity_name, g.moving_duration_sec, g.elevation_gain_m,
               g.avg_speed_m_per_sec, g.max_speed_m_per_sec, g.max_hr,
               c.avg_pace_500m_sec, c.avg_watts, c.avg_stroke_rate,
               c.workout_type
          FROM workouts AS w
          LEFT JOIN garmin_activity_details AS g ON g.workout_id = w.id
          LEFT JOIN concept2_session_details AS c ON c.workout_id = w.id
         WHERE w.id = ? AND w.superseded_by IS NULL
        """,
        (workout_id,),
    ).fetchone()
    if row is None:
        return None

    sample_summary = conn.execute(
        """
        SELECT AVG(pace_sec_per_km) AS avg_pace_sec_per_km,
               AVG(cadence) AS avg_cadence,
               AVG(power_w) AS avg_power_w
          FROM workout_samples
         WHERE workout_id = ?
        """,
        (workout_id,),
    ).fetchone()
    plan = conn.execute(
        """
        SELECT id, planned_date, type, description, status, target_metrics, notes
          FROM planned_sessions
         WHERE workout_id = ?
         ORDER BY id DESC LIMIT 1
        """,
        (workout_id,),
    ).fetchone()
    strength = conn.execute(
        """
        SELECT COUNT(*) AS set_count, COUNT(DISTINCT ss.exercise) AS exercise_count,
               SUM(ss.reps * COALESCE(ss.weight_kg, 0)) AS volume_kg
          FROM strength_sessions AS session
          JOIN strength_sets AS ss ON ss.session_id = session.id
         WHERE session.workout_id = ?
        """,
        (workout_id,),
    ).fetchone()
    strength_rows = conn.execute(
        """
        SELECT ss.exercise, ss.set_num, ss.reps, ss.weight_kg, ss.rpe,
               ss.e1rm_kg, ss.notes
          FROM strength_sessions AS session
          JOIN strength_sets AS ss ON ss.session_id = session.id
         WHERE session.workout_id = ?
         ORDER BY ss.exercise COLLATE NOCASE, ss.set_num
        """,
        (workout_id,),
    ).fetchall()
    exercises: list[dict[str, Any]] = []
    for strength_set in strength_rows:
        exercise = strength_set["exercise"]
        if not exercises or exercises[-1]["exercise"] != exercise:
            exercises.append({"exercise": exercise, "sets": []})
        exercises[-1]["sets"].append({
            "set_num": strength_set["set_num"],
            "reps": strength_set["reps"],
            "weight_kg": strength_set["weight_kg"],
            "rpe": strength_set["rpe"],
            "e1rm_kg": strength_set["e1rm_kg"],
            "notes": strength_set["notes"],
        })

    return {
        "workout": {
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
            "rpe": row["rpe"],
            "session_load": row["session_load"],
            "notes": row["notes"],
        },
        "source_summary": {
            "activity_name": row["activity_name"],
            "moving_duration_sec": row["moving_duration_sec"],
            "elevation_gain_m": row["elevation_gain_m"],
            "avg_speed_m_per_sec": row["avg_speed_m_per_sec"],
            "max_speed_m_per_sec": row["max_speed_m_per_sec"],
            "avg_pace_500m_sec": row["avg_pace_500m_sec"],
            "avg_watts": row["avg_watts"],
            "avg_stroke_rate": row["avg_stroke_rate"],
            "workout_type": row["workout_type"],
        },
        "sample_summary": dict(sample_summary) if sample_summary else None,
        "matched_plan": (
            {
                "id": plan["id"],
                "date": plan["planned_date"],
                "type": plan["type"],
                "description": plan["description"],
                "status": plan["status"],
                "target_metrics": _json_object(plan["target_metrics"]),
                "notes": plan["notes"],
            }
            if plan else None
        ),
        "strength_summary": (
            {
                **dict(strength),
                "session_name": _hevy_session_name(row["notes"])
                if row["source"] == "hevy" else None,
                "exercises": exercises,
            }
            if strength and strength["set_count"] else None
        ),
    }
