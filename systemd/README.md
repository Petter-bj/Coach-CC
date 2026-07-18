# systemd for Trening

These units are for the Linux VPS deployment. They deliberately cover only
deterministic data jobs: sync, backup, and the optional weekly-plan proposal.
The existing macOS monitor is tied to Claude Code/tmux and is therefore not
installed on the VPS.

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
