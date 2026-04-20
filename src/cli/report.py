"""`report morning` og `report weekly` — kombinerte rapporter.

Disse er ikke regler-for-regler-kopier av de andre CLI-ene; de plukker ut
det som betyr noe for en morgen- eller ukes-oppsummering og samler det i
ett strukturert svar.
"""

from __future__ import annotations

from datetime import date, timedelta

import typer

from src.analysis.recovery import recovery_snapshot
from src.cli._common import emit, parse_range
from src.db.connection import connect

app = typer.Typer(add_completion=False, no_args_is_help=True)


RECOMMENDATION_LABELS = {
    "rest": "HVIL",
    "light": "LETT (deload)",
    "easy": "ROLIG",
    "normal": "NORMAL",
    "intensive": "INTENSIV",
}


# ===========================================================================
# MORGENRAPPORT
# ===========================================================================


def _morning_data(target_date: date) -> dict:
    with connect() as c:
        snap = recovery_snapshot(c, target_date)
        # Siste økt (ekskl. superseded)
        last = c.execute(
            """
            SELECT local_date, source, type, duration_sec, distance_m, avg_hr, rpe
              FROM workouts
             WHERE superseded_by IS NULL AND local_date <= ?
             ORDER BY started_at_utc DESC LIMIT 1
            """, (target_date.isoformat(),)
        ).fetchone()
        # Dagens plan
        planned = [dict(r) for r in c.execute(
            "SELECT * FROM planned_sessions WHERE planned_date = ? AND status = 'planned'",
            (target_date.isoformat(),),
        ).fetchall()]
        # Aktiv blokk + topp-mål
        block = c.execute(
            """
            SELECT b.name, b.phase, b.start_date, b.end_date,
                   g.title AS goal_title
              FROM training_blocks b
              LEFT JOIN goals g ON b.primary_goal_id = g.id
             WHERE b.start_date <= ? AND b.end_date >= ?
             ORDER BY b.start_date DESC LIMIT 1
            """,
            (target_date.isoformat(), target_date.isoformat()),
        ).fetchone()
        top_goals = [dict(r) for r in c.execute(
            "SELECT title, target_date, metric, target_value, priority FROM goals "
            "WHERE status = 'active' ORDER BY priority LIMIT 3"
        ).fetchall()]

    return {
        "date": target_date.isoformat(),
        "recovery": snap,
        "last_workout": dict(last) if last else None,
        "planned_today": planned,
        "active_block": dict(block) if block else None,
        "top_goals": top_goals,
    }


