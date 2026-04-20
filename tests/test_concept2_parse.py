"""Tests for Concept2 session- og interval-parsing mot fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from src.sources.concept2 import (
    parse_concept2_intervals,
    parse_concept2_session,
)

FIX = Path("tests/fixtures/concept2/raw")


def _load_detail() -> dict:
    data = json.loads((FIX / "result_115541952_detail.json").read_text())
    return data["data"]


def test_parse_session_basic_fields() -> None:
    data = _load_detail()
    workout, details = parse_concept2_session(data)

    assert workout["source"] == "concept2"
    assert workout["external_id"] == "115541952"
    assert workout["type"] == "skierg"
    assert workout["timezone"] == "Europe/Oslo"
    assert workout["local_date"] == "2026-04-18"
    assert workout["duration_sec"] == 1500.0  # time 15000 tenths / 10
    assert workout["distance_m"] == 5310

    assert details["c2_result_id"] == 115541952
    assert details["workout_type"] == "VariableInterval"
    assert details["source"] == "ErgData iOS"
    assert details["stroke_count"] == 1067
    assert details["drag_factor"] == 98
    assert details["verified"] == 1


def test_parse_session_computes_pace_from_distance() -> None:
    data = _load_detail()
    _w, details = parse_concept2_session(data)
    # pace_500 = time_sec / distance * 500 = 1500 / 5310 * 500 ≈ 141.2 sek/500m
    assert details["avg_pace_500m_sec"] is not None
    assert 140 < details["avg_pace_500m_sec"] < 143


def test_parse_session_avg_hr_255_normalized_to_none() -> None:
    """Concept2 bruker 255 som 'no HR data'-sentinel — skal bli None."""
    data = _load_detail()
    # Fixturet har heart_rate={'average': 0, 'min': 0}; avg_hr blir None
    workout, _ = parse_concept2_session(data)
    assert workout["avg_hr"] is None


def test_parse_session_utc_conversion() -> None:
    """started_at_utc skal være 2 timer før local (Europe/Oslo sommertid)."""
    data = _load_detail()
    workout, _ = parse_concept2_session(data)
    # Local: 2026-04-18 11:53:00 → UTC: 2026-04-18 09:53:00Z
    assert workout["started_at_utc"] == "2026-04-18T09:53:00Z"


def test_parse_intervals_count_and_structure() -> None:
    data = _load_detail()
    intervals = parse_concept2_intervals(data)
    assert len(intervals) == 6  # fixturet har 6 intervaller

    # Første intervall
    i0 = intervals[0]
    assert i0["interval_num"] == 0
    assert i0["machine"] == "skierg"
    assert i0["type"] == "time"
    assert i0["distance_m"] == 906
    assert i0["stroke_rate"] == 37

    # Alle HR-verdier i fixturet er 255 → skal normaliseres til None
    for iv in intervals:
        assert iv["hr_min"] is None or iv["hr_min"] != 255


def test_parse_intervals_hr_sentinel_normalized() -> None:
    data = {
        "workout": {
            "intervals": [
                {"machine": "skierg", "type": "time", "time": 3000,
                 "distance": 900, "heart_rate": {"min": 255, "max": 255, "average": 155}},
            ]
        }
    }
    iv = parse_concept2_intervals(data)[0]
    assert iv["hr_min"] is None
    assert iv["hr_max"] is None
    assert iv["hr_avg"] == 155  # ekte verdi beholdes


def test_parse_session_avg_watts_computed() -> None:
    data = _load_detail()
    _w, details = parse_concept2_session(data)
    # 5310m / 1500s = 3.54 m/s; watts = 2.8 * 3.54^3 ≈ 124W
    assert details["avg_watts"] is not None
    assert 100 < details["avg_watts"] < 150
