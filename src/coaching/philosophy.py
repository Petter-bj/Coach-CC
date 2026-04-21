"""Coaching-prinsipper som ren Python.

Grunnregler (for full prosa + kildehenvisning, se brukerens private doc
under `~/Library/Application Support/Trening/docs/philosophy.md`):

Styrke:
  1. 4–10 direkte sett per muskelgruppe per uke (tatt nær failure).
     < 4 = under-stimulus, > 10 = over-volum-flagg.
  2. RIR 1–2 på arbeidssett (default). Obligatorisk failure er unødvendig;
     konsekvent RIR > 3 = ikke nok stimulus.
  3. 2× frekvens per muskel per uke.
  4. Dobbel progresjon per øvelse. Topp-sett når rep_max → øk vekt
     +increment_kg, reset til rep_min. Under rep_max → samme vekt, +1 rep.
  5. e1RM kun for compound-løft.
  6. Ingen planlagt deload — reaktiv kun ved stagnasjon 3+ uker.
  7. Readiness-data påvirker cardio/volum, IKKE styrkeintensitet
     (unntatt ved readiness < 25, mulig sykdomssignal).

Løping (Norwegian Singles-inspirert, lactate-guided pyramidal):
  8. 75–80% av ukesvolum i easy-sone (HR < 70% HRmax, lactate < 1.0).
     20–25% av ukesvolum i sub-threshold "golden zone"
     (HR 80–87% HRmax, lactate 2.0–3.0).
  9. Sub-threshold som INTERVAL (ikke continuous tempo) — 2–3 singles/uke,
     aldri to sub-threshold samme dag.
  10. Gråsonen 3.0–4.0 mmol / HR 87–91% = flagg hvis ofte brukt utilsiktet
      (sliter ut uten tilsvarende adaptasjon).
  11. Volum-progresjon: forsiktig +10–15% over 2 uker, deretter flat/redusert.
      Ingen "10%-regel" — bruk 10–14 dagers loading + 7 lettere.
  12. Langtur maks 16–18 km for 10K-fokus, 18–22 km HM-build.

Styrke ↔ løp-konflikt:
  13. Heavy lift (≥ 85% av 1RM) < 24t før sub-threshold eller hardere løp
      = flagg ("eats the threshold").
  14. Plyometrics / eksplosivt lett-vekt er KOMPATIBELT med løpevolum.
  15. `training_priority = cardio` → styrke 60% volum race-uke,
      reduser intensitet i peak-fase.

Fueling:
  16. Hardøkter > 60 min: 60–90 g karbo/time (standard), 90–120 g/h i race.
  17. Protein: 1.8–2.0 g/kg (vedlikehold/bygg), 2.0–2.4 (cut).

Skader:
  18. Shin splints aktiv = hard stop på løping. Cross-train OK.
  19. Return etter symptomfri: 3d symptomfri → kort mykt løp ≤20 min @ < 70%HR,
      pusher +10%/uke hvis holder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# Konstanter — terskler alle coaching-regler leser fra
# ---------------------------------------------------------------------------

# Styrke
STRENGTH_SETS_PER_MUSCLE_MIN = 4
STRENGTH_SETS_PER_MUSCLE_MAX = 10
STRENGTH_RIR_TARGET_LOW = 1
STRENGTH_RIR_TARGET_HIGH = 2
STRENGTH_RIR_UNDER_STIMULUS_THRESHOLD = 3  # snitt RIR > 3 → under-stimulus
STRENGTH_FREQUENCY_DEFAULT = 2  # ganger/uke per muskel
HEAVY_LIFT_THRESHOLD_PCT_1RM = 0.85
HEAVY_LIFT_BEFORE_HARD_RUN_HOURS = 24

# Løping — Olympiatoppen I-soner (% av HRmax). Samme modell som
# brukes av Olympiatoppen (Norges toppidrettssenter) og dermed norske
# utholdenhetsutøvere generelt.
#
#   Z1: 55–72%   restitusjon / svært lett aerob
#   Z2: 72–82%   lett aerob (base-volum)
#   Z3: 82–87%   moderat / aerob terskel  ("golden zone" — lactate 2-3 mmol)
#   Z4: 87–92%   anaerob terskel           (lactate ~4 mmol — Bakkens "grey zone")
#   Z5: 92–97%   VO2max / hardt intervall
RUN_HR_Z1_MAX_PCT = 0.72
RUN_HR_Z2_MAX_PCT = 0.82
RUN_HR_Z3_MAX_PCT = 0.87
RUN_HR_Z4_MAX_PCT = 0.92

# Løpe-volum-distribusjon (andel av ukesvolum)
RUN_EASY_VOLUME_SHARE_MIN = 0.75
RUN_SUBTHRESHOLD_VOLUME_SHARE_LOW = 0.20
RUN_SUBTHRESHOLD_VOLUME_SHARE_HIGH = 0.25

# Volum-progresjon (mer konservativ pga skade-historikk)
RUN_VOLUME_RAMP_MAX_PCT_PER_2_WEEKS = 0.15

# Fueling
CARB_PER_HOUR_RACE_MIN_G = 90
CARB_PER_HOUR_RACE_MAX_G = 120
CARB_PER_HOUR_HARD_MIN_G = 60
CARB_PER_HOUR_HARD_MAX_G = 90
PROTEIN_PER_KG_MAINTAIN_LOW = 1.8
PROTEIN_PER_KG_MAINTAIN_HIGH = 2.0

# Restitusjon
SLEEP_SHORT_SINGLE_NIGHT_H = 6
SLEEP_SHORT_CUMULATIVE_7D_H = 42
READINESS_ILLNESS_GATE = 25
MORNING_HR_DEVIATION_FLAG_BPM = 7


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
        if training_readiness < READINESS_ILLNESS_GATE:
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


# ---------------------------------------------------------------------------
# Løpe-sone-klassifisering (HR-basert, Bakken pyramidal modell)
# ---------------------------------------------------------------------------


RunZone = Literal["Z1", "Z2", "Z3", "Z4", "Z5"]


def classify_run_zone(avg_hr: int | None, hr_max: int | None) -> RunZone | None:
    """Klassifiser en løpeøkt i Olympiatoppens 5 I-soner.

    Sone-grensene (% av HRmax):
        Z1  < 72%    restitusjon / svært lett
        Z2  72–82%   lett aerob (base-volum)
        Z3  82–87%   aerob terskel / "golden zone" — Bakkens sub-threshold
        Z4  87–92%   anaerob terskel — tradisjonell threshold ("grey zone"
                     per Bakken: unngås utilsiktet, brukes sparingly)
        Z5  ≥ 92%    VO2max / intervaller

    Args:
        avg_hr: snitt-HR fra Garmin-økta
        hr_max: brukerens målte max HR

    Returns:
        Sonenavn ("Z1"-"Z5"), eller None hvis data mangler.
    """
    if avg_hr is None or hr_max is None or hr_max <= 0:
        return None
    pct = avg_hr / hr_max
    if pct < RUN_HR_Z1_MAX_PCT:
        return "Z1"
    if pct < RUN_HR_Z2_MAX_PCT:
        return "Z2"
    if pct < RUN_HR_Z3_MAX_PCT:
        return "Z3"
    if pct < RUN_HR_Z4_MAX_PCT:
        return "Z4"
    return "Z5"


def weekly_intensity_distribution(
    sessions: list[dict],
) -> dict:
    """Summér ukens løpevolum per Olympiatoppen-sone og returner fordeling.

    Args:
        sessions: liste av dicts med {distance_m, zone}. `zone` = "Z1"..."Z5"
            eller None (None ignoreres).

    Returns:
        {
          "total_km": float,
          "z1_km"/"z1_pct" ... "z5_km"/"z5_pct",
          "aerobic_km": float  (Z1+Z2+Z3, alt sub-threshold),
          "aerobic_pct": float,
          "flags": [str, ...]   # advarsler ift. Bakken-modell
        }
    """
    by_zone = {"Z1": 0.0, "Z2": 0.0, "Z3": 0.0, "Z4": 0.0, "Z5": 0.0}
    total = 0.0
    for s in sessions:
        zone = s.get("zone")
        dist_m = s.get("distance_m") or 0
        if zone in by_zone:
            km = dist_m / 1000.0
            by_zone[zone] += km
            total += km

    flags: list[str] = []
    if total == 0:
        return {"total_km": 0.0, "flags": ["no_run_data"]}

    pct = {z: by_zone[z] / total for z in by_zone}
    # Z1+Z2 er easy aerobic volume. Z3 er sub-threshold ("golden zone").
    easy_share = pct["Z1"] + pct["Z2"]
    sub_share = pct["Z3"]
    grey_share = pct["Z4"]

    # Bakken pyramidal regel: mye Z1+Z2, moderat Z3, lite Z4+Z5
    if easy_share < RUN_EASY_VOLUME_SHARE_MIN:
        flags.append(
            f"low_aerobic_share: Z1+Z2 = {easy_share:.0%} "
            f"(mål ≥ {RUN_EASY_VOLUME_SHARE_MIN:.0%})"
        )
    if sub_share < RUN_SUBTHRESHOLD_VOLUME_SHARE_LOW and total > 20:
        flags.append(
            f"z3_share_low: {sub_share:.0%} "
            f"(mål {RUN_SUBTHRESHOLD_VOLUME_SHARE_LOW:.0%}–"
            f"{RUN_SUBTHRESHOLD_VOLUME_SHARE_HIGH:.0%} i Z3)"
        )
    if grey_share > 0.10:
        flags.append(
            f"z4_share_high: {grey_share:.0%} i Z4 — tradisjonell terskelsone, "
            "sliter ut uten tilsvarende adaptasjon hvis utilsiktet"
        )

    return {
        "total_km": round(total, 1),
        "z1_km": round(by_zone["Z1"], 1), "z1_pct": round(pct["Z1"], 3),
        "z2_km": round(by_zone["Z2"], 1), "z2_pct": round(pct["Z2"], 3),
        "z3_km": round(by_zone["Z3"], 1), "z3_pct": round(pct["Z3"], 3),
        "z4_km": round(by_zone["Z4"], 1), "z4_pct": round(pct["Z4"], 3),
        "z5_km": round(by_zone["Z5"], 1), "z5_pct": round(pct["Z5"], 3),
        "aerobic_km": round(by_zone["Z1"] + by_zone["Z2"] + by_zone["Z3"], 1),
        "aerobic_pct": round(easy_share + sub_share, 3),
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Volum-sanity for styrke
# ---------------------------------------------------------------------------


def weekly_strength_volume_check(sets_per_muscle: dict[str, int]) -> dict:
    """Flag muskelgrupper under/over anbefalt ukentlig sett-antall.

    Args:
        sets_per_muscle: {"chest": 9, "back": 14, ...} — direkte sett per uke

    Returns:
        {"under": [...], "over": [...], "in_range": [...], "flags": [str]}
    """
    under: list[str] = []
    over: list[str] = []
    in_range: list[str] = []
    for muscle, sets in sets_per_muscle.items():
        if sets < STRENGTH_SETS_PER_MUSCLE_MIN:
            under.append(muscle)
        elif sets > STRENGTH_SETS_PER_MUSCLE_MAX:
            over.append(muscle)
        else:
            in_range.append(muscle)

    flags: list[str] = []
    if under:
        flags.append(
            f"under_stimulus: {', '.join(under)} "
            f"(< {STRENGTH_SETS_PER_MUSCLE_MIN} sett/uke)"
        )
    if over:
        flags.append(
            f"over_volume: {', '.join(over)} "
            f"(> {STRENGTH_SETS_PER_MUSCLE_MAX} sett/uke — akkumulert fatigue-risiko)"
        )

    return {
        "under": under, "over": over, "in_range": in_range,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Fueling-anbefaling
# ---------------------------------------------------------------------------


def fueling_recommendation(
    duration_min: int,
    intensity: Literal["easy", "moderate", "hard", "race"],
) -> dict:
    """Returner karbo-per-time-anbefaling for en økt.

    Args:
        duration_min: planlagt varighet
        intensity: økt-intensitet

    Returns:
        {
          "carb_per_hour_g": (min, max) eller None,
          "total_carb_g": int eller None,
          "reasoning": str,
        }
    """
    if duration_min < 60:
        return {
            "carb_per_hour_g": None,
            "total_carb_g": None,
            "reasoning": (
                "Økt < 60 min — ingen fueling-behov under økta. Fokuser på "
                "pre-workout og post-workout måltid i stedet."
            ),
        }

    if intensity == "race":
        low, high = CARB_PER_HOUR_RACE_MIN_G, CARB_PER_HOUR_RACE_MAX_G
        reasoning = (
            f"Race: {low}–{high} g karbo/time. Multi-transportable "
            "(glukose + fruktose 2:1) for høyere absorpsjon. Krever "
            "mage-trening på forhånd."
        )
    elif intensity == "hard":
        low, high = CARB_PER_HOUR_HARD_MIN_G, CARB_PER_HOUR_HARD_MAX_G
        reasoning = (
            f"Hardøkt > 60 min: {low}–{high} g karbo/time. Start i nedre "
            "ende hvis du ikke er vant; pusher oppover over tid."
        )
    elif intensity == "moderate":
        low, high = 30, 60
        reasoning = (
            "Moderat økt > 60 min: 30–60 g karbo/time som vedlikehold."
        )
    else:  # easy
        low, high = 0, 30
        reasoning = (
            "Easy økt: fueling valgfritt. Noen base-fase sub-threshold-økter "
            "er bevisst i partial glycogen depletion som adaptiv stimulus — "
            "men ikke på langtur > 90 min."
        )

    hours = duration_min / 60.0
    total_low = int(low * hours)
    total_high = int(high * hours)
    return {
        "carb_per_hour_g": (low, high),
        "total_carb_g": (total_low, total_high),
        "reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# Blokk-fase-modulering — hvordan coaching-beslutninger skifter per fase
# ---------------------------------------------------------------------------


PhaseName = Literal["base", "build", "peak", "taper", "recovery"]


@dataclass
class PhaseGuidance:
    phase: str
    focus: str  # kort stikkord-fokus for fasen

    # Løping — skade-gated. Shin splints / generell vev-sensitivitet begrenser.
    run_intensity_cap_zone: str  # høyeste tillatte løpe-sone rutinemessig
    should_recommend_run_z3: bool  # rene Z3-intervaller som løp
    should_recommend_run_hard_intervals: bool  # Z5/VO2max som løp
    allow_neuromuscular_work: bool  # strides, korte accelerasjoner
    allow_progression_runs: bool  # easy → svak Z3-drift siste 5-10 min
    allow_long_runs_over_16km: bool

    # Cross-training (SkiErg, sykkel, roerg) — CV-gated. Ingen impact.
    # Regel: cross-training-intensitet skal ALDRI være mer begrenset enn
    # løpe-intensitet. Hvis du ikke kan løpe hardt pga skade, kan du likevel
    # få CV-stimulus via cross-training.
    cross_training_intensity_cap_zone: str
    should_recommend_cross_training_z3: bool
    should_recommend_cross_training_hard_intervals: bool

    volume_ramp_pct_per_week_max: float
    strength_modulation: str  # "normal" | "reduced" | "minimal"
    notes: list[str]  # ekstra kontekst for boten


def phase_guidance(phase: str | None) -> PhaseGuidance:
    """Returner hva som er passende gjøren i hver fase.

    base:      volum-toleranse + aerob base, ingen Z3-ramp
    build:     Z3-ramp tillatt, introduser Z5-økt når base-volum er stabilt
    peak:      race-spesifikt (race-pace, submax-økter)
    taper:     volum-reduksjon, intensitet bevart, sharp + rested
    recovery:  aktiv restitusjon, alt mykt

    Ukjent/None fase → default til base (konservativ).
    """
    if phase == "build":
        return PhaseGuidance(
            phase="build",
            focus="Z3-ramp + første Z5-økter, bygge spesifikk kapasitet",
            run_intensity_cap_zone="Z5",
            should_recommend_run_z3=True,
            should_recommend_run_hard_intervals=True,
            allow_neuromuscular_work=True,
            allow_progression_runs=True,
            allow_long_runs_over_16km=True,
            cross_training_intensity_cap_zone="Z5",
            should_recommend_cross_training_z3=True,
            should_recommend_cross_training_hard_intervals=True,
            volume_ramp_pct_per_week_max=0.10,
            strength_modulation="reduced",
            notes=[
                "Z3-volum skal være 20–25% av ukesvolumet i build.",
                "Introduser én Z5-økt/uke når base-volum > 40 km/uke.",
                "Heavy legs 24t før hardøkt → flagg (eats the threshold).",
            ],
        )
    if phase == "peak":
        return PhaseGuidance(
            phase="peak",
            focus="race-pace-spesifikt arbeid, sharp",
            run_intensity_cap_zone="Z5",
            should_recommend_run_z3=True,
            should_recommend_run_hard_intervals=True,
            allow_neuromuscular_work=True,
            allow_progression_runs=True,
            allow_long_runs_over_16km=True,
            cross_training_intensity_cap_zone="Z5",
            should_recommend_cross_training_z3=True,
            should_recommend_cross_training_hard_intervals=True,
            volume_ramp_pct_per_week_max=0.0,  # ikke ramp i peak
            strength_modulation="minimal",
            notes=[
                "Hold volum konstant eller svakt fallende.",
                "Styrke: bare tekniske sett, ingen tungt 48t før race.",
                "1–2 hardøkter/uke, ikke mer.",
            ],
        )
    if phase == "taper":
        return PhaseGuidance(
            phase="taper",
            focus="volum ned 30–50%, intensitet bevart, hvile",
            run_intensity_cap_zone="Z5",
            should_recommend_run_z3=True,
            should_recommend_run_hard_intervals=True,
            allow_neuromuscular_work=True,  # strides holder på skarphet
            allow_progression_runs=False,  # unngå nye stimuli
            allow_long_runs_over_16km=False,
            cross_training_intensity_cap_zone="Z3",  # lettere cross-training
            should_recommend_cross_training_z3=True,
            should_recommend_cross_training_hard_intervals=False,  # ikke CV-stress
            volume_ramp_pct_per_week_max=-0.3,  # ramping ned
            strength_modulation="minimal",
            notes=[
                "Negative taper: korte race-pace-biter, lite volum.",
                "Ingen nye stimuli, ingen tunge løft.",
                "Strides 2–3×/uke holder på neural sharpness.",
                "Søvn + hydrering + fueling er jobben.",
            ],
        )
    if phase == "recovery":
        return PhaseGuidance(
            phase="recovery",
            focus="aktiv restitusjon etter race eller skade",
            run_intensity_cap_zone="Z2",
            should_recommend_run_z3=False,
            should_recommend_run_hard_intervals=False,
            allow_neuromuscular_work=False,  # kroppen skal hvile
            allow_progression_runs=False,
            allow_long_runs_over_16km=False,
            cross_training_intensity_cap_zone="Z2",
            should_recommend_cross_training_z3=False,
            should_recommend_cross_training_hard_intervals=False,
            volume_ramp_pct_per_week_max=0.0,
            strength_modulation="reduced",
            notes=[
                "Ingen harde økter på minst 7–10 dager etter race.",
                "Cross-training OK og ofte foretrukket — men også lett.",
                "Lytt til kropp — ikke struktur.",
            ],
        )
    # base eller None — IMPORTANT nyanse:
    # Bakken-base generelt er Z3-HEAVY (sub-threshold 2-3 mmol er *definerende*
    # for fasen, ~4 sub-threshold-økter/uke i elite-versjonen, 2-3 i amatør-
    # Singles-versjonen). Det som ekskluderes er Z4+ (over-threshold / VO2max).
    #
    # Men denne PhaseGuidance defaulter konservativt: `should_recommend_run_z3
    # = False`. Det er fordi default-bruken er en REBUILD-base (skade- eller
    # detraining-restart) der løpe-Z3 er tissue-gated. En sunn, trent løper i
    # full Bakken-base skal ha run-Z3.
    #
    # Signal-gatene som åpner run-Z3 (håndteres av caller, ikke denne):
    #   - Ingen aktive shin splints / kne / leggrelaterte skader
    #   - Løpevolum ≥ 30 km/uke stabilt i 2+ uker
    #   - Ingen volum-ramp > 15% siste uke
    return PhaseGuidance(
        phase="base",
        focus="sub-threshold-arbeid som kjerne-stimulus, aerob base, volum-toleranse",
        run_intensity_cap_zone="Z2",  # default for rebuild-base; caller kan oppgradere
        should_recommend_run_z3=False,  # default konservativt; true for sunn base
        should_recommend_run_hard_intervals=False,  # Z5-løping venter til build
        allow_neuromuscular_work=True,
        allow_progression_runs=True,
        allow_long_runs_over_16km=False,
        cross_training_intensity_cap_zone="Z3",
        should_recommend_cross_training_z3=True,
        should_recommend_cross_training_hard_intervals=False,
        volume_ramp_pct_per_week_max=0.10,
        strength_modulation="normal",
        notes=[
            "VOLUM-TIER matter: Bakken-metoden er optimalisert for 100+ km/uke. "
            "Under 25 km/uke løp: cross-training bærer CV-stimulus, løping kun "
            "for spesifikk adaptasjon. 25-55 km/uke: polarisert hybrid (Seiler) "
            "passer bedre enn Bakken-pure. 55+ km/uke: full Bakken-pyramidal.",
            "Bakken-base er Z3-TUNG ved høyvolum: sub-threshold (2-3 mmol, 82-87% "
            "HRmax) er *definerende* stimulus. 2-3 økter/uke i amatør-versjonen.",
            "Men default i denne koden er REBUILD-base: run-Z3 blokkert fordi "
            "skade-historikk (shin splints) / nylig lavt volum gjør det usikkert. "
            "Z3-stimulusen flyttes midlertidig til cross-training (SkiErg, sykkel).",
            "Caller (bot) skal oppgradere run-Z3 til True når ALLE: (a) ingen "
            "aktive løpe-skader, (b) løpevolum ≥ 30 km/uke stabilt, (c) ingen "
            "volum-spike siste uke. Da er det 'late base' med full Bakken-profil.",
            "CROSS-TRAINING hard (Z4-Z5) er volum-tier-avhengig i base: "
            "Ved HØYT volum (>= 55 km/uke løp): Bakken-pyramidal — Z4-Z5 venter "
            "til build, kun sub-threshold stimulus nå. "
            "Ved LOW/MODERATE volum (< 55 km/uke): polarisert hybrid — 1 hard "
            "cross-training-økt/uke (Z4-Z5) er appropriat stimulus fordi løpe-"
            "volumet alene ikke bærer nok aerob belastning. Caller (bot) kan "
            "oppgradere `should_recommend_cross_training_hard_intervals` til "
            "True når løpevolum < 55 km/uke og ingen overlast-signal.",
            "Sustained Z4-Z5 LØPING venter uansett til build — tissue stress, "
            "ikke CV-stress, er regulerende der. Uansett volum-tier.",
            "Korte bursts (<30 sek) som briefly spiker til Z4-Z5 er OK og "
            "anbefalt — neuromuskulære, ikke aerobe. Strides 4–6×15–30s submax. "
            "Når shin stabil: X-element 10×200m eller hill sprints.",
            "Progressive runs OK (easy → svak Z3-drift siste 5–10 min av langtur) "
            "når volum er stabilt og shin er rolig.",
            "Volum-progresjon løping maks +10%/uke (strengere hvis skade-risiko).",
            "Styrke: normal progresjon — base-fase konflikter ikke med tunge løft.",
        ],
    )


# ---------------------------------------------------------------------------
# Søvn-basert readiness-flag
# ---------------------------------------------------------------------------


def sleep_readiness_flag(
    last_night_h: float | None,
    cumulative_7d_h: float | None,
) -> dict | None:
    """Flagg søvnunderskudd som påvirker dagsform.

    Args:
        last_night_h: siste natts søvn (timer)
        cumulative_7d_h: sum siste 7 dager

    Returns:
        dict med severity + message, eller None hvis OK.
    """
    if last_night_h is not None and last_night_h < SLEEP_SHORT_SINGLE_NIGHT_H:
        if (cumulative_7d_h is not None
                and cumulative_7d_h < SLEEP_SHORT_CUMULATIVE_7D_H):
            return {
                "severity": "strong_flag",
                "message": (
                    f"Kun {last_night_h:.1f}t i natt OG snitt < 6t siste 7 dager "
                    f"({cumulative_7d_h:.1f}t totalt). Søvngjeld akkumulert — "
                    "flytt dagens hardøkt, kjør bare easy eller hvile."
                ),
            }
        return {
            "severity": "soft_flag",
            "message": (
                f"Kun {last_night_h:.1f}t søvn i natt — dagens hardøkt er OK, "
                "men ikke press ekstra. Forvent høyere HR ved samme tempo."
            ),
        }
    return None
