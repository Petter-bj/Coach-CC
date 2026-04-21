"""Tester for Hevy-parsing mot fixture fra ekte API-respons."""

from __future__ import annotations

import json
from pathlib import Path

from src.sources.hevy import (
    _duration_sec,
    _epley,
    _local_date_from_utc,
    _parse_iso_to_utc,
    parse_hevy_workout,
)

FIX = Path("tests/fixtures/hevy/raw")


def _load_workout() -> dict:
    data = json.loads((FIX / "workouts_page.json").read_text())
    return data["workouts"][0]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_iso_with_offset() -> None:
    assert _parse_iso_to_utc("2026-04-21T16:22:30+00:00") == "2026-04-21T16:22:30Z"


def test_parse_iso_with_z_suffix() -> None:
    assert _parse_iso_to_utc("2026-04-21T17:04:00.745Z") == "2026-04-21T17:04:00Z"


def test_parse_iso_with_oslo_offset() -> None:
    # 18:22 Oslo (UTC+2 i april) → 16:22 UTC
    assert _parse_iso_to_utc("2026-04-21T18:22:30+02:00") == "2026-04-21T16:22:30Z"


def test_local_date_from_utc_oslo() -> None:
    # 23:30 UTC → 01:30 neste dag i Oslo (sommertid)
    assert _local_date_from_utc("2026-04-21T23:30:00Z", "Europe/Oslo") == "2026-04-22"


def test_duration_sec_basic() -> None:
    # 16:22:30 → 17:03:49 = 41:19 = 2479 sek
    assert _duration_sec("2026-04-21T16:22:30Z", "2026-04-21T17:03:49Z") == 2479


def test_duration_sec_none_end() -> None:
    assert _duration_sec("2026-04-21T16:22:30Z", None) is None


def test_epley_basic() -> None:
    # 100kg × 5 → 100 * (1 + 5/30) = 116.67
    assert _epley(100, 5) == 116.67


def test_epley_zero_weight_is_none() -> None:
    assert _epley(0, 10) is None
    assert _epley(None, 10) is None


def test_epley_zero_reps_is_none() -> None:
    assert _epley(50, 0) is None


# ---------------------------------------------------------------------------
# parse_hevy_workout
# ---------------------------------------------------------------------------


def test_parse_workout_basic_fields() -> None:
    w, sets = parse_hevy_workout(_load_workout())

    assert w["source"] == "hevy"
    assert w["external_id"] == "edb2ff65-0797-46e5-b356-11d62411f031"
    assert w["type"] == "strength_training"
    assert w["started_at_utc"] == "2026-04-21T16:22:30Z"
    assert w["local_date"] == "2026-04-21"
    assert w["duration_sec"] == 2479
    assert "Push" in w["notes"]
    assert "Følte meg sterk" in w["notes"]


def test_parse_workout_sets_numbered_per_exercise() -> None:
    _, sets = parse_hevy_workout(_load_workout())

    # 3 Shoulder Press + 2 Chest Fly = 5 sett (Plank droppes — reps=null)
    assert len(sets) == 5

    shoulder = [s for s in sets if s["exercise"] == "Shoulder Press (Dumbbell)"]
    assert [s["set_num"] for s in shoulder] == [1, 2, 3]
    assert [s["reps"] for s in shoulder] == [6, 5, 4]
    assert [s["weight_kg"] for s in shoulder] == [30, 30, 30]
    assert [s["rpe"] for s in shoulder] == [8, None, 9]

    fly = [s for s in sets if s["exercise"] == "Chest Fly (Machine)"]
    assert [s["set_num"] for s in fly] == [1, 2]


def test_parse_workout_e1rm_computed() -> None:
    _, sets = parse_hevy_workout(_load_workout())
    sh1 = next(s for s in sets if s["exercise"] == "Shoulder Press (Dumbbell)"
               and s["set_num"] == 1)
    # 30 kg × 6 → 30 * (1 + 6/30) = 36.0
    assert sh1["e1rm_kg"] == 36.0


def test_parse_workout_skips_reps_null_sets() -> None:
    """Plank-settet har reps=null og skal droppes (CHECK reps > 0 i DB)."""
    _, sets = parse_hevy_workout(_load_workout())
    assert not any(s["exercise"] == "Plank" for s in sets)


def test_parse_workout_bodyweight_zero_mapped_to_null() -> None:
    # Lag fiksert workout med bodyweight push-ups (weight=0)
    data = {
        "id": "test-001",
        "title": "BW",
        "start_time": "2026-04-21T10:00:00Z",
        "end_time": "2026-04-21T10:15:00Z",
        "exercises": [{
            "title": "Push-up",
            "sets": [{"weight_kg": 0, "reps": 20}],
        }],
    }
    _, sets = parse_hevy_workout(data)
    assert sets[0]["weight_kg"] is None
    assert sets[0]["e1rm_kg"] is None


def test_parse_workout_empty_description_no_dash() -> None:
    data = {
        "id": "test-002",
        "title": "Pull",
        "description": "",
        "start_time": "2026-04-21T10:00:00Z",
        "end_time": "2026-04-21T11:00:00Z",
        "exercises": [],
    }
    w, _ = parse_hevy_workout(data)
    assert w["notes"] == "Økt: Pull"
