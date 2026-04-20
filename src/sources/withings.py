"""Withings source: vekt + kroppssammensetning.

Én strøm (`weight`). Refresher OAuth2-tokens automatisk når de utløper.
Bruker httpx direkte — ingen wrapper-lib (withings-api støtter kun pydantic v1).

Withings API-kontrakt:
* Token-endpoint: POST https://wbsapi.withings.net/v2/oauth2
* Measurement-endpoint: POST https://wbsapi.withings.net/measure
* Verdier dekodes via `actual = value * 10^unit`
* Svaret kan ha flere målinger per gruppe (full body composition scan)

Tokens lagres i `credentials/withings.json` og oppdateres etter refresh.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from src.paths import WITHINGS_CREDS
from src.sources.base import FatalError, RetryableError, Source, upsert_row

TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"

# Måletyper vi bryr oss om (Withings docs table 2).
MEASTYPES = {
    1: "weight_kg",
    5: "fat_free_mass_kg",
    6: "fat_ratio_pct",
    8: "fat_mass_kg",
    76: "muscle_mass_kg",
    77: "hydration_kg",
    88: "bone_mass_kg",
}
REQUEST_MEASTYPES = ",".join(str(t) for t in MEASTYPES.keys())

# Refresh token proaktivt når < 5 min igjen
REFRESH_MARGIN_SEC = 300


# ===========================================================================
# Pure parsers
# ===========================================================================


def _decode(value: int, unit: int) -> float:
    """Withings: `actual = value * 10^unit`. unit er typisk -2 eller -3."""
    return value * (10**unit)


def parse_measure_group(group: dict, fallback_timezone: str) -> dict:
    """Konverter én Withings-måle-gruppe til en withings_weight-rad.

    Args:
        group: ett element fra `body.measuregrps[]`.
        fallback_timezone: `body.timezone` fra toppen av responsen, brukes
            hvis gruppa mangler egen timezone.
    """
    tz_name = group.get("timezone") or fallback_timezone or "UTC"
    tz = ZoneInfo(tz_name)

    unix_ts = group["date"]
    dt_utc = datetime.fromtimestamp(unix_ts, tz=ZoneInfo("UTC"))
    dt_local = dt_utc.astimezone(tz)

    row: dict[str, Any] = {
        "grpid": group["grpid"],
        "measured_at_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timezone": tz_name,
        "local_date": dt_local.date().isoformat(),
        "deviceid": group.get("deviceid"),
        "model": group.get("model"),
    }

    # Initialiser alle verdikolonner som None (gruppa har kanskje bare vekt)
    for col in MEASTYPES.values():
        row[col] = None

    for m in group.get("measures", []):
        col = MEASTYPES.get(m["type"])
        if col is None:
            continue
        row[col] = _decode(m["value"], m["unit"])

    return row


# ===========================================================================
# Credentials helpers
# ===========================================================================


def _load_credentials() -> dict:
    if not WITHINGS_CREDS.exists():
        raise FatalError(
            f"Mangler {WITHINGS_CREDS} — kjør spikes/withings_oauth.py først"
        )
    return json.loads(WITHINGS_CREDS.read_text())


def _save_credentials(creds: dict) -> None:
    WITHINGS_CREDS.write_text(json.dumps(creds, indent=2))


def _needs_refresh(creds: dict) -> bool:
    expires_at = creds.get("expires_at", 0)
    return time.time() + REFRESH_MARGIN_SEC >= expires_at


def _refresh_token(creds: dict) -> dict:
    """Bytter refresh_token mot ny access_token (og ny refresh_token)."""
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "action": "requesttoken",
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": creds["refresh_token"],
            },
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise RetryableError(f"Withings token refresh connection: {e}") from e

    if resp.status_code != 200:
        raise RetryableError(f"Withings token refresh HTTP {resp.status_code}: {resp.text[:200]}")

    payload = resp.json()
    if payload.get("status") != 0:
        # status != 0 betyr vanligvis at refresh_token er ugyldig → fatal
        raise FatalError(f"Withings refresh_token avvist: {payload}")

    body = payload["body"]
    creds["access_token"] = body["access_token"]
    creds["refresh_token"] = body["refresh_token"]
    creds["expires_at"] = int(time.time()) + int(body["expires_in"])
    _save_credentials(creds)
    return creds


# ===========================================================================
# Source
# ===========================================================================


@dataclass
class WithingsSource(Source):
    def __post_init__(self) -> None:
        self.name = "withings"
        self.streams = ["weight"]
        self.backfill_days = {"weight": 30}

    def fetch_stream(
        self, conn: sqlite3.Connection, stream: str, since_date: str
    ) -> tuple[int, int]:
        if stream != "weight":
            raise ValueError(f"Ukjent Withings-strøm: {stream}")

        creds = _load_credentials()
        if _needs_refresh(creds):
            creds = _refresh_token(creds)

        # Konverter since_date YYYY-MM-DD → unix timestamp (00:00 UTC)
        start_dt = datetime.fromisoformat(since_date).replace(
            tzinfo=ZoneInfo("UTC")
        )
        start_unix = int(start_dt.timestamp())
        end_unix = int(time.time())

        try:
            resp = httpx.post(
                MEASURE_URL,
                headers={"Authorization": f"Bearer {creds['access_token']}"},
                data={
                    "action": "getmeas",
                    "startdate": start_unix,
                    "enddate": end_unix,
                    "meastypes": REQUEST_MEASTYPES,
                },
                timeout=30,
            )
        except httpx.HTTPError as e:
            raise RetryableError(f"Withings measure connection: {e}") from e

        if resp.status_code == 401:
            raise FatalError("Withings 401 Unauthorized — token ugyldig")
        if resp.status_code != 200:
            raise RetryableError(f"Withings HTTP {resp.status_code}: {resp.text[:200]}")

        payload = resp.json()
        status = payload.get("status")
        if status == 601:
            raise RetryableError("Withings 601 rate limit")
        if status != 0:
            raise RetryableError(f"Withings status {status}: {payload}")

        body = payload.get("body") or {}
        fallback_tz = body.get("timezone") or "Europe/Oslo"
        groups = body.get("measuregrps") or []

        ins = upd = 0
        for group in groups:
            row = parse_measure_group(group, fallback_tz)
            i, u = upsert_row(conn, "withings_weight", row, ["grpid"])
            ins += i
            upd += u

        conn.commit()
        return ins, upd
