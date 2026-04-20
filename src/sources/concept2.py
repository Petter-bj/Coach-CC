"""Concept2 Logbook source.

To strømmer:
    sessions   — liste fra /api/users/me/results, oppretter workouts +
                 concept2_session_details + concept2_intervals
    fit_samples — laster ned FIT per økt som mangler, parser til workout_samples

Bruker personal long-lived token (se spikes/concept2_oauth.py) mot
log.concept2.com/api. Ingen OAuth-refresh nødvendig.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from src.fit_parser import parse_fit_to_samples
from src.paths import CONCEPT2_CREDS, FIT_FILES_DIR
from src.sources.base import FatalError, RetryableError, Source

API_BASE = "https://log.concept2.com/api"

# Hvor mange resultater vi henter per kall (Concept2 limit ser ut til å være 50)
RESULTS_PAGE_LIMIT = 50


# ===========================================================================
# Pure parsers
# ===========================================================================


def _timezone_from_detail(data: dict) -> str:
    return data.get("timezone") or "Europe/Oslo"


def _parse_local_to_utc(local_ts: str, tz_name: str) -> str:
    """'2026-04-18 11:53:00' (lokal) + 'Europe/Oslo' → UTC ISO."""
    dt = datetime.fromisoformat(local_ts)
    local = dt.replace(tzinfo=ZoneInfo(tz_name))
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_concept2_session(data: dict) -> tuple[dict, dict]:
    """Én Concept2 result detail → (workouts_row, concept2_session_details_row)."""
    tz_name = _timezone_from_detail(data)
    local_ts = data["date"]  # "2026-04-18 11:53:00"
    started_at_utc = _parse_local_to_utc(local_ts, tz_name)
    local_date = local_ts[:10]

    # Pace per 500m fra total tid og total distanse: (time/distance)*500
    # time er i tiendedeler sek; distance i m
    avg_pace = None
    if data.get("time") and data.get("distance"):
        time_sec = data["time"] / 10.0
        avg_pace = time_sec / data["distance"] * 500

    avg_watts = None
    if data.get("time") and data.get("distance"):
        # P = 2.8 * (m/s)^3 — erg-standard. Unngår å finne i fixtures; bruker stroke-data hvis tilgjengelig
        ms = data["distance"] / (data["time"] / 10.0)
        if ms > 0:
            avg_watts = 2.8 * (ms**3)

    avg_hr = None
    hr = data.get("heart_rate")
    if isinstance(hr, dict):
        a = hr.get("average")
        if isinstance(a, (int, float)) and a > 0:
            avg_hr = int(a)

    workouts_row = {
        "external_id": str(data["id"]),
        "source": "concept2",
        "started_at_utc": started_at_utc,
        "timezone": tz_name,
        "local_date": local_date,
        "duration_sec": (data["time"] / 10.0) if data.get("time") else None,
        "type": data.get("type"),  # skierg | rower | bikeerg
        "distance_m": data.get("distance"),
        "avg_hr": avg_hr,
        "calories": data.get("calories_total"),
    }

    details_row = {
        "c2_result_id": data["id"],
        "type": data["type"],
        "time_tenths": data.get("time"),
        "workout_type": data.get("workout_type"),
        "source": data.get("source"),
        "avg_pace_500m_sec": avg_pace,
        "avg_watts": avg_watts,
        "avg_stroke_rate": data.get("stroke_rate"),
        "stroke_count": data.get("stroke_count"),
        "drag_factor": data.get("drag_factor"),
        "rest_distance_m": data.get("rest_distance"),
        "rest_time_tenths": data.get("rest_time"),
        "verified": 1 if data.get("verified") else 0,
        "raw_json": json.dumps(data, ensure_ascii=False),
    }
    return workouts_row, details_row


def parse_concept2_intervals(data: dict) -> list[dict]:
    """Intervall-breakdown fra workout.intervals[]."""
    workout = data.get("workout") or {}
    intervals = workout.get("intervals") or []
    out: list[dict] = []
    for i, iv in enumerate(intervals):
        hr = iv.get("heart_rate") or {}
        # HR=255 er Concept2 "no data"-sentinel — normaliser til None
        def _hr(key):
            v = hr.get(key)
            if v is None or v == 255:
                return None
            return v
        out.append({
            "interval_num": i,
            "machine": iv.get("machine"),
            "type": iv.get("type"),
            "time_tenths": iv.get("time"),
            "distance_m": iv.get("distance"),
            "calories_total": iv.get("calories_total"),
            "stroke_rate": iv.get("stroke_rate"),
            "rest_distance_m": iv.get("rest_distance"),
            "rest_time_tenths": iv.get("rest_time"),
            "hr_min": _hr("min"),
            "hr_max": _hr("max"),
            "hr_avg": _hr("average"),
        })
    return out


# ===========================================================================
# Credentials
# ===========================================================================


def _load_token() -> str:
    if not CONCEPT2_CREDS.exists():
        raise FatalError(
            f"Mangler {CONCEPT2_CREDS} — kjør spikes/concept2_oauth.py først"
        )
    creds = json.loads(CONCEPT2_CREDS.read_text())
    token = creds.get("access_token")
    if not token:
        raise FatalError("Mangler access_token i Concept2 credentials")
    return token


# ===========================================================================
# Source
# ===========================================================================


@dataclass
class Concept2Source(Source):
    def __post_init__(self) -> None:
        self.name = "concept2"
        self.streams = ["sessions", "fit_samples"]
        self.backfill_days = {"sessions": 30, "fit_samples": 30}
        self._token: str | None = None

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = _load_token()
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def fetch_stream(
        self, conn: sqlite3.Connection, stream: str, since_date: str
    ) -> tuple[int, int]:
        if stream == "sessions":
            return self._fetch_sessions(conn, since_date)
        if stream == "fit_samples":
            return self._fetch_fit_samples(conn)
        raise ValueError(f"Ukjent Concept2-strøm: {stream}")

    # -------------------------------------------------------------
    # Sessions
    # -------------------------------------------------------------
    def _fetch_sessions(self, conn, since_date: str) -> tuple[int, int]:
        # Hent listen av resultater (kun sammendrag; vi må hente detaljer per)
        try:
            resp = httpx.get(
                f"{API_BASE}/users/me/results",
                headers=self._headers(),
                params={"from": since_date, "limit": RESULTS_PAGE_LIMIT},
                timeout=30,
            )
        except httpx.HTTPError as e:
            raise RetryableError(f"Concept2 list connection: {e}") from e

        if resp.status_code == 401:
            raise FatalError("Concept2 401 — token ugyldig")
        if resp.status_code != 200:
            raise RetryableError(f"Concept2 list HTTP {resp.status_code}")

        items = resp.json().get("data") or []
        ins = upd = 0
        for summary in items:
            result_id = summary["id"]
            # Hent detail for å få workout.intervals + timezone
            detail_resp = httpx.get(
                f"{API_BASE}/users/me/results/{result_id}",
                headers=self._headers(),
                timeout=30,
            )
            if detail_resp.status_code != 200:
                continue
            data = detail_resp.json().get("data") or {}
            workouts_row, details_row = parse_concept2_session(data)
            intervals = parse_concept2_intervals(data)

            # Upsert workouts
            conn.execute(
                """
                INSERT INTO workouts
                    (external_id, source, started_at_utc, timezone, local_date,
                     duration_sec, type, distance_m, avg_hr, calories)
                VALUES (:external_id, :source, :started_at_utc, :timezone,
                        :local_date, :duration_sec, :type, :distance_m,
                        :avg_hr, :calories)
                ON CONFLICT (source, external_id) DO UPDATE SET
                    started_at_utc = excluded.started_at_utc,
                    duration_sec = excluded.duration_sec,
                    distance_m = excluded.distance_m,
                    avg_hr = excluded.avg_hr,
                    calories = excluded.calories
                """,
                workouts_row,
            )
            wid = conn.execute(
                "SELECT id FROM workouts WHERE source='concept2' AND external_id=?",
                (workouts_row["external_id"],),
            ).fetchone()["id"]

            details_row["workout_id"] = wid
            conn.execute(
                """
                INSERT INTO concept2_session_details
                    (workout_id, c2_result_id, type, time_tenths, workout_type,
                     source, avg_pace_500m_sec, avg_watts, avg_stroke_rate,
                     stroke_count, drag_factor, rest_distance_m,
                     rest_time_tenths, verified, raw_json)
                VALUES (:workout_id, :c2_result_id, :type, :time_tenths,
                        :workout_type, :source, :avg_pace_500m_sec, :avg_watts,
                        :avg_stroke_rate, :stroke_count, :drag_factor,
                        :rest_distance_m, :rest_time_tenths, :verified, :raw_json)
                ON CONFLICT (workout_id) DO UPDATE SET
                    avg_pace_500m_sec = excluded.avg_pace_500m_sec,
                    avg_watts = excluded.avg_watts,
                    avg_stroke_rate = excluded.avg_stroke_rate,
                    stroke_count = excluded.stroke_count,
                    drag_factor = excluded.drag_factor,
                    raw_json = excluded.raw_json
                """,
                details_row,
            )

            # Bytt ut intervaller fullstendig (enkelt og idempotent)
            conn.execute(
                "DELETE FROM concept2_intervals WHERE session_details_workout_id = ?",
                (wid,),
            )
            if intervals:
                conn.executemany(
                    """
                    INSERT INTO concept2_intervals
                        (session_details_workout_id, interval_num, machine, type,
                         time_tenths, distance_m, calories_total, stroke_rate,
                         rest_distance_m, rest_time_tenths, hr_min, hr_max, hr_avg)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            wid,
                            iv["interval_num"],
                            iv["machine"],
                            iv["type"],
                            iv["time_tenths"],
                            iv["distance_m"],
                            iv["calories_total"],
                            iv["stroke_rate"],
                            iv["rest_distance_m"],
                            iv["rest_time_tenths"],
                            iv["hr_min"],
                            iv["hr_max"],
                            iv["hr_avg"],
                        )
                        for iv in intervals
                    ],
                )
            ins += 1

        conn.commit()
        return ins, upd

    # -------------------------------------------------------------
    # FIT samples
    # -------------------------------------------------------------
    def _fetch_fit_samples(self, conn) -> tuple[int, int]:
        missing = conn.execute(
            """
            SELECT w.id AS workout_id, d.c2_result_id
              FROM workouts w
              JOIN concept2_session_details d ON d.workout_id = w.id
             WHERE w.source = 'concept2' AND d.fit_file_path IS NULL
             ORDER BY w.started_at_utc DESC
             LIMIT 20
            """
        ).fetchall()
        if not missing:
            return 0, 0

        total_samples = 0
        total_updated = 0
        for row in missing:
            wid = row["workout_id"]
            result_id = row["c2_result_id"]
            try:
                fit_resp = httpx.get(
                    f"{API_BASE}/users/me/results/{result_id}/export/fit",
                    headers=self._headers(),
                    timeout=60,
                    follow_redirects=True,
                )
            except httpx.HTTPError as e:
                raise RetryableError(f"Concept2 FIT download: {e}") from e

            if fit_resp.status_code != 200:
                conn.execute(
                    "INSERT INTO alerts (source, level, message) VALUES ('concept2', 'warning', ?)",
                    (f"FIT-download HTTP {fit_resp.status_code} for result {result_id}",),
                )
                continue

            fit_path = FIT_FILES_DIR / f"concept2_{result_id}.fit"
            fit_path.write_bytes(fit_resp.content)

            try:
                samples, _summary = parse_fit_to_samples(fit_path)
            except Exception as e:  # noqa: BLE001
                conn.execute(
                    "INSERT INTO alerts (source, level, message) VALUES ('concept2', 'warning', ?)",
                    (f"FIT-parse feilet for result {result_id}: {e}",),
                )
                continue

            conn.execute("DELETE FROM workout_samples WHERE workout_id = ?", (wid,))
            conn.executemany(
                """
                INSERT INTO workout_samples
                    (workout_id, t_offset_sec, hr, pace_sec_per_km,
                     speed_m_per_sec, cadence, power_w, distance_m,
                     altitude_m, vertical_oscillation_mm,
                     ground_contact_ms, stride_length_mm)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        wid,
                        s["t_offset_sec"],
                        s["hr"],
                        s["pace_sec_per_km"],
                        s["speed_m_per_sec"],
                        s["cadence"],
                        s["power_w"],
                        s["distance_m"],
                        s["altitude_m"],
                        s["vertical_oscillation_mm"],
                        s["ground_contact_ms"],
                        s["stride_length_mm"],
                    )
                    for s in samples
                ],
            )
            conn.execute(
                "UPDATE concept2_session_details SET fit_file_path = ? WHERE workout_id = ?",
                (str(fit_path.relative_to(FIT_FILES_DIR.parent.parent)), wid),
            )
            total_samples += len(samples)
            total_updated += 1

        conn.commit()
        return total_samples, total_updated
