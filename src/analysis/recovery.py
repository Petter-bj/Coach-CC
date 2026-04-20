"""Recovery-snapshot og treningsanbefaling.

Samler alle kontekst-signalene (Garmin-readiness, HRV, søvn, RHR, ACR,
skader, sykdom) og produserer én strukturert anbefaling.

ACR — Acute:Chronic Workload Ratio:
    acute = sum(session_load siste 7 dager)
    chronic = avg(daglig session_load siste 28 dager) × 7
    acr = acute / chronic

Terskler (per plan §7a):
    0.8 – 1.3 : sweet spot, normal trening
    > 1.5     : forhøyet skade-risiko, deload
    < 0.8     : undertrening (OK under taper)

Hvis `workouts.session_load` er NULL faller vi tilbake på Garmins `acuteLoad`
når det er tilgjengelig, ellers en grov estimat: `duration_min × 5` (RPE
median ≈ 5). Dette flaggest i responsen så Claude vet om estimatet er
basert på solide data eller ikke.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

ACR_SWEET_LOW = 0.8
ACR_SWEET_HIGH = 1.3
ACR_RISK = 1.5
READINESS_LOW = 30

# Fallback-RPE når workouts.rpe er NULL
DEFAULT_RPE = 5


@dataclass
class LoadCalc:
    acute_load: float
    chronic_load: float
    acr: float | None
    acr_zone: str  # sweet | risk | undertraining | insufficient
    source: str    # computed | garmin_acute
    workouts_counted: int
    workouts_without_rpe: int


def _load_for_workout(w: dict) -> float:
    """Session-load estimat.

    Rekkefølge:
    1. Eksplisitt pre-beregnet `session_load`-kolonne
    2. `rpe × duration_min` hvis RPE er satt
    3. `duration_min × DEFAULT_RPE` som siste fallback
    """
    if w.get("session_load"):
        return float(w["session_load"])
    dur_min = (w.get("duration_sec") or 0) / 60
    if w.get("rpe"):
        return dur_min * float(w["rpe"])
    return dur_min * DEFAULT_RPE


MIN_CHRONIC_DAYS = 14  # færre enn dette → ACR er ikke meningsfull


def compute_load(conn: sqlite3.Connection, as_of: date) -> LoadCalc:
    """Beregn acute/chronic/ACR fra workouts-tabellen.

    Ekskluderer superseded-rader. Bruker workouts.session_load hvis satt,
    ellers estimert via DEFAULT_RPE × duration_min.

    Chronic-delen divideres på *faktisk antall dager dekket* (ikke alltid
    28) — ellers overvurderes ACR i systemets første måned.
    """
    acute_start = (as_of - timedelta(days=6)).isoformat()
    chronic_start = (as_of - timedelta(days=27)).isoformat()
    as_of_iso = as_of.isoformat()

    rows = [
        dict(r) for r in conn.execute(
            """
            SELECT local_date, duration_sec, rpe, session_load
              FROM workouts
             WHERE superseded_by IS NULL
               AND local_date BETWEEN ? AND ?
            """,
            (chronic_start, as_of_iso),
        ).fetchall()
    ]

    acute_sum = 0.0
    chronic_sum = 0.0
    workouts_counted = 0
    workouts_without_rpe = 0
    min_date: str | None = None

    for w in rows:
        load = _load_for_workout(w)
        if not load:
            continue
        workouts_counted += 1
        if not w.get("session_load"):
            workouts_without_rpe += 1
        if w["local_date"] >= acute_start:
            acute_sum += load
        chronic_sum += load
        if min_date is None or w["local_date"] < min_date:
            min_date = w["local_date"]

    # Faktiske dager dekket i chronic-vinduet
    if min_date:
        chronic_days_covered = (
            (as_of - date.fromisoformat(min_date)).days + 1
        )
        chronic_days_covered = min(chronic_days_covered, 28)
    else:
        chronic_days_covered = 0

    acr: float | None = None
    chronic_weekly = 0.0
    if chronic_days_covered >= MIN_CHRONIC_DAYS and chronic_sum > 0:
        chronic_weekly = (chronic_sum / chronic_days_covered) * 7
        acr = round(acute_sum / chronic_weekly, 2)

    # Sonefordeling
    if acr is None:
        zone = "insufficient"
    elif acr > ACR_RISK:
        zone = "risk"
    elif ACR_SWEET_LOW <= acr <= ACR_SWEET_HIGH:
        zone = "sweet"
    elif acr < ACR_SWEET_LOW:
        zone = "undertraining"
    else:
        # 1.3 < acr <= 1.5
        zone = "elevated"

    return LoadCalc(
        acute_load=round(acute_sum, 1),
        chronic_load=round(chronic_weekly, 1),
        acr=acr,
        acr_zone=zone,
        source="computed",
        workouts_counted=workouts_counted,
        workouts_without_rpe=workouts_without_rpe,
    )


def _baseline(conn: sqlite3.Connection, metric: str, window: int = 30) -> dict | None:
    row = conn.execute(
        """
        SELECT value, sample_size, insufficient_data
          FROM user_baselines
         WHERE metric = ? AND window_days = ?
        """,
        (metric, window),
    ).fetchone()
    if row is None or row["insufficient_data"]:
        return None
    return {"value": row["value"], "n": row["sample_size"]}


def _latest_rows(conn: sqlite3.Connection, as_of: date) -> dict[str, dict]:
    """Slå opp siste tilgjengelige rader per relevant tabell."""
    as_of_iso = as_of.isoformat()
    rows: dict[str, dict] = {}

    # Garmin daily (kan mangle for dagen — ta siste ≤ as_of)
    row = conn.execute(
        """
        SELECT * FROM garmin_daily
         WHERE local_date <= ?
         ORDER BY local_date DESC LIMIT 1
        """,
        (as_of_iso,),
    ).fetchone()
    if row:
        rows["garmin_daily"] = dict(row)

    row = conn.execute(
        "SELECT * FROM garmin_sleep WHERE local_date <= ? "
        "ORDER BY local_date DESC LIMIT 1", (as_of_iso,),
    ).fetchone()
    if row:
        rows["garmin_sleep"] = dict(row)

    row = conn.execute(
        "SELECT * FROM garmin_hrv WHERE local_date <= ? "
        "ORDER BY local_date DESC LIMIT 1", (as_of_iso,),
    ).fetchone()
    if row:
        rows["garmin_hrv"] = dict(row)

    row = conn.execute(
        "SELECT * FROM wellness_daily WHERE local_date <= ? "
        "ORDER BY local_date DESC LIMIT 1", (as_of_iso,),
    ).fetchone()
    if row:
        rows["wellness"] = dict(row)

    return rows


def _delta_vs_baseline(value: float | None, baseline: dict | None) -> dict:
    """Sammenlign verdi mot baseline."""
    if value is None or baseline is None:
        return {"value": value, "baseline": baseline["value"] if baseline else None,
                "delta": None, "status": "unknown"}
    delta = round(value - baseline["value"], 2)
    # Generisk vurdering: +/-5% er "normal", ellers markant
    pct = delta / baseline["value"] * 100 if baseline["value"] else 0
    if abs(pct) < 5:
        status = "on_baseline"
    elif pct > 0:
        status = "above"
    else:
        status = "below"
    return {
        "value": value,
        "baseline": baseline["value"],
        "delta": delta,
        "delta_pct": round(pct, 1),
        "status": status,
    }


def _decide_recommendation(
    signals: dict[str, Any],
    load: LoadCalc,
) -> tuple[str, list[str]]:
    """Regel-basert valg av anbefaling. Returnerer (recommendation, rationale)."""
    reasons: list[str] = []

    # Ubetingede REST-grunner
    if signals.get("illness_flag"):
        reasons.append("Du har flagget dagen som syk — kroppen skal bruke energi på å bli frisk.")
        return "rest", reasons

    severe_injuries = [i for i in signals.get("active_injuries", []) if i["severity"] >= 3]
    if severe_injuries:
        for inj in severe_injuries:
            reasons.append(f"Alvorlig skade ({inj['body_part']}, sev={inj['severity']}) — hvil.")
        return "rest", reasons

    # ACR-basert deload
    if load.acr_zone == "risk":
        reasons.append(
            f"ACR {load.acr:.2f} over skade-grense ({ACR_RISK}) — volumet har økt for raskt."
        )
        return "light", reasons
    if load.acr_zone == "elevated":
        reasons.append(
            f"ACR {load.acr:.2f} over sweet spot (1.3) — hold tilbake litt."
        )

    # Garmin readiness
    readiness = signals.get("garmin_readiness", {})
    if readiness.get("score") is not None and readiness["score"] < READINESS_LOW:
        reasons.append(
            f"Garmin training readiness {readiness['score']} ({readiness.get('level')}) "
            f"— lav."
        )
        if not reasons or "risk" in load.acr_zone:
            return "light", reasons
        return "easy", reasons

    # HRV markant under baseline
    hrv = signals.get("hrv_vs_baseline", {})
    if hrv.get("status") == "below" and hrv.get("delta_pct", 0) < -10:
        reasons.append(
            f"HRV {hrv['value']}ms, {hrv['delta_pct']:.1f}% under baseline — "
            f"kroppen er ikke helt frisk."
        )
        return "easy", reasons

    # Moderate injury → unngå involverte øvelser, ellers normalt
    moderate_injuries = [i for i in signals.get("active_injuries", []) if i["severity"] == 2]
    if moderate_injuries:
        for inj in moderate_injuries:
            reasons.append(
                f"Moderat skade ({inj['body_part']}) — unngå belastende øvelser for området."
            )

    # Low wellness scores
    wellness = signals.get("wellness") or {}
    if wellness.get("motivation") and wellness["motivation"] <= 3:
        reasons.append(f"Lav motivasjon ({wellness['motivation']}/10) — kort økt kan være nok.")
        return "easy", reasons
    if wellness.get("muscle_soreness") and wellness["muscle_soreness"] >= 8:
        reasons.append(f"Høy sårhet ({wellness['muscle_soreness']}/10) — mobilitet/rolig.")
        return "easy", reasons

    # Default-sti
    if load.acr_zone == "undertraining":
        reasons.append(
            f"ACR {load.acr:.2f} under 0.8 — rom for å bygge volum, gitt at du ikke er i taper."
        )
        return "normal", reasons

    if not reasons:
        reasons.append("Alle signaler innen normal range — følg planen.")
    return "normal", reasons


def recovery_snapshot(
    conn: sqlite3.Connection, as_of: date | None = None
) -> dict[str, Any]:
    """Full recovery-snapshot med anbefaling."""
    as_of = as_of or date.today()
    latest = _latest_rows(conn, as_of)
    load = compute_load(conn, as_of)

    # Baselines
    rhr_base = _baseline(conn, "resting_hr")
    sleep_base = _baseline(conn, "sleep_score")
    hrv_base = _baseline(conn, "hrv_last_night_ms")
    readiness_base = _baseline(conn, "training_readiness")

    # Current values
    daily = latest.get("garmin_daily") or {}
    sleep = latest.get("garmin_sleep") or {}
    hrv = latest.get("garmin_hrv") or {}
    wellness = latest.get("wellness") or {}

    rhr_delta = _delta_vs_baseline(daily.get("resting_hr"), rhr_base)
    sleep_delta = _delta_vs_baseline(sleep.get("sleep_score"), sleep_base)
    hrv_delta = _delta_vs_baseline(hrv.get("last_night_avg_ms"), hrv_base)
    readiness_delta = _delta_vs_baseline(
        daily.get("training_readiness_score"), readiness_base
    )

    # Active injuries/contexts
    active_injuries = [dict(r) for r in conn.execute(
        """
        SELECT body_part, severity, status, notes, started_at
          FROM injuries WHERE status IN ('active', 'healing')
        """
    ).fetchall()]

    active_contexts = [dict(r) for r in conn.execute(
        """
        SELECT category, starts_on, ends_on, notes
          FROM context_log
         WHERE ends_on IS NULL OR ends_on >= date('now')
        """
    ).fetchall()]

    signals = {
        "illness_flag": bool(wellness.get("illness_flag")),
        "active_injuries": active_injuries,
        "garmin_readiness": {
            "score": daily.get("training_readiness_score"),
            "level": daily.get("training_readiness_level"),
        },
        "hrv_vs_baseline": hrv_delta,
        "wellness": wellness,
    }

    recommendation, rationale = _decide_recommendation(signals, load)

    return {
        "as_of": as_of.isoformat(),
        "recommendation": recommendation,
        "rationale": rationale,
        "load": {
            "acute_7d": load.acute_load,
            "chronic_28d_weekly": load.chronic_load,
            "acr": load.acr,
            "zone": load.acr_zone,
            "workouts_counted": load.workouts_counted,
            "workouts_without_rpe": load.workouts_without_rpe,
        },
        "readiness": {
            "garmin_score": daily.get("training_readiness_score"),
            "garmin_level": daily.get("training_readiness_level"),
            "vs_baseline": readiness_delta,
        },
        "hrv": hrv_delta,
        "sleep_score": sleep_delta,
        "resting_hr": rhr_delta,
        "sleep_duration_hours": (
            round((sleep.get("duration_sec") or 0) / 3600, 1) if sleep else None
        ),
        "wellness": wellness,
        "active_injuries": active_injuries,
        "active_contexts": active_contexts,
    }
