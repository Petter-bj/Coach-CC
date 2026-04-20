"""`sleep_summary` — søvn-oversikt over et tidsintervall."""

from __future__ import annotations

import typer

from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=False)


def _sleep_summary(range_label: str) -> dict:
    r = parse_range(range_label)
    with connect() as c:
        rows = [
            dict(row) for row in c.execute(
                """
                SELECT local_date, duration_sec, deep_sec, light_sec, rem_sec,
                       awake_sec, sleep_score, sleep_score_qualifier,
                       avg_respiration
                  FROM garmin_sleep
                 WHERE local_date BETWEEN ? AND ?
                 ORDER BY local_date DESC
                """,
                (r.start, r.end),
            ).fetchall()
        ]

    # Bare netter med faktiske data (duration > 0) teller i aggregater
    valid = [x for x in rows if (x["duration_sec"] or 0) > 0]

    if not valid:
        return {
            "range": r.label,
            "start": r.start,
            "end": r.end,
            "nights": len(valid),
            "rows_with_stub": len(rows) - len(valid),
            "rows": rows,
        }

    # Aggregater — kun på gyldige netter
    total_duration = sum(x["duration_sec"] for x in valid)
    total_deep = sum(x["deep_sec"] or 0 for x in valid)
    total_light = sum(x["light_sec"] or 0 for x in valid)
    total_rem = sum(x["rem_sec"] or 0 for x in valid)
    total_awake = sum(x["awake_sec"] or 0 for x in valid)
    scores = [x["sleep_score"] for x in valid if x["sleep_score"] is not None]

    stages_pct = None
    if total_duration > 0:
        stages_pct = {
            "deep": round(100 * total_deep / total_duration, 1),
            "light": round(100 * total_light / total_duration, 1),
            "rem": round(100 * total_rem / total_duration, 1),
            "awake": round(100 * total_awake / total_duration, 1),
        }

    return {
        "range": r.label,
        "start": r.start,
        "end": r.end,
        "nights": len(valid),
        "rows_with_stub": len(rows) - len(valid),
        "avg_duration_sec": round(total_duration / len(valid), 1),
        "avg_duration_hours": round(total_duration / len(valid) / 3600, 2),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "min_score": min(scores) if scores else None,
        "max_score": max(scores) if scores else None,
        "stages_pct": stages_pct,
        "rows": rows,
    }


def _format_text(data: dict) -> str:
    if data["nights"] == 0:
        return f"Ingen søvn-data for {data['range']} ({data['start']} → {data['end']})\n"

    lines = [f"# Søvn {data['range']} ({data['start']} → {data['end']})"]
    lines.append(
        f"  {data['nights']} netter, snitt {data['avg_duration_hours']:.1f}t, "
        f"score {data['avg_score']} (min {data['min_score']} / max {data['max_score']})"
    )
    p = data["stages_pct"]
    if p:
        lines.append(
            f"  Stadier: deep {p['deep']}% · light {p['light']}% · "
            f"rem {p['rem']}% · awake {p['awake']}%"
        )
    if data.get("rows_with_stub"):
        lines.append(f"  ({data['rows_with_stub']} dag(er) uten målt søvn utelatt fra snitt)")
    lines.append("")
    lines.append("## Per natt (nyeste først)")
    for r in data["rows"]:
        hours = (r["duration_sec"] or 0) / 3600
        if hours > 0:
            score = r["sleep_score"] or "—"
            qualifier = r["sleep_score_qualifier"] or ""
            lines.append(f"  {r['local_date']}  {hours:.1f}t  score={score} {qualifier}")
        else:
            lines.append(f"  {r['local_date']}  (ingen søvn målt)")
    return "\n".join(lines) + "\n"


@app.command()
def main(
    range: str = typer.Option("last_7d", "--range", help="last_7d | last_30d | week_of=YYYY-MM-DD"),
    json_output: bool = typer.Option(False, "--json", help="Strukturert JSON"),
) -> None:
    """Søvn-oversikt over et tidsintervall."""
    data = _sleep_summary(range)
    emit(data, as_json=json_output, text=_format_text(data))


if __name__ == "__main__":
    app()
