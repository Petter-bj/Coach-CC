# Trening

Personlig helse- og treningsdata-system. Henter automatisk fra Garmin,
Withings, Concept2 og Yazio til én lokal SQLite-DB på Mac, og eksponerer
smale CLI-er som Claude Code kan bruke via Telegram for morgenrapporter
og spørsmål om data.

## Arkitektur

```
launchd (hver time + 03:00 backup)
   ↓
python -m src.sync
   ├── Garmin (HRV, sleep, readiness, aktiviteter + FIT-samples)
   ├── Withings (vekt + kroppssammensetning)
   ├── Concept2 (skierg + FIT stroke-samples)
   └── Yazio (kcal + makroer per måltid)
   ↓
SQLite: ~/Library/Application Support/Trening/health.db
   ↓
src/cli/*  (status, sleep_summary, report morning/weekly, strength log, ...)
   ↓
Claude Code-sesjon med Telegram channel-plugin → bruker
```

All runtime-state ligger under `~/Library/` (ikke `~/Documents/`) for å
unngå TCC-friksjon.

## Førstegangs-setup

### 1. Forutsetninger

- macOS med Python 3.12+ (`brew install python@3.14`)
- [`uv`](https://github.com/astral-sh/uv) (`brew install uv`)
- Konto hos: Garmin Connect, Withings, Concept2, Yazio
- Telegram-konto (for bot) og Claude Max

### 2. Install og avhengigheter

```bash
git clone <repo-url>
cd Trening
uv sync
```

### 3. Credentials

Opprett `~/Library/Application Support/Trening/credentials/.env` med:

```bash
GARMIN_EMAIL=...
GARMIN_PASSWORD=...

WITHINGS_CLIENT_ID=...
WITHINGS_CLIENT_SECRET=...
WITHINGS_REDIRECT_URI=http://localhost:8080/callback

CONCEPT2_ACCESS_TOKEN=...      # fra log.concept2.com > Edit Profile > Applications

YAZIO_EMAIL=...
YAZIO_PASSWORD=...             # SIWA-brukere må sette passord først (Glemt passord-flow)
YAZIO_CLIENT_ID=...            # reverse-engineered, se merknad nedenfor
YAZIO_CLIENT_SECRET=...

TELEGRAM_BOT_TOKEN=...         # fra @BotFather
TELEGRAM_ALLOWED_CHAT_IDS=...
```

Dev-app registrering:
- Withings: [developer.withings.com](https://developer.withings.com) →
  Create application → Public API Integration → callback `http://localhost:8080/callback`

**Yazio-merknad:** Yazio tilbyr ikke offentlig dev-API. `CLIENT_ID`/`SECRET` er
felles konstanter som reverse-engineeres fra Yazio-appen. Samme verdier brukes
av community-klienter som [`dimensi/yazio`](https://github.com/dimensi/yazio)
og [`juriadams/yazio`](https://github.com/juriadams/yazio); sjekk
`src/utils/constants.ts` der. Dette bryter teknisk Yazios TOS selv om ingen har
blitt saksøkt for det — bruk på eget ansvar.

### 4. Kjør auth-spikes (én gang hver)

```bash
uv run python spikes/garmin_login.py      # MFA-prompt i terminal
uv run python spikes/withings_oauth.py    # åpner nettleser
uv run python spikes/concept2_oauth.py    # token-basert, direkte
uv run python spikes/yazio_login.py       # password-grant
```

Alle lagrer tokens i `~/Library/Application Support/Trening/credentials/`.

### 5. Første sync + migreringer

```bash
uv run python -m src.sync
```

### 6. Installer launchd-jobber

```bash
uv run python -m launchd.install install
```

Jobber lagt til:
- `com.trening.sync` — kjører ved boot + hver time
- `com.trening.backup` — kjører kl 03:00 daglig
- `com.trening.bot` — auto-starter Claude Code + Telegram channel i tmux

Verifiser:
```bash
uv run python -m launchd.install status
```

### 7. Valgfritt: Import historisk styrkelogg

```bash
uv run python spikes/import_strength_xlsx.py path/til/logg.xlsx
```

### 8. Claude Code + Telegram channel

```bash
claude  # starter Claude Code i repo-roten
# Inni Claude Code:
/plugin install telegram@claude-plugins-official
/telegram:configure $TELEGRAM_BOT_TOKEN
/telegram:access pair <kode fra Telegram>
/telegram:access policy allowlist
```

Start med channel-støtte:
```bash
claude --channels plugin:telegram@claude-plugins-official
# Hold i tmux for å overleve terminal-lukking:
tmux new -s trening 'claude --channels plugin:telegram@claude-plugins-official'
```

## Daglig bruk

### Via Telegram
Send meldinger til boten din:
- `morgenrapport` — Claude kjører `src.cli.report morning`
- `søvn siste uke` — `src.cli.sleep_summary --range last_7d`
- Screenshot av styrkeøkt → Claude parser + logger via `strength log`

### Via terminal
```bash
uv run python -m src.cli.status
uv run python -m src.cli.report morning
uv run python -m src.cli.report weekly
uv run python -m src.cli.last_workouts --limit 10
uv run python -m src.cli.baselines show
uv run python -m src.cli.wellness log --sleep 8 --soreness 3 --motivation 8 --energy 7
```

## Mapper

| Sti | Innhold |
|---|---|
| Kode (dette repoet) | `~/Documents/Prosjekter/Trening/` |
| DB + credentials + state | `~/Library/Application Support/Trening/` |
| FIT-filer + backups | `~/Library/Application Support/Trening/fit_files/` + `backups/` |
| Logger | `~/Library/Logs/Trening/` |
| Screenshot-cache | `~/Library/Caches/Trening/` |

## Gjenoppretting (restore)

```bash
# Stopp launchd-jobber
uv run python -m launchd.install uninstall

# Finn siste gyldige backup
ls -la ~/Library/Application\ Support/Trening/backups/

# Erstatt DB
cp ~/Library/Application\ Support/Trening/backups/daily-YYYY-MM-DD.db \
   ~/Library/Application\ Support/Trening/health.db

# Verifiser
sqlite3 ~/Library/Application\ Support/Trening/health.db 'PRAGMA integrity_check;'

# Re-installer launchd
uv run python -m launchd.install install
```

## Dependency-policy

Ingen auto-upgrade av `garminconnect`, `fitdecode`, etc. Versjoner låses
i `uv.lock`. Ved manuell bump: kjør `pytest` og minst én manuell sync før
launchd får lov å kjøre igjen.

## Privacy

Claude Code via Telegram sender data til Anthropic. Systemet er designet
slik at Claude får aggregerte svar fra CLI-er, ikke rå tabell-dumps.
Rapporter og analyse bygges lokalt (Python); Claude formulerer naturlig
språk rundt ferdig-beregnede tall.

Screenshots lagres lokalt under `~/Library/Caches/Trening/` og renses
automatisk etter 30 dager.

## Tester

```bash
uv run pytest tests/
```

130+ tester dekker schema-migrasjoner, parser-funksjoner, FIT-replay,
dedupe, baselines, recovery-regler og CLI-kontrakter.
