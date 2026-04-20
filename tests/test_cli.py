"""Tester CLI-ene: JSON-output-kontrakt, range-parsing, og end-to-end mot DB."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.cli._common import parse_range


# ---------------------------------------------------------------------------
# parse_range — pure
# ---------------------------------------------------------------------------


def test_parse_range_last_7d() -> None:
    r = parse_range("last_7d")
    assert r.label == "last_7d"
    assert r.end == date.today().isoformat()
    assert r.start == (date.today() - timedelta(days=6)).isoformat()
    assert r.days == 7


def test_parse_range_last_30d() -> None:
    r = parse_range("last_30d")
    assert r.days == 30


def test_parse_range_last_nd() -> None:
    r = parse_range("last_14d")
    assert r.days == 14


def test_parse_range_week_of_returns_monday_to_sunday() -> None:
    # 2026-04-20 er en mandag
    r = parse_range("week_of=2026-04-20")
    assert r.start == "2026-04-20"  # mandag
    assert r.end == "2026-04-26"  # søndag
    assert r.days == 7


def test_parse_range_week_of_handles_midweek_input() -> None:
    # 2026-04-23 er torsdag — skal gi uka mandag-søndag rundt
    r = parse_range("week_of=2026-04-23")
    assert r.start == "2026-04-20"
    assert r.end == "2026-04-26"


def test_parse_range_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        parse_range("sometime_last_month")


# ---------------------------------------------------------------------------
# CLI-subprocess-tester — verifiserer --json-kontrakt mot live DB.
# ---------------------------------------------------------------------------


def _cli(module: str, *args: str) -> dict:
    """Run `python -m src.cli.<module> [args] --json` og parse output.

    `--json` settes sist siden typer-apper med subkommandoer krever flagget
    etter subkommandoen.
    """
    result = subprocess.run(
        [sys.executable, "-m", f"src.cli.{module}", *args, "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_status_cli_returns_valid_json() -> None:
    data = _cli("status")
    assert "streams" in data
    assert "alerts" in data
    assert "injuries" in data
    assert "contexts" in data
    assert isinstance(data["streams"], list)


def test_sleep_summary_cli_schema() -> None:
    data = _cli("sleep_summary", "--range", "last_7d")
    assert data["range"] == "last_7d"
    assert "nights" in data
    assert "rows" in data
    assert isinstance(data["rows"], list)


def test_hrv_trend_cli_schema() -> None:
    data = _cli("hrv_trend", "--range", "last_30d")
    assert data["range"] == "last_30d"
    assert "nights" in data
    assert "avg_last_night" in data


def test_weight_trend_cli_schema() -> None:
    data = _cli("weight_trend", "--range", "last_30d")
    assert "days_with_data" in data
    assert "latest_kg" in data


def test_last_workouts_cli_schema() -> None:
    data = _cli("last_workouts", "--limit", "3")
    assert data["limit"] == 3
    assert "count" in data
    assert isinstance(data["rows"], list)


def test_last_workouts_cli_type_filter() -> None:
    data = _cli("last_workouts", "--type", "running", "--limit", "10")
    assert data["type_filter"] == "running"
    for row in data["rows"]:
        assert row["type"] == "running"


def test_nutrition_today_cli_schema() -> None:
    data = _cli("nutrition", "today")
    assert "date" in data
    assert "meals" in data


def test_nutrition_week_cli_schema() -> None:
    data = _cli("nutrition", "week", "--week-of", "2026-04-20")
    assert "week_of" in data
    assert "days_logged" in data
    assert len(data["days"]) == 7


def test_last_workouts_excludes_superseded() -> None:
    """Dedupe markerte Garmin indoor_rowing som superseded — skal ikke
    dukke opp i last_workouts."""
    data = _cli("last_workouts", "--limit", "20")
    for row in data["rows"]:
        assert row["superseded_by"] is None
