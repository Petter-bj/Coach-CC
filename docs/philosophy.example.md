# Training philosophy — template

This file documents the sources and stances that drive the coaching rules
in `src/coaching/philosophy.py`. Keep your personal version **outside the
repo** at `~/Library/Application Support/Trening/docs/philosophy.md` — it
typically contains specific creator names, paid-product references, and
personal preferences that don't belong in a public reference impl.

The structure below is a starting point. Fill in the sections that matter
to you, delete the ones that don't.

---

## 0. Sources

| Source | Field | Platform | One-line stance |
|---|---|---|---|
| _coach name_ | strength / running / nutrition | where you follow them | core position |

Keep attributions tight — one line per source. Detail lives in the section
that cites them.

---

## 1. Strength

### 1.1 Volume per muscle group per week

Default range (e.g. `N–M` direct sets/week). What does "direct" vs
"indirect" mean in your framework? When do you flag over- or under-volume?

### 1.2 Proximity to failure (RIR)

Target RIR range for working sets. When is obligatory failure appropriate
(if ever)? What RIR pattern signals under-stimulus?

### 1.3 Frequency per muscle group

Default sessions/week per muscle. How does this interact with your weekly
split?

### 1.4 Progression model

Double progression? Linear? Block periodization? Auto-regulation on RPE?
Be specific — this drives `progression next` CLI output.

### 1.5 Exercise selection

Compound priority, machine vs free weight stance, number of exercises
per target muscle, tempo control.

### 1.6 Intensity techniques

Drop sets, supersets, myo-reps, rest-pause — do you use them and when?

### 1.7 Deload

Planned cadence, reactive triggers, or against deloads entirely?

### 1.8 e1RM tracking

Compound-only, all exercises, or not at all?

---

## 2. Running / endurance

### 2.1 Intensity zones

How many zones? Defined by HR, pace, RPE, lactate? What are the thresholds?

### 2.2 Weekly structure

Hard sessions per week, spacing rules, long run placement, cross-training
role.

### 2.3 Volume progression

10%-rule? Block-based? Signal-driven (morning HR, HRV, subjective)?

### 2.4 Intensity distribution

Polarized (80/20)? Pyramidal? Threshold-heavy? What's your stance on the
"grey zone" (classical threshold, ~4 mmol / ~90% HRmax)?

### 2.5 Long run philosophy

Max length per focus distance. Easy throughout or with race-pace segments?

### 2.6 Taper

Duration by race distance. Volume reduction pattern. Intensity during taper.

---

## 3. Strength ↔ endurance integration

How do they interact? Heavy lifts before hard runs — compatible or
incompatible? Plyometrics role? Priority shifts by season?

---

## 4. Fueling

### 4.1 During training

Carbs/hour targets by intensity and duration. Multi-transportable carbs?

### 4.2 Protein

g/kg targets for maintenance, build, cut phases.

### 4.3 Periodized nutrition

Train-low / train-high strategies.

---

## 5. Recovery and readiness

### 5.1 What signals do you monitor?

HRV, morning HR, sleep, subjective wellness, training readiness scores.

### 5.2 How do signals modulate sessions?

Hard gating vs soft warnings. Which signal gates what type of session?

### 5.3 Sleep thresholds

Single-night and cumulative-debt thresholds that trigger session modulation.

---

## 6. Injury protocols

Specific injuries you're prone to and the rules that go with them. Return
protocols after symptom-free periods.

---

## 7. Numeric thresholds

Collect the concrete constants here so they can be copied directly into
`src/coaching/philosophy.py`. Keep source attributions in prose above;
this section is just the numbers.

---

## 8. Known gaps

What you don't have strong sources for yet. Candidate sources to explore.

---

## 9. Revisions

Append a log entry when you change a rule. Update this file first, then
the code, then CLAUDE.md — never code-first, or you lose the traceability.
