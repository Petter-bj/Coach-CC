"""FIT-fil-parsing med automatisk ZIP-utpakking.

Garmin sender ORIGINAL-formatet som ZIP (`<activity_id>_ACTIVITY.fit` inni),
mens Concept2 sender rå FIT. Denne modulen håndterer begge.

Hovedfunksjon:
    parse_fit_to_samples(path) → list[dict]

Hver sample-dict har nøkler som matcher `workout_samples`-kolonnene:
    t_offset_sec, hr, pace_sec_per_km, speed_m_per_sec, cadence, power_w,
    distance_m, altitude_m, vertical_oscillation_mm, ground_contact_ms,
    stride_length_mm.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import fitdecode

# Bytes-prefix som kjennetegner FIT-fil (ASCII ".FIT" på offset 8 i header).
FIT_MAGIC_OFFSET = 8
FIT_MAGIC = b".FIT"

# Bytes-prefix som kjennetegner ZIP.
ZIP_MAGIC = b"PK\x03\x04"


@dataclass
class FitSummary:
    """Lettvekts-oppsummering returnert ved siden av sample-lista."""
    start_time_utc: datetime | None
    end_time_utc: datetime | None
    total_distance_m: float | None
    total_timer_time_sec: float | None
    sport: str | None
    sub_sport: str | None
    record_count: int


def _open_fit_bytes(path: Path) -> bytes:
    """Returner rå FIT-bytes. Pakker ut ZIP hvis nødvendig."""
    raw = path.read_bytes()
    if raw.startswith(ZIP_MAGIC):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            # Ta første .fit inne i ZIP-en
            for name in zf.namelist():
                if name.lower().endswith(".fit"):
                    return zf.read(name)
        raise ValueError(f"ZIP {path} inneholder ingen .fit-fil")
    # Ellers: må starte med FIT-header
    if len(raw) < 12 or raw[FIT_MAGIC_OFFSET : FIT_MAGIC_OFFSET + 4] != FIT_MAGIC:
        raise ValueError(f"{path} er ikke en gyldig FIT-fil (mangler .FIT-header)")
    return raw


def _speed_to_pace_sec_per_km(speed_m_per_s: float | None) -> float | None:
    """Konverter m/s → sekunder per kilometer."""
    if speed_m_per_s is None or speed_m_per_s <= 0:
        return None
    return 1000.0 / speed_m_per_s


def _extract_record(frame: fitdecode.FitDataMessage) -> dict | None:
    """Ekstraher felter fra en 'record'-frame til dict med våre kolonnenavn."""
    fields = {f.name: f.value for f in frame.fields}
    ts = fields.get("timestamp")
    if ts is None:
        return None
    # Normaliser speed — bruk enhanced_speed hvis tilgjengelig
    speed = fields.get("enhanced_speed")
    if speed is None:
        speed = fields.get("speed")
    return {
        "timestamp": ts,
        "hr": fields.get("heart_rate"),
        "pace_sec_per_km": _speed_to_pace_sec_per_km(speed),
        "speed_m_per_sec": float(speed) if speed is not None else None,
        "cadence": fields.get("cadence"),
        "power_w": fields.get("power"),
        "distance_m": fields.get("distance") if fields.get("enhanced_altitude") is None else fields.get("distance"),
        "altitude_m": fields.get("enhanced_altitude") or fields.get("altitude"),
        "vertical_oscillation_mm": fields.get("vertical_oscillation"),
        "ground_contact_ms": fields.get("stance_time"),
        "stride_length_mm": fields.get("step_length"),
    }


def parse_fit_to_samples(path: Path) -> tuple[list[dict], FitSummary]:
    """Parse FIT-fil til liste med sample-dicts + lettvekts-oppsummering.

    Samples har `t_offset_sec` relativt til første timestamp, slik at vi
    kan peke dem direkte på `workout_samples.workout_id` uten å vite
    hvilket workout_id rowen skal få ennå.
    """
    fit_bytes = _open_fit_bytes(path)

    records: list[dict] = []
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    session_info = {
        "total_distance": None,
        "total_timer_time": None,
        "sport": None,
        "sub_sport": None,
    }

    with fitdecode.FitReader(io.BytesIO(fit_bytes)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "record":
                rec = _extract_record(frame)
                if rec is None:
                    continue
                if first_ts is None:
                    first_ts = rec["timestamp"]
                last_ts = rec["timestamp"]
                records.append(rec)
            elif frame.name == "session":
                for f in frame.fields:
                    if f.name in session_info:
                        session_info[f.name] = f.value

    samples: list[dict] = []
    for r in records:
        offset = int((r["timestamp"] - first_ts).total_seconds()) if first_ts else 0
        samples.append({
            "t_offset_sec": offset,
            "hr": r["hr"],
            "pace_sec_per_km": r["pace_sec_per_km"],
            "speed_m_per_sec": r["speed_m_per_sec"],
            "cadence": r["cadence"],
            "power_w": r["power_w"],
            "distance_m": r["distance_m"],
            "altitude_m": r["altitude_m"],
            "vertical_oscillation_mm": r["vertical_oscillation_mm"],
            "ground_contact_ms": r["ground_contact_ms"],
            "stride_length_mm": r["stride_length_mm"],
        })

    summary = FitSummary(
        start_time_utc=first_ts,
        end_time_utc=last_ts,
        total_distance_m=session_info["total_distance"],
        total_timer_time_sec=session_info["total_timer_time"],
        sport=session_info["sport"],
        sub_sport=session_info["sub_sport"],
        record_count=len(samples),
    )
    return samples, summary
