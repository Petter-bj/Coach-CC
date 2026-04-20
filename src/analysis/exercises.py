"""Exercise-navn → muskelgruppe-mapping med alias-støtte.

Bruker `src/data/exercise_muscles.json` som kilde. Normaliserer navnet
(lowercase, stripp punktum/spesialtegn) før lookup. Ukjente øvelser
returneres som {primary: None, unknown: True}.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

MAPPING_PATH = Path(__file__).resolve().parent.parent / "data" / "exercise_muscles.json"


def _normalize(name: str) -> str:
    """Normaliser navn: lowercase, stripp punktum/parentes, kollaps whitespace."""
    cleaned = re.sub(r"[().,]", "", name.lower().strip())
    return re.sub(r"\s+", " ", cleaned)


@lru_cache(maxsize=1)
def _load_mapping() -> dict:
    data = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    exercises = data.get("exercises", {})
    # Bygg alias → canonical mapping
    alias_lookup: dict[str, str] = {}
    for canonical, info in exercises.items():
        alias_lookup[_normalize(canonical.replace("_", " "))] = canonical
        for alias in info.get("aliases", []):
            alias_lookup[_normalize(alias)] = canonical
    return {"exercises": exercises, "aliases": alias_lookup}


def lookup(name: str) -> dict:
    """Slå opp en øvelse. Returnerer {canonical, primary, secondary, unknown}."""
    mapping = _load_mapping()
    normalized = _normalize(name)
    canonical = mapping["aliases"].get(normalized)
    if not canonical:
        return {
            "input": name,
            "canonical": None,
            "primary": None,
            "secondary": [],
            "unknown": True,
        }
    info = mapping["exercises"][canonical]
    return {
        "input": name,
        "canonical": canonical,
        "primary": info["primary"],
        "secondary": list(info.get("secondary", [])),
        "unknown": False,
    }


def list_muscles() -> set[str]:
    """Alle muskelgrupper vi kjenner til."""
    mapping = _load_mapping()
    muscles: set[str] = set()
    for info in mapping["exercises"].values():
        muscles.add(info["primary"])
        muscles.update(info.get("secondary", []))
    return muscles
