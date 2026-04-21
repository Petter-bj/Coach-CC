# Ideas / future features

Tracked future ideas for the Trening system. Not committed scope — just
a dumping ground so nothing gets forgotten between implementation sprints.

Prioritised loosely, most valuable/interesting at the top.

---

## 1. Location-aware route planning

**Concept.** You ask Claude "plan me a 10k easy this evening, starting from
home" and it:

1. Resolves your location (static anchor, iOS Shortcut webhook, or
   CoreLocation via macOS)
2. Generates a matching loop via a routing API (BRouter, GraphHopper
   round-trip, or Mapbox)
3. Cross-references upcoming weather (temp, precip, wind) for the session window
4. Optionally pushes the route as a **Course** to your Garmin watch via
   `python-garminconnect` so it shows up natively on the watch
5. Returns the GPX + a readable summary

**MVP stack.**
- Static anchor in a new `user_settings` table
- BRouter or GraphHopper "round trip" endpoint (both free)
- OpenMeteo for weather forecast (free, no key)
- GPX to `~/Library/Caches/Trening/routes/` + maybe Garmin Course upload

**New tables.**
- `user_settings` — anchor lat/lng, preferred terrain, favourite loops
- `planned_routes` — generated rute + GPX path + was-pushed-to-watch flag

**CLI.**
- `src.cli.route plan --type easy --distance-km 10 [--push-garmin]`
- `src.cli.route list --range last_30d`

**Nice-to-have.**
- Claude learns patterns: "Mondays you prefer flat; Sundays you go into
  Bymarka" → surfaces as default preference
- After each run, reconcile: which % of the actual run matched the
  planned route? Adherence score for routes, parallel to plan adherence.

---

## 2. Weather correlation analysis

**Concept.** Track weather during every workout and correlate with
performance. Surface insights like:

- "You run 15 s/km slower when temp > 25 °C"
- "HR at the same pace is ~8 bpm higher in humid conditions"
- "You've only done 3 Z5 sessions in headwind > 8 m/s and RPE was 1
  point higher than similar sessions in calm"
- "Your best times are temp 8–15 °C, low wind — the past two weeks have
  been ideal"

This also makes the morning report smarter: *"Forecast 28 °C and humid
today — expect pace to be slower, consider moving the tempo run to
tomorrow morning"*.

**Implementation.**

1. **New table** `weather_observations`:
   ```
   workout_id FK (nullable — daily rows use local_date instead)
   local_date
   temp_c, feels_like_c, humidity_pct, precip_mm, wind_m_s, wind_dir_deg,
   cloud_cover_pct, pressure_hpa, source, fetched_at
   ```

2. **Fetch strategy**:
   - For existing workouts: lookup OpenMeteo archive API by lat/lng
     (need start-location from `garmin_activity_details` or FIT file)
   - For ongoing: fetch forecast for user anchor + refine at workout-end
   - For non-GPS workouts (indoor skierg, strength): skip or use outdoor
     weather as context anyway

3. **New analysis module** `src/analysis/weather_correlation.py`:
   - `correlate(metric='pace', filter_type='running')` → simple linear
     regression pace ~ temp + humidity + wind
   - Returns coefficients Claude can phrase naturally
   - Robust to small N (reports "insufficient data" if < 20 workouts)

4. **Extend `recovery.py`** to include weather in snapshot when relevant
   (morning report mentions expected conditions).

5. **New CLI**:
   - `src.cli.weather correlate --metric pace [--type running]`
   - `src.cli.weather show --workout-id N`

**Sync integration.**
- New stream in an OpenMeteo "source" class (no auth), runs once daily
  for anchor + backfills per-workout on-demand
- Or simpler: backfill as part of Garmin activity sync — when a new
  activity comes in with GPS, fetch weather for that point/time

**Decisions still to make.**
- Store per-workout snapshot (one row) or hourly sampling?
- For indoor workouts: skip or store outdoor anyway (could still matter
  — barometric pressure affects sleep/HRV)

---

## 3. Garmin Course push integration

If #1 is built, the real magic is auto-pushing the route to the watch:

```python
client.create_course(name, gpx_bytes)
```

(Needs to verify the method exists in current garminconnect — if not, use
the download URL `courses/create` via the library session.)

User experience:
```
User: "plan me a 6k tempo starting from home"
Claude: [builds route] [fetches weather] [pushes to Garmin as Course]
        "Pushed 6.1 km tempo loop to Garmin — sync your watch, Follow
         Course on next run. Temp is 12°C with light wind, good conditions."
```

Zero GPX fiddling, zero import clicks.

---

## 4. Sleep-quality fine-tuning

Garmin sleep score is a black box. Could layer our own analysis on top:
- Bedtime consistency (std dev of sleep-start time over 30d)
- Cumulative sleep debt (7d rolling vs. personal average)
- Chronotype awareness (morning vs. evening person — derived from HR/HRV
  recovery patterns across the day)

---

## 5. 1RM projection + progressive overload coach

Based on e1RM trajectory per exercise, estimate when each lift hits a
goal. E.g. "Bench 100kg goal: projected 2026-09 at current +0.4kg/week
trend". Warn when trajectory flattens (plateau → deload? volume change?).

---

