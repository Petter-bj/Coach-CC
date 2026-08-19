# Coach-CC

Coach-CC is a private, self-hosted training-data and coaching system. It
collects training, health, and nutrition data in one SQLite database, then
uses deterministic analysis and a private AI coach to support training
planning, review, and day-to-day decisions.

It is built for one person, not as a hosted product. The production deployment
runs continuously on a Linux VPS and is accessible only through a private
Tailscale network.

> **Personal project disclaimer:** Garmin Connect is accessed through an
> unofficial client and Yazio uses reverse-engineered endpoints. Either
> integration may break without warning. This repository is shared as a
> reference implementation; it comes with no support or service guarantees.

## What it does

- Syncs data from Garmin Connect, Withings, Concept2 Logbook, Yazio, and Hevy.
- Stores normalized workouts, FIT samples, sleep, HRV, readiness, weight,
  nutrition, and strength sessions in SQLite.
- Calculates personal baselines, recovery signals, training load, progression,
  and workout-plan reconciliation.
- Provides CLI tools for reports and analysis, plus a private FastAPI dashboard
  optimized for desktop and phone.
- Uses DeepSeek V4-Pro for conversational coaching with curated context rather
  than database, filesystem, or shell access.
- Keeps plan, injury, review, and Hevy-routine changes behind explicit proposal
  and confirmation flows.
- Runs hourly data syncs, nightly SQLite backups, and off-server backup pulls
  to a Mac over Tailscale.

## Architecture

```text
Garmin · Withings · Concept2 · Yazio · Hevy
                     │
                     ▼
          src.sync (hourly systemd timer)
                     │
                     ▼
      SQLite + FIT files (/var/lib/trening)
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
 deterministic analysis    FastAPI + dashboard
 baselines · recovery       private coach · plans · reviews
 reconciliation             127.0.0.1:8080
          │                     │
          └──────────┬──────────┘
                     ▼
            Tailscale Serve
                     │
          Mac / iPhone (private tailnet)

Nightly SQLite backup → VPS backup directory → pulled to Mac every 24 hours
```

The dashboard API only listens on `127.0.0.1`. Tailscale Serve is the external
entry point, so the service is not exposed on the public VPS interface.

## Safety and privacy model

The coaching model is deliberately narrow:

- DeepSeek receives a curated summary for the active surface, not raw database
  tables, FIT samples, GPS positions, credentials, or shell access.
- The model cannot execute commands or write directly to the database.
- Health-status, plan, block, review, and Hevy-routine changes are stored as
  visible proposals and require a separate user confirmation.
- Credentials stay outside the repository, in owner-only files on the VPS.

## Repository layout

```text
src/
  sources/        Garmin, Withings, Concept2, Yazio, and Hevy sync adapters
  analysis/       baselines, recovery, exercises, and training-load analysis
  coaching/       deterministic philosophy, knowledge maps, and coach clients
  api/            FastAPI dashboard, planning, reviews, and proposal flows
  cli/            terminal reports and data-inspection commands
  integrations/   narrow write integrations, currently Hevy routines
systemd/           VPS services and timers
launchd/           macOS helpers, including the off-server backup pull agent
dashboard_preview/ static dashboard client used by the FastAPI service
knowledge/         curated coaching knowledge sent selectively to the model
tests/             unit, integration, API, migration, and backup tests
```

## Local development

### Prerequisites

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv)
- Accounts for the data providers you want to enable

```bash
git clone https://github.com/Petter-bj/Coach-CC.git
cd Coach-CC
uv sync
uv run pytest
```

The source adapters load credentials from the runtime credentials directory:

```text
~/Library/Application Support/Trening/credentials/.env  # macOS default
/var/lib/trening/credentials/.env                       # VPS deployment
```

The exact variables depend on the sources in use. Typical examples are
`GARMIN_EMAIL`, `GARMIN_PASSWORD`, `WITHINGS_CLIENT_ID`,
`CONCEPT2_ACCESS_TOKEN`, `YAZIO_EMAIL`, `YAZIO_PASSWORD`, and
`HEVY_API_KEY`. Never commit personal credentials.

Run a manual sync locally:

```bash
uv run python -m src.sync
```

Start the dashboard locally after configuring a database and credentials:

```bash
uv run python -m src.api
```

It listens on `http://127.0.0.1:8080` and serves both the dashboard and
`/api/*` from the same origin.

### Static dashboard preview

For visual work without personal data, the dashboard can run as a static demo:

```bash
python3 -m http.server 4173 --directory dashboard_preview
```

Open `http://localhost:4173`. The static preview uses representative data and
does not persist coach messages or call external providers.

## VPS deployment

The primary deployment target is a small Linux VPS. `systemd/` contains units
for the hourly sync, nightly backup, private API, and optional weekly planning
job.

```bash
cd /opt/trening/app
uv sync --frozen
sudo install -m 0644 systemd/trening-*.service systemd/trening-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trening-sync.timer trening-backup.timer trening-api.service
```

See [`systemd/README.md`](systemd/README.md) for runtime paths, credential
permissions, API verification, and the Tailscale setup.

The database backup uses SQLite's online backup API and runs an integrity check
before keeping the copy. Daily backups are retained for 14 days and weekly
backups for eight weeks. A macOS launch agent pulls completed VPS backups to
the Mac at login and then every 24 hours when the Mac is available.

## Hevy routines

With a `HEVY_API_KEY`, Coach-CC can import strength history and create a Hevy
routine after the user explicitly approves a generated proposal. The model
never receives the API key and cannot call Hevy directly; the narrow
integration layer validates the routine and resolves exercise-template IDs.

## Testing

```bash
uv run pytest
```

The test suite currently contains 374 tests covering migrations, parsers, FIT
replay, source behavior, reconciliation, baselines, recovery rules, coach
proposal validation, API flows, backup installation, and CLI contracts.

## Notes

- Norwegian is used in code comments, user-facing coaching text, and commit
  history because this is a Norwegian personal project.
- `launchd/` also contains older Mac/Telegram helpers. The current primary
  product path is the private VPS dashboard and API.
- Dependency versions are locked in `uv.lock`. Run tests and a manual sync
  before upgrading provider libraries.

## License

[MIT](LICENSE)
