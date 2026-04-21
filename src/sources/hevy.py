"""Hevy source — styrkeøkter via Hevy API v1 (stabil, API key-basert).

Én strøm:
    workouts — paginert GET /v1/workouts, oppretter workouts +
               strength_sessions + strength_sets per økt.

Hevy API krever Pro-abonnement og en API key fra hevy.com → Settings →
Developer. Samme key brukes av Hevy MCP-serveren; legg den i
`~/Library/Application Support/Trening/credentials/.env` som:

    HEVY_API_KEY=...

Første kjøring backfill'er 180 dager. Etter det oppdateres
`last_successful_upper_bound` til newest `updated_at` vi har sett, og vi
fortsetter å hente alle økter med `updated_at >= last_cursor` — dette
fanger både nye og editerte økter.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.sources.base import FatalError, RetryableError, Source, upsert_row

API_BASE = "https://api.hevyapp.com/v1"
PAGE_SIZE = 10  # Hevy API max ser ut til å være 10
BACKFILL_DAYS = 180

# Hevy bruker ISO 8601 m/ offset, eksempel: "2026-04-21T16:22:30+00:00"


# ===========================================================================
# Pure parsers
# ===========================================================================


def _parse_iso_to_utc(ts: str) -> str:
    """Normaliser Hevy-timestamp ('2026-04-21T16:22:30+00:00' eller
    '2026-04-21T17:04:00.745Z') til ISO UTC uten mikrosekunder."""
    # Håndter både 'Z' og eksplisitt offset
    normalized = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_date_from_utc(utc_iso: str, tz_name: str = "Europe/Oslo") -> str:
    """UTC ISO → YYYY-MM-DD i gitt timezone."""
    from zoneinfo import ZoneInfo

    dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(tz_name)).date().isoformat()


def _duration_sec(start_utc: str, end_utc: str | None) -> int | None:
    if not end_utc:
        return None
    try:
        s = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((e - s).total_seconds()))


def _epley(weight_kg: float | None, reps: int) -> float | None:
    """Epley 1RM-estimat: weight * (1 + reps/30). Samme formel som strength-CLI."""
    if weight_kg is None or weight_kg <= 0 or reps <= 0:
        return None
    return round(weight_kg * (1 + reps / 30), 2)


def parse_hevy_workout(data: dict) -> tuple[dict, list[dict]]:
    """Én Hevy workout → (workouts_row, [strength_sets_rows]).

    strength_sessions-raden trenger bare workout_id og lages etter upsert.
    strength_sets-radene trenger session_id (fylles inn etter upsert).
    """
    tz_name = "Europe/Oslo"  # Hevy leverer ikke tz; anta brukerens lokale
    started_at_utc = _parse_iso_to_utc(data["start_time"])
    local_date = _local_date_from_utc(started_at_utc, tz_name)
    duration = _duration_sec(
        started_at_utc,
        _parse_iso_to_utc(data["end_time"]) if data.get("end_time") else None,
    )

    title = data.get("title") or "Hevy workout"
    notes = (data.get("description") or "").strip() or None

    workouts_row = {
        "external_id": data["id"],
        "source": "hevy",
        "started_at_utc": started_at_utc,
        "timezone": tz_name,
        "local_date": local_date,
        "duration_sec": duration,
        "type": "strength_training",
        "notes": f"Økt: {title}" if not notes else f"Økt: {title} — {notes}",
    }

    sets_rows: list[dict] = []
    for ex in data.get("exercises") or []:
        ex_name = ex.get("title") or "Unknown exercise"
        ex_sets = ex.get("sets") or []
        # set_num er løpende per (session, exercise) — start på 1
        for set_idx, s in enumerate(ex_sets, start=1):
            reps = s.get("reps")
            if reps is None or reps <= 0:
                # Hopp over sett uten reps (f.eks. durations-only sett —
                # plank, hold-øvelser). Database-CHECK krever reps > 0.
                continue
            weight = s.get("weight_kg")
            # Hevy har 0/null for bodyweight; lagre som NULL for konsistens
            if weight == 0:
                weight = None
            sets_rows.append({
                "exercise": ex_name,
                "set_num": set_idx,
                "reps": reps,
                "weight_kg": weight,
                "rpe": s.get("rpe"),
                "e1rm_kg": _epley(weight, reps),
                "notes": None,
            })

    return workouts_row, sets_rows


# ===========================================================================
# Credentials
# ===========================================================================


def _load_api_key() -> str:
    key = os.environ.get("HEVY_API_KEY")
    if not key:
        raise FatalError(
            "Mangler HEVY_API_KEY. Legg til i "
            "~/Library/Application Support/Trening/credentials/.env:\n"
            "    HEVY_API_KEY=sk_live_..."
        )
    return key


# ===========================================================================
# Source
# ===========================================================================


@dataclass
class HevySource(Source):
    def __post_init__(self) -> None:
        self.name = "hevy"
        self.streams = ["workouts"]
        self.backfill_days = {"workouts": BACKFILL_DAYS}
        self._api_key: str | None = None

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = _load_api_key()
        return self._api_key

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Accept": "application/json"}

    def fetch_stream(
        self, conn: sqlite3.Connection, stream: str, since_date: str
    ) -> tuple[int, int]:
        if stream == "workouts":
            return self._fetch_workouts(conn, since_date)
        raise ValueError(f"Ukjent Hevy-strøm: {stream}")

    # -------------------------------------------------------------
    # Workouts
    # -------------------------------------------------------------
    def _fetch_workouts(self, conn: sqlite3.Connection, since_date: str) -> tuple[int, int]:
        """Paginert fetch fra /v1/workouts.

        Hevy returnerer alltid nyeste først. Vi paginerer til vi treffer en
        økt som starter før `since_date`, eller til vi er tom for sider.
        """
        # since_date kommer som YYYY-MM-DD — konverter til UTC cutoff
        cutoff = datetime.fromisoformat(since_date).replace(tzinfo=timezone.utc)

        ins = upd = 0
        page = 1
        stop = False

        while not stop:
            try:
                resp = httpx.get(
                    f"{API_BASE}/workouts",
                    headers=self._headers(),
                    params={"page": page, "pageSize": PAGE_SIZE},
                    timeout=30,
                )
            except httpx.HTTPError as e:
                raise RetryableError(f"Hevy list connection: {e}") from e

            if resp.status_code == 401:
                raise FatalError("Hevy 401 — API key ugyldig eller utløpt")
            if resp.status_code == 403:
                raise FatalError("Hevy 403 — mangler Pro-abonnement eller API-tilgang")
            if resp.status_code == 429:
                raise RetryableError("Hevy 429 — rate limited")
            if resp.status_code != 200:
                raise RetryableError(f"Hevy list HTTP {resp.status_code}")

            payload = resp.json()
            workouts = payload.get("workouts") or []
            page_count = payload.get("page_count") or 1

            if not workouts:
                break

            for w in workouts:
                # Sjekk cutoff — hvis økta startet før backfill-vinduet,
                # stopper vi paginering (nyeste først, så alle etterfølgende
                # er enda eldre)
                try:
                    start_dt = datetime.fromisoformat(
                        w["start_time"].replace("Z", "+00:00")
                    )
                except (KeyError, ValueError):
                    continue
                if start_dt < cutoff:
                    stop = True
                    break

                workouts_row, sets_rows = parse_hevy_workout(w)

                # Upsert workouts
                i, u = upsert_row(
                    conn, "workouts", workouts_row, ["source", "external_id"],
                    update_cols=[
                        "started_at_utc", "timezone", "local_date",
                        "duration_sec", "type", "notes",
                    ],
                )
                ins += i
                upd += u

                wid = conn.execute(
                    "SELECT id FROM workouts WHERE source='hevy' AND external_id=?",
                    (workouts_row["external_id"],),
                ).fetchone()["id"]

                # Upsert strength_sessions (1:1 med workout)
                existing_sess = conn.execute(
                    "SELECT id FROM strength_sessions WHERE workout_id=?",
                    (wid,),
                ).fetchone()
                if existing_sess:
                    session_id = existing_sess["id"]
                    # Bytt ut sett-radene fullstendig (enkel idempotens, samme
                    # mønster som concept2_intervals)
                    conn.execute(
                        "DELETE FROM strength_sets WHERE session_id=?",
                        (session_id,),
                    )
                else:
                    cur = conn.execute(
                        "INSERT INTO strength_sessions (workout_id) VALUES (?)",
                        (wid,),
                    )
                    session_id = cur.lastrowid

                if sets_rows:
                    conn.executemany(
                        """
                        INSERT INTO strength_sets
                            (session_id, exercise, set_num, reps, weight_kg,
                             rpe, e1rm_kg, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                session_id,
                                s["exercise"],
                                s["set_num"],
                                s["reps"],
                                s["weight_kg"],
                                s["rpe"],
                                s["e1rm_kg"],
                                s["notes"],
                            )
                            for s in sets_rows
                        ],
                    )

            if page >= page_count:
                stop = True
            page += 1

        conn.commit()
        return ins, upd
