"""Tests for Yazio parsing mot fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from src.sources.yazio import (
    parse_yazio_consumed,
    parse_yazio_daily,
    parse_yazio_meals,
)

FIX = Path("tests/fixtures/yazio/raw")


def _summary(date_str: str) -> dict:
    return json.loads((FIX / f"daily_summary_{date_str}.json").read_text())


# ---------------------------------------------------------------------------
# daily
# ---------------------------------------------------------------------------


def test_parse_daily_today_has_breakfast() -> None:
    """2026-04-20: bare frokost logget (683 kcal)."""
    row = parse_yazio_daily("2026-04-20", _summary("2026-04-20"))
    assert row["local_date"] == "2026-04-20"
    assert row["kcal"] == 683.4
    assert row["protein_g"] == 48.88  # fra fixture
    assert row["carbs_g"] == 94.25
    assert row["fat_g"] == 10.75
    assert row["kcal_goal"] == 3126.2


def test_parse_daily_empty_day() -> None:
    """2026-04-14: ingen mat logget. Sum skal være 0, Yazio default-mål gjelder."""
    row = parse_yazio_daily("2026-04-14", _summary("2026-04-14"))
    assert row["kcal"] == 0
    assert row["protein_g"] == 0
    assert row["carbs_g"] == 0
    assert row["fat_g"] == 0
    # Før brukeren satte opp profil hadde Yazio default 2000 kcal-mål
    assert row["kcal_goal"] == 2000


def test_parse_daily_includes_water_and_steps() -> None:
    row = parse_yazio_daily("2026-04-20", _summary("2026-04-20"))
    assert "steps" in row  # kan være 0 eller faktisk tall
    assert "water_ml" in row


def test_parse_daily_rounds_to_2_decimals() -> None:
    row = parse_yazio_daily("2026-04-20", _summary("2026-04-20"))
    # Ingen felter skal ha mer enn 2 desimaler
    for field in ("kcal", "protein_g", "carbs_g", "fat_g"):
        v = row[field]
        if v is None or v == 0:
            continue
        assert round(v, 2) == v


# ---------------------------------------------------------------------------
# meals
# ---------------------------------------------------------------------------


def test_parse_meals_returns_4_rows() -> None:
    rows = parse_yazio_meals("2026-04-20", _summary("2026-04-20"))
    assert len(rows) == 4
    meals = {r["meal"] for r in rows}
    assert meals == {"breakfast", "lunch", "dinner", "snack"}


def test_parse_meals_breakfast_has_kcal() -> None:
    rows = parse_yazio_meals("2026-04-20", _summary("2026-04-20"))
    breakfast = next(r for r in rows if r["meal"] == "breakfast")
    assert breakfast["kcal"] == 683.4
    assert breakfast["protein_g"] == 48.88
    assert breakfast["energy_goal_kcal"] is not None


def test_parse_meals_unused_meals_have_zero_not_none() -> None:
    rows = parse_yazio_meals("2026-04-20", _summary("2026-04-20"))
    lunch = next(r for r in rows if r["meal"] == "lunch")
    assert lunch["kcal"] == 0  # ingen lunsj logget i dag
    assert lunch["protein_g"] == 0


# ---------------------------------------------------------------------------
# consumed_items
# ---------------------------------------------------------------------------


def test_parse_consumed_empty_day() -> None:
    """2026-04-19: ingen consumed items (bruker startet logging 20. april)."""
    consumed = json.loads((FIX / "consumed_items_2026-04-19.json").read_text())
    rows = parse_yazio_consumed("2026-04-19", consumed)
    assert rows == []


def test_parse_consumed_handles_products_and_simple() -> None:
    """Syntetisk data med begge typer items."""
    consumed = {
        "products": [
            {
                "id": "uuid-1",
                "daytime": "breakfast",
                "product_id": "prod-1",
                "amount": 100,
                "serving": "g",
                "serving_quantity": 1,
            }
        ],
        "simple_products": [
            {
                "id": "uuid-2",
                "daytime": "lunch",
                "amount": 50,
            }
        ],
        "recipe_portions": [],
    }
    rows = parse_yazio_consumed("2026-04-20", consumed)
    assert len(rows) == 2
    by_type = {r["type"]: r for r in rows}
    assert by_type["product"]["product_id"] == "prod-1"
    assert by_type["product"]["amount"] == 100
    assert by_type["simple"]["product_id"] is None
    assert by_type["simple"]["amount"] == 50
