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

## 9. Cold-start onboarding

When someone else clones the repo, the first-time setup is painful
(4 spike scripts, OAuth dances, env vars). Could build an interactive
`src.cli.onboard` that walks through each step and verifies as it goes.
Out of scope for personal use but would make the repo more useful as
reference.

---

## 10. Replace strength screenshot flow with a direct API integration

**Concept.** Today's flow: take screenshot in the strength-logging app →
send to Telegram → Claude vision parses → confirm → `strength log`. It
works, but is lossy (OCR can misread weights), slow (vision + chat
round-trip), and requires manual confirmation on every session.

An API-based integration would:
- Eliminate OCR mistakes (structured data, not pixels)
- Skip the confirmation step for trusted data
- Unlock richer fields that aren't in a typical screenshot (rest
  between sets, set-level timestamps, exercise variant/grip, RIR)
- Run automatically as part of the hourly sync

**Candidate strength-tracking apps.**

| App | API availability | Notes |
|---|---|---|
| **Hevy** | Official API (Pro tier) | Clean REST endpoints; `/workouts`, `/exercises` — proven integrations. Most likely target. |
| **HeavySet** (current app) | No public API | iOS-only; user currently uses this, but is open to switching |
| **Strong** | No public API | iCloud-only sync; could reverse-engineer CloudKit but fragile |
| **FitNotes** | No API | Android-only; CSV export works for batch |
| **MacroFactor Lift** | No API yet | Stronger by Science's companion app; might come eventually |
| **Liftin** / **Boostcamp** / **SetForge** | Varying | Need to survey |

User is not loyal to HeavySet — switching to Hevy is on the table if the
API integration saves enough friction to be worth re-learning an app.

**Implementation sketch (assuming Hevy or similar).**

1. New source class `src/sources/hevy.py` (or whichever app wins):
   - Stream `workouts` pulls recent sessions via REST
   - Maps Hevy exercise IDs to our `exercise_muscles.json` canonical
     names (new mapping table needed — `strength_app_exercise_map`)
   - Direct insert into `workouts` + `strength_sessions` + `strength_sets`
     (bypasses the `strength_sessions_pending` table entirely)

2. Keep screenshot flow as **fallback** for:
   - Old sessions imported from xlsx or handwritten logs
   - Days where the user forgot to log digitally
   - Non-supported apps for anyone using the repo

3. PR-sanity-check still runs — API can mis-send just as vision can
   mis-parse (fat-finger weight). Keep the `--force-pr` path.

4. Exercise-name mapping is the hardest part. Hevy's exercise IDs are
   stable per user, so mapping can be built incrementally: on first
   import of each exercise, prompt user "Hevy 'Bench Press Dumbbell' →
   map to our `dumbbell_bench_press`?"

**Decision still to make.**
- Does the user want to migrate fully to Hevy (or whichever app) or keep
  using the current combo of screenshot + xlsx import?
- Free tier of most APIs is limited — Hevy Pro is ~$5/mo, manageable

**Hybrid flow (probably the right answer).**
- API handles the common case: "hour after gym, sync picks up session,
  e1RM updated, no user interaction"
- Screenshot flow reserved for exceptions (travel gym with unfamiliar
  app, handwritten log on paper)
- Both write to the same canonical `workouts`/`strength_sessions` tables

**Bonus: add Hevy MCP for bidirectional chat integration.**

[chrisdoc/hevy-mcp](https://github.com/chrisdoc/hevy-mcp) exposes 18 Hevy
API tools via Model Context Protocol — complementary to the scheduled
sync source:

- 🔄 **Scheduled sync** (our `src/sources/hevy.py`): Pull workouts into
  SQLite every hour for reports, baselines, PRs
- 💬 **MCP** (chrisdoc/hevy-mcp): Let Claude **write** to Hevy from chat
  — create/update routines, move exercises, scale weights, set up next
  week's plan. Bidirectional where the source class is read-only.

Example interactions the MCP unlocks:
- "I'm tired today, replace tomorrow's heavy deadlift with a Z2 skierg"
  → Claude modifies the routine in Hevy; it shows up on phone/watch
- "Add 2.5kg to bench next push day" → Claude updates the routine
  template so the progression sticks
- "Build me a 6-week hypertrophy block focused on chest and back" →
  Claude generates routines and organizes them in a folder

Install is a couple of lines in `~/.claude/settings.json` under
`mcpServers`. Requires same Hevy Pro key as the sync source class, so
no extra cost.

**Recommended build order once Hevy Pro is active:**
1. Install chrisdoc/hevy-mcp first — immediate interactive value
2. Log a couple of weeks of workouts to validate Hevy as the app
3. Then build `src/sources/hevy.py` for scheduled sync → baselines/PRs

---

## 11. Independent Telegram alerter (decoupled from Claude Code)

Small Python script + launchd plist that polls `alerts` table every 5
min and sends plain Telegram messages via Bot API when unacknowledged
alerts exist. Works even when Claude Code session is down.

Today Claude Code surfaces alerts when you ask — silent otherwise.
