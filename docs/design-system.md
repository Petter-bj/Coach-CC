# Trening — Design System

Locked from `Trening - I dag.html`. Reference for all subsequent pages (Week, Month, Logbook, Calendar, etc).

> **Aesthetic north star**: warm dark mode, papir-aktig, monokrom dominant. Things 3 / Bear / iA Writer / Procreate — *not* Bloomberg, *not* gaming, *not* AI-default.

---

## 1. Color tokens

### Surfaces
| Token | Hex | Use |
|---|---|---|
| `--bg`     | `#100E0C` | Page background. Warm near-black. |
| `--bg-1`   | `#1A1815` | Card background. ~2–3 tones above page. |
| `--bg-2`   | `#221F1B` | Active surface, hover, pressed input. |
| `--line`   | `rgba(245,241,234,0.06)` | Default hairline (card border, dividers). |
| `--line-2` | `rgba(245,241,234,0.10)` | Stronger hairline (input border, button outline). |

### Text
| Token | Hex | Use |
|---|---|---|
| `--ink`   | `#F5F1EA` | Primary — headings, hero numbers, primary button label. |
| `--ink-2` | `#A8A099` | Secondary — body, sub-labels, metric units. |
| `--mute`  | `#6B6660` | Tertiary — eyebrows, hint text, axis labels. |
| `--dim`   | `#4A4540` | Disabled, very-dim placeholder. |
| `--vdim`  | `#2E2A26` | Inactive sparkline / chart skeleton. |

### Accents — sparing (max 4–5 per page)

**Sage `#8FA779`** (`--sage`, soft `rgba(143,167,121,0.14)`)
- Sync-OK indicator dot (header)
- "Klar?"-status dot when ready (readiness banner)
- Z2 / target-zone fill in HR distribution bar
- Trend ↑ arrows for **good** changes only (HRV up, sleep quality up, recovery up)

Forbidden for sage:
- ❌ Buttons / CTAs (use cream `--ink`)
- ❌ Decorative tags ("Aerob base" stays neutral border-only)
- ❌ Sparklines / chart fills (always cream/grey)
- ❌ Tab underline (cream)
- ❌ ↓ arrows — even when semantically positive (e.g. RHR drop), down arrows stay `--mute`

**Rust `#B5654A`** (`--rust`, soft `rgba(181,101,74,0.10)`, mid `rgba(181,101,74,0.32)`)
- "Klar?"-dot when **not** ready (caution or stop)
- Readiness-stop banner (left rule + soft gradient bg)
- Active injury indicator
- Sync-fail state
- HRV drop > 10% vs baseline

Rust must be rare. A normal "good morning" page shows zero rust.

### Tokens that map to states (monochrome)

```
--go      = var(--ink-2)   /* "ready" — same as text */
--caution = var(--mute)
--stop    = var(--rust)
```

---

## 2. Typography

### Stacks
- **Sans (prose, labels, headings)**: `'Geist', ui-sans-serif, system-ui, sans-serif`
- **Mono (tabular numbers only)**: `'Geist Mono', ui-monospace, SFMono-Regular, monospace` + `font-variant-numeric: tabular-nums`

Mono is reserved for: HR values, kg, km, kcal, percentages, time-of-day, durations, deltas. Never for prose.

### Scale
| Role | Size | Weight | Family | Notes |
|---|---|---|---|---|
| Hero (page H1, "Good morning") | 40px | 500 | Sans | `letter-spacing: -0.02em` |
| Section headline (card H2, e.g. "Z2 Easy Run") | 28px | 500 | Sans | `letter-spacing: -0.015em` |
| Card title / subhead | 16–17px | 500 | Sans | mixed case, never all-caps |
| Body | 13.5px | 400 | Sans | line-height ~1.55 |
| Body small / secondary | 13px | 400 | Sans | `--ink-2` |
| Eyebrow / label | 11.5px | 500 | Sans | `--mute`, `letter-spacing: 0.01em`, mixed case |
| Mono stat (large) | 32–48px | 500 | Mono | `tnum`, `letter-spacing: -0.02em` |
| Mono stat (inline) | 12–13px | 400 | Mono | `tnum` |
| Delta arrow | 11.5px | 400 | Mono | `tnum` |

Rules:
- Mixed case everywhere. No `text-transform: uppercase`.
- Never use mono for headings or prose, only for numerals + units.

---

## 3. Spacing

Tailwind tokens used in Today, ordered by frequency:
- `gap-2` (8px), `gap-2.5` (10px), `gap-3` (12px) — inline rows, dot+label pairs
- `gap-4` (16px), `gap-5` (20px) — between metric tiles
- `gap-6` (24px), `gap-7` (28px) — major section gaps
- `p-5` / `p-6` (20–24px) — card body padding
- `px-7 / px-9` (28 / 36px) — page horizontal padding
- `py-12` (48px) — page top padding ("hero" breathing room)
- `mt-7 / mt-12` (28 / 48px) — between major sections (1.5× vs original)
- `space-y-2.5 / space-y-3` — list-style stacks (HR zones, sync log)

