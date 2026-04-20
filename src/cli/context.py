"""`context` — livshendelser som påvirker trening (reise, sykdom, stress)."""

from __future__ import annotations

from datetime import date

import typer

from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)

VALID_CATEGORIES = {"travel", "illness", "stress", "life_event", "other"}


@app.command()
def log(
    category: str = typer.Option(..., "--category",
        help="travel|illness|stress|life_event|other"),
    starts: str = typer.Option(None, "--starts",
        help="YYYY-MM-DD (default: i dag)"),
    ends: str = typer.Option(None, "--ends",
        help="YYYY-MM-DD (NULL = pågående)"),
    notes: str = typer.Option(None, "--notes"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Logg kontekst-periode."""
    if category not in VALID_CATEGORIES:
        typer.echo(f"Ugyldig kategori: {', '.join(sorted(VALID_CATEGORIES))}", err=True)
        raise typer.Exit(1)
    start_date = starts or date.today().isoformat()
    with connect() as c:
        cur = c.execute(
            """
            INSERT INTO context_log (category, starts_on, ends_on, notes)
            VALUES (?, ?, ?, ?)
            """,
            (category, start_date, ends, notes),
        )
        ctx_id = cur.lastrowid
    emit({"id": ctx_id, "category": category}, as_json=json_output,
         text=f"✓ Kontekst #{ctx_id} ({category}): {start_date} → {ends or 'pågående'}\n")


@app.command()
def active(json_output: bool = typer.Option(False, "--json")) -> None:
    """Vis aktive kontekst-perioder (ends_on i fremtiden eller NULL)."""
    with connect() as c:
        rows = [dict(r) for r in c.execute(
            """
            SELECT * FROM context_log
             WHERE ends_on IS NULL OR ends_on >= date('now')
             ORDER BY starts_on DESC
            """
        ).fetchall()]
    if json_output:
        emit({"count": len(rows), "rows": rows}, as_json=True)
        return
    if not rows:
        emit({}, as_json=False, text="Ingen aktive kontekst-perioder\n")
        return
    lines = [f"# Aktive kontekst-perioder ({len(rows)})"]
    for r in rows:
        notes = f": {r['notes']}" if r["notes"] else ""
        lines.append(f"  #{r['id']} {r['category']:12} "
                     f"{r['starts_on']} → {r['ends_on'] or 'pågående'}{notes}")
    emit({}, as_json=False, text="\n".join(lines) + "\n")


@app.command()
def range(
    range_: str = typer.Option("last_30d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis alle kontekst-perioder som overlapper et intervall."""
    r = parse_range(range_)
    with connect() as c:
        rows = [dict(row) for row in c.execute(
            """
            SELECT * FROM context_log
             WHERE starts_on <= ?
               AND (ends_on IS NULL OR ends_on >= ?)
             ORDER BY starts_on DESC
            """,
            (r.end, r.start),
        ).fetchall()]
    if json_output:
        emit({"range": r.label, "count": len(rows), "rows": rows}, as_json=True)
        return
    lines = [f"# Kontekst {r.label} — {len(rows)} perioder"]
    for row in rows:
        notes = f": {row['notes']}" if row["notes"] else ""
        lines.append(f"  {row['category']:12} {row['starts_on']} → "
                     f"{row['ends_on'] or 'pågående'}{notes}")
    emit({}, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
