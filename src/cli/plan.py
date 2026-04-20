"""`plan` — ukentlig treningsplan + adherence-tracking."""

from __future__ import annotations

from datetime import date

import typer

from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def show(
    week_of: str = typer.Option(None, "--week-of", help="YYYY-MM-DD (default: i dag)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis planlagt uke."""
    anchor = week_of or date.today().isoformat()
    r = parse_range(f"week_of={anchor}")
    with connect() as c:
        rows = [dict(row) for row in c.execute(
            """
            SELECT p.*,
                   w.id AS completed_workout_id, w.type AS completed_type,
                   w.duration_sec, w.distance_m
              FROM planned_sessions p
              LEFT JOIN workouts w ON p.workout_id = w.id
             WHERE p.planned_date BETWEEN ? AND ?
             ORDER BY p.planned_date
            """,
            (r.start, r.end),
        ).fetchall()]
    data = {"week_of": r.label, "start": r.start, "end": r.end, "rows": rows}
    if json_output:
        emit(data, as_json=True)
        return
    if not rows:
        emit(data, as_json=False, text=f"Ingen planlagte økter {r.start} → {r.end}\n")
        return
    lines = [f"# Plan for uken {r.start} → {r.end}"]
    for p in rows:
        status_tag = {"planned": "□", "completed": "✓", "skipped": "✗",
                      "modified": "↻"}.get(p["status"], "?")
        lines.append(f"  {status_tag} {p['planned_date']}  "
                     f"{(p['type'] or '—'):15} {p['description'] or ''}")
    emit(data, as_json=False, text="\n".join(lines) + "\n")


@app.command()
def update(
    date_: str = typer.Option(..., "--date"),
    type_: str = typer.Option(None, "--type"),
    description: str = typer.Option(None, "--description"),
    status: str = typer.Option(None, "--status",
        help="planned|completed|skipped|modified"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Legg til eller oppdater planlagt økt for en dato."""
    with connect() as c:
        existing = c.execute(
            "SELECT id FROM planned_sessions WHERE planned_date = ?", (date_,)
        ).fetchone()
        if existing:
            sets: list[str] = []
            params: list = []
            if type_:
                sets.append("type = ?"); params.append(type_)
            if description is not None:
                sets.append("description = ?"); params.append(description)
            if status:
                sets.append("status = ?"); params.append(status)
            if sets:
                params.append(existing["id"])
                c.execute(f"UPDATE planned_sessions SET {', '.join(sets)} "
                          "WHERE id = ?", params)
            row_id = existing["id"]
            verb = "oppdatert"
        else:
            cur = c.execute(
                """
                INSERT INTO planned_sessions (planned_date, type, description, status)
                VALUES (?, ?, ?, ?)
                """,
                (date_, type_, description, status or "planned"),
            )
            row_id = cur.lastrowid
            verb = "opprettet"
    emit({"id": row_id, "date": date_}, as_json=json_output,
         text=f"✓ Plan #{row_id} {verb} for {date_}\n")


@app.command()
def adherence(
    range_: str = typer.Option("last_7d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Beregn planadherance-prosent (gjennomført vs planlagt)."""
    r = parse_range(range_)
    with connect() as c:
        rows = c.execute(
            """
            SELECT status, COUNT(*) AS n
              FROM planned_sessions
             WHERE planned_date BETWEEN ? AND ?
             GROUP BY status
            """,
            (r.start, r.end),
        ).fetchall()

    counts = {row["status"]: row["n"] for row in rows}
    planned = sum(counts.values())
    completed = counts.get("completed", 0)
    skipped = counts.get("skipped", 0)
    modified = counts.get("modified", 0)

    if planned == 0:
        data = {"range": r.label, "planned": 0, "adherence_pct": None,
                "note": "Ingen planlagte økter i perioden"}
        emit(data, as_json=json_output,
             text=f"Ingen planlagte økter i {r.label} — adherence N/A\n")
        return

    pct = round(100 * completed / planned, 1)
    data = {
        "range": r.label,
        "planned": planned,
        "completed": completed,
        "skipped": skipped,
        "modified": modified,
        "adherence_pct": pct,
    }
    emit(data, as_json=json_output,
         text=f"Planadherance {r.label}: {pct}% "
              f"({completed}/{planned} completed, {skipped} skipped, {modified} modified)\n")


if __name__ == "__main__":
    app()
