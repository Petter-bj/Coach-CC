"""`zones` — beregne tid i hver Olympiatoppen-sone fra workout_samples.

Kommandoer:
  workout --workout-id N         Sone-fordeling for én økt
  range --range last_7d          Aggregert ukesbilde
  list --range last_7d           Liste alle økter med sone-fordeling

Krever at workout_samples har data for økten (FIT-fil parset).
"""

from __future__ import annotations

import sqlite3
from collections import Counter

import typer

from src.cli._common import emit, parse_range
from src.coaching.philosophy import (
    RUN_HR_Z1_MAX_PCT, RUN_HR_Z2_MAX_PCT, RUN_HR_Z3_MAX_PCT, RUN_HR_Z4_MAX_PCT,
    classify_run_zone,
)
from src.coaching.preferences import get_hr_max
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _zone_minutes_for_workout(
    conn: sqlite3.Connection, workout_id: int, hr_max: int,
) -> dict:
    """Tell antall sekunder per sone for én workout. Returner zones-dict + totals."""
    rows = conn.execute(
        """
        SELECT t_offset_sec, hr
          FROM workout_samples
         WHERE workout_id = ?
           AND hr IS NOT NULL
         ORDER BY t_offset_sec
        """,
        (workout_id,),
    ).fetchall()

    counter: Counter[str] = Counter()
    no_zone = 0
    for r in rows:
        z = classify_run_zone(r["hr"], hr_max)
        if z is None:
            no_zone += 1
        else:
            counter[z] += 1

    total_sec = sum(counter.values())
    if total_sec == 0:
        return {
            "workout_id": workout_id,
            "samples_with_hr": len(rows),
            "samples_classified": 0,
            "minutes_total": 0,
            "zones": {z: {"sec": 0, "min": 0, "pct": 0} for z in
                      ("Z1", "Z2", "Z3", "Z4", "Z5")},
        }

    return {
        "workout_id": workout_id,
        "samples_with_hr": len(rows),
        "samples_classified": total_sec,
        "samples_unclassified": no_zone,
        "minutes_total": round(total_sec / 60.0, 1),
        "zones": {
            z: {
                "sec": counter[z],
                "min": round(counter[z] / 60.0, 1),
                "pct": round(100 * counter[z] / total_sec, 1),
            } for z in ("Z1", "Z2", "Z3", "Z4", "Z5")
        },
    }


def _format_zone_line(z: str, data: dict) -> str:
    pct = data["pct"]
    bar = "█" * max(1, int(pct / 5)) if pct > 0 else ""
    return f"  {z}  {data['min']:>5.1f} min  {pct:>5.1f}%  {bar}"


