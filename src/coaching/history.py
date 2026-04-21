"""Hjelpere for å finne historisk context for en øvelse.

Primært: "siste topp-sett" definert som det tyngste settet med flest reps
fra siste økta som inneholdt øvelsen. Brukes av progression-CLI.
"""

from __future__ import annotations

import sqlite3


def last_top_set(
    conn: sqlite3.Connection,
    exercise_name: str,
    within_days: int = 90,
) -> dict | None:
    """Finn topp-settet fra siste økta som inneholdt denne øvelsen.

    Args:
        conn: DB-connection
        exercise_name: øvelsesnavn (case-insensitive match)
        within_days: ignorer historikk eldre enn N dager

    Returns:
        {"reps": N, "weight_kg": X, "rpe": Y, "e1rm_kg": Z, "started_at_utc": T,
         "workout_id": W, "local_date": D} eller None hvis ingen historikk.
    """
    # Finn siste workout_id som har denne øvelsen
    row = conn.execute(
        """
        SELECT w.id AS workout_id, w.started_at_utc, w.local_date, w.source
          FROM strength_sets ss
          JOIN strength_sessions sess ON ss.session_id = sess.id
          JOIN workouts w ON sess.workout_id = w.id
         WHERE LOWER(ss.exercise) = LOWER(?)
           AND w.superseded_by IS NULL
           AND date(w.started_at_utc) >= date('now', ?)
         ORDER BY w.started_at_utc DESC
         LIMIT 1
        """,
        (exercise_name, f"-{within_days} days"),
    ).fetchone()

    if row is None:
        return None

    # Topp-sett definert som: max(weight_kg), tie-break max(reps)
    # Bodyweight (weight_kg IS NULL) scorer lavere enn ethvert vektet sett
    top = conn.execute(
        """
        SELECT reps, weight_kg, rpe, e1rm_kg
          FROM strength_sets ss
          JOIN strength_sessions sess ON ss.session_id = sess.id
         WHERE sess.workout_id = ?
           AND LOWER(ss.exercise) = LOWER(?)
         ORDER BY COALESCE(weight_kg, 0) DESC, reps DESC
         LIMIT 1
        """,
        (row["workout_id"], exercise_name),
    ).fetchone()

    if top is None:
        return None

    return {
        "reps": top["reps"],
        "weight_kg": top["weight_kg"],
        "rpe": top["rpe"],
        "e1rm_kg": top["e1rm_kg"],
        "started_at_utc": row["started_at_utc"],
        "local_date": row["local_date"],
        "workout_id": row["workout_id"],
        "source": row["source"],
    }


def exercise_sessions_count(
    conn: sqlite3.Connection, exercise_name: str, within_days: int = 90
) -> int:
    """Antall ulike økter som har hatt denne øvelsen (for å sjekke om nok data)."""
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT sess.workout_id) AS n
          FROM strength_sets ss
          JOIN strength_sessions sess ON ss.session_id = sess.id
          JOIN workouts w ON sess.workout_id = w.id
         WHERE LOWER(ss.exercise) = LOWER(?)
           AND w.superseded_by IS NULL
           AND date(w.started_at_utc) >= date('now', ?)
        """,
        (exercise_name, f"-{within_days} days"),
    ).fetchone()
    return row["n"] if row else 0


def known_exercises(conn: sqlite3.Connection, within_days: int = 180) -> list[dict]:
    """Hent alle øvelser vi har historikk for, med siste dato og antall økter."""
    rows = conn.execute(
        """
        SELECT ss.exercise,
               COUNT(DISTINCT sess.workout_id) AS sessions,
               MAX(w.local_date) AS last_seen
          FROM strength_sets ss
          JOIN strength_sessions sess ON ss.session_id = sess.id
          JOIN workouts w ON sess.workout_id = w.id
         WHERE w.superseded_by IS NULL
           AND date(w.started_at_utc) >= date('now', ?)
         GROUP BY ss.exercise
         ORDER BY last_seen DESC, sessions DESC
        """,
        (f"-{within_days} days",),
    ).fetchall()
    return [dict(r) for r in rows]
