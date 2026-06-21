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
    3b. Plugin disconnect (bun-bot respawnet uten at Claude reconnectet)
    4. api.telegram.org nåbar (TCP connect)
    5. Sync har kjørt vellykket siste SYNC_STALE_HOURS timer

Dedupe: hver alert-type sendes maks én gang per 30 min.
Auto-recovery:
    process_dead / tmux_dead → kickstart bot
    plugin_disconnect        → full tmux-restart (cooldown RESTART_COOLDOWN_MINUTES)
    sync_stale               → kickstart sync
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

from src.paths import APP_SUPPORT, DB_PATH, ENV_FILE, LOGS, ensure_runtime_dirs

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

# Telegram-bot (bun MCP-server) prosess-identifikator
BOT_PROCESS_PATTERN = "bun server.ts"

# Sync-staleness: hvis ingen vellykket sync_run siste N timer → flagg
SYNC_STALE_HOURS = 12

# Plugin-disconnect: hvis bun-bot er > N sek yngre enn Claude-prosessen,
# har bun respawnet uten at Claude reconnectet → plugin er disconnected.
# I en frisk synkron oppstart er differansen ~1-40 sek.
PLUGIN_AGE_SKEW_SEC = 120

# Minste tid mellom auto-restarts av tmux (hindrer restart-loop)
RESTART_COOLDOWN_MINUTES = 8


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


