"""`weight_trend` — vekt-trend over et tidsintervall.

Bruker kun første veiing per dag (jf. plan §17) for å unngå kvelds-spikes.
"""

from __future__ import annotations

import typer

from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=False)


def _weight_trend(range_label: str) -> dict:
    r = parse_range(range_label)
    with connect() as c:
        rows = [
            dict(row) for row in c.execute(
                """
                SELECT local_date,
                       MIN(measured_at_utc) AS first_measured,
                       (SELECT weight_kg FROM withings_weight w2
                         WHERE w2.local_date = w.local_date
                         ORDER BY measured_at_utc ASC LIMIT 1) AS weight_kg,
                       (SELECT fat_ratio_pct FROM withings_weight w2
                         WHERE w2.local_date = w.local_date
                         ORDER BY measured_at_utc ASC LIMIT 1) AS fat_ratio_pct
                  FROM withings_weight w
                 WHERE local_date BETWEEN ? AND ?
                 GROUP BY local_date
                 ORDER BY local_date DESC
                """,
                (r.start, r.end),
            ).fetchall()
        ]

    weights = [x["weight_kg"] for x in rows if x["weight_kg"] is not None]
    trend_kg_per_week = None
    if len(rows) >= 2:
        # Enkel lineær: (siste vekt - første vekt) / uker
        from datetime import date as _date
        first = next(r for r in reversed(rows) if r["weight_kg"] is not None)
        last = next(r for r in rows if r["weight_kg"] is not None)
        if first["local_date"] != last["local_date"]:
            days = (_date.fromisoformat(last["local_date"]) - _date.fromisoformat(first["local_date"])).days
            if days > 0:
                trend_kg_per_week = round(
                    (last["weight_kg"] - first["weight_kg"]) / days * 7, 2
                )

    return {
        "range": r.label,
        "start": r.start,
        "end": r.end,
        "days_with_data": len(rows),
        "latest_kg": rows[0]["weight_kg"] if rows else None,
        "min_kg": min(weights) if weights else None,
        "max_kg": max(weights) if weights else None,
        "avg_kg": round(sum(weights) / len(weights), 2) if weights else None,
        "trend_kg_per_week": trend_kg_per_week,
        "rows": rows,
    }


def _format_text(data: dict) -> str:
    if data["days_with_data"] == 0:
        return f"Ingen vekt-data for {data['range']}\n"

    lines = [f"# Vekt {data['range']} ({data['start']} → {data['end']})"]
    lines.append(
        f"  {data['days_with_data']} dager, siste {data['latest_kg']:.2f}kg  "
        f"snitt {data['avg_kg']:.2f}kg  (min {data['min_kg']:.2f} / max {data['max_kg']:.2f})"
    )
    if data["trend_kg_per_week"] is not None:
        sign = "+" if data["trend_kg_per_week"] >= 0 else ""
        lines.append(f"  Trend: {sign}{data['trend_kg_per_week']} kg/uke")
    lines.append("")
    lines.append("## Per dag (nyeste først)")
    for r in data["rows"]:
        fat = f"  fett {r['fat_ratio_pct']:.1f}%" if r["fat_ratio_pct"] else ""
        lines.append(f"  {r['local_date']}  {r['weight_kg']:.2f} kg{fat}")
    return "\n".join(lines) + "\n"


@app.command()
def main(
    range: str = typer.Option("last_30d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vekt-trend (første veiing per dag)."""
    data = _weight_trend(range)
    emit(data, as_json=json_output, text=_format_text(data))


if __name__ == "__main__":
    app()
