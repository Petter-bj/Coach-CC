"""Automatiske, men brukerbekreftede øktvurderinger.

En gjennomført planlagt økt er ikke nødvendigvis "tolket". Garmin-matchen
setter ``planned_sessions.status='completed'``; denne modulen oppretter så én
pending review som brukeren kan bekrefte eller legge til et avvik på.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any


def _target_metrics(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _format_duration(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    minutes = round(float(seconds) / 60)
    return f"{minutes} min"


def _format_distance(meters: float | None) -> str | None:
    if meters is None:
        return None
    return f"{meters / 1000:.1f} km".replace(".", ",")


def build_review_comment(
    *,
    target_metrics: dict[str, Any],
    duration_sec: int | float | None,
    avg_hr: int | None,
    distance_m: float | None,
) -> str:
    """Lag en nøktern, deterministisk coach-kommentar fra registrerte tall."""
    observations: list[str] = []
    planned_duration = target_metrics.get("duration_min")
    actual_duration = _format_duration(duration_sec)

    if actual_duration and isinstance(planned_duration, (int, float)):
        difference = round(float(duration_sec) / 60 - planned_duration)
        if abs(difference) <= max(3, planned_duration * 0.1):
            observations.append(
                f"Varigheten var {actual_duration} mot planlagte {planned_duration:g} min, godt innenfor planen."
            )
        elif difference < 0:
            observations.append(
                f"Varigheten ble {actual_duration} mot planlagte {planned_duration:g} min."
            )
        else:
            observations.append(
                f"Varigheten ble {actual_duration}, litt over planlagte {planned_duration:g} min."
            )
    elif actual_duration:
        observations.append(f"Garmin registrerte {actual_duration}.")

    if avg_hr is not None:
        observations.append(f"Snittpulsen var {avg_hr} bpm.")
    if distance_m is not None:
        observations.append(f"Distanse: {_format_distance(distance_m)}.")

    if not observations:
        observations.append("Økten er registrert og lagt inn i belastningsbildet.")

    return " ".join(observations) + " Sjekk gjerne om noe avviker fra opplevelsen din."


def ensure_pending_reviews(
    conn: sqlite3.Connection,
    *,
    since_days_ago: int = 30,
) -> int:
    """Opprett manglende pending reviews for automatisk fullførte økter.

    Funksjonen er idempotent: den unike nøkkelen på ``planned_session_id``
    sikrer at en ny sync aldri lager en ny vurderingsrunde for samme økt.
    """
    cutoff = (date.today() - timedelta(days=since_days_ago)).isoformat()
    rows = conn.execute(
        """
        SELECT p.id AS planned_session_id, p.target_metrics,
               w.id AS workout_id, w.duration_sec, w.avg_hr, w.distance_m
          FROM planned_sessions p
          JOIN workouts w ON w.id = p.workout_id
         WHERE p.status = 'completed'
           AND p.workout_id IS NOT NULL
           AND p.planned_date >= ?
           AND NOT EXISTS (
               SELECT 1
                 FROM session_reviews r
                WHERE r.planned_session_id = p.id
           )
         ORDER BY p.planned_date, p.id
        """,
        (cutoff,),
    ).fetchall()

    for row in rows:
        comment = build_review_comment(
            target_metrics=_target_metrics(row["target_metrics"]),
            duration_sec=row["duration_sec"],
            avg_hr=row["avg_hr"],
            distance_m=row["distance_m"],
        )
        conn.execute(
            """
            INSERT INTO session_reviews
                (planned_session_id, workout_id, coach_comment)
            VALUES (?, ?, ?)
            """,
            (row["planned_session_id"], row["workout_id"], comment),
        )

    return len(rows)
