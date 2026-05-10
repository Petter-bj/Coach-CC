"""`cadence` — kadens-tracking for løping.

Garmin sampler cadence i steg/minutt × 0.5 (single-foot). Vi normaliserer
til total skritt-frekvens (begge bein) ved å gange med 2 hvis verdien
ser ut som single-foot (40-100 → multipliser).

Kommandoer:
  workout --workout-id N    Snitt + range for én økt
  trend --range last_30d    Trend over tid + sammenligning mot 170-mål
"""

from __future__ import annotations

import sqlite3

import typer

from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)

# Mål-kadens (steg per minutt totalt). Klassisk anbefaling for skadefri løping.
TARGET_CADENCE = 170


def _normalize_cadence(raw: int | float | None) -> int | None:
    """Konverter Garmin-rå-cadence (single-foot, halv av total) til total."""
    if raw is None:
        return None
    v = float(raw)
    if v <= 0:
        return None
    # Garmin sender ofte single-foot rate (40-100 spm). Total = 2× verdi.
    # Hvis verdi allerede er > 110, antar vi total.
    if v < 110:
        v = v * 2
    return int(round(v))


def _workout_cadence_stats(
    conn: sqlite3.Connection, workout_id: int,
) -> dict | None:
    """Snitt, median, p10, p90 av cadence for en workout. None hvis ingen data."""
    rows = conn.execute(
        """
        SELECT cadence, speed_m_per_sec
          FROM workout_samples
         WHERE workout_id = ?
           AND cadence IS NOT NULL
           AND speed_m_per_sec IS NOT NULL
           AND speed_m_per_sec > 1.5  -- filtrer gå-/stillstand-perioder
         ORDER BY t_offset_sec
        """,
        (workout_id,),
    ).fetchall()

    cadences = [_normalize_cadence(r["cadence"]) for r in rows]
    cadences = [c for c in cadences if c is not None]

    if not cadences:
        return None

    cadences_sorted = sorted(cadences)
    n = len(cadences_sorted)

    def _pct(p: float) -> int:
        idx = max(0, min(n - 1, int(n * p / 100)))
        return cadences_sorted[idx]

    return {
        "samples": n,
        "mean": round(sum(cadences) / n, 1),
        "median": _pct(50),
        "p10": _pct(10),
        "p90": _pct(90),
    }


@app.command()
def workout(
    workout_id: int = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis kadens-statistikk for én løpeøkt."""
    with connect() as c:
        meta = c.execute(
            "SELECT local_date, type, distance_m, duration_sec FROM workouts WHERE id = ?",
            (workout_id,),
        ).fetchone()
        if meta is None:
            typer.echo(f"Workout {workout_id} ikke funnet", err=True)
            raise typer.Exit(1)
        stats = _workout_cadence_stats(c, workout_id)

    if stats is None:
        emit({"workout_id": workout_id, "note": "ingen kadens-samples"},
             as_json=json_output,
             text=f"Ingen kadens-data for workout #{workout_id}\n")
        return

    diff = stats["median"] - TARGET_CADENCE
    direction = f"{'+' if diff >= 0 else ''}{diff} fra mål {TARGET_CADENCE}"

    payload = {
        "workout_id": workout_id,
        "date": meta["local_date"], "type": meta["type"],
        **stats,
        "target": TARGET_CADENCE,
        "diff_from_target": diff,
    }

    if json_output:
        emit(payload, as_json=True)
        return

    lines = [
        f"# Kadens — workout #{workout_id} ({meta['local_date']}, {meta['type']})",
        f"  Samples (m/ pace > 1.5 m/s): {stats['samples']}",
        f"  Snitt:   {stats['mean']} spm",
        f"  Median:  {stats['median']} spm  ({direction})",
        f"  P10-P90: {stats['p10']}-{stats['p90']} spm",
    ]
    emit(payload, as_json=False, text="\n".join(lines) + "\n")


@app.command()
def trend(
    range_: str = typer.Option("last_30d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis kadens-trend over et tidsrom — én linje per løpeøkt."""
    r = parse_range(range_)
    with connect() as c:
        rows = c.execute(
            """
            SELECT id, local_date, type, distance_m
              FROM workouts
             WHERE local_date BETWEEN ? AND ?
               AND superseded_by IS NULL
               AND (type = 'running' OR type LIKE 'run%')
             ORDER BY local_date
            """,
            (r.start, r.end),
        ).fetchall()
        items = []
        for w in rows:
            stats = _workout_cadence_stats(c, w["id"])
            if not stats:
                continue
            items.append({
                "workout_id": w["id"],
                "date": w["local_date"],
                "type": w["type"],
                "distance_km": round((w["distance_m"] or 0) / 1000.0, 2),
                "median_cadence": stats["median"],
                "mean_cadence": stats["mean"],
                "diff_from_target": stats["median"] - TARGET_CADENCE,
            })

    if not items:
        emit({"range": r.label, "items": [], "note": "ingen løpeøkter med kadens i perioden"},
             as_json=json_output,
             text=f"Ingen løpeøkter med kadens-data i {r.label}\n")
        return

    medians = [it["median_cadence"] for it in items]
    avg_median = round(sum(medians) / len(medians), 1)
    trend_first = sum(medians[:max(1, len(medians)//3)]) / max(1, len(medians)//3)
    trend_last = sum(medians[-max(1, len(medians)//3):]) / max(1, len(medians)//3)
    trend_delta = round(trend_last - trend_first, 1)

    payload = {
        "range": r.label,
        "items": items,
        "summary": {
            "n_workouts": len(items),
            "avg_median_cadence": avg_median,
            "target": TARGET_CADENCE,
            "avg_diff_from_target": round(avg_median - TARGET_CADENCE, 1),
            "trend_delta": trend_delta,
        },
    }

    if json_output:
        emit(payload, as_json=True)
        return

    lines = [
        f"# Kadens-trend ({r.label}) — mål: {TARGET_CADENCE} spm",
        f"  Snitt-median: {avg_median} ({avg_median - TARGET_CADENCE:+.1f} fra mål)",
        f"  Trend første→siste tredjedel: {trend_delta:+.1f} spm "
        f"({'oppover' if trend_delta > 0 else 'nedover' if trend_delta < 0 else 'flat'})",
        "",
        f"  {'Dato':10} {'Type':12} {'km':>5} {'median':>7} {'vs mål':>7}",
    ]
    for it in items:
        d = it["diff_from_target"]
        marker = "✓" if d >= -3 else ("~" if d >= -8 else "✗")
        lines.append(
            f"  {it['date']:10} {it['type']:12} {it['distance_km']:>5.2f} "
            f"{it['median_cadence']:>7} {d:>+7d} {marker}"
        )
    emit(payload, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
