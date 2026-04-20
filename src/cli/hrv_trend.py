"""`hrv_trend` — HRV-trender over et tidsintervall."""

from __future__ import annotations

import typer

from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=False)


def _hrv_trend(range_label: str) -> dict:
    r = parse_range(range_label)
    with connect() as c:
        rows = [
            dict(row) for row in c.execute(
                """
                SELECT local_date, last_night_avg_ms, weekly_avg_ms, status,
                       last_night_5min_high_ms
                  FROM garmin_hrv
                 WHERE local_date BETWEEN ? AND ?
                 ORDER BY local_date DESC
                """,
                (r.start, r.end),
            ).fetchall()
        ]

    values = [x["last_night_avg_ms"] for x in rows if x["last_night_avg_ms"]]
    latest_weekly = rows[0]["weekly_avg_ms"] if rows else None
    latest_status = rows[0]["status"] if rows else None

    return {
        "range": r.label,
        "start": r.start,
        "end": r.end,
        "nights": len(rows),
        "avg_last_night": round(sum(values) / len(values), 1) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "latest_weekly_avg": latest_weekly,
        "latest_status": latest_status,
        "rows": rows,
    }


def _format_text(data: dict) -> str:
    if data["nights"] == 0:
        return f"Ingen HRV-data for {data['range']}\n"

    lines = [f"# HRV {data['range']} ({data['start']} → {data['end']})"]
    lines.append(
        f"  {data['nights']} målinger, snitt {data['avg_last_night']} ms "
        f"(range {data['min']}–{data['max']})"
    )
    lines.append(
        f"  Siste weekly_avg: {data['latest_weekly_avg']} ms, "
        f"status: {data['latest_status'] or '—'}"
    )
    lines.append("")
    lines.append("## Per natt (nyeste først)")
    for r in data["rows"]:
        lines.append(
            f"  {r['local_date']}  last_night={r['last_night_avg_ms']}ms  "
            f"weekly={r['weekly_avg_ms']}ms  status={r['status'] or '—'}"
        )
    return "\n".join(lines) + "\n"


@app.command()
def main(
    range: str = typer.Option("last_30d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """HRV-trend over et tidsintervall."""
    data = _hrv_trend(range)
    emit(data, as_json=json_output, text=_format_text(data))


if __name__ == "__main__":
    app()
