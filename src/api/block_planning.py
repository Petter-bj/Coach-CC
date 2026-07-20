"""Trygg forslag- og bekreftelsesflyt for opprettelse og endring av blokker."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from src.api.blocks import build_block_payload
from src.coaching.knowledge import select_knowledge, topic_flags_from_text
from src.coaching.strength_context import build_strength_context
from src.coaching.strength_structure import normalize_strength_structure


VALID_PHASES = {"base", "build", "peak", "taper", "recovery"}
MIN_BLOCK_WEEKS = 2
MAX_BLOCK_WEEKS = 12


def _text(value: Any, *, maximum: int, required: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean:
        return None if required else ""
    return clean[:maximum]


def _monday(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed - timedelta(days=parsed.weekday())


def build_block_coach_context(
    conn: sqlite3.Connection,
    as_of: date | None = None,
    *,
    question: str = "",
) -> dict[str, Any]:
    """Kuratert strategi-kontekst, uten rå helse- eller posisjonsdata."""
    payload = build_block_payload(conn, as_of)
    block = payload["block"]
    goals = [
        {"id": row["id"], "title": row["title"], "target_date": row["target_date"], "priority": row["priority"]}
        for row in conn.execute(
            """
            SELECT id, title, target_date, priority
              FROM goals
             WHERE status = 'active'
             ORDER BY CASE priority WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END, id
             LIMIT 5
            """
        ).fetchall()
    ]
    include_strength, include_running = topic_flags_from_text(question)
    return {
        "as_of": payload["as_of"],
        "current_block": {
            "id": block["id"],
            "is_example": block["is_example"],
            "name": block["name"],
            "phase": block["phase"],
            "start_date": block["start_date"],
            "end_date": block["end_date"],
            "goal": block.get("goal"),
            "notes": block.get("notes"),
            "strength_structure": block.get("strength_structure"),
            "weeks": [
                {
                    "number": week["number"],
                    "focus": week["focus"],
                    "progression_note": week.get("progression_note"),
                    "planned_volume_note": week.get("planned_volume_note"),
                    "is_deload": week["is_deload"],
                }
                for week in block["weeks"]
            ],
        },
        "known_goals": goals,
        # Blokk-flaten får alltid coach-kjerne + planlegging/fase, pluss
        # styrke/løping når spørsmålet berører det.
        "coaching_policy": _block_coaching_policy(question),
        **({"strength_context": build_strength_context(conn)} if include_strength else {}),
        "proposal_contract": {
            "writes_are_not_automatic": True,
            "creates_individual_sessions": False,
            "normal_week_range": f"{MIN_BLOCK_WEEKS}–{MAX_BLOCK_WEEKS}",
        },
    }


def _block_coaching_policy(question: str) -> dict[str, Any]:
    include_strength, include_running = topic_flags_from_text(question)
    return select_knowledge(
        surface="block",
        include_strength=include_strength,
        include_running=include_running,
        include_planning=True,
    )


def validate_block_proposal(
    raw: dict[str, Any] | None,
    *,
    context: dict[str, Any],
) -> tuple[dict[str, Any], int | None] | None:
    """Valider modellens blokk-kandidat før den kan bli en lagret diff."""
    if not isinstance(raw, dict):
        return None
    requested_action = raw.get("action")
    if requested_action not in {"create", "update"}:
        return None
    current = context["current_block"]
    target_id = current["id"] if isinstance(current.get("id"), int) else None
    # En virtuell eksempelblokk finnes ikke i databasen og kan kun bli starten
    # på en ny, reell blokk.
    if requested_action == "update" and target_id is None:
        return None

    name = _text(raw.get("name"), maximum=120, required=True)
    phase = raw.get("phase")
    start = _monday(raw.get("start_date"))
    goal = _text(raw.get("goal"), maximum=600, required=True)
    notes = _text(raw.get("notes"), maximum=1_000) or None
    strength_structure = normalize_strength_structure(raw.get("strength_structure"))
    raw_weeks = raw.get("weeks")
    if (
        not name
        or phase not in VALID_PHASES
        or start is None
        or not goal
        or strength_structure is None
        or not isinstance(raw_weeks, list)
        or not MIN_BLOCK_WEEKS <= len(raw_weeks) <= MAX_BLOCK_WEEKS
    ):
        return None

    weeks: list[dict[str, Any]] = []
    for raw_week in raw_weeks:
        if not isinstance(raw_week, dict):
            return None
        focus = _text(raw_week.get("focus"), maximum=160, required=True)
        progression = _text(raw_week.get("progression_note"), maximum=700) or None
        volume = _text(raw_week.get("planned_volume_note"), maximum=240) or None
        is_deload = raw_week.get("is_deload", False)
        if not focus or not isinstance(is_deload, bool):
            return None
        weeks.append({
            "focus": focus,
            "progression_note": progression,
            "planned_volume_note": volume,
            "is_deload": is_deload,
        })

    end = start + timedelta(days=len(weeks) * 7 - 1)
    return ({
        "action": requested_action,
        "name": name,
        "phase": phase,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "goal": goal,
        "notes": notes,
        "strength_structure": strength_structure,
        "weeks": weeks,
    }, target_id)


def create_block_proposal(
    conn: sqlite3.Connection,
    *,
    target_block_id: int | None,
    question: str,
    coach_answer: str,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO block_plan_proposals (target_block_id, question, coach_answer, proposal_json)
        VALUES (?, ?, ?, ?)
        """,
        (target_block_id, question, coach_answer, json.dumps(proposal, ensure_ascii=False)),
    )
    return {
        "id": cursor.lastrowid,
        "target_block_id": target_block_id,
        "answer": coach_answer,
        "proposal": proposal,
        "status": "pending",
    }


