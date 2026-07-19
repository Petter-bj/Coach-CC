# systemd for Trening

These units are for the Linux VPS deployment. They cover deterministic data
jobs (sync, backup, optional weekly-plan proposal) and the private, read-only
dashboard API. The existing macOS monitor is tied to Claude Code/tmux and is
therefore not installed on the VPS.

## Layout

The unit files assume:

| Path | Purpose |
| --- | --- |
| `/opt/trening/app` | Git checkout and its `uv` virtual environment |
| `/var/lib/trening` | SQLite DB, credentials, FIT files, and app backups |
| `/var/log/trening` | App logs |
| `/var/cache/trening` | Disposable caches |

`StateDirectory`, `LogsDirectory`, and `CacheDirectory` create the three
runtime directories with the correct ownership when each unit runs. The
Python app receives the corresponding paths through `TRENING_*_DIR`.

## First installation

Run these commands on the VPS as an administrator after the repository has
been cloned to `/opt/trening/app` and dependencies have been installed with
`uv sync --frozen`:

```bash
sudo useradd --system --create-home --user-group trening
sudo chown -R trening:trening /opt/trening/app
sudo install -d -o trening -g trening -m 0700 /var/lib/trening/credentials
sudo install -m 0644 systemd/trening-*.service systemd/trening-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trening-sync.timer trening-backup.timer
```

Copy the existing credential files to `/var/lib/trening/credentials/` with
owner `trening:trening` and mode `0600`. Do not enable `trening-weekly-plan.timer`
until `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_CHAT_IDS` have been verified
on the VPS.

## Verify before cutover

```bash
sudo systemctl start trening-sync.service
sudo systemctl status trening-sync.service
sudo journalctl -u trening-sync.service -n 100 --no-pager
sudo systemctl list-timers 'trening-*'
```

Check the first sync and a backup on the VPS before disabling the Mac jobs.
Do not run two long-lived instances against copied OAuth refresh-token files:
some providers rotate refresh tokens, so one instance can invalidate the
other's credentials.

## Private dashboard API

`trening-api.service` serves both dashboardets statiske filer og `/api/*` fra
`127.0.0.1:8080`; den er bevisst ikke eksponert på det offentlige
nettverksgrensesnittet. Tailscale Serve blir den eneste ruten fra telefonen.

Dashboardet henter `/api/today` på samme URL som selve siden. Det betyr at
ingen API-nøkkel ligger i JavaScript eller på telefonen. Når Tailscale Serve
videresender en forespørsel, legger den til `Tailscale-User-Login`; appen
godtar den headeren bare fordi Uvicorn lytter på localhost.

Før første oppstart lager du en separat API-hemmelighet på VPS-en. Den brukes
kun til lokal administrativ verifisering, er ikke en LLM-nøkkel og må aldri
committes:

```bash
sudo sh -c 'umask 077; openssl rand -hex 32 | sed "s/^/TRENING_API_TOKEN=/" > /var/lib/trening/credentials/api.env'
sudo chown trening:trening /var/lib/trening/credentials/api.env
sudo systemctl enable --now trening-api.service
```

Verifiser lokalt på VPS-en uten å skrive ut nøkkelen:

```bash
sudo sh -c '. /var/lib/trening/credentials/api.env && curl -fsS -H "Authorization: Bearer $TRENING_API_TOKEN" http://127.0.0.1:8080/health'
```

API-et eksponerer `GET /health`, `GET /api/today`, den smale skrivehandlingen
`POST /api/reviews/{id}/confirm`, og den foreløpig kun lesende
`POST /api/coach/chat`. `api/today` skiller automatisk Garmin-data, den
deterministiske coach-anbefalingen, planlagte økter, baseline-endringer,
ukestatus og pending reviews.

Coachen bruker DeepSeek V4-Pro, men får kun en kuratert kontekst. Den får aldri
FIT-samples, GPS-posisjoner, kontoinformasjon, rå matvarelogg, database- eller
shelltilgang. Nøkkelen bor separat på VPS-en og skal ha samme eier/modus som
`api.env`:

```bash
sudo chown trening:trening /var/lib/trening/credentials/deepseek.env
sudo chmod 600 /var/lib/trening/credentials/deepseek.env
```

Den første chat-versjonen kan ikke gjøre endringer. Planforslag og en
eksplisitt godkjenningshandling bygges som et eget steg, slik at et modellsvar
aldri kan skrive til planen eller Garmin av seg selv.
