"""Validerte og bekreftbare forslag for Hevy-rutiner.

En Hevy-rutine er det brukeren kaller en mal. Modellen kan beskrive en
kandidat, men aldri kalle Hevy direkte: kandidaten lagres først og blir sendt
først etter et eget, synlig brukerklikk.
"""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any


MAX_EXERCISES = 12
MAX_SETS_PER_EXERCISE = 10
VALID_SET_TYPES = {"normal", "warmup"}


def _text(value: Any, *, maximum: int, required: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean:
        return None if required else ""
    return clean[:maximum]


def _number(value: Any, *, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        return None
    return number


def validate_hevy_routine(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normaliser kun en liten, styrkeorientert Hevy-rutine-kandidat."""
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title"), maximum=120, required=True)
    notes = _text(raw.get("notes"), maximum=1_000) or None
    raw_exercises = raw.get("exercises")
    if not title or not isinstance(raw_exercises, list) or not 1 <= len(raw_exercises) <= MAX_EXERCISES:
        return None

    exercises: list[dict[str, Any]] = []
    for raw_exercise in raw_exercises:
        if not isinstance(raw_exercise, dict):
            return None
        exercise = _text(raw_exercise.get("exercise"), maximum=120, required=True)
        note = _text(raw_exercise.get("notes"), maximum=500) or None
        rest = _number(raw_exercise.get("rest_seconds"), minimum=0, maximum=900)
        raw_sets = raw_exercise.get("sets")
        if not exercise or not isinstance(raw_sets, list) or not 1 <= len(raw_sets) <= MAX_SETS_PER_EXERCISE:
            return None
        sets: list[dict[str, Any]] = []
        for raw_set in raw_sets:
            if not isinstance(raw_set, dict):
                return None
            set_type = raw_set.get("type", "normal")
            reps = _number(raw_set.get("reps"), minimum=1, maximum=100)
            weight = _number(raw_set.get("weight_kg"), minimum=0, maximum=1_000)
            if set_type not in VALID_SET_TYPES or reps is None:
                return None
            sets.append({
                "type": set_type,
                "reps": int(reps),
                **({"weight_kg": weight} if weight is not None else {}),
            })
        exercises.append({
            "exercise": exercise,
            "sets": sets,
            **({"rest_seconds": int(rest)} if rest is not None else {}),
            **({"notes": note} if note else {}),
        })
    return {"title": title, "notes": notes, "exercises": exercises}


def create_hevy_routine_proposal(
    conn: sqlite3.Connection,
    *,
    week_start: str,
    question: str,
    coach_answer: str,
    routine: dict[str, Any],
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO hevy_routine_proposals (week_start, question, coach_answer, routine_json)
        VALUES (?, ?, ?, ?)
        """,
        (week_start, question, coach_answer, json.dumps(routine, ensure_ascii=False)),
    )
    return {
        "id": cursor.lastrowid,
        "week_start": week_start,
        "routine": routine,
        "status": "pending",
    }


def pending_hevy_routine_proposal(
    conn: sqlite3.Connection,
    proposal_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, week_start, routine_json, status
          FROM hevy_routine_proposals
         WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    if row is None or row["status"] != "pending":
        return None
    try:
        routine = json.loads(row["routine_json"])
    except json.JSONDecodeError:
        return None
    if not isinstance(routine, dict):
        return None
    return {"id": row["id"], "week_start": row["week_start"], "routine": routine}


def mark_hevy_routine_proposal_applied(
    conn: sqlite3.Connection,
    proposal_id: int,
    *,
    hevy_routine_id: str,
) -> bool:
    cursor = conn.execute(
        """
        UPDATE hevy_routine_proposals
           SET status = 'applied', hevy_routine_id = ?,
               applied_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
         WHERE id = ? AND status = 'pending'
        """,
        (hevy_routine_id, proposal_id),
    )
    return cursor.rowcount == 1


def discard_hevy_routine_proposal(conn: sqlite3.Connection, proposal_id: int) -> bool:
    cursor = conn.execute(
        """
        UPDATE hevy_routine_proposals
           SET status = 'discarded'
         WHERE id = ? AND status = 'pending'
        """,
        (proposal_id,),
    )
    return cursor.rowcount == 1
