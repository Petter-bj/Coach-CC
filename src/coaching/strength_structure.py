"""Stabil styrkestruktur som hører til en treningsblokk.

Strukturen beskriver *formen* på styrketreningen, ikke en låst øvelsesliste.
Dermed kan Hevy-maler kalibreres eller justeres uten at coachen begynner å
finne opp et nytt program hver uke.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_STRENGTH_STRUCTURE = {
    "sessions_per_week": 3,
    "frequency_target_per_muscle": 2,
    "templates": [
        {
            "name": "Fullkropp A",
            "emphasis": "Knebøy/quad · horisontalt press · vertikalt drag",
            "placement": "Moderat beinvolum; ikke dagen før terskel eller hardere løp.",
        },
        {
            "name": "Overkropp",
            "emphasis": "Press · drag · skuldre og armer",
            "placement": "Kan ligge nær en kvalitetsløpeøkt fordi beinbelastningen er lav.",
        },
        {
            "name": "Underkropp B",
            "emphasis": "Hoftehengsel/bakside · komplementær overkropp",
            "placement": "Legg den etter hard løping heller enn rett før neste terskeløkt.",
        },
    ],
}


def default_strength_structure() -> dict[str, Any]:
    """Returner en kopi slik at kallere aldri deler muterbar tilstand."""
    return deepcopy(DEFAULT_STRENGTH_STRUCTURE)


def normalize_strength_structure(value: Any) -> dict[str, Any] | None:
    """Valider og begrens en modellforeslått blokkstruktur.

    ``None`` betyr at modellen brukte den bevisste standarden. Annen ugyldig
    data forkastes, slik at en blokk aldri kan lagre et vilkårlig JSON-objekt.
    """
    if value is None:
        return default_strength_structure()
    if not isinstance(value, dict):
        return None

    sessions = value.get("sessions_per_week")
    frequency = value.get("frequency_target_per_muscle", 2)
    templates = value.get("templates")
    if (
        isinstance(sessions, bool)
        or not isinstance(sessions, int)
        or not 1 <= sessions <= 5
        or isinstance(frequency, bool)
        or not isinstance(frequency, int)
        or not 1 <= frequency <= 3
        or not isinstance(templates, list)
        or len(templates) != sessions
    ):
        return None

    clean_templates: list[dict[str, str]] = []
    names: set[str] = set()
    for template in templates:
        if not isinstance(template, dict):
            return None
        name = _text(template.get("name"), 60)
        emphasis = _text(template.get("emphasis"), 220)
        placement = _text(template.get("placement"), 260)
        if not name or not emphasis or not placement or name.casefold() in names:
            return None
        names.add(name.casefold())
        clean_templates.append({
            "name": name,
            "emphasis": emphasis,
            "placement": placement,
        })

    return {
        "sessions_per_week": sessions,
        "frequency_target_per_muscle": frequency,
        "templates": clean_templates,
    }


def _text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean[:maximum] if clean else None
