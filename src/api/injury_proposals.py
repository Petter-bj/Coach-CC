"""Trygge, brukerbekreftede endringer av skade-kontekst.

Modellen tolker aldri symptomer som en diagnose. Den kan bare beskrive en
kandidat basert på brukerens egne ord. API-laget validerer kandidaten, lagrer
den som et forslag, og oppdaterer ``injuries`` først etter et eget klikk.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any


VALID_ACTIONS = {"create", "update"}
VALID_STATUSES = {"active", "healing", "resolved"}


def _text(value: Any, *, maximum: int, required: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean:
        return None if required else ""
    return clean[:maximum]


def _severity(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= 3 else None


def _past_or_today(value: Any, *, reported_on: date) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed <= reported_on else None


def validate_injury_proposal(
    raw: dict[str, Any] | None,
    *,
    active_injuries: list[dict[str, Any]],
    reported_on: date,
) -> dict[str, Any] | None:
    """Godta bare komplette, små statusendringer for kjente skader.

    ``active_injuries`` er den kuraterte konteksten modellen faktisk så. Den
    kan derfor ikke peke på en vilkårlig eller allerede avsluttet skaderad.
    """
    if not isinstance(raw, dict) or raw.get("action") not in VALID_ACTIONS:
        return None
    action = raw["action"]
    status = raw.get("status")
    severity = _severity(raw.get("severity"))
    notes = _text(raw.get("notes"), maximum=800) or None
    if status not in VALID_STATUSES or severity is None:
        return None

    if action == "create":
        body_part = _text(raw.get("body_part"), maximum=80, required=True)
        # En ny skade skal ikke markeres som løst av modellen. Hvis brukeren
        # beskriver en gammel, løst plage er det bedre å diskutere den enn å
        # legge inn en kunstig aktiv helselogg.
        if not body_part or status != "active":
            return None
        started_at = _past_or_today(raw.get("started_at"), reported_on=reported_on)
        return {
            "action": "create",
            "body_part": body_part,
            "severity": severity,
            "status": "active",
            "started_at": started_at or reported_on.isoformat(),
            "notes": notes,
            "reported_on": reported_on.isoformat(),
        }

    injury_id = raw.get("injury_id")
    if not isinstance(injury_id, int):
        return None
    known = next(
        (injury for injury in active_injuries if injury.get("id") == injury_id),
        None,
    )
    if known is None:
        return None
    return {
        "action": "update",
        "injury_id": injury_id,
        "body_part": known["body_part"],
        "from_status": known["status"],
        "from_severity": known["severity"],
        "status": status,
        "severity": severity,
        "notes": notes,
        "reported_on": reported_on.isoformat(),
    }


def create_injury_proposal(
    conn: sqlite3.Connection,
    *,
    question: str,
    coach_answer: str,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    target_id = proposal.get("injury_id") if proposal["action"] == "update" else None
    cursor = conn.execute(
        """
        INSERT INTO injury_status_proposals (
            target_injury_id, question, coach_answer, proposal_json
        ) VALUES (?, ?, ?, ?)
        """,
        (target_id, question, coach_answer, json.dumps(proposal, ensure_ascii=False)),
    )
    return {"id": cursor.lastrowid, "injury": proposal, "status": "pending"}


def _pending_row(conn: sqlite3.Connection, proposal_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, target_injury_id, proposal_json, status
          FROM injury_status_proposals
         WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()


def _append_note(existing: str | None, note: str | None, reported_on: str) -> str | None:
    if not note:
        return existing
    entry = f"{reported_on}: {note}"
    return f"{existing}\n{entry}" if existing else entry


def apply_injury_proposal(conn: sqlite3.Connection, proposal_id: int) -> dict[str, Any] | None:
    """Oppdater én skade atomisk etter eksplisitt brukerbekreftelse."""
    row = _pending_row(conn, proposal_id)
    if row is None or row["status"] != "pending":
        return None
    try:
        proposal = json.loads(row["proposal_json"])
    except json.JSONDecodeError:
        return None
    if not isinstance(proposal, dict) or proposal.get("action") not in VALID_ACTIONS:
        return None

    if proposal["action"] == "create":
        cursor = conn.execute(
            """
            INSERT INTO injuries (body_part, severity, started_at, status, notes)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (
                proposal["body_part"],
                proposal["severity"],
                proposal["started_at"],
                _append_note(None, proposal.get("notes"), proposal["reported_on"]),
            ),
        )
        injury_id = cursor.lastrowid
    else:
        injury_id = proposal.get("injury_id")
        if not isinstance(injury_id, int):
            return None
        injury = conn.execute(
            """
            SELECT id, body_part, notes FROM injuries
             WHERE id = ? AND status IN ('active', 'healing')
            """,
            (injury_id,),
        ).fetchone()
        if injury is None:
            return None
        resolved_at = proposal["reported_on"] if proposal["status"] == "resolved" else None
        conn.execute(
            """
            UPDATE injuries
               SET severity = ?, status = ?, resolved_at = ?, notes = ?
             WHERE id = ?
            """,
            (
                proposal["severity"],
                proposal["status"],
                resolved_at,
                _append_note(injury["notes"], proposal.get("notes"), proposal["reported_on"]),
                injury_id,
            ),
        )

    updated = conn.execute(
        """
        SELECT id, body_part, severity, started_at, resolved_at, status, notes
          FROM injuries WHERE id = ?
        """,
        (injury_id,),
    ).fetchone()
    conn.execute(
        """
        UPDATE injury_status_proposals
           SET status = 'applied', applied_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
         WHERE id = ? AND status = 'pending'
        """,
        (proposal_id,),
    )
    return {
        "id": proposal_id,
        "status": "applied",
        "injury": dict(updated) if updated is not None else None,
    }


def discard_injury_proposal(conn: sqlite3.Connection, proposal_id: int) -> bool:
    cursor = conn.execute(
        """
        UPDATE injury_status_proposals SET status = 'discarded'
         WHERE id = ? AND status = 'pending'
        """,
        (proposal_id,),
    )
    return cursor.rowcount == 1
