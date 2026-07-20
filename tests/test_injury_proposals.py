"""Skadeendringer er forslag til brukeren, aldri en modell-sideeffekt."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date

import httpx

from src.api.app import create_app
from src.api.coach import build_coach_context
from src.api.injury_proposals import validate_injury_proposal
from src.coaching.deepseek import CoachReply
from src.db.connection import configure
from src.db.migrations import migrate


def _database(path) -> int:
    conn = sqlite3.connect(path)
    configure(conn)
    migrate(conn)
    injury = conn.execute(
        """
        INSERT INTO injuries (body_part, severity, started_at, status, notes)
        VALUES ('Legghinneplager', 2, '2026-07-10', 'active', 'Smerte ved løping.')
        """
    )
    conn.commit()
    conn.close()
    return injury.lastrowid


def test_chat_injury_candidate_requires_confirmation_before_changing_context(tmp_path) -> None:
    db_path = tmp_path / "trening.db"
    injury_id = _database(db_path)
    seen: dict = {}

    def responder(question: str, context: dict) -> CoachReply:
        seen["question"] = question
        seen["context"] = context
        return CoachReply(
            answer="Jeg foreslår å sette legghinneplagene til i bedring.",
            model="deepseek-v4-pro",
            injury_proposal={
                "action": "update",
                "injury_id": injury_id,
                "severity": 1,
                "status": "healing",
                "notes": "Brukeren oppgir ingen smerte de siste tre dagene.",
            },
        )

    async def chat() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=create_app(db_path=db_path, api_token="test-token", coach_responder=responder)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                "/api/coach/chat",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Kjenner ikke smerte i leggen de siste tre dagene."},
            )

    chat = asyncio.run(chat())

    assert chat.status_code == 200
    body = chat.json()
    assert body["changes_applied"] is False
    assert body["injury_proposal"] == {
        "id": body["injury_proposal"]["id"],
        "status": "pending",
        "injury": {
            "action": "update",
            "injury_id": injury_id,
            "body_part": "Legghinneplager",
            "from_status": "active",
            "from_severity": 2,
            "status": "healing",
            "severity": 1,
            "notes": "Brukeren oppgir ingen smerte de siste tre dagene.",
            "reported_on": date.today().isoformat(),
        },
    }
    assert seen["question"].startswith("Kjenner ikke smerte")
    assert seen["context"]["active_injuries"] == [{
        "id": injury_id,
        "body_part": "Legghinneplager",
        "severity": 2,
        "started_at": "2026-07-10",
        "status": "active",
        "notes": "Smerte ved løping.",
    }]

    conn = sqlite3.connect(db_path)
    configure(conn)
    before = conn.execute("SELECT severity, status FROM injuries WHERE id = ?", (injury_id,)).fetchone()
    assert before["severity"] == 2
    assert before["status"] == "active"
    conn.close()

    async def apply(proposal_id: int) -> httpx.Response:
        transport = httpx.ASGITransport(
            app=create_app(db_path=db_path, api_token="test-token", coach_responder=responder)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                f"/api/injury-proposals/{proposal_id}/apply",
                headers={"Authorization": "Bearer test-token"},
            )

    applied = asyncio.run(apply(body["injury_proposal"]["id"]))

    conn = sqlite3.connect(db_path)
    configure(conn)
    after = conn.execute("SELECT severity, status, notes FROM injuries WHERE id = ?", (injury_id,)).fetchone()
    context_after = build_coach_context(conn)
    conn.close()

    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert after["severity"] == 1
    assert after["status"] == "healing"
    assert "Brukeren oppgir ingen smerte" in after["notes"]
    assert context_after["active_injuries"][0]["status"] == "healing"
    assert context_after["active_injuries"][0]["severity"] == 1


def test_injury_candidate_cannot_target_injury_outside_model_context() -> None:
    proposal = validate_injury_proposal(
        {
            "action": "update",
            "injury_id": 99,
            "severity": 1,
            "status": "resolved",
            "notes": "Føles bra.",
        },
        active_injuries=[{
            "id": 7,
            "body_part": "Kne",
            "severity": 2,
            "status": "active",
        }],
        reported_on=date(2026, 7, 20),
    )

    assert proposal is None


def test_new_injury_candidate_cannot_be_marked_resolved() -> None:
    proposal = validate_injury_proposal(
        {
            "action": "create",
            "body_part": "Legg",
            "severity": 2,
            "status": "resolved",
            "started_at": "2026-07-20",
        },
        active_injuries=[],
        reported_on=date(2026, 7, 20),
    )

    assert proposal is None
