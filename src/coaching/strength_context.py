"""Kuratert, personlig styrkekontekst for coach- og Hevy-forslag.

LLM-en skal aldri måtte gjette brukerens øvelsesvalg eller progresjonsnivå fra
en generell styrkefilosofi. Denne modulen henter bare de små fakta den trenger:
lagrede preferanser, øvelsesoverstyringer og komprimerte toppsett fra Hevy-
historikken. Rå sett- og treningsdata forlater ikke databasen.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.coaching.history import known_exercises, last_top_set
from src.coaching.preferences import list_exercise_prefs, list_prefs


PROFILE_KEYS = (
    "training_priority",
    "strength_goal",
    "strength_sessions_per_week",
    "strength_session_duration_min",
    "strength_preferred_split",
    "strength_preferred_exercises",
    "strength_avoid_exercises",
    "strength_available_equipment",
    "strength_notes",
    "strength_rep_min_default",
    "strength_rep_max_default",
    "strength_increment_kg_default",
)

_DEFAULT_PROFILE = {
    "training_priority": "cardio",
    "strength_rep_min_default": "6",
    "strength_rep_max_default": "10",
    "strength_increment_kg_default": "2.5",
}


def build_strength_context(
    conn: sqlite3.Connection,
    *,
    history_days: int = 180,
    max_exercises: int = 16,
) -> dict[str, Any]:
    """Returner et lite, modellklart bilde av styrkeprofilen.

    ``initialized`` er med vilje streng: standardverdiene alene betyr ikke at
    systemet kjenner brukerens ønskede oppsett. Uten spesifikk profil eller
    faktisk historikk kan coachen fortsatt foreslå en stabil programstruktur,
    men skal ikke late som at den kjenner personlige øvelsesvalg eller vekter.
    """
    all_preferences = list_prefs(conn)
    preferences = {
        key: all_preferences[key]
        for key in PROFILE_KEYS
        if key in all_preferences
    }
    overrides = [
        {
            "exercise": item.display_name,
            "rep_min": item.rep_min,
            "rep_max": item.rep_max,
            "increment_kg": item.increment_kg,
            "exercise_type": item.exercise_type,
            "notes": item.notes,
        }
        for item in list_exercise_prefs(conn)
    ]

    history: list[dict[str, Any]] = []
    for exercise in known_exercises(conn, within_days=history_days)[:max_exercises]:
        name = exercise["exercise"]
        top_set = last_top_set(conn, name, within_days=history_days)
        history.append({
            "exercise": name,
            "sessions": exercise["sessions"],
            "last_seen": exercise["last_seen"],
            "last_top_set": top_set,
        })

    specific_preferences = {
        key: value
        for key, value in preferences.items()
        if _DEFAULT_PROFILE.get(key) != value
    }
    initialized = bool(specific_preferences or overrides or history)

    return {
        "initialized": initialized,
        "profile": preferences,
        "exercise_overrides": overrides,
        "recent_exercise_history": history,
        "program_structure": {
            "principle": "stable_template_family",
            "default_frequency_per_muscle_per_week": 2,
            "avoid_unprompted_exercise_churn": True,
            "change_exercises_only_for_reason": (
                "brukerønske, konkret utstyrshensyn, vedvarende problem eller "
                "en bevisst programmeringsbeslutning"
            ),
        },
        "generation_rules": {
            "use_profile_and_history_before_generic_heuristics": True,
            "weights_and_reps_must_follow_known_progression_when_available": True,
            "do_not_invent_personal_exercise_preferences": True,
            "when_uninitialized": (
                "You may propose a structurally coherent, stable template family "
                "when the user has stated frequency or schedule. Do not claim "
                "personal exercise preferences or start weights; mark initial "
                "loads for calibration and retain the structure across weeks."
            ),
        },
    }
