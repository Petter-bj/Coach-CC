"""Yazio personal-login spike.

Yazio har ikke offisielt public API, men har en reverse-engineered OAuth2
password-grant-flow brukt av flere community-klienter (juriadams/yazio på
npm). Vi implementerer samme tilnærming i Python med httpx.

Forhåndskrav:
- Yazio-konto registrert via app (iOS/Android)
- Minst én dag med loggede måltider
- YAZIO_EMAIL og YAZIO_PASSWORD i .env

Usage:
    .venv/bin/python spikes/yazio_login.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import ENV_FILE, YAZIO_CREDS, ensure_runtime_dirs  # noqa: E402

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_RAW = REPO_ROOT / "tests" / "fixtures" / "yazio" / "raw"

# Yazio CLIENT_ID + SECRET leses fra miljøvariabler. Disse er reverse-engineered
# fra Yazio-appen; se README for hvordan finne verdiene (brukes også av
# community-klienter som dimensi/yazio).
BASE_URL = "https://yzapi.yazio.com/v20"
CLIENT_ID = os.environ.get("YAZIO_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("YAZIO_CLIENT_SECRET", "")


def save_fixture(name: str, data: object) -> None:
    FIXTURES_RAW.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_RAW / f"{name}.json"
    with path.open("w") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)
    print(f"  → {path.relative_to(REPO_ROOT)}")


def main() -> int:
    ensure_runtime_dirs()

    email = os.environ.get("YAZIO_EMAIL")
    password = os.environ.get("YAZIO_PASSWORD")
    if not email or not password:
        print("ERROR: sett YAZIO_EMAIL og YAZIO_PASSWORD i .env", file=sys.stderr)
        return 1
    if not CLIENT_ID or not CLIENT_SECRET:
        print(
            "ERROR: sett YAZIO_CLIENT_ID og YAZIO_CLIENT_SECRET i .env.\n"
            "  Verdiene er felles for alle Yazio-brukere (reverse-engineered\n"
            "  fra Yazio-appen). Se README for hvordan finne dem.",
            file=sys.stderr,
        )
        return 1

    print(f"Logger inn på Yazio som {email}...")
    token_resp = httpx.post(
        f"{BASE_URL}/oauth/token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "password",
            "username": email,
            "password": password,
        },
        timeout=30,
    )
    token_resp.raise_for_status()
    token_payload = token_resp.json()

    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_in = token_payload.get("expires_in", 3600)

    credentials = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + int(expires_in),
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "base_url": BASE_URL,
    }

    YAZIO_CREDS.parent.mkdir(parents=True, exist_ok=True)
    with YAZIO_CREDS.open("w") as f:
        json.dump(credentials, f, indent=2)
    os.chmod(YAZIO_CREDS, 0o600)
    print(f"✓ Credentials lagret i {YAZIO_CREDS}")

    headers = {"Authorization": f"Bearer {access_token}"}

    # Brukerprofil
    print("\nHenter brukerprofil...")
    user_resp = httpx.get(f"{BASE_URL}/user", headers=headers, timeout=30)
    user_resp.raise_for_status()
    user = user_resp.json()
    save_fixture("user", user)
    print(f"  {user.get('first_name')} {user.get('last_name', '')}, "
          f"land: {user.get('country')}, food_db: {user.get('food_database_country')}, "
          f"premium: {user.get('premium_type')}")
    print(f"  unit_energy: {user.get('unit_energy')}, unit_mass: {user.get('unit_mass')}")

    # Daglige sammendrag siste 7 dager
    print("\nHenter daglige sammendrag (siste 7 dager)...")
    today = date.today()
    days_with_data = 0
    for i in range(7):
        d = today - timedelta(days=i)
        resp = httpx.get(
            f"{BASE_URL}/user/widgets/daily-summary",
            headers=headers,
            params={"date": d.isoformat()},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  {d}: HTTP {resp.status_code}")
            continue
        data = resp.json()
        save_fixture(f"daily_summary_{d.isoformat()}", data)

        # Quick-look: sum opp kcal fra alle måltider
        meals = data.get("meals", {})
        total_kcal = 0.0
        total_protein = 0.0
        total_carbs = 0.0
        total_fat = 0.0
        for meal_name, meal in meals.items():
            n = meal.get("nutrients", {}) or {}
            total_kcal += n.get("energy.energy", 0) or 0
            total_protein += n.get("nutrient.protein", 0) or 0
            total_carbs += n.get("nutrient.carb", 0) or 0
            total_fat += n.get("nutrient.fat", 0) or 0
        if total_kcal > 0:
            days_with_data += 1
            print(f"  {d}: {total_kcal:.0f} kcal, "
                  f"P {total_protein:.0f}g, K {total_carbs:.0f}g, F {total_fat:.0f}g")
        else:
            print(f"  {d}: (ingen logget mat)")

    print(f"\n  {days_with_data}/7 dager hadde logget mat")

    # Consumed items for dagen med mest data
    print(f"\nHenter consumed-items for i går ({(today - timedelta(days=1)).isoformat()})...")
    yesterday = today - timedelta(days=1)
    consumed_resp = httpx.get(
        f"{BASE_URL}/user/consumed-items",
        headers=headers,
        params={"date": yesterday.isoformat()},
        timeout=30,
    )
    consumed_resp.raise_for_status()
    consumed = consumed_resp.json()
    save_fixture(f"consumed_items_{yesterday.isoformat()}", consumed)
    products = consumed.get("products", [])
    print(f"  {len(products)} produkter")
    if products:
        p = products[0]
        print(f"  Første eksempel: {p.get('daytime')} — product_id={p.get('product_id')} "
              f"amount={p.get('amount')} serving={p.get('serving')}")

    # Hent detaljer på ett product for å se næringsstruktur
    if products:
        product_id = products[0].get("product_id")
        print(f"\nHenter detaljer for product_id {product_id}...")
        prod_resp = httpx.get(
            f"{BASE_URL}/products/{product_id}",
            headers=headers,
            timeout=30,
        )
        if prod_resp.status_code == 200:
            prod = prod_resp.json()
            save_fixture(f"product_{product_id}", prod)
            print(f"  Navn: {prod.get('name')}")
            nutrients = prod.get("nutrients", {})
            if nutrients:
                print(f"  nutrient-nøkler (første 10): {list(nutrients.keys())[:10]}")
        else:
            print(f"  HTTP {prod_resp.status_code}")

    print(f"\nFerdig. Fixtures i {FIXTURES_RAW.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.HTTPStatusError as e:
        print(f"\nHTTP {e.response.status_code}: {e.response.text[:500]}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"\nFAIL: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(10)
