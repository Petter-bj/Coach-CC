"""Garmin Connect auth spike + data + FIT-download.

Kjøres manuelt én gang for å:
1. Logge inn (med MFA-prompt), cache tokens til credentials/
2. Hente én dag med eksempel-data fra alle relevante endpoints
3. Laste ned én FIT-fil (KRITISK TEST — avgjør om vi får samples)
4. Lagre JSON-responser som raw fixtures i tests/fixtures/garmin/raw/
   (gitignored; vi lager redacted kopier etter inspeksjon)

Usage:
    GARMIN_EMAIL=... GARMIN_PASSWORD=... .venv/bin/python spikes/garmin_login.py

MFA-kode spørres interaktivt.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Legg src/ på path slik at vi kan importere paths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import (
    APP_SUPPORT,
    CREDENTIALS_DIR,
    ENV_FILE,
    FIT_FILES_DIR,
    GARMIN_TOKENS,
    ensure_runtime_dirs,
)

from dotenv import load_dotenv  # noqa: E402
from garminconnect import Garmin  # noqa: E402

# Last .env fra ~/Library/Application Support/Trening/credentials/.env
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_RAW = REPO_ROOT / "tests" / "fixtures" / "garmin" / "raw"


def prompt_mfa() -> str:
    return input("Garmin MFA-kode: ").strip()


def save_fixture(name: str, data: object) -> None:
    FIXTURES_RAW.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_RAW / f"{name}.json"
    with path.open("w") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)
    print(f"  → {path.relative_to(REPO_ROOT)}")


def main() -> int:
    ensure_runtime_dirs()

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        print("ERROR: sett GARMIN_EMAIL og GARMIN_PASSWORD i miljøet", file=sys.stderr)
        return 1

    tokenstore = str(GARMIN_TOKENS.parent)  # garminconnect lagrer flere filer
    print(f"Logger inn som {email}...")
    print(f"Tokenstore: {tokenstore}")

    client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    client.login(tokenstore=tokenstore)
    print("✓ Innlogget, tokens cachet")

    # Bruk i går som referansedato (ferdig data; i dag kan være ufullstendig)
    target_date = (date.today() - timedelta(days=1)).isoformat()
    print(f"\nHenter data for {target_date}...")

    endpoints = {
        "hrv": lambda: client.get_hrv_data(target_date),
        "sleep": lambda: client.get_sleep_data(target_date),
        "body_battery": lambda: client.get_body_battery(target_date, target_date),
        "training_readiness": lambda: client.get_training_readiness(target_date),
        "morning_training_readiness": lambda: client.get_morning_training_readiness(target_date),
        "rhr_day": lambda: client.get_rhr_day(target_date),
        "stress": lambda: client.get_stress_data(target_date),
        "spo2": lambda: client.get_spo2_data(target_date),
        "max_metrics": lambda: client.get_max_metrics(target_date),
        "user_summary": lambda: client.get_user_summary(target_date),
        "steps": lambda: client.get_steps_data(target_date),
        "intensity_minutes": lambda: client.get_intensity_minutes_data(target_date),
        "all_day_stress": lambda: client.get_all_day_stress(target_date),
    }

    for name, fetch in endpoints.items():
        try:
            data = fetch()
            size_hint = len(data) if hasattr(data, "__len__") else "scalar"
            print(f"  {name}: OK ({size_hint})")
            save_fixture(f"daily_{name}", data)
        except Exception as e:
            print(f"  {name}: FEIL — {type(e).__name__}: {e}")

    # Aktiviteter — siste 5
    print("\nHenter siste aktiviteter...")
    try:
        activities = client.get_activities(0, 5)
        print(f"  {len(activities)} aktiviteter")
        save_fixture("activities_latest_5", activities)
    except Exception as e:
        print(f"  FEIL: {e}")
        activities = []

    # FIT-download — KRITISK TEST
    if activities:
        activity = activities[0]
        activity_id = activity.get("activityId")
        print(f"\nFIT-download test: aktivitet {activity_id}...")
        try:
            fit_bytes = client.download_activity(activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
            fit_path = FIT_FILES_DIR / f"garmin_spike_{activity_id}.fit"
            with fit_path.open("wb") as f:
                f.write(fit_bytes)
            print(f"  ✓ FIT-fil lagret: {fit_path} ({len(fit_bytes)} bytes)")
            print(f"  ✓ KRITISK SPIKE BESTÅTT — FIT-download fungerer!")
        except Exception as e:
            print(f"  ✗ FEIL — {type(e).__name__}: {e}")
            print("  ✗ KRITISK SPIKE FEILET — vurder fallback uten samples i v1")

        # Hent også activity details for first activity
        try:
            details = client.get_activity_details(activity_id)
            save_fixture(f"activity_details_{activity_id}", details)
            print(f"  ✓ activity_details: OK")
        except Exception as e:
            print(f"  activity_details: FEIL — {e}")
    else:
        print("\nIngen aktiviteter funnet — hopper over FIT-test")

    print(f"\nFerdig. Fixtures i {FIXTURES_RAW.relative_to(REPO_ROOT)}")
    print(f"Tokens cachet i {CREDENTIALS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
