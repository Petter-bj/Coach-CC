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

## 10. Scheduled sync source for Hevy → local SQLite ✅ Done 2026-04-21

**Shipped** — `src/sources/hevy.py` fetches Hevy workouts via
`GET /v1/workouts`, 180-day backfill on first run, idempotent on re-run
(delete-and-insert pattern for sets, mirroring concept2_intervals).
Workouts land in canonical `workouts` + `strength_sessions` +
`strength_sets`, so baselines, PRs, volume reports, and ACR all reflect
Hevy training.

`HEVY_API_KEY` lives in `credentials/.env`; same key MCP uses.
`src/sync.py` loads `.env` via python-dotenv at start (also fixes the
lurking issue that Yazio refresh would have failed under launchd).

Migration 002 drops the `workouts.source` CHECK constraint so future
sources don't require a table rebuild.

Reconcile extended: source='strength' (xlsx import + chat-screenshot
logs) gets `superseded_by` pointed at matching Hevy workout within
±1 hour. Hevy is canonical going forward.

**Still open** — exercise-name normalization. Hevy titles ("Shoulder
Press (Dumbbell)") are stored as-is in `strength_sets.exercise`. For
`prs`/baselines to group correctly across free-text variations, build
an `exercise_aliases` table if needed. Defer until baselines show noise.

---

## 11. Independent Telegram alerter (decoupled from Claude Code)

Small Python script + launchd plist that polls `alerts` table every 5
min and sends plain Telegram messages via Bot API when unacknowledged
alerts exist. Works even when Claude Code session is down.

Today Claude Code surfaces alerts when you ask — silent otherwise.

---

## 12. Proactive coaching — outbound messages on schedule / on event

**The gap.** Today the bot is fully reactive — it only responds when you
message it. A real coach sends "how was the session?" after a workout,
"HRV looks low, sleep OK?" at breakfast, "did you skip today's run?" at
18:00, without you asking. Our system has all the data to do this but
no mechanism to act on it.

**Trigger taxonomy.**

*Time-based:*
- 07:00 daily → morning check-in ("sleep OK? log wellness before I give
  you today's readiness")
- 20:00 Sunday → weekly review push
- 18:00 daily → "planned workout today, you haven't logged anything
  yet — still happening?"

*Data-driven (runs after each hourly sync):*
- New Garmin activity with no RPE after 2h → "how hard was that?"
- HRV < baseline × 0.9 → "HRV a bit low — alcohol, stress, sickness?"
- Sleep score < 60 → "rough night, consider moving the intensive
  session to tomorrow"
- ACR crossed 1.5 → "your load is spiking, recommend deload next week"
- 3+ consecutive days below protein goal → "protein is trending low,
  adjust meals?"
- Same injury active > 14 days → "knee still sore after 2 weeks —
  should we flag this to a physio?"

*Anti-spam guardrails:*
- Max 3 messages / day
- Don't nag the same thing twice within 48h
- Quiet hours (no messages 22:00–06:00)
- Pause all proactive messages during active `context_log` (travel,
  illness — already stressed enough)

**Architecture options.**

### Option A: Template-based (simplest, ~1 day to build)

```
launchd (every 30 min)
   ↓
python -m src.proactive.check
   ↓
Evaluates triggers against current DB state
   ↓
For each fired trigger:
   - Load pre-written message template
   - Fill in data (actual HRV ms, baseline value, etc.)
   - Send via Telegram Bot API directly
   - Log in proactive_messages table (dedupe, rate-limit)
```

- Pro: Cheap, no AI calls, predictable
- Con: Messages feel canned after a week

### Option B: Claude-generated messages (natural coach voice)

Same trigger logic, but when a trigger fires the script calls Claude API
(not the Max subscription — a separate API key) with trigger context
and asks it to phrase the message naturally. Then sends via Telegram.

- Pro: Feels genuinely conversational
- Con: API costs (~$0.01-0.05/message), separate billing from Max
- Mitigation: batch trigger evaluations to minimize calls

### Option C: Trigger → inject to running Claude Code session

Harder — Claude Code plan mode / channels don't obviously support
external pushes. Would require deeper Claude Code integration.

Probably overkill. Option A → B evolution is cleaner.

**New schema.**

```sql
CREATE TABLE proactive_triggers (
    id, name, schedule_cron or event_type,
    enabled, quiet_hours_start, quiet_hours_end, ...
);

CREATE TABLE proactive_messages (
    id, trigger_id, sent_at, payload,
    user_reacted_at  -- did user reply in Telegram?
);
```

**Metrics to track.**
- Response rate per trigger type (are these useful or noise?)
- User reply latency (did they engage quickly or ignore?)
- Did the trigger correlate with actual behavior change
  (e.g. did the "load spike" warning lead to a deload in the next 7d?)

Without that feedback loop, a proactive system becomes spam. With it,
the coach gets better at knowing when YOU want to hear from it.

**Dependencies on other IDEAS.**
- Idea #11 (independent Telegram alerter) is a prerequisite — same
  outbound-messaging infrastructure, just rule-based
- Eventually both become "one proactive service, many triggers"
