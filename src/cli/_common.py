"""Felles hjelpere for CLI-ene.

Konvensjoner (jf. CLAUDE.md):
- Alle CLI-er støtter `--json` for strukturert output
- Default er menneskelig-lesbar tekst
- Tidsintervaller: `last_7d` | `last_30d` | `week_of=YYYY-MM-DD`
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass
class DateRange:
    """Inkluderende date-range: start og end (begge YYYY-MM-DD)."""
    start: str
    end: str
    label: str

    @property
    def days(self) -> int:
        return (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days + 1


def parse_range(raw: str) -> DateRange:
    """Parse tidsintervall-argument til DateRange.

    Støtter:
        last_7d      — de siste 7 dagene (inkl. i dag)
        last_30d     — siste 30 dager
        last_Nd      — siste N dager (N >= 1)
        week_of=YYYY-MM-DD — ISO-uken som inneholder datoen
    """
    today = date.today()
    if raw == "last_7d":
        start = today - timedelta(days=6)
        return DateRange(start.isoformat(), today.isoformat(), "last_7d")
    if raw == "last_30d":
        start = today - timedelta(days=29)
        return DateRange(start.isoformat(), today.isoformat(), "last_30d")
    if raw.startswith("last_") and raw.endswith("d"):
        n = int(raw[5:-1])
        start = today - timedelta(days=n - 1)
        return DateRange(start.isoformat(), today.isoformat(), raw)
    if raw.startswith("week_of="):
        anchor = date.fromisoformat(raw.split("=", 1)[1])
        # ISO-uka: mandag–søndag
        monday = anchor - timedelta(days=anchor.weekday())
        sunday = monday + timedelta(days=6)
        return DateRange(monday.isoformat(), sunday.isoformat(), f"week_of={anchor}")
    raise ValueError(f"Ukjent range-format: {raw!r}")


def emit(payload: dict, *, as_json: bool, text: str | None = None) -> None:
    """Skriv output til stdout.

    Args:
        payload: strukturert data (brukes hvis as_json=True)
        as_json: flagg fra CLI-argumentet
        text: menneskelig-lesbar tekst (brukes hvis as_json=False; fallback
            til JSON hvis ikke oppgitt)
    """
    if as_json:
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, default=str)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(text if text is not None else json.dumps(payload, default=str) + "\n")
        if not (text or "").endswith("\n"):
            sys.stdout.write("\n")