def _format_morning(d: dict) -> str:
    snap = d["recovery"]
    out: list[str] = [f"# Morgenrapport — {d['date']}", ""]

    # Søvn
    dur = snap["sleep_duration_hours"]
    sscore = snap["sleep_score"]
    if dur:
        line = f"🌙 Søvn: {dur}t"
        if sscore["value"] is not None:
            line += f", score {sscore['value']}"
            if sscore["baseline"] is not None:
                delta = sscore["delta"]
                sign = "+" if delta >= 0 else ""
                line += f" (baseline {sscore['baseline']}, {sign}{delta})"
        out.append(line)
    else:
        out.append("🌙 Ingen søvn-data ennå")

    # HRV + RHR
    hrv = snap["hrv"]
    rhr = snap["resting_hr"]
    if hrv["value"]:
        line = f"❤  HRV {hrv['value']}ms"
        if hrv["baseline"]:
            sign = "+" if hrv["delta"] >= 0 else ""
            line += f" (baseline {hrv['baseline']}, {sign}{hrv['delta']})"
        out.append(line)
    if rhr["value"]:
        line = f"   RHR {rhr['value']} bpm"
        if rhr["baseline"]:
            sign = "+" if rhr["delta"] >= 0 else ""
            line += f" (baseline {rhr['baseline']}, {sign}{rhr['delta']})"
        out.append(line)

    # Readiness + last + chronic load
    rd = snap["readiness"]
    if rd["garmin_score"]:
        line = (f"💪 Training readiness {rd['garmin_score']} "
                f"({rd['garmin_level']})")
        if rd["vs_baseline"]["baseline"]:
            sign = "+" if rd["vs_baseline"]["delta"] >= 0 else ""
            line += f" — baseline {rd['vs_baseline']['baseline']}, {sign}{rd['vs_baseline']['delta']}"
        out.append(line)

    load = snap["load"]
    if load["acr"] is not None:
        zone_labels = {
            "sweet": "sweet spot",
            "elevated": "over sweet spot",
            "risk": "høy risiko",
            "undertraining": "undertrening",
            "insufficient": "for lite data",
        }
        out.append(
            f"📊 Load: acute {load['acute_7d']:.0f}, chronic {load['chronic_28d_weekly']:.0f}  "
            f"ACR {load['acr']:.2f} ({zone_labels.get(load['zone'], load['zone'])})"
        )
    else:
        out.append(f"📊 Load: {load['workouts_counted']} økter — for lite data for ACR")

    if load["workouts_without_rpe"]:
        out.append(
            f"   ⚠ {load['workouts_without_rpe']} økter uten RPE — estimat brukt (sett RPE via `rpe set`)"
        )

    # Kontekst
    if snap["active_injuries"]:
        out.append("")
        out.append("🩹 Aktive skader:")
        for inj in snap["active_injuries"]:
            out.append(f"   {inj['body_part']} (sev {inj['severity']}, {inj['status']})")

    if snap["active_contexts"]:
        out.append("")
        out.append("📌 Kontekst:")
        for ctx in snap["active_contexts"]:
            notes = f": {ctx['notes']}" if ctx["notes"] else ""
            out.append(f"   {ctx['category']} ({ctx['starts_on']} → "
                       f"{ctx['ends_on'] or 'pågående'}){notes}")

    # Aktiv blokk + mål
    out.append("")
    block = d["active_block"]
    if block:
        goal = f" → {block['goal_title']}" if block["goal_title"] else ""
        out.append(f"🎯 Blokk: {block['name']} ({block['phase']}) "
                   f"{block['start_date']} → {block['end_date']}{goal}")
    goals = d["top_goals"]
    if goals:
        out.append("   Aktive mål:")
        for g in goals[:3]:
            tgt = f" — {g['target_value']} {g['metric']}" if g["metric"] else ""
            deadline = f" innen {g['target_date']}" if g["target_date"] else ""
            out.append(f"     [{g['priority']}] {g['title']}{tgt}{deadline}")

    # Siste økt
    last = d["last_workout"]
    if last:
        dur_min = (last["duration_sec"] or 0) / 60
        out.append("")
        out.append(f"🏃 Siste økt: {last['local_date']} — {last['type']} "
                   f"({dur_min:.0f} min)"
                   + (f", HR {last['avg_hr']}" if last["avg_hr"] else ""))

    # Planlagt i dag
    if d["planned_today"]:
        out.append("")
        out.append(f"📅 Planlagt i dag:")
        for p in d["planned_today"]:
            out.append(f"   {(p['type'] or '—'):15} {p['description'] or ''}")

    # Anbefaling
    out.append("")
    out.append("─" * 60)
    out.append(f"## Anbefaling: {RECOMMENDATION_LABELS.get(snap['recommendation'], snap['recommendation'].upper())}")
    for reason in snap["rationale"]:
        out.append(f"  • {reason}")

    return "\n".join(out) + "\n"


# ===========================================================================
# UKESRAPPORT
# ===========================================================================


def _weekly_data(week_of: str) -> dict:
    r = parse_range(f"week_of={week_of}")
    with connect() as c:
        # Workouts i uka (ekskl. superseded)
        workouts = [dict(w) for w in c.execute(
            """
            SELECT w.id, w.local_date, w.source, w.type, w.duration_sec,
                   w.distance_m, w.avg_hr, w.rpe, w.session_load, w.notes
              FROM workouts w
             WHERE w.superseded_by IS NULL
               AND w.local_date BETWEEN ? AND ?
             ORDER BY w.started_at_utc
            """, (r.start, r.end)
        ).fetchall()]

        # Søvn
        sleep_rows = [dict(x) for x in c.execute(
            "SELECT local_date, sleep_score, duration_sec, sleep_score_qualifier "
            "FROM garmin_sleep WHERE local_date BETWEEN ? AND ? "
            "AND duration_sec > 0 ORDER BY local_date", (r.start, r.end)
        ).fetchall()]

        # Vekt (første veiing per dag)
        weight_rows = [dict(x) for x in c.execute(
            """
            SELECT w.local_date,
                   (SELECT weight_kg FROM withings_weight w2
                     WHERE w2.local_date = w.local_date
                     ORDER BY measured_at_utc ASC LIMIT 1) AS weight_kg
              FROM (SELECT DISTINCT local_date FROM withings_weight
                     WHERE local_date BETWEEN ? AND ?) w
             ORDER BY local_date
            """, (r.start, r.end)
        ).fetchall()]

        # Kosthold
        nutr_rows = [dict(x) for x in c.execute(
            "SELECT local_date, kcal, protein_g, carbs_g, fat_g FROM yazio_daily "
            "WHERE local_date BETWEEN ? AND ? ORDER BY local_date",
            (r.start, r.end)
        ).fetchall()]

        # Plan-adherence
        planned = c.execute(
            "SELECT status, COUNT(*) AS n FROM planned_sessions "
            "WHERE planned_date BETWEEN ? AND ? GROUP BY status",
            (r.start, r.end)
        ).fetchall()
        plan_counts = {row["status"]: row["n"] for row in planned}

        # Volum per muskelgruppe (kun i uka)
        from src.analysis.exercises import lookup
        from collections import defaultdict
        sets = [dict(x) for x in c.execute(
            """
            SELECT s.exercise, s.reps, s.weight_kg
              FROM strength_sets s
              JOIN strength_sessions ss ON s.session_id = ss.id
              JOIN workouts w ON ss.workout_id = w.id
             WHERE w.local_date BETWEEN ? AND ?
            """, (r.start, r.end)
        ).fetchall()]
        volume: dict[str, float] = defaultdict(float)
        for s in sets:
            info = lookup(s["exercise"])
            if info["unknown"]:
                continue
            vol = (s["reps"] or 0) * (s["weight_kg"] or 0)
            volume[info["primary"]] += vol
            for sec in info["secondary"]:
                volume[sec] += vol * 0.5

    return {
        "week_of": r.label,
        "start": r.start,
        "end": r.end,
        "workouts": workouts,
        "sleep_rows": sleep_rows,
        "weight_rows": weight_rows,
        "nutrition_rows": nutr_rows,
        "plan_counts": plan_counts,
        "muscle_volume": dict(volume),
        "total_sets": len(sets),
    }