def _goal_id(conn: sqlite3.Connection, title: str) -> int:
    row = conn.execute(
        "SELECT id FROM goals WHERE title = ? AND status = 'active' ORDER BY id LIMIT 1",
        (title,),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    return int(conn.execute(
        "INSERT INTO goals (title, priority, status) VALUES (?, 'A', 'active')",
        (title,),
    ).lastrowid)


def _store_block_weeks(conn: sqlite3.Connection, block_id: int, proposal: dict[str, Any]) -> None:
    conn.execute("DELETE FROM training_block_weeks WHERE training_block_id = ?", (block_id,))
    start = date.fromisoformat(proposal["start_date"])
    for number, week in enumerate(proposal["weeks"]):
        conn.execute(
            """
            INSERT INTO training_block_weeks (
                training_block_id, week_start, focus, progression_note,
                planned_volume_note, is_deload
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                block_id,
                (start + timedelta(days=number * 7)).isoformat(),
                week["focus"],
                week["progression_note"],
                week["planned_volume_note"],
                int(week["is_deload"]),
            ),
        )


def apply_block_proposal(conn: sqlite3.Connection, proposal_id: int) -> dict[str, Any] | None:
    """Opprett eller oppdater blokk atomisk etter brukerens bekreftelse."""
    row = conn.execute(
        """
        SELECT id, target_block_id, proposal_json, status
          FROM block_plan_proposals WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    if row is None or row["status"] != "pending":
        return None
    try:
        proposal = json.loads(row["proposal_json"])
    except json.JSONDecodeError:
        return None
    if not isinstance(proposal, dict):
        return None

    action = proposal.get("action")
    goal = proposal.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        return None
    if action == "update":
        block_id = row["target_block_id"]
        if not isinstance(block_id, int):
            return None
        exists = conn.execute("SELECT 1 FROM training_blocks WHERE id = ?", (block_id,)).fetchone()
        if exists is None:
            return None
        overlap = conn.execute(
            """
            SELECT id FROM training_blocks
             WHERE id != ? AND start_date <= ? AND end_date >= ?
             LIMIT 1
            """,
            (block_id, proposal["end_date"], proposal["start_date"]),
        ).fetchone()
        if overlap is not None:
            return None
        goal_id = _goal_id(conn, goal)
        conn.execute(
            """
            UPDATE training_blocks
               SET name = ?, phase = ?, start_date = ?, end_date = ?,
                   primary_goal_id = ?, notes = ?, strength_structure_json = ?
             WHERE id = ?
            """,
            (
                proposal["name"], proposal["phase"], proposal["start_date"], proposal["end_date"],
                goal_id, proposal.get("notes"),
                json.dumps(proposal["strength_structure"], ensure_ascii=False),
                block_id,
            ),
        )
    elif action == "create":
        overlap = conn.execute(
            """
            SELECT id FROM training_blocks
             WHERE start_date <= ? AND end_date >= ?
             LIMIT 1
            """,
            (proposal["end_date"], proposal["start_date"]),
        ).fetchone()
        if overlap is not None:
            return None
        goal_id = _goal_id(conn, goal)
        block_id = int(conn.execute(
            """
            INSERT INTO training_blocks (
                name, phase, start_date, end_date, primary_goal_id, notes, strength_structure_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal["name"], proposal["phase"], proposal["start_date"], proposal["end_date"],
                goal_id, proposal.get("notes"),
                json.dumps(proposal["strength_structure"], ensure_ascii=False),
            ),
        ).lastrowid)
    else:
        return None

    _store_block_weeks(conn, block_id, proposal)
    conn.execute(
        """
        UPDATE block_plan_proposals
           SET status = 'applied', applied_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
         WHERE id = ? AND status = 'pending'
        """,
        (proposal_id,),
    )
    return {"id": proposal_id, "status": "applied", "block_id": block_id}


def discard_block_proposal(conn: sqlite3.Connection, proposal_id: int) -> bool:
    cursor = conn.execute(
        """
        UPDATE block_plan_proposals SET status = 'discarded'
         WHERE id = ? AND status = 'pending'
        """,
        (proposal_id,),
    )
    return cursor.rowcount == 1
