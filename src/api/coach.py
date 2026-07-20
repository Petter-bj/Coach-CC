"""Kuraterer den minimale treningskonteksten en ekstern coach-modell får."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from src.api.today import build_today_payload
from src.coaching.knowledge import select_knowledge, topic_flags_from_text


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def build_coach_context(
    conn: sqlite3.Connection,
    target_date: date | None = None,
    *,
    question: str = "",
) -> dict[str, Any]:
    """Lag et begrenset, JSON-serialiserbart bilde av den personlige konteksten.

    Rå FIT-samples, GPS-posisjoner, kontoinformasjon, konsumert-mat-detaljer
    og database-ID-er er bevisst utelatt. Modellen trenger ikke disse dataene
    for å føre en treningssamtale.
    """
    target_date = target_date or date.today()
    today = build_today_payload(conn, target_date)
    recent_start = (target_date - timedelta(days=27)).isoformat()
    as_of = target_date.isoformat()

    recent_workouts = _rows(
        conn,
        """
        SELECT local_date, type, duration_sec, distance_m, avg_hr, rpe, session_load
          FROM workouts
         WHERE superseded_by IS NULL AND local_date BETWEEN ? AND ?
         ORDER BY local_date DESC, started_at_utc DESC
         LIMIT 20
        """,
        (recent_start, as_of),
    )
    goals = _rows(
        conn,
        """
        SELECT title, target_date, metric, target_value, priority, notes
          FROM goals
         WHERE status = 'active'
         ORDER BY priority, target_date
        """,
    )
    injuries = _rows(
        conn,
        """
        SELECT id, body_part, severity, started_at, status, notes
          FROM injuries
         WHERE status IN ('active', 'healing')
         ORDER BY severity DESC, started_at DESC
        """,
    )
    life_context = _rows(
        conn,
        """
        SELECT category, starts_on, ends_on, notes
          FROM context_log
         WHERE starts_on <= ? AND (ends_on IS NULL OR ends_on >= ?)
         ORDER BY starts_on DESC
        """,
        (as_of, as_of),
    )
    preferences = {
        row["key"]: row["value"]
        for row in conn.execute(
            """
            SELECT key, value FROM user_preferences
             WHERE key IN ('training_priority', 'hr_max', 'hr_max_garmin',
                           'hr_lactate_threshold', 'hr_lactate_threshold_garmin',
                           'weight_kg', 'weight_kg_garmin')
             ORDER BY key
            """
        ).fetchall()
    }
    nutrition = conn.execute(
        """
        SELECT local_date, kcal, protein_g, carbs_g, fat_g, water_ml,
               kcal_goal, protein_goal_g, carbs_goal_g, fat_goal_g
          FROM yazio_daily
         WHERE local_date <= ?
         ORDER BY local_date DESC
         LIMIT 1
        """,
        (as_of,),
    ).fetchone()
    weight = conn.execute(
        """
        SELECT local_date, weight_kg, fat_ratio_pct
          FROM withings_weight
         WHERE local_date <= ?
         ORDER BY local_date DESC, measured_at_utc DESC
         LIMIT 1
        """,
        (as_of,),
    ).fetchone()

    # Coaching-kjerne + relevante temamoduler. På dag-flaten er styrke/løping/
    # skade avledet fra spørsmålet; planlegging hører til uke-/blokk-flatene.
    include_strength, include_running = topic_flags_from_text(
        question,
        " ".join(str(w.get("type") or "") for w in recent_workouts),
    )
    coaching_policy = select_knowledge(
        surface="today",
        include_strength=include_strength,
        include_running=include_running or bool(injuries),
    )

    return {
        "date": today["date"],
        "today": {
            "recommendation": today["recommendation"],
            "planned_sessions": today["planned_sessions"],
            "metrics": today["metrics"],
            "week": today["week"],
            "pending_reviews": today["reviews"],
        },
        "goals": goals,
        "preferences": preferences,
        "active_injuries": injuries,
        "active_life_context": life_context,
        "recent_workouts": recent_workouts,
        "latest_nutrition_summary": dict(nutrition) if nutrition else None,
        "latest_weight_summary": dict(weight) if weight else None,
        "coaching_policy": coaching_policy,
    }
