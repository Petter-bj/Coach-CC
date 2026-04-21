"""Leser og skriver coaching-preferenser fra DB.

To lag:
- `user_preferences` — globale KV (training_priority, default rep-window, ...)
- `exercise_preferences` — per-øvelse-overstyringer

`get_exercise_prefs(conn, name)` returnerer alltid en fullt utfylt rad —
hvis en per-øvelse-rad ikke finnes eller har NULL-felt, fylles det inn
fra globale defaults.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# user_preferences (global KV)
# ---------------------------------------------------------------------------


def get_pref(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM user_preferences WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_pref(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO user_preferences (key, value, updated_at)
        VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT (key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value),
    )
    conn.commit()


def list_prefs(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM user_preferences ORDER BY key"
    ).fetchall()
    return {r["key"]: r["value"] for r in rows}


# Globale defaults — kalles av get_exercise_prefs ved fallback
def _global_rep_min(conn: sqlite3.Connection) -> int:
    v = get_pref(conn, "strength_rep_min_default")
    return int(v) if v else 6


def _global_rep_max(conn: sqlite3.Connection) -> int:
    v = get_pref(conn, "strength_rep_max_default")
    return int(v) if v else 10


def _global_increment(conn: sqlite3.Connection) -> float:
    v = get_pref(conn, "strength_increment_kg_default")
    return float(v) if v else 2.5


def training_priority(conn: sqlite3.Connection) -> str:
    """Hent training_priority — 'cardio' | 'strength' | 'balanced'."""
    return get_pref(conn, "training_priority") or "cardio"


# ---------------------------------------------------------------------------
# exercise_preferences (per øvelse)
# ---------------------------------------------------------------------------


@dataclass
class ExercisePrefs:
    exercise_lower: str
    display_name: str
    rep_min: int
    rep_max: int
    increment_kg: float
    exercise_type: str | None  # 'compound' | 'isolation' | None
    notes: str | None
    is_default: bool  # True hvis ingen per-øvelse-rad fantes (alt fra defaults)


def get_exercise_prefs(
    conn: sqlite3.Connection, exercise_name: str
) -> ExercisePrefs:
    """Hent prefs for en øvelse, med fallback til globale defaults for NULL-felt."""
    key = exercise_name.lower()
    row = conn.execute(
        """
        SELECT exercise_lower, display_name, rep_min, rep_max,
               increment_kg, exercise_type, notes
          FROM exercise_preferences
         WHERE exercise_lower = ?
        """,
        (key,),
    ).fetchone()

    g_min = _global_rep_min(conn)
    g_max = _global_rep_max(conn)
    g_inc = _global_increment(conn)

    if row is None:
        return ExercisePrefs(
            exercise_lower=key,
            display_name=exercise_name,
            rep_min=g_min,
            rep_max=g_max,
            increment_kg=g_inc,
            exercise_type=None,
            notes=None,
            is_default=True,
        )

    return ExercisePrefs(
        exercise_lower=row["exercise_lower"],
        display_name=row["display_name"],
        rep_min=row["rep_min"] if row["rep_min"] is not None else g_min,
        rep_max=row["rep_max"] if row["rep_max"] is not None else g_max,
        increment_kg=row["increment_kg"] if row["increment_kg"] is not None else g_inc,
        exercise_type=row["exercise_type"],
        notes=row["notes"],
        is_default=False,
    )


def set_exercise_prefs(
    conn: sqlite3.Connection,
    exercise_name: str,
    *,
    rep_min: int | None = None,
    rep_max: int | None = None,
    increment_kg: float | None = None,
    exercise_type: str | None = None,
    notes: str | None = None,
) -> None:
    """Upsert per-øvelse-prefs. NULL-felt beholdes hvis raden finnes."""
    key = exercise_name.lower()
    existing = conn.execute(
        "SELECT 1 FROM exercise_preferences WHERE exercise_lower = ?", (key,)
    ).fetchone()

    if existing:
        # Kun oppdater felt som er eksplisitt satt (ikke None)
        updates: list[str] = []
        params: list[Any] = []
        if rep_min is not None:
            updates.append("rep_min = ?")
            params.append(rep_min)
        if rep_max is not None:
            updates.append("rep_max = ?")
            params.append(rep_max)
        if increment_kg is not None:
            updates.append("increment_kg = ?")
            params.append(increment_kg)
        if exercise_type is not None:
            updates.append("exercise_type = ?")
            params.append(exercise_type)
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)
        updates.append("display_name = ?")
        params.append(exercise_name)
        updates.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
        params.append(key)
        conn.execute(
            f"UPDATE exercise_preferences SET {', '.join(updates)} WHERE exercise_lower = ?",
            params,
        )
    else:
        conn.execute(
            """
            INSERT INTO exercise_preferences
                (exercise_lower, display_name, rep_min, rep_max,
                 increment_kg, exercise_type, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (key, exercise_name, rep_min, rep_max, increment_kg,
             exercise_type, notes),
        )
    conn.commit()


def list_exercise_prefs(conn: sqlite3.Connection) -> list[ExercisePrefs]:
    """Hent alle øvelser som har egen override (ikke de som bruker default)."""
    rows = conn.execute(
        """
        SELECT exercise_lower, display_name, rep_min, rep_max,
               increment_kg, exercise_type, notes
          FROM exercise_preferences
         ORDER BY display_name
        """
    ).fetchall()
    g_min = _global_rep_min(conn)
    g_max = _global_rep_max(conn)
    g_inc = _global_increment(conn)
    return [
        ExercisePrefs(
            exercise_lower=r["exercise_lower"],
            display_name=r["display_name"],
            rep_min=r["rep_min"] if r["rep_min"] is not None else g_min,
            rep_max=r["rep_max"] if r["rep_max"] is not None else g_max,
            increment_kg=r["increment_kg"] if r["increment_kg"] is not None else g_inc,
            exercise_type=r["exercise_type"],
            notes=r["notes"],
            is_default=False,
        )
        for r in rows
    ]
