"""`volume` — aggregert treningsvolum per muskelgruppe."""

from __future__ import annotations

from collections import defaultdict

import typer

from src.analysis.exercises import lookup
from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)

# Andel volum som telles for sekundær muskelgruppe
SECONDARY_WEIGHT = 0.5


@app.command()
def main(
    range_: str = typer.Option("last_7d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Total volum per muskelgruppe over et intervall."""
    r = parse_range(range_)
    with connect() as c:
        sets = [dict(row) for row in c.execute(
            """
            SELECT s.exercise, s.reps, s.weight_kg
              FROM strength_sets s
              JOIN strength_sessions ss ON s.session_id = ss.id
              JOIN workouts w ON ss.workout_id = w.id
             WHERE w.local_date BETWEEN ? AND ?
            """,
            (r.start, r.end),
        ).fetchall()]

    totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"sets": 0, "reps": 0, "volume_kg": 0.0}
    )
    unknown_exercises: set[str] = set()

    for s in sets:
        info = lookup(s["exercise"])
        reps = s["reps"] or 0
        weight = s["weight_kg"] or 0
        volume = reps * weight  # kg × reps
        if info["unknown"]:
            unknown_exercises.add(s["exercise"])
            continue
        # Primær muskelgruppe: 100%
        totals[info["primary"]]["sets"] += 1
        totals[info["primary"]]["reps"] += reps
        totals[info["primary"]]["volume_kg"] += volume
        # Sekundære: 50%
        for sec in info["secondary"]:
            totals[sec]["sets"] += SECONDARY_WEIGHT
            totals[sec]["reps"] += reps * SECONDARY_WEIGHT
            totals[sec]["volume_kg"] += volume * SECONDARY_WEIGHT

    # Sorter etter volum
    rows = [
        {"muscle": m, "sets": round(t["sets"], 1),
         "reps": round(t["reps"], 0), "volume_kg": round(t["volume_kg"], 0)}
        for m, t in totals.items()
    ]
    rows.sort(key=lambda x: x["volume_kg"], reverse=True)

    data = {
        "range": r.label,
        "total_sets_logged": len(sets),
        "unknown_exercises": sorted(unknown_exercises),
        "by_muscle": rows,
    }

    if json_output:
        emit(data, as_json=True)
        return

    lines = [f"# Volum per muskelgruppe {r.label} ({r.start} → {r.end})"]
    if not rows:
        lines.append("  Ingen styrkeøkter logget i perioden")
    else:
        lines.append(f"  {len(sets)} sett logget på {len(rows)} muskelgrupper")
        lines.append("")
        lines.append(f"  {'Muskel':<14} {'Sett':>6} {'Reps':>6} {'Volum (kg)':>12}")
        for row in rows:
            lines.append(f"  {row['muscle']:<14} {row['sets']:>6} "
                         f"{row['reps']:>6.0f} {row['volume_kg']:>12.0f}")
    if unknown_exercises:
        lines.append("")
        lines.append(f"  ⚠ Ukjente øvelser (ikke i mapping):")
        for ex in sorted(unknown_exercises):
            lines.append(f"    {ex}")
        lines.append("  Legg til i src/data/exercise_muscles.json for å inkludere.")
    emit(data, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
