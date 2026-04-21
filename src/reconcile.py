"""Dedupe av overlappende økter mellom kilder.

Hovedproblem: Garmin-klokka auto-detekterer Concept2-skierg-økter som
`indoor_rowing`. Begge logges separat — Concept2 via API-eksport, Garmin
via klokkas HR-overvåking.

Regel (per plan §4):
* Concept2 er source of truth for erg-data (watts, stroke-rate, per-intervall).
* Garmin-raden merkes `superseded_by = <concept2_workout_id>` når vi er trygge
  på at det er samme økt. Den slettes IKKE — Garmin har HR-data Concept2 mangler
  (hvis pulsbeltet var på).

Match-logikk:
* Type matcher via mapping: skierg↔indoor_rowing, rower↔indoor_rowing.
* Intervallene overlapper tidsmessig (Garmin [g_start, g_end] og
  Concept2 [c_start, c_end] har ≥ 1 sekund felles tid).
* Concept2-varigheten er mellom 20% og 150% av Garmin-varigheten.

Kombinasjonen av temporal overlap + type-map + duration-ratio filtrerer
bort tilfeller der du har to skierg-økter samme dag (ingen overlapp),
men fanger det realistiske scenarioet der Garmin starter 30-45 min
tidligere med oppvarming før du logger skierg-økta på ErgData-appen.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

# Type-mapping: Garmin → Concept2
TYPE_MATCH = {
    "indoor_rowing": {"skierg", "rower", "bikeerg"},
    "rowing": {"rower"},
}

# Duration-toleranse: Concept2 kan være mellom 20% og 150% av Garmin.
# Observert: 25 min skierg ≈ 0.45 av 56 min Garmin indoor_rowing.
DURATION_MIN_RATIO = 0.2
DURATION_MAX_RATIO = 1.5

# Søkevindu rundt Garmin-økta (vi henter Concept2-kandidater som kan
# overlappe — hvis c_end >= g_start - SEARCH_MIN og c_start <= g_end + SEARCH_MIN).
SEARCH_SLACK_MIN = 5


def _parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _intervals_overlap(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> float:
    """Returner antall sekunder intervallene [a_start, a_end] og
    [b_start, b_end] overlapper. 0 eller negativ = ingen overlapp."""
    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    return max(0.0, (overlap_end - overlap_start).total_seconds())


def _match_score(garmin: dict, concept2: dict) -> float | None:
    """Returner "match-score" (0-1) eller None hvis ikke match.

    Kriterier:
    * Type-mapping må matche (indoor_rowing ↔ skierg/rower/bikeerg)
    * Intervallene må overlappe tidsmessig
    * Duration-ratio innenfor 0.2-1.5
    """
    # Type-sjekk
    garmin_type = garmin.get("type")
    concept2_type = concept2.get("type")
    if garmin_type not in TYPE_MATCH:
        return None
    if concept2_type not in TYPE_MATCH[garmin_type]:
        return None

    # Duration-sjekk
    g_dur = garmin.get("duration_sec") or 0
    c_dur = concept2.get("duration_sec") or 0
    if not (g_dur and c_dur):
        return None
    ratio = c_dur / g_dur
    if not (DURATION_MIN_RATIO <= ratio <= DURATION_MAX_RATIO):
        return None

    # Temporal overlap
    g_start = _parse_utc(garmin["started_at_utc"])
    g_end = g_start + timedelta(seconds=g_dur)
    c_start = _parse_utc(concept2["started_at_utc"])
    c_end = c_start + timedelta(seconds=c_dur)
    overlap_sec = _intervals_overlap(g_start, g_end, c_start, c_end)
    if overlap_sec <= 0:
        return None

    # Score: hvor stor andel av Concept2-økta som faktisk ligger innenfor
    # Garmin-vinduet (0-1), vektet sammen med duration-ratio.
    overlap_share = overlap_sec / c_dur
    dur_score = 1.0 - abs(1.0 - ratio)
    return 0.7 * overlap_share + 0.3 * dur_score


# Dedupe strength-logg (xlsx eller chat-screenshot) mot Hevy-sync:
# Hevy har mest presise data (reps, vekt, RPE, hviletid) og er source of truth.
# Match-regel: source='strength' og source='hevy' som starter innenfor samme
# 1-times-vindu — mark strength-raden som superseded_by Hevy-raden.
STRENGTH_MATCH_WINDOW_MIN = 60


def _dedupe_strength_vs_hevy(conn: sqlite3.Connection) -> int:
    """Finn xlsx/screenshot-styrkerader som duplikerer Hevy-økter."""
    strength_rows = conn.execute(
        """
        SELECT id, started_at_utc, duration_sec
          FROM workouts
         WHERE source = 'strength'
           AND superseded_by IS NULL
        """
    ).fetchall()

    if not strength_rows:
        return 0

    marked = 0
    for s in strength_rows:
        s_start = _parse_utc(s["started_at_utc"])
        window_start = (s_start - timedelta(minutes=STRENGTH_MATCH_WINDOW_MIN)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        window_end = (s_start + timedelta(minutes=STRENGTH_MATCH_WINDOW_MIN)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        candidate = conn.execute(
            """
            SELECT id, started_at_utc
              FROM workouts
             WHERE source = 'hevy'
               AND started_at_utc BETWEEN ? AND ?
             ORDER BY ABS(strftime('%s', started_at_utc) - strftime('%s', ?))
             LIMIT 1
            """,
            (window_start, window_end, s["started_at_utc"]),
        ).fetchone()

        if candidate is not None:
            conn.execute(
                "UPDATE workouts SET superseded_by = ? WHERE id = ?",
                (candidate["id"], s["id"]),
            )
            marked += 1

    return marked


def dedupe_workouts(conn: sqlite3.Connection) -> int:
    """Finn overlapp mellom Garmin indoor_rowing og Concept2 skierg/rower,
    og mellom source='strength' og source='hevy'.

    Marker den mindre presise kilden med `superseded_by = <preferred_id>`.

    Returns:
        Antall økter markert som superseded i denne kjøringen (begge regler summert).
    """
    # Kandidater: Garmin indoor_rowing uten eksisterende superseded_by
    garmin_rows = conn.execute(
        """
        SELECT id, source, type, started_at_utc, duration_sec, local_date
          FROM workouts
         WHERE source = 'garmin'
           AND type IN ('indoor_rowing', 'rowing')
           AND superseded_by IS NULL
        """
    ).fetchall()

    if not garmin_rows and not conn.execute(
        "SELECT 1 FROM workouts WHERE source='hevy' LIMIT 1"
    ).fetchone():
        return 0

    marked = 0
    for g in garmin_rows:
        g_start = _parse_utc(g["started_at_utc"])
        g_end = g_start + timedelta(seconds=g["duration_sec"] or 0)
        # Kandidat-vindu: Concept2 start kan være opptil g_end + slack,
        # og Concept2 end kan være helt fra g_start - slack (altså økter
        # som kan overlappe i det hele tatt med Garmin-intervallet).
        window_start = (g_start - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        window_end = (g_end + timedelta(minutes=SEARCH_SLACK_MIN)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        candidates = conn.execute(
            """
            SELECT id, source, type, started_at_utc, duration_sec
              FROM workouts
             WHERE source = 'concept2'
               AND started_at_utc BETWEEN ? AND ?
            """,
            (window_start, window_end),
        ).fetchall()

        best: tuple[float, int] | None = None
        for c in candidates:
            score = _match_score(dict(g), dict(c))
            if score is None:
                continue
            if best is None or score > best[0]:
                best = (score, c["id"])

        if best is not None:
            conn.execute(
                "UPDATE workouts SET superseded_by = ? WHERE id = ?",
                (best[1], g["id"]),
            )
            marked += 1

    # Andre regel: strength (xlsx/chat) ↔ hevy
    marked += _dedupe_strength_vs_hevy(conn)

    conn.commit()
    return marked
