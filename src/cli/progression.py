"""`progression` — dobbel progresjon-anbefalinger.

Svarer på "hva skal jeg ta på benk i morgen?" basert på siste økts topp-sett
og rep-vinduet konfigurert for øvelsen.

Kommandoer:
    progression next "Bench Press"               — anbefaling for neste økt
    progression next "Bench Press" --json        — strukturert output
    progression history "Bench Press"            — siste topp-sett (debug)
"""

from __future__ import annotations

import typer

from src.cli._common import emit
from src.coaching.history import exercise_sessions_count, last_top_set
from src.coaching.philosophy import next_set_for_exercise
from src.coaching.preferences import get_exercise_prefs
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("next")
def next_cmd(
    exercise: str = typer.Argument(..., help="Øvelsesnavn"),
    within_days: int = typer.Option(
        90, "--within-days",
        help="Ignorer historikk eldre enn N dager",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Anbefal neste sett basert på dobbel progresjon."""
    with connect() as c:
        prefs = get_exercise_prefs(c, exercise)
        top = last_top_set(c, exercise, within_days=within_days)
        sessions = exercise_sessions_count(c, exercise, within_days=within_days)

    rec = next_set_for_exercise(
        top,
        rep_min=prefs.rep_min,
        rep_max=prefs.rep_max,
        increment_kg=prefs.increment_kg,
    )

    payload = {
        "exercise": prefs.display_name,
        "prefs": {
            "rep_min": prefs.rep_min,
            "rep_max": prefs.rep_max,
            "increment_kg": prefs.increment_kg,
            "exercise_type": prefs.exercise_type,
            "is_default": prefs.is_default,
        },
        "last_top_set": top,
        "sessions_in_window": sessions,
        "recommendation": {
            "action": rec.action,
            "target_weight_kg": rec.target_weight_kg,
            "target_reps": rec.target_reps,
            "reasoning": rec.reasoning,
        },
    }

    if json_output:
        emit(payload, as_json=True)
        return

    lines = [f"# Neste økt: {prefs.display_name}"]
    lines.append(f"  Rep-vindu: {prefs.rep_min}–{prefs.rep_max}, "
                 f"increment: {prefs.increment_kg} kg")
    if top:
        w = top["weight_kg"]
        wtxt = f"{w} kg" if w else "bodyweight"
        rpe = f", RPE {top['rpe']}" if top.get("rpe") else ""
        lines.append(f"  Siste topp-sett ({top['local_date']}): "
                     f"{top['reps']} × {wtxt}{rpe}")
    else:
        lines.append(f"  Ingen historikk siste {within_days} dager")

    lines.append("")
    lines.append(f"→ {rec.reasoning}")
    if rec.action == "add_weight":
        lines.append(f"   Målvekt: {rec.target_weight_kg} kg × {rec.target_reps}+ reps")
    elif rec.action == "add_reps":
        if rec.target_weight_kg:
            lines.append(f"   Samme vekt ({rec.target_weight_kg} kg), sikt mot "
                         f"{rec.target_reps} reps")
        else:
            lines.append(f"   Bodyweight, sikt mot {rec.target_reps} reps")

    emit(payload, as_json=False, text="\n".join(lines) + "\n")


@app.command("history")
def history_cmd(
    exercise: str = typer.Argument(...),
    within_days: int = typer.Option(90, "--within-days"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis siste topp-sett for en øvelse (debug/verifiser)."""
    with connect() as c:
        top = last_top_set(c, exercise, within_days=within_days)
        sessions = exercise_sessions_count(c, exercise, within_days=within_days)

    payload = {"exercise": exercise, "last_top_set": top,
               "sessions_in_window": sessions}

    if json_output:
        emit(payload, as_json=True)
        return

    if top is None:
        emit(payload, as_json=False,
             text=f"(ingen historikk for '{exercise}' siste {within_days} dager)\n")
        return

    w = top["weight_kg"]
    wtxt = f"{w} kg" if w else "bodyweight"
    lines = [f"# {exercise} — topp-sett"]
    lines.append(f"  {top['local_date']} ({top['source']})  "
                 f"{top['reps']} × {wtxt}"
                 f"{', RPE ' + str(top['rpe']) if top.get('rpe') else ''}"
                 f"{', e1RM ' + str(top['e1rm_kg']) + ' kg' if top.get('e1rm_kg') else ''}")
    lines.append(f"  {sessions} økter totalt i vinduet")
    emit(payload, as_json=False, text="\n".join(lines) + "\n")


if __name__ == "__main__":
    app()
