"""Tester pure Garmin-parsers mot faktiske fixture-payloads fra spike-kjøringen.

Disse funksjonene skal være deterministisk og teste uten nettverkskall.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.sources.garmin import (
    parse_garmin_activity,
    parse_garmin_daily,
    parse_garmin_hrv,
    parse_garmin_sleep,
)

FIX = Path("tests/fixtures/garmin/raw")


def _load(name: str):
    return json.loads((FIX / name).read_text())


# ---------------------------------------------------------------------------
# Daily aggregator
# ---------------------------------------------------------------------------


def test_parse_daily_aggregates_from_multiple_endpoints() -> None:
    row = parse_garmin_daily(
        local_date="2026-04-19",
        rhr=_load("daily_rhr_day.json"),
        body_battery=_load("daily_body_battery.json"),
        training_readiness=_load("daily_training_readiness.json"),
        max_metrics=_load("daily_max_metrics.json"),
        spo2=_load("daily_spo2.json"),
        stress=_load("daily_stress.json"),
        user_summary=_load("daily_user_summary.json"),
        intensity_minutes=_load("daily_intensity_minutes.json"),
    )
    assert row["local_date"] == "2026-04-19"
    assert row["resting_hr"] == 48  # verifisert fra fixture
    assert row["vo2max"] == 50.2  # vo2MaxPreciseValue
    assert row["steps"] == 11644
    assert row["total_calories"] == 2870
    assert row["active_calories"] == 655
    assert row["bmr_calories"] == 2215
    assert row["step_goal"] == 10000
    # Training readiness er en liste — vi tar siste (index -1)
    assert row["training_readiness_score"] is not None
    assert row["training_readiness_level"] in (
        "LOW", "MODERATE", "GOOD", "HIGH", "MAXIMUM", "UNKNOWN", None
    )


def test_parse_daily_handles_all_none_gracefully() -> None:
    row = parse_garmin_daily(
        local_date="2026-01-01",
        rhr=None, body_battery=None, training_readiness=None,
        max_metrics=None, spo2=None, stress=None,
        user_summary=None, intensity_minutes=None,
    )
    assert row["local_date"] == "2026-01-01"
    assert row["resting_hr"] is None


# ---------------------------------------------------------------------------
# Sleep
# ---------------------------------------------------------------------------


def test_parse_sleep_extracts_stages_and_score() -> None:
    row = parse_garmin_sleep(_load("daily_sleep.json"))
    assert row is not None
    assert row["local_date"] == "2026-04-19"
    assert row["duration_sec"] == 27540
    assert row["deep_sec"] == 4200
    assert row["light_sec"] == 18060
    assert row["rem_sec"] == 5280
    assert row["awake_sec"] == 2220
    assert row["sleep_score"] == 81
    assert row["sleep_score_qualifier"] == "GOOD"
    assert row["avg_respiration"] == 13.0
    assert row["lowest_respiration"] == 6.0
    assert row["sleep_from_device"] == 1
    # Tidsstempel konvertert fra ms til ISO
    assert row["sleep_start_utc"].startswith("2026-04-1")
    assert row["sleep_start_utc"].endswith("Z")


def test_parse_sleep_returns_none_for_empty() -> None:
    assert parse_garmin_sleep({}) is None
    assert parse_garmin_sleep({"dailySleepDTO": {}}) is None


# ---------------------------------------------------------------------------
# HRV
# ---------------------------------------------------------------------------


def test_parse_hrv_summary() -> None:
    row = parse_garmin_hrv(_load("daily_hrv.json"))
    assert row is not None
    assert row["local_date"] == "2026-04-19"
    assert row["last_night_avg_ms"] == 74
    assert row["weekly_avg_ms"] == 76
    assert row["last_night_5min_high_ms"] == 99
    assert row["status"] == "NONE"
    assert row["feedback_phrase"] == "ONBOARDING_1"


def test_parse_hrv_baseline_none_for_new_user() -> None:
    """User har nylig fått klokke — baseline er null i 3 uker."""
    row = parse_garmin_hrv(_load("daily_hrv.json"))
    assert row["baseline_low_upper"] is None
    assert row["baseline_balanced_low"] is None
    assert row["baseline_balanced_upper"] is None


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


def test_parse_activity_first_running() -> None:
    activities = _load("activities_latest_5.json")
    running = next(a for a in activities if a["activityType"]["typeKey"] == "running")
    workout, details = parse_garmin_activity(running)

    assert workout["external_id"] == str(running["activityId"])
    assert workout["source"] == "garmin"
    assert workout["type"] == "running"
    assert workout["started_at_utc"].endswith("Z")
    assert workout["local_date"].startswith("2026-")
    assert workout["distance_m"] == running["distance"]
    assert workout["duration_sec"] == int(running["duration"])

    assert details["garmin_activity_id"] == running["activityId"]
    assert details["activity_name"] == running["activityName"]
    assert details["start_latitude"] == running["startLatitude"]
    assert details["has_polyline"] in (0, 1)
    assert details["raw_json"]  # JSON er serialisert


def test_parse_activity_indoor_rowing_type_preserved() -> None:
    """Garmin klassifiserer Concept2-økter som indoor_rowing."""
    activities = _load("activities_latest_5.json")
    rowing = next(a for a in activities if a["activityType"]["typeKey"] == "indoor_rowing")
    workout, _details = parse_garmin_activity(rowing)
    assert workout["type"] == "indoor_rowing"
    # indoor_rowing har distance 0 (Garmin registrerer ikke avstand)
    assert workout["distance_m"] == 0.0
