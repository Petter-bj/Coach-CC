"""Kuratert coaching-kunnskap som sendes til den eksterne modellen.

Modellen får aldri hele ``CLAUDE.md`` — den inneholder CLI-, MCP-, Claude Code-
og drifts-instruksjoner modellen verken skal se eller følge. I stedet vedlikeholdes
et lite sett kuraterte moduler under ``knowledge/`` som bare beskriver *treningsfaglig*
policy. Denne modulen laster dem og velger relevante moduler per coach-flate og tema.

``src/coaching/philosophy.py`` er fortsatt den deterministiske autoriteten: kunnskapen
her forklarer og rammer inn, den overstyrer aldri en hard-stop eller et regel-tall.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# knowledge/ ligger i repo-roten, ved siden av src/.
KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"

# Den lille kjernen sendes til alle coach-flater.
CORE_MODULE = "coach_core.md"

# Moduler gruppert etter tema. Rekkefølgen er stabil for forutsigbar kontekst.
STRENGTH_MODULES = (
    "strength/progression.md",
    "strength/readiness_and_deload.md",
    "strength/exercise_selection.md",
)
RUNNING_MODULES = (
    "running/zones_and_distribution.md",
    "running/injury_guardrails.md",
)
PLANNING_MODULES = ("planning/phases_and_priority.md",)


@lru_cache(maxsize=None)
def _load_module(relative_path: str) -> str | None:
    """Les én kunnskapsmodul. Returner None hvis filen mangler."""
    path = KNOWLEDGE_DIR / relative_path
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _collect(paths: tuple[str, ...]) -> list[dict[str, str]]:
    modules: list[dict[str, str]] = []
    for relative_path in paths:
        text = _load_module(relative_path)
        if text is not None:
            modules.append({"module": relative_path, "content": text})
    return modules


def select_knowledge(
    *,
    surface: str,
    include_strength: bool = False,
    include_running: bool = False,
    include_planning: bool = False,
) -> dict[str, object]:
    """Velg coach-kjerne + relevante moduler for en gitt flate.

    Args:
        surface: "today" | "week" | "block" — kun for sporbarhet i konteksten.
        include_strength/include_running/include_planning: hvilke temamoduler
            som er relevante for spørsmålet/flaten.

    Returnerer et lite, JSON-serialiserbart objekt som legges rett i coach-
    konteksten. Selve utvelgelsen (hvilke temaer) gjøres av API-laget som kjenner
    flaten og spørsmålet; denne funksjonen kjenner bare filene.
    """
    ordered: list[str] = []
    if include_planning:
        ordered.extend(PLANNING_MODULES)
    if include_strength:
        ordered.extend(STRENGTH_MODULES)
    if include_running:
        ordered.extend(RUNNING_MODULES)

    core = _load_module(CORE_MODULE)
    return {
        "surface": surface,
        "authority": "philosophy.py (deterministisk) står over denne teksten",
        "core": core or "",
        "modules": _collect(tuple(ordered)),
    }


_STRENGTH_HINTS = (
    "styrke", "hevy", "mal", "rutine", "løft", "sett", "reps", "fullkropp",
    "push", "pull", "legs", "bein", "overkropp", "underkropp", "squat", "bench",
    "deadlift", "markløft", "knebøy", "benk", "curl", "press",
)
_RUNNING_HINTS = (
    "løp", "løping", "intervall", "terskel", "z2", "z3", "z4", "z5", "sone",
    "langtur", "tempo", "pace", "kadens", "skierg", "cardio", "km", "aerob",
    "restitusjon", "shin", "legghinne",
)


def topic_flags_from_text(*texts: str) -> tuple[bool, bool]:
    """Utled (include_strength, include_running) fra fri tekst.

    En enkel norsk nøkkelord-heuristikk over brukerens melding og ukens innhold.
    Den er bevisst romslig: å ta med en ekstra relevant modul er billig, å mangle
    en er dyrt.
    """
    haystack = " ".join(text for text in texts if text).casefold()
    include_strength = any(hint in haystack for hint in _STRENGTH_HINTS)
    include_running = any(hint in haystack for hint in _RUNNING_HINTS)
    return include_strength, include_running
