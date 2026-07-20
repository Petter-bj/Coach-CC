"""Kontrakter for den smale, kun lesende DeepSeek-coachen."""

from __future__ import annotations

import json

import httpx
import pytest

from src.coaching.deepseek import (
    CoachProviderError,
    CoachUnavailableError,
    ask_deepseek_block_coach,
    ask_deepseek_week_coach,
    ask_deepseek_coach,
)


def test_ask_deepseek_coach_sends_only_given_context_and_uses_v4_pro() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Ta en rolig dag."}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        reply = ask_deepseek_coach(
            "Bør jeg løpe i dag?",
            {"date": "2026-07-19", "today": {"recommendation": "easy"}},
            api_key="test-key",
            http_client=client,
        )
    finally:
        client.close()

    assert reply.answer == "Ta en rolig dag."
    assert reply.model == "deepseek-v4-pro"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["model"] == "deepseek-v4-pro"
    assert captured["body"]["thinking"] == {"type": "enabled"}
    assert captured["body"]["max_tokens"] == 8_000
    assert captured["body"]["stream"] is False
    assert "Bør jeg løpe i dag?" in captured["body"]["messages"][1]["content"]


def test_ask_deepseek_coach_keeps_a_short_conversation_without_repeating_it_in_context() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Da holder vi oss til det."}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        ask_deepseek_coach(
            "Kjenner det ikke i kneet lenger.",
            {
                "date": "2026-07-19",
                "conversation_history": [
                    {"role": "user", "content": "Kneet var stivt."},
                    {"role": "assistant", "content": "Ta en rolig dag."},
                ],
            },
            api_key="test-key",
            http_client=client,
        )
    finally:
        client.close()

    messages = captured["body"]["messages"]
    assert messages[1:3] == [
        {"role": "user", "content": "Kneet var stivt."},
        {"role": "assistant", "content": "Ta en rolig dag."},
    ]
    assert "conversation_history" not in messages[3]["content"]


def test_ask_deepseek_coach_parses_injury_candidate_without_writing() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "answer": "Jeg foreslår å sette leggen til i bedring.",
                "injury_proposal": {
                    "action": "update",
                    "injury_id": 7,
                    "severity": 1,
                    "status": "healing",
                    "notes": "Brukeren oppgir at smerten er borte i hverdagen.",
                },
            })}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        reply = ask_deepseek_coach(
            "Kjenner ikke noe i leggen lenger.",
            {"date": "2026-07-20", "active_injuries": [{"id": 7, "body_part": "Legg", "status": "active", "severity": 2}]},
            api_key="test-key",
            http_client=client,
        )
    finally:
        client.close()

    assert reply.answer == "Jeg foreslår å sette leggen til i bedring."
    assert reply.injury_proposal == {
        "action": "update",
        "injury_id": 7,
        "severity": 1,
        "status": "healing",
        "notes": "Brukeren oppgir at smerten er borte i hverdagen.",
    }


def test_ask_deepseek_coach_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(CoachUnavailableError, match="API-nøkkel"):
        ask_deepseek_coach("Hei", {}, api_key="")


def test_ask_deepseek_coach_hides_provider_failures() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(CoachProviderError, match="kunne ikke nås"):
            ask_deepseek_coach("Hei", {}, api_key="test-key", http_client=client)
    finally:
        client.close()


def test_week_coach_parses_a_structured_candidate_without_writing() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "answer": "Flytt den til torsdag.",
                "operations": [{
                    "action": "move",
                    "session_id": 12,
                    "to_date": "2026-07-23",
                    "reason": "Mer rom.",
                }],
                "hevy_routine": {
                    "title": "Fullkropp A",
                    "notes": "Kontrollert styrke.",
                    "exercises": [{
                        "exercise": "Barbell Squat",
                        "rest_seconds": 120,
                        "sets": [{"type": "normal", "weight_kg": 60, "reps": 6}],
                    }],
                },
                "injury_proposal": {
                    "action": "update",
                    "injury_id": 7,
                    "status": "resolved",
                    "severity": 1,
                    "notes": "Brukeren oppgir at plagen er borte.",
                },
            })}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        reply = ask_deepseek_week_coach(
            "Kan du flytte økta?",
            {"scope": {"week_start": "2026-07-20"}},
            api_key="test-key",
            http_client=client,
        )
    finally:
        client.close()

    assert reply.answer == "Flytt den til torsdag."
    assert reply.operations == [{
        "action": "move",
        "session_id": 12,
        "to_date": "2026-07-23",
        "reason": "Mer rom.",
    }]
    assert reply.hevy_routine == {
        "title": "Fullkropp A",
        "notes": "Kontrollert styrke.",
        "exercises": [{
            "exercise": "Barbell Squat",
            "rest_seconds": 120,
            "sets": [{"type": "normal", "weight_kg": 60, "reps": 6}],
        }],
    }
    assert reply.injury_proposal == {
        "action": "update",
        "injury_id": 7,
        "status": "resolved",
        "severity": 1,
        "notes": "Brukeren oppgir at plagen er borte.",
    }


def test_week_coach_parses_multiple_hevy_routines() -> None:
    """Den nye liste-formen gir flere mal-kandidater i ett svar."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "answer": "To fullkropp-maler, tirsdag og fredag.",
                "operations": [],
                "hevy_routines": [
                    {
                        "title": "Fullkropp A",
                        "purpose": "Fullkropp A · tirsdag",
                        "date": "2026-07-21",
                        "exercises": [{"exercise": "Barbell Squat", "sets": [{"type": "normal", "reps": 6}]}],
                    },
                    {
                        "title": "Fullkropp B",
                        "weekday": "fredag",
                        "exercises": [{"exercise": "Barbell Bench Press", "sets": [{"type": "normal", "reps": 8}]}],
                    },
                ],
            })}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        reply = ask_deepseek_week_coach(
            "Lag Hevy-maler for fullkropp tirsdag og fredag",
            {"scope": {"week_start": "2026-07-20"}},
            api_key="test-key",
            http_client=client,
        )
    finally:
        client.close()

    assert len(reply.hevy_routines) == 2
    assert [routine["title"] for routine in reply.hevy_routines] == ["Fullkropp A", "Fullkropp B"]
    # Enkelt-feltet er ikke satt når modellen bruker liste-formen.
    assert reply.hevy_routine is None


def test_block_coach_parses_a_structured_candidate_without_writing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "enabled"}
        assert "BLOKKONTEKST" in body["messages"][-1]["content"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "answer": "Dette er et utkast.",
                "proposal": {
                    "action": "create",
                    "name": "4 uker tilbake i rytme",
                    "phase": "base",
                    "start_date": "2026-07-20",
                    "goal": "Stabil løping",
                    "notes": "Rolig start.",
                    "weeks": [{"focus": "Rytme", "is_deload": False}],
                },
            })}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        reply = ask_deepseek_block_coach(
            "Lag en blokk",
            {"current_block": {"is_example": True}},
            api_key="test-key",
            http_client=client,
        )
    finally:
        client.close()

    assert reply.answer == "Dette er et utkast."
    assert reply.proposal is not None
    assert reply.proposal["name"] == "4 uker tilbake i rytme"
