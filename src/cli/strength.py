"""`strength` — logg styrkeøkt fra parsed JSON (chat-bekreft-flow).

Brukes av Claude Code etter at hen har lest en screenshot og bekreftet
innholdet med brukeren i Telegram.

Kommandoer:
  log     — valider og commit en hel økt (eller --dry-run for forhåndsvisning)
  check   — samme som --dry-run: valider + PR-sjekk uten å skrive
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from pydantic import ValidationError

from src.cli._common import emit
from src.db.connection import connect
from src.paths import SCREENSHOT_CACHE_DIR, ensure_runtime_dirs
from src.schemas import StrengthSession

app = typer.Typer(add_completion=False, no_args_is_help=True)

# PR-advarsel: ny e1RM > PR_WARN_FACTOR × eksisterende best
PR_WARN_FACTOR = 1.4

DEFAULT_TZ = "Europe/Oslo"


def _epley(weight_kg: float | None, reps: int) -> float | None:
    if not weight_kg or reps <= 0:
        return None
    return round(weight_kg * (1 + reps / 30), 2)


def _local_to_utc(local_iso: str, tz_name: str = DEFAULT_TZ) -> str:
    """Konverter 'YYYY-MM-DDTHH:MM' (lokal) → ISO 8601 UTC."""
    dt = datetime.fromisoformat(local_iso)
    aware = dt.replace(tzinfo=ZoneInfo(tz_name))
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr_warnings(conn: sqlite3.Connection, session: StrengthSession) -> list[dict]:
    """Finn potensielle urimelige PR-er (ny e1RM > 1.4× eksisterende best)."""
    warnings: list[dict] = []
    for ex in session.exercises:
        existing_best = conn.execute(
            """
            SELECT MAX(e1rm_kg) AS best FROM strength_sets
             WHERE LOWER(exercise) = LOWER(?)
            """,
            (ex.name,),
        ).fetchone()
        best = existing_best["best"] if existing_best else None
        for s_idx, s in enumerate(ex.sets, start=1):
            new_e1rm = _epley(s.weight_kg, s.reps)
            if new_e1rm is None:
                continue
            if best is not None and new_e1rm > best * PR_WARN_FACTOR:
                warnings.append({
                    "exercise": ex.name,
                    "set_num": s_idx,
                    "weight_kg": s.weight_kg,
                    "reps": s.reps,
                    "new_e1rm": new_e1rm,
                    "previous_best_e1rm": best,
                    "factor": round(new_e1rm / best, 2),
                    "message": (
                        f"{ex.name} sett {s_idx}: {s.weight_kg}kg × {s.reps} "
                        f"= e1RM {new_e1rm:.1f}kg, {round(new_e1rm/best, 2)}× "
                        f"din forrige topp ({best:.1f}kg). Stemmer vekten?"
                    ),
                })
    return warnings


def _insert_session(
    conn: sqlite3.Connection,
    session: StrengthSession,
    screenshot_path: str | None,
) -> tuple[int, int, int]:
    """Skriv workouts + strength_sessions + strength_sets i én transaksjon.

    Returnerer (workout_id, session_id, sets_inserted).
    """
    local_date = session.local_date()
    started_utc = _local_to_utc(session.started_at_local)

    external_id = (
        f"chat_{session.started_at_local.replace(':', '')}"
        f"_{session.session_name or 'strength'}"
    )

    # Fjern eksisterende chat-logg med samme external_id (idempotens ved re-kjøring)
    existing = conn.execute(
        "SELECT id FROM workouts WHERE source='strength' AND external_id=?",
        (external_id,),
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM workouts WHERE id = ?", (existing["id"],))
        # Cascading sletter strength_sessions + strength_sets

    cur = conn.execute(
        """
        INSERT INTO workouts (external_id, source, started_at_utc, timezone,
                              local_date, type, notes)
        VALUES (?, 'strength', ?, ?, ?, 'strength_training', ?)
        """,
        (external_id, started_utc, DEFAULT_TZ, local_date,
         session.session_name and f"Økt: {session.session_name}" or session.notes),
    )
    workout_id = cur.lastrowid

    cur = conn.execute(
        "INSERT INTO strength_sessions (workout_id) VALUES (?)",
        (workout_id,),
    )
    session_id = cur.lastrowid

    sets_inserted = 0
    for ex in session.exercises:
        for set_num, s in enumerate(ex.sets, start=1):
            e1rm = _epley(s.weight_kg, s.reps)
            conn.execute(
                """
                INSERT INTO strength_sets
                    (session_id, exercise, set_num, reps, weight_kg, rpe,
                     e1rm_kg, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, ex.name, set_num, s.reps, s.weight_kg,
                 s.rpe, e1rm, s.notes),
            )
            sets_inserted += 1

    conn.commit()
    return workout_id, session_id, sets_inserted


