"""`rpe` — sett eller vis RPE (Rate of Perceived Exertion) for en økt."""

from __future__ import annotations

import typer

from src.cli._common import emit
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("set")
def set_(
    workout_id: int = typer.Option(..., "--workout-id"),
    rpe: int = typer.Option(..., "--rpe", min=0, max=10),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Sett RPE og beregn session_load (RPE × varighet i minutter)."""
    with connect() as c:
        row = c.execute(
            "SELECT id, duration_sec FROM workouts WHERE id = ?",
            (workout_id,),
        ).fetchone()
        if not row:
            typer.echo(f"Workout #{workout_id} finnes ikke", err=True)
            raise typer.Exit(1)
        duration_min = (row["duration_sec"] or 0) / 60
        session_load = round(rpe * duration_min, 1)
        c.execute(
            "UPDATE workouts SET rpe = ?, session_load = ? WHERE id = ?",
            (rpe, session_load, workout_id),
        )
    emit({"workout_id": workout_id, "rpe": rpe, "session_load": session_load},
         as_json=json_output,
         text=f"✓ Workout #{workout_id}: RPE={rpe}, session_load={session_load}\n")


@app.command()
def missing(
    limit: int = typer.Option(10, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis siste økter som mangler RPE."""
    with connect() as c:
        rows = [dict(r) for r in c.execute(
            """
            SELECT id, source, type, local_date, duration_sec, distance_m
              FROM workouts
             WHERE rpe IS NULL AND superseded_by IS NULL
             ORDER BY started_at_utc DESC LIMIT ?
            """, (limit,)
        ).fetchall()]
    if json_output:
        emit({"count": len(rows), "rows": rows}, as_json=True)
        return
    if not rows:
        emit({}, as_json=False, text="Alle økter har RPE satt ✓\n")
        return
    lines = [f"# Økter uten RPE ({len(rows)})"]
    for w in rows:
        dur = (w["duration_sec"] or 0) / 60
        dist = f" {(w['distance_m'] or 0)/1000:.1f}km" if w['distance_m'] else ""
        lines.append(f"  #{w['id']:3}  {w['local_date']}  "
                     f"{w['source']:8} {w['type']:16} {dur:.0f}min{dist}")
    lines.append("\nSett med: uv run python -m src.cli.rpe set --workout-id <id> --rpe <0-10>")
    emit({}, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
