"""Bot health monitor — kjøres hvert 5. min av launchd.

Sjekker om Telegram-bot-en lever, svarer og har gyldig auth. Sender alert
direkte via Telegram Bot API (uavhengig av Claude Code) hvis noe er galt.

Entry point:
    uv run python -m src.monitor

Launchd-job `com.trening.monitor` kjører dette hvert 5. min.

Health checks:
    1. Claude Code-prosess lever (pgrep claude --channels)
    2. tmux-sesjon "trening" lever
    3. Ingen nylig "API Error: 401" i pane-output (auth-fail)
    4. api.telegram.org nåbar (TCP connect)

Dedupe: hver alert-type sendes maks én gang per 30 min.
Auto-recovery: process_dead → kickstart bot.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.paths import APP_SUPPORT, ENV_FILE, LOGS, ensure_runtime_dirs

# Last .env så TELEGRAM_BOT_TOKEN og _ALLOWED_CHAT_IDS er tilgjengelig
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

STATE_FILE = APP_SUPPORT / "monitor_state.json"
DEDUPE_MINUTES = 30
TMUX_SOCKET = "/tmp/trening-socket"
TMUX_SESSION = "trening"
TELEGRAM_HOST = "api.telegram.org"
TELEGRAM_PORT = 443

# Hvor mange linjer å sjekke fra tmux pane for 401-errors
PANE_TAIL_LINES = 200

# Claude-prosess-identifikator (matches "claude --channels plugin:...")
CLAUDE_PROCESS_PATTERN = "claude --channels plugin:telegram"


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    type: str
    message: str
    auto_recoverable: bool = False


def find_claude_process() -> int | None:
    """Returner PID av claude --channels-prosessen, eller None."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", CLAUDE_PROCESS_PATTERN],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None
    pids = result.stdout.strip().split("\n")
    pids = [p for p in pids if p.strip()]
    return int(pids[0]) if pids else None


