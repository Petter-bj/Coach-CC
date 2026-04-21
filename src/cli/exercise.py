"""`exercise` — per-øvelse coaching-preferenser.

Primært for å sette rep-vindu og vekt-increment per øvelse. CLI-en er
case-insensitive — "Bench Press" og "bench press" refererer til samme rad.

Kommandoer:
    exercise list                                  — vis alle satte overrides
    exercise show "Bench Press"                    — vis effektive verdier (m/ defaults)
    exercise set "Bench Press" --rep-window 6-10 --increment 2.5
    exercise set "Lateral Raise (Dumbbell)" --rep-window 10-15 --increment 1 --type isolation
    exercise known                                 — list alle øvelser vi har historikk for
"""

from __future__ import annotations

import typer

from src.cli._common import emit
from src.coaching.history import known_exercises
from src.coaching.preferences import (
    ExercisePrefs,
    get_exercise_prefs,
    list_exercise_prefs,
    set_exercise_prefs,
)
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


VALID_TYPES = {"compound", "isolation"}


def _parse_rep_window(raw: str) -> tuple[int, int]:
    """Parse 'M-N' eller 'M–N' → (M, N)."""
    normalized = raw.replace("–", "-").replace(" ", "")
    parts = normalized.split("-")
    if len(parts) != 2:
        raise typer.BadParameter(f"rep-window må være på formen 'M-N' (f.eks. 6-10), fikk {raw!r}")
    try:
        m, n = int(parts[0]), int(parts[1])
    except ValueError:
        raise typer.BadParameter(f"rep-window må være heltall, fikk {raw!r}")
    if not (1 <= m < n <= 30):
        raise typer.BadParameter(f"rep-window må tilfredsstille 1 ≤ min < max ≤ 30")
    return m, n


def _fmt_prefs(p: ExercisePrefs) -> str:
    suffix = " (default — ingen override)" if p.is_default else ""
    lines = [f"# {p.display_name}{suffix}"]
    lines.append(f"  rep-vindu:    {p.rep_min}–{p.rep_max}")
    lines.append(f"  increment:    {p.increment_kg} kg")
    lines.append(f"  type:         {p.exercise_type or '(uspesifisert)'}")
    if p.notes:
        lines.append(f"  notes:        {p.notes}")
    return "\n".join(lines) + "\n"


@app.command("list")
def list_cmd(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List alle øvelser med egen override (ikke de som bruker default)."""
    with connect() as c:
        rows = list_exercise_prefs(c)

    payload = [
        {
            "display_name": r.display_name,
            "rep_min": r.rep_min,
            "rep_max": r.rep_max,
            "increment_kg": r.increment_kg,
            "exercise_type": r.exercise_type,
            "notes": r.notes,
        }
        for r in rows
    ]

    if json_output:
        emit({"exercises": payload}, as_json=True)
        return

    if not rows:
        emit({"exercises": []}, as_json=False,
             text="(ingen per-øvelse-overrides satt — alle bruker defaults)\n")
        return

    lines = ["# Øvelse-overrides"]
    for p in rows:
        lines.append(f"  {p.display_name:40s}  {p.rep_min}-{p.rep_max} reps, "
                     f"+{p.increment_kg} kg"
                     f"{' (' + p.exercise_type + ')' if p.exercise_type else ''}")
    emit({"exercises": payload}, as_json=False, text="\n".join(lines) + "\n")


@app.command("show")
def show_cmd(
    name: str = typer.Argument(..., help="Øvelsesnavn"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis effektive prefs for en øvelse (fletter override + defaults)."""
    with connect() as c:
        p = get_exercise_prefs(c, name)

    payload = {
        "display_name": p.display_name,
        "rep_min": p.rep_min,
        "rep_max": p.rep_max,
        "increment_kg": p.increment_kg,
        "exercise_type": p.exercise_type,
        "notes": p.notes,
        "is_default": p.is_default,
    }
    emit(payload, as_json=json_output, text=_fmt_prefs(p))


@app.command("set")
def set_cmd(
    name: str = typer.Argument(..., help="Øvelsesnavn"),
    rep_window: str = typer.Option(None, "--rep-window",
        help="F.eks. '6-10'. Hopper over hvis ikke satt."),
    increment: float = typer.Option(None, "--increment",
        help="Vekt-increment i kg, f.eks. 2.5"),
    exercise_type: str = typer.Option(None, "--type",
        help="compound | isolation"),
    notes: str = typer.Option(None, "--notes"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Sett/oppdater preferanser for én øvelse."""
    rep_min = rep_max = None
    if rep_window:
        rep_min, rep_max = _parse_rep_window(rep_window)

    if exercise_type is not None and exercise_type not in VALID_TYPES:
        raise typer.BadParameter(f"--type må være en av {sorted(VALID_TYPES)}")

    with connect() as c:
        set_exercise_prefs(
            c, name,
            rep_min=rep_min, rep_max=rep_max,
            increment_kg=increment,
            exercise_type=exercise_type,
            notes=notes,
        )
        p = get_exercise_prefs(c, name)

    payload = {
        "ok": True,
        "display_name": p.display_name,
        "rep_min": p.rep_min,
        "rep_max": p.rep_max,
        "increment_kg": p.increment_kg,
        "exercise_type": p.exercise_type,
        "notes": p.notes,
    }
    emit(payload, as_json=json_output,
         text=f"✓ Oppdatert {p.display_name}\n" + _fmt_prefs(p))


@app.command("known")
def known_cmd(
    days: int = typer.Option(180, "--within-days"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List alle øvelser vi har historikk for (Hevy + xlsx + screenshots)."""
    with connect() as c:
        rows = known_exercises(c, within_days=days)

    if json_output:
        emit({"exercises": rows}, as_json=True)
        return

    if not rows:
        emit({"exercises": []}, as_json=False,
             text=f"(ingen styrke-historikk siste {days} dager)\n")
        return

    lines = [f"# Øvelser med historikk (siste {days} dager)"]
    for r in rows:
        lines.append(f"  {r['exercise']:40s}  {r['sessions']:2d} økter   "
                     f"(sist {r['last_seen']})")
    emit({"exercises": rows}, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
