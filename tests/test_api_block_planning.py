"""Kontrakter for blokkcoachen: forslag først, lagring kun ved bekreftelse."""

from __future__ import annotations

import asyncio
import sqlite3

import httpx

from src.api.app import create_app
from src.coaching.deepseek import BlockCoachReply
from src.db.connection import configure
from src.db.migrations import migrate


def _db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    configure(conn)
    migrate(conn)
    return conn


def _six_week_candidate(*, action: str = "create", goal: str = "Bygge stabil løpstoleranse") -> dict:
    return {
        "action": action,
        "name": "6 uker · Stabil løpsrytme",
        "phase": "base",
        "start_date": "2026-07-20",
        "goal": goal,
        "notes": "Rolig og kontrollert progresjon.",
        "weeks": [
            {"focus": "Rytme", "progression_note": "Tre rolige løpsdager.", "planned_volume_note": "3 rolige", "is_deload": False},
            {"focus": "Frekvens", "progression_note": "Legg til en kort tur.", "planned_volume_note": "3–4 rolige", "is_deload": False},
            {"focus": "Terskel", "progression_note": "Én kontrollert kvalitetsøkt.", "planned_volume_note": "1 kvalitet", "is_deload": False},
            {"focus": "Konsolider", "progression_note": "Hold belastningen lik.", "planned_volume_note": "Samme rytme", "is_deload": False},
            {"focus": "Robusthet", "progression_note": "Forleng én rolig tur.", "planned_volume_note": "Litt mer rolig", "is_deload": False},
            {"focus": "Deload", "progression_note": "Trekk ned volumet.", "planned_volume_note": "Redusert volum", "is_deload": True},
        ],
    }


def test_block_coach_proposes_then_creates_only_after_confirmation(tmp_path) -> None:
    path = tmp_path / "trening.db"
    conn = _db(path)
    conn.commit()
    conn.close()
    seen: dict = {}

    def responder(question: str, context: dict) -> BlockCoachReply:
        seen["question"] = question
        seen["context"] = context
        return BlockCoachReply(
            answer="Dette er et rolig seksukersutkast. Se gjennom det før du bruker det.",
            model="deepseek-v4-pro",
            proposal=_six_week_candidate(),
        )

    async def ask_then_apply() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(db_path=path, api_token="test-token", block_coach_responder=responder)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            proposed = await client.post(
                "/api/blocks/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Lag en seksukers blokk for stabil løping"},
            )
            proposal_id = proposed.json()["proposal"]["id"]
            before = await client.get(
                "/api/blocks?day=2026-07-20",
                headers={"Authorization": "Bearer test-token"},
            )
            applied = await client.post(
                f"/api/block-proposals/{proposal_id}/apply",
                headers={"Authorization": "Bearer test-token"},
            )
        return proposed, before, applied

    proposed, before, applied = asyncio.run(ask_then_apply())

    assert proposed.status_code == 200
    assert proposed.json()["changes_applied"] is False
    assert proposed.json()["proposal"]["proposal"]["end_date"] == "2026-08-30"
    assert before.json()["block"]["is_example"] is True
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert seen["question"] == "Lag en seksukers blokk for stabil løping"
    assert seen["context"]["proposal_contract"]["writes_are_not_automatic"] is True

    conn = _db(path)
    try:
        block = conn.execute("SELECT name, start_date, end_date FROM training_blocks").fetchone()
        week_count = conn.execute("SELECT COUNT(*) AS n FROM training_block_weeks").fetchone()["n"]
        session_count = conn.execute("SELECT COUNT(*) AS n FROM planned_sessions").fetchone()["n"]
    finally:
        conn.close()
    assert dict(block) == {
        "name": "6 uker · Stabil løpsrytme",
        "start_date": "2026-07-20",
        "end_date": "2026-08-30",
    }
    assert week_count == 6
    assert session_count == 0


def test_overlapping_block_proposal_cannot_create_an_orphan_goal(tmp_path) -> None:
    path = tmp_path / "trening.db"
    conn = _db(path)
    conn.execute("INSERT INTO goals (title, priority, status) VALUES ('Eksisterende mål', 'A', 'active')")
    goal_id = conn.execute("SELECT id FROM goals WHERE title = 'Eksisterende mål'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO training_blocks (name, phase, start_date, end_date, primary_goal_id)
        VALUES ('Eksisterende blokk', 'base', '2026-07-20', '2026-08-30', ?)
        """,
        (goal_id,),
    )
    conn.commit()
    conn.close()

    def responder(_question: str, _context: dict) -> BlockCoachReply:
        return BlockCoachReply(
            answer="Her er et alternativ.",
            model="deepseek-v4-pro",
            proposal=_six_week_candidate(goal="Mål som ikke skal lagres ved konflikt"),
        )

    async def ask_then_try_apply() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=create_app(db_path=path, api_token="test-token", block_coach_responder=responder)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            proposed = await client.post(
                "/api/blocks/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Lag en annen blokk"},
            )
            proposal_id = proposed.json()["proposal"]["id"]
            return await client.post(
                f"/api/block-proposals/{proposal_id}/apply",
                headers={"Authorization": "Bearer test-token"},
            )

    response = asyncio.run(ask_then_try_apply())
    assert response.status_code == 409

    conn = _db(path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM training_blocks").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM goals").fetchone()["n"] == 1
    finally:
        conn.close()


def test_block_coach_history_survives_new_page_loads_and_feeds_next_answer(tmp_path) -> None:
    path = tmp_path / "trening.db"
    conn = _db(path)
    conn.commit()
    conn.close()
    seen_contexts: list[list[dict]] = []

    def responder(question: str, context: dict) -> BlockCoachReply:
        seen_contexts.append(context.get("conversation_history", []))
        return BlockCoachReply(
            answer=f"Svar på: {question}",
            model="deepseek-v4-pro",
            proposal=None,
        )

    async def talk_then_reload() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(db_path=path, api_token="test-token", block_coach_responder=responder)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            first = await client.post(
                "/api/blocks/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Jeg vil prioritere løping denne høsten"},
            )
            history = await client.get(
                "/api/blocks/coach/history",
                headers={"Authorization": "Bearer test-token"},
            )
            second = await client.post(
                "/api/blocks/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Hva bør det bety for blokken?"},
            )
        return first, history, second

    first, history, second = asyncio.run(talk_then_reload())

    assert first.status_code == 200
    assert history.status_code == 200
    assert history.json()["messages"] == [
        {"role": "user", "content": "Jeg vil prioritere løping denne høsten"},
        {"role": "assistant", "content": "Svar på: Jeg vil prioritere løping denne høsten", "model": "deepseek-v4-pro"},
    ]
    assert second.status_code == 200
    assert seen_contexts[0] == []
    assert seen_contexts[1] == [
        {"role": "user", "content": "Jeg vil prioritere løping denne høsten"},
        {"role": "assistant", "content": "Svar på: Jeg vil prioritere løping denne høsten"},
    ]
