"""`intake` — alkohol + koffein-logging (HRV/recovery-korrelasjon)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import typer

from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def log(
    alcohol: float = typer.Option(None, "--alcohol",
        help="Enheter alkohol (1 enhet ≈ 12g ren alkohol, 1 øl 0.33L = 1 enhet)"),
    caffeine: int = typer.Option(None, "--caffeine",
        help="mg koffein (1 kopp kaffe ≈ 95mg, 1 espresso ≈ 64mg)"),
    notes: str = typer.Option(None, "--notes"),
    date_: str = typer.Option(None, "--date", help="YYYY-MM-DD (default: i dag)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Logg én hendelse (alkohol og/eller koffein)."""
    if alcohol is None and caffeine is None:
        typer.echo("Må ha minst --alcohol eller --caffeine", err=True)
        raise typer.Exit(1)
    d = date_ or date.today().isoformat()
    logged_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as c:
        cur = c.execute(
            """
            INSERT INTO intake_log (logged_at_utc, timezone, local_date,
                                     alcohol_units, caffeine_mg, notes)
            VALUES (?, 'Europe/Oslo', ?, ?, ?, ?)
            """,
            (logged_utc, d, alcohol, caffeine, notes),
        )
        intake_id = cur.lastrowid
    parts = []
    if alcohol:
        parts.append(f"{alcohol} enhet(er) alkohol")
    if caffeine:
        parts.append(f"{caffeine} mg koffein")
    emit({"id": intake_id, "local_date": d, "alcohol": alcohol, "caffeine": caffeine},
         as_json=json_output,
         text=f"✓ Logget for {d}: {' + '.join(parts)}\n")


@app.command()
def today(json_output: bool = typer.Option(False, "--json")) -> None:
    """Oppsummer dagens inntak."""
    d = date.today().isoformat()
    with connect() as c:
        row = c.execute(
            """
            SELECT COALESCE(SUM(alcohol_units), 0) AS total_alcohol,
                   COALESCE(SUM(caffeine_mg), 0) AS total_caffeine,
                   COUNT(*) AS entries
              FROM intake_log WHERE local_date = ?
            """, (d,)
        ).fetchone()
    data = {"date": d, **dict(row)}
    emit(data, as_json=json_output,
         text=f"Inntak {d}: {row['total_alcohol']} enheter alkohol, "
              f"{row['total_caffeine']} mg koffein ({row['entries']} logginger)\n")


@app.command()
def show(
    range_: str = typer.Option("last_7d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis inntak per dag over et intervall."""
    r = parse_range(range_)
    with connect() as c:
        rows = [dict(row) for row in c.execute(
            """
            SELECT local_date,
                   COALESCE(SUM(alcohol_units), 0) AS alcohol,
                   COALESCE(SUM(caffeine_mg), 0) AS caffeine
              FROM intake_log
             WHERE local_date BETWEEN ? AND ?
             GROUP BY local_date
             ORDER BY local_date DESC
            """,
            (r.start, r.end),
        ).fetchall()]
    data = {"range": r.label, "count": len(rows), "rows": rows}
    if json_output:
        emit(data, as_json=True)
        return
    lines = [f"# Inntak {r.label} — {len(rows)} dager med data"]
    for row in rows:
        lines.append(f"  {row['local_date']}  alk={row['alcohol']} enh  "
                     f"koff={row['caffeine']} mg")
    emit(data, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
