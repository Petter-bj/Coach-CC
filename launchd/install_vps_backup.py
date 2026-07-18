"""Installer Mac-jobben som henter VPS-databasebackuper via Tailscale.

Denne jobben er separat fra de gamle Claude/sync-launchd-jobbene. Den kjører
ved innlogging og deretter hvert døgn, og trekker kun immutable SQLite-backuper
fra ``/var/lib/trening/backups``. SSH-nøkkelen forblir passordbeskyttet og
brukes fra macOS Keychain.

Usage:
    uv run python -m launchd.install_vps_backup install --host 100.123.150.88
    uv run python -m launchd.install_vps_backup uninstall
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.trening.vps-backup"
INTERVAL_SECONDS = 24 * 60 * 60


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _run_launchctl(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, check=False
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _validate_host(host: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:")
    if not host or any(char not in allowed for char in host):
        raise ValueError("--host må være et vanlig vertsnavn eller en IP-adresse")
    return host


def _script(host: str) -> str:
    return f"""#!/bin/zsh
set -euo pipefail
umask 077

readonly HOST={host}
readonly DESTINATION=\"$HOME/Library/Application Support/Trening/offsite-vps-backups\"
readonly SSH_KEY=\"$HOME/.ssh/trening_vps\"

mkdir -p \"$DESTINATION\"
chmod 700 \"$DESTINATION\"

# Ved oppstart kan Tailscale bruke noen sekunder på å komme på nett.
for attempt in {{1..12}}; do
  if /usr/bin/ssh -i \"$SSH_KEY\" -o BatchMode=yes -o IdentitiesOnly=yes \\
    -o UseKeychain=yes -o AddKeysToAgent=yes petter@\"$HOST\" true; then
    exec /usr/bin/rsync -a --ignore-existing \\
      -e \"/usr/bin/ssh -i $SSH_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o UseKeychain=yes -o AddKeysToAgent=yes\" \\
      petter@\"$HOST\":/var/lib/trening/backups/ \"$DESTINATION/\"
  fi
  sleep 10
done

print -u2 \"[vps-backup] VPS-en var ikke tilgjengelig over Tailscale\"
exit 1
"""


def install(host: str) -> int:
    host = _validate_host(host)
    home = Path.home()
    app_support = home / "Library" / "Application Support" / "Trening"
    scripts_dir = app_support / "scripts"
    logs_dir = home / "Library" / "Logs" / "Trening"
    launch_agents = home / "Library" / "LaunchAgents"
    script_path = scripts_dir / "pull-vps-backup.sh"
    plist_path = launch_agents / f"{LABEL}.plist"

    scripts_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    launch_agents.mkdir(parents=True, exist_ok=True)

    script_path.write_text(_script(host), encoding="utf-8")
    script_path.chmod(0o700)

    plist = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/zsh", str(script_path)],
        "RunAtLoad": True,
        "StartInterval": INTERVAL_SECONDS,
        "ProcessType": "Background",
        "StandardOutPath": str(logs_dir / "vps-backup.stdout.log"),
        "StandardErrorPath": str(logs_dir / "vps-backup.stderr.log"),
    }
    plist_path.write_bytes(plistlib.dumps(plist, sort_keys=False))

    _run_launchctl("bootout", _domain(), str(plist_path))
    rc, output = _run_launchctl("bootstrap", _domain(), str(plist_path))
    if rc:
        print(f"✗ Kunne ikke starte {LABEL}: {output}", file=sys.stderr)
        return rc

    print(f"✓ Installert {LABEL} → {plist_path}")
    print(f"✓ Backupene lagres i {app_support / 'offsite-vps-backups'}")
    return 0


def uninstall() -> int:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    _run_launchctl("bootout", _domain(), str(plist_path))
    if plist_path.exists():
        plist_path.unlink()
    print(f"✓ Fjernet {LABEL}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--host", required=True)
    subparsers.add_parser("uninstall")
    args = parser.parse_args(argv)

    if args.command == "install":
        return install(args.host)
    return uninstall()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
