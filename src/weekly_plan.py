"""Weekly plan-proposer auto-trigger — kjøres søndag 20:00 av launchd.

Computes neste mandags uke via proposer, sender forslag til Telegram.
Bruker direkte Bot API (independent of bot Claude session) slik at det
fungerer selv om bot-en er nede.

Bot-svaret håndteres av brukeren — de kan svare "godkjent" eller
modifisere via vanlig chat. Når bot er oppe, svarer den naturlig.

Entry point:
    uv run python -m src.weekly_plan
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from dotenv import load_dotenv

from src.coaching.proposer import ProposedWeek, propose_week
from src.db.connection import connect
from src.monitor import send_telegram_message
from src.paths import ENV_FILE

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)


# Maks lengde i Telegram-melding — kuttes ved behov
TELEGRAM_MAX_CHARS = 3500


def _next_monday(today: date) -> date:
    """Returner neste mandag (eller i dag hvis i dag er mandag — sjeldent
    ettersom dette kjøres søndag)."""
    days_ahead = (0 - today.weekday()) % 7
    return today + timedelta(days=days_ahead or 7)


def format_proposal_for_telegram(p: ProposedWeek) -> str:
    """Formater forslag for Telegram-melding (kort, lesbar)."""
    variant_label = {
        "A_green": "🟢 Grønn (full uke)",
        "B_yellow": "🟡 Gul (softer)",
        "C_red": "🔴 Rød (cross-train-modus)",
    }.get(p.variant, p.variant)

    lines = [
        f"📋 *Forslag uke {p.week_start_date}*",
        f"Variant: {variant_label}",
        f"Phase: {p.phase} · Estimert løp-km: {p.estimated_run_km}",
        "",
        "*Reasoning:*",
    ]
    for r in p.reasoning:
        lines.append(f"• {r}")

    lines.append("")
    lines.append("*Økter:*")
    for s in p.sessions:
        zone = f" ({s.intensity_zone})" if s.intensity_zone else ""
        dur = f" · {s.duration_min} min" if s.duration_min else ""
        hr = ""
        if s.hr_target:
            hr = f" · HR {s.hr_target[0]}-{s.hr_target[1]}"
        lines.append(f"{s.day_of_week} {s.planned_date[5:]}: "
                     f"{s.type}{zone}{dur}{hr}")
        lines.append(f"  → {s.description[:120]}"
                     + ("…" if len(s.description) > 120 else ""))
        if s.notes:
            lines.append(f"  ⚠ {s.notes[:120]}")

    lines.append("")
    lines.append("Svar med:")
    lines.append("• \"godkjent\" → bot lagrer og pusher Hevy-routines")
    lines.append("• \"endre [...]\" → bot justerer")
    lines.append("• \"vis full\" → mer detaljer")

    text = "\n".join(lines)
    if len(text) > TELEGRAM_MAX_CHARS:
        text = text[:TELEGRAM_MAX_CHARS - 50] + "\n\n…(kuttet, kjør 'plan show' for full)"
    return text


def main() -> int:
    monday = _next_monday(date.today())
    with connect() as c:
        proposal = propose_week(c, monday)

    text = format_proposal_for_telegram(proposal)
    ok = send_telegram_message(text)
    if ok:
        print(f"[weekly_plan] Sendt forslag for uke {proposal.week_start_date} "
              f"(variant {proposal.variant})")
        return 0
    print("[weekly_plan] Kunne ikke sende Telegram-melding", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
