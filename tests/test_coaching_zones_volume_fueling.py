"""Tester for sone-klassifisering, volum-sanity og fueling-regler."""

from __future__ import annotations

import pytest

from src.coaching.philosophy import (
    classify_run_zone,
    fueling_recommendation,
    sleep_readiness_flag,
    weekly_intensity_distribution,
    weekly_strength_volume_check,
)


# ---------------------------------------------------------------------------
# classify_run_zone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("avg_hr,hr_max,expected", [
    (130, 200, "Z1"),   # 65%   — restitusjon
    (140, 200, "Z1"),   # 70%   — restitusjon (< 72% grense)
    (150, 200, "Z2"),   # 75%   — lett aerob
    (160, 200, "Z2"),   # 80%   — lett aerob
    (165, 200, "Z3"),   # 82.5% — golden zone
    (172, 200, "Z3"),   # 86%   — golden zone
    (176, 200, "Z4"),   # 88%   — anaerob terskel (grey)
    (183, 200, "Z4"),   # 91.5% — anaerob terskel (grey)
    (185, 200, "Z5"),   # 92.5% — VO2max
    (195, 200, "Z5"),   # 97.5% — VO2max
])
def test_zone_classification(avg_hr, hr_max, expected):
    assert classify_run_zone(avg_hr, hr_max) == expected


def test_zone_classification_missing_data():
    assert classify_run_zone(None, 200) is None
    assert classify_run_zone(150, None) is None
    assert classify_run_zone(150, 0) is None


# ---------------------------------------------------------------------------
# weekly_intensity_distribution
# ---------------------------------------------------------------------------


def test_distribution_healthy_pyramidal():
    """80% Z1-Z2 + 20% Z3 = ideal pyramidal (Bakken)."""
    sessions = [
        {"distance_m": 10000, "zone": "Z2"},
        {"distance_m": 10000, "zone": "Z2"},
        {"distance_m": 10000, "zone": "Z2"},
        {"distance_m": 10000, "zone": "Z1"},
        {"distance_m": 10000, "zone": "Z3"},
    ]
    result = weekly_intensity_distribution(sessions)
    assert result["total_km"] == 50.0
    assert result["z1_pct"] == 0.2
    assert result["z2_pct"] == 0.6
    assert result["z3_pct"] == 0.2
    assert result["aerobic_pct"] == 1.0  # Z1+Z2+Z3 = 100%
    # Ingen flagg
    assert result["flags"] == []


def test_distribution_too_much_z4_grey_zone():
    sessions = [
        {"distance_m": 30000, "zone": "Z2"},
        {"distance_m": 10000, "zone": "Z4"},  # 25% i Z4 → flagg
    ]
    result = weekly_intensity_distribution(sessions)
    flags = " ".join(result["flags"])
    assert "z4_share_high" in flags


def test_distribution_aerobic_share_too_low():
    """Bare 60% aerobic (Z1+Z2) = ikke nok lett løping."""
    sessions = [
        {"distance_m": 12000, "zone": "Z2"},
        {"distance_m": 8000, "zone": "Z3"},
    ]
    result = weekly_intensity_distribution(sessions)
    flags = " ".join(result["flags"])
    assert "low_aerobic_share" in flags


def test_distribution_empty():
    result = weekly_intensity_distribution([])
    assert result["total_km"] == 0.0
    assert result["flags"] == ["no_run_data"]


def test_distribution_ignores_unclassified():
    sessions = [
        {"distance_m": 10000, "zone": "Z2"},
        {"distance_m": 5000, "zone": None},  # f.eks. økt uten HR-data
    ]
    result = weekly_intensity_distribution(sessions)
    assert result["total_km"] == 10.0  # 5k droppes


# ---------------------------------------------------------------------------
# weekly_strength_volume_check
# ---------------------------------------------------------------------------


def test_volume_in_range_all_good():
    result = weekly_strength_volume_check({
        "chest": 8, "back": 9, "shoulders": 6,
    })
    assert result["in_range"] == ["chest", "back", "shoulders"]
    assert result["under"] == []
    assert result["over"] == []
    assert result["flags"] == []


def test_volume_under_stimulus_flagged():
    result = weekly_strength_volume_check({
        "chest": 2, "back": 8,
    })
    assert result["under"] == ["chest"]
    assert "under_stimulus" in " ".join(result["flags"])


def test_volume_over_volume_flagged():
    result = weekly_strength_volume_check({
        "shoulders": 15, "chest": 8,
    })
    assert result["over"] == ["shoulders"]
    assert "over_volume" in " ".join(result["flags"])


def test_volume_boundary_10_is_ok():
    result = weekly_strength_volume_check({"chest": 10, "legs": 4})
    assert result["in_range"] == ["chest", "legs"]


# ---------------------------------------------------------------------------
# fueling_recommendation
# ---------------------------------------------------------------------------


def test_fueling_short_session_no_recommendation():
    result = fueling_recommendation(45, "hard")
    assert result["carb_per_hour_g"] is None
    assert "< 60 min" in result["reasoning"]


def test_fueling_race_90_to_120_per_hour():
    result = fueling_recommendation(90, "race")
    low, high = result["carb_per_hour_g"]
    assert low == 90
    assert high == 120
    # 90 min = 1.5 timer
    assert result["total_carb_g"] == (135, 180)


def test_fueling_hard_60_to_90_per_hour():
    result = fueling_recommendation(120, "hard")
    low, high = result["carb_per_hour_g"]
    assert low == 60
    assert high == 90
    # 2 timer
    assert result["total_carb_g"] == (120, 180)


def test_fueling_moderate_lower_range():
    result = fueling_recommendation(75, "moderate")
    low, high = result["carb_per_hour_g"]
    assert (low, high) == (30, 60)


def test_fueling_easy_optional():
    result = fueling_recommendation(120, "easy")
    low, _ = result["carb_per_hour_g"]
    assert low == 0  # fueling valgfritt
    assert "depletion" in result["reasoning"]


# ---------------------------------------------------------------------------
# sleep_readiness_flag
# ---------------------------------------------------------------------------


def test_sleep_good_no_flag():
    assert sleep_readiness_flag(last_night_h=8.0, cumulative_7d_h=56.0) is None


def test_sleep_one_bad_night_soft_flag():
    flag = sleep_readiness_flag(last_night_h=5.5, cumulative_7d_h=50.0)
    assert flag is not None
    assert flag["severity"] == "soft_flag"


def test_sleep_bad_night_and_cumulative_debt_strong_flag():
    flag = sleep_readiness_flag(last_night_h=5.5, cumulative_7d_h=40.0)
    assert flag["severity"] == "strong_flag"
    assert "Søvngjeld" in flag["message"]


def test_sleep_missing_data_no_flag():
    assert sleep_readiness_flag(None, None) is None
    assert sleep_readiness_flag(None, 40.0) is None
