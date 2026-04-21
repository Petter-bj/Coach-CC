# Trening

> **Disclaimer:** Personal hobby project built for my own Mac. Published
> as reference — not a product. Uses unofficial scrapers (Garmin Connect)
> and reverse-engineered endpoints (Yazio) that can break at any time
> without warning. Use at your own risk. No support provided.

A personal health and training data system. Automatically pulls from
Garmin Connect, Withings, Concept2 Logbook, and Yazio into a single local
SQLite database on a Mac, then exposes narrow CLI commands that Claude
Code (via the Telegram channel plugin) can call for morning briefings
and ad-hoc questions.

Code comments and commit history are in Norwegian (written during
development); the system itself works regardless.

## Architecture

```
launchd (hourly sync + nightly backup + auto-restart bot)
   ↓
python -m src.sync
   ├── Garmin    (HRV, sleep, readiness, activities + FIT samples)
   ├── Withings  (weight + body composition)
   ├── Concept2  (SkiErg sessions + FIT stroke samples)
   └── Yazio     (kcal + macros per meal)
   ↓
SQLite: ~/Library/Application Support/Trening/health.db
   ↓
src/cli/*  (status, sleep_summary, report morning/weekly, strength log, ...)
   ↓
Claude Code session with Telegram channel plugin → user
```

All runtime state lives under `~/Library/` (not `~/Documents/`) to avoid
macOS TCC (Transparency, Consent & Control) friction with launchd.

## First-time setup

### 1. Prerequisites

- macOS with Python 3.12+ (`brew install python@3.14`)
- [`uv`](https://github.com/astral-sh/uv) (`brew install uv`)
- `tmux` (`brew install tmux`) — for the auto-start bot
- Accounts at: Garmin Connect, Withings, Concept2, Yazio
- Telegram account (for bot) and Claude Max subscription

### 2. Install and dependencies

```bash
git clone <repo-url>
cd Trening
uv sync
```

### 3. Credentials

Create `~/Library/Application Support/Trening/credentials/.env`:

```bash
GARMIN_EMAIL=...
GARMIN_PASSWORD=...

WITHINGS_CLIENT_ID=...
WITHINGS_CLIENT_SECRET=...
WITHINGS_REDIRECT_URI=http://localhost:8080/callback

CONCEPT2_ACCESS_TOKEN=...      # from log.concept2.com > Edit Profile > Applications

YAZIO_EMAIL=...
YAZIO_PASSWORD=...             # SIWA-only users must set a password first (Forgot Password flow)
YAZIO_CLIENT_ID=...            # reverse-engineered — see note below
YAZIO_CLIENT_SECRET=...

TELEGRAM_BOT_TOKEN=...         # from @BotFather
TELEGRAM_ALLOWED_CHAT_IDS=...
```

Developer app registration:
- Withings: [developer.withings.com](https://developer.withings.com) →
  Create application → Public API Integration → callback `http://localhost:8080/callback`

**Yazio note:** Yazio does not offer a public developer API. `CLIENT_ID` /
`CLIENT_SECRET` are shared constants reverse-engineered from the Yazio
mobile app binary. The same values are used by community clients like
[`dimensi/yazio`](https://github.com/dimensi/yazio) and
[`juriadams/yazio`](https://github.com/juriadams/yazio) — check
`src/utils/constants.ts` in those repos. This technically violates Yazio's
ToS, though nobody has been sued for it to date. Use at your own risk.

### 4. Run auth spikes (once each)

```bash
uv run python spikes/garmin_login.py      # MFA prompt in terminal
uv run python spikes/withings_oauth.py    # opens browser
uv run python spikes/concept2_oauth.py    # token-based, direct
uv run python spikes/yazio_login.py       # password grant
```

Each spike stores tokens in `~/Library/Application Support/Trening/credentials/`.

### 5. First sync + migrations

```bash
uv run python -m src.sync
```

### 6. Install launchd jobs

```bash
uv run python -m launchd.install install
```

Jobs installed:
- `com.trening.sync` — runs at boot + every hour
- `com.trening.backup` — runs daily at 03:00
- `com.trening.bot` — auto-starts Claude Code + Telegram channel in tmux

Verify:
```bash
uv run python -m launchd.install status
```

### 7. Optional: Import historical strength log

```bash
uv run python spikes/import_strength_xlsx.py path/to/log.xlsx
```

### 8. Claude Code + Telegram channel

```bash
# Start a BotFather bot on Telegram first (@BotFather → /newbot)
# and send a message to your bot to activate the chat.

claude  # starts Claude Code in the repo root
# Inside Claude Code:
/plugin install telegram@claude-plugins-official
/telegram:configure $TELEGRAM_BOT_TOKEN
/telegram:access pair <code sent from Telegram>
/telegram:access policy allowlist
```

The `com.trening.bot` launchd job automatically starts Claude Code inside
a detached tmux session at login and re-checks every minute. If the bot
crashes or you restart your Mac, it comes back up within ~60 seconds.

## Daily usage

### Via Telegram
Message your bot:
- `morning report` → Claude runs `src.cli.report morning`
- `sleep last week` → `src.cli.sleep_summary --range last_7d`
- Screenshot of a strength session → Claude parses + logs via `strength log`

### Via terminal
```bash
uv run python -m src.cli.status
uv run python -m src.cli.report morning
uv run python -m src.cli.report weekly
uv run python -m src.cli.last_workouts --limit 10
uv run python -m src.cli.baselines show
uv run python -m src.cli.wellness log --sleep 8 --soreness 3 --motivation 8 --energy 7
```

## Directories

| Path | Contents |
|---|---|
| Source code (this repo) | `~/Documents/Prosjekter/Trening/` |
| DB + credentials + state | `~/Library/Application Support/Trening/` |
| FIT files + backups | `~/Library/Application Support/Trening/fit_files/` + `backups/` |
| Logs | `~/Library/Logs/Trening/` |
| Screenshot cache | `~/Library/Caches/Trening/` |

## Restore from backup

```bash
# Stop launchd jobs
uv run python -m launchd.install uninstall

# Find a known-good backup
ls -la ~/Library/Application\ Support/Trening/backups/

# Replace the DB
cp ~/Library/Application\ Support/Trening/backups/daily-YYYY-MM-DD.db \
   ~/Library/Application\ Support/Trening/health.db

# Verify integrity
sqlite3 ~/Library/Application\ Support/Trening/health.db 'PRAGMA integrity_check;'

# Re-install launchd
uv run python -m launchd.install install
```

## Dependency policy

No auto-upgrades of `garminconnect`, `fitdecode`, etc. Versions are pinned
in `uv.lock`. When bumping manually: run `pytest` and at least one manual
sync before letting launchd take over again.

## Privacy

Telegram messages flow through Anthropic (Claude). The system is designed
so Claude receives aggregated outputs from the CLIs rather than raw table
dumps. Reports and analysis are built locally (Python); Claude just
phrases them in natural language around pre-computed numbers.

Screenshots are stored locally under `~/Library/Caches/Trening/` and
cleaned up automatically after 30 days.

## Tests

```bash
uv run pytest tests/
```

130+ tests covering schema migrations, parser functions, FIT replay,
dedupe logic, baselines, recovery rules, and CLI contracts.

## License

MIT — see [LICENSE](LICENSE).
