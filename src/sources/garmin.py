"""Garmin Connect source.

5 strømmer:
    daily   — aggregater (RHR, VO2, BB, readiness, steps, stress, SpO2, kalorier)
    sleep   — søvn-DTO (stages, score)
    hrv     — HRV-summary (weeklyAvg, lastNightAvg, status, baseline)
    activities — liste + detail; oppretter workouts + garmin_activity_details
    fit_samples — laster ned FIT for aktiviteter uten fit_file_path, parser til workout_samples

Arkitektur:
* Alle `parse_*`-funksjonene er pure og testes direkte mot fixtures.
* `GarminSource.fetch_stream` orkestrerer nettverkskall og insert.
* garmin-klienten lastes lazy fra cached tokens i credentials-katalogen.

Idempotens via `ON CONFLICT DO UPDATE` / `DO NOTHING` på naturlige nøkler.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from src.fit_parser import parse_fit_to_samples
from src.paths import FIT_FILES_DIR, GARMIN_TOKENS
from src.sources.base import FatalError, RetryableError, Source, upsert_row


# ===========================================================================
# PURE PARSERS — hver tar rå JSON-payload og returnerer dict for INSERT.
# ===========================================================================


def parse_garmin_daily(
    local_date: str,
    rhr: dict | None,
    body_battery: list | None,
    training_readiness: list | None,
    max_metrics: list | None,
    spo2: dict | None,
    stress: dict | None,
    user_summary: dict | None,
    intensity_minutes: dict | None,
) -> dict:
    """Aggreger endpoint-responser for én dag til én garmin_daily-rad."""
    row: dict[str, Any] = {"local_date": local_date}

    # RHR — nested i allMetrics.metricsMap
    rhr_val = None
    if rhr:
        m = (rhr.get("allMetrics") or {}).get("metricsMap") or {}
        values = m.get("WELLNESS_RESTING_HEART_RATE") or []
        if values:
            rhr_val = values[0].get("value")
    row["resting_hr"] = int(rhr_val) if rhr_val else None

    # Body Battery
    bb_min = bb_max = None
    if body_battery:
        for entry in body_battery:
            mn = entry.get("charged") or entry.get("bodyBatteryMin")
            mx = entry.get("drained") or entry.get("bodyBatteryMax")
            bb_min = mn if mn is not None else bb_min
            bb_max = mx if mx is not None else bb_max
    row["body_battery_min"] = bb_min
    row["body_battery_max"] = bb_max

    # Training readiness (liste, ta siste)
    if training_readiness:
        latest = training_readiness[-1]
        row["training_readiness_score"] = latest.get("score")
        row["training_readiness_level"] = latest.get("level")
        row["acute_load"] = latest.get("acuteLoad")
        row["recovery_time_hours"] = (
            latest.get("recoveryTime") // 60 if latest.get("recoveryTime") else None
        )

    # VO2 max
    if max_metrics:
        gen = (max_metrics[0] or {}).get("generic") or {}
        row["vo2max"] = gen.get("vo2MaxPreciseValue") or gen.get("vo2MaxValue")

    # SpO2
    if spo2:
        row["spo2_avg"] = spo2.get("averageSpO2")
        row["spo2_lowest"] = spo2.get("lowestSpO2")

    # Stress
    if stress:
        row["stress_avg"] = stress.get("avgStressLevel")
        row["stress_max"] = stress.get("maxStressLevel")

    # User summary
    if user_summary:
        row["steps"] = user_summary.get("totalSteps")
        row["step_goal"] = user_summary.get("dailyStepGoal")
        row["distance_m"] = user_summary.get("totalDistanceMeters")
        row["total_calories"] = (
            int(user_summary["totalKilocalories"])
            if user_summary.get("totalKilocalories")
            else None
        )
        row["active_calories"] = (
            int(user_summary["activeKilocalories"])
            if user_summary.get("activeKilocalories")
            else None
        )
        row["bmr_calories"] = (
            int(user_summary["bmrKilocalories"])
            if user_summary.get("bmrKilocalories")
            else None
        )

    # Intensity minutes
    if intensity_minutes:
        row["intensity_minutes_moderate"] = intensity_minutes.get(
            "weeklyModerateMinutes"
        ) or intensity_minutes.get("dailyModerateMinutes")
        row["intensity_minutes_vigorous"] = intensity_minutes.get(
            "weeklyVigorousMinutes"
        ) or intensity_minutes.get("dailyVigorousMinutes")

    return row


def parse_garmin_sleep(sleep_payload: dict) -> dict | None:
    """Parse daily_sleep.json → garmin_sleep-rad. Returner None hvis ufullstendig."""
    dto = (sleep_payload or {}).get("dailySleepDTO") or {}
    if not dto.get("calendarDate"):
        return None

    scores = dto.get("sleepScores") or {}
    overall = scores.get("overall") or {}

    def _iso_or_none(ms: int | None) -> str | None:
        if ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    return {
        "local_date": dto["calendarDate"],
        "sleep_start_utc": _iso_or_none(dto.get("sleepStartTimestampGMT")),
        "sleep_end_utc": _iso_or_none(dto.get("sleepEndTimestampGMT")),
        "duration_sec": dto.get("sleepTimeSeconds"),
        "deep_sec": dto.get("deepSleepSeconds"),
        "light_sec": dto.get("lightSleepSeconds"),
        "rem_sec": dto.get("remSleepSeconds"),
        "awake_sec": dto.get("awakeSleepSeconds"),
        "nap_sec": dto.get("napTimeSeconds"),
        "sleep_score": overall.get("value"),
        "sleep_score_qualifier": overall.get("qualifierKey"),
        "avg_respiration": dto.get("averageRespirationValue"),
        "lowest_respiration": dto.get("lowestRespirationValue"),
        "sleep_from_device": 1 if dto.get("sleepFromDevice") else 0,
    }


def parse_garmin_hrv(hrv_payload: dict) -> dict | None:
    """Parse daily_hrv.json → garmin_hrv-rad."""
    summary = (hrv_payload or {}).get("hrvSummary") or {}
    if not summary.get("calendarDate"):
        return None

    baseline = summary.get("baseline") or {}
    return {
        "local_date": summary["calendarDate"],
        "last_night_avg_ms": summary.get("lastNightAvg"),
        "last_night_5min_high_ms": summary.get("lastNight5MinHigh"),
        "weekly_avg_ms": summary.get("weeklyAvg"),
        "baseline_low_upper": baseline.get("lowUpper") if isinstance(baseline, dict) else None,
        "baseline_balanced_low": baseline.get("balancedLow") if isinstance(baseline, dict) else None,
        "baseline_balanced_upper": baseline.get("balancedUpper") if isinstance(baseline, dict) else None,
        "status": summary.get("status"),
        "feedback_phrase": summary.get("feedbackPhrase"),
    }


def _parse_iso_local(ts: str, tz: str = "Europe/Oslo") -> tuple[str, str]:
    """Konverter '2026-04-19 11:16:58' (lokal) til (utc_iso, local_date).

    Garmin returnerer startTimeLocal og startTimeGMT. Vi trenger UTC-isoformat
    for lagring, og local_date for indeksering. Bruker GMT direkte hvis vi har
    det (mer presist).
    """
    ts_clean = ts.replace(" ", "T") + "Z" if " " in ts and "Z" not in ts else ts
    # Hvis ikke Z — antar GMT
    if not ts_clean.endswith("Z"):
        ts_clean = ts_clean.rstrip("Z") + "Z"
    return ts_clean, ts_clean[:10]


def parse_garmin_activity(item: dict) -> tuple[dict, dict]:
    """Én aktivitet → (workouts_row, garmin_activity_details_row)."""
    activity_type = (item.get("activityType") or {}).get("typeKey") or "unknown"
    gmt = item.get("startTimeGMT")  # f.eks. "2026-04-19 09:16:58"
    local_raw = item.get("startTimeLocal")  # f.eks. "2026-04-19 11:16:58"

    # UTC → ISO 8601
    started_at_utc = (
        gmt.replace(" ", "T") + "Z" if gmt and "T" not in gmt else gmt
    ) or ""
    # Lokal dato
    local_date = (local_raw or gmt or "")[:10]

    workouts_row = {
        "external_id": str(item["activityId"]),
        "source": "garmin",
        "started_at_utc": started_at_utc,
        "timezone": "Europe/Oslo",
        "local_date": local_date,
        "duration_sec": (
            int(item["duration"]) if item.get("duration") is not None else None
        ),
        "type": activity_type,
        "distance_m": item.get("distance"),
        "avg_hr": int(item["averageHR"]) if item.get("averageHR") else None,
        "calories": int(item["calories"]) if item.get("calories") else None,
    }
    details_row = {
        "garmin_activity_id": item["activityId"],
        "activity_name": item.get("activityName"),
        "activity_type_key": activity_type,
        "activity_type_parent_id": (item.get("activityType") or {}).get("parentTypeId"),
        "moving_duration_sec": item.get("movingDuration"),
        "elevation_gain_m": item.get("elevationGain"),
        "elevation_loss_m": item.get("elevationLoss"),
        "avg_speed_m_per_sec": item.get("averageSpeed"),
        "max_speed_m_per_sec": item.get("maxSpeed"),
        "max_hr": int(item["maxHR"]) if item.get("maxHR") else None,
        "start_latitude": item.get("startLatitude"),
        "start_longitude": item.get("startLongitude"),
        "has_polyline": 1 if item.get("hasPolyline") else 0,
        "device_id": item.get("deviceId"),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }
    return workouts_row, details_row


# ===========================================================================
# INSERT HELPERS
# ===========================================================================


def _dates_in_range(since_date: str, until: date | None = None) -> Iterator[str]:
    """Yield 'YYYY-MM-DD' for hver dag fra since_date til (og med) until."""
    start = date.fromisoformat(since_date)
    end = until or date.today()
    d = start
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


# ===========================================================================
# SOURCE IMPLEMENTATION
# ===========================================================================


@dataclass
class GarminSource(Source):
    def __post_init__(self) -> None:
        self.name = "garmin"
        self.streams = [
            "daily", "sleep", "hrv", "activities", "fit_samples", "profile",
        ]
        self.backfill_days = {
            "daily": 14,
            "sleep": 7,
            "hrv": 14,
            "activities": 30,
            "fit_samples": 30,
            "profile": 1,  # billig kall; ok å kjøre hver time
        }
        self._client = None

    # -------------------------------------------------------------
    # Lazy client
    # -------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            from garminconnect import (  # local import — ikke nødvendig i tester
                Garmin,
                GarminConnectAuthenticationError,
                GarminConnectConnectionError,
            )
            try:
                c = Garmin()
                c.login(tokenstore=str(GARMIN_TOKENS.parent))
                self._client = c
            except GarminConnectAuthenticationError as e:
                raise FatalError(f"Garmin auth failed: {e}") from e
            except GarminConnectConnectionError as e:
                raise RetryableError(f"Garmin connection: {e}") from e
        return self._client

    # -------------------------------------------------------------
    # Stream dispatcher
    # -------------------------------------------------------------
    def fetch_stream(
        self, conn: sqlite3.Connection, stream: str, since_date: str
    ) -> tuple[int, int]:
        if stream == "daily":
            return self._fetch_daily(conn, since_date)
        if stream == "sleep":
            return self._fetch_sleep(conn, since_date)
        if stream == "hrv":
            return self._fetch_hrv(conn, since_date)
        if stream == "activities":
            return self._fetch_activities(conn, since_date)
        if stream == "fit_samples":
            return self._fetch_fit_samples(conn)
        if stream == "profile":
            return self._fetch_profile(conn)
        raise ValueError(f"Ukjent Garmin-strøm: {stream}")

    # -------------------------------------------------------------
    # Daily (12 endpoints aggregert per dag)
    # -------------------------------------------------------------
    def _fetch_daily(self, conn, since_date) -> tuple[int, int]:
        ins = upd = 0
        for d in _dates_in_range(since_date):
            try:
                rhr = self._safe(self.client.get_rhr_day, d)
                bb = self._safe(self.client.get_body_battery, d, d)
                tr = self._safe(self.client.get_training_readiness, d)
                mx = self._safe(self.client.get_max_metrics, d)
                sp = self._safe(self.client.get_spo2_data, d)
                st = self._safe(self.client.get_stress_data, d)
                us = self._safe(self.client.get_user_summary, d)
                im = self._safe(self.client.get_intensity_minutes_data, d)
            except RetryableError:
                raise  # bubble up så stream-retry fanger den
            row = parse_garmin_daily(d, rhr, bb, tr, mx, sp, st, us, im)
            # Skip hvis alle felt utenom local_date er None
            if all(v is None for k, v in row.items() if k != "local_date"):
                continue
            i, u = upsert_row(conn, "garmin_daily", row, ["local_date"])
            ins += i
            upd += u
        conn.commit()
        return ins, upd

    def _fetch_sleep(self, conn, since_date) -> tuple[int, int]:
        ins = upd = 0
        for d in _dates_in_range(since_date):
            payload = self._safe(self.client.get_sleep_data, d)
            row = parse_garmin_sleep(payload) if payload else None
            if not row:
                continue
            i, u = upsert_row(conn, "garmin_sleep", row, ["local_date"])
            ins += i
            upd += u
        conn.commit()
        return ins, upd

    def _fetch_hrv(self, conn, since_date) -> tuple[int, int]:
        ins = upd = 0
        for d in _dates_in_range(since_date):
            payload = self._safe(self.client.get_hrv_data, d)
            row = parse_garmin_hrv(payload) if payload else None
            if not row:
                continue
            i, u = upsert_row(conn, "garmin_hrv", row, ["local_date"])
            ins += i
            upd += u
        conn.commit()
        return ins, upd

    # -------------------------------------------------------------
    # Activities
    # -------------------------------------------------------------
    def _fetch_activities(self, conn, since_date) -> tuple[int, int]:
        # Hent rikelig — 50 aktiviteter bakover, filtrer på since_date lokalt
        items = self._safe(self.client.get_activities, 0, 50) or []
        ins = upd = 0
        for item in items:
            local = (item.get("startTimeLocal") or "")[:10]
            if local and local < since_date:
                continue
            workout_row, details_row = parse_garmin_activity(item)

            # Upsert workouts via helper (gir korrekt ins/upd-telling)
            i, u = upsert_row(
                conn, "workouts", workout_row, ["source", "external_id"],
                update_cols=["started_at_utc", "duration_sec", "distance_m",
                             "avg_hr", "calories"],
            )
            ins += i
            upd += u

            # Hent workout_id for FK
            wid = conn.execute(
                "SELECT id FROM workouts WHERE source = 'garmin' AND external_id = ?",
                (workout_row["external_id"],),
            ).fetchone()["id"]
            details_row["workout_id"] = wid

            upsert_row(
                conn, "garmin_activity_details", details_row, ["workout_id"],
                update_cols=["activity_name", "moving_duration_sec",
                             "elevation_gain_m", "elevation_loss_m",
                             "avg_speed_m_per_sec", "max_speed_m_per_sec",
                             "max_hr", "raw_json"],
            )

        conn.commit()
        return ins, upd

    # -------------------------------------------------------------
    # FIT samples: last ned manglende FIT, parse, insert workout_samples
    # -------------------------------------------------------------
    # -------------------------------------------------------------
    # Profile (HRmax, lactate threshold, VO2max, vekt, høyde)
    # -------------------------------------------------------------
    def _fetch_profile(self, conn) -> tuple[int, int]:
        """Hent Garmins bruker-profil og sync til `user_preferences`-tabell.

        Lagrer Garmin-verdier under `*_garmin`-suffikset så brukerens egne
        overstyringer (f.eks. `hr_max` satt manuelt) aldri blir overskrevet.
        `src.coaching.preferences.get_hr_max(conn)` leser user-override først,
        faller tilbake til Garmin-verdi.
        """
        from src.coaching.preferences import get_pref, set_pref

        try:
            zones = self._safe(self.client.connectapi, "/biometric-service/heartRateZones")
            profile = self._safe(self.client.get_user_profile)
        except (RetryableError, FatalError):
            raise
        except Exception as e:  # noqa: BLE001
            raise RetryableError(f"Garmin profile fetch: {e}") from e

        user_data = (profile or {}).get("userData") or {}

        # Pull verdier med fallback
        max_hr = None
        lt_hr = None
        if zones and isinstance(zones, list) and zones:
            z0 = zones[0]
            max_hr = z0.get("maxHeartRateUsed")
            lt_hr = z0.get("lactateThresholdHeartRateUsed")
        if not lt_hr:
            lt_hr = user_data.get("lactateThresholdHeartRate")

        vo2max = user_data.get("vo2MaxRunning")
        weight_g = user_data.get("weight")  # i gram
        weight_kg = round(weight_g / 1000.0, 1) if weight_g else None
        height_cm = user_data.get("height")

        # Map → prefs keys. Alle bruker `_garmin`-suffiks.
        updates: dict[str, str] = {}
        if max_hr:
            updates["hr_max_garmin"] = str(int(max_hr))
        if lt_hr:
            updates["hr_lactate_threshold_garmin"] = str(int(lt_hr))
        if vo2max:
            updates["vo2max_running_garmin"] = str(vo2max)
        if weight_kg:
            updates["weight_kg_garmin"] = str(weight_kg)
        if height_cm:
            updates["height_cm_garmin"] = str(int(height_cm))

        changed = 0
        for k, v in updates.items():
            if get_pref(conn, k) != v:
                set_pref(conn, k, v)
                changed += 1

        return 0, changed

    def _fetch_fit_samples(self, conn) -> tuple[int, int]:
        missing = conn.execute(
            """
            SELECT w.id AS workout_id, d.garmin_activity_id
              FROM workouts w
              JOIN garmin_activity_details d ON d.workout_id = w.id
             WHERE w.source = 'garmin' AND d.fit_file_path IS NULL
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
            aid = row["garmin_activity_id"]
            try:
                from garminconnect import Garmin
                fit_bytes = self._safe(
                    self.client.download_activity,
                    aid,
                    dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
                )
            except RetryableError:
                raise
            if not fit_bytes:
                continue
            fit_path = FIT_FILES_DIR / f"garmin_{aid}.fit"
            fit_path.write_bytes(fit_bytes)

            try:
                samples, _summary = parse_fit_to_samples(fit_path)
            except Exception as e:
                # Ugyldig FIT — marker pathen og fortsett
                conn.execute(
                    "INSERT INTO alerts (source, level, message) VALUES ('garmin', 'warning', ?)",
                    (f"FIT-parse feilet for aktivitet {aid}: {e}",),
                )
                continue

            # Insert samples (clear+replace hvis allerede finnes)
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
                "UPDATE garmin_activity_details SET fit_file_path = ? WHERE workout_id = ?",
                (str(fit_path.relative_to(FIT_FILES_DIR.parent.parent)), wid),
            )
            total_samples += len(samples)
            total_updated += 1

        conn.commit()
        return total_samples, total_updated

    # -------------------------------------------------------------
    # Safe wrapper — fanger kjente nettverksfeil og mapper til vår taxonomi
    # -------------------------------------------------------------
    def _safe(self, fn, *args, **kwargs):
        """Kall `fn(*args, **kwargs)` og map feil til vårt hierarki."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            cls = type(e).__name__
            # Auth-feil → FatalError
            if "authentication" in msg or "unauthorized" in cls.lower() or "401" in msg:
                raise FatalError(f"{cls}: {e}") from e
            # Nettverks-/rate-limit-feil → RetryableError
            if any(kw in msg for kw in ("429", "500", "502", "503", "504", "timeout", "connection")):
                raise RetryableError(f"{cls}: {e}") from e
            if any(kw in cls.lower() for kw in ("connection", "timeout")):
                raise RetryableError(f"{cls}: {e}") from e
            # Fallback: behandle som retryable (antatt flaky)
            raise RetryableError(f"{cls}: {e}") from e