def find_bot_process() -> int | None:
    """Returner PID av bun-bot-prosessen (Telegram MCP-server), eller None."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", BOT_PROCESS_PATTERN],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None
    pids = [p for p in result.stdout.strip().split("\n") if p.strip()]
    return int(pids[0]) if pids else None


def _parse_etime(etime: str) -> float | None:
    """Parse `ps -o etime`-format til sekunder.

    Format: [[DD-]HH:]MM:SS  (macOS/BSD ps støtter ikke `etimes`).
    Eksempler: '42:19' → 2539s, '1:23:45' → 5025s, '3-00:15:32' → 260132s.
    """
    etime = etime.strip()
    if not etime:
        return None
    days = 0
    if "-" in etime:
        day_part, etime = etime.split("-", 1)
        try:
            days = int(day_part)
        except ValueError:
            return None
    parts = etime.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:       # MM:SS
        h, m, s = 0, nums[0], nums[1]
    elif len(nums) == 3:     # HH:MM:SS
        h, m, s = nums
    else:
        return None
    return days * 86400 + h * 3600 + m * 60 + s


def process_age_seconds(pid: int) -> float | None:
    """Returner prosessens alder i sekunder via `ps -o etime=`."""
    try:
        result = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return _parse_etime(result.stdout)


def plugin_out_of_sync() -> bool:
    """True hvis bun-bot er vesentlig yngre enn Claude — dvs. bun respawnet
    uten at Claude reconnectet til den nye MCP-instansen (plugin disconnect).

    I en frisk synkron oppstart starter Claude først og spawner bun noen sek
    senere, så Claude er alltid LITT eldre (~1-40 sek). Hvis Claude er
    > PLUGIN_AGE_SKEW_SEC eldre enn bun, har bun krasjet og respawnet alene.
    """
    claude_pid = find_claude_process()
    bot_pid = find_bot_process()
    if claude_pid is None or bot_pid is None:
        return False
    claude_age = process_age_seconds(claude_pid)
    bot_age = process_age_seconds(bot_pid)
    if claude_age is None or bot_age is None:
        return False
    return (claude_age - bot_age) > PLUGIN_AGE_SKEW_SEC


def has_plugin_disconnect(pane_output: str, recent_lines: int = 40) -> bool:
    """Sjekk om de siste pane-linjene inneholder Claudes egne disconnect-fraser.

    Komplementær til plugin_out_of_sync() — fanger tilfeller der Claude
    eksplisitt rapporterer at den ikke fikk sendt svar."""
    tail = "\n".join(pane_output.splitlines()[-recent_lines:]).lower()
    phrases = [
        "telegram-tilkoblingen",
        "telegram-pluginen",
        "telegram-kanalen falt ut",
        "kanalen falt ut",
        "pluginen har koblet fra",
        "pluginen disconnecta",
        "tilkoblingen ser ut til å ha droppet",
    ]
    return any(p in tail for p in phrases)


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


def hours_since_last_successful_sync() -> float | None:
    """Returner timer siden siste sync_runs-rad med status='success'.

    None hvis DB ikke eksisterer eller ingen vellykket sync ennå.
    """
    import sqlite3
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT MAX(finished_at) AS last_ok
              FROM sync_runs
             WHERE status = 'success'
            """
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if not row or not row["last_ok"]:
        return None
    try:
        last = datetime.fromisoformat(row["last_ok"].replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = datetime.now(timezone.utc) - last
    return delta.total_seconds() / 3600.0


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
        # Ikke restart for plugin-disconnect samtidig — 401 må fikses manuelt
        return issues

    # Check 3b: plugin disconnect (bun respawnet uten at Claude reconnectet)
    if plugin_out_of_sync() or has_plugin_disconnect(pane):
        issues.append(Issue(
            type="plugin_disconnect",
            message=(
                "Telegram-plugin disconnected — bun-bot respawnet uten at "
                "Claude reconnectet. Auto-restart av tmux-sesjon utføres."
            ),
            auto_recoverable=True,
        ))

    # Check 4: Telegram-tilkobling
    if not telegram_reachable():
        issues.append(Issue(
            type="telegram_unreachable",
            message=f"Kan ikke nå {TELEGRAM_HOST}:{TELEGRAM_PORT} — nettverks- eller Telegram-problem",
            auto_recoverable=False,
        ))

    # Check 5: Sync har kjørt vellykket nylig
    hours_stale = hours_since_last_successful_sync()
    if hours_stale is not None and hours_stale > SYNC_STALE_HOURS:
        issues.append(Issue(
            type="sync_stale",
            message=(
                f"Siste vellykkede sync var {hours_stale:.1f}t siden "
                f"(grense: {SYNC_STALE_HOURS}t). Garmin/Hevy/etc-data er ikke oppdatert. "
                "Mac kan ha sovet eller launchd-job kan ha feilet. "
                "Fix: `cd ~/Documents/Prosjekter/Trening && uv run python -m launchd.install kickstart sync`"
            ),
            auto_recoverable=True,
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


def _restart_cooldown_ok(state: dict, now: datetime | None = None) -> bool:
    """True hvis det er lenge nok siden forrige auto-restart (unngå loop)."""
    now = now or datetime.now(timezone.utc)
    last = state.get("last_restart")
    if not last:
        return True
    try:
        last_ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True
    return (now - last_ts) >= timedelta(minutes=RESTART_COOLDOWN_MINUTES)


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


def send_telegram_message(text: str) -> bool:
    """Send rå melding til Telegram via Bot API. Returner True ved suksess.

    Brukes også av andre moduler (weekly_plan etc.) — derfor er den generisk.
    Mangler TELEGRAM_BOT_TOKEN/chat_id → returnerer False og logger.
    """
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
    payload = {"chat_id": chat_id, "text": text}
    try:
        resp = httpx.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[monitor] Telegram-API returnerte {resp.status_code}: "
                  f"{resp.text[:200]}", file=sys.stderr)
        return resp.status_code == 200
    except httpx.HTTPError as e:
        print(f"[monitor] Telegram-melding feilet: {e}", file=sys.stderr)
        return False


def send_telegram_alert(message: str) -> bool:
    """Send health-alert med standard prefix. Wrapper over send_telegram_message."""
    return send_telegram_message(f"🚨 Bot-health alert\n\n{message}")


# ---------------------------------------------------------------------------
# Auto-recovery
# ---------------------------------------------------------------------------


def _kickstart_bot() -> bool:
    """launchctl kickstart com.trening.bot. Returner True ved forsøk utført."""
    uid = os.getuid()
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/com.trening.bot"],
            capture_output=True, text=True, timeout=10,
        )
        print(f"[monitor] launchctl kickstart bot → rc={result.returncode} "
              f"{result.stdout.strip()}")
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[monitor] kickstart feilet: {e}", file=sys.stderr)
        return False


def _full_restart_bot() -> bool:
    """Drep tmux-sesjon + bun-prosesser, så kickstart bot.

    Brukes for plugin_disconnect: tvinger Claude OG bun til å starte på nytt
    sammen, slik at plugin-tilkoblingen er synk. En enkel kickstart holder
    ikke — start-bot.sh ser at tmux allerede lever og gjør ingenting.
    """
    # 1. Drep tmux-sesjonen (river med seg Claude + barn)
    try:
        subprocess.run(
            ["tmux", "-S", TMUX_SOCKET, "kill-session", "-t", TMUX_SESSION],
            capture_output=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    # 2. Rydd opp evt. foreldreløse bun-prosesser
    for pattern in ("bun server.ts", "bun run --cwd.*telegram"):
        try:
            subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=5)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    time.sleep(2)
    # 3. Kickstart launchd-jobben → start-bot.sh lager ny tmux + Claude + bun
    ok = _kickstart_bot()
    # 4. kickstart er treg (start-bot.sh venter på nett). Verifiser at tmux
    #    faktisk kommer opp; hvis ikke innen ~8 sek, kjør start-bot.sh direkte.
    time.sleep(8)
    if not tmux_session_alive():
        script = APP_SUPPORT / "scripts" / "start-bot.sh"
        if script.exists():
            try:
                subprocess.run(["bash", str(script)], capture_output=True, timeout=90)
                print("[monitor] kickstart traff ikke — kjørte start-bot.sh direkte")
                ok = True
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                print(f"[monitor] start-bot.sh fallback feilet: {e}", file=sys.stderr)
    print("[monitor] Full restart (plugin_disconnect): tmux drept + restartet")
    return ok


def try_auto_recover(issue_type: str, state: dict | None = None) -> bool:
    """Forsøk å auto-fikse kjente issues. Returner True ved forsøk utført."""
    if issue_type == "process_dead":
        return _kickstart_bot()
    if issue_type == "tmux_dead":
        # Samme fiks — kickstart bot som sjekker/lager tmux-sesjon
        return _kickstart_bot()
    if issue_type == "plugin_disconnect":
        # Cooldown: ikke restart oftere enn RESTART_COOLDOWN_MINUTES
        if state is not None and not _restart_cooldown_ok(state):
            print("[monitor] plugin_disconnect oppdaget, men restart-cooldown "
                  "aktiv — hopper over")
            return False
        ok = _full_restart_bot()
        if state is not None and ok:
            state["last_restart"] = datetime.now(timezone.utc).isoformat()
        return ok
    if issue_type == "sync_stale":
        uid = os.getuid()
        try:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/com.trening.sync"],
                capture_output=True, text=True, timeout=10,
            )
            print(f"[monitor] Auto-recover sync_stale: launchctl kickstart sync → "
                  f"rc={result.returncode}")
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
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
            if try_auto_recover(issue.type, state):
                recovered.append(issue.type)

        if should_alert(state, issue.type, now):
            # Inkluder auto-recovery-status i alerten
            extra = ""
            if issue.type in recovered:
                if issue.type == "plugin_disconnect":
                    extra = "\n\n🔧 Auto-restartet tmux-sesjon. Bot bør svare igjen om ~45 sek."
                else:
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
