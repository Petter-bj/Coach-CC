"""Concept2 Logbook personal-token spike.

For personlig bruk (lese egne data) anbefaler Concept2 å generere en
long-lived authorization token direkte fra Edit Profile > Applications,
i stedet for full OAuth2-flow. Dette går rett mot log.concept2.com
(produksjon) uten godkjenningsprosess.

Forhåndskrav:
- CONCEPT2_ACCESS_TOKEN i .env (fra log.concept2.com Edit Profile > Applications)
- Minst én loggført økt på din konto

Usage:
    .venv/bin/python spikes/concept2_oauth.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import (  # noqa: E402
    CONCEPT2_CREDS,
    ENV_FILE,
    FIT_FILES_DIR,
    ensure_runtime_dirs,
)

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_RAW = REPO_ROOT / "tests" / "fixtures" / "concept2" / "raw"

BASE_URL = "https://log.concept2.com"
API_BASE = f"{BASE_URL}/api"


def save_fixture(name: str, data: object) -> None:
    FIXTURES_RAW.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_RAW / f"{name}.json"
    with path.open("w") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)
    print(f"  → {path.relative_to(REPO_ROOT)}")


def main() -> int:
    ensure_runtime_dirs()

    access_token = os.environ.get("CONCEPT2_ACCESS_TOKEN")
    if not access_token:
        print(
            "ERROR: sett CONCEPT2_ACCESS_TOKEN i .env\n"
            "Generer via log.concept2.com → Edit Profile → Applications → "
            "Concept2 Logbook API integration",
            file=sys.stderr,
        )
        return 1

    # Lagre credentials m/ token (ingen refresh siden long-lived personal token)
    credentials = {
        "access_token": access_token,
        "token_type": "personal_long_lived",
        "base_url": BASE_URL,
        "stored_at": int(time.time()),
    }
    CONCEPT2_CREDS.parent.mkdir(parents=True, exist_ok=True)
    with CONCEPT2_CREDS.open("w") as f:
        json.dump(credentials, f, indent=2)
    os.chmod(CONCEPT2_CREDS, 0o600)
    print(f"✓ Credentials lagret i {CONCEPT2_CREDS}")

    headers = {"Authorization": f"Bearer {access_token}"}

    # Profil
    print("\nHenter bruker-profil...")
    me_resp = httpx.get(f"{API_BASE}/users/me", headers=headers, timeout=30)
    me_resp.raise_for_status()
    me = me_resp.json()
    save_fixture("user_me", me)
    user_id = me.get("data", {}).get("id")
    username = me.get("data", {}).get("username")
    print(f"  user_id: {user_id}, username: {username}")

    # Siste 30 dager resultater
    since = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    print(f"\nHenter resultater siden {since}...")
    results_resp = httpx.get(
        f"{API_BASE}/users/me/results",
        headers=headers,
        params={"from": since, "limit": 50},
        timeout=30,
    )
    results_resp.raise_for_status()
    results = results_resp.json()
    save_fixture("results_last_30d", results)

    items = results.get("data", [])
    print(f"  {len(items)} resultater funnet")

    # Vis typer
    types_count: dict[str, int] = {}
    for r in items:
        t = r.get("type", "?")
        types_count[t] = types_count.get(t, 0) + 1
    print(f"  Typer: {types_count}")

    # Plukk skierg hvis det finnes, ellers første
    skierg_items = [r for r in items if r.get("type") == "skierg"]
    target = skierg_items[0] if skierg_items else (items[0] if items else None)

    if target:
        result_id = target.get("id")
        result_type = target.get("type")
        print(f"\nHenter detaljer for {result_type}-økt {result_id}...")

        detail_resp = httpx.get(
            f"{API_BASE}/users/me/results/{result_id}",
            headers=headers,
            timeout=30,
        )
        detail_resp.raise_for_status()
        detail = detail_resp.json()
        save_fixture(f"result_{result_id}_detail", detail)

        # KRITISK: FIT-download for slag-nivå-data
        print(f"\nFIT-download test: økt {result_id}...")
        try:
            fit_resp = httpx.get(
                f"{API_BASE}/users/me/results/{result_id}/export/fit",
                headers=headers,
                timeout=60,
                follow_redirects=True,
            )
            fit_resp.raise_for_status()
            fit_path = FIT_FILES_DIR / f"concept2_spike_{result_id}.fit"
            with fit_path.open("wb") as f:
                f.write(fit_resp.content)
            print(f"  ✓ FIT-fil lagret: {fit_path.name} ({len(fit_resp.content)} bytes)")
        except Exception as e:
            print(f"  ✗ FIT-download feilet — {type(e).__name__}: {e}")

        # TCX som fallback
        try:
            tcx_resp = httpx.get(
                f"{API_BASE}/users/me/results/{result_id}/export/tcx",
                headers=headers,
                timeout=60,
                follow_redirects=True,
            )
            tcx_resp.raise_for_status()
            print(f"  ✓ TCX tilgjengelig ({len(tcx_resp.content)} bytes)")
        except Exception as e:
            print(f"  TCX-download: {e}")

        # Strukturoversikt
        data = detail.get("data", {})
        print(f"\n  Struktur på detail-respons:")
        print(f"    date: {data.get('date')}")
        print(f"    type: {data.get('type')}")
        print(f"    distance: {data.get('distance')} m")
        print(f"    time: {data.get('time')} tidels-sek")
        print(f"    workout_type: {data.get('workout_type')}")
        print(f"    stroke_rate (avg): {data.get('stroke_rate')}")
        workout = data.get('workout')
        if workout:
            print(f"    workout keys: {list(workout.keys())}")
            intervals = workout.get('intervals', [])
            print(f"    intervals: {len(intervals)}")
            if intervals:
                iv = intervals[0]
                print(f"      første interval keys: {list(iv.keys())}")
    else:
        print("\nIngen resultater funnet — logg noen økter på Concept2 og prøv igjen")

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
