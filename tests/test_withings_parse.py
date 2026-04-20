"""Tests for Withings måle-gruppe-parsing."""

from __future__ import annotations

import json
from pathlib import Path

from src.sources.withings import parse_measure_group, _decode

FIX = Path("tests/fixtures/withings/raw")


def _load_measurements():
    return json.loads((FIX / "measurements_last_30d.json").read_text())


def test_decode_weight_example() -> None:
    # Fra fixture: value=74580, unit=-3 → 74.58 kg
    assert _decode(74580, -3) == 74.58


def test_decode_fat_ratio_example() -> None:
    # value=20350, unit=-3 → 20.35 %
    assert _decode(20350, -3) == 20.35


def test_parse_first_group_weight_only() -> None:
    data = _load_measurements()
    body = data["body"]
    fallback_tz = body["timezone"]
    group = body["measuregrps"][0]

    row = parse_measure_group(group, fallback_tz)

    assert row["grpid"] == group["grpid"]
    assert row["measured_at_utc"].endswith("Z")
    assert row["timezone"] == "Europe/Oslo"
    assert row["local_date"].startswith("2026-")
    assert row["weight_kg"] == 74.58
    assert row["fat_ratio_pct"] is None  # første gruppe har bare vekt


def test_parse_group_uses_fallback_timezone_when_missing() -> None:
    group = {
        "grpid": 99999,
        "date": 1776582519,
        "measures": [{"type": 1, "value": 80000, "unit": -3}],
        "deviceid": None,
        "model": None,
        # Ingen "timezone"-felt på gruppa
    }
    row = parse_measure_group(group, "America/New_York")
    assert row["timezone"] == "America/New_York"
    assert row["weight_kg"] == 80.0


def test_parse_group_with_multiple_measures() -> None:
    """Syntetisk body-composition-gruppe med vekt + fett + muskel."""
    group = {
        "grpid": 123,
        "date": 1776582519,
        "timezone": "Europe/Oslo",
        "deviceid": "xx",
        "model": "Body Smart",
        "measures": [
            {"type": 1, "value": 75000, "unit": -3},    # 75 kg weight
            {"type": 6, "value": 22000, "unit": -3},    # 22% fat
            {"type": 8, "value": 16500, "unit": -3},    # 16.5 kg fat mass
            {"type": 76, "value": 36000, "unit": -3},   # 36 kg muscle
            {"type": 88, "value": 3200, "unit": -3},    # 3.2 kg bone
            {"type": 77, "value": 40000, "unit": -3},   # 40 kg hydration
            {"type": 5, "value": 58500, "unit": -3},    # 58.5 kg fat free mass
        ],
    }
    row = parse_measure_group(group, "Europe/Oslo")
    assert row["weight_kg"] == 75.0
    assert row["fat_ratio_pct"] == 22.0
    assert row["fat_mass_kg"] == 16.5
    assert row["muscle_mass_kg"] == 36.0
    assert row["bone_mass_kg"] == 3.2
    assert row["hydration_kg"] == 40.0
    assert row["fat_free_mass_kg"] == 58.5


def test_unknown_meastype_ignored() -> None:
    """Ukjente måletyper skal ikke krasje parsingen."""
    group = {
        "grpid": 42,
        "date": 1776582519,
        "measures": [
            {"type": 1, "value": 70000, "unit": -3},
            {"type": 999, "value": 123, "unit": 0},  # ukjent
        ],
    }
    row = parse_measure_group(group, "Europe/Oslo")
    assert row["weight_kg"] == 70.0


def test_all_7_groups_parse_without_error() -> None:
    """Sanity: alle 7 gruppene i fixturet skal parse uten feil."""
    data = _load_measurements()
    fallback = data["body"]["timezone"]
    for group in data["body"]["measuregrps"]:
        row = parse_measure_group(group, fallback)
        assert "grpid" in row
        assert row["measured_at_utc"]
