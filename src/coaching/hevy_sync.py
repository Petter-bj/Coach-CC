"""Push foreslåtte strength-dager til Hevy som routine-oppdateringer.

Strategi:
1. Hent brukerens eksisterende Hevy-routines via `/v1/routines`
2. For hver strength-dag i forslaget (Upper 1, Upper 2, Lower):
   match by title-keywords, ikke eksakt navn
3. For hver øvelse i matchet routine: slå opp siste topp-sett, kjør
   dobbel progresjon via `next_set_for_exercise`, oppdater routine-vekter
4. Pusher med PUT `/v1/routines/{id}`

Ikke-opinionert om navn: hvis en routine har "Push" i tittelen, matcher
den upper_1_push. Bruker så øvelsene som allerede ligger der.
"""

from __future__ import annotations

import os

import httpx

from src.coaching.history import last_top_set
from src.coaching.philosophy import next_set_for_exercise
from src.coaching.preferences import get_exercise_prefs
from src.coaching.proposer import ProposedWeek
from src.db.connection import connect

HEVY_API = "https://api.hevyapp.com/v1"


# Match session type → title-keywords i Hevy-routines (OR-match)
TYPE_TO_KEYWORDS: dict[str, list[str]] = {
    "upper_1_push": ["push", "upper 1", "upper push"],
    "upper_2_pull": ["pull", "upper 2", "upper pull"],
    "lower": ["lower", "legs", "leg day"],
}


def _api_key() -> str:
    key = os.environ.get("HEVY_API_KEY")
    if not key:
        raise RuntimeError("HEVY_API_KEY ikke satt i miljøet")
    return key


def _headers() -> dict[str, str]:
    return {"api-key": _api_key(), "Accept": "application/json",
            "Content-Type": "application/json"}


def _fetch_routines() -> list[dict]:
    """Hent alle routines. Paginerer om nødvendig."""
    routines: list[dict] = []
    page = 1
    while True:
        resp = httpx.get(
            f"{HEVY_API}/routines",
            headers=_headers(),
            params={"page": page, "pageSize": 10},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("routines") or []
        routines.extend(batch)
        if page >= (data.get("page_count") or 1) or not batch:
            break
        page += 1
    return routines


def _match_routine(session_type: str, routines: list[dict]) -> dict | None:
    """Finn routine som matcher session_type. Først eksakt-title, så keywords."""
    keywords = TYPE_TO_KEYWORDS.get(session_type, [])
    if not keywords:
        return None
    for r in routines:
        title_lower = (r.get("title") or "").lower()
        for kw in keywords:
            if kw in title_lower:
                return r
    return None


def _apply_progression_to_exercise(
    conn, exercise_title: str, ex_data: dict,
) -> tuple[dict, str | None]:
    """Juster sets i en exercise-block basert på dobbel progresjon.

    Returnerer (oppdatert_exercise, note). note er string hvis noe endret,
    None hvis ingen endring.
    """
    prefs = get_exercise_prefs(conn, exercise_title)
    top = last_top_set(conn, exercise_title, within_days=60)
    if top is None:
        return ex_data, None

    rec = next_set_for_exercise(
        top, rep_min=prefs.rep_min, rep_max=prefs.rep_max,
        increment_kg=prefs.increment_kg,
    )

    if rec.action == "no_data":
        return ex_data, None

    # Apply target weight/reps to every "normal" set in the routine
    updated = dict(ex_data)
    sets = list(ex_data.get("sets") or [])
    new_sets: list[dict] = []
    changed = False
    for s in sets:
        if s.get("type") != "normal":
            new_sets.append(s)
            continue
        new_s = dict(s)
        if rec.target_weight_kg is not None and new_s.get("weight_kg") != rec.target_weight_kg:
            new_s["weight_kg"] = rec.target_weight_kg
            changed = True
        if rec.target_reps is not None:
            new_s["reps"] = rec.target_reps
            changed = True
        new_sets.append(new_s)

    updated["sets"] = new_sets
    note = None
    if changed:
        note = (
            f"{exercise_title}: {rec.action} → "
            f"{rec.target_weight_kg or '—'} kg × {rec.target_reps}"
        )
    return updated, note


def _update_routine(routine_id: str, routine: dict) -> bool:
    """Push oppdatert routine til Hevy via PUT. Returnerer True ved suksess."""
    payload = {
        "routine": {
            "title": routine.get("title"),
            "folder_id": routine.get("folder_id"),
            "notes": routine.get("notes") or "",
            "exercises": routine.get("exercises") or [],
        }
    }
    resp = httpx.put(
        f"{HEVY_API}/routines/{routine_id}",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    return resp.status_code in (200, 204)


def sync_strength_routines_for_week(proposal: ProposedWeek) -> dict:
    """Gjennomgå alle strength-dager i et ukesforslag, oppdater matchende
    Hevy-routines med next-session-progresjon per øvelse.

    Returnerer {updated, skipped, errors, notes}.
    """
    strength_types = set(TYPE_TO_KEYWORDS.keys())
    strength_sessions = [s for s in proposal.sessions if s.type in strength_types]

    result = {"updated": 0, "skipped": 0, "errors": 0, "notes": []}

    if not strength_sessions:
        result["notes"].append("Ingen strength-dager å speile")
        return result

    try:
        routines = _fetch_routines()
    except (httpx.HTTPError, RuntimeError) as e:
        result["errors"] += 1
        result["notes"].append(f"Kunne ikke hente routines: {e}")
        return result

    with connect() as conn:
        for session in strength_sessions:
            routine = _match_routine(session.type, routines)
            if routine is None:
                result["skipped"] += 1
                result["notes"].append(
                    f"Ingen routine matchet for {session.type} "
                    f"(søkte etter {TYPE_TO_KEYWORDS[session.type]})"
                )
                continue

            # Apply progression to hver øvelse i routinen
            changes: list[str] = []
            updated_exercises: list[dict] = []
            for ex in routine.get("exercises") or []:
                title = ex.get("title") or "Unknown"
                new_ex, note = _apply_progression_to_exercise(conn, title, ex)
                updated_exercises.append(new_ex)
                if note:
                    changes.append(note)

            if not changes:
                result["skipped"] += 1
                result["notes"].append(
                    f"{routine.get('title')}: ingen progresjonsendringer"
                )
                continue

            routine_with_updates = dict(routine)
            routine_with_updates["exercises"] = updated_exercises

            try:
                ok = _update_routine(routine["id"], routine_with_updates)
                if ok:
                    result["updated"] += 1
                    result["notes"].append(
                        f"✓ {routine.get('title')} ({session.day_of_week}): "
                        + "; ".join(changes[:3])
                        + (f" (+{len(changes)-3} more)" if len(changes) > 3 else "")
                    )
                else:
                    result["errors"] += 1
                    result["notes"].append(
                        f"✗ {routine.get('title')}: PUT-request feilet"
                    )
            except httpx.HTTPError as e:
                result["errors"] += 1
                result["notes"].append(
                    f"✗ {routine.get('title')}: {e}"
                )

    return result