def _save_screenshot(src: Path) -> str | None:
    """Kopier screenshot til ~/Library/Caches/Trening/strength_screenshots/."""
    ensure_runtime_dirs()
    if not src.exists():
        return None
    dest_name = f"{datetime.now().strftime('%Y%m%dT%H%M%S')}_{src.name}"
    dest = SCREENSHOT_CACHE_DIR / dest_name
    shutil.copy2(src, dest)
    return str(dest)


def _parse_data(data_arg: str) -> StrengthSession:
    """Parse JSON-streng eller @-prefikset fil-sti."""
    if data_arg.startswith("@"):
        payload = Path(data_arg[1:]).read_text(encoding="utf-8")
    else:
        payload = data_arg
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"Ugyldig JSON: {e}")
    try:
        return StrengthSession.model_validate(raw)
    except ValidationError as e:
        raise typer.BadParameter(f"Schema-feil:\n{e}")


def _format_preview(session: StrengthSession, warnings: list[dict]) -> str:
    lines = [f"# Styrkeøkt-forhåndsvisning ({session.started_at_local})"]
    if session.session_name:
        lines.append(f"  Økt: {session.session_name}")
    lines.append(f"  {len(session.exercises)} øvelser, {session.total_sets()} sett")
    lines.append("")
    for ex in session.exercises:
        lines.append(f"  {ex.name}")
        for i, s in enumerate(ex.sets, 1):
            rpe_tag = f" @ RPE {s.rpe}" if s.rpe else ""
            w = f"{s.weight_kg}kg" if s.weight_kg else "bodyweight"
            e1rm = _epley(s.weight_kg, s.reps)
            e1rm_tag = f"  (e1RM {e1rm:.1f})" if e1rm else ""
            lines.append(f"    {i}. {s.reps} × {w}{rpe_tag}{e1rm_tag}")
    if session.notes:
        lines.append(f"\n  Notes: {session.notes}")

    if warnings:
        lines.append("")
        lines.append("⚠  PR-ADVARSLER — dobbeltsjekk vekt:")
        for w in warnings:
            lines.append(f"  {w['message']}")
    return "\n".join(lines) + "\n"


@app.command("log")
def log_cmd(
    data: str = typer.Option(..., "--data",
        help="JSON-streng eller @/sti/til/fil.json"),
    image: str = typer.Option(None, "--image",
        help="(Valgfritt) sti til screenshot som kopieres til cache"),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="Valider og vis, men ikke skriv til DB"),
    force_pr: bool = typer.Option(False, "--force-pr",
        help="Tillat urimelige PR-er uten å feile"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Logg en styrkeøkt fra parsed JSON (chat-bekreft-flow)."""
    session = _parse_data(data)

    with connect() as c:
        warnings = _pr_warnings(c, session)

    if dry_run:
        result = {
            "valid": True,
            "exercises": len(session.exercises),
            "sets": session.total_sets(),
            "pr_warnings": warnings,
            "would_insert": False,
        }
        emit(result, as_json=json_output,
             text=_format_preview(session, warnings))
        return

    # PR-advarsler blokkerer insert med mindre --force-pr
    if warnings and not force_pr:
        result = {"ok": False, "pr_warnings": warnings,
                  "hint": "Bekreft med --force-pr hvis vekten stemmer"}
        if json_output:
            emit(result, as_json=True)
        else:
            msg = _format_preview(session, warnings)
            msg += "\nBlokkert av PR-sjekk. Kjør på nytt med --force-pr hvis riktig.\n"
            emit(result, as_json=False, text=msg)
        raise typer.Exit(2)

    screenshot = _save_screenshot(Path(image)) if image else None
    with connect() as c:
        wid, sid, n = _insert_session(c, session, screenshot)

    result = {
        "ok": True,
        "workout_id": wid,
        "session_id": sid,
        "sets_inserted": n,
        "screenshot_path": screenshot,
        "pr_warnings": warnings,
    }
    text = (
        f"✓ Workout #{wid} lagret: {len(session.exercises)} øvelser, "
        f"{n} sett, dato {session.local_date()}\n"
    )
    if warnings:
        text += f"  ({len(warnings)} PR-advarsler overstyrt med --force-pr)\n"
    emit(result, as_json=json_output, text=text)


@app.command("check")
def check_cmd(
    data: str = typer.Option(..., "--data",
        help="JSON-streng eller @/sti/til/fil.json"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Valider JSON + PR-sjekk uten å skrive (alias for `log --dry-run`)."""
    session = _parse_data(data)
    with connect() as c:
        warnings = _pr_warnings(c, session)
    result = {
        "valid": True,
        "exercises": len(session.exercises),
        "sets": session.total_sets(),
        "pr_warnings": warnings,
    }
    emit(result, as_json=json_output,
         text=_format_preview(session, warnings))


if __name__ == "__main__":
    app()
