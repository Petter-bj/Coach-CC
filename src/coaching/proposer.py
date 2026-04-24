"""Foreslår neste ukes treningsplan basert på aktiv blokk, adherence og state.

Kjernefunksjon: `propose_week(conn, week_start_date)` → `ProposedWeek`.

Logikken:
1. Les aktiv blokk → bestemmer phase + canonical week-struktur
2. Les siste ukes shin-status → variant A (grønn) / B (gul) / C (rød)
3. Les volum-trend → skaler løpevolum innenfor +10-15%/uke-regel
4. Les HRV/wellness → flagg soften-mode hvis signal-dropp
5. Emit 7 dager med type, intensitet, varighet + reasoning

Propose-output er pure data; persistens (skrive til `planned_sessions`,
oppdatere training-plan.md, pushe til Hevy) håndteres av CLI-en.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from src.coaching.philosophy import PhaseGuidance, phase_guidance, running_ruling
from src.coaching.preferences import current_phase, get_active_block, get_hr_max


# ---------------------------------------------------------------------------
# Data-typer
# ---------------------------------------------------------------------------


SessionType = Literal[
    "upper_1_push", "upper_2_pull", "lower",
    "easy_run", "z3_run", "long_run",
    "easy_skierg", "z3_skierg", "hard_skierg",
    "rest", "prehab",
]


@dataclass
class ProposedSession:
    planned_date: str  # YYYY-MM-DD
    day_of_week: str  # Mon/Tue/...
    type: SessionType
    duration_min: int | None
    intensity_zone: str | None  # "Z2", "Z3", etc.
    hr_target: tuple[int, int] | None  # (low, high) bpm
    description: str  # kort plan-tekst
    notes: str = ""  # valgfrie merknader


@dataclass
class ProposedWeek:
    week_start_date: str  # YYYY-MM-DD (mandag)
    variant: str  # "A_green" | "B_yellow" | "C_red"
    sessions: list[ProposedSession] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    estimated_run_km: float = 0.0
    phase: str = "base"


# ---------------------------------------------------------------------------
# State-lesere
# ---------------------------------------------------------------------------


def weekly_running_volume_km(conn: sqlite3.Connection, days_back: int = 7) -> float:
    """Sum km løping siste N dager (fra workouts med type starts with 'run' eller 'running')."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(distance_m), 0) / 1000.0 AS km
          FROM workouts
         WHERE superseded_by IS NULL
           AND (type = 'running' OR type = 'run' OR type LIKE 'run_%')
           AND date(started_at_utc) >= date('now', ?)
        """,
        (f"-{days_back} days",),
    ).fetchone()
    return float(row["km"]) if row else 0.0


def shin_status(conn: sqlite3.Connection) -> str:
    """Returner klassifisering: 'clear' | 'active_mild' | 'active_moderate' | 'active_severe'."""
    rows = conn.execute(
        """
        SELECT body_part, severity, notes
          FROM injuries
         WHERE status = 'active'
        """
    ).fetchall()
    from src.coaching.philosophy import SHIN_SPLINTS_KEYWORDS

    max_severity = 0
    for r in rows:
        bp = (r["body_part"] or "").lower()
        notes = (r["notes"] or "").lower()
        if any(k in bp or k in notes for k in SHIN_SPLINTS_KEYWORDS):
            sev = int(r["severity"] or 1)
            if sev > max_severity:
                max_severity = sev

    if max_severity == 0:
        return "clear"
    if max_severity == 1:
        return "active_mild"
    if max_severity == 2:
        return "active_moderate"
    return "active_severe"


def recent_hrv_dip(conn: sqlite3.Connection, threshold_pct: float = 0.1) -> bool:
    """Returner True hvis siste 7d HRV-snitt er mer enn threshold% under 30d-snitt."""
    row = conn.execute(
        """
        SELECT
            AVG(CASE WHEN date(local_date) >= date('now', '-7 days')
                     THEN last_night_avg_ms END) AS avg_7d,
            AVG(CASE WHEN date(local_date) >= date('now', '-30 days')
                     THEN last_night_avg_ms END) AS avg_30d
          FROM garmin_hrv
         WHERE last_night_avg_ms IS NOT NULL
           AND date(local_date) >= date('now', '-30 days')
        """
    ).fetchone()
    if not row or row["avg_7d"] is None or row["avg_30d"] is None:
        return False
    a7 = float(row["avg_7d"])
    a30 = float(row["avg_30d"])
    if a30 <= 0:
        return False
    return (a30 - a7) / a30 >= threshold_pct


def wellness_avg_7d(conn: sqlite3.Connection) -> float | None:
    """Snitt av (sleep + motivation + energy - soreness) / 3 siste 7d.
    Høyere = bedre. None hvis ingen data."""
    row = conn.execute(
        """
        SELECT AVG((sleep_quality + motivation + energy) / 3.0) AS score
          FROM wellness_daily
         WHERE date(local_date) >= date('now', '-7 days')
        """
    ).fetchone()
    return float(row["score"]) if row and row["score"] is not None else None


# ---------------------------------------------------------------------------
# Core proposal logic
# ---------------------------------------------------------------------------


# Canonical ULU-uke: dag-0 = mandag
DAYS_NO = ["Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"]


def propose_week(
    conn: sqlite3.Connection,
    week_start: date,
) -> ProposedWeek:
    """Foreslå en uke fra week_start (mandag). Bruker aktiv blokks canonical
    struktur, justerer for shin-status og volum-trend."""
    # --- Context ---
    block = get_active_block(conn)
    phase = block.phase if block else "base"
    guidance = phase_guidance(phase)

    shin = shin_status(conn)
    last_week_km = weekly_running_volume_km(conn, days_back=7)
    hrv_dip = recent_hrv_dip(conn)
    wellness = wellness_avg_7d(conn)
    hr_max = get_hr_max(conn) or 195  # fallback estimate

    # --- Variant-valg basert på shin ---
    if shin == "active_severe":
        variant = "C_red"
    elif shin == "active_moderate":
        variant = "C_red"  # treat moderate as no-running like severe for safety
    elif shin == "active_mild" or hrv_dip:
        variant = "B_yellow"
    else:
        variant = "A_green"

    # --- Volum-beregning ---
    # +10-15% fra forrige uke; strengere hvis shin er aktiv
    volume_growth_pct = 0.10 if shin != "clear" else 0.15
    target_run_km = last_week_km * (1 + volume_growth_pct) if last_week_km > 2 else 10.0

    reasoning: list[str] = []
    reasoning.append(f"Aktiv blokk: {block.name if block else '(ingen)'} (phase={phase})")
    reasoning.append(f"Shin-status: {shin} → variant {variant}")
    reasoning.append(
        f"Løp siste uke: {last_week_km:.1f} km → foreslår ~{target_run_km:.1f} km "
        f"({'+' if volume_growth_pct >= 0 else ''}{volume_growth_pct*100:.0f}%)"
    )
    if hrv_dip:
        reasoning.append("⚠ HRV-dropp > 10% siste 7d vs 30d — softer hard-økter")
    if wellness is not None and wellness < 5:
        reasoning.append(f"⚠ Wellness-snitt {wellness:.1f}/10 lavt — vurder å redusere volum")

    # --- HR-targets ---
    z2_hr = (int(0.72 * hr_max), int(0.82 * hr_max))
    z3_hr = (int(0.82 * hr_max), int(0.87 * hr_max))
    hard_hr = (int(0.90 * hr_max), int(0.95 * hr_max))

    # --- Canonical week by variant ---
    sessions: list[ProposedSession] = []

    def add(day_idx: int, **kwargs) -> None:
        dt = week_start + timedelta(days=day_idx)
        sessions.append(ProposedSession(
            planned_date=dt.isoformat(),
            day_of_week=DAYS_NO[day_idx],
            **kwargs,
        ))

    if variant == "A_green":
        # Full ULU + Z3-løp
        add(0, type="upper_1_push", duration_min=60, intensity_zone=None,
            hr_target=None,
            description="Upper 1 (push-tung) — bench/OHP compound, lettere pull-assist")
        # Tue: easy run hvis volum > 20 km/uke, ellers easy skierg
        if last_week_km >= 20:
            km_tue = max(4.0, target_run_km * 0.20)
            dur_tue = int(km_tue * 6.5)
            add(1, type="easy_run", duration_min=dur_tue, intensity_zone="Z2",
                hr_target=z2_hr,
                description=f"Easy run ~{km_tue:.1f} km, Z2")
        else:
            add(1, type="easy_skierg", duration_min=25, intensity_zone="Z2",
                hr_target=z2_hr,
                description="Easy SkiErg 25-30 min Z2")
        add(2, type="lower", duration_min=75, intensity_zone=None, hr_target=None,
            description="Lower (squat/deadlift compound) + SkiErg 4×4 Z4-Z5 kveld",
            notes="HARD dag. 4×4 min hard + 3 min rest mellom.")
        # Thu: Z3 run (kort format første gang)
        km_thu = 5.0 if last_week_km < 20 else 6.0
        add(3, type="z3_run", duration_min=35, intensity_zone="Z3", hr_target=z3_hr,
            description=f"Z3 run: 10 warmup + 4×3 min @ Z3 (2 min jog pause) + 10 cool — ~{km_thu:.1f} km",
            notes="Monitor shin-respons 48t etterpå.")
        add(4, type="upper_2_pull", duration_min=75, intensity_zone=None, hr_target=None,
            description="Upper 2 (pull-tung) + 20 min dedikert prehab (PRI + foot + glute)")
        add(5, type="easy_skierg", duration_min=25, intensity_zone="Z2", hr_target=z2_hr,
            description="Easy SkiErg 25-30 min Z2")
        km_sun = max(7.0, target_run_km * 0.55)
        dur_sun = int(km_sun * 6.2)
        add(6, type="long_run", duration_min=dur_sun, intensity_zone="Z2", hr_target=z2_hr,
            description=f"Long run ~{km_sun:.1f} km Z2 (lett samtaletempo)")

    elif variant == "B_yellow":
        # Litt softere: ingen Z3-løp, enklere Wed, fortsatt 2 løp
        add(0, type="upper_1_push", duration_min=60, intensity_zone=None, hr_target=None,
            description="Upper 1 (push-tung)")
        add(1, type="easy_skierg", duration_min=30, intensity_zone="Z2", hr_target=z2_hr,
            description="Easy SkiErg 30 min Z2")
        add(2, type="lower", duration_min=60, intensity_zone=None, hr_target=None,
            description="Lower + SkiErg 5×6 Z3 (ikke 4×4 denne uka — softer hard-dag)",
            notes="Gul-variant: mindre peak intensitet")
        km_thu = 5.0
        add(3, type="easy_run", duration_min=int(km_thu * 6.5), intensity_zone="Z2", hr_target=z2_hr,
            description=f"Easy run ~{km_thu:.1f} km Z2 (ingen Z3 denne uka)")
        add(4, type="upper_2_pull", duration_min=60, intensity_zone=None, hr_target=None,
            description="Upper 2 + 20 min prehab")
        add(5, type="easy_skierg", duration_min=25, intensity_zone="Z2", hr_target=z2_hr,
            description="Easy SkiErg 25 min Z2")
        km_sun = max(7.0, target_run_km * 0.55)
        add(6, type="long_run", duration_min=int(km_sun * 6.5), intensity_zone="Z2", hr_target=z2_hr,
            description=f"Long run ~{km_sun:.1f} km Z2")

    else:  # C_red
        # Ingen løp, alt cross-train / prehab-tung
        add(0, type="upper_1_push", duration_min=60, intensity_zone=None, hr_target=None,
            description="Upper 1 (push-tung)")
        add(1, type="easy_skierg", duration_min=30, intensity_zone="Z2", hr_target=z2_hr,
            description="Easy SkiErg 30 min Z2")
        add(2, type="lower", duration_min=60, intensity_zone=None, hr_target=None,
            description="Lower + easy SkiErg (ingen hard cardio)",
            notes="Rød-variant: shin-flare, ingen løping denne uka")
        add(3, type="easy_skierg", duration_min=30, intensity_zone="Z2", hr_target=z2_hr,
            description="Easy SkiErg i stedet for løp")
        add(4, type="upper_2_pull", duration_min=75, intensity_zone=None, hr_target=None,
            description="Upper 2 + 30 min dedikert prehab (ekstra PRI + foot-work)",
            notes="Aggressiv prehab denne uka.")
        add(5, type="easy_skierg", duration_min=40, intensity_zone="Z2", hr_target=z2_hr,
            description="Lang easy SkiErg 40 min Z2 (erstatter løp-volum)")
        add(6, type="rest", duration_min=None, intensity_zone=None, hr_target=None,
            description="Rest / easy sykkeltur hvis føles bra. Prioriter hvile.")

    # Estimér løpe-km i denne planen
    est_km = 0.0
    for s in sessions:
        if s.type in ("easy_run", "z3_run", "long_run"):
            # Heuristikk: estimert pace 6:30/km for easy, 5:30/km for Z3
            pace_min = 5.5 if s.type == "z3_run" else 6.5
            if s.duration_min:
                est_km += s.duration_min / pace_min

    return ProposedWeek(
        week_start_date=week_start.isoformat(),
        variant=variant,
        sessions=sessions,
        reasoning=reasoning,
        estimated_run_km=round(est_km, 1),
        phase=phase,
    )