def _format_weekly(d: dict) -> str:
    out = [f"# Ukesrapport {d['start']} → {d['end']}", ""]

    # Trening
    total_sec = sum(w["duration_sec"] or 0 for w in d["workouts"])
    by_type: dict[str, int] = {}
    for w in d["workouts"]:
        t = w["type"] or "unknown"
        by_type[t] = by_type.get(t, 0) + 1
    out.append(f"## Trening — {len(d['workouts'])} økter, {total_sec/3600:.1f}t totalt")
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        out.append(f"  {t}: {n}")

    if d["muscle_volume"]:
        out.append("")
        out.append("### Volum per muskelgruppe (kg totalt)")
        sorted_vol = sorted(d["muscle_volume"].items(), key=lambda x: -x[1])
        for muscle, vol in sorted_vol:
            out.append(f"  {muscle:<14} {vol:>6.0f}")
        out.append(f"  ({d['total_sets']} sett logget)")

    # Planadherance
    planned = sum(d["plan_counts"].values())
    if planned:
        completed = d["plan_counts"].get("completed", 0)
        pct = 100 * completed / planned
        out.append("")
        out.append(f"## Planadherance: {pct:.0f}% ({completed}/{planned})")

    # Søvn
    if d["sleep_rows"]:
        avg_score = sum(x["sleep_score"] or 0 for x in d["sleep_rows"] if x["sleep_score"]) \
                    / max(1, sum(1 for x in d["sleep_rows"] if x["sleep_score"]))
        avg_hours = sum(x["duration_sec"] or 0 for x in d["sleep_rows"]) / len(d["sleep_rows"]) / 3600
        out.append("")
        out.append(f"## Søvn — {len(d['sleep_rows'])} netter, snitt {avg_hours:.1f}t (score {avg_score:.0f})")

    # Vekt
    if d["weight_rows"]:
        weights = [x["weight_kg"] for x in d["weight_rows"] if x["weight_kg"]]
        out.append("")
        if len(weights) >= 2:
            delta = weights[-1] - weights[0]
            sign = "+" if delta >= 0 else ""
            out.append(f"## Vekt — {len(weights)} målinger, {weights[0]:.1f}kg → {weights[-1]:.1f}kg ({sign}{delta:.2f})")
        else:
            out.append(f"## Vekt — {weights[0]:.1f}kg (kun 1 måling)")

    # Kosthold
    logged = [x for x in d["nutrition_rows"] if (x["kcal"] or 0) > 0]
    if logged:
        avg_kcal = sum(x["kcal"] for x in logged) / len(logged)
        avg_p = sum(x["protein_g"] for x in logged) / len(logged)
        out.append("")
        out.append(f"## Kosthold — {len(logged)}/7 dager logget")
        out.append(f"  Snitt: {avg_kcal:.0f} kcal, P={avg_p:.0f}g")

    return "\n".join(out) + "\n"


# ===========================================================================
# CLI
# ===========================================================================


@app.command()
def morning(
    date_: str = typer.Option(None, "--date", help="YYYY-MM-DD (default: i dag)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Morgenrapport: søvn, HRV, readiness, load, anbefaling."""
    target = date.fromisoformat(date_) if date_ else date.today()
    data = _morning_data(target)
    emit(data, as_json=json_output, text=_format_morning(data))


@app.command()
def weekly(
    week_of: str = typer.Option(None, "--week-of", help="YYYY-MM-DD (default: denne uken)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Ukesrapport: trening, volum, søvn, vekt, kosthold."""
    anchor = week_of or date.today().isoformat()
    data = _weekly_data(anchor)
    emit(data, as_json=json_output, text=_format_weekly(data))


if __name__ == "__main__":
    app()
