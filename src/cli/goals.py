"""`goals` — CRUD for treningsmål."""

from __future__ import annotations

import typer

from src.cli._common import emit
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("list")
def list_(
    all_: bool = typer.Option(False, "--all", help="Inkluder achieved/abandoned"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis mål (aktive som default)."""
    with connect() as c:
        sql = "SELECT * FROM goals"
        if not all_:
            sql += " WHERE status = 'active'"
        sql += " ORDER BY priority, target_date"
        rows = [dict(r) for r in c.execute(sql).fetchall()]

    if json_output:
        emit({"count": len(rows), "rows": rows}, as_json=True)
        return
    if not rows:
        emit({}, as_json=False, text="Ingen mål\n")
        return
    lines = [f"# Mål ({len(rows)})"]
    for g in rows:
        target = f" {g['target_value']}" if g["target_value"] else ""
        metric = f" [{g['metric']}{target}]" if g["metric"] else ""
        prio = g["priority"] or "?"
        tgt_date = f" innen {g['target_date']}" if g["target_date"] else ""
        status = g["status"]
        status_tag = f" ({status})" if status != "active" else ""
        lines.append(f"  [{prio}] {g['title']}{metric}{tgt_date}{status_tag}")
        if g["notes"]:
            lines.append(f"       — {g['notes']}")
    emit({}, as_json=False, text="\n".join(lines) + "\n")


@app.command()
def add(
    title: str = typer.Option(..., "--title"),
    target_date: str = typer.Option(None, "--target-date", help="YYYY-MM-DD"),
    metric: str = typer.Option(None, "--metric",
        help="f.eks. 10k_time_sec, bench_1rm_kg, weight_kg"),
    target: float = typer.Option(None, "--target", help="Måltall"),
    priority: str = typer.Option("B", "--priority", help="A | B | C"),
    notes: str = typer.Option(None, "--notes"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Legg til et nytt mål."""
    with connect() as c:
        cur = c.execute(
            """
            INSERT INTO goals (title, target_date, metric, target_value,
                               priority, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title, target_date, metric, target, priority, notes),
        )
        goal_id = cur.lastrowid
    emit({"id": goal_id, "title": title}, as_json=json_output,
         text=f"✓ Mål #{goal_id} opprettet: {title}\n")


@app.command()
def update(
    id_: int = typer.Option(..., "--id"),
    status: str = typer.Option(None, "--status",
        help="active | achieved | abandoned | on_hold"),
    notes: str = typer.Option(None, "--notes"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Oppdater status/notes på et mål."""
    sets: list[str] = []
    params: list = []
    if status:
        sets.append("status = ?")
        params.append(status)
        if status == "achieved":
            sets.append("resolved_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if not sets:
        emit({}, as_json=json_output, text="Ingenting å oppdatere\n")
        return
    params.append(id_)
    with connect() as c:
        c.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id = ?", params)
    emit({"id": id_, "status": status}, as_json=json_output,
         text=f"✓ Mål #{id_} oppdatert\n")


if __name__ == "__main__":
    app()
