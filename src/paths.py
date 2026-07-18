"""Runtime paths for Trening.

All state lives outside the git repo to avoid TCC friction and keep source
code portable. macOS keeps the original ``~/Library/...`` defaults. A Linux
deployment can override them with absolute paths through:

* ``TRENING_DATA_DIR`` — database, credentials, FIT files, and backups
* ``TRENING_LOG_DIR`` — application log files
* ``TRENING_CACHE_DIR`` — disposable caches

Directories are created lazily by :func:`ensure_runtime_dirs`, not at import
time. This keeps imports side-effect free and makes paths easy to test.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()


def _configured_dir(env_name: str, default: Path) -> Path:
    """Return an absolute directory from an optional environment override.

    Relative paths would silently depend on a service's working directory, so
    reject them early with an actionable error instead.
    """
    configured = os.environ.get(env_name)
    if not configured:
        return default

    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{env_name} må være en absolutt sti, fikk: {configured!r}")
    return path


APP_SUPPORT = _configured_dir(
    "TRENING_DATA_DIR", HOME / "Library" / "Application Support" / "Trening"
)
LOGS = _configured_dir("TRENING_LOG_DIR", HOME / "Library" / "Logs" / "Trening")
CACHES = _configured_dir(
    "TRENING_CACHE_DIR", HOME / "Library" / "Caches" / "Trening"
)

DB_PATH = APP_SUPPORT / "health.db"
CREDENTIALS_DIR = APP_SUPPORT / "credentials"
FIT_FILES_DIR = APP_SUPPORT / "fit_files"
BACKUPS_DIR = APP_SUPPORT / "backups"
SCREENSHOT_CACHE_DIR = CACHES / "strength_screenshots"

SYNC_LOCK = APP_SUPPORT / "sync.lock"

ENV_FILE = CREDENTIALS_DIR / ".env"
GARMIN_TOKENS = CREDENTIALS_DIR / "garmin_tokens.json"
WITHINGS_CREDS = CREDENTIALS_DIR / "withings.json"
CONCEPT2_CREDS = CREDENTIALS_DIR / "concept2.json"
YAZIO_CREDS = CREDENTIALS_DIR / "yazio.json"
TELEGRAM_TOKEN_FILE = CREDENTIALS_DIR / "telegram_token"

SYNC_LOG = LOGS / "sync.jsonl"
BOT_LOG = LOGS / "bot.jsonl"

# First-time backfill cutoff — new Garmin watch received 2026-04-13.
BACKFILL_START_DATE = "2026-04-13"
DEFAULT_TIMEZONE = "Europe/Oslo"


def ensure_runtime_dirs() -> None:
    """Create all runtime directories with correct permissions."""
    for d in (
        APP_SUPPORT,
        LOGS,
        CACHES,
        CREDENTIALS_DIR,
        FIT_FILES_DIR,
        BACKUPS_DIR,
        SCREENSHOT_CACHE_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDENTIALS_DIR, 0o700)
