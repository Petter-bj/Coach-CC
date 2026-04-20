"""Yazio source — kosthold via reverse-engineered OAuth2 password-grant.

Tre strømmer:
    daily          — summert kcal + makroer per dag (yazio_daily)
    meals          — per-måltid-breakdown (yazio_meals)
    consumed_items — detaljerte produkt-rader (yazio_consumed_items)

Bruker v20-endepunktene fra dimensi/yazio-forken. Baked-in client_id/secret
er samme som Yazio-appen bruker; ingen self-service dev-registrering finnes.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

from src.paths import YAZIO_CREDS
from src.sources.base import FatalError, RetryableError, Source

BASE_URL = "https://yzapi.yazio.com/v20"
TOKEN_URL = f"{BASE_URL}/oauth/token"
CLIENT_ID = "1_4hiybetvfksgw40o0sog4s884kwc840wwso8go4k8c04goo4c"
CLIENT_SECRET = "6rok2m65xuskgkgogw40wkkk8sw0osg84s8cggsc4woos4s8o"

REFRESH_MARGIN_SEC = 300

MEAL_ORDER = ("breakfast", "lunch", "dinner", "snack")


# ===========================================================================
# Pure parsers
# ===========================================================================


def _nutrients(meal: dict) -> dict:
    return meal.get("nutrients") or {}


def parse_yazio_daily(local_date: str, summary: dict) -> dict:
    """Aggreger alle 4 meals til én yazio_daily-rad."""
    meals = summary.get("meals") or {}
    goals = summary.get("goals") or {}

    total = {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for meal_name in MEAL_ORDER:
        n = _nutrients(meals.get(meal_name) or {})
        total["kcal"] += n.get("energy.energy", 0) or 0
        total["protein_g"] += n.get("nutrient.protein", 0) or 0
        total["carbs_g"] += n.get("nutrient.carb", 0) or 0
        total["fat_g"] += n.get("nutrient.fat", 0) or 0

    return {
        "local_date": local_date,
        "kcal": round(total["kcal"], 2),
        "protein_g": round(total["protein_g"], 2),
        "carbs_g": round(total["carbs_g"], 2),
        "fat_g": round(total["fat_g"], 2),
        "steps": summary.get("steps"),
        "water_ml": summary.get("water_intake"),
        "kcal_goal": goals.get("energy.energy"),
        "protein_goal_g": goals.get("nutrient.protein"),
        "carbs_goal_g": goals.get("nutrient.carb"),
        "fat_goal_g": goals.get("nutrient.fat"),
    }


def parse_yazio_meals(local_date: str, summary: dict) -> list[dict]:
    """Én rad per måltid (breakfast/lunch/dinner/snack)."""
    meals = summary.get("meals") or {}
    rows: list[dict] = []
    for meal_name in MEAL_ORDER:
        meal = meals.get(meal_name) or {}
        n = _nutrients(meal)
        rows.append({
            "local_date": local_date,
            "meal": meal_name,
            "kcal": round(n.get("energy.energy", 0) or 0, 2),
            "protein_g": round(n.get("nutrient.protein", 0) or 0, 2),
            "carbs_g": round(n.get("nutrient.carb", 0) or 0, 2),
            "fat_g": round(n.get("nutrient.fat", 0) or 0, 2),
            "energy_goal_kcal": meal.get("energy_goal"),
        })
    return rows


def parse_yazio_consumed(local_date: str, consumed_resp: dict) -> list[dict]:
    """Flat liste med alle consumed items (products + simple_products)."""
    rows: list[dict] = []
    for p in consumed_resp.get("products", []) or []:
        rows.append({
            "id": p["id"],
            "local_date": local_date,
            "daytime": p.get("daytime"),
            "type": "product",
            "product_id": p.get("product_id"),
            "amount": p.get("amount"),
            "serving": p.get("serving"),
            "serving_quantity": p.get("serving_quantity"),
        })
    for p in consumed_resp.get("simple_products", []) or []:
        rows.append({
            "id": p["id"],
            "local_date": local_date,
            "daytime": p.get("daytime"),
            "type": "simple",
            "product_id": None,
            "amount": p.get("amount"),
            "serving": None,
            "serving_quantity": None,
        })
    return rows


# ===========================================================================
# Credentials
# ===========================================================================


def _load_credentials() -> dict:
    if not YAZIO_CREDS.exists():
        raise FatalError(
            f"Mangler {YAZIO_CREDS} — kjør spikes/yazio_login.py først"
        )
    return json.loads(YAZIO_CREDS.read_text())


def _save_credentials(creds: dict) -> None:
    YAZIO_CREDS.write_text(json.dumps(creds, indent=2))


def _needs_refresh(creds: dict) -> bool:
    return time.time() + REFRESH_MARGIN_SEC >= creds.get("expires_at", 0)


def _refresh(creds: dict) -> dict:
    """Prøv refresh_token først; fallback til password-grant ved å hente
    credentials på nytt fra .env."""
    try:
        resp = httpx.post(
            TOKEN_URL,
            json={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": creds["refresh_token"],
            },
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise RetryableError(f"Yazio refresh connection: {e}") from e

    if resp.status_code == 401:
        raise FatalError("Yazio refresh_token ugyldig — kjør spikes/yazio_login.py på nytt")
    if resp.status_code != 200:
        raise RetryableError(f"Yazio refresh HTTP {resp.status_code}: {resp.text[:200]}")

    body = resp.json()
    creds["access_token"] = body["access_token"]
    creds["refresh_token"] = body.get("refresh_token", creds["refresh_token"])
    creds["expires_at"] = int(time.time()) + int(body["expires_in"])
    _save_credentials(creds)
    return creds


# ===========================================================================
# Source
# ===========================================================================


@dataclass
class YazioSource(Source):
    def __post_init__(self) -> None:
        self.name = "yazio"
        self.streams = ["daily", "meals", "consumed_items"]
        self.backfill_days = {
            "daily": 14,
            "meals": 14,
            "consumed_items": 14,
        }
        self._creds: dict | None = None

    def _auth_headers(self) -> dict[str, str]:
        if self._creds is None:
            self._creds = _load_credentials()
        if _needs_refresh(self._creds):
            self._creds = _refresh(self._creds)
        return {"Authorization": f"Bearer {self._creds['access_token']}"}

    def fetch_stream(
        self, conn: sqlite3.Connection, stream: str, since_date: str
    ) -> tuple[int, int]:
        if stream == "daily":
            return self._fetch_daily_and_meals(conn, since_date, write_meals=False)
        if stream == "meals":
            return self._fetch_daily_and_meals(conn, since_date, write_meals=True)
        if stream == "consumed_items":
            return self._fetch_consumed(conn, since_date)
        raise ValueError(f"Ukjent Yazio-strøm: {stream}")

    def _fetch_daily_and_meals(
        self, conn, since_date: str, write_meals: bool
    ) -> tuple[int, int]:
        """Henter daily-summary per dag. Kan skrive enten yazio_daily
        eller yazio_meals (eller begge samtidig i en fetch-iterasjon)."""
        headers = self._auth_headers()
        ins = upd = 0
        for d in _dates_in_range(since_date):
            try:
                resp = httpx.get(
                    f"{BASE_URL}/user/widgets/daily-summary",
                    headers=headers,
                    params={"date": d},
                    timeout=30,
                )
            except httpx.HTTPError as e:
                raise RetryableError(f"Yazio daily-summary connection: {e}") from e

            if resp.status_code == 401:
                raise FatalError("Yazio 401 — token ugyldig")
            if resp.status_code != 200:
                raise RetryableError(f"Yazio daily HTTP {resp.status_code}")

            summary = resp.json()

            if write_meals:
                # Skriv 4 rader per dag
                for row in parse_yazio_meals(d, summary):
                    conn.execute(
                        """
                        INSERT INTO yazio_meals
                            (local_date, meal, kcal, protein_g, carbs_g,
                             fat_g, energy_goal_kcal)
                        VALUES (:local_date, :meal, :kcal, :protein_g,
                                :carbs_g, :fat_g, :energy_goal_kcal)
                        ON CONFLICT (local_date, meal) DO UPDATE SET
                            kcal = excluded.kcal,
                            protein_g = excluded.protein_g,
                            carbs_g = excluded.carbs_g,
                            fat_g = excluded.fat_g,
                            energy_goal_kcal = excluded.energy_goal_kcal,
                            synced_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                        """,
                        row,
                    )
                    ins += 1
            else:
                row = parse_yazio_daily(d, summary)
                conn.execute(
                    """
                    INSERT INTO yazio_daily
                        (local_date, kcal, protein_g, carbs_g, fat_g, steps,
                         water_ml, kcal_goal, protein_goal_g, carbs_goal_g,
                         fat_goal_g)
                    VALUES (:local_date, :kcal, :protein_g, :carbs_g, :fat_g,
                            :steps, :water_ml, :kcal_goal, :protein_goal_g,
                            :carbs_goal_g, :fat_goal_g)
                    ON CONFLICT (local_date) DO UPDATE SET
                        kcal = excluded.kcal,
                        protein_g = excluded.protein_g,
                        carbs_g = excluded.carbs_g,
                        fat_g = excluded.fat_g,
                        steps = excluded.steps,
                        water_ml = excluded.water_ml,
                        kcal_goal = excluded.kcal_goal,
                        protein_goal_g = excluded.protein_goal_g,
                        carbs_goal_g = excluded.carbs_goal_g,
                        fat_goal_g = excluded.fat_goal_g,
                        synced_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                    """,
                    row,
                )
                ins += 1

        conn.commit()
        return ins, upd

    def _fetch_consumed(self, conn, since_date: str) -> tuple[int, int]:
        headers = self._auth_headers()
        ins = upd = 0
        for d in _dates_in_range(since_date):
            try:
                resp = httpx.get(
                    f"{BASE_URL}/user/consumed-items",
                    headers=headers,
                    params={"date": d},
                    timeout=30,
                )
            except httpx.HTTPError as e:
                raise RetryableError(f"Yazio consumed connection: {e}") from e
            if resp.status_code == 401:
                raise FatalError("Yazio 401 — token ugyldig")
            if resp.status_code != 200:
                continue

            consumed = resp.json()
            rows = parse_yazio_consumed(d, consumed)
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO yazio_consumed_items
                        (id, local_date, daytime, type, product_id,
                         amount, serving, serving_quantity)
                    VALUES (:id, :local_date, :daytime, :type, :product_id,
                            :amount, :serving, :serving_quantity)
                    ON CONFLICT (id) DO UPDATE SET
                        daytime = excluded.daytime,
                        amount = excluded.amount,
                        serving = excluded.serving,
                        serving_quantity = excluded.serving_quantity,
                        synced_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                    """,
                    row,
                )
                ins += 1
        conn.commit()
        return ins, upd


def _dates_in_range(since_date: str, until: date | None = None):
    """Yield 'YYYY-MM-DD' for hver dag fra since_date til (og med) until."""
    start = date.fromisoformat(since_date)
    end = until or date.today()
    d = start
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)