@app.command()
def workout(
    workout_id: int = typer.Argument(..., help="workouts.id"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis sone-fordeling for én økt basert på FIT-samples."""
    with connect() as c:
        hr_max = get_hr_max(c) or 195
        meta = c.execute(
            """
            SELECT w.local_date, w.type, w.duration_sec, w.distance_m, w.source
              FROM workouts w
             WHERE w.id = ?
            """,
            (workout_id,),
        ).fetchone()
        if meta is None:
            typer.echo(f"Workout {workout_id} ikke funnet", err=True)
            raise typer.Exit(1)

        data = _zone_minutes_for_workout(c, workout_id, hr_max)

    payload = {
        "workout": {
            "id": workout_id,
            "date": meta["local_date"],
            "type": meta["type"],
            "duration_sec": meta["duration_sec"],
            "distance_m": meta["distance_m"],
            "source": meta["source"],
        },
        "hr_max_used": hr_max,
        **data,
    }

    if json_output:
        emit(payload, as_json=True)
        return

    if data["samples_classified"] == 0:
        text = (
            f"# Workout #{workout_id} ({meta['local_date']}, {meta['type']})\n"
            f"  Ingen HR-samples i workout_samples — kjør sync hvis FIT-fil mangler.\n"
        )
        emit(payload, as_json=False, text=text)
        return

    km = (meta["distance_m"] or 0) / 1000.0
    lines = [
        f"# Workout #{workout_id} ({meta['local_date']}, {meta['type']})",
        f"  Distanse: {km:.2f} km · varighet: {(meta['duration_sec'] or 0)/60:.1f} min · "
        f"HRmax brukt: {hr_max}",
        f"  Klassifiserte samples: {data['samples_classified']} av {data['samples_with_hr']} "
        f"({round(100*data['samples_classified']/max(data['samples_with_hr'],1),1)}%)",
        "",
    ]
    for z in ("Z1", "Z2", "Z3", "Z4", "Z5"):
        lines.append(_format_zone_line(z, data["zones"][z]))
    emit(payload, as_json=False, text="\n".join(lines) + "\n")


@app.command("range")
def range_cmd(
    range_: str = typer.Option("last_7d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Aggregert sone-fordeling for alle økter i et tidsrom."""
    r = parse_range(range_)
    with connect() as c:
        hr_max = get_hr_max(c) or 195
        ids = c.execute(
            """
            SELECT id
              FROM workouts
             WHERE local_date BETWEEN ? AND ?
               AND superseded_by IS NULL
             ORDER BY local_date
            """,
            (r.start, r.end),
        ).fetchall()
        per_workout = []
        totals: Counter[str] = Counter()
        total_classified = 0
        for row in ids:
            wd = _zone_minutes_for_workout(c, row["id"], hr_max)
            if wd["samples_classified"] == 0:
                continue
            per_workout.append(wd)
            for z in ("Z1", "Z2", "Z3", "Z4", "Z5"):
                totals[z] += wd["zones"][z]["sec"]
                total_classified += wd["zones"][z]["sec"]

    if total_classified == 0:
        emit({"range": r.label, "total_workouts": 0,
              "note": "Ingen HR-samples i perioden"}, as_json=json_output,
             text=f"Ingen HR-samples for {r.label}\n")
        return

    zones_summary = {
        z: {
            "min": round(totals[z] / 60.0, 1),
            "pct": round(100 * totals[z] / total_classified, 1),
        } for z in ("Z1", "Z2", "Z3", "Z4", "Z5")
    }

    aerobic_min = (totals["Z1"] + totals["Z2"]) / 60.0
    sub_threshold_min = totals["Z3"] / 60.0
    grey_min = totals["Z4"] / 60.0
    hard_min = totals["Z5"] / 60.0

    flags = []
    aerobic_pct = (totals["Z1"] + totals["Z2"]) / total_classified
    grey_pct = totals["Z4"] / total_classified
    if aerobic_pct < 0.75:
        flags.append(f"Aerob (Z1+Z2): {aerobic_pct*100:.0f}% < 75% — for mye intensitet")
    if grey_pct > 0.10:
        flags.append(f"Grå sone (Z4): {grey_pct*100:.0f}% > 10% — sliter ut uten gevinst")

    payload = {
        "range": r.label,
        "start": r.start, "end": r.end,
        "total_workouts": len(per_workout),
        "total_minutes_classified": round(total_classified / 60.0, 1),
        "hr_max_used": hr_max,
        "zones": zones_summary,
        "aerobic_share": round(aerobic_pct, 3),
        "flags": flags,
    }

    if json_output:
        emit(payload, as_json=True)
        return

    lines = [
        f"# Sonefordeling {r.label}: {len(per_workout)} økter, "
        f"{round(total_classified/60.0,1)} min total",
        f"  HRmax: {hr_max}",
        "",
    ]
    for z in ("Z1", "Z2", "Z3", "Z4", "Z5"):
        lines.append(_format_zone_line(z, zones_summary[z]))
    lines.append("")
    lines.append(f"  Aerob (Z1+Z2): {round(aerobic_pct*100,1)}%  "
                 f"Sub-threshold (Z3): {round(zones_summary['Z3']['pct'],1)}%  "
                 f"Grå (Z4): {round(grey_pct*100,1)}%  "
                 f"Hard (Z5): {round(zones_summary['Z5']['pct'],1)}%")
    if flags:
        lines.append("")
        lines.append("  ⚠ Flags:")
        for f in flags:
            lines.append(f"    - {f}")
    emit(payload, as_json=False, text="\n".join(lines) + "\n")


@app.command("list")
def list_cmd(
    range_: str = typer.Option("last_7d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List alle økter i tidsrommet med kort sone-oppsummering."""
    r = parse_range(range_)
    with connect() as c:
        hr_max = get_hr_max(c) or 195
        rows = c.execute(
            """
            SELECT id, local_date, type, duration_sec, distance_m, source
              FROM workouts
             WHERE local_date BETWEEN ? AND ?
               AND superseded_by IS NULL
             ORDER BY local_date
            """,
            (r.start, r.end),
        ).fetchall()
        items = []
        for w in rows:
            zd = _zone_minutes_for_workout(c, w["id"], hr_max)
            if zd["samples_classified"] == 0:
                continue
            items.append({
                "id": w["id"], "date": w["local_date"], "type": w["type"],
                "source": w["source"], "minutes": zd["minutes_total"],
                "z2_min": zd["zones"]["Z2"]["min"],
                "z3_min": zd["zones"]["Z3"]["min"],
                "z4_min": zd["zones"]["Z4"]["min"],
                "z5_min": zd["zones"]["Z5"]["min"],
            })

    if not items:
        emit({"range": r.label, "items": []}, as_json=json_output,
             text=f"Ingen klassifiserbare økter i {r.label}\n")
        return

    if json_output:
        emit({"range": r.label, "items": items}, as_json=True)
        return

    lines = [f"# Økter med soner ({r.label}):"]
    lines.append(f"  {'Dato':10} {'Type':18} {'Min':>5} {'Z2':>5} {'Z3':>5} {'Z4':>5} {'Z5':>5}")
    for it in items:
        lines.append(
            f"  {it['date']:10} {it['type']:18} {it['minutes']:>5.0f} "
            f"{it['z2_min']:>5.1f} {it['z3_min']:>5.1f} "
            f"{it['z4_min']:>5.1f} {it['z5_min']:>5.1f}"
        )
    emit({"range": r.label, "items": items}, as_json=False,
         text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
