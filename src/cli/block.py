"""`block` — treningsblokker (periodisering)."""

from __future__ import annotations

from datetime import date

import typer

from src.cli._common import emit
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)

VALID_PHASES = {"base", "build", "peak", "taper", "recovery"}


@app.command()
def set(
    phase: str = typer.Option(..., "--phase", help="base|build|peak|taper|recovery"),
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD"),
    name: str = typer.Option(None, "--name"),
    goal_id: int = typer.Option(None, "--goal-id"),
    notes: str = typer.Option(None, "--notes"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Legg til en ny treningsblokk."""
    if phase not in VALID_PHASES:
        typer.echo(f"Ugyldig phase. Bruk: {', '.join(sorted(VALID_PHASES))}", err=True)
        raise typer.Exit(1)
    with connect() as c:
        cur = c.execute(
            """
            INSERT INTO training_blocks (name, phase, start_date, end_date,
                                          primary_goal_id, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name or f"{phase}-{start}", phase, start, end, goal_id, notes),
        )
        block_id = cur.lastrowid
    emit({"id": block_id, "phase": phase, "start": start, "end": end},
         as_json=json_output,
         text=f"✓ Block #{block_id} ({phase}): {start} → {end}\n")


@app.command()
def current(json_output: bool = typer.Option(False, "--json")) -> None:
    """Vis aktiv blokk (den som inneholder dagens dato)."""
    today_iso = date.today().isoformat()
    with connect() as c:
        row = c.execute(
            """
            SELECT b.*, g.title AS goal_title
              FROM training_blocks b
              LEFT JOIN goals g ON b.primary_goal_id = g.id
             WHERE b.start_date <= ? AND b.end_date >= ?
             ORDER BY b.start_date DESC LIMIT 1
            """,
            (today_iso, today_iso),
        ).fetchone()
    data = dict(row) if row else {"active": False}
    if json_output:
        emit(data, as_json=True)
        return
    if not row:
        emit(data, as_json=False, text="Ingen aktiv blokk\n")
        return
    goal = f" → mål: {row['goal_title']}" if row["goal_title"] else ""
    emit(data, as_json=False,
         text=f"# {row['name']} ({row['phase']})\n"
              f"  {row['start_date']} → {row['end_date']}{goal}\n"
              + (f"  {row['notes']}\n" if row['notes'] else ""))


@app.command("list")
def list_(json_output: bool = typer.Option(False, "--json")) -> None:
    """Vis alle blokker (nyeste først)."""
    with connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM training_blocks ORDER BY start_date DESC"
        ).fetchall()]
    if json_output:
        emit({"count": len(rows), "rows": rows}, as_json=True)
        return
    if not rows:
        emit({}, as_json=False, text="Ingen blokker\n")
        return
    lines = [f"# Blokker ({len(rows)})"]
    for b in rows:
        lines.append(f"  #{b['id']} {b['name']} ({b['phase']}): "
                     f"{b['start_date']} → {b['end_date']}")
    emit({}, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