## 6. Alcohol + HRV correlation

Already logging via `intake_log`. Concept: regression of
`hrv_delta_vs_baseline ~ alcohol_units` with 12-24-48h lag. Should be
able to say: "1 beer drops HRV by 3ms next night; 4+ beers drops it
8-12ms and takes 2 days to return to baseline".

---

## 7. Automatic menstrual cycle tracking (not applicable here but)

Skipped for this user. Would be valuable if this were ever generalised.

---

## 8. iPhone Shortcut → push-from-phone logging

- Voice: "Hey Siri, log 2 beers tonight"
- Shortcut calls a local webhook that runs `intake log --alcohol 2`
- Same for wellness: "Hey Siri, feeling tired today, motivation 4"

Needs a local HTTP endpoint listening on home network — maybe Ngrok or
iPhone hotspot. Or a Telegram bot message triggered by Shortcut
(simpler, no infrastructure).

---

## 9. Cold-start onboarding + partial-adoption README

Two related angles for making the repo useful to people who don't have
all four data sources.

**9a. Interactive onboarding CLI.**
When someone else clones the repo, the first-time setup is painful
(4 spike scripts, OAuth dances, env vars). Could build an interactive
`src.cli.onboard` that walks through each step and verifies as it goes:
- Prompts for each service the user wants to enable
- Runs the matching spike, handles MFA interactively
- Verifies credentials landed in the right place
- Gives clear "skip this step" option for services the user doesn't use

**9b. "Recommended stack" section in README.**
Today the README assumes you have all four data sources + Hevy Pro +
Claude Max. That's a lot of commitment for someone just browsing.

Add a tiered stack guide showing how the system degrades gracefully:

| Level | Cost | What you get |
|---|---|---|
| **Minimum viable** | Free (Garmin watch + Claude Code) | HRV, sleep, readiness, workouts + morning reports |
| **+ Withings smart scale** | ~$100 one-time for scale | Weight trends + body composition baselines |
| **+ Concept2 SkiErg/rower** | Hardware-dependent | Stroke-level erg data for intervals |
| **+ Yazio (free tier)** | Free | Daily kcal + macro tracking, NO food database |
| **+ Hevy Pro** | $2.99/mo–$75 lifetime | Structured strength logging with MCP bidirectional control |
| **+ Claude Max** | $20/mo | Runs the Telegram coaching layer on top of all of it |

Each source class (`src/sources/*`) already handles being skipped —
the existing `source_stream_state` logic tracks enabled streams
independently. So "minimum viable" is literally just enabling Garmin in
`sync.py` `SOURCES = [GarminSource]` and skipping the others.

The README should make this explicit so a reader sees "I can get real
value from just my Garmin watch" rather than "I need 6 accounts and
Node.js v24 before this works".

---

## 10. Scheduled sync source for Hevy → local SQLite

**Status.** Hevy MCP (bidirectional chat integration) was shipped
2026-04-21 — Claude can read/write to Hevy during conversations via
[chrisdoc/hevy-mcp](https://github.com/chrisdoc/hevy-mcp). Setup is
documented in README step 7.

**What's still missing.** The MCP is interactive-only — it runs when
Claude wants a tool, not in the background. To get Hevy workouts into
the local SQLite for baselines, PRs, volume reports, and dedupe against
Garmin `indoor_rowing`/Concept2, we need a proper source class that
runs in the hourly sync.

**Implementation sketch.**

1. New source class `src/sources/hevy.py`:
   - Stream `workouts` pulls recent sessions via REST
     (https://api.hevyapp.com/v1/workouts)
   - Stream `events` uses `/workouts/events` for incremental fetch
     (newer than a cursor, including deletions)
   - Maps Hevy exercise IDs → canonical names in `exercise_muscles.json`
     (new table: `hevy_exercise_map(hevy_id, canonical_name)`)
   - Inserts into `workouts` + `strength_sessions` + `strength_sets`
     — same tables as xlsx import and screenshot flow

2. PR-sanity-check still runs — API can mis-send just as vision can
   misread (fat-finger weight).

3. Exercise-name mapping: build it incrementally on first import.
   Prompt user via Telegram: "Hevy exercise 'Bench Press Dumbbell' →
   map to our canonical `dumbbell_bench_press`?"

4. Dedupe with existing sources: if a Hevy workout overlaps with a
   Garmin `strength_training` activity on the same day, mark the Garmin
   row `superseded_by = hevy_workout_id`. Same logic as current
   Garmin↔Concept2 reconcile.

**Hybrid flow.**
- Hevy source handles the common case: "hour after gym, sync picks up
  session, e1RM updated, no user interaction"
- Screenshot flow reserved for exceptions (travel gym with unfamiliar
  app, handwritten log on paper)
- Both write to the same canonical `workouts`/`strength_sessions` tables

**Prerequisite.** User should log ~2 weeks in Hevy first to validate
the app fits, before investing in building the sync source.

---

## 11. Independent Telegram alerter (decoupled from Claude Code)

Small Python script + launchd plist that polls `alerts` table every 5
min and sends plain Telegram messages via Bot API when unacknowledged
alerts exist. Works even when Claude Code session is down.

Today Claude Code surfaces alerts when you ask — silent otherwise.
