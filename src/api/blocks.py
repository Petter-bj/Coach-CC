"""Lesekontrakt for det strategiske laget: treningsblokker.

En blokk er planmotoren over enkeluker og økter. Når databasen ikke har en
aktiv blokk, returnerer vi en tydelig merket eksempelblokk. Den er virtuell og
lagres aldri i SQLite; den kan derfor ikke lekke inn i brukerens plan eller
belastningsregnskap.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from src.coaching.strength_structure import default_strength_structure, normalize_strength_structure


EXAMPLE_WEEKS = (
    {
        "focus": "Rytme og toleranse",
        "progression_note": "Finn en stabil rytme med tre løpsdager uten å jage fart.",
        "planned_volume_note": "3 løpeøkter · 1 valgfri styrke/cross-train",
        "is_deload": False,
    },
    {
        "focus": "Bygge frekvens",
        "progression_note": "Behold rolig volum og legg til korte, lette stigningsløp hvis kroppen er fin.",
        "planned_volume_note": "3–4 løpeøkter · fortsatt mest rolig",
        "is_deload": False,
    },
    {
        "focus": "Kontrollert terskel",
        "progression_note": "Én kontrollert kvalitetsøkt, resten lett nok til at kne og legger er stabile.",
        "planned_volume_note": "1 kvalitet · 2 rolige · 1 styrke",
        "is_deload": False,
    },
    {
        "focus": "Konsolidere",
        "progression_note": "Hold kvaliteten lik og la totalbelastningen sette seg før neste byggesteg.",
        "planned_volume_note": "Samme rytme · ingen volumjakt",
        "is_deload": False,
    },
    {
        "focus": "Bygge robust volum",
        "progression_note": "Utvid én rolig økt forsiktig bare dersom belastningen og symptomene er stabile.",
        "planned_volume_note": "1 lengre rolig · 1 kvalitet · 1–2 lette",
        "is_deload": False,
    },
    {
        "focus": "Deload og vurdering",
        "progression_note": "Trekk ned volumet, behold litt rytme og vurder hva kroppen responderte best på.",
        "planned_volume_note": "Redusert volum · lett kvalitet eller helt rolig",
        "is_deload": True,
    },
)


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _phase_label(phase: str) -> str:
    return {
        "base": "Base",
        "build": "Bygg",
        "peak": "Toppform",
        "taper": "Nedtrapping",
        "recovery": "Restitusjon",
    }.get(phase, phase.title())


def _example_block(as_of: date) -> dict[str, Any]:
    start = _week_start(as_of)
    weeks = []
    for index, definition in enumerate(EXAMPLE_WEEKS, start=1):
        week_start = start + timedelta(days=(index - 1) * 7)
        weeks.append({
            "number": index,
            "week_start": week_start.isoformat(),
            "week_end": (week_start + timedelta(days=6)).isoformat(),
            "status": "current" if index == 1 else "upcoming",
            "planned_session_count": 0,
            **definition,
        })
    return {
        "id": "example-base-6",
        "source": "example",
        "is_example": True,
        "name": "6 uker · Base og robusthet",
        "phase": "base",
        "phase_label": "Base",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=6 * 7 - 1)).isoformat(),
        "goal": "Bygge stabil løpstoleranse og en rytme som tåler mer planlagt kvalitet senere.",
        "notes": "Eksempelblokk — ikke aktiv og ikke skrevet til planen din.",
        "strength_structure": default_strength_structure(),
        "principles": [
            "Én kontrollert kvalitetsøkt når signalene og kroppen tillater det.",
            "Rolige økter er hovedvolumet; endre bare én belastningsvariabel av gangen.",
            "Smerte eller vedvarende irritasjon slår alltid planens volumprogresjon.",
        ],
        "weeks": weeks,
    }


def _real_block(conn: sqlite3.Connection, row: sqlite3.Row, as_of: date) -> dict[str, Any]:
    block_id = row["id"]
    saved_weeks = {
        item["week_start"]: item
        for item in conn.execute(
            """
            SELECT week_start, focus, progression_note, planned_volume_note, is_deload
              FROM training_block_weeks
             WHERE training_block_id = ?
             ORDER BY week_start
            """,
            (block_id,),
        ).fetchall()
    }
    session_counts = {
        item["week_start"]: item["n"]
        for item in conn.execute(
            """
            SELECT date(planned_date, '-' || ((strftime('%w', planned_date) + 6) % 7) || ' days')
                     AS week_start,
                   COUNT(*) AS n
              FROM planned_sessions
             WHERE training_block_id = ?
             GROUP BY week_start
            """,
            (block_id,),
        ).fetchall()
    }
    cursor = _week_start(date.fromisoformat(row["start_date"]))
    end = date.fromisoformat(row["end_date"])
    current_start = _week_start(as_of)
    weeks = []
    number = 1
    while cursor <= end:
        saved = saved_weeks.get(cursor.isoformat())
        weeks.append({
            "number": number,
            "week_start": cursor.isoformat(),
            "week_end": (cursor + timedelta(days=6)).isoformat(),
            "status": "completed" if cursor < current_start else "current" if cursor == current_start else "upcoming",
            "focus": saved["focus"] if saved else "Planlegging mangler",
            "progression_note": saved["progression_note"] if saved else None,
            "planned_volume_note": saved["planned_volume_note"] if saved else None,
            "is_deload": bool(saved["is_deload"]) if saved else False,
            "planned_session_count": session_counts.get(cursor.isoformat(), 0),
        })
        cursor += timedelta(days=7)
        number += 1

    try:
        stored_structure = json.loads(row["strength_structure_json"])
    except (KeyError, TypeError, ValueError):
        stored_structure = None
    strength_structure = normalize_strength_structure(stored_structure) or default_strength_structure()

    return {
        "id": block_id,
        "source": "plan",
        "is_example": False,
        "name": row["name"],
        "phase": row["phase"],
        "phase_label": _phase_label(row["phase"]),
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "goal": row["goal_title"],
        "notes": row["notes"],
        "strength_structure": strength_structure,
        "principles": [],
        "weeks": weeks,
    }


def build_block_payload(
    conn: sqlite3.Connection,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Returner den aktive, reelle blokken eller en ufarlig eksempelblokk."""
    as_of = as_of or date.today()
    row = conn.execute(
        """
        SELECT b.*, g.title AS goal_title
          FROM training_blocks b
          LEFT JOIN goals g ON g.id = b.primary_goal_id
         WHERE b.start_date <= ? AND b.end_date >= ?
         ORDER BY b.start_date DESC
         LIMIT 1
        """,
        (as_of.isoformat(), as_of.isoformat()),
    ).fetchone()
    active = row is not None
    if row is None:
        # Blokker opprettes ofte på søndag for mandagen etter. Vis nærmeste
        # planlagte blokk i stedet for å late som at den ikke finnes før den
        # første dagen faktisk begynner.
        row = conn.execute(
            """
            SELECT b.*, g.title AS goal_title
              FROM training_blocks b
              LEFT JOIN goals g ON g.id = b.primary_goal_id
             WHERE b.start_date > ?
             ORDER BY b.start_date ASC
             LIMIT 1
            """,
            (as_of.isoformat(),),
        ).fetchone()
    block = _real_block(conn, row, as_of) if row else _example_block(as_of)
    block["is_active"] = active if row else False
    return {
        "as_of": as_of.isoformat(),
        "active": active,
        "block": block,
    }
