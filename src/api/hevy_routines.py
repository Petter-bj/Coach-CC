"""Validerte og bekreftbare forslag for Hevy-rutiner.

En Hevy-rutine er det brukeren kaller en mal. Modellen kan beskrive en
kandidat, men aldri kalle Hevy direkte: kandidaten lagres først og blir sendt
først etter et eget, synlig brukerklikk.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from typing import Any


MAX_EXERCISES = 12
MAX_SETS_PER_EXERCISE = 10
MAX_ROUTINES_PER_REPLY = 4
VALID_SET_TYPES = {"normal", "warmup"}

# Norske ukedagsnavn → offset fra mandag. Modellen får lov å foreslå en ukedag
# i stedet for en dato; da løser vi den til den faktiske datoen i valgt uke.
_WEEKDAY_OFFSETS = {
    "mandag": 0,
    "tirsdag": 1,
    "onsdag": 2,
    "torsdag": 3,
    "fredag": 4,
    "lørdag": 5,
    "lordag": 5,
    "søndag": 6,
    "sondag": 6,
}


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


def _week_bounds(week_start_iso: str) -> tuple[date, date]:
    start = date.fromisoformat(week_start_iso)
    monday = start - timedelta(days=start.weekday())
    return monday, monday + timedelta(days=6)


def _resolve_week_date(raw: dict[str, Any], *, week_start_iso: str) -> str | None:
    """Løs et forslags dag til en ISO-dato innenfor valgt uke.

    Godtar enten en eksplisitt ISO-dato (``date``/``suggested_date``) eller et
    norsk ukedagsnavn (``weekday``/``day``). Returnerer None hvis dagen mangler
    eller faller utenfor uken — da vises malen uten dato-merke.
    """
    monday, sunday = _week_bounds(week_start_iso)

    explicit = raw.get("date") or raw.get("suggested_date")
    if isinstance(explicit, str):
        try:
            parsed = date.fromisoformat(explicit.strip())
        except ValueError:
            parsed = None
        if parsed is not None and monday <= parsed <= sunday:
            return parsed.isoformat()

    weekday = raw.get("weekday") or raw.get("day")
    if isinstance(weekday, str):
        offset = _WEEKDAY_OFFSETS.get(weekday.strip().casefold())
        if offset is not None:
            return (monday + timedelta(days=offset)).isoformat()

    return None


def validate_hevy_routines(
    raw: Any,
    *,
    week_start: str,
) -> list[dict[str, Any]]:
    """Normaliser en liste med Hevy-mal-forslag for én uke.

    Hvert element blir ``{"routine": {...}, "suggested_date": ..., "purpose": ...}``.
    ``routine`` inneholder bare det Hevy trenger (title/notes/exercises); dato og
    hensikt er dashboard-metadata. Ugyldige elementer forkastes stille, slik at et
    delvis gyldig svar fortsatt gir de gyldige malene. Tar imot både den nye
    liste-formen og en enkelt rutine (bakoverkompatibelt).
    """
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    validated: list[dict[str, Any]] = []
    for item in raw[:MAX_ROUTINES_PER_REPLY]:
        if not isinstance(item, dict):
            continue
        # ``name`` godtas som alias for ``title`` slik at modellen kan bruke det
        # mer naturlige feltnavnet for en mal.
        core_raw = dict(item)
        if "title" not in core_raw and isinstance(core_raw.get("name"), str):
            core_raw["title"] = core_raw["name"]
        routine = validate_hevy_routine(core_raw)
        if routine is None:
            continue
        purpose = _text(item.get("purpose") or item.get("role"), maximum=120) or None
        suggested_date = _resolve_week_date(item, week_start_iso=week_start)
        validated.append({
            "routine": routine,
            "suggested_date": suggested_date,
            "purpose": purpose,
        })
    return validated


def _weekday_no(iso_date: str | None) -> str | None:
    if not iso_date:
        return None
    try:
        weekday = date.fromisoformat(iso_date).weekday()
    except ValueError:
        return None
    return (
        "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"
    )[weekday]


def _proposal_public(row_or_dict: dict[str, Any]) -> dict[str, Any]:
    """Ett Hevy-forslag i den formen dashboardet forventer."""
    suggested_date = row_or_dict.get("suggested_date")
    return {
        "id": row_or_dict["id"],
        "week_start": row_or_dict["week_start"],
        "routine": row_or_dict["routine"],
        "suggested_date": suggested_date,
        "weekday": _weekday_no(suggested_date),
        "purpose": row_or_dict.get("purpose"),
        "status": row_or_dict.get("status", "pending"),
    }


def create_hevy_routine_proposal(
    conn: sqlite3.Connection,
    *,
    week_start: str,
    question: str,
    coach_answer: str,
    routine: dict[str, Any],
    suggested_date: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO hevy_routine_proposals
            (week_start, question, coach_answer, routine_json, suggested_date, purpose)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            week_start,
            question,
            coach_answer,
            json.dumps(routine, ensure_ascii=False),
            suggested_date,
            purpose,
        ),
    )
    return _proposal_public({
        "id": cursor.lastrowid,
        "week_start": week_start,
        "routine": routine,
        "suggested_date": suggested_date,
        "purpose": purpose,
        "status": "pending",
    })


def pending_hevy_routine_proposal(
    conn: sqlite3.Connection,
    proposal_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, week_start, routine_json, suggested_date, purpose, status
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
    return {
        "id": row["id"],
        "week_start": row["week_start"],
        "routine": routine,
        "suggested_date": row["suggested_date"],
        "purpose": row["purpose"],
    }


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
