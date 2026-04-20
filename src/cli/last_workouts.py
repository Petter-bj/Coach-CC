"""`last_workouts` — liste over siste økter (ekskl. superseded)."""

from __future__ import annotations

import typer

from src.cli._common import emit
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=False)


def _last_workouts(limit: int, type_filter: str | None) -> dict:
    sql = """
        SELECT w.id, w.source, w.type, w.started_at_utc, w.local_date,
               w.duration_sec, w.distance_m, w.avg_hr, w.calories, w.rpe,
               w.notes, w.superseded_by,
               (SELECT COUNT(*) FROM workout_samples WHERE workout_id = w.id) AS sample_count
          FROM workouts w
         WHERE w.superseded_by IS NULL
    """
    params: list = []
    if type_filter:
        sql += " AND w.type = ?"
        params.append(type_filter)
    sql += " ORDER BY w.started_at_utc DESC LIMIT ?"
    params.append(max(1, limit))

    with connect() as c:
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]
    return {
        "limit": limit,
        "type_filter": type_filter,
        "count": len(rows),
        "rows": rows,
    }


def _format_text(data: dict) -> str:
    filter_note = f" (type={data['type_filter']})" if data["type_filter"] else ""
    if data["count"] == 0:
        return f"Ingen økter funnet{filter_note}\n"
    lines = [f"# Siste {data['count']} økter{filter_note}"]
    for w in data["rows"]:
        dur_min = (w["duration_sec"] or 0) / 60
        dist_km = (w["distance_m"] or 0) / 1000
        extras = []
        if w["avg_hr"]:
            extras.append(f"HR {w['avg_hr']}")
        if w["rpe"]:
            extras.append(f"RPE {w['rpe']}")
        if w["sample_count"]:
            extras.append(f"{w['sample_count']} samples")
        extras_str = "  " + " · ".join(extras) if extras else ""
        lines.append(
            f"  {w['local_date']}  {w['source']:8} {w['type']:16} "
            f"{dur_min:.0f} min"
            + (f", {dist_km:.2f} km" if w["distance_m"] else "")
            + extras_str
        )
        if w["notes"]:
            lines.append(f"          — {w['notes']}")
    return "\n".join(lines) + "\n"


@app.command()
def main(
    limit: int = typer.Option(10, "--limit"),
    type: str | None = typer.Option(None, "--type",
        help="Filtrer på type: running, indoor_rowing, skierg, strength_training, ..."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Liste siste økter (hopper over superseded)."""
    data = _last_workouts(limit, type)
    emit(data, as_json=json_output, text=_format_text(data))


if __name__ == "__main__":
    app()
