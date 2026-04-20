"""Engangs-importer for strukturert styrkelogg fra Excel.

Leser `Styrkelogg`-arket fra Treningslogg_Petter.xlsx og populerer:
    workouts (source='strength', én rad per (Dato, Økt))
    strength_sessions
    strength_sets
Beregner e1RM per sett (Epley: vekt × (1 + reps/30)).

Idempotent: eksisterende strength-workouts med samme external_id oppdateres
heller enn dupliseres.

Usage:
    .venv/bin/python spikes/import_strength_xlsx.py <path/til/fil.xlsx>
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl

from src.db.connection import connect
from src.db.migrations import migrate


def _epley_e1rm(weight_kg: float, reps: int) -> float:
    """Estimert 1RM via Epley-formelen."""
    if reps <= 0 or weight_kg <= 0:
        return 0.0
    return round(weight_kg * (1 + reps / 30), 2)


def _normalize_date(value) -> str | None:
    """Konverter Excel-dato-celle til ISO YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        # Forsøk parse f.eks. '2026-04-06'
        try:
            return datetime.fromisoformat(value.strip()).date().isoformat()
        except ValueError:
            return None
    return None


def _group_by_session(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Gruppér sett per (dato, økt)."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["date"], r["session_name"])
        groups[key].append(r)
    return groups


def read_xlsx(path: Path) -> list[dict]:
    """Parse Styrkelogg-arket til en flat liste med set-dicts."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Styrkelogg"]
    rows: list[dict] = []
    for row in ws.iter_rows(min_row=3, values_only=True):
        dato, okt, ovelse, sett_nr, reps, vekt, notat = row
        date_iso = _normalize_date(dato)
        if not date_iso or not ovelse:
            continue
        try:
            reps_int = int(reps) if reps is not None else None
        except (TypeError, ValueError):
            reps_int = None
        if not reps_int or reps_int <= 0:
            continue
        try:
            vekt_f = float(vekt) if vekt is not None else None
        except (TypeError, ValueError):
            vekt_f = None
        rows.append({
            "date": date_iso,
            "session_name": str(okt or "Unknown"),
            "exercise": str(ovelse).strip(),
            "set_num": int(sett_nr) if sett_nr is not None else len(rows) + 1,
            "reps": reps_int,
            "weight_kg": vekt_f,
            "notes": str(notat).strip() if notat else None,
        })
    return rows


def import_strength(path: Path) -> dict:
    """Hovedfunksjon: leser fil, oppretter workouts/sessions/sets i DB."""
    rows = read_xlsx(path)
    groups = _group_by_session(rows)

    workouts_created = 0
    sessions_created = 0
    sets_created = 0
    skipped_existing = 0

    with connect() as conn:
        migrate(conn)

        for (date_iso, session_name), session_rows in sorted(groups.items()):
            external_id = f"xlsx_{date_iso}_{session_name.lower()}"

            # Sjekk om workout allerede finnes
            existing = conn.execute(
                "SELECT id FROM workouts WHERE source='strength' AND external_id=?",
                (external_id,),
            ).fetchone()

            if existing:
                # Idempotens: slett gamle sets og gjenskap dem
                wid = existing["id"]
                sess_row = conn.execute(
                    "SELECT id FROM strength_sessions WHERE workout_id=?",
                    (wid,),
                ).fetchone()
                if sess_row:
                    conn.execute(
                        "DELETE FROM strength_sets WHERE session_id=?",
                        (sess_row["id"],),
                    )
                    session_id = sess_row["id"]
                else:
                    cur = conn.execute(
                        "INSERT INTO strength_sessions (workout_id) VALUES (?)",
                        (wid,),
                    )
                    session_id = cur.lastrowid
                    sessions_created += 1
                skipped_existing += 1
            else:
                # Opprett workout-rad
                # Approx tid: antar økt startet kl 17:00 lokal tid (ingen tid i logg)
                started_local = f"{date_iso}T17:00:00"
                cur = conn.execute(
                    """
                    INSERT INTO workouts
                        (external_id, source, started_at_utc, timezone,
                         local_date, type, notes)
                    VALUES (?, 'strength', ?, 'Europe/Oslo', ?, 'strength_training', ?)
                    """,
                    (
                        external_id,
                        f"{date_iso}T15:00:00Z",  # 17:00 lokal = 15:00 UTC (sommertid)
                        date_iso,
                        f"Økt: {session_name}",
                    ),
                )
                wid = cur.lastrowid
                workouts_created += 1

                cur = conn.execute(
                    "INSERT INTO strength_sessions (workout_id) VALUES (?)",
                    (wid,),
                )
                session_id = cur.lastrowid
                sessions_created += 1

            # Sett inn settene
            for s in session_rows:
                e1rm = _epley_e1rm(s["weight_kg"] or 0, s["reps"])
                conn.execute(
                    """
                    INSERT INTO strength_sets
                        (session_id, exercise, set_num, reps, weight_kg,
                         e1rm_kg, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, s["exercise"], s["set_num"],
                     s["reps"], s["weight_kg"], e1rm, s["notes"]),
                )
                sets_created += 1

        conn.commit()

    return {
        "workouts_created": workouts_created,
        "workouts_refreshed": skipped_existing,
        "sessions_created": sessions_created,
        "sets_created": sets_created,
        "total_groups": len(groups),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: import_strength_xlsx.py <path>", file=sys.stderr)
        return 1
    path = Path(sys.argv[1]).expanduser().resolve()
    if not path.exists():
        print(f"Fil ikke funnet: {path}", file=sys.stderr)
        return 1

    stats = import_strength(path)
    print(f"✓ Import fullført fra {path.name}")
    print(f"  Grupper (økter): {stats['total_groups']}")
    print(f"  Nye workouts:    {stats['workouts_created']}")
    print(f"  Oppdaterte:      {stats['workouts_refreshed']}")
    print(f"  Strength_sessions: {stats['sessions_created']}")
    print(f"  Strength_sets:     {stats['sets_created']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
