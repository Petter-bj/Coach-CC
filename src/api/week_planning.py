"""Trygg forslag- og bekreftelsesflyt for ukecoachen.

Modellen har bare lov til å beskrive endringer. Denne modulen validerer dem mot
ukens faktiske plan, persisterer den validerte diffen og lar bare en egen
``apply``-handling oppdatere ``planned_sessions``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from typing import Any

from src.api.blocks import build_block_payload
from src.api.week import build_week_overview
from src.coaching.knowledge import select_knowledge, topic_flags_from_text
from src.coaching.philosophy import phase_guidance, running_ruling
from src.coaching.strength_context import build_strength_context


VALID_ACTIONS = {"move", "skip", "replace", "add"}
VALID_SESSION_TYPES = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
MAX_OPERATIONS = 6

_WEEKDAY_NAMES_NO = (
    "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag",
)


def _week_bounds(week_start: date) -> tuple[str, str]:
    monday = week_start - timedelta(days=week_start.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def _week_relation(monday: date, today: date) -> str:
    """Om den valgte uken er 'past', 'current' eller 'future' relativt til i dag."""
    today_monday = today - timedelta(days=today.weekday())
    if monday < today_monday:
        return "past"
    if monday > today_monday:
        return "future"
    return "current"


def _active_injuries(conn: sqlite3.Connection, as_of: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, body_part, severity, started_at, status, notes
              FROM injuries
             WHERE status IN ('active', 'healing')
             ORDER BY severity DESC, started_at DESC
            """
        ).fetchall()
    ]