Vertical rhythm rule of thumb: card padding 22px, card-head/foot 14px × 20px, sections separated by 28–48px.

---

## 4. Components

### Card

```html
<section class="card-soft">
  <div class="card-head">
    <span class="eyebrow">Eyebrow</span>
  </div>
  <div class="card-body"><!-- content --></div>
  <div class="card-foot"><!-- optional --></div>
</section>
```

- `border-radius: 10px`
- `border: 1px solid var(--line)`
- `background: var(--bg-1)` (or soft gradient via `.card-soft`)
- `card-head` / `card-foot` separated by `1px solid var(--line)`, padding `14px 20px`
- `card-body` padding `22px`

### Stat tile (label + value + sparkline + delta)
Vertical stack inside a hairline-divided cell (no card chrome — just `border-right: 1px solid var(--line)` between siblings).

```
┌──────────────────────────┐
│ HRV — i natt    (eyebrow, --mute, 11.5px)
│ 58 ms           (--ink, 32px mono tnum)
│ ↑+3% vs baseline 56  (delta sage if up-good, mute otherwise; 11.5px)
│ ┄┄┄ sparkline (cream stroke 1px, area-fill cream 0–18%)
│ 7d range 51–62  (--mute, 11.5px mono)
└──────────────────────────┘
```

Sparkline: smooth Bezier (`Q` curves), stroke `var(--ink)` opacity 0.55, area fill `var(--ink)` opacity 0.04. Never sage.

### Status indicator
Three forms in priority order:

1. **Dot** (`<span class="dot dot-sage|dot-rust|dot-mute">`) — 7px circle. Use only for binary states that need at-a-glance recognition (sync, readiness).
2. **Hairline** (`<span class="hair">`) — 14px × 1px ink-2 line. Use as a typographic "connector" before italic state text.
3. **Italic state text** (`<span class="state-text">`) — `font-style: italic; color: var(--ink-2)`. Default for soft states like "lagret 14:32", "synket 19:44", "ikke logget i dag".

### Tab nav

```html
<button class="tab tab-active">I dag</button>
<button class="tab">Uke</button>
```

- Padding `10px 14px`, font 13px sans, weight 500
- Active: `color: var(--ink); box-shadow: inset 0 -1px 0 var(--ink);`
- Inactive: `color: var(--mute)`, hover → `var(--ink-2)`
- Underline always cream — never sage, never rust.

### Buttons

**Primary** — filled cream

```html
<button class="btn btn-primary">Marker fullført</button>
```

- `background: var(--ink)`, `color: var(--bg)`, `border: 1px solid var(--ink)`
- `padding: 9px 14px`, `border-radius: 8px`, font 13px / 500
- Hover: subtle darken via `color-mix`

**Secondary / outline / ghost**

```html
<button class="btn">Hopp over</button>
<button class="btn btn-ghost btn-sm">Tving synk</button>
```

- `border: 1px solid var(--line-2)`, `color: var(--ink-2)`, transparent bg
- Hover: `color: var(--ink)`, border brightens, bg `rgba(255,255,255,0.03)`
- `.btn-ghost` removes border until hover; `.btn-sm` shrinks to `6px 10px` / 12px

No button ever takes the sage or rust accent.

### Tag (neutral)

```html
<span class="stag">Aerob base</span>
```

- `border: 1px solid var(--line-2)`, `border-radius: 999px`, `padding: 3px 10px`
- `font-size: 11.5px`, `color: var(--ink-2)`
- No fill, no accent color — pure typographic chip.

---

## Cross-page conventions

- Page padding: `px-7` mobile / `px-9` desktop, `py-12` top
- Max content width: `max-w-[1280px]`, centered
- Section spacing: `mt-7` between cards in same group, `mt-12` between named sections
- Hairline above section labels: `border-top: 1px solid var(--line)` + 11.5px mute eyebrow 14px below
- Norwegian throughout (mixed case): "I dag", "Uke", "Måned", "Logg", "Klar?", "Lagret", "Synket"
- All clock/date/duration values right-aligned within their cell, mono tnum

---

## Open extensions (TODO when needed)

These weren't required for Today but will likely come up on other pages:

- **Full charts** (Trends): line/bar/stacked rules — axes mute, grid line, data cream with opacity gradients, sage only on Z2/target segments in stacked
- **Modal/overlay** (Week edit-session): backdrop `rgba(0,0,0,0.5)` + 4px blur, card-soft 12px radius max-w 480px
- **Form fields** (Today quick-inputs, Week edit): slider track bg-2 + fill ink, RPE 0-10 pill row, number stepper with ghost +/-, focus state via subtle border darken (no ring)
- **Empty states**: italic state-text on muted line ("ingen økt planlagt", "ikke logget i dag")
- **Loading states** (HTMX swaps): vdim placeholder shimmer or just dimmed content during swap
