"""`injury` — aktive skader og nigglens-tracking."""

from __future__ import annotations

from datetime import date

import typer

from src.cli._common import emit
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def log(
    body_part: str = typer.Option(..., "--body-part",
        help="f.eks. knee_right, lower_back, shoulder_left"),
    severity: int = typer.Option(..., "--severity", min=1, max=3,
        help="1=niggle, 2=moderat, 3=alvorlig"),
    notes: str = typer.Option(None, "--notes"),
    started: str = typer.Option(None, "--started", help="YYYY-MM-DD (default: i dag)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Logg ny skade."""
    with connect() as c:
        cur = c.execute(
            """
            INSERT INTO injuries (body_part, severity, started_at, notes)
            VALUES (?, ?, ?, ?)
            """,
            (body_part, severity, started or date.today().isoformat(), notes),
        )
        inj_id = cur.lastrowid
    emit({"id": inj_id, "body_part": body_part, "severity": severity},
         as_json=json_output,
         text=f"✓ Skade #{inj_id} registrert: {body_part} (sev {severity})\n")


@app.command()
def update(
    id_: int = typer.Option(..., "--id"),
    status: str = typer.Option(None, "--status", help="active|healing|resolved"),
    notes: str = typer.Option(None, "--notes"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Oppdater skade-status."""
    sets: list[str] = []
    params: list = []
    if status:
        sets.append("status = ?")
        params.append(status)
        if status == "resolved":
            sets.append("resolved_at = date('now')")
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if not sets:
        emit({}, as_json=json_output, text="Ingenting å oppdatere\n")
        return
    params.append(id_)
    with connect() as c:
        c.execute(f"UPDATE injuries SET {', '.join(sets)} WHERE id = ?", params)
    emit({"id": id_, "status": status}, as_json=json_output,
         text=f"✓ Skade #{id_} oppdatert\n")


@app.command()
def active(json_output: bool = typer.Option(False, "--json")) -> None:
    """Vis aktive skader (status=active eller healing)."""
    with connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM injuries WHERE status IN ('active', 'healing') "
            "ORDER BY severity DESC, started_at DESC"
        ).fetchall()]
    if json_output:
        emit({"count": len(rows), "rows": rows}, as_json=True)
        return
    if not rows:
        emit({}, as_json=False, text="Ingen aktive skader\n")
        return
    lines = [f"# Aktive skader ({len(rows)})"]
    for i in rows:
        notes = f" — {i['notes']}" if i["notes"] else ""
        lines.append(
            f"  #{i['id']} {i['body_part']} sev={i['severity']} "
            f"({i['status']}, siden {i['started_at']}){notes}"
        )
    emit({}, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
