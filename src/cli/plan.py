"""`plan` — ukentlig treningsplan + adherence-tracking + proposer."""

from __future__ import annotations

from datetime import date, timedelta

import typer

from src.cli._common import emit, parse_range
from src.coaching.proposer import ProposedWeek, propose_week
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


def _format_proposal(proposal: ProposedWeek) -> str:
    """Formater forslaget som lesbar tekst for terminal / Telegram."""
    variant_label = {
        "A_green": "🟢 Grønn (full struktur)",
        "B_yellow": "🟡 Gul (softer uke)",
        "C_red": "🔴 Rød (ingen løp, prehab-fokus)",
    }.get(proposal.variant, proposal.variant)

    lines = [
        f"# Forslag uke {proposal.week_start_date} (variant: {variant_label})",
        f"# Phase: {proposal.phase}  ·  Estimert løp-km: {proposal.estimated_run_km}",
        "",
        "## Reasoning",
    ]
    for r in proposal.reasoning:
        lines.append(f"  - {r}")
    lines.append("")
    lines.append("## Økter")
    for s in proposal.sessions:
        hr = ""
        if s.hr_target:
            hr = f" [HR {s.hr_target[0]}-{s.hr_target[1]}]"
        zone = f" ({s.intensity_zone})" if s.intensity_zone else ""
        dur = f" · {s.duration_min} min" if s.duration_min else ""
        lines.append(f"  {s.day_of_week} {s.planned_date}: {s.type}{zone}{dur}{hr}")
        lines.append(f"    → {s.description}")
        if s.notes:
            lines.append(f"    ⚠ {s.notes}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _next_monday(anchor: date) -> date:
    """Returner mandagen som er nærmest fremover (eller i dag hvis mandag)."""
    days_ahead = (0 - anchor.weekday()) % 7  # 0 = mandag
    return anchor + timedelta(days=days_ahead or 7)  # skip to next mon if today IS mon


@app.command()
def propose(
    week_of: str = typer.Option(None, "--week-of",
        help="YYYY-MM-DD (default: neste mandag)"),
    save: bool = typer.Option(False, "--save",
        help="Skriv forslaget til planned_sessions-tabellen"),
    push_hevy: bool = typer.Option(False, "--push-hevy",
        help="Speil strength-dagene til Hevy-routines (krever --save)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Foreslå neste ukes treningsplan basert på aktiv blokk og state.

    Default er dry-run: vis forslag + reasoning, ingen skriving.
    Med --save: skriver til planned_sessions.
    Med --save --push-hevy: oppdaterer også Hevy routines for strength-dagene.
    """
    if week_of:
        week_start = date.fromisoformat(week_of)
        # Sikre at det er mandag
        if week_start.weekday() != 0:
            week_start = week_start - timedelta(days=week_start.weekday())
    else:
        week_start = _next_monday(date.today())

    with connect() as c:
        proposal = propose_week(c, week_start)

    if json_output:
        emit({
            "week_start_date": proposal.week_start_date,
            "variant": proposal.variant,
            "phase": proposal.phase,
            "estimated_run_km": proposal.estimated_run_km,
            "reasoning": proposal.reasoning,
            "sessions": [
                {
                    "planned_date": s.planned_date,
                    "day_of_week": s.day_of_week,
                    "type": s.type,
                    "duration_min": s.duration_min,
                    "intensity_zone": s.intensity_zone,
                    "hr_target": s.hr_target,
                    "description": s.description,
                    "notes": s.notes,
                } for s in proposal.sessions
            ],
        }, as_json=True)
    else:
        typer.echo(_format_proposal(proposal))

    if save:
        with connect() as c:
            for s in proposal.sessions:
                existing = c.execute(
                    "SELECT id FROM planned_sessions WHERE planned_date = ?",
                    (s.planned_date,),
                ).fetchone()
                if existing:
                    c.execute(
                        """
                        UPDATE planned_sessions
                           SET type = ?, description = ?, status = 'planned'
                         WHERE id = ?
                        """,
                        (s.type, s.description + (f"\n{s.notes}" if s.notes else ""),
                         existing["id"]),
                    )
                else:
                    c.execute(
                        """
                        INSERT INTO planned_sessions
                            (planned_date, type, description, status)
                        VALUES (?, ?, ?, 'planned')
                        """,
                        (s.planned_date, s.type,
                         s.description + (f"\n{s.notes}" if s.notes else "")),
                    )
            c.commit()
        typer.echo(f"✓ Lagret {len(proposal.sessions)} økter til planned_sessions\n")

    if push_hevy:
        if not save:
            typer.echo("⚠ --push-hevy krever --save. Hopper over Hevy-push.",
                       err=True)
            return
        from src.coaching.hevy_sync import sync_strength_routines_for_week
        result = sync_strength_routines_for_week(proposal)
        typer.echo(f"✓ Hevy-sync: {result['updated']} routines oppdatert, "
                   f"{result['skipped']} skipped, {result['errors']} errors\n")
        for note in result.get("notes", []):
            typer.echo(f"  - {note}")


if __name__ == "__main__":
    app()
