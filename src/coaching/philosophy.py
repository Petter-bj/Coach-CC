"""Coaching-prinsipper som ren Python.

Grunnregler (jf. beslutninger i samtale 2026-04-21):

1. **Styrke krever nærhet til failure.** Ikke anbefal "ta det rolig" på styrke
   basert på readiness med mindre readiness er ekstremt lav (<25).
2. **Dobbel progresjon** er standard for styrke. Per øvelse har du et
   rep-vindu [rep_min, rep_max]. Topp-sett når rep_max → øk vekt med
   increment_kg, reset til rep_min. Under rep_max → samme vekt, push +1 rep.
3. **e1RM kun for compound.** Isolasjon tracker vi på reps/sett-progresjon.
4. **Ingen planlagt deload.** Naturlig deload skjer via ferier, sykdom og
   livet — ikke noe vi scheduler. Ikke foreslå deload proaktivt.
5. **Readiness-data påvirker kondisjon/volum, ikke styrkeintensitet.**
6. **Training-priority er brukerkonfigurerbar** — `cardio` | `strength` |
   `balanced`. Påvirker hvordan konflikter mellom styrke- og løpeøkter løses.
7. **Skade-spesifikke hard-stops:** shin splints aktiv → ingen løping
   uansett hva planen sier. Cross-train (skierg/sykkel) er OK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# Progression — double progression
# ---------------------------------------------------------------------------


@dataclass
class ProgressionRecommendation:
    action: Literal["add_weight", "add_reps", "hold", "no_data"]
    target_weight_kg: float | None
    target_reps: int | None
    reasoning: str
    basis: dict  # raw input som anbefalingen bygger på (for debug/display)


def next_set_for_exercise(
    last_top_set: dict | None,
    rep_min: int,
    rep_max: int,
    increment_kg: float,
) -> ProgressionRecommendation:
    """Dobbel progresjon-regel.

    Args:
        last_top_set: siste økts topp-sett som {"reps": N, "weight_kg": X}
            eller None hvis ingen historikk.
        rep_min, rep_max: rep-vindu (f.eks. 6 og 10).
        increment_kg: vekt-tillegg når topp-sett treffer rep_max.

    Returnerer anbefaling for neste økt.

    Regel:
        - Ingen historikk → no_data, be brukeren etablere baseline
        - Bodyweight (weight_kg=None) → push reps
        - reps >= rep_max → øk vekt + increment, reset til rep_min
        - reps < rep_max → samme vekt, push +1 rep (tak ved rep_max)
    """
    if last_top_set is None or last_top_set.get("reps") is None:
        return ProgressionRecommendation(
            action="no_data",
            target_weight_kg=None,
            target_reps=None,
            reasoning=(
                "Ingen historikk for denne øvelsen. Etabler baseline: "
                f"velg en vekt du klarer {rep_min}–{rep_max} reps på, "
                f"kjør {rep_min}–3 arbeidssett."
            ),
            basis={"last_top_set": None, "rep_min": rep_min, "rep_max": rep_max},
        )

    reps = last_top_set["reps"]
    weight = last_top_set.get("weight_kg")

    if weight is None or weight == 0:
        # Bodyweight — push reps oppover uten rep-cap
        return ProgressionRecommendation(
            action="add_reps",
            target_weight_kg=None,
            target_reps=reps + 1,
            reasoning=(
                f"Bodyweight — siste topp-sett {reps} reps. "
                f"Sikt mot {reps + 1} neste gang."
            ),
            basis={"last_top_set": last_top_set},
        )

    if reps >= rep_max:
        new_weight = round(weight + increment_kg, 2)
        return ProgressionRecommendation(
            action="add_weight",
            target_weight_kg=new_weight,
            target_reps=rep_min,
            reasoning=(
                f"Traff {reps} reps ved {weight} kg (topp av {rep_min}–{rep_max}-vinduet). "
                f"Øk til {new_weight} kg, start på {rep_min} reps."
            ),
            basis={"last_top_set": last_top_set, "increment_kg": increment_kg},
        )

    # reps < rep_max → samme vekt, push +1 rep
    target = min(reps + 1, rep_max)
    next_weight = round(weight + increment_kg, 2)
    return ProgressionRecommendation(
        action="add_reps",
        target_weight_kg=weight,
        target_reps=target,
        reasoning=(
            f"Sist: {reps} reps ved {weight} kg. Behold {weight} kg, sikt mot "
            f"{target} reps. Når du lander {rep_max} reps → neste økt blir "
            f"{next_weight} kg."
        ),
        basis={"last_top_set": last_top_set, "rep_max": rep_max},
    )


# ---------------------------------------------------------------------------
# Injury hard-stops
# ---------------------------------------------------------------------------


SHIN_SPLINTS_KEYWORDS = ("shin", "legghinne", "legg hinne", "mediotibial", "skinn")


@dataclass
class InjuryRuling:
    allow: bool
    reason: str | None
    alternative: str | None  # forslag til cross-training hvis allow=False


def running_ruling(active_injuries: list[dict]) -> InjuryRuling:
    """Hard regel: shin splints aktiv → ingen løping.

    Args:
        active_injuries: liste av dicts fra `injury active`-CLI-en.
            Forventede felt: body_part, severity, started_at, notes.

    Returns:
        InjuryRuling med `allow=False` hvis shin splints aktiv, ellers True.
    """
    for inj in active_injuries:
        bp = (inj.get("body_part") or "").lower()
        notes = (inj.get("notes") or "").lower()
        if any(k in bp or k in notes for k in SHIN_SPLINTS_KEYWORDS):
            started = inj.get("started_at") or inj.get("logged_at") or "?"
            severity = inj.get("severity", "?")
            return InjuryRuling(
                allow=False,
                reason=(
                    f"Shin splints aktiv (siden {started}, severity {severity}/3). "
                    f"Ingen løping i dag — uansett hva planen sier eller "
                    f"readiness/HRV indikerer."
                ),
                alternative=(
                    "Cross-train i stedet: skierg, sykkel eller svømming. "
                    "Opprettholder kondisjon uten aksial belastning på leggen."
                ),
            )

    return InjuryRuling(allow=True, reason=None, alternative=None)


# ---------------------------------------------------------------------------
# Readiness-mapping (styrke vs kondisjon)
# ---------------------------------------------------------------------------


def readiness_advice(
    workout_type: Literal["strength", "cardio", "cross"],
    training_readiness: int | None,
) -> dict | None:
    """Gi readiness-basert råd — men bare på cardio/cross, ikke styrke.

    Args:
        workout_type: hvilken type økt vurderes
        training_readiness: Garmin Training Readiness-score 0-100

    Returns:
        dict med `severity` og `message`, eller None hvis ingen advarsel.
    """
    if training_readiness is None:
        return None

    if workout_type == "strength":
        # Kun blokker hvis readiness er ekstremt lav (brukeren er tydelig syk/crashet)
        if training_readiness < 25:
            return {
                "severity": "warning",
                "message": (
                    f"Training readiness {training_readiness}/100 er ekstremt lavt — "
                    "vurder om du er i ferd med å bli syk. Ellers: styrke krever "
                    "intensitet nær failure, kjør planlagt økt hvis kroppen sier OK."
                ),
            }
        return None  # Readiness påvirker ikke styrkeintensitet ellers

    # Cardio / cross-training
    if training_readiness < 40:
        return {
            "severity": "recommend_easy",
            "message": (
                f"Readiness {training_readiness}/100 er lav. "
                "Anbefal Z1-Z2 bare — skip intervaller/tempo i dag. "
                "Flytt hardøkta til i morgen eller overmorgen."
            ),
        }
    if training_readiness < 60:
        return {
            "severity": "caution",
            "message": (
                f"Readiness {training_readiness}/100 er under optimal. "
                "Planlagt hardøkt er OK, men ikke press ekstra."
            ),
        }
    return None


# ---------------------------------------------------------------------------
# Strength vs running conflict (for priority-variabelen)
# ---------------------------------------------------------------------------


def strength_running_conflict(
    priority: str,
    *,
    is_race_week: bool = False,
    same_muscle_group_hours_ago: int | None = None,
) -> dict | None:
    """Returner styrkemodulering hvis det er konflikt.

    Args:
        priority: 'cardio' | 'strength' | 'balanced'
        is_race_week: løp denne uken
        same_muscle_group_hours_ago: antall timer siden samme muskelgruppe ble
            trent hardt (for advarsel om legs før langtur)

    Returns:
        dict med modulering ('skip', 'reduce', 'flag') eller None.
    """
    if is_race_week:
        if priority == "cardio":
            return {
                "action": "reduce",
                "guidance": (
                    "Race-uke + kondis-prioritet: kutt styrkevolum til ~60%, "
                    "bare tekniske sett. Ingen deadlifts eller tungt heavy legs "
                    "48t før racet."
                ),
            }
        if priority == "balanced":
            return {
                "action": "reduce",
                "guidance": "Race-uke: reduser styrke til ~75%, dropp legs-dagen hvis < 72t før racet.",
            }
        # priority == strength → ingen reduksjon av styrke selv i race-uke

    if same_muscle_group_hours_ago is not None and same_muscle_group_hours_ago < 36:
        if priority == "cardio":
            return {
                "action": "flag",
                "guidance": (
                    f"Tunge legs for {same_muscle_group_hours_ago}t siden og planlagt løpeøkt "
                    "snart — legg tyngden på rolig pace. Restitusjonen er ikke ferdig."
                ),
            }

    return None