def tmux_session_alive() -> bool:
    try:
        result = subprocess.run(
            ["tmux", "-S", TMUX_SOCKET, "has-session", "-t", TMUX_SESSION],
            capture_output=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0


def capture_tmux_pane(tail_lines: int = PANE_TAIL_LINES) -> str:
    try:
        result = subprocess.run(
            ["tmux", "-S", TMUX_SOCKET, "capture-pane", "-t", TMUX_SESSION,
             "-p", "-S", f"-{tail_lines}"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def has_recent_auth_error(pane_output: str) -> bool:
    """Sjekk om pane inneholder nylig 401/auth-feil."""
    # Se etter "API Error: 401" eller "authentication_error" eller "/login"-prompt
    indicators = [
        "API Error: 401",
        "authentication_error",
        "Invalid authentication credentials",
    ]
    return any(ind in pane_output for ind in indicators)


def telegram_reachable(timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((TELEGRAM_HOST, TELEGRAM_PORT), timeout=timeout):
            return True
    except (socket.timeout, socket.gaierror, OSError):
        return False


def check_health() -> list[Issue]:
    """Kjør alle sjekker, returner liste med issues (tom = alt OK)."""
    issues: list[Issue] = []

    # Check 1: Claude-prosess
    pid = find_claude_process()
    if pid is None:
        issues.append(Issue(
            type="process_dead",
            message="Claude Code channel-prosess er ikke kjørende",
            auto_recoverable=True,
        ))
        # Ikke sjekk tmux/pane hvis prosessen er død
        return issues

    # Check 2: tmux-sesjon
    if not tmux_session_alive():
        issues.append(Issue(
            type="tmux_dead",
            message=f"tmux-sesjon '{TMUX_SESSION}' på socket {TMUX_SOCKET} er ikke aktiv",
            auto_recoverable=True,
        ))
        return issues  # Kan ikke capture pane hvis ikke tmux

    # Check 3: auth 401 i pane
    pane = capture_tmux_pane()
    if has_recent_auth_error(pane):
        issues.append(Issue(
            type="auth_401",
            message=(
                "Claude Max OAuth-token utløpt (401) — boten kan ikke svare. "
                "Fix: drep tmux-sesjon, kjør `claude` + `/login`, restart bot."
            ),
            auto_recoverable=False,
        ))

    # Check 4: Telegram-tilkobling
    if not telegram_reachable():
        issues.append(Issue(
            type="telegram_unreachable",
            message=f"Kan ikke nå {TELEGRAM_HOST}:{TELEGRAM_PORT} — nettverks- eller Telegram-problem",
            auto_recoverable=False,
        ))

    return issues


# ---------------------------------------------------------------------------
# Dedupe-state
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_alerts": {}}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"last_alerts": {}}


def save_state(state: dict) -> None:
    ensure_runtime_dirs()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def should_alert(state: dict, issue_type: str, now: datetime | None = None) -> bool:
    """Returner True hvis denne issue-typen ikke er alertet på innen siste
    DEDUPE_MINUTES."""
    now = now or datetime.now(timezone.utc)
    last = state.get("last_alerts", {}).get(issue_type)
    if not last:
        return True
    try:
        last_ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return True
    elapsed = now - last_ts
    return elapsed >= timedelta(minutes=DEDUPE_MINUTES)


def mark_alerted(state: dict, issue_type: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    state.setdefault("last_alerts", {})[issue_type] = now.isoformat()


# ---------------------------------------------------------------------------
# Telegram-alert
# ---------------------------------------------------------------------------


def _resolve_chat_id() -> str | None:
    """Finn første tillatte chat_id. Prøver i rekkefølge:
    1. Env var TELEGRAM_ALLOWED_CHAT_IDS (kommaseparert)
    2. Telegram plugin sin access.json (~/.claude/channels/telegram/access.json)
    """
    env_val = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    if env_val:
        first = env_val.split(",")[0].strip()
        if first:
            return first

    access_path = Path.home() / ".claude" / "channels" / "telegram" / "access.json"
    if access_path.exists():
        try:
            access = json.loads(access_path.read_text())
            allow_from = access.get("allowFrom") or []
            if allow_from:
                return str(allow_from[0])
        except (OSError, json.JSONDecodeError):
            pass
    return None


def send_telegram_alert(message: str) -> bool:
    """Send alert direkte via Telegram Bot API. Returnerer True ved suksess."""
    import httpx

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[monitor] Mangler TELEGRAM_BOT_TOKEN i .env", file=sys.stderr)
        return False

    chat_id = _resolve_chat_id()
    if not chat_id:
        print("[monitor] Fant ingen chat_id (verken i env eller access.json)",
              file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🚨 Bot-health alert\n\n{message}",
    }
    try:
        resp = httpx.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[monitor] Telegram-API returnerte {resp.status_code}: "
                  f"{resp.text[:200]}", file=sys.stderr)
        return resp.status_code == 200
    except httpx.HTTPError as e:
        print(f"[monitor] Telegram-alert feilet: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Auto-recovery
# ---------------------------------------------------------------------------


def try_auto_recover(issue_type: str) -> bool:
    """Forsøk å auto-fikse kjente issues. Returner True ved forsøk utført."""
    if issue_type == "process_dead":
        uid = os.getuid()
        try:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/com.trening.bot"],
                capture_output=True, text=True, timeout=10,
            )
            print(f"[monitor] Auto-recover attempt: launchctl kickstart → "
                  f"rc={result.returncode} {result.stdout.strip()}")
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"[monitor] Auto-recover feilet: {e}", file=sys.stderr)
            return False
    if issue_type == "tmux_dead":
        # Samme fiks — kickstart bot som sjekker/lager tmux-sesjon
        return try_auto_recover("process_dead")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    issues = check_health()
    now = datetime.now(timezone.utc)

    if not issues:
        print(f"[monitor] {now.isoformat()} — all health checks passed")
        return 0

    state = load_state()
    alerted_now = []
    recovered = []

    for issue in issues:
        if issue.auto_recoverable:
            if try_auto_recover(issue.type):
                recovered.append(issue.type)

        if should_alert(state, issue.type, now):
            # Inkluder auto-recovery-status i alerten
            extra = ""
            if issue.type in recovered:
                extra = "\n\n🔧 Auto-recovery-forsøk utført (launchctl kickstart bot). Sjekk igjen om noen minutter."

            if send_telegram_alert(issue.message + extra):
                mark_alerted(state, issue.type, now)
                alerted_now.append(issue.type)
                print(f"[monitor] Alerted: {issue.type}")
            else:
                print(f"[monitor] Kunne ikke sende alert for {issue.type} "
                      "(Telegram down?)", file=sys.stderr)
        else:
            print(f"[monitor] Skip alert for {issue.type} (deduped)")

    save_state(state)

    # Exit-kode indikerer om det er issues — nyttig for launchd-logging
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
