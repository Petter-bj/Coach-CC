# systemd deployment

These units run Coach-CC's deterministic jobs and private FastAPI dashboard on
a Linux VPS. They are the primary production deployment path.

## Runtime layout

| Path | Purpose |
| --- | --- |
| `/opt/trening/app` | Git checkout and its `uv` virtual environment |
| `/var/lib/trening` | SQLite DB, credentials, FIT files, and backups |
| `/var/log/trening` | Application logs |
| `/var/cache/trening` | Disposable caches |

`StateDirectory`, `LogsDirectory`, and `CacheDirectory` create the runtime
directories with the correct ownership. The application receives their paths
through `TRENING_*_DIR` environment variables.

## First installation

Run these commands on the VPS after cloning the repository to
`/opt/trening/app`:

```bash
cd /opt/trening/app
uv sync --frozen

sudo useradd --system --create-home --user-group trening
sudo chown -R trening:trening /opt/trening/app
sudo install -d -o trening -g trening -m 0700 /var/lib/trening/credentials
sudo install -m 0644 systemd/trening-*.service systemd/trening-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trening-sync.timer trening-backup.timer trening-api.service
```

Copy provider credentials to `/var/lib/trening/credentials/` with owner
`trening:trening` and mode `0600`. The dashboard also uses:

- `api.env` for the local administrative verification token.
- `deepseek.env` for the private coach API key.
- `hevy.env` when Hevy-routine creation is enabled.

Create the API token without printing it to the terminal:

```bash
sudo sh -c 'umask 077; openssl rand -hex 32 | sed "s/^/TRENING_API_TOKEN=/" > /var/lib/trening/credentials/api.env'
sudo chown trening:trening /var/lib/trening/credentials/api.env
sudo chmod 600 /var/lib/trening/credentials/api.env
```

## Timers

| Unit | Schedule | Purpose |
| --- | --- | --- |
| `trening-sync.timer` | five minutes after boot, then hourly with up to two minutes of jitter | provider sync and plan reconciliation |
| `trening-backup.timer` | daily around 03:00 UTC, with up to ten minutes of jitter | SQLite online backup and integrity check |
| `trening-weekly-plan.timer` | optional, Sundays at 20:00 UTC | historical Telegram weekly-plan proposal |

Verify the installation:

```bash
sudo systemctl start trening-sync.service
sudo systemctl status trening-sync.service --no-pager
sudo journalctl -u trening-sync.service -n 100 --no-pager
sudo systemctl list-timers 'trening-*'
```

Do not run two persistent sync instances against copied OAuth refresh-token
files. Some providers rotate refresh tokens, so two clients can invalidate
each other.

## Private dashboard

`trening-api.service` serves the static dashboard and `/api/*` from
`127.0.0.1:8080`. It is intentionally not exposed on the public network
interface. Tailscale Serve is the sole route from a phone or other personal
device.

```bash
sudo tailscale serve --bg 8080
```

The dashboard and API use the same private URL. No API token is sent to the
browser: requests from Tailscale Serve are identified by the
`Tailscale-User-Login` header, which is accepted only because Uvicorn listens
on localhost. The `TRENING_API_TOKEN` is retained for local administrative
verification:

```bash
sudo sh -c '. /var/lib/trening/credentials/api.env && curl -fsS -H "Authorization: Bearer $TRENING_API_TOKEN" http://127.0.0.1:8080/health'
```

The API provides today, week, block, workout-detail, review, planning,
manual-sync, and coach routes. Proposal flows are explicit: health-status,
week, block, review, and Hevy-routine changes are validated and shown to the
user before a separate confirmation endpoint applies them.

## Coach key

The coach uses DeepSeek V4-Pro and receives only a curated context. It never
receives raw FIT samples, GPS positions, account information, the full food
log, database access, or shell access. Store its key separately:

```bash
sudo chown trening:trening /var/lib/trening/credentials/deepseek.env
sudo chmod 600 /var/lib/trening/credentials/deepseek.env
```

The model can return text and unapplied proposal candidates; it can never
write to the database, plan, Garmin, or Hevy by itself.
