"""`prefs` — globale coaching-preferenser (KV-store).

Typisk bruk:
    prefs list
    prefs get training_priority
    prefs set training_priority strength
    prefs set strength_rep_min_default 5

Gyldige nøkler:
    training_priority               cardio | strength | balanced
    strength_rep_min_default        heltall (f.eks. 6)
    strength_rep_max_default        heltall (f.eks. 10)
    strength_increment_kg_default   float (f.eks. 2.5)
"""

from __future__ import annotations

import typer

from src.cli._common import emit
from src.coaching.preferences import get_pref, list_prefs, set_pref
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


VALID_PRIORITIES = {"cardio", "strength", "balanced"}


def _validate(key: str, value: str) -> None:
    if key == "training_priority" and value not in VALID_PRIORITIES:
        raise typer.BadParameter(
            f"training_priority må være en av {sorted(VALID_PRIORITIES)}"
        )
    if key in ("strength_rep_min_default", "strength_rep_max_default"):
        try:
            v = int(value)
            if v < 1 or v > 30:
                raise ValueError
        except ValueError:
            raise typer.BadParameter(f"{key} må være heltall 1-30")
    if key == "strength_increment_kg_default":
        try:
            v = float(value)
            if v <= 0 or v > 50:
                raise ValueError
        except ValueError:
            raise typer.BadParameter(f"{key} må være positiv float ≤ 50")


@app.command("list")
def list_cmd(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vis alle satte preferenser."""
    with connect() as c:
        prefs = list_prefs(c)

    if json_output:
        emit(prefs, as_json=True)
        return

    if not prefs:
        emit({}, as_json=False, text="(ingen preferenser satt)\n")
        return

    lines = ["# Preferenser"]
    for k, v in prefs.items():
        lines.append(f"  {k}: {v}")
    emit(prefs, as_json=False, text="\n".join(lines) + "\n")


@app.command("get")
def get_cmd(
    key: str = typer.Argument(..., help="Nøkkel å hente"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Hent én preferanse."""
    with connect() as c:
        value = get_pref(c, key)

    payload = {"key": key, "value": value}
    if json_output:
        emit(payload, as_json=True)
    else:
        if value is None:
            emit(payload, as_json=False, text=f"{key}: (ikke satt)\n")
        else:
            emit(payload, as_json=False, text=f"{key}: {value}\n")


@app.command("set")
def set_cmd(
    key: str = typer.Argument(..., help="Nøkkel"),
    value: str = typer.Argument(..., help="Ny verdi"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Sett en preferanse (validert)."""
    _validate(key, value)
    with connect() as c:
        old = get_pref(c, key)
        set_pref(c, key, value)

    payload = {"key": key, "old_value": old, "new_value": value}
    text = f"✓ {key}: {old or '(ikke satt)'} → {value}\n"
    emit(payload, as_json=json_output, text=text)


if __name__ == "__main__":
    app()
