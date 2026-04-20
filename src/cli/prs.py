"""`prs` — personal records per øvelse basert på estimert 1RM (Epley)."""

from __future__ import annotations

import typer

from src.cli._common import emit
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def main(
    exercise: str = typer.Option(None, "--exercise",
        help="Filtrer på én øvelse"),
    limit: int = typer.Option(20, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis e1RM-rekorder per øvelse."""
    with connect() as c:
        # Window-funksjon for å plukke beste sett per øvelse i én query
        sql = """
            WITH ranked AS (
                SELECT s.exercise, s.e1rm_kg, s.weight_kg, s.reps,
                       w.local_date,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.exercise
                           ORDER BY s.e1rm_kg DESC, w.started_at_utc DESC
                       ) AS rn,
                       COUNT(*) OVER (PARTITION BY s.exercise) AS total_sets
                  FROM strength_sets s
                  JOIN strength_sessions ss ON s.session_id = ss.id
                  JOIN workouts w ON ss.workout_id = w.id
                 WHERE s.e1rm_kg IS NOT NULL
            )
            SELECT exercise, e1rm_kg AS best_e1rm, weight_kg, reps, local_date,
                   total_sets
              FROM ranked
             WHERE rn = 1
        """
        params: list = []
        if exercise:
            sql += " AND exercise = ?"
            params.append(exercise)
        sql += " ORDER BY best_e1rm DESC LIMIT ?"
        params.append(limit)

        rows = [dict(r) for r in c.execute(sql, params).fetchall()]

    data = {"count": len(rows), "rows": rows}
    if json_output:
        emit(data, as_json=True)
        return
    if not rows:
        emit(data, as_json=False, text="Ingen e1RM-data ennå. Logg styrkeøkter først.\n")
        return
    lines = [f"# PRs ({len(rows)} øvelser)"]
    lines.append(f"  {'Øvelse':<25} {'e1RM':>8}  best set")
    for r in rows:
        ctx = f"{r['weight_kg']}kg × {r['reps']} ({r['local_date']})"
        lines.append(f"  {r['exercise']:<25} {r['best_e1rm']:>7.1f}kg  {ctx}")
    emit(data, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
