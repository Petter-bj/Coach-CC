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
