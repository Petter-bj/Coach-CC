"""`nutrition` — dagens eller ukens kosthold fra Yazio."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import typer

from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=False)


def _today_data(day: str | None = None) -> dict:
    d = day or date.today().isoformat()
    with connect() as c:
        daily = c.execute(
            "SELECT * FROM yazio_daily WHERE local_date = ?", (d,)
        ).fetchone()
        meals = [
            dict(r) for r in c.execute(
                """
                SELECT meal, kcal, protein_g, carbs_g, fat_g, energy_goal_kcal
                  FROM yazio_meals
                 WHERE local_date = ?
                 ORDER BY CASE meal
                     WHEN 'breakfast' THEN 1
                     WHEN 'lunch' THEN 2
                     WHEN 'dinner' THEN 3
                     WHEN 'snack' THEN 4 END
                """,
                (d,),
            ).fetchall()
        ]
    return {
        "date": d,
        "daily": dict(daily) if daily else None,
        "meals": meals,
    }


def _week_data(week_of: str) -> dict:
    r = parse_range(f"week_of={week_of}")
    with connect() as c:
        rows_by_date = {
            row["local_date"]: dict(row)
            for row in c.execute(
                """
                SELECT local_date, kcal, protein_g, carbs_g, fat_g,
                       kcal_goal, protein_goal_g, carbs_goal_g, fat_goal_g
                  FROM yazio_daily
                 WHERE local_date BETWEEN ? AND ?
                """,
                (r.start, r.end),
            ).fetchall()
        }

    # Fyll ut alle 7 dager — None-verdier for dager uten data
    days = []
    current = date.fromisoformat(r.start)
    end_d = date.fromisoformat(r.end)
    while current <= end_d:
        iso = current.isoformat()
        if iso in rows_by_date:
            days.append(rows_by_date[iso])
        else:
            days.append({
                "local_date": iso,
                "kcal": None,
                "protein_g": None,
                "carbs_g": None,
                "fat_g": None,
                "kcal_goal": None,
                "protein_goal_g": None,
                "carbs_goal_g": None,
                "fat_goal_g": None,
            })
        current += timedelta(days=1)

    days_logged = [d for d in days if (d["kcal"] or 0) > 0]
    if days_logged:
        avg_kcal = sum(d["kcal"] for d in days_logged) / len(days_logged)
        avg_p = sum(d["protein_g"] for d in days_logged) / len(days_logged)
        avg_c = sum(d["carbs_g"] for d in days_logged) / len(days_logged)
        avg_f = sum(d["fat_g"] for d in days_logged) / len(days_logged)
    else:
        avg_kcal = avg_p = avg_c = avg_f = 0.0

    return {
        "week_of": r.label,
        "start": r.start,
        "end": r.end,
        "days_logged": len(days_logged),
        "avg_kcal": round(avg_kcal, 0),
        "avg_protein_g": round(avg_p, 0),
        "avg_carbs_g": round(avg_c, 0),
        "avg_fat_g": round(avg_f, 0),
        "days": days,
    }


def _format_today(data: dict) -> str:
    lines = [f"# Kosthold {data['date']}"]
    if not data["daily"]:
        return "\n".join(lines) + "\n  Ingen data logget\n"

    d = data["daily"]
    kcal = d.get("kcal") or 0
    kcal_goal = d.get("kcal_goal") or 0
    pct = (kcal / kcal_goal * 100) if kcal_goal else 0

    lines.append(
        f"  {kcal:.0f} / {kcal_goal:.0f} kcal  ({pct:.0f}%)"
    )
    lines.append(
        f"  Makro: P={d['protein_g'] or 0:.0f}g  K={d['carbs_g'] or 0:.0f}g  "
        f"F={d['fat_g'] or 0:.0f}g"
    )
    if any(m["kcal"] for m in data["meals"]):
        lines.append("")
        lines.append("## Per måltid")
        for m in data["meals"]:
            if (m["kcal"] or 0) > 0:
                lines.append(
                    f"  {m['meal']:10} {m['kcal']:>5.0f} kcal  "
                    f"P={m['protein_g']:>4.0f}g  K={m['carbs_g']:>4.0f}g  F={m['fat_g']:>4.0f}g"
                )
    return "\n".join(lines) + "\n"


def _format_week(data: dict) -> str:
    lines = [f"# Kostholds-uke {data['week_of']} ({data['start']} → {data['end']})"]
    lines.append(f"  {data['days_logged']} av 7 dager logget")
    if data["days_logged"] > 0:
        lines.append(
            f"  Snitt: {data['avg_kcal']:.0f} kcal  "
            f"P={data['avg_protein_g']:.0f}g  K={data['avg_carbs_g']:.0f}g  F={data['avg_fat_g']:.0f}g"
        )
    lines.append("")
    lines.append("## Per dag")
    for d in data["days"]:
        kcal = d["kcal"] or 0
        if kcal > 0:
            lines.append(
                f"  {d['local_date']}  {kcal:.0f} kcal  "
                f"P={d['protein_g'] or 0:.0f}g K={d['carbs_g'] or 0:.0f}g F={d['fat_g'] or 0:.0f}g"
            )
        else:
            lines.append(f"  {d['local_date']}  (ingen mat logget)")
    return "\n".join(lines) + "\n"


@app.command("today")
def today(
    date_: str = typer.Option(None, "--date", help="YYYY-MM-DD (default: i dag)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Dagens kosthold."""
    data = _today_data(date_)
    emit(data, as_json=json_output, text=_format_today(data))


@app.command("week")
def week(
    week_of: str = typer.Option(..., "--week-of", help="YYYY-MM-DD (noen dag i ønsket uke)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Ukens kosthold-sammendrag."""
    data = _week_data(week_of)
    emit(data, as_json=json_output, text=_format_week(data))


if __name__ == "__main__":
    app()
