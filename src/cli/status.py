"""`status`-CLI: oversikt over sync-helse og aktiv kontekst.

Viser:
* Siste-synket-tid per (source, stream)
* Ubekreftede alerts
* Aktive skader + pågående kontekst-perioder (reise/sykdom/stress)
* Dagens dato og backfill-vinduer som er tilgjengelige
"""

from __future__ import annotations

import typer

from src.cli._common import emit
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=False)


def _collect_status() -> dict:
    with connect() as c:
        streams = [
            dict(r) for r in c.execute(
                """
                SELECT source, stream, last_successful_sync_at,
                       consecutive_failures, last_error_message, next_retry_at
                  FROM source_stream_state
                 ORDER BY source, stream
                """
            ).fetchall()
        ]
        alerts = [
            dict(r) for r in c.execute(
                """
                SELECT id, source, level, message, created_at
                  FROM alerts
                 WHERE acknowledged_at IS NULL
                 ORDER BY created_at DESC
                """
            ).fetchall()
        ]
        injuries = [
            dict(r) for r in c.execute(
                """
                SELECT id, body_part, severity, started_at, status, notes
                  FROM injuries
                 WHERE status IN ('active', 'healing')
                 ORDER BY started_at DESC
                """
            ).fetchall()
        ]
        contexts = [
            dict(r) for r in c.execute(
                """
                SELECT id, category, starts_on, ends_on, notes
                  FROM context_log
                 WHERE ends_on IS NULL OR ends_on >= date('now')
                 ORDER BY starts_on DESC
                """
            ).fetchall()
        ]

    return {
        "streams": streams,
        "alerts": alerts,
        "injuries": injuries,
        "contexts": contexts,
    }


def _format_text(data: dict) -> str:
    lines = ["# Sync-status\n"]

    # Streams
    if data["streams"]:
        lines.append("## Sist-synket per kilde/strøm")
        for s in data["streams"]:
            last = s["last_successful_sync_at"] or "aldri"
            fails = s["consecutive_failures"]
            marker = f" ⚠ {fails} feil" if fails else ""
            err = f"  (feil: {s['last_error_message']})" if s["last_error_message"] else ""
            lines.append(f"  {s['source']:10} / {s['stream']:18} {last}{marker}{err}")
    else:
        lines.append("## Ingen synkroniserte strømmer ennå")

    # Alerts
    lines.append("")
    if data["alerts"]:
        lines.append(f"## Ubekreftede alerts ({len(data['alerts'])})")
        for a in data["alerts"]:
            lines.append(f"  [{a['level'].upper():7}] {a['source']}: {a['message']}")
    else:
        lines.append("## Ingen ubekreftede alerts")

    # Injuries
    lines.append("")
    if data["injuries"]:
        lines.append(f"## Aktive skader ({len(data['injuries'])})")
        for inj in data["injuries"]:
            notes = f" — {inj['notes']}" if inj["notes"] else ""
            lines.append(
                f"  {inj['body_part']} (sev={inj['severity']}, {inj['status']}, "
                f"siden {inj['started_at']}){notes}"
            )
    else:
        lines.append("## Ingen aktive skader")

    # Context
    lines.append("")
    if data["contexts"]:
        lines.append(f"## Aktive kontekst-perioder ({len(data['contexts'])})")
        for ctx in data["contexts"]:
            period = f"{ctx['starts_on']} → {ctx['ends_on'] or 'pågående'}"
            notes = f": {ctx['notes']}" if ctx["notes"] else ""
            lines.append(f"  {ctx['category']:12} {period}{notes}")
    else:
        lines.append("## Ingen aktive kontekst-perioder")

    return "\n".join(lines) + "\n"


@app.command()
def main(json_output: bool = typer.Option(False, "--json", help="Strukturert JSON")) -> None:
    """Vis systemstatus og aktiv kontekst."""
    data = _collect_status()
    emit(data, as_json=json_output, text=_format_text(data))


if __name__ == "__main__":
    app()
