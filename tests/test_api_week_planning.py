"""Kontrakter for blokk-oversikt og trygge forslag fra ukecoachen."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date

import httpx

from src.api.app import create_app
from src.api.blocks import build_block_payload
from src.coaching.deepseek import WeeklyCoachReply
from src.db.connection import configure
from src.db.migrations import migrate


def _db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    configure(conn)
    migrate(conn)
    return conn


def test_missing_active_block_returns_a_virtual_example_only() -> None:
    conn = _db(":memory:")
    try:
        payload = build_block_payload(conn, date(2026, 7, 20))
        stored = conn.execute("SELECT COUNT(*) AS n FROM training_blocks").fetchone()["n"]
    finally:
        conn.close()

    assert payload["active"] is False
    assert payload["block"]["is_example"] is True
    assert payload["block"]["id"] == "example-base-6"
    assert len(payload["block"]["weeks"]) == 6
    assert payload["block"]["weeks"][-1]["is_deload"] is True
    assert stored == 0


def test_weekly_coach_requires_confirmation_before_writing_plan(tmp_path) -> None:
    path = tmp_path / "trening.db"
    conn = _db(path)
    session = conn.execute(
        """
        INSERT INTO planned_sessions (planned_date, type, description, status, target_metrics)
        VALUES ('2026-07-21', 'threshold_run', '6 × 3 min terskel', 'planned', '{"duration_min": 54}')
        """
    )
    session_id = session.lastrowid
    conn.commit()
    conn.close()

    seen: dict = {}

    def responder(question: str, context: dict) -> WeeklyCoachReply:
        seen["question"] = question
        seen["context"] = context
        return WeeklyCoachReply(
            answer="Flytt terskeløkta til torsdag så du får mer rom etter helgen.",
            model="deepseek-v4-pro",
            operations=[{
                "action": "move",
                "session_id": session_id,
                "to_date": "2026-07-23",
                "reason": "Mer restitusjon mellom kvalitetsøktene.",
            }],
        )

    async def ask_then_apply() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(
                db_path=path,
                api_token="test-token",
                weekly_coach_responder=responder,
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/weeks/2026-07-20/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Flytt terskeløkta mot slutten av uka"},
            )
            proposal_id = response.json()["proposal"]["id"]
            before = await client.get(
                "/api/week?start=2026-07-20",
                headers={"Authorization": "Bearer test-token"},
            )
            applied = await client.post(
                f"/api/week-proposals/{proposal_id}/apply",
                headers={"Authorization": "Bearer test-token"},
            )
        return response, before, applied

    response, before, applied = asyncio.run(ask_then_apply())

    assert response.status_code == 200
    payload = response.json()
    assert payload["changes_applied"] is False
    assert payload["proposal"]["operations"][0]["before"]["date"] == "2026-07-21"
    assert payload["proposal"]["operations"][0]["after"]["date"] == "2026-07-23"
    assert before.json()["days"][1]["planned_sessions"][0]["date"] == "2026-07-21"
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert seen["question"] == "Flytt terskeløkta mot slutten av uka"
    assert seen["context"]["scope"]["week_start"] == "2026-07-20"

    conn = _db(path)
    try:
        row = conn.execute(
            "SELECT planned_date, status FROM planned_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        proposal_status = conn.execute(
            "SELECT status FROM weekly_plan_proposals"
        ).fetchone()["status"]
    finally:
        conn.close()
    assert dict(row) == {"planned_date": "2026-07-23", "status": "modified"}
    assert proposal_status == "applied"


def test_weekly_coach_discards_hallucinated_or_out_of_week_changes(tmp_path) -> None:
    path = tmp_path / "trening.db"
    conn = _db(path)
    conn.execute(
        """
        INSERT INTO planned_sessions (planned_date, type, description, status)
        VALUES ('2026-07-21', 'easy_run', 'Rolig tur', 'planned')
        """
    )
    conn.commit()
    conn.close()

    def responder(_question: str, _context: dict) -> WeeklyCoachReply:
        return WeeklyCoachReply(
            answer="Jeg ville ikke endret denne uken uten å se mer.",
            model="deepseek-v4-pro",
            operations=[{
                "action": "move",
                "session_id": 99999,
                "to_date": "2026-07-30",
                "reason": "ugyldig",
            }],
        )

    async def ask() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=create_app(db_path=path, api_token="test-token", weekly_coach_responder=responder)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                "/api/weeks/2026-07-20/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Hva tenker du om denne uken?"},
            )

    response = asyncio.run(ask())
    assert response.status_code == 200
    assert response.json()["proposal"] is None

    conn = _db(path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM weekly_plan_proposals").fetchone()["n"] == 0
    finally:
        conn.close()
