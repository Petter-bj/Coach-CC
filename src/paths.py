"""Runtime paths for Trening.

All state lives outside the git repo to avoid TCC friction and keep
source code portable. Directories are created on first import.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()

APP_SUPPORT = HOME / "Library" / "Application Support" / "Trening"
LOGS = HOME / "Library" / "Logs" / "Trening"
CACHES = HOME / "Library" / "Caches" / "Trening"

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
TELEGRAM_TOKEN_FILE = CREDENTIALS_DIR / "telegram_token"

SYNC_LOG = LOGS / "sync.jsonl"
BOT_LOG = LOGS / "bot.jsonl"

# First-time backfill cutoff — new Garmin watch received 2026-04-13.
BACKFILL_START_DATE = "2026-04-13"
DEFAULT_TIMEZONE = "Europe/Oslo"


def ensure_runtime_dirs() -> None:
    """Create all runtime directories with correct permissions."""
    for d in (APP_SUPPORT, LOGS, CACHES, CREDENTIALS_DIR, FIT_FILES_DIR, BACKUPS_DIR, SCREENSHOT_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDENTIALS_DIR, 0o700)
