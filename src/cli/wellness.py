"""`wellness` — daglig subjektiv morgensjekk-inn (søvn, sårhet, motivasjon, energi)."""

from __future__ import annotations

from datetime import date

import typer

from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def log(
    sleep: int = typer.Option(..., "--sleep", min=1, max=10, help="Søvnkvalitet 1-10"),
    soreness: int = typer.Option(..., "--soreness", min=1, max=10, help="Muskelsårhet 1-10"),
    motivation: int = typer.Option(..., "--motivation", min=1, max=10),
    energy: int = typer.Option(..., "--energy", min=1, max=10),
    illness: bool = typer.Option(False, "--illness", help="Flagg dagen som syk"),
    notes: str = typer.Option(None, "--notes"),
    date_: str = typer.Option(None, "--date", help="YYYY-MM-DD (default: i dag)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Logg dagens wellness. Én rad per dag (upsert)."""
    d = date_ or date.today().isoformat()
    row = {
        "local_date": d,
        "sleep_quality": sleep,
        "muscle_soreness": soreness,
        "motivation": motivation,
        "energy": energy,
        "illness_flag": 1 if illness else 0,
        "notes": notes,
    }
    with connect() as c:
        c.execute(
            """
            INSERT INTO wellness_daily
                (local_date, sleep_quality, muscle_soreness, motivation,
                 energy, illness_flag, notes)
            VALUES (:local_date, :sleep_quality, :muscle_soreness, :motivation,
                    :energy, :illness_flag, :notes)
            ON CONFLICT (local_date) DO UPDATE SET
                sleep_quality = excluded.sleep_quality,
                muscle_soreness = excluded.muscle_soreness,
                motivation = excluded.motivation,
                energy = excluded.energy,
                illness_flag = excluded.illness_flag,
                notes = excluded.notes
            """,
            row,
        )
    emit({"logged": d, **row}, as_json=json_output,
         text=f"✓ Wellness logget for {d}: søvn={sleep} sårhet={soreness} "
              f"motivasjon={motivation} energi={energy}"
              f"{' (SYK)' if illness else ''}\n")


@app.command()
def today(json_output: bool = typer.Option(False, "--json")) -> None:
    """Vis dagens wellness-rad (hvis logget)."""
    d = date.today().isoformat()
    with connect() as c:
        row = c.execute(
            "SELECT * FROM wellness_daily WHERE local_date = ?", (d,)
        ).fetchone()
    data = dict(row) if row else {"local_date": d, "logged": False}
    emit(
        data, as_json=json_output,
        text=(f"Wellness {d}: søvn={row['sleep_quality']} "
              f"sårhet={row['muscle_soreness']} mot={row['motivation']} "
              f"energi={row['energy']}"
              f"{' (SYK)' if row['illness_flag'] else ''}\n"
              if row else f"Ingen wellness logget for {d}\n"),
    )


@app.command()
def show(
    range_: str = typer.Option("last_7d", "--range"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis wellness-historikk over et intervall."""
    r = parse_range(range_)
    with connect() as c:
        rows = [dict(row) for row in c.execute(
            "SELECT * FROM wellness_daily WHERE local_date BETWEEN ? AND ? "
            "ORDER BY local_date DESC",
            (r.start, r.end),
        ).fetchall()]

    data = {"range": r.label, "count": len(rows), "rows": rows}
    if json_output:
        emit(data, as_json=True)
        return

    lines = [f"# Wellness {r.label} ({r.start} → {r.end}) — {len(rows)} dager"]
    for row in rows:
        sick = " (SYK)" if row["illness_flag"] else ""
        notes = f" — {row['notes']}" if row["notes"] else ""
        lines.append(
            f"  {row['local_date']}  søvn={row['sleep_quality']} "
            f"sårhet={row['muscle_soreness']} mot={row['motivation']} "
            f"energi={row['energy']}{sick}{notes}"
        )
    emit(data, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
