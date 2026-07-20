"""Kontrakter for blokk-oversikt og trygge forslag fra ukecoachen."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date

import httpx

from src.api.app import create_app
from src.api.blocks import build_block_payload
from src.api.hevy_routines import create_hevy_routine_proposal
from src.api.week import build_week_overview
from src.api.week_planning import build_week_coach_context
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


def test_week_overview_exposes_the_matching_block_week() -> None:
    conn = _db(":memory:")
    try:
        block_id = conn.execute(
            """
            INSERT INTO training_blocks (name, phase, start_date, end_date)
            VALUES ('6 uker · Stabil løpsrytme', 'base', '2026-07-20', '2026-08-30')
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO training_block_weeks (
                training_block_id, week_start, focus, progression_note,
                planned_volume_note, is_deload
            ) VALUES (?, '2026-07-20', 'Rytme og toleranse', 'Hold det rolig.', '3 rolige økter', 0)
            """,
            (block_id,),
        )
        payload = build_week_overview(conn, date(2026, 7, 20))
        before_block = build_week_overview(conn, date(2026, 7, 13))
    finally:
        conn.close()

    assert payload["block_context"] == {
        "id": block_id,
        "name": "6 uker · Stabil løpsrytme",
        "phase": "base",
        "week_number": 1,
        "total_weeks": 6,
        "focus": "Rytme og toleranse",
        "progression_note": "Hold det rolig.",
        "planned_volume_note": "3 rolige økter",
        "is_deload": False,
    }
    assert before_block["block_context"] is None


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


def test_week_coach_context_exposes_today_and_named_days() -> None:
    """Konteksten skal gi modellen i-dag, valgt uke og norske ukedag/dato-par."""
    conn = _db(":memory:")
    try:
        context = build_week_coach_context(
            conn, date(2026, 7, 20), question="Lag Hevy-maler tirsdag og fredag",
            today=date(2026, 7, 22),
        )
    finally:
        conn.close()

    scope = context["scope"]
    assert scope["today"] == "2026-07-22"
    assert scope["week_start"] == "2026-07-20"
    assert scope["week_end"] == "2026-07-26"
    assert scope["iso_week"] == date(2026, 7, 20).isocalendar().week
    assert scope["relation_to_today"] == "current"

    # Alle syv dager med norsk ukedagsnavn og ISO-dato, i rekkefølge.
    days = context["days"]
    assert [day["weekday"] for day in days] == [
        "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag",
    ]
    assert days[1]["date"] == "2026-07-21"  # tirsdag
    assert days[4]["date"] == "2026-07-24"  # fredag
    assert days[2]["is_today"] is True  # onsdag 22.


def test_week_coach_context_includes_knowledge_without_leaking_claude_md() -> None:
    """Modellen får coach-kjerne + relevante moduler, men aldri CLI/MCP-instrukser."""
    conn = _db(":memory:")
    try:
        context = build_week_coach_context(
            conn, date(2026, 7, 20), question="Lag en Hevy-mal for fullkropp",
        )
    finally:
        conn.close()

    policy = context["coaching_policy"]
    assert policy["surface"] == "week"
    assert policy["core"]  # kjernen er alltid med
    module_names = {module["module"] for module in policy["modules"]}
    # Styrkespørsmål på ukeflaten → planlegging + styrke er med.
    assert "planning/phases_and_priority.md" in module_names
    assert any(name.startswith("strength/") for name in module_names)
    assert "strength_context" in context
    assert context["strength_context"]["initialized"] is False
    assert context["block"]["strength_structure"]["sessions_per_week"] == 3

    # Ingen operative CLI-/MCP-/Claude Code-instrukser skal lekke inn.
    blob = json.dumps(context, ensure_ascii=False).lower()
    for forbidden in ("uv run", "src.cli", "hevy mcp", "launchd", "claude code", "mcp-verktøy"):
        assert forbidden not in blob


def test_week_coach_running_question_excludes_strength_context() -> None:
    """En løpeforespørsel får løpepolicy, ikke hele styrkeprofilen."""
    conn = _db(":memory:")
    try:
        context = build_week_coach_context(
            conn, date(2026, 7, 20), question="Bør jeg løpe terskel eller rolig i morgen?",
        )
    finally:
        conn.close()

    module_names = {
        module["module"] for module in context["coaching_policy"]["modules"]
    }
    assert "strength_context" not in context
    assert not any(name.startswith("strength/") for name in module_names)
    assert "running/zones_and_distribution.md" in module_names


def test_weekly_coach_creates_two_hevy_routines_for_two_days(tmp_path) -> None:
    """«Fullkropp tirsdag og fredag» → to forslag med riktige datoer, og bare den
    bekreftede malen opprettes i Hevy."""
    path = tmp_path / "trening.db"
    conn = _db(path)
    conn.close()
    created: list[dict] = []

    def responder(question: str, context: dict) -> WeeklyCoachReply:
        # Modellen kjenner ukens datoer fra konteksten og spør aldri om dato.
        assert context["scope"]["week_start"] == "2026-07-20"
        return WeeklyCoachReply(
            answer="To fullkropp-maler, tirsdag og fredag.",
            model="deepseek-v4-pro",
            operations=[],
            hevy_routines=[
                {
                    "title": "Fullkropp A",
                    "purpose": "Fullkropp A · tirsdag",
                    "weekday": "tirsdag",
                    "exercises": [{"exercise": "Barbell Squat", "sets": [{"type": "normal", "reps": 6}]}],
                },
                {
                    "title": "Fullkropp B",
                    "purpose": "Fullkropp B · fredag",
                    "weekday": "fredag",
                    "exercises": [{"exercise": "Barbell Bench Press", "sets": [{"type": "normal", "reps": 8}]}],
                },
            ],
        )

    def create_in_hevy(routine: dict) -> str:
        created.append(routine)
        return f"hevy-{routine['title']}"

    async def ask_then_apply_one():
        transport = httpx.ASGITransport(
            app=create_app(
                db_path=path,
                api_token="test-token",
                weekly_coach_responder=responder,
                hevy_routine_creator=create_in_hevy,
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            proposed = await client.post(
                "/api/weeks/2026-07-20/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Lag Hevy-maler for fullkropp tirsdag og fredag"},
            )
            first_id = proposed.json()["hevy_proposals"][0]["id"]
            applied = await client.post(
                f"/api/hevy-routine-proposals/{first_id}/apply",
                headers={"Authorization": "Bearer test-token"},
            )
        return proposed, applied

    proposed, applied = asyncio.run(ask_then_apply_one())

    body = proposed.json()
    assert body["changes_applied"] is False
    # To forslag med riktige datoer i valgt uke.
    assert len(body["hevy_proposals"]) == 2
    assert body["hevy_proposals"][0]["routine"]["title"] == "Fullkropp A"
    assert body["hevy_proposals"][0]["suggested_date"] == "2026-07-21"
    assert body["hevy_proposals"][0]["weekday"] == "tirsdag"
    assert body["hevy_proposals"][1]["suggested_date"] == "2026-07-24"
    # Bakoverkompatibelt enkelt-felt peker på første forslag.
    assert body["hevy_proposal"]["id"] == body["hevy_proposals"][0]["id"]

    # Ingen Hevy-kall skjedde før bekreftelse, og bare den bekreftede malen
    # ble opprettet.
    assert applied.status_code == 200
    assert [routine["title"] for routine in created] == ["Fullkropp A"]

    # Den andre malen står fortsatt som pending, urørt.
    conn = _db(path)
    try:
        statuses = {
            row["suggested_date"]: row["status"]
            for row in conn.execute(
                "SELECT suggested_date, status FROM hevy_routine_proposals ORDER BY id"
            ).fetchall()
        }
    finally:
        conn.close()
    assert statuses == {"2026-07-21": "applied", "2026-07-24": "pending"}


def test_weekly_coach_requires_confirmation_before_creating_hevy_routine(tmp_path) -> None:
    path = tmp_path / "trening.db"
    conn = _db(path)
    conn.close()
    created: list[dict] = []

    def responder(_question: str, _context: dict) -> WeeklyCoachReply:
        return WeeklyCoachReply(
            answer="Her er en mal du kan opprette i Hevy.",
            model="deepseek-v4-pro",
            operations=[],
            hevy_routine={
                "title": "Fullkropp A",
                "notes": "Kontrollert styrke.",
                "exercises": [{
                    "exercise": "Barbell Squat",
                    "rest_seconds": 120,
                    "sets": [
                        {"type": "normal", "weight_kg": 60, "reps": 6},
                        {"type": "normal", "weight_kg": 60, "reps": 6},
                    ],
                }],
            },
        )

    def create_in_hevy(routine: dict) -> str:
        created.append(routine)
        return "hevy-routine-123"

    async def ask_then_apply() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(
                db_path=path,
                api_token="test-token",
                weekly_coach_responder=responder,
                hevy_routine_creator=create_in_hevy,
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            proposed = await client.post(
                "/api/weeks/2026-07-20/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Lag en Hevy-mal for fullkropp."},
            )
            proposal_id = proposed.json()["hevy_proposal"]["id"]
            applied = await client.post(
                f"/api/hevy-routine-proposals/{proposal_id}/apply",
                headers={"Authorization": "Bearer test-token"},
            )
        return proposed, applied

    proposed, applied = asyncio.run(ask_then_apply())

    assert proposed.status_code == 200
    assert proposed.json()["proposal"] is None
    assert proposed.json()["hevy_proposal"]["routine"]["title"] == "Fullkropp A"
    assert created == [{
        "title": "Fullkropp A",
        "notes": "Kontrollert styrke.",
        "exercises": [{
            "exercise": "Barbell Squat",
            "rest_seconds": 120,
            "sets": [
                {"type": "normal", "weight_kg": 60.0, "reps": 6},
                {"type": "normal", "weight_kg": 60.0, "reps": 6},
            ],
        }],
    }]
    assert applied.status_code == 200
    assert applied.json() == {
        "id": proposed.json()["hevy_proposal"]["id"],
        "status": "applied",
        "hevy_routine_id": "hevy-routine-123",
    }

    conn = _db(path)
    try:
        row = conn.execute(
            "SELECT status, hevy_routine_id FROM hevy_routine_proposals"
        ).fetchone()
    finally:
        conn.close()
    assert dict(row) == {"status": "applied", "hevy_routine_id": "hevy-routine-123"}


def test_user_can_confirm_a_hevy_routine_seen_after_a_missing_receipt(tmp_path) -> None:
    """Reserveknappen lukker bare et fortsatt ventende forslag."""
    path = tmp_path / "trening.db"
    conn = _db(path)
    proposal = create_hevy_routine_proposal(
        conn,
        week_start="2026-07-20",
        question="Lag en Hevy-mal.",
        coach_answer="Her er malen.",
        routine={
            "title": "Fullkropp A",
            "exercises": [{"exercise": "Barbell Squat", "sets": [{"type": "normal", "reps": 6}]}],
        },
    )
    conn.commit()
    conn.close()

    async def confirm() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(db_path=path, api_token="test-token"))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                f"/api/hevy-routine-proposals/{proposal['id']}/confirm-created",
                headers={"Authorization": "Bearer test-token"},
            )

    response = asyncio.run(confirm())

    assert response.status_code == 200
    assert response.json() == {
        "id": proposal["id"],
        "status": "applied",
        "hevy_routine_id": "confirmed-in-hevy",
    }
    conn = _db(path)
    try:
        row = conn.execute(
            "SELECT status, hevy_routine_id FROM hevy_routine_proposals WHERE id = ?",
            (proposal["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert dict(row) == {"status": "applied", "hevy_routine_id": "confirmed-in-hevy"}


def test_weekly_coach_returns_separate_hevy_and_injury_proposals(tmp_path) -> None:
    """Én melding kan gi både en Hevy-mal og en brukergodkjent helsestatus."""
    path = tmp_path / "trening.db"
    conn = _db(path)
    injury_id = conn.execute(
        """
        INSERT INTO injuries (body_part, severity, started_at, status, notes)
        VALUES ('IT-band-syndrom', 2, '2026-07-10', 'active', 'Tidligere smerte ved løping.')
        """
    ).lastrowid
    conn.commit()
    conn.close()
    created: list[dict] = []

    def responder(_question: str, context: dict) -> WeeklyCoachReply:
        assert context["active_injuries"] == [{
            "id": injury_id,
            "body_part": "IT-band-syndrom",
            "severity": 2,
            "started_at": "2026-07-10",
            "status": "active",
            "notes": "Tidligere smerte ved løping.",
        }]
        return WeeklyCoachReply(
            answer="Her er en Hevy-mal og et forslag om å avslutte IT-band-statusen.",
            model="deepseek-v4-pro",
            operations=[],
            hevy_routine={
                "title": "Fullkropp A",
                "exercises": [{
                    "exercise": "Barbell Squat",
                    "sets": [{"type": "normal", "weight_kg": 60, "reps": 6}],
                }],
            },
            injury_proposal={
                "action": "update",
                "injury_id": injury_id,
                "status": "resolved",
                "severity": 1,
                "notes": "Brukeren oppgir at IT-band-plagene er borte.",
            },
        )

    async def ask_then_confirm_injury() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(
                db_path=path,
                api_token="test-token",
                weekly_coach_responder=responder,
                hevy_routine_creator=lambda routine: created.append(routine) or "hevy-1",
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            proposed = await client.post(
                "/api/weeks/2026-07-20/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Lag en Hevy-mal. IT-band-problemet er borte nå."},
            )
            injury_proposal_id = proposed.json()["injury_proposal"]["id"]
            applied = await client.post(
                f"/api/injury-proposals/{injury_proposal_id}/apply",
                headers={"Authorization": "Bearer test-token"},
            )
        return proposed, applied

    proposed, applied = asyncio.run(ask_then_confirm_injury())

    assert proposed.status_code == 200
    body = proposed.json()
    assert body["hevy_proposal"]["routine"]["title"] == "Fullkropp A"
    assert body["injury_proposal"] == {
        "id": body["injury_proposal"]["id"],
        "status": "pending",
        "injury": {
            "action": "update",
            "injury_id": injury_id,
            "body_part": "IT-band-syndrom",
            "from_status": "active",
            "from_severity": 2,
            "status": "resolved",
            "severity": 1,
            "notes": "Brukeren oppgir at IT-band-plagene er borte.",
            "reported_on": date.today().isoformat(),
        },
    }
    # Forslagene er uavhengige. Å godkjenne helsestatus skal ikke opprette
    # Hevy-malen, og det skjer ingen Hevy-skriving før dens eget grønne klikk.
    assert created == []
    assert applied.status_code == 200
    assert applied.json()["injury"]["status"] == "resolved"

    conn = _db(path)
    try:
        status = conn.execute(
            "SELECT status FROM injuries WHERE id = ?", (injury_id,)
        ).fetchone()["status"]
        hevy_status = conn.execute(
            "SELECT status FROM hevy_routine_proposals"
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "resolved"
    assert hevy_status == "pending"


def test_hevy_proposal_can_be_discussed_without_changing_or_creating_it(tmp_path) -> None:
    """Et spørsmål om ett kort får rutinen som kontekst, men er aldri en write."""
    path = tmp_path / "trening.db"
    conn = _db(path)
    conn.close()
    seen: dict = {}
    created: list[dict] = []

    routine = {
        "title": "Fullkropp A",
        "notes": "Kontrollert styrke.",
        "exercises": [{
            "exercise": "Barbell Squat",
            "sets": [{"type": "normal", "weight_kg": 60, "reps": 6}],
        }],
    }

    def responder(question: str, context: dict) -> WeeklyCoachReply:
        if "hevy_proposal_under_discussion" in context:
            seen["question"] = question
            seen["context"] = context
            return WeeklyCoachReply(
                answer="Tre sett gir nok arbeid uten at denne økta blir unødvendig tung.",
                model="deepseek-v4-pro",
                operations=[],
                hevy_routines=[],
            )
        return WeeklyCoachReply(
            answer="Her er et forslag.",
            model="deepseek-v4-pro",
            operations=[],
            hevy_routine=routine,
        )

    async def propose_then_ask() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(
                db_path=path,
                api_token="test-token",
                weekly_coach_responder=responder,
                hevy_routine_creator=lambda item: created.append(item) or "hevy-1",
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            proposed = await client.post(
                "/api/weeks/2026-07-20/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Lag en Hevy-mal for fullkropp"},
            )
            proposal_id = proposed.json()["hevy_proposal"]["id"]
            discussed = await client.post(
                f"/api/hevy-routine-proposals/{proposal_id}/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Hvorfor er det tre sett?"},
            )
        return proposed, discussed

    proposed, discussed = asyncio.run(propose_then_ask())

    assert proposed.status_code == 200
    assert discussed.status_code == 200
    assert discussed.json() == {
        "answer": "Tre sett gir nok arbeid uten at denne økta blir unødvendig tung.",
        "model": "deepseek-v4-pro",
        "changes_applied": False,
        "replacement": None,
    }
    assert seen["question"] == "Hvorfor er det tre sett?"
    assert seen["context"]["hevy_proposal_under_discussion"] == {
        "routine": routine,
        "suggested_date": None,
        "purpose": None,
        "status": "pending",
    }
    assert created == []

    conn = _db(path)
    try:
        rows = conn.execute(
            "SELECT status, routine_json FROM hevy_routine_proposals ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert json.loads(rows[0]["routine_json"]) == routine


def test_hevy_proposal_adjustment_replaces_draft_and_still_requires_confirmation(tmp_path) -> None:
    """En justering forkaster originalutkastet, men skriver ikke til Hevy før klikk."""
    path = tmp_path / "trening.db"
    conn = _db(path)
    conn.close()
    created: list[dict] = []

    original = {
        "title": "Fullkropp A",
        "notes": None,
        "exercises": [{
            "exercise": "Barbell Squat",
            "sets": [{"type": "normal", "weight_kg": 60, "reps": 6}],
        }],
    }
    replacement = {
        "title": "Fullkropp A",
        "purpose": "Knevennlig fullkropp · tirsdag",
        "weekday": "tirsdag",
        "exercises": [{
            "exercise": "Leg Press",
            "sets": [{"type": "normal", "weight_kg": 90, "reps": 8}],
        }],
    }

    def responder(_question: str, context: dict) -> WeeklyCoachReply:
        if "hevy_proposal_under_discussion" in context:
            assert context["hevy_proposal_under_discussion"]["routine"] == original
            return WeeklyCoachReply(
                answer="Jeg har byttet squat med leg press og beholdt tirsdagen.",
                model="deepseek-v4-pro",
                operations=[],
                hevy_routines=[replacement],
            )
        return WeeklyCoachReply(
            answer="Her er et forslag.",
            model="deepseek-v4-pro",
            operations=[],
            hevy_routine=original,
        )

    def create_in_hevy(routine: dict) -> str:
        created.append(routine)
        return "hevy-leg-press"

    async def propose_adjust_then_apply() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(
            app=create_app(
                db_path=path,
                api_token="test-token",
                weekly_coach_responder=responder,
                hevy_routine_creator=create_in_hevy,
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            proposed = await client.post(
                "/api/weeks/2026-07-20/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Lag en Hevy-mal for fullkropp"},
            )
            original_id = proposed.json()["hevy_proposal"]["id"]
            adjusted = await client.post(
                f"/api/hevy-routine-proposals/{original_id}/coach",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Bytt squat med leg press"},
            )
            # En ny pending-mal er fortsatt bare en lokal kandidat.
            assert created == []
            replacement_id = adjusted.json()["replacement"]["id"]
            applied = await client.post(
                f"/api/hevy-routine-proposals/{replacement_id}/apply",
                headers={"Authorization": "Bearer test-token"},
            )
        return proposed, adjusted, applied

    proposed, adjusted, applied = asyncio.run(propose_adjust_then_apply())

    assert proposed.status_code == 200
    assert adjusted.status_code == 200
    body = adjusted.json()
    assert body["changes_applied"] is False
    assert body["replacement"]["routine"]["exercises"][0]["exercise"] == "Leg Press"
    assert body["replacement"]["suggested_date"] == "2026-07-21"
    assert created == [{
        "title": "Fullkropp A",
        "notes": None,
        "exercises": [{
            "exercise": "Leg Press",
            "sets": [{"type": "normal", "weight_kg": 90.0, "reps": 8}],
        }],
    }]
    assert applied.status_code == 200

    conn = _db(path)
    try:
        rows = conn.execute(
            "SELECT status, routine_json FROM hevy_routine_proposals ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert [row["status"] for row in rows] == ["discarded", "applied"]
    assert json.loads(rows[0]["routine_json"]) == original
    assert json.loads(rows[1]["routine_json"])["exercises"][0]["exercise"] == "Leg Press"
