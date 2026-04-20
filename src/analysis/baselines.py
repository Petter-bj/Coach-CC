"""Rolling baselines per metrikk + vindu.

Beregner median + MAD (median absolute deviation) over rullerende vinduer.
MAD er mer robust mot outliers enn standardavvik — én dag med plastpose
på vekta drar ikke ned baseline.

Metrikker vi baselinerer (per bruker-preferanse):
    resting_hr              → garmin_daily
    sleep_score             → garmin_sleep
    weight_kg               → withings_weight (første veiing per dag)
    hrv_last_night_ms       → garmin_hrv
    training_readiness      → garmin_daily.training_readiness_score
    stress_avg              → garmin_daily

Vinduer: 7d, 30d, 90d.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from statistics import median
from typing import Callable

# Minimumsantall datapunkter før vi regner baseline som gyldig
MIN_SAMPLES = {7: 4, 30: 14, 90: 30}

WINDOWS = (7, 30, 90)


@dataclass
class BaselineSpec:
    metric: str
    query: str  # Skal returnere kolonner (value) — én rad per dag


SPECS: list[BaselineSpec] = [
    BaselineSpec(
        metric="resting_hr",
        query="""
            SELECT resting_hr AS value FROM garmin_daily
             WHERE resting_hr IS NOT NULL AND local_date >= ?
        """,
    ),
    BaselineSpec(
        metric="sleep_score",
        query="""
            SELECT sleep_score AS value FROM garmin_sleep
             WHERE sleep_score IS NOT NULL AND local_date >= ?
        """,
    ),
    BaselineSpec(
        metric="weight_kg",
        query="""
            SELECT weight_kg AS value FROM (
                SELECT local_date, weight_kg,
                       ROW_NUMBER() OVER (
                           PARTITION BY local_date
                           ORDER BY measured_at_utc ASC
                       ) AS rn
                  FROM withings_weight
                 WHERE weight_kg IS NOT NULL AND local_date >= ?
            ) WHERE rn = 1
        """,
    ),
    BaselineSpec(
        metric="hrv_last_night_ms",
        query="""
            SELECT last_night_avg_ms AS value FROM garmin_hrv
             WHERE last_night_avg_ms IS NOT NULL AND local_date >= ?
        """,
    ),
    BaselineSpec(
        metric="training_readiness",
        query="""
            SELECT training_readiness_score AS value FROM garmin_daily
             WHERE training_readiness_score IS NOT NULL AND local_date >= ?
        """,
    ),
    BaselineSpec(
        metric="stress_avg",
        query="""
            SELECT stress_avg AS value FROM garmin_daily
             WHERE stress_avg IS NOT NULL AND local_date >= ?
        """,
    ),
]


def _mad(values: list[float], med: float) -> float:
    """Median absolute deviation."""
    return median(abs(v - med) for v in values)


def compute_baseline(values: list[float]) -> dict | None:
    """Returner robust baseline-dict eller None hvis for få datapunkter."""
    if len(values) < 2:
        return None
    med = median(values)
    return {
        "value": round(med, 2),  # value == median for robust baseline
        "median": round(med, 2),
        "mad": round(_mad(values, med), 2),
        "sample_size": len(values),
    }


def refresh_baselines(conn: sqlite3.Connection) -> int:
    """Beregn baselines for alle (metric, window)-par. Returner antall rader skrevet."""
    from datetime import date, timedelta

    written = 0
    today = date.today()

    for spec in SPECS:
        for window in WINDOWS:
            # window=7 skal gi nøyaktig 7 dager inkludert i dag
            since = (today - timedelta(days=window - 1)).isoformat()
            rows = conn.execute(spec.query, (since,)).fetchall()
            values = [r["value"] for r in rows if r["value"] is not None]

            min_n = MIN_SAMPLES[window]
            insufficient = len(values) < min_n

            if insufficient:
                row = {
                    "metric": spec.metric,
                    "window_days": window,
                    "value": None,
                    "median": None,
                    "mad": None,
                    "sample_size": len(values),
                    "insufficient_data": 1,
                }
            else:
                stats = compute_baseline(values) or {}
                row = {
                    "metric": spec.metric,
                    "window_days": window,
                    "value": stats.get("value"),
                    "median": stats.get("median"),
                    "mad": stats.get("mad"),
                    "sample_size": stats.get("sample_size"),
                    "insufficient_data": 0,
                }

            conn.execute(
                """
                INSERT INTO user_baselines
                    (metric, window_days, value, median, mad, sample_size,
                     computed_at, insufficient_data)
                VALUES (?, ?, ?, ?, ?, ?,
                        strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), ?)
                ON CONFLICT (metric, window_days) DO UPDATE SET
                    value = excluded.value,
                    median = excluded.median,
                    mad = excluded.mad,
                    sample_size = excluded.sample_size,
                    computed_at = excluded.computed_at,
                    insufficient_data = excluded.insufficient_data
                """,
                (
                    row["metric"], row["window_days"], row["value"],
                    row["median"], row["mad"], row["sample_size"],
                    row["insufficient_data"],
                ),
            )
            written += 1

    conn.commit()
    return written
