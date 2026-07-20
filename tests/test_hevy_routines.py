"""Kontrakter for eksplisitt opprettelse av en Hevy-mal."""

from __future__ import annotations

import json

import httpx
import pytest

from src.api.hevy_routines import validate_hevy_routine, validate_hevy_routines
from src.integrations.hevy import HevyRoutineError, create_routine


def test_validate_hevy_routine_normalises_a_small_routine() -> None:
    candidate = validate_hevy_routine({
        "title": "  Fullkropp A  ",
        "notes": "  Kontrollert styrke. ",
        "exercises": [{
            "exercise": " Barbell Squat ",
            "rest_seconds": 120,
            "sets": [{"type": "normal", "weight_kg": 60, "reps": 6}],
        }],
    })

    assert candidate == {
        "title": "Fullkropp A",
        "notes": "Kontrollert styrke.",
        "exercises": [{
            "exercise": "Barbell Squat",
            "rest_seconds": 120,
            "sets": [{"type": "normal", "weight_kg": 60.0, "reps": 6}],
        }],
    }


def test_validate_hevy_routine_rejects_an_invalid_set() -> None:
    assert validate_hevy_routine({
        "title": "Broken",
        "exercises": [{
            "exercise": "Barbell Squat",
            "sets": [{"type": "normal", "reps": 0}],
        }],
    }) is None


def test_validate_hevy_routines_resolves_weekday_and_date_within_week() -> None:
    """«Tirsdag og fredag» gir to maler med riktige datoer i valgt uke."""
    routines = validate_hevy_routines(
        [
            {
                "title": "Fullkropp A",
                "purpose": "Fullkropp A · tirsdag",
                "weekday": "tirsdag",
                "exercises": [{"exercise": "Barbell Squat", "sets": [{"type": "normal", "reps": 6}]}],
            },
            {
                "name": "Fullkropp B",  # name som alias for title
                "date": "2026-07-24",  # fredag
                "exercises": [{"exercise": "Barbell Bench Press", "sets": [{"type": "normal", "reps": 8}]}],
            },
        ],
        week_start="2026-07-20",
    )

    assert len(routines) == 2
    assert routines[0]["routine"]["title"] == "Fullkropp A"
    assert routines[0]["suggested_date"] == "2026-07-21"  # tirsdag
    assert routines[0]["purpose"] == "Fullkropp A · tirsdag"
    assert routines[1]["routine"]["title"] == "Fullkropp B"
    assert routines[1]["suggested_date"] == "2026-07-24"  # fredag


def test_validate_hevy_routines_drops_dates_outside_the_week() -> None:
    routines = validate_hevy_routines(
        [{
            "title": "Utenfor uka",
            "date": "2026-08-01",
            "exercises": [{"exercise": "Barbell Squat", "sets": [{"type": "normal", "reps": 6}]}],
        }],
        week_start="2026-07-20",
    )
    assert len(routines) == 1
    assert routines[0]["suggested_date"] is None


def test_validate_hevy_routines_accepts_a_single_dict_backward_compatible() -> None:
    routines = validate_hevy_routines(
        {
            "title": "Fullkropp A",
            "exercises": [{"exercise": "Barbell Squat", "sets": [{"type": "normal", "reps": 6}]}],
        },
        week_start="2026-07-20",
    )
    assert len(routines) == 1
    assert routines[0]["routine"]["title"] == "Fullkropp A"


def test_create_routine_resolves_templates_then_posts_exact_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEVY_API_KEY", "fake-test-key")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.path == "/v1/exercise_templates"
            assert request.headers["api-key"] == "fake-test-key"
            return httpx.Response(200, json={
                "exercise_templates": [{"id": "squat-id", "title": "Barbell Squat"}],
                "page_count": 1,
            })
        assert request.method == "POST"
        assert request.url.path == "/v1/routines"
        captured["payload"] = json.loads(request.content)
        return httpx.Response(201, json={"routine": {"id": "routine-123"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        routine_id = create_routine({
            "title": "Fullkropp A",
            "notes": "Kontrollert styrke.",
            "exercises": [{
                "exercise": "Barbell Squat",
                "rest_seconds": 120,
                "sets": [{"type": "normal", "weight_kg": 60.0, "reps": 6}],
            }],
        }, http_client=client)
    finally:
        client.close()

    assert routine_id == "routine-123"
    assert captured["payload"] == {
        "routine": {
            "title": "Fullkropp A",
            "folder_id": None,
            "notes": "Kontrollert styrke.",
            "exercises": [{
                "exercise_template_id": "squat-id",
                "rest_seconds": 120,
                "sets": [{"type": "normal", "weight_kg": 60.0, "reps": 6}],
            }],
        }
    }


def test_create_routine_explains_when_an_exercise_cannot_be_matched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEVY_API_KEY", "fake-test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"exercise_templates": [], "page_count": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(HevyRoutineError, match="Fant ikke"):
            create_routine({
                "title": "Fullkropp A",
                "exercises": [{
                    "exercise": "Imaginary lift",
                    "sets": [{"type": "normal", "reps": 6}],
                }],
            }, http_client=client)
    finally:
        client.close()