def _as_iso_date(value: Any, *, start: str, end: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    iso = parsed.isoformat()
    return iso if start <= iso <= end else None


def _compact_session(session: dict[str, Any]) -> dict[str, Any]:
    """Minste planidentitet som trengs i en synlig diff."""
    return {
        "id": session["id"],
        "date": session["date"],
        "type": session.get("type"),
        "description": session.get("description"),
        "status": session.get("status"),
        "target_metrics": session.get("target_metrics"),
    }


def _clean_text(value: Any, *, maximum: int, required: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None if required else ""
    return cleaned[:maximum]


def _clean_metrics(value: Any) -> dict[str, Any] | None:
    """Godta kun et lite, JSON-serialiserbart målobjekt fra modellen."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    allowed = {
        "duration_min",
        "distance_km",
        "zone",
        "intensity_zone",
        "hr_target",
        "pace_target",
        "reps",
        "sets",
    }
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key not in allowed:
            continue
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, (int, float)):
            cleaned[key] = item
        elif isinstance(item, str) and len(item.strip()) <= 80:
            cleaned[key] = item.strip()
        elif key == "hr_target" and isinstance(item, list) and len(item) == 2 and all(
            isinstance(number, (int, float)) and not isinstance(number, bool)
            for number in item
        ):
            cleaned[key] = item
    return cleaned or None


def build_week_coach_context(
    conn: sqlite3.Connection,
    week_start: date,
    *,
    question: str = "",
    today: date | None = None,
) -> dict[str, Any]:
    """Kuratert kontekst for én uke, uten rå GPS/FIT eller matvarelogg.

    Gir modellen eksplisitt tidsforankring (dagens dato, valgt ukes datoer med
    norske ukedagsnavn, ISO-ukenummer og om uken er fortid/nåtid/fremtid), aktiv
    blokk/fase med uke-i-blokk, ukens planlagte og gjennomførte økter, aktive
    skader med deterministiske begrensninger, og en liten coaching-kjerne med
    relevante temamoduler. Modellen skal aldri måtte spørre om dato.
    """
    today = today or date.today()
    start, end = _week_bounds(week_start)
    monday = date.fromisoformat(start)
    week = build_week_overview(conn, monday)
    block_payload = build_block_payload(conn, monday)
    block = block_payload["block"]
    block_context = week.get("block_context")

    # Syv navngitte dager med planlagte og gjennomførte økter, slik at modellen
    # kan mappe en ukedag rett til en dato uten å gjette.
    days: list[dict[str, Any]] = []
    for day in week["days"]:
        day_date = date.fromisoformat(day["date"])
        days.append({
            "date": day["date"],
            "weekday": _WEEKDAY_NAMES_NO[day_date.weekday()],
            "status": day["status"],
            "is_today": day["date"] == today.isoformat(),
            "planned_sessions": day.get("planned_sessions", []),
            "completed_workouts": [
                {
                    "type": workout.get("type"),
                    "duration_sec": workout.get("duration_sec"),
                    "distance_m": workout.get("distance_m"),
                    "avg_hr": workout.get("avg_hr"),
                    "rpe": workout.get("rpe"),
                }
                for workout in day.get("workouts", [])
            ],
        })

    injuries = _active_injuries(conn, today.isoformat())
    ruling = running_ruling(injuries)
    phase = block.get("phase")
    guidance = phase_guidance(phase)
    constraints = {
        "running": {
            "allowed": ruling.allow,
            "reason": ruling.reason,
            "cross_training_alternative": ruling.alternative,
        },
        "phase": {
            "phase": guidance.phase,
            "focus": guidance.focus,
            "run_intensity_cap_zone": guidance.run_intensity_cap_zone,
            "should_recommend_run_z3": guidance.should_recommend_run_z3,
            "strength_modulation": guidance.strength_modulation,
            "volume_ramp_pct_per_week_max": guidance.volume_ramp_pct_per_week_max,
            "notes": guidance.notes,
        },
    }

    # Temavalg følger spørsmålet, ikke bare hva som tilfeldigvis finnes i den
    # valgte uka. En ren løpeforespørsel skal ikke få hele styrkebiblioteket
    # bare fordi uken også inneholder en styrkedag.
    include_strength, include_running = topic_flags_from_text(question)
    coaching_policy = select_knowledge(
        surface="week",
        include_strength=include_strength,
        include_running=include_running,
        include_planning=True,
    )

    return {
        "scope": {
            "today": today.isoformat(),
            "week_start": start,
            "week_end": end,
            "iso_week": monday.isocalendar().week,
            "relation_to_today": _week_relation(monday, today),
            "source": "plan_and_automatic_summaries",
        },
        "days": days,
        "week": week,
        "block": {
            "name": block["name"],
            "phase": block["phase"],
            "goal": block.get("goal"),
            "is_example": block["is_example"],
            "week_number": block_context.get("week_number") if block_context else None,
            "total_weeks": block_context.get("total_weeks") if block_context else None,
            "focus": block_context.get("focus") if block_context else None,
            "strength_structure": block.get("strength_structure"),
        },
        "active_injuries": injuries,
        "deterministic_constraints": constraints,
        "coaching_policy": coaching_policy,
        **({"strength_context": build_strength_context(conn)} if include_strength else {}),
        "proposal_contract": {
            "writes_are_not_automatic": True,
            "allowed_actions": sorted(VALID_ACTIONS),
            "only_selected_week": True,
            "can_propose_hevy_routines": True,
            "hevy_created_only_after_confirmation": True,
        },
    }


def validate_operations(
    raw_operations: list[dict[str, Any]],
    *,
    week_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Returner kun operasjoner som er trygge og gyldige i valgt uke."""
    scope = week_context["scope"]
    start, end = scope["week_start"], scope["week_end"]
    sessions = {
        int(session["id"]): session
        for day in week_context["week"]["days"]
        for session in day.get("planned_sessions", [])
        if isinstance(session.get("id"), int)
    }
    validated: list[dict[str, Any]] = []
    touched_sessions: set[int] = set()
    for raw in raw_operations[:MAX_OPERATIONS]:
        if not isinstance(raw, dict):
            continue
        action = raw.get("action")
        if action not in VALID_ACTIONS:
            continue
        reason = _clean_text(raw.get("reason"), maximum=500) or ""

        if action in {"move", "skip", "replace"}:
            session_id = raw.get("session_id")
            if not isinstance(session_id, int) or session_id not in sessions or session_id in touched_sessions:
                continue
            before = _compact_session(sessions[session_id])
            # En plan som allerede er gjennomført eller vurdert skal ikke bli
            # endret av en fremtidsrettet ukediff.
            if before["status"] not in {"planned", "modified"}:
                continue
            operation: dict[str, Any] = {
                "action": action,
                "session_id": session_id,
                "reason": reason,
                "before": before,
            }
            if action == "move":
                to_date = _as_iso_date(raw.get("to_date"), start=start, end=end)
                if to_date is None or to_date == before["date"]:
                    continue
                operation["to_date"] = to_date
                operation["after"] = {**before, "date": to_date, "status": "modified"}
            elif action == "skip":
                operation["after"] = {**before, "status": "skipped"}
            else:
                session_type = raw.get("type")
                description = _clean_text(raw.get("description"), maximum=500, required=True)
                if not isinstance(session_type, str) or not VALID_SESSION_TYPES.fullmatch(session_type) or not description:
                    continue
                metrics = _clean_metrics(raw.get("target_metrics"))
                operation.update({
                    "type": session_type,
                    "description": description,
                    "target_metrics": metrics,
                    "after": {
                        **before,
                        "type": session_type,
                        "description": description,
                        "target_metrics": metrics,
                        "status": "modified",
                    },
                })
            touched_sessions.add(session_id)
            validated.append(operation)
            continue

        # add: begrenset til valgt uke og uten å late som at det finnes en
        # eksisterende økt å endre.
        planned_date = _as_iso_date(raw.get("date"), start=start, end=end)
        session_type = raw.get("type")
        description = _clean_text(raw.get("description"), maximum=500, required=True)
        if (
            planned_date is None
            or not isinstance(session_type, str)
            or not VALID_SESSION_TYPES.fullmatch(session_type)
            or not description
        ):
            continue
        metrics = _clean_metrics(raw.get("target_metrics"))
        validated.append({
            "action": "add",
            "date": planned_date,
            "type": session_type,
            "description": description,
            "target_metrics": metrics,
            "reason": reason,
            "after": {
                "date": planned_date,
                "type": session_type,
                "description": description,
                "target_metrics": metrics,
                "status": "planned",
            },
        })
    return validated


def create_proposal(
    conn: sqlite3.Connection,
    *,
    week_start: str,
    question: str,
    coach_answer: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Lagre den validerte diffen — fortsatt uten å skrive selve planen."""
    cursor = conn.execute(
        """
        INSERT INTO weekly_plan_proposals (week_start, question, coach_answer, operations_json)
        VALUES (?, ?, ?, ?)
        """,
        (week_start, question, coach_answer, json.dumps(operations, ensure_ascii=False)),
    )
    return {
        "id": cursor.lastrowid,
        "week_start": week_start,
        "question": question,
        "answer": coach_answer,
        "operations": operations,
        "status": "pending",
    }


def _proposal_row(conn: sqlite3.Connection, proposal_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, week_start, coach_answer, operations_json, status
          FROM weekly_plan_proposals
         WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()


def apply_proposal(conn: sqlite3.Connection, proposal_id: int) -> dict[str, Any] | None:
    """Bruk én pending diff atomisk etter eksplisitt brukerbekreftelse."""
    row = _proposal_row(conn, proposal_id)
    if row is None or row["status"] != "pending":
        return None
    try:
        operations = json.loads(row["operations_json"])
    except json.JSONDecodeError:
        return None
    if not isinstance(operations, list) or not operations:
        return None

    # Forslaget kan ha blitt gammelt mens brukeren tenkte. Verifiser hele
    # "før"-siden av diffen før én eneste rad endres, slik at vi aldri delvis
    # bruker en utdatert planendring.
    for operation in operations:
        if operation.get("action") not in {"move", "skip", "replace"}:
            continue
        before = operation.get("before")
        session_id = operation.get("session_id")
        if not isinstance(before, dict) or not isinstance(session_id, int):
            return None
        current = conn.execute(
            """
            SELECT planned_date, type, description, status, target_metrics
              FROM planned_sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if current is None or current["status"] not in {"planned", "modified"}:
            return None
        if (
            current["planned_date"] != before.get("date")
            or current["type"] != before.get("type")
            or current["description"] != before.get("description")
        ):
            return None

    for operation in operations:
        action = operation.get("action")
        if action == "move":
            conn.execute(
                """
                UPDATE planned_sessions
                   SET planned_date = ?, status = 'modified'
                 WHERE id = ? AND status IN ('planned', 'modified')
                """,
                (operation["to_date"], operation["session_id"]),
            )
        elif action == "skip":
            conn.execute(
                """
                UPDATE planned_sessions
                   SET status = 'skipped'
                 WHERE id = ? AND status IN ('planned', 'modified')
                """,
                (operation["session_id"],),
            )
        elif action == "replace":
            conn.execute(
                """
                UPDATE planned_sessions
                   SET type = ?, description = ?, target_metrics = ?, status = 'modified'
                 WHERE id = ? AND status IN ('planned', 'modified')
                """,
                (
                    operation["type"],
                    operation["description"],
                    json.dumps(operation.get("target_metrics"), ensure_ascii=False)
                    if operation.get("target_metrics") is not None else None,
                    operation["session_id"],
                ),
            )
        elif action == "add":
            conn.execute(
                """
                INSERT INTO planned_sessions (planned_date, type, description, target_metrics, status, notes)
                VALUES (?, ?, ?, ?, 'planned', ?)
                """,
                (
                    operation["date"],
                    operation["type"],
                    operation["description"],
                    json.dumps(operation.get("target_metrics"), ensure_ascii=False)
                    if operation.get("target_metrics") is not None else None,
                    operation.get("reason") or None,
                ),
            )
    conn.execute(
        """
        UPDATE weekly_plan_proposals
           SET status = 'applied', applied_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
         WHERE id = ? AND status = 'pending'
        """,
        (proposal_id,),
    )
    return {
        "id": proposal_id,
        "status": "applied",
        "week_start": row["week_start"],
        "operations": operations,
    }


def discard_proposal(conn: sqlite3.Connection, proposal_id: int) -> bool:
    """Forkast en pending diff uten å endre planen."""
    cursor = conn.execute(
        """
        UPDATE weekly_plan_proposals
           SET status = 'discarded'
         WHERE id = ? AND status = 'pending'
        """,
        (proposal_id,),
    )
    return cursor.rowcount == 1
