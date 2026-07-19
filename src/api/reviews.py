"""Skriveoperasjoner for den smale review-flyten."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _target_metrics(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def confirm_review(
    conn: sqlite3.Connection,
    *,
    review_id: int,
    note: str | None,
) -> dict[str, str | int | None] | None:
    """Marker én pending review som vurdert, eventuelt med brukerens notat.

    Returnerer ``None`` hvis reviewen ikke finnes eller allerede er vurdert.
    Det gjør handlingen trygg mot dobbeltklikk og utdaterte dashboard-faner.
    """
    cleaned_note = note.strip() if note else None
    if cleaned_note == "":
        cleaned_note = None
    cursor = conn.execute(
        """
        UPDATE session_reviews
           SET status = 'reviewed',
               user_note = COALESCE(?, user_note),
               reviewed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
         WHERE id = ?
           AND status = 'pending'
        """,
        (cleaned_note, review_id),
    )
    if cursor.rowcount != 1:
        return None
    stored_note = conn.execute(
        "SELECT user_note FROM session_reviews WHERE id = ?", (review_id,)
    ).fetchone()["user_note"]
    return {
        "id": review_id,
        "status": "reviewed",
        "user_note": stored_note,
    }


def pending_review_context(
    conn: sqlite3.Connection,
    *,
    review_id: int,
) -> dict[str, Any] | None:
    """Hent én pending review i et format som er trygt å gi coach-laget.

    Dette er bevisst plan-vs-faktisk-aggregater, ikke FIT-samples, GPS eller
    rå Garmin-data. ID-en brukes bare av API-et og sendes aldri til modellen.
    """
    row = conn.execute(
        """
        SELECT r.coach_comment,
               p.planned_date, p.type, p.description, p.target_metrics,
               w.source AS workout_source, w.duration_sec, w.avg_hr,
               w.distance_m
          FROM session_reviews r
          JOIN planned_sessions p ON p.id = r.planned_session_id
          JOIN workouts w ON w.id = r.workout_id
         WHERE r.id = ?
           AND r.status = 'pending'
        """,
        (review_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "planned_session": {
            "date": row["planned_date"],
            "type": row["type"],
            "description": row["description"],
            "target_metrics": _target_metrics(row["target_metrics"]),
        },
        "actual": {
            "source": row["workout_source"],
            "duration_sec": row["duration_sec"],
            "avg_hr": row["avg_hr"],
            "distance_m": row["distance_m"],
        },
        "previous_coach_comment": row["coach_comment"],
    }


def save_reconsidered_review(
    conn: sqlite3.Connection,
    *,
    review_id: int,
    note: str,
    coach_comment: str,
) -> dict[str, str | int] | None:
    """Lagre brukerens avvik og ny coach-vurdering uten å bekrefte økten.

    Det er nøkkelen i review-flyten: kortet er fremdeles gult, og brukeren
    må fortsatt ta den eksplisitte bekreftelsen som gjør det grønt.
    """
    cursor = conn.execute(
        """
        UPDATE session_reviews
           SET user_note = ?,
               coach_source = 'agent',
               coach_comment = ?
         WHERE id = ?
           AND status = 'pending'
        """,
        (note, coach_comment, review_id),
    )
    if cursor.rowcount != 1:
        return None
    return {
        "id": review_id,
        "status": "pending",
        "user_note": note,
        "coach_source": "agent",
        "coach_comment": coach_comment,
    }
