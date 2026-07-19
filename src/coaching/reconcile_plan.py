"""Reconcile planned_sessions med faktiske workouts.

Når brukeren melder "ferdig med Thu Z3" oppdaterer boten markdown, men ikke
alltid `planned_sessions.status` i DB. Det betyr at proposer-en (som leser
adherence fra DB) tror du ikke har fullført noe.

Denne modulen kjører etter hver sync: matcher `planned_sessions` med
`workouts` på dato + type-familie, setter status='completed' og fyller
`workout_id`. Den etterfyller også `workout_id` for eldre, manuelt markerte
completed-økter, slik at de kan få en automatisk review senere.

Kjøres automatisk i `sync.py` etter baselines. Også eksponert som CLI:
    src.cli.plan reconcile [--since YYYY-MM-DD] [--apply]
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta


# Plan-type → workouts.type-familier som teller som "fullført denne typen"
# Vi matcher inkluderende: hvis plan-typen *inneholder* en kjent substring,
# bruker vi tilhørende workout-type-familie. Dette håndterer både proposer-
# generated typer (easy_run, lower) og historiske/varierende navn
# (long_run_easy, run_z3, upper_2_pull_plus_prehab, skierg_z3).
PLAN_TYPE_MATCHERS: list[tuple[str, list[str]]] = [
    # Mer spesifikke først. SkiErg-økt kan registreres som indoor_rowing
    # (Garmin auto-detected) eller skierg (Concept2 kanonisk). Vi unngår
    # fitness_equipment her fordi det er for ambigøst (kan være styrke).
    ("skierg", ["indoor_rowing", "skierg"]),
    ("erg", ["indoor_rowing", "skierg"]),
    ("run", ["running"]),
    ("løp", ["running"]),
    ("upper", ["strength_training"]),
    ("lower", ["strength_training"]),
    ("legs", ["strength_training"]),
    ("push", ["strength_training"]),
    ("pull", ["strength_training"]),
    ("strength", ["strength_training"]),
    ("styrke", ["strength_training"]),
    ("squat", ["strength_training"]),
    ("bench", ["strength_training"]),
]


def _match_workout_types(plan_type: str) -> list[str] | None:
    """Returner liste workout.type-verdier som matcher plan-type-strengen."""
    if not plan_type:
        return None
    pt = plan_type.lower()
    matched: set[str] = set()
    for substr, workout_types in PLAN_TYPE_MATCHERS:
        if substr in pt:
            matched.update(workout_types)
    return sorted(matched) if matched else None


@dataclass
class ReconcileResult:
    matched: int = 0
    unmatched: int = 0
    no_type_map: int = 0
    already_completed: int = 0
    backfilled: int = 0
    rows_examined: int = 0
    matches: list[tuple[int, int, str]] | None = None  # (plan_id, workout_id, plan_type)


def reconcile_planned_sessions(
    conn: sqlite3.Connection,
    since_days_ago: int = 30,
    apply: bool = True,
) -> ReconcileResult:
    """Match planned_sessions med workouts på samme dato + type-familie.

    Args:
        since_days_ago: ignorer planned_sessions eldre enn dette
        apply: hvis False = dry-run (returner matches uten å skrive)
    """
    cutoff = (date.today() - timedelta(days=since_days_ago)).isoformat()

    rows = conn.execute(
        """
        SELECT id, planned_date, type, status, workout_id
          FROM planned_sessions
         WHERE planned_date >= ?
           AND planned_date <= date('now')
         ORDER BY planned_date
        """,
        (cutoff,),
    ).fetchall()

    result = ReconcileResult(matches=[])

    for p in rows:
        result.rows_examined += 1
        # En allerede fullført økt med workout_id er ferdig behandlet. Eldre
        # manuelt markerte completed-rader kan derimot mangle koblingen;
        # la dem gå gjennom matchen for å etterfylle den uten å endre status.
        if p["status"] == "completed" and p["workout_id"] is not None:
            result.already_completed += 1
            continue

        # Hopp over hvis status er manuelt satt til skipped/modified
        if p["status"] in ("skipped",):
            continue

        plan_type = (p["type"] or "").lower()
        match_types = _match_workout_types(plan_type)
        if not match_types:
            result.no_type_map += 1
            continue

        placeholders = ",".join("?" * len(match_types))
        workout = conn.execute(
            f"""
            SELECT id, type
              FROM workouts
             WHERE local_date = ?
               AND type IN ({placeholders})
               AND superseded_by IS NULL
             ORDER BY started_at_utc
             LIMIT 1
            """,
            (p["planned_date"], *match_types),
        ).fetchone()

        if workout is None:
            result.unmatched += 1
            continue

        result.matches.append((p["id"], workout["id"], plan_type))
        result.matched += 1
        if p["status"] == "completed":
            result.backfilled += 1

    if apply and result.matches:
        for plan_id, workout_id, _type in result.matches:
            conn.execute(
                """
                UPDATE planned_sessions
                   SET status = 'completed', workout_id = ?
                 WHERE id = ?
                """,
                (workout_id, plan_id),
            )
        conn.commit()

    return result
